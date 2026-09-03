"""Registro ordenado de detectores (D-014). Cadena, no selector.

Un blast es impulsivo y de banda ancha; una embarcación es sostenida y de banda
angosta. Buscan características OPUESTAS en el mismo audio: la operación útil
es correr ambos y etiquetar lo que salió, no elegir uno. Cada detección lleva
`event_type` y `detector` al registro — el detector ya cambió una vez en
silencio y eso no puede repetirse (R-3.3).

Interfaz (R-3.1 — debe poder expresar CUALQUIER tipo de evento, incluidos los
que ningún detector produce hoy):

    detect(fs, samples, cfg, rms) -> {"type": str, "score": float,
                                      "label": str, "meta": dict} | None

None = sin detección. Una excepción se contabiliza (F-02: jamás en silencio).

Qué corre y en qué orden:
  - OCEANKIND_DETECTORS="psd_tonal,ml_mfcc" (env, por unidad) manda si está
    definida. Es la superficie D-014 para el segundo modelo del cliente.
  - Si no, se deriva de detection_mode (afinable por config remota firmada):
    psd → [psd_tonal] · rms → [rms] · auto → [psd_tonal, con caída a rms
    SOLO si el clasificador levanta excepción].
  NOTA contrato: el documento de config firmado solo conoce detection_mode;
  agregar una clave `detectors` es un cambio de contrato que necesita
  convergencia con el backend (propuesto en docs/TODO.md). Hasta entonces la
  lista explícita es configuración local de la unidad.

Un detector pedido que no puede cargar (falta librosa, falta el modelo) es un
evento de salud: aparece en health.degraded_reason y en el log a gritos. Un
detector que desaparece en silencio es el modo de falla que este sistema
existe para eliminar.
"""

import importlib
import logging
import os

from .. import config as C
from .. import health

log = logging.getLogger("oceankind")

# nombre → módulo. Agregar el próximo modelo del cliente = una línea acá +
# su módulo con detect(). Los módulos se importan tardío: ml_mfcc arrastra
# librosa/sklearn y solo debe pesar si está activo.
AVAILABLE = {
    "psd_tonal": ".psd_tonal",
    "rms":       ".rms_level",
    "ml_mfcc":   ".ml_mfcc",
}

DETECTORS_ENV = [d.strip() for d in os.environ.get("OCEANKIND_DETECTORS", "").split(",") if d.strip()]

_loaded: dict = {}          # nombre → módulo importado (o None si falló la carga)
_registry_errors: dict = {} # nombre → motivo de carga fallida (para health)


def _load(name: str):
    """Importa un detector una vez. Fallo de carga = ruidoso + health, jamás silencio."""
    if name in _loaded:
        return _loaded[name]
    try:
        mod = importlib.import_module(AVAILABLE[name], package=__name__)
        # Carga real (modelo, dependencias) si el módulo la define.
        if hasattr(mod, "load"):
            mod.load()
        _loaded[name] = mod
        _registry_errors.pop(name, None)
        log.info("Detector cargado: %s", name)
    except Exception as exc:
        _loaded[name] = None
        _registry_errors[name] = str(exc)
        log.error("🔴 DETECTOR %r NO CARGA: %s — pedido en la configuración y ausente "
                  "del registro. Esto es un evento de salud, no un detalle.", name, exc)
    health.set_registry_error(
        "; ".join(f"detector {n} no carga: {e[:80]}" for n, e in _registry_errors.items()) or None)
    return _loaded[name]


def active_names(cfg: dict) -> list:
    """Qué detectores corresponden ahora. La lista env manda; si no, el modo."""
    if DETECTORS_ENV:
        return [n for n in DETECTORS_ENV if n in AVAILABLE]
    mode = cfg["detection_mode"]
    return {"psd": ["psd_tonal"], "rms": ["rms"], "auto": ["psd_tonal"]}.get(mode, [])


def loaded_names(cfg: dict) -> list:
    """Los que además cargaron — esto es lo que publica status.json (R-2.6)."""
    return [n for n in active_names(cfg) if _load(n) is not None]


def run(fs: int, samples, cfg: dict, rms: float) -> list:
    """Corre la cadena en orden. Devuelve las detecciones tipadas, en orden.

    Cada elemento: {type, score, label, meta, detector, decided_by}. Una
    excepción de un detector se contabiliza (F-02) y no frena a los demás.
    En modo auto, una excepción del clasificador cae al detector rms REAL
    (F-01: la caída documentada tiene que existir y poder disparar).
    """
    names = active_names(cfg)
    unknown = [d for d in DETECTORS_ENV if d not in AVAILABLE]
    if unknown:
        health.set_registry_error(f"OCEANKIND_DETECTORS contiene desconocidos: {unknown}")
    detections = []
    auto_fallback = (not DETECTORS_ENV and cfg["detection_mode"] == "auto")
    for name in names:
        mod = _load(name)
        if mod is None:
            continue
        try:
            d = mod.detect(fs, samples, cfg, rms)
            if name == "psd_tonal":
                health.record_classify_result(True)
        except Exception as exc:
            health.record_classify_result(False, f"{name}: {exc}")
            if auto_fallback:
                d = _run_fallback_rms(fs, samples, cfg, rms)
            else:
                continue
        if d is not None:
            detections.append({**d, "detector": d.get("detector", name),
                               "decided_by": d.get("decided_by", name)})
    return detections


def _run_fallback_rms(fs, samples, cfg, rms):
    mod = _load("rms")
    if mod is None:
        return None
    d = mod.detect(fs, samples, cfg, rms)
    if d is not None:
        d["decided_by"] = "rms_fallback"
    return d


def register(name: str, module) -> None:
    """Registra un detector en runtime (tests, o el próximo modelo del cliente
    cargado como plugin). El módulo debe exponer detect(fs, samples, cfg, rms)."""
    AVAILABLE[name] = None
    _loaded[name] = module
