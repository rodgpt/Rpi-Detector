"""Push directo de eventos al backend del dashboard (contrato §Event upload).

Dos caminos por evento, deliberadamente, y no son alternativas:

    dispositivo ──► blob ──► backend      durable. EL registro. contra esto reconcilia
        └───────► POST /api/devices/events   rápido. puede fallar. no lleva nada que el blob no lleve

**El invariante:** el blob se escribe pase lo que pase con este endpoint, y
ningún evento se descarta jamás por el resultado de un push. Este módulo
compra latencia (el índice del dashboard se entera al instante) y nada más.

Corre en el hilo de transporte y en el drenado del heartbeat. Nunca en captura.
Un evento por request, sin lotes (contrato). Idempotente en event_id: 202
siempre, así cada reintento es seguro por construcción y no llevamos cuenta
de lo ya enviado.

Deshabilitado (sin OCEANKIND_BACKEND_URL) el dispositivo es blob-only y el
índice del dashboard se actualiza solo por su pase de reconciliación.
"""

import json
import logging
import threading
import urllib.error
import urllib.request

from . import __version__
from . import config as C
from . import health
from . import storage

log = logging.getLogger("oceankind")

# Sin esto, urllib manda "Python-urllib/3.13" — la firma por defecto que
# Cloudflare Bot Fight Mode bloquea en el borde (error 1010), antes de que la
# petición llegue al backend. El dispositivo nunca veía eso: solo el código
# 403, indistinguible de un site mismatch real. Costó una sesión entera de
# depuración descubrirlo porque el backend nunca vio la petición — sus logs
# no mostraban NADA — y push.py imprimía un mensaje inventado en vez del
# cuerpo real de la respuesta. Ver TODO.md, 2026-09-22.
_USER_AGENT = f"oceankind-device/{__version__}"

_lock = threading.Lock()
_auth_failed = False          # 401: credencial rechazada/revocada → dejar de intentar
_backend_down_logged = False  # dedup del log de "backend inalcanzable" (eventos)

# ── Heartbeat (salud/telemetría, D-0xx) ───────────────────────────────────────
# Camino aparte de push_event(): sin spool y sin cola propia. Comparte
# _auth_failed/auth_failed() con eventos (misma credencial), pero NO comparte
# _backend_down_logged (pueden estar caídos por separado, aunque sea raro).
_hb_fail_streak  = 0      # fallos consecutivos del POST de heartbeat
_hb_down_logged  = False  # dedup del log de "backend inalcanzable" (heartbeat)


def enabled() -> bool:
    return bool(C.BACKEND_URL)


def auth_failed() -> bool:
    with _lock:
        return _auth_failed


def _serialize(event: dict) -> bytes:
    """Byte-idéntico al blob (contrato): mismo sanitizado y mismo dumps que
    storage.upload_json. Sin envoltorio, sin segundo esquema."""
    return json.dumps(storage.sanitize_for_json(event), indent=2, allow_nan=False).encode()


