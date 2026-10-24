"""Salud y honestidad (R-2.x): contadores, duty cycle medido, avisos de degradación.

El propósito del sistema es notar algo; un componente que se degrada sin decirlo
lo derrota. Nada aquí decide alertas — solo dice la verdad, siempre completa:
un contador que solo aparece en la falla es indistinguible de uno que no existe.

Duty cycle en el modelo continuo: fracción de frames de audio ENTREGADOS por la
captura contra el reloj de pared. Con el hilo de captura sano es ~100; cae si el
stream se corta o PortAudio pierde bloques (overflow). Medido, no afirmado (R-1.2).

Regla aprendida con F-29 (2026-10-24): las ventanas móviles se recortan AL LEER,
no solo al escribir. Recortadas solo al escribir, un stream que muere deja la
ventana congelada en su último valor sano — duty 100 %, audio_ok true — que es
exactamente el sordo-silencioso que este módulo existe para delatar.

Hilos: record_* corre en captura/clasificador; maybe_alert_* y note_* en
housekeeping. Las degradaciones se encolan aquí y housekeeping las despacha
con flush_degraded_alerts(), que desde D-020 sólo las deja en el log a nivel
ERROR: quien avisa al humano es el BACKEND, al ver cambiar
health.degraded_reason entre dos heartbeats. El dispositivo no manda nada.
"""

import logging
import threading
import time
from collections import deque

from . import config as C

log = logging.getLogger("oceankind")

_lock = threading.Lock()

# Contadores publicados en status.json
_clips_dropped_total   = 0      # clips descartados (cola de bloques / archivo / transporte)
_suppressed_total      = 0      # detecciones registradas con notificación suprimida
_events_dropped_total  = 0      # eventos perdidos por tope del spool
_capture_overflows     = 0      # bloques perdidos reportados por PortAudio / cola llena

# Detector: fallos consecutivos POR DETECTOR (F-02, D-014). Un solo contador
# compartido dejaba que el éxito de psd_tonal borrara los fallos de ml_mfcc en
# cada clip: un segundo detector podía fallar en TODOS los clips sin escalar.
_classify_fail: dict = {}          # nombre → fallos consecutivos
_detector_alert_sent: dict = {}    # nombre → ya se avisó esta racha
_detectors_loaded: set = set()     # lo que el registro cargó de verdad
_registry_consulted = False        # False hasta que el registro reporte por primera vez

# Audio (hidrófono desconectado)
_rms_history: deque = deque()
_audio_alert_sent = False

# Captura detenida (F-29): última ventana de audio armada por el clasificador.
_last_clip_mono: float | None = None
_capture_alert_sent = False

# Subidas al almacenamiento fallando (spool de eventos creciendo).
_upload_backlog_streak = 0
_upload_alert_sent = False

# Avisos de degradación pendientes de despacho (los produce cualquier hilo, los
# envía housekeeping). Acotado: si nadie los despacha, no crecen sin fin.
_alert_queue: deque = deque(maxlen=20)

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


def set_loaded_detectors(names) -> None:
    """El registro informa qué detectores cargaron de verdad (R-2.6). Con el
    registro consultado y CERO cargados, detector_ok es false: el contrato dice
    "false when no detector loaded" y antes aquí decía true."""
    global _detectors_loaded, _registry_consulted
    with _lock:
        _detectors_loaded = set(names)
        _registry_consulted = True


# Duty cycle: (monotonic, frames_entregados) en ventana móvil
_frames_window: deque = deque()
_capture_started_mono: float | None = None
_frames_total = 0

# Latidos de hilos para el watchdog (R-2.7): cada loop vigilado llama beat()
# en CADA vuelta, también las vacías. Vida ≠ salud: esto prueba que el hilo
# se agenda, no que el audio fluya (eso lo cubre el fail-loud de captura).
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


# ─── Avisos de degradación (cola; los despacha housekeeping) ─────────────────

def _queue_alert(reason: str) -> None:
    with _lock:
        _alert_queue.append(reason)


