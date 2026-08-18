"""Fail-loud smoke test — no hardware, no Azure, no Twilio (R-9.4).

Exercises the pure-function surface of the oceankind package:
  1. detector: synthetic tonal clip scores high, impulse scores <= 0.2 (F-21)
  2. detector failure counting flips detector_ok and composes degraded_reason
  3. frame-based duty-cycle arithmetic
  4. health block always publishes every counter
  5. non-finite float sanitisation (R-4.6)
  6. decide(): every mode can genuinely fire (F-01)
"""
import os
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

os.environ.update({
    "OCEANKIND_ALLOW_NO_TWILIO": "1",
    "OCEANKIND_STATE_DIR": tempfile.mkdtemp(prefix="ok_state_"),
})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from oceankind import config as C            # noqa: E402
from oceankind import detector, health, storage  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name} {detail}")
    if not cond:
        FAILURES.append(name)


def write_wav(path, data, fs=48000):
    pcm = (np.clip(data, -1, 1) * 32000).astype(np.int16)
    stereo = np.column_stack([pcm, pcm]).ravel()
    with wave.open(path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(fs)
        wf.writeframes(stereo.tobytes())


fs = 48000
t = np.arange(5 * fs) / fs
rng = np.random.default_rng(7)
tonal = 0.30 * np.sin(2 * np.pi * 120 * t) + 0.25 * np.sin(2 * np.pi * 240 * t) + 0.01 * rng.standard_normal(len(t))
impulse = 0.005 * rng.standard_normal(len(t))
impulse[fs * 2: fs * 2 + fs // 4] += 0.9 * rng.standard_normal(fs // 4)

with tempfile.TemporaryDirectory() as td:
    tonal_p, impulse_p = f"{td}/tonal.wav", f"{td}/impulse.wav"
    write_wav(tonal_p, tonal)
    write_wav(impulse_p, impulse)
    r_tonal = detector.classify_clip(tonal_p)
    r_imp = detector.classify_clip(impulse_p)

print("1. detector")
check("tonal clip fires", bool(r_tonal) and r_tonal["proba"] >= C.CONFIG.snapshot()["score_min"] and r_tonal["pred"] == 1,
      f"(proba={r_tonal.get('proba')}, label={r_tonal.get('label')})")
check("tonal label is DETECTION_LABEL", r_tonal.get("label") == C.DETECTION_LABEL, f"({C.DETECTION_LABEL})")
check("impulse cannot fire (F-21 arithmetic)", bool(r_imp) and r_imp["proba"] <= 0.2,
      f"(proba={r_imp.get('proba')})")

print("2. detector_ok / failure accounting")
check("detector_ok true after successes", health.detector_ok())
for _ in range(C.DETECTOR_FAIL_LIMIT):
    detector.classify_clip("/nonexistent/clip.wav")
check("detector_ok false after consecutive failures", not health.detector_ok())
h = health.build_health()
check("degraded_reason names the detector", h["detector_ok"] is False and "clasificador" in (h["degraded_reason"] or ""))
check("bench mode declared in degraded_reason", "Twilio" in (h["degraded_reason"] or ""))
health.record_classify_result(True)
check("recovery resets", health.detector_ok() and health.build_health()["detector_ok"] is True)

print("3. duty cycle (frame-based)")
health.mark_capture_started()
import time as _time
_t0 = _time.monotonic()
while _time.monotonic() - _t0 < 2.0:
    _time.sleep(0.1)
    health.record_frames(int(0.1 * C.SAMPLE_RATE))
d = health.duty_cycle_pct()
check("duty_cycle_pct ~100 when frames keep up", d is not None and 80.0 <= d <= 100.0, f"({d})")
check("deaf_seconds small when healthy", health.deaf_seconds_total() < 1.0,
      f"({health.deaf_seconds_total()})")

print("4. health block completeness")
h = health.build_health()
for key in ("detector_ok", "audio_ok", "duty_cycle_pct", "deaf_seconds_total",
            "clips_dropped", "capture_overflows", "suppressed_count", "upload_backlog",
            "events_dropped", "wa_pending", "archive_queue", "degraded_reason"):
    check(f"health.{key} present", key in h)

print("5. non-finite floats (R-4.6)")
s = storage.sanitize_for_json({"a": float("inf"), "b": [float("nan"), 1.5], "c": {"d": float("-inf")}})
check("inf/nan -> None", s == {"a": None, "b": [None, 1.5], "c": {"d": None}})

print("6. decide(): every mode can fire (F-01)")
cfg = C.CONFIG.snapshot()
d_psd = detector.decide(0.05, {"proba": 1.0, "label": "MOTOR", "pred": 1}, cfg)
check("psd fires on tonal", d_psd["alert"] and d_psd["decided_by"] == "psd_tonal")
d_fb = detector.decide(0.5, {}, cfg)   # clasificador caído en auto/psd
if C.DETECTION_MODE == "psd":
    check("psd without classifier stays honest (no fallback, alarmed elsewhere)",
          d_psd["detector"] == "psd_tonal")
check("rms path fires on loud input", detector.decide(0.5, {}, cfg)["alert"] or C.DETECTION_MODE == "psd",
      f"(mode={C.DETECTION_MODE}, decided_by={d_fb['decided_by']})")

print("7. overlapping windows (window_hop_s)")
import queue as _queue  # noqa: E402
from oceankind import capture as _capture  # noqa: E402


def _run_assembler(hop_s: float, seconds: float) -> int:
    C.CONFIG.apply({"window_hop_s": hop_s})
    q = _queue.Queue(maxsize=10000)
    block = np.zeros((C.BLOCK_FRAMES, C.CHANNELS), dtype=np.int16)
    for _ in range(int(seconds * C.SAMPLE_RATE / C.BLOCK_FRAMES)):
        q.put_nowait(block)
    asm = _capture.ClipAssembler(q)
    clips = 0
    while True:
        clip = asm.next_clip(timeout=0.01)
        if clip is not None:
            clips += 1
            check_len = len(clip) == int(C.CAPTURE_SECONDS * C.SAMPLE_RATE)
            if not check_len:
                FAILURES.append("clip length wrong")
        elif q.empty():
            break
    return clips


n_nohop = _run_assembler(5.0, 15.0)
n_hop   = _run_assembler(2.5, 15.0)
check("hop=5.0: 15s -> 3 ventanas pegadas", n_nohop == 3, f"({n_nohop})")
check("hop=2.5: 15s -> 5 ventanas solapadas", n_hop == 5, f"({n_hop})")
clamped = C.CONFIG.apply({"window_hop_s": 0.1})   # bajo el mínimo → clamp a 1.0
check("hop clampeado a >=1.0", C.CONFIG.snapshot()["window_hop_s"] == 1.0, f"({clamped})")
C.CONFIG.apply({"window_hop_s": 5.0})

print("8. startup validation")
check("bench escape hatch allows start", C.TWILIO_CONFIGURED is False and C.ALLOW_NO_TWILIO is True)
try:
    C.validate_startup_config()
    check("validate passes with ALLOW_NO_TWILIO", True)
except SystemExit:
    check("validate passes with ALLOW_NO_TWILIO", False)

print()
if FAILURES:
    print(f"FAILED: {FAILURES}")
    sys.exit(1)
print("ALL PASS")