def _post(event: dict) -> str:
    """Un POST, un evento. Devuelve 'ok' | 'retry' | 'reject' | 'auth' según la
    tabla de códigos del contrato."""
    global _auth_failed, _backend_down_logged
    req = urllib.request.Request(
        f"{C.BACKEND_URL.rstrip('/')}/api/devices/events",
        data=_serialize(event),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent":   _USER_AGENT,
            "X-Device-Id":  C.DEVICE_ID,
            "X-Device-Key": C.DEVICE_KEY,
        },
    )
    eid = str(event.get("event_id", "?"))[:8]
    body_snippet = ""
    try:
        with urllib.request.urlopen(req, timeout=C.BACKEND_TIMEOUT_S) as resp:
            code = resp.status
    except urllib.error.HTTPError as exc:
        code = exc.code
        # El cuerpo real, no una adivinanza. Es lo que distinguió un rechazo
        # genuino del backend ("site mismatch: document says…") de un bloqueo
        # de Cloudflare ("error code: 1010") que nunca llegó al backend — con
        # el mismo código 403 en ambos casos, indistinguibles sin esto.
        try:
            body_snippet = exc.read(500).decode("utf-8", errors="replace").strip()
        except Exception:
            pass
    except Exception as exc:           # timeout, DNS, conexión rechazada…
        if not _backend_down_logged:
            log.warning("push %s: backend inalcanzable (%s) — normal si está desplegando; "
                        "se reintenta desde el spool", eid, exc)
            _backend_down_logged = True
        return "retry"

    if code == 202:
        _backend_down_logged = False
        return "ok"
    if code == 401:
        # Un dispositivo revocado que deja de reportar en silencio es
        # indistinguible de uno muerto: evento de salud, no línea de log.
        with _lock:
            _auth_failed = True
        log.error("push %s: 401 — credencial del backend rechazada (%s). Push DETENIDO hasta "
                  "reinicio con OCEANKIND_DEVICE_KEY corregida. El blob sigue escribiéndose.",
                  eid, body_snippet or "sin cuerpo")
        return "auth"
    if code in (400, 403):
        # El cuerpo real de la respuesta, no una adivinanza fija. Un 403 con
        # cuerpo JSON tipo {"detail":"site mismatch: ..."} es un rechazo real
        # del backend (aprovisionamiento). Un 403 con cuerpo tipo
        # "error code: 1010" y sin JSON es Cloudflare bloqueando la petición
        # ANTES de que llegue al backend — nunca aparecerá en sus logs, y sin
        # el cuerpo aquí ambos casos eran indistinguibles.
        health.count_push_rejected()
        log.error("push %s: %d — %s. El evento queda en el blob; este push no se reintenta.",
                  eid, code, body_snippet or "sin cuerpo de respuesta")
        return "reject"
    if code >= 500:
        if not _backend_down_logged:
            log.warning("push %s: backend %d (%s) — se reintenta desde el spool",
                       eid, code, body_snippet or "sin cuerpo")
            _backend_down_logged = True
        return "retry"
    log.warning("push %s: respuesta inesperada %d (%s) — tratada como reintentable",
               eid, code, body_snippet or "sin cuerpo")
    return "retry"


def push_event(event: dict) -> None:
    """Intenta el push; si no procede, lo encola. JAMÁS levanta: el llamador
    (transporte) ya escribió el blob y el resultado del push no puede tocar
    nada de eso (el invariante del contrato)."""
    if not enabled():
        return
    if auth_failed():
        _spool(event)
        return
    try:
        outcome = _post(event)
    except Exception as exc:
        log.warning("push: error inesperado (%s) — encolado", exc)
        outcome = "retry"
    if outcome in ("retry", "auth"):
        _spool(event)


def spool_for_later(event: dict) -> None:
    """Para trabajos que nunca llegaron al POST (cierre ordenado, cola llena)."""
    if enabled():
        _spool(event)


def _spool(event: dict) -> None:
    """Cola acotada en STATE_DIR. Desborde: se descarta el más viejo, contado.
    Aceptable porque el blob tiene el registro; el push solo compra latencia."""
    try:
        C.PUSH_SPOOL_DIR.mkdir(parents=True, exist_ok=True)
        (C.PUSH_SPOOL_DIR / f"{event['event_id']}.json").write_bytes(_serialize(event))
        queue = sorted(C.PUSH_SPOOL_DIR.glob("*.json"))
        if len(queue) > C.PUSH_SPOOL_MAX:
            dropped = len(queue) - C.PUSH_SPOOL_MAX
            for old in queue[:dropped]:
                old.unlink(missing_ok=True)
            health.count_push_dropped(dropped)
            log.warning("spool de push lleno — %d push/es más antiguo/s descartado/s "
                        "(el blob los conserva; reconcile los indexará)", dropped)
    except Exception as exc:
        health.count_push_dropped(1)
        log.warning("no se pudo encolar el push (%s) — el blob conserva el evento", exc)


