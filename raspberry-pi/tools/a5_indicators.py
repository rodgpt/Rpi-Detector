#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
a5_indicators.py
================
Compute two soundscape indicators from recordings of the "A5" underwater
hydrophone (48 kHz, stereo, ~5 s clips):

  1. click_rate_hz : snapping-shrimp click rate  (biophonic, absolute count/second)
  2. ndsi          : Normalized Difference Soundscape Index (relative spectral balance)

Both are calibration-free and comparable across time for THIS device.

--------------------------------------------------------------------------
BACKGROUND  (read this first if you have no prior context)
--------------------------------------------------------------------------
This is part of a low-cost coastal underwater-noise monitoring effort (MAR
FUTURA, central Chile). The "A5" is an autonomous hydrophone deployed at
Zapallar that records a ~5 s stereo clip once per minute at 48 kHz.

The monitoring idea is to describe a coastal soundscape with TWO complementary
numbers per clip, because either one alone is ambiguous:

  * click_rate_hz  -- an ABSOLUTE, count-based measure of BIOPHONY. Snapping
    shrimp produce sharp broadband clicks; their rate indexes biological
    activity and is comparable across devices (a click either happened or not,
    so it does not depend on the recorder's absolute gain calibration).

  * ndsi -- a RELATIVE spectral ratio: how much ANTHROPHONY (vessel / machinery
    noise, low frequencies) there is versus BIOPHONY (shrimp band). It runs
    -1..+1; negative = human-noise-dominated, positive = biology-dominated.
    NDSI needs no calibration but is ambiguous alone (a quiet site and a busy
    balanced site can give the same value) -- the click rate anchors it.

Together: click rate says HOW MUCH biology is present; NDSI says how much human
noise sits on top of it. Both come straight from the raw clip, no calibration.

(The method was validated in a companion study on a 16 kHz HydroMoth array; the
A5 is a different, higher-rate device, so its detector had to be re-tuned --
see "WHY THESE PARAMETER CHOICES" below.)

--------------------------------------------------------------------------
USAGE
--------------------------------------------------------------------------
  # single file  -> prints one JSON line
  python a5_indicators.py clip_2026-07-14T17-26-33.wav

  # a folder (recursive) -> writes a CSV with one row per clip
  python a5_indicators.py  /path/to/folder  results.csv

  # from Python
  from a5_indicators import compute_indicators
  row = compute_indicators("clip.wav")   # -> dict

--------------------------------------------------------------------------
REQUIREMENTS
--------------------------------------------------------------------------
  numpy, scipy      (no numba, no other deps)
  pip install numpy scipy

--------------------------------------------------------------------------
HOW THE INDICATORS ARE DEFINED
--------------------------------------------------------------------------
NDSI (Kasten-style, bandwidth-normalized):
    alpha = E_anthro / |A|^k     (anthrophony,  A = 300-700 Hz)
    beta  = E_bio    / |B|^k     (biophony,     B = 2000-5000 Hz)
    NDSI  = (beta - alpha) / (beta + alpha)          in [-1, +1]
  E_* are band-summed Welch power; k = 0.75.  NDSI > 0 = biophony-dominated,
  NDSI < 0 = anthrophony-dominated.  A secondary wideband NDSI (bio 2-20 kHz)
  is also returned but is NOT recommended as the primary metric (see notes).

Click rate:
  A dual-threshold transient detector on the envelope of a 2 kHz high-passed
  signal.  A click is counted when the envelope AND its positive derivative
  both exceed fixed thresholds, with a 3 ms refractory window.  The thresholds
  are FIXED ABSOLUTE VALUES (see CONFIG) calibrated on this device so that the
  result is deterministic and comparable across clips and deployments -- NOT
  re-estimated per file (that would erase real between-period variation).

--------------------------------------------------------------------------
WHY THESE PARAMETER CHOICES  (so a future editor doesn't "fix" them wrongly)
--------------------------------------------------------------------------
* FIXED PERCENTILE THRESHOLDS, not mean + k*sigma.  The companion HydroMoth
  study set click thresholds as mean + k*std of the envelope. That FAILS on the
  A5: the envelope's standard deviation is dominated by the click spikes
  themselves (mean ~50 but std ~390), so mean + k*std lands far above almost
  every sample and detects ~0 clicks. Instead we fix the threshold at a high
  PERCENTILE of the pooled envelope (P98 for the envelope, P95 for its positive
  derivative). Percentiles are robust to the spikes and directly control the
  detected fraction. The values are then FROZEN as absolute constants so every
  clip is scored identically and real quiet/busy variation is preserved.

* PRIMARY biophony band is 2-5 kHz, NOT the wider 2-20 kHz.  Although the A5's
  48 kHz sampling captures shrimp-click energy up to ~20 kHz, the per-Hz
  bandwidth normalization (k=0.75) penalizes the wide band because most of the
  5-20 kHz range is silent between clicks, diluting its average density. The
  wideband NDSI therefore reads systematically more negative and is misleading
  as a headline number. `ndsi` (2-5 kHz) is the metric to display; keep
  `ndsi_wideband` only for reference. (Using 2-5 kHz also matches the companion
  HydroMoth study, so the two devices stay comparable.)

--------------------------------------------------------------------------
WHAT TO EXPECT  (typical values from the Jul 2026 Zapallar deployment)
--------------------------------------------------------------------------
  click_rate_hz : median ~7/s (IQR ~6-9). NOTE: essentially FLAT across the
                  24 h cycle in this winter deployment -- do NOT expect a
                  post-sunset peak here (the companion Nov/spring data showed
                  ~17/s with a strong nocturnal peak; snapping-shrimp activity
                  is seasonal). A flat click diel is a real result, not a bug.
  ndsi (2-5 kHz): median ~+0.07, but with a clear DAILY cycle -- more positive
                  (biophonic) at night, dipping toward 0 / negative during the
                  day (~11-20 h) when human/boat activity raises the 300-700 Hz
                  anthrophony band. So here the diel signal lives in the
                  anthrophony, not the biophony.

--------------------------------------------------------------------------
IMPORTANT CALIBRATION NOTE
--------------------------------------------------------------------------
The click thresholds ENV_THRESHOLD / DERIV_THRESHOLD were derived from the
pooled envelope distribution of the A5 Zapallar deployment (48 kHz, medium
gain, ~2470 clips, 14-16 Jul 2026).  They are tied to this device's GAIN and
frequency response.  If you change the hydrophone, its gain, or the housing,
re-derive them (see recalibrate_thresholds() at the bottom).
"""

import sys, os, glob, json, re
import numpy as np
from scipy.io import wavfile
from scipy.signal import welch, butter, sosfiltfilt, decimate

# =====================================================================
# CONFIG  (parameters determined for the A5 hydrophone)
# =====================================================================
EXPECTED_SR   = 48000          # Hz, native sampling rate of the A5

# --- NDSI ---
NDSI_K        = 0.75           # bandwidth-normalization exponent
ANTHRO_BAND   = (300, 700)     # Hz  (vessel / machinery)
BIO_BAND      = (2000, 5000)   # Hz  (snapping-shrimp clicks) -- PRIMARY
BIO_BAND_WIDE = (2000, 20000)  # Hz  -- secondary, not recommended as primary

# --- click detector ---
HP_CUTOFF_HZ    = 2000         # high-pass to isolate click transients
ENV_RATE_HZ     = 1000         # envelope decimated to this rate
ENV_THRESHOLD   = 72.5657      # fixed envelope threshold  (pooled P98, A5)
DERIV_THRESHOLD = 14.1406      # fixed derivative threshold (pooled P95, A5)
REFRACTORY_MS   = 3            # min gap between counted clicks

_FNAME_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})[T_](\d{2})-(\d{2})-(\d{2})")


# =====================================================================
# core
# =====================================================================
def _load_mono(path):
    """Read a WAV; return (sample_rate, mono float64 signal)."""
    sr, x = wavfile.read(path)
    x = np.asarray(x)
    if x.ndim == 2:                     # stereo -> mean (A5 channels are identical)
        x = x.mean(axis=1)
    # integer PCM -> float (scaling is irrelevant: both indicators are ratios/counts)
    return sr, x.astype(np.float64)


def compute_ndsi(x, sr):
    """Return dict with primary + wideband NDSI and the band energies."""
    f, P = welch(x, fs=sr, nperseg=min(len(x), sr))

    def band_energy(lo, hi):
        m = (f >= lo) & (f < hi)
        return float(P[m].sum())

    def ndsi(e_anthro, e_bio, aband, bband):
        aw = (aband[1] - aband[0]) ** NDSI_K
        bw = (bband[1] - bband[0]) ** NDSI_K
        alpha = e_anthro / aw
        beta  = e_bio / bw
        denom = beta + alpha
        return float((beta - alpha) / denom) if denom > 0 else float("nan")

    e_anthro = band_energy(*ANTHRO_BAND)
    e_bio    = band_energy(*BIO_BAND)
    e_bio_w  = band_energy(*BIO_BAND_WIDE)
    return {
        "ndsi":          ndsi(e_anthro, e_bio,   ANTHRO_BAND, BIO_BAND),
        "ndsi_wideband": ndsi(e_anthro, e_bio_w, ANTHRO_BAND, BIO_BAND_WIDE),
        "anthro_energy_300_700Hz": e_anthro,
        "bio_energy_2_5kHz":       e_bio,
        "bio_energy_2_20kHz":      e_bio_w,
    }


def _envelope(x, sr):
    """2 kHz high-pass -> abs envelope -> decimate to ENV_RATE_HZ; return (env, denv)."""
    sos = butter(4, HP_CUTOFF_HZ / (sr / 2.0), btype="high", output="sos")
    xf = sosfiltfilt(sos, x)
    env = np.abs(xf)
    factor = int(round(sr / ENV_RATE_HZ))
    if factor > 1:
        env = decimate(env, factor, ftype="fir", zero_phase=True)
    env = np.clip(env, 0, None)
    denv = np.clip(np.diff(env, prepend=env[0]), 0, None)   # positive slope only
    return env, denv


def count_clicks(x, sr):
    """Return snapping-shrimp click rate (clicks per second)."""
    env, denv = _envelope(x, sr)
    tms = max(1, int(round(REFRACTORY_MS / 1000.0 * ENV_RATE_HZ)))
    cand = np.where((env > ENV_THRESHOLD) & (denv > DERIV_THRESHOLD))[0]
    if cand.size == 0:
        n = 0
    else:                              # greedily enforce refractory window
        n = 1
        last = cand[0]
        for idx in cand[1:]:
            if idx - last >= tms:
                n += 1
                last = idx
    dur = len(x) / sr
    return n / dur if dur > 0 else float("nan")


def _timestamp_from_name(path):
    m = _FNAME_RE.search(os.path.basename(path))
    if not m:
        return None
    y, mo, d, H, M, S = m.groups()
    return f"{y}-{mo}-{d}T{H}:{M}:{S}-04:00"   # A5 clock is Chile local time


def compute_indicators(path):
    """Compute both indicators for one WAV file. Returns a dict."""
    sr, x = _load_mono(path)
    if sr != EXPECTED_SR:
        # thresholds are calibrated for EXPECTED_SR; warn but still compute
        sys.stderr.write(
            f"WARNING: {os.path.basename(path)} sr={sr} != {EXPECTED_SR}; "
            f"click thresholds may not be valid.\n")
    row = {
        "file": os.path.basename(path),
        "timestamp_local": _timestamp_from_name(path),
        "duration_s": round(len(x) / sr, 3),
        "click_rate_hz": round(count_clicks(x, sr), 3),
    }
    nd = compute_ndsi(x, sr)
    row["ndsi"] = round(nd["ndsi"], 4)
    row["ndsi_wideband"] = round(nd["ndsi_wideband"], 4)
    row["anthro_energy_300_700Hz"] = round(nd["anthro_energy_300_700Hz"], 4)
    row["bio_energy_2_5kHz"] = round(nd["bio_energy_2_5kHz"], 4)
    row["bio_energy_2_20kHz"] = round(nd["bio_energy_2_20kHz"], 4)
    return row


def process_folder(folder, out_csv=None):
    """Compute indicators for every .wav under `folder`; optionally write CSV."""
    import csv
    files = sorted(glob.glob(os.path.join(folder, "**", "*.wav"), recursive=True) +
                   glob.glob(os.path.join(folder, "**", "*.WAV"), recursive=True))
    rows = []
    for i, p in enumerate(files):
        try:
            rows.append(compute_indicators(p))
        except Exception as e:
            sys.stderr.write(f"skip {p}: {e}\n")
        if (i + 1) % 100 == 0:
            sys.stderr.write(f"  {i+1}/{len(files)}\n")
    if out_csv and rows:
        cols = list(rows[0].keys())
        with open(out_csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        sys.stderr.write(f"wrote {len(rows)} rows -> {out_csv}\n")
    return rows


# =====================================================================
# (optional) recalibration helper -- run if the device / gain changes
# =====================================================================
def recalibrate_thresholds(folder, p_env=98.0, p_der=95.0):
    """
    Re-derive ENV_THRESHOLD / DERIV_THRESHOLD from a representative set of clips
    (e.g. one full day). Pool all envelopes and take percentiles. Prints values
    to paste back into the CONFIG block above.
    """
    files = sorted(glob.glob(os.path.join(folder, "**", "*.wav"), recursive=True))
    envs, denvs = [], []
    for p in files:
        sr, x = _load_mono(p)
        e, de = _envelope(x, sr)
        envs.append(e); denvs.append(de)
    env = np.concatenate(envs); denv = np.concatenate(denvs)
    e_th = float(np.percentile(env, p_env))
    d_th = float(np.percentile(denv, p_der))
    print(f"ENV_THRESHOLD   = {e_th:.4f}   # pooled P{p_env}")
    print(f"DERIV_THRESHOLD = {d_th:.4f}   # pooled P{p_der}")
    return e_th, d_th


# =====================================================================
# CLI
# =====================================================================
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    target = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else None
    if os.path.isdir(target):
        process_folder(target, out)
    else:
        print(json.dumps(compute_indicators(target), ensure_ascii=False))
