"""Disparador OTA por asignación (D-021): converger a la release asignada.

El panel asigna una release (un tag git) por dispositivo; viaja dentro del
documento de configuración firmado, junto a los umbrales. Este módulo es lo
único que hace algo con ella en el proceso:

  1. recuerda la asignación (`.assigned_release`, y la publica en status.json → ota)
  2. si la unidad no corre esa release, no está retenida (hold), esa release no
     falló ya en esta unidad y no hay otra OTA en curso, lanza
     `update_oceankind.sh --ref <tag>` DESACOPLADO del servicio

Desacoplado porque el script reinicia este mismo servicio para verificar el
build: si fuera hijo nuestro moriría con nosotros a mitad de la instalación —
el corte que el journal del script existe para reparar, provocado a propósito.
`start_new_session=True` lo saca de nuestro cgroup lógico de señales y systemd
no lo mata con el servicio (KillMode=control-group sí lo haría; el script, al
reiniciarnos, lo hace desde fuera vía sudo systemctl, y sobrevive porque corre
en su propia sesión). El log va a /tmp/oceankind/logs/update.log.

Todo lo que el OTA sabe se lee de los ficheros que el script escribe
(`.installed_sha`, `.installed_release`, `.ota_failed_*`): este proceso nunca
necesita git para informar qué corre. Corre en housekeeping, jamás en captura.
"""

import logging
import os
import subprocess
import threading
import time

from . import config as C

log = logging.getLogger("oceankind")

_lock = threading.Lock()
_assigned: str | None = None
_hold = False
_last_trigger_mono = 0.0
_last_trigger_ref: str | None = None


def _read(path) -> str | None:
    try:
        v = path.read_text().strip()
        return v or None
    except OSError:
        return None


def installed_sha() -> str | None:
    return _read(C.INSTALLED_SHA_FILE)


def installed_release() -> str | None:
    return _read(C.INSTALLED_REF_FILE)


def failed_sha() -> str | None:
    return _read(C.FAILED_SHA_FILE)


def failed_release() -> str | None:
    return _read(C.FAILED_REF_FILE)


def in_progress() -> bool:
    """El script toma un mkdir-lock con su pid; un lock huérfano (pid muerto)
    no cuenta como 'en curso' — el propio script lo limpia al arrancar."""
    pid_file = C.OTA_LOCK_DIR / "pid"
    if not C.OTA_LOCK_DIR.is_dir():
        return False
    pid = _read(pid_file)
    if not pid or not pid.isdigit():
        return True
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _load_assignment() -> None:
    """Al arrancar: lo último que una config verificada dejó escrito. Así una
    unidad reiniciada sabe su asignación antes del primer poll."""
    global _assigned, _hold
    raw = _read(C.ASSIGNED_RELEASE_FILE)
    if raw is None:
        return
    # formato: "<release o -> <hold 0|1>"
    parts = raw.split()
    rel = parts[0] if parts else "-"
    with _lock:
        _assigned = None if rel == "-" else rel
        _hold = len(parts) > 1 and parts[1] == "1"


_load_assignment()


def record_assignment(release: str | None, hold: bool) -> None:
    """Llamado por main tras aplicar una config verificada. Persistido para el
    timer diario del script (que lee el mismo fichero cuando corre sin --ref)."""
    global _assigned, _hold
    with _lock:
        _assigned, _hold = release, bool(hold)
    try:
        C.ASSIGNED_RELEASE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = C.ASSIGNED_RELEASE_FILE.with_suffix(".tmp")
        tmp.write_text(f"{release or '-'} {1 if hold else 0}\n")
        os.replace(tmp, C.ASSIGNED_RELEASE_FILE)
    except OSError as exc:
        # Overlay activo o disco lleno: la asignación sigue en memoria y en el
        # heartbeat; el script sabe pedirla al backend si el fichero no está.
        log.warning("ota: no se pudo persistir la asignación (%s)", exc)


def status_block() -> dict:
    """status.json → ota. Todas las claves, siempre (contrato)."""
    with _lock:
        assigned, hold = _assigned, _hold
    return {
        "installed_sha":     installed_sha(),
        "installed_release": installed_release(),
        "failed_sha":        failed_sha(),
        "failed_release":    failed_release(),
        "assigned_release":  assigned,
        "hold":              hold,
        "in_progress":       in_progress(),
    }


def maybe_trigger() -> str:
    """Housekeeping, tras cada poll de config. Devuelve qué decidió, para el
    log y para las pruebas: 'hold' | 'none' | 'current' | 'failed' | 'busy' |
    'cooldown' | 'invalid' | 'no-script' | 'launched' | 'error'."""
    global _last_trigger_mono, _last_trigger_ref
    with _lock:
        assigned, hold = _assigned, _hold
    if hold:
        return "hold"
    if not assigned:
        return "none"
    if not C.RELEASE_RE.match(assigned):
        return "invalid"
    if assigned == installed_release():
        return "current"
    if assigned == failed_release():
        # Ya rompió esta unidad y se revirtió. Reintentarlo solo sería un bucle
        # de crash con buena cara; el panel ve failed_sha y decide un humano.
        return "failed"
    if in_progress():
        return "busy"
    now = time.monotonic()
    if _last_trigger_ref == assigned and now - _last_trigger_mono < C.OTA_RETRIGGER_S:
        return "cooldown"
    if not C.OTA_SCRIPT.is_file():
        log.error("ota: asignada %s pero no existe %s — re-provisionar con setup.sh",
                  assigned, C.OTA_SCRIPT)
        return "no-script"
    try:
        C.OTA_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(C.OTA_LOG_FILE, "ab") as logf:
            # argv explícito, sin shell: el tag ya pasó RELEASE_RE y aun así
            # jamás se interpola en una cadena de comandos.
            subprocess.Popen(
                ["/bin/bash", str(C.OTA_SCRIPT), "--ref", assigned],
                stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
                start_new_session=True, close_fds=True,
            )
    except Exception as exc:        # noqa: BLE001
        log.error("ota: no se pudo lanzar la actualización a %s: %s", assigned, exc)
        return "error"
    _last_trigger_mono, _last_trigger_ref = now, assigned
    log.warning("⬆️  OTA lanzada: %s → %s (log en %s). El servicio se reiniciará para verificar.",
                installed_release() or installed_sha() or "desconocida", assigned, C.OTA_LOG_FILE)
    return "launched"
