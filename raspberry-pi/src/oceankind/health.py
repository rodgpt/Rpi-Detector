"""Salud y honestidad (R-2.x): contadores, duty cycle medido, avisos de degradación.

El propósito del sistema es notar algo; un componente que se degrada sin decirlo
lo derrota. Nada aquí decide alertas — solo dice la verdad, siempre completa:
un contador que solo aparece en la falla es indistinguible de uno que no existe.

Duty cycle en el modelo continuo: fracción de frames de audio ENTREGADOS por la
captura contra el reloj de pared. Con el hilo de captura sano es ~100; cae si el
stream se corta o PortAudio pierde bloques (overflow). Medido, no afirmado (R-1.2).
"""

import logging
import threading
import time
from collections import deque

from . import config as C
from . import notify

log = logging.getLogger("oceankind")

_lock = threading.Lock()

# Contadores publicados en status.json
_clips_dropped_total   = 0      # clips descartados (cola de bloques / archivo / transporte)
_suppressed_total      = 0      # detecciones registradas con notificación suprimida
_events_dropped_total  = 0      # eventos perdidos por tope del spool
_capture_overflows     = 0      # bloques perdidos reportados por PortAudio / cola llena

# Detector
_classify_fail_consec  = 0
_detector_alert_sent   = False

# Audio (hidrófono desconectado)
_rms_history: deque = deque()
_audio_alert_sent = False

# Config remota rechazada (firma, claves desconocidas, enum inválido…).
# "Rejecting a config is a health event, not a debug line" — contrato.
_config_error: str | None = None

# Push al backend: rechazos terminales (400/403) y descartes del spool de push.
# Ninguno toca el registro (el blob); se publican porque un push rechazado
# durante una semana que nadie ve es una falla silenciosa.
_push_rejected_total = 0
_push_dropped_total  = 0


def count_push_rejected(n: int = 1) -> None:
    global _push_rejected_total
    with _lock:
        _push_rejected_total += n


def count_push_dropped(n: int = 1) -> None:
    global _push_dropped_total
    with _lock:
        _push_dropped_total += n


def set_config_error(reason: str | None) -> None:
    global _config_error
    with _lock:
        _config_error = reason


# Registro de detectores: un detector pedido que no carga (falta librosa,
# falta el modelo) es un evento de salud, no un log perdido (F-02, D-014).
_registry_error: str | None = None


def set_registry_error(reason: str | None) -> None:
    global _registry_error
    with _lock:
        _registry_error = reason

# Duty cycle: (monotonic, frames_entregados) en ventana móvil
_frames_window: deque = deque()
_capture_started_mono: float | None = None
_frames_total = 0

# Latidos de hilos para el watchdog (R-2.7): cada loop vigilado llama beat()
# en CADA vuelta, también las vacías. Vida ≠ salud: esto prueba que el hilo
# se agenda, no que el audio fluya (eso ya lo cubre el fail-loud).
_beats: dict = {}


def beat(name: str) -> None:
    _beats[name] = time.monotonic()


def stale_beats(max_age_s: float) -> list:
    """Hilos vigilados cuyo último latido es más viejo que max_age_s."""
    now = time.monotonic()
    return sorted(n for n, t in _beats.items() if now - t > max_age_s)


def count_clips_dropped(n: int = 1) -> None:
    global _clips_dropped_total
    with _lock:
        _clips_dropped_total += n


def count_suppressed() -> int:
    global _suppressed_total
    with _lock:
        _suppressed_total += 1
        return _suppressed_total


def count_events_dropped(n: int = 1) -> None:
    global _events_dropped_total
    with _lock:
        _events_dropped_total += n


def count_capture_overflow(n: int = 1) -> None:
    global _capture_overflows
    with _lock:
        _capture_overflows += n


# ─── Duty cycle (R-1.2, F-05) ────────────────────────────────────────────────

def mark_capture_started() -> None:
    global _capture_started_mono
    with _lock:
        _capture_started_mono = time.monotonic()


def record_frames(n_frames: int) -> None:
    """Llamado por la captura al entregar bloques. Barato: O(1) amortizado."""
    global _frames_total
    now = time.monotonic()
    with _lock:
        _frames_total += n_frames
        _frames_window.append((now, n_frames))
        cutoff = now - C.DUTY_WINDOW_S
        while _frames_window and _frames_window[0][0] < cutoff:
            _frames_window.popleft()


def duty_cycle_pct() -> float | None:
    with _lock:
        if _capture_started_mono is None or not _frames_window:
            return None
        now = time.monotonic()
        span = min(now - _capture_started_mono, C.DUTY_WINDOW_S)
        if span <= 1.0:
            return None
        delivered_s = sum(n for _, n in _frames_window) / C.SAMPLE_RATE
        return round(min(100.0, delivered_s / span * 100), 1)


def deaf_seconds_total() -> float:
    with _lock:
        if _capture_started_mono is None:
            return 0.0
        elapsed = time.monotonic() - _capture_started_mono
        return round(max(0.0, elapsed - _frames_total / C.SAMPLE_RATE), 1)


# ─── Detector (F-02) ─────────────────────────────────────────────────────────

