"""Detector PSD de peaks tonales (Emily Barosin / Integral Consulting).

Opera sobre arrays en memoria: en el modelo continuo el audio nunca toca el
disco salvo para archivo y clips de eventos. La Fase 3 (D-014) convierte esto
en un registro ordenado de detectores; la interfaz ya calza:
    classify_samples(fs, data, cfg) -> {pred, proba, label} | {}

OJO (F-21): este algoritmo necesita tonos SOSTENIDOS. Un evento impulsivo de
menos de un segundo puntúa a lo sumo 0.2 y NO puede disparar con score_min>=0.4.
Detecta maquinaria de embarcaciones, no explosiones. El segundo detector del
registro (Fase 3) cubre impulsos; no "arreglar" esto cambiando umbrales.
"""

import logging

import numpy as np

from . import config as C
from . import health

log = logging.getLogger("oceankind")


def classify_samples(fs: int, data: np.ndarray, cfg: dict | None = None) -> dict:
    """Clasifica un clip (array [N] o [N, canales]) con el detector PSD.

    Devuelve {pred, proba, label} o {} si falló (contabilizado, nunca en
    silencio — F-02). proba = fracción de segundos con ≥2 peaks tonales.
    """
    cfg = cfg or C.CONFIG.snapshot()
    try:
        from scipy import signal as sp_signal  # noqa: PLC0415

        if data.ndim == 2:                     # estéreo dual-mono → mono
            data = data.mean(axis=1)
        data = data.astype(np.float32)
        if C.PSD_DECIMATION > 1:
            data = sp_signal.decimate(data, C.PSD_DECIMATION)
            fs //= C.PSD_DECIMATION

        chunk = fs
        n_chunks = len(data) // chunk
        if n_chunks == 0:
            health.record_classify_result(False, "clip vacío o más corto que 1s")
            return {}
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
        health.record_classify_result(True)
        return {
            "pred":  pred,
            "proba": round(proba, 4),
            "label": C.DETECTION_LABEL if pred == 1 else "background",
        }
    except Exception as exc:
        health.record_classify_result(False, str(exc))
        return {}


def classify_clip(wav_path: str, cfg: dict | None = None) -> dict:
    """Wrapper de archivo (herramientas de banco y tests)."""
    try:
        import scipy.io.wavfile as wavfile  # noqa: PLC0415
        import warnings as _w               # noqa: PLC0415
        _w.filterwarnings("ignore", category=wavfile.WavFileWarning)
        fs, data = wavfile.read(wav_path)
    except Exception as exc:
        health.record_classify_result(False, str(exc))
        return {}
    return classify_samples(fs, data, cfg)


def decide(rms: float, ml_result: dict, cfg: dict) -> dict:
    """La decisión de alerta, honesta por modo (F-01):
      psd  → doble gate RMS+score; sin fallback, pero con alarma si el
             clasificador cae (F-02) — nunca silencio.
      rms  → solo umbral RMS. SÍ puede disparar.
      auto → PSD si clasifica; si el clasificador cae, fallback RMS real.
    Devuelve alert, decided_by, detector, event_type, label y score (0..1;
    para RMS es el RMS normalizado — no hay confianza que inventar).
    """
    mode = cfg["detection_mode"]
    score = ml_result.get("proba", 0.0) if ml_result else 0.0
    if mode == "psd" or (mode == "auto" and ml_result):
        return {
            "alert":      rms >= cfg["alert_min_rms"] and score >= cfg["score_min"],
            "decided_by": "psd_tonal",
            "detector":   "psd_tonal",
            "event_type": "vessel",     # firma tonal = maquinaria (D-014)
            "label":      (ml_result.get("label") if ml_result else None) or C.DETECTION_LABEL,
            "score":      score,
        }
    return {
        "alert":      rms >= cfg["alert_threshold"],
        "decided_by": "rms" if mode == "rms" else "rms_fallback",
        "detector":   "rms",
        "event_type": "unknown",        # el RMS no distingue tipo de evento
        "label":      "RUIDO FUERTE",
        "score":      rms,
    }