def drain_push_spool() -> None:
    """Reintenta el spool completo, un request por evento, en cada heartbeat.
    Se corta ante backend caído o 401; los rechazos terminales salen del spool."""
    if not enabled() or auth_failed() or not C.PUSH_SPOOL_DIR.is_dir():
        return
    for f in sorted(C.PUSH_SPOOL_DIR.glob("*.json")):
        try:
            event = json.loads(f.read_text())
        except Exception:
            f.unlink(missing_ok=True)
            continue
        outcome = _post(event)
        if outcome == "retry":
            break                      # backend sigue caído; no insistir esta vuelta
        f.unlink(missing_ok=True)      # ok o rechazo terminal: fuera del spool
        if outcome == "ok":
            log.info("  → push pendiente entregado: %s", str(event.get("event_id", "?"))[:8])
        if outcome == "auth":
            break


def heartbeat_fail_streak() -> int:
    with _lock:
        return _hb_fail_streak


def post_heartbeat(status: dict) -> None:
    """POST de salud/telemetría (contrato §Device heartbeat) — NO es un evento:
    nunca toca la cola de eventos, el spool de eventos, ni `auth_failed()` para
    NADA que no sea leerlo.

    Sin reintento, a propósito. El valor de un heartbeat es "vivo AHORA
    MISMO"; uno que llegue tarde no vale nada, y si se reintentara podría
    entregarse DESPUÉS de uno más fresco y sobreescribirlo con datos viejos
    (regresión). El siguiente tick, heartbeat_interval_s después, ya lo
    reemplaza — fallar y descartar es lo correcto, no una carencia. El
    backend además hace cumplir esto del otro lado (last_seen monótono):
    doble seguro, no solo disciplina del dispositivo.

    No depende de almacenamiento (Azure/OUTPUT_DIR): status.json es un
    resumen que se sobreescribe cada tick, sin valor histórico propio, así
    que no necesita la durabilidad que sí exige un evento — se autorrepara
    solo en el siguiente heartbeat si uno se pierde.
    """
    global _auth_failed, _hb_fail_streak, _hb_down_logged
    if not enabled() or auth_failed():
        return
    req = urllib.request.Request(
        f"{C.BACKEND_URL.rstrip('/')}/api/devices/heartbeat",
        data=_serialize(status),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent":   _USER_AGENT,
            "X-Device-Id":  C.DEVICE_ID,
            "X-Device-Key": C.DEVICE_KEY,
        },
    )
    body_snippet = ""
    try:
        with urllib.request.urlopen(req, timeout=C.BACKEND_TIMEOUT_S) as resp:
            code = resp.status
    except urllib.error.HTTPError as exc:
        code = exc.code
        try:
            body_snippet = exc.read(500).decode("utf-8", errors="replace").strip()
        except Exception:
            pass
    except Exception as exc:           # timeout, DNS, conexión rechazada…
        with _lock:
            _hb_fail_streak += 1
        if not _hb_down_logged:
            log.warning("heartbeat: backend inalcanzable (%s) — sin cola, se reintenta "
                       "solo (con datos frescos) en el próximo tick", exc)
            _hb_down_logged = True
        return

    if code == 202:
        with _lock:
            _hb_fail_streak = 0
        _hb_down_logged = False
        return
    if code == 401:
        # Misma credencial que los eventos: un 401 aquí también los detiene,
        # y viceversa (auth_failed() es compartido).
        with _lock:
            _auth_failed = True
            _hb_fail_streak += 1
        log.error("heartbeat: 401 — credencial del backend rechazada (%s). Heartbeat y push "
                  "de eventos DETENIDOS hasta reinicio con OCEANKIND_DEVICE_KEY corregida.",
                  body_snippet or "sin cuerpo")
        return
    # Cualquier otro código (400/403/5xx/inesperado): se descarta igual, sin
    # distinguir motivo — no hay spool ni nada que conservar.
    with _lock:
        _hb_fail_streak += 1
    if not _hb_down_logged:
        log.warning("heartbeat: %d (%s) — descartado, sin reintento", code,
                   body_snippet or "sin cuerpo")
        _hb_down_logged = True


def spool_len() -> int:
    try:
        return sum(1 for _ in C.PUSH_SPOOL_DIR.glob("*.json")) if C.PUSH_SPOOL_DIR.is_dir() else 0
    except Exception:
        return 0