def record_classify_result(ok: bool, reason: str = "") -> None:
    """DETECTOR_FAIL_LIMIT fallos consecutivos → detector_ok false + UNA alarma.
    Devolver {} y seguir en silencio es el patrón que produjo F-02."""
    global _classify_fail_consec, _detector_alert_sent
    if ok:
        if _classify_fail_consec >= C.DETECTOR_FAIL_LIMIT:
            log.warning("Detector recuperado tras %d fallos", _classify_fail_consec)
        _classify_fail_consec = 0
        _detector_alert_sent = False
        return
    _classify_fail_consec += 1
    log.warning("clasificación falló (%d consecutivos): %s", _classify_fail_consec, reason)
    if _classify_fail_consec >= C.DETECTOR_FAIL_LIMIT and not _detector_alert_sent:
        log.error("🔴 DETECTOR DEGRADADO: %d fallos consecutivos — la unidad NO está detectando",
                  _classify_fail_consec)
        notify.send_degraded_alert(
            f"detector caído ({_classify_fail_consec} fallos): {reason[:80]}")
        _detector_alert_sent = True


def detector_ok() -> bool:
    if C.CONFIG.snapshot()["detection_mode"] == "rms":
        return True     # el modo rms no usa el clasificador
    return _classify_fail_consec < C.DETECTOR_FAIL_LIMIT


# ─── Audio (R-2.2) ───────────────────────────────────────────────────────────

def record_rms(rms: float) -> None:
    now = time.time()
    with _lock:
        _rms_history.append((now, rms))
        cutoff = now - C.AUDIO_HEALTH_WINDOW_S
        while _rms_history and _rms_history[0][0] < cutoff:
            _rms_history.popleft()


def audio_health() -> tuple[bool, float, int]:
    """ok=True si aún faltan datos (no alarmar al arrancar) o si el pico de RMS
    supera el piso. ok=False → probable hidrófono desconectado."""
    with _lock:
        if len(_rms_history) < 3:
            return True, 0.0, len(_rms_history)
        peak = max(r for _, r in _rms_history)
        return (peak >= C.AUDIO_FLOOR_RMS), peak, len(_rms_history)


def maybe_alert_audio_health() -> None:
    global _audio_alert_sent
    ok, peak, n = audio_health()
    if ok:
        if _audio_alert_sent:
            log.info("🎙️ señal de audio recuperada (pico %.4f)", peak)
        _audio_alert_sent = False
        return
    if not _audio_alert_sent:
        log.error("🔴 SIN SEÑAL DE AUDIO: pico %.4f bajo el piso %s en %d muestras — "
                  "probable hidrófono desconectado", peak, C.AUDIO_FLOOR_RMS, n)
        notify.send_degraded_alert(f"sin señal de audio (pico {peak:.4f}) — revisar hidrófono/cable")
        _audio_alert_sent = True


def audio_status_str() -> str:
    ok, peak, _n = audio_health()
    return (f"🎙️ grabando OK (pico {peak:.4f})" if ok
            else f"⚠️ SIN AUDIO (pico {peak:.4f}) — revisar hidrófono/cable")


# ─── El bloque health de status.json ─────────────────────────────────────────

def build_health() -> dict:
    from . import push, storage  # noqa: PLC0415 — import tardío, evita ciclo
    audio_ok, peak_rms, _n = audio_health()
    det_ok = detector_ok()
    reasons = []
    if not det_ok:
        reasons.append(f"clasificador caído ({_classify_fail_consec} fallos consecutivos)")
    if not audio_ok:
        reasons.append(f"sin señal de audio (pico RMS {peak_rms:.4f} bajo el piso "
                       f"{C.AUDIO_FLOOR_RMS}) — revisar hidrófono/cable")
    if not C.TWILIO_CONFIGURED:
        reasons.append("sin credenciales Twilio — ninguna alerta WhatsApp puede salir (modo banco)")
    if push.auth_failed():
        reasons.append("credencial del backend rechazada (401) — push de eventos detenido; "
                       "el blob sigue escribiéndose")
    with _lock:
        if _config_error:
            reasons.append(f"config remota rechazada: {_config_error}")
        if _registry_error:
            reasons.append(_registry_error)
        clips_dropped  = _clips_dropped_total
        suppressed     = _suppressed_total
        events_dropped = _events_dropped_total
        overflows      = _capture_overflows
        push_rejected  = _push_rejected_total
        push_dropped   = _push_dropped_total
    return {
        "detector_ok":        det_ok,
        "audio_ok":           audio_ok,
        "duty_cycle_pct":     duty_cycle_pct(),
        "deaf_seconds_total": deaf_seconds_total(),
        "clips_dropped":      clips_dropped,
        "capture_overflows":  overflows,
        "suppressed_count":   suppressed,
        "upload_backlog":     storage.event_spool_len(),
        "events_dropped":     events_dropped,
        "push_enabled":       push.enabled(),
        "push_backlog":       push.spool_len(),
        "push_rejected":      push_rejected,
        "push_dropped":       push_dropped,
        "wa_pending":         notify.pending_alert_count(),
        "archive_queue":      _archive_queue_len(),
        "degraded_reason":    "; ".join(reasons) if reasons else None,
    }


def _archive_queue_len() -> int:
    try:
        return sum(1 for _ in C.ARCHIVE_DIR.glob("clip_*.wav")) if C.ARCHIVE_DIR.exists() else 0
    except Exception:
        return 0