def flush_degraded_alerts() -> int:
    """Despacha los avisos encolados: una línea ERROR por episodio, desde
    housekeeping. El aviso al humano lo manda el backend (D-020) cuando ve el
    cambio en health.degraded_reason del siguiente heartbeat — que ya lleva el
    mismo motivo. Devuelve cuántos despachó."""
    sent = 0
    while True:
        with _lock:
            if not _alert_queue:
                return sent
            reason = _alert_queue.popleft()
        log.error("🔴 DEGRADADO: %s — el backend avisa con el próximo heartbeat", reason)
        sent += 1


# ─── Duty cycle (R-1.2, F-05) ────────────────────────────────────────────────

def mark_capture_started() -> None:
    global _capture_started_mono
    with _lock:
        _capture_started_mono = time.monotonic()


def _trim_frames_window(now: float) -> None:
    cutoff = now - C.DUTY_WINDOW_S
    while _frames_window and _frames_window[0][0] < cutoff:
        _frames_window.popleft()


def record_frames(n_frames: int) -> None:
    """Llamado por la captura al entregar bloques. Barato: O(1) amortizado."""
    global _frames_total
    now = time.monotonic()
    with _lock:
        _frames_total += n_frames
        _frames_window.append((now, n_frames))
        _trim_frames_window(now)


def duty_cycle_pct() -> float | None:
    """None solo mientras no hay nada que medir (captura no arrancada o <1 s).
    Con captura arrancada y ventana vacía devuelve 0.0 — un stream muerto
    desde el arranque tiene que verse como 0, no como "sin dato"."""
    with _lock:
        if _capture_started_mono is None:
            return None
        now = time.monotonic()
        span = min(now - _capture_started_mono, C.DUTY_WINDOW_S)
        if span <= 1.0:
            return None
        _trim_frames_window(now)      # F-29: recortar AL LEER, no solo al escribir
        delivered_s = sum(n for _, n in _frames_window) / C.SAMPLE_RATE
        return round(min(100.0, delivered_s / span * 100), 1)


def deaf_seconds_total() -> float:
    with _lock:
        if _capture_started_mono is None:
            return 0.0
        elapsed = time.monotonic() - _capture_started_mono
        return round(max(0.0, elapsed - _frames_total / C.SAMPLE_RATE), 1)


# ─── Captura detenida (F-29) ─────────────────────────────────────────────────

def record_clip() -> None:
    """El clasificador armó una ventana completa de audio. Es la única prueba
    positiva de que la cadena captura → cola → ensamblador sigue viva."""
    global _last_clip_mono
    with _lock:
        _last_clip_mono = time.monotonic()


def last_clip_age_s() -> float | None:
    """Segundos desde la última ventana armada (o desde el arranque de la
    captura si aún no hubo ninguna). None si la captura no arrancó."""
    with _lock:
        if _capture_started_mono is None:
            return None
        ref = _last_clip_mono if _last_clip_mono is not None else _capture_started_mono
        return round(time.monotonic() - ref, 1)


def capture_stalled() -> bool:
    age = last_clip_age_s()
    return age is not None and age >= C.CAPTURE_STALL_S


def capture_health() -> tuple[bool, str | None]:
    """(ok, motivo). ok=False si no se arman ventanas (stall) o si el duty
    cycle medido cayó bajo el mínimo. Es lo que lee build_health."""
    age = last_clip_age_s()
    if age is not None and age >= C.CAPTURE_STALL_S:
        return False, (f"captura detenida: sin ventanas de audio hace {age:.0f} s "
                       f"(límite {C.CAPTURE_STALL_S:.0f} s) — revisar tarjeta/driver")
    duty = duty_cycle_pct()
    if duty is not None and duty < C.CAPTURE_DUTY_MIN_PCT:
        return False, (f"duty cycle {duty:.1f} % bajo el mínimo {C.CAPTURE_DUTY_MIN_PCT:.0f} % "
                       "— la captura pierde audio")
    return True, None


def maybe_alert_capture() -> None:
    """Housekeeping, cada heartbeat. UNA alarma por racha, igual que audio."""
    global _capture_alert_sent
    ok, reason = capture_health()
    if ok:
        if _capture_alert_sent:
            log.info("🎚️ captura recuperada (duty %s %%)", duty_cycle_pct())
        _capture_alert_sent = False
        return
    if not _capture_alert_sent:
        log.error("🔴 CAPTURA DEGRADADA: %s", reason)
        _queue_alert(reason)
        _capture_alert_sent = True


