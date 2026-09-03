"""Shim de compatibilidad — el algoritmo PSD vive en detectors/psd_tonal.py.

Desde la Fase 3 (D-014) la detección corre por el registro ordenado
(`oceankind.detectors`). Este módulo conserva las funciones de clasificación
que usan las herramientas de banco y los tests, delegando en el módulo
canónico, con la misma contabilidad de fallos (F-02).
"""

import logging

from . import config as C
from . import health
from .detectors import psd_tonal

log = logging.getLogger("oceankind")


def classify_samples(fs: int, data, cfg: dict | None = None) -> dict:
    """{pred, proba, label} del PSD tonal, o {} si falló (contabilizado)."""
    cfg = cfg or C.CONFIG.snapshot()
    try:
        r = psd_tonal.analyze(fs, data, cfg)
        health.record_classify_result(True)
        return r
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
