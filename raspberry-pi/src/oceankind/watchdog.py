"""Watchdog systemd (R-2.7): el caso del CUELGUE, que nada detectaba.

systemd reinicia un proceso que crashea; nada notaba uno que se cuelga. Con
WatchdogSec en la unidad, systemd espera pings WATCHDOG=1 por NOTIFY_SOCKET;
si dejan de llegar, mata y reinicia el servicio.

Quién decide pingear: el loop principal (housekeeping), y SOLO si los hilos
trabajadores (clasificador y transporte) dieron señales de vida recientes
(health.beat en cada vuelta de sus loops, incluso vacías). Así:

  - se cuelga el main        → no hay pings          → reinicio
  - se cuelga el clasificador→ beat viejo → sin ping → reinicio
  - se cuelga el transporte  → ídem                  → reinicio
  - hidrófono muerto, red caída, detector degradado → NO es cuelgue: esos
    estados ya gritan por health/degraded_reason y un reinicio en loop solo
    los taparía. El watchdog no opina sobre salud, solo sobre vida.

Sin NOTIFY_SOCKET (corrida a mano, banco sin systemd) todo esto es no-op.
El ping usa el protocolo sd_notify crudo (datagrama UNIX) — sin dependencias.
"""

import logging
import os
import socket
import time

from . import config as C
from . import health

log = logging.getLogger("oceankind")

_sock: socket.socket | None = None
_addr: str | None = None
_last_ping = 0.0
_starved_logged = False


def arm() -> bool:
    """Prepara el socket si systemd nos dio NOTIFY_SOCKET. Devuelve si quedó armado."""
    global _sock, _addr
    _addr = os.environ.get("NOTIFY_SOCKET") or None
    if not _addr:
        log.info("Watchdog: sin NOTIFY_SOCKET — no armado (normal fuera de systemd)")
        return False
    if _addr.startswith("@"):          # socket abstracto de Linux
        _addr = "\0" + _addr[1:]
    _sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    log.info("Watchdog: armado (ping cada %.0fs si los hilos laten; systemd manda WatchdogSec)",
             C.WATCHDOG_PING_S)
    return True


def _ping() -> None:
    try:
        _sock.sendto(b"WATCHDOG=1", _addr)
    except Exception as exc:
        log.warning("Watchdog: ping falló (%s)", exc)


def tick() -> None:
    """Llamar en cada vuelta del loop principal (~1 s). Pingea a intervalo SOLO
    si todos los hilos vigilados latieron hace poco. Un hilo colgado deja de
    latir → dejamos de pingear → systemd reinicia. A gritos en el log mientras
    tanto, por si alguien está mirando."""
    global _last_ping, _starved_logged
    if _sock is None:
        return
    now = time.monotonic()
    if now - _last_ping < C.WATCHDOG_PING_S:
        return
    stale = health.stale_beats(C.WATCHDOG_BEAT_MAX_S)
    if stale:
        if not _starved_logged:
            log.error("🔴 Watchdog: hilo/s sin señales de vida %s — se RETIENE el ping; "
                      "systemd reiniciará el servicio al vencer WatchdogSec", stale)
            _starved_logged = True
        return
    _starved_logged = False
    _last_ping = now
    _ping()
