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

from . import config as C
from . import health
from . import storage

log = logging.getLogger("oceankind")

_lock = threading.Lock()
_auth_failed = False          # 401: credencial rechazada/revocada → dejar de intentar
_backend_down_logged = False  # dedup del log de "backend inalcanzable"


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
            "X-Device-Id":  C.DEVICE_ID,
            "X-Device-Key": C.DEVICE_KEY,
        },
    )
    eid = str(event.get("event_id", "?"))[:8]
    try:
        with urllib.request.urlopen(req, timeout=C.BACKEND_TIMEOUT_S) as resp:
            code = resp.status
    except urllib.error.HTTPError as exc:
        code = exc.code
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
        log.error("push %s: 401 — credencial del backend rechazada. Push DETENIDO hasta "
                  "reinicio con OCEANKIND_DEVICE_KEY corregida. El blob sigue escribiéndose.", eid)
        return "auth"
    if code in (400, 403):
        # 400 = bug nuestro (documento malformado / captured_utc naive).
        # 403 = site no coincide con el registro (error de aprovisionamiento).
        health.count_push_rejected()
        log.error("push %s: %d — %s. El evento queda en el blob; este push no se reintenta.",
                  eid, code,
                  "documento malformado o captured_utc sin offset (bug del dispositivo)"
                  if code == 400 else
                  "site no coincide con el registro del dispositivo (aprovisionamiento)")
        return "reject"
    if code >= 500:
        if not _backend_down_logged:
            log.warning("push %s: backend %d — se reintenta desde el spool", eid, code)
            _backend_down_logged = True
        return "retry"
    log.warning("push %s: respuesta inesperada %d — tratada como reintentable", eid, code)
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


def spool_len() -> int:
    try:
        return sum(1 for _ in C.PUSH_SPOOL_DIR.glob("*.json")) if C.PUSH_SPOOL_DIR.is_dir() else 0
    except Exception:
        return 0
