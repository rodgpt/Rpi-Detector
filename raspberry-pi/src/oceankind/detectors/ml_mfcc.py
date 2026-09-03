"""Detector MFCC + regresión logística — model.joblib restaurado (F-24, D-014).

El clasificador que el build de julio dejó huérfano vuelve como segundo
miembro del registro, con el extractor de features portado tal cual de
`tools/predict.py` (el modelo se entrenó con ESOS features de librosa; una
reimplementación aproximada rompería el modelo en silencio).

event_type "blast" — es el detector de impulsos del par D-014. Dos advertencias
que son del cliente, no nuestras (D-015, dependencias 2 y 3):
  * ML_POSITIVE_LABEL entrenado era FILTRO (filtro de piscina, señal proxy).
    Si el modelo nunca se reentrenó con explosiones reales, esto detecta
    filtros de piscina con etiqueta de blast. Documentado en F-21/F-24.
  * Ningún detector fue validado contra audio etiquetado de campo.
El arnés corre lo que el cliente entregue; cuando llegue el modelo nuevo,
reemplazar el bundle (o registrar otro módulo) es el único cambio.

Dependencias PESADAS y opcionales: librosa (+numba), scikit-learn, joblib.
NO están en requirements.txt: instalarlas solo en unidades que activen este
detector, y medir la memoria — 512 MB es poco para librosa (R-9.5, D-011).
Si faltan, la carga falla A GRITOS vía el registro (health.degraded_reason),
jamás en silencio (F-02).
"""

import logging
from pathlib import Path

import numpy as np

from .. import config as C

log = logging.getLogger("oceankind")

EVENT_TYPE = "blast"

MODEL_PATH = Path(C.ML_MODEL_PATH)

_bundle = None


def load() -> None:
    """Carga el bundle una vez. Excepción = el registro lo reporta como evento
    de salud. Bundle: {"model", "sr", "n_mfcc"} (formato de tools/predict.py)."""
    global _bundle
    if _bundle is not None:
        return
    import joblib  # noqa: PLC0415
    import librosa  # noqa: F401,PLC0415 — falla acá si falta, no en el primer clip
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"modelo no encontrado: {MODEL_PATH} "
                                f"(OCEANKIND_ML_MODEL_PATH)")
    _bundle = joblib.load(MODEL_PATH)
    for key in ("model", "sr", "n_mfcc"):
        if key not in _bundle:
            raise ValueError(f"bundle inválido: falta {key!r}")
    log.info("ml_mfcc: modelo cargado (%s, sr=%s, n_mfcc=%s)",
             MODEL_PATH.name, _bundle["sr"], _bundle["n_mfcc"])


def _extract_features(y: np.ndarray, sr: int, n_mfcc: int) -> np.ndarray:
    """Idéntico a tools/predict.py — el modelo se entrenó con esto."""
    import librosa  # noqa: PLC0415
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=n_mfcc)
    mfcc_mean = mfcc.mean(axis=1)
    mfcc_std = mfcc.std(axis=1)
    cent = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    bw = librosa.feature.spectral_bandwidth(y=y, sr=sr)[0]
    rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)[0]
    flat = librosa.feature.spectral_flatness(y=y)[0]
    zcr = librosa.feature.zero_crossing_rate(y)[0]
    rms = librosa.feature.rms(y=y)[0]
    extras = np.array([
        cent.mean(), cent.std(),
        bw.mean(), bw.std(),
        rolloff.mean(), rolloff.std(),
        flat.mean(), flat.std(),
        zcr.mean(), zcr.std(),
        rms.mean(), rms.std(),
    ])
    return np.concatenate([mfcc_mean, mfcc_std, extras])


def detect(fs: int, samples: np.ndarray, cfg: dict, rms: float) -> dict | None:
    import librosa  # noqa: PLC0415
    load()
    y = samples
    if y.ndim == 2:
        # El entrenamiento usó librosa.load(mono=True): downmix igual.
        # (Los dos hidrófonos promediados en un canal — TODO de audio abierto.)
        y = y.mean(axis=1)
    y = y.astype(np.float32) / 32768.0
    sr = int(_bundle["sr"])
    if fs != sr:
        y = librosa.resample(y, orig_sr=fs, target_sr=sr)
    feats = _extract_features(y, sr, int(_bundle["n_mfcc"])).reshape(1, -1)
    proba = float(_bundle["model"].predict_proba(feats)[0, 1])
    if rms >= cfg["alert_min_rms"] and proba >= C.ML_SCORE_MIN:
        return {"type": EVENT_TYPE, "score": proba, "label": C.ML_LABEL,
                "meta": {"proba": round(proba, 4), "model": MODEL_PATH.name}}
    return None