# ─── Detector (F-02) ─────────────────────────────────────────────────────────

def record_classify_result(ok: bool, reason: str = "", detector: str = "psd_tonal") -> None:
    """DETECTOR_FAIL_LIMIT fallos consecutivos DEL MISMO detector → detector_ok
    false + UNA alarma por racha. Devolver {} y seguir en silencio es el patrón
    que produjo F-02. `detector` por defecto psd_tonal: el shim detector.py y
    las herramientas de banco solo conocen ese."""
    with _lock:
        if ok:
            prev = _classify_fail.get(detector, 0)
            if prev >= C.DETECTOR_FAIL_LIMIT:
                log.warning("Detector %s recuperado tras %d fallos", detector, prev)
            _classify_fail[detector] = 0
            _detector_alert_sent[detector] = False
            return
        n = _classify_fail.get(detector, 0) + 1
        _classify_fail[detector] = n
        escalate = n >= C.DETECTOR_FAIL_LIMIT and not _detector_alert_sent.get(detector)
        if escalate:
            _detector_alert_sent[detector] = True
    log.warning("clasificación falló (%s, %d consecutivos): %s", detector, n, reason)
    if escalate:
        log.error("🔴 DETECTOR DEGRADADO: %s — %d fallos consecutivos — la unidad NO está "
                  "detectando con ese detector", detector, n)
        _queue_alert(f"detector {detector} caído ({n} fallos): {reason[:80]}")


def _failing_detectors() -> list:
    """[(nombre, fallos)] de los detectores en racha de fallo. Con lock tomado."""
    names = _detectors_loaded if _registry_consulted else set(_classify_fail)
    return sorted((n, _classify_fail.get(n, 0)) for n in names
                  if _classify_fail.get(n, 0) >= C.DETECTOR_FAIL_LIMIT)


def detector_ok() -> bool:
    if C.CONFIG.snapshot()["detection_mode"] == "rms":
        return True     # el modo rms no usa el clasificador
    with _lock:
        if _registry_consulted and not _detectors_loaded:
            return False    # contrato: "false when no detector loaded"
        return not _failing_detectors()


# ─── Audio (R-2.2) ───────────────────────────────────────────────────────────

def _trim_rms(now: float) -> None:
    cutoff = now - C.AUDIO_HEALTH_WINDOW_S
    while _rms_history and _rms_history[0][0] < cutoff:
        _rms_history.popleft()


def record_rms(rms: float) -> None:
    now = time.time()
    with _lock:
        _rms_history.append((now, rms))
        _trim_rms(now)


def audio_health() -> tuple[bool, float, int]:
    """ok=True si aún faltan datos (no alarmar al arrancar) o si el pico de RMS
    supera el piso. ok=False → probable hidrófono desconectado.

    La ventana se recorta AL LEER (F-29): con el stream muerto no llegan
    muestras nuevas y, sin recorte, el pico viejo seguía valiendo para siempre.
    Si tras recortar quedan <3 muestras y la captura lleva más de la ventana
    arrancada, eso ya no es "arrancando": es que no llega audio, y lo reporta
    capture_health(); aquí se devuelve ok=True para no duplicar el motivo."""
    with _lock:
        _trim_rms(time.time())
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
        _queue_alert(f"sin señal de audio (pico {peak:.4f}) — revisar hidrófono/cable")
        _audio_alert_sent = True


# ─── Subidas al almacenamiento (spool de eventos) ────────────────────────────

