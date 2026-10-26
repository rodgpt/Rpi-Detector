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

**Desacoplado de verdad = en OTRO cgroup, no en otra sesión.** Bajo systemd se
lanza como unidad transitoria propia (`sudo systemctl start --no-block
oceankind-ota@<tag>.service`, plantilla que instala setup.sh), que
KillMode=control-group del servicio no alcanza. La primera versión de este
módulo usaba `start_new_session=True` y creía que bastaba: no basta. Una nueva
sesión sigue dentro del cgroup del servicio, así que `systemctl restart
oceankind` mataba al propio script en mitad de la verificación; el servicio
nuevo arrancaba, no veía `.installed_release`, relanzaba el script, que leía el
journal en `verifying`, reiniciaba el servicio… y moría otra vez. Un bucle cada
siete segundos, visto en el banco el 2026-10-24 con la primera release asignada
(el harness de fallos no podía verlo: su `systemctl` es un stub). Fuera de
systemd (banco a mano, pruebas) se usa Popen desacoplado, que ahí sí sobrevive.

Segunda capa: el instante del último lanzamiento se persiste en STATE_DIR, así
que OTA_RETRIGGER_S vale también a través de reinicios del servicio y ningún
bucle de este tipo puede ir más rápido que uno por media hora.
El log del script va a /tmp/oceankind/logs/update.log (y al journal de su unidad).

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

# systemd pone INVOCATION_ID en el entorno de cada servicio que arranca. Es la
# señal de "estamos bajo systemd": ahí la única forma de sobrevivir al restart
# es una unidad aparte. Sin ella (banco a mano, pruebas) se usa Popen.
UNDER_SYSTEMD = bool(os.environ.get("INVOCATION_ID"))
OTA_UNIT_TEMPLATE = "oceankind-ota@{ref}.service"


def _load_last_trigger() -> None:
    """Último lanzamiento persistido: "<epoch> <ref>". Sobrevive a los reinicios
    del servicio (tmpfs), no a un reboot — que ya corta cualquier bucle."""
    global _last_trigger_mono, _last_trigger_ref
    raw = _read(C.OTA_LAST_TRIGGER_FILE)
    if not raw:
        return
    parts = raw.split()
    try:
        age = max(0.0, time.time() - float(parts[0]))
    except (ValueError, IndexError):
        return
    _last_trigger_mono = time.monotonic() - age
    _last_trigger_ref = parts[1] if len(parts) > 1 else None


def _save_last_trigger(ref: str) -> None:
    try:
        C.OTA_LAST_TRIGGER_FILE.parent.mkdir(parents=True, exist_ok=True)
        C.OTA_LAST_TRIGGER_FILE.write_text(f"{time.time():.0f} {ref}\n")
    except OSError as exc:
        log.warning("ota: no se pudo persistir el último lanzamiento (%s)", exc)


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
_load_last_trigger()


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
    # Se anota ANTES de lanzar: si el lanzamiento mata este proceso (restart del
    # servicio) el siguiente arranque ya sabe que acaba de ocurrir.
    _last_trigger_mono, _last_trigger_ref = now, assigned
    _save_last_trigger(assigned)
    how = _launch(assigned)
    if how is None:
        return "error"
    log.warning("⬆️  OTA lanzada (%s): %s → %s (log en %s). El servicio se reiniciará para verificar.",
                how, installed_release() or installed_sha() or "desconocida", assigned, C.OTA_LOG_FILE)
    return "launched"


def _launch(ref: str) -> str | None:
    """Lanza el script fuera de nuestro cgroup. Devuelve cómo ('systemd' |
    'popen') o None si no se pudo. argv explícito, sin shell: el tag ya pasó
    RELEASE_RE y aun así jamás se interpola en una cadena de comandos."""
    if UNDER_SYSTEMD:
        unit = OTA_UNIT_TEMPLATE.format(ref=ref)
        try:
            r = subprocess.run(
                ["sudo", "-n", "systemctl", "start", "--no-block", unit],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
            )
        except Exception as exc:        # noqa: BLE001
            log.error("ota: no se pudo arrancar %s: %s", unit, exc)
            return None
        if r.returncode == 0:
            return "systemd"
        # Sin la plantilla (unidad provisionada antes de hoy) o sin sudo: decirlo
        # y NO caer a Popen, que aquí moriría con el restart y provocaría el bucle.
        log.error("ota: `systemctl start %s` falló (%s): %s — re-provisionar con setup.sh "
                  "(instala la plantilla oceankind-ota@.service) o asignar desde el timer diario",
                  unit, r.returncode, (r.stderr or r.stdout).strip()[:200])
        return None
    try:
        C.OTA_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(C.OTA_LOG_FILE, "ab") as logf:
            subprocess.Popen(
                ["/bin/bash", str(C.OTA_SCRIPT), "--ref", ref],
                stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
                start_new_session=True, close_fds=True,
            )
        return "popen"
    except Exception as exc:        # noqa: BLE001
        log.error("ota: no se pudo lanzar la actualización a %s: %s", ref, exc)
        return None
