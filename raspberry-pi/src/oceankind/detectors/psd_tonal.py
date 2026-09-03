"""Detector PSD de peaks tonales (Emily Barosin / Integral Consulting).

Firma sostenida de banda angosta en 55–1000 Hz = maquinaria de embarcación
(eje, palas, armónicos de motor). event_type "vessel".

CANÓNICO desde la Fase 3 (F-23): la copia standalone `detector_psd.py` quedó
retirada en legacy/. Este archivo es el algoritmo que corre.

OJO (F-21): necesita tonos SOSTENIDOS — proba = fracción de segundos tonales
del clip, así que un impulso de <1 s puntúa a lo sumo 0.2 y no puede disparar
con score_min>=0.4. Los impulsos son del detector ml_mfcc (o el próximo modelo
del cliente). No "arreglar" esto con umbrales.

Umbrales calibrados con datos de campo de Zapallar (2026-08-07); afinables por
config remota firmada (score_min, alert_min_rms, psd_threshold_db, psd_f_min/max).
"""

import numpy as np

from .. import config as C

EVENT_TYPE = "vessel"


def analyze(fs: int, samples: np.ndarray, cfg: dict) -> dict:
    """Corre el algoritmo y devuelve {pred, proba, label} SIEMPRE (también
    bajo umbral). Lo usan detect() y las herramientas de banco."""
    from scipy import signal as sp_signal  # noqa: PLC0415

    data = samples
    if data.ndim == 2:                     # estéreo dual-mono → mono
        data = data.mean(axis=1)
    data = data.astype(np.float32)
    if C.PSD_DECIMATION > 1:
        data = sp_signal.decimate(data, C.PSD_DECIMATION)
        fs //= C.PSD_DECIMATION

    chunk = fs
    n_chunks = len(data) // chunk
    if n_chunks == 0:
        raise ValueError("clip vacío o más corto que 1s")
    f_min, f_max = cfg["psd_f_min"], cfg["psd_f_max"]
    thr_db = cfg["psd_threshold_db"]
    n_tonal = 0
    for i in range(n_chunks):
        seg = data[i * chunk:(i + 1) * chunk]
        freqs, psd = sp_signal.welch(seg, fs=fs, nperseg=min(C.PSD_NFFT, len(seg)),
                                     nfft=C.PSD_NFFT)
        psd_db = 10 * np.log10(psd + 1e-10)
        mask = (freqs >= f_min) & (freqs <= f_max)
        pf, ff = psd_db[mask], freqs[mask]
        if len(ff) < 3:
            continue
        df = float(np.mean(np.diff(ff)))
        search = max(1, int(C.PSD_SEARCH_HZ / df))
        peaks, _props = sp_signal.find_peaks(pf, distance=max(1, int(2 / df)))
        valid = 0
        for idx in peaks:
            lo = max(0, idx - search)
            hi = min(len(pf), idx + search + 1)
            # Banda de guarda alrededor del peak: sin ella el hombro del
            # propio tono comprime la prominencia medida.
            bg = np.concatenate([
                pf[lo:max(lo, idx - C.PSD_GUARD_BINS)],
                pf[min(hi, idx + C.PSD_GUARD_BINS + 1):hi],
            ])
            if len(bg) == 0:
                continue
            if pf[idx] - bg.max() >= thr_db:
                valid += 1
                if valid >= 2:
                    break
        if valid >= 2:
            n_tonal += 1

    proba = n_tonal / n_chunks
    pred = 1 if proba >= 0.5 else 0
    return {"pred": pred, "proba": round(proba, 4),
            "label": C.DETECTION_LABEL if pred == 1 else "background"}


def detect(fs: int, samples: np.ndarray, cfg: dict, rms: float) -> dict | None:
    """Interfaz de registro. Doble gate: RMS mínimo (filtra ruido de fondo
    fuerte) y score mínimo. Ambos afinables sin firmware (R-3.6)."""
    r = analyze(fs, samples, cfg)
    if rms >= cfg["alert_min_rms"] and r["proba"] >= cfg["score_min"]:
        return {"type": EVENT_TYPE, "score": r["proba"],
                "label": r["label"] or C.DETECTION_LABEL, "meta": r}
    return None