def note_upload_backlog() -> None:
    """Housekeeping, cada heartbeat. Cola de eventos > 0 durante
    UPLOAD_BACKLOG_ALERT_TICKS heartbeats seguidos, o ya a media capacidad →
    degradado + UNA alarma por racha. Antes `upload_backlog` solo era un número
    en status.json: una credencial de storage revocada no avisaba a nadie."""
    global _upload_backlog_streak, _upload_alert_sent
    from . import storage  # noqa: PLC0415 — import tardío, evita ciclo
    n = storage.event_spool_len()
    with _lock:
        _upload_backlog_streak = _upload_backlog_streak + 1 if n > 0 else 0
        streak = _upload_backlog_streak
    degraded = streak >= C.UPLOAD_BACKLOG_ALERT_TICKS or n >= C.EVENT_SPOOL_MAX // 2
    if not degraded:
        if _upload_alert_sent and n == 0:
            log.info("☁️ spool de eventos drenado — subidas recuperadas")
            _upload_alert_sent = False
        return
    if not _upload_alert_sent:
        log.error("🔴 SUBIDAS FALLANDO: %d evento/s en cola tras %d heartbeats", n, streak)
        _queue_alert(f"subidas al almacenamiento fallando: {n} evento/s en cola "
                     f"({streak} heartbeats) — revisar credencial/red")
        _upload_alert_sent = True


def _upload_backlog_reason(backlog: int) -> str | None:
    with _lock:
        streak = _upload_backlog_streak
    if streak >= C.UPLOAD_BACKLOG_ALERT_TICKS or backlog >= C.EVENT_SPOOL_MAX // 2:
        return (f"subidas al almacenamiento fallando: {backlog} evento/s en cola "
                f"({streak} heartbeats seguidos)")
    return None


# ─── El bloque health de status.json ─────────────────────────────────────────

def build_health() -> dict:
    from . import push, storage  # noqa: PLC0415 — import tardío, evita ciclo
    audio_ok, peak_rms, _n = audio_health()
    det_ok = detector_ok()
    cap_ok, cap_reason = capture_health()
    reasons = []
    if not det_ok:
        with _lock:
            none_loaded = _registry_consulted and not _detectors_loaded
            failing = _failing_detectors()
        if none_loaded:
            reasons.append("ningún detector cargado (revisar OCEANKIND_DETECTORS / detection_mode)")
        else:
            reasons.append("clasificador caído: " + ", ".join(
                f"{n} ({c} fallos consecutivos)" for n, c in failing))
    if not cap_ok:
        reasons.append(cap_reason)
    if not audio_ok:
        reasons.append(f"sin señal de audio (pico RMS {peak_rms:.4f} bajo el piso "
                       f"{C.AUDIO_FLOOR_RMS}) — revisar hidrófono/cable")
    if push.auth_failed():
        reasons.append("credencial del backend rechazada (401) — push de eventos detenido; "
                       "el blob sigue escribiéndose")
    hb_fail_streak = push.heartbeat_fail_streak()
    if hb_fail_streak >= 3:
        # 3 fallos seguidos = 3 intervalos de heartbeat sin contacto — el
        # mismo umbral que usa el dashboard para marcar "sin contacto"
        # (STALE_AFTER_MS = 3 × heartbeat_interval_s por defecto). Si esto se
        # ve, el link es el problema, no el equipo.
        reasons.append(f"heartbeat: {hb_fail_streak} intentos fallidos seguidos — "
                       "revisar conectividad")
    upload_backlog = storage.event_spool_len()
    backlog_reason = _upload_backlog_reason(upload_backlog)
    if backlog_reason:
        reasons.append(backlog_reason)
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
        "capture_ok":         cap_ok,
        "duty_cycle_pct":     duty_cycle_pct(),
        "deaf_seconds_total": deaf_seconds_total(),
        "last_clip_age_s":    last_clip_age_s(),
        "clips_dropped":      clips_dropped,
        "capture_overflows":  overflows,
        "suppressed_count":   suppressed,
        "upload_backlog":     upload_backlog,
        "events_dropped":     events_dropped,
        "push_enabled":       push.enabled(),
        "push_backlog":       push.spool_len(),
        "push_rejected":      push_rejected,
        "push_dropped":       push_dropped,
        "heartbeat_fail_streak": hb_fail_streak,
        "archive_queue":      _archive_queue_len(),
        "degraded_reason":    "; ".join(reasons) if reasons else None,
    }


def _archive_queue_len() -> int:
    try:
        return sum(1 for _ in C.ARCHIVE_DIR.glob("clip_*.wav")) if C.ARCHIVE_DIR.exists() else 0
    except Exception:
        return 0
