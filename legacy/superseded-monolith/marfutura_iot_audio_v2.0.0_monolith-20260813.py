#!/usr/bin/env python3
"""
OceanKind — monitor acústico. Contrato de datos v2 (docs/DATA-CONTRACT.md).

Graba clips de 5 s en continuo, los clasifica (detector PSD tonal, con fallback
RMS real), y escribe el árbol v2 al almacenamiento:

    sites/{site}/status.json                    estado + salud, cada heartbeat
    sites/{site}/power_history.json             telemetría solar en buckets
    sites/{site}/events/YYYY/MM/DD/*.json       UN blob por detección, inmutable
    sites/{site}/clips/YYYY/MM/DD/{uuid}.wav    audio de eventos notificados
    _sites.json                                 registro de sitios (merge al arrancar)

Sin manifest.json (retirado en v2 — D-016). Alertas WhatsApp/voz vía Twilio,
config remota desde sites/{site}/remote_config.json.

Configuración y secretos: /etc/oceankind.env (ver raspberry-pi/oceankind.env).
El servicio NO ARRANCA sin OCEANKIND_TWILIO_SID/TOKEN ni, con almacenamiento
configurado, sin OCEANKIND_SITE y coordenadas (R-8.1, contrato v2).

Banco de pruebas sin Azure (R-9.4): OCEANKIND_OUTPUT_DIR=./out escribe el mismo
árbol a disco; se valida con  python3 tools/validate_contract.py ./out

Dependencias: raspberry-pi/requirements.txt
"""

import json
import logging
import os
import re
import subprocess
import sys
import time
import uuid
import wave
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Hora local ───────────────────────────────────────────────────────────────
# Los mensajes de WhatsApp van en hora local de Chile (antes iban en UTC y
# confundían al leerlos). Internamente todo se sigue guardando en UTC.
try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo(os.getenv("OCEANKIND_TZ", "America/Santiago"))
except Exception:                              # fallback si faltara tzdata
    LOCAL_TZ = timezone(timedelta(hours=-4))


def fmt_local(dt=None, fmt="%Y-%m-%d %H:%M"):
    """Formatea un datetime en hora local de Chile. Sin argumento usa 'ahora'."""
    d = dt or datetime.now(timezone.utc)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(LOCAL_TZ).strftime(fmt)


def fmt_local_iso(iso_str, fmt="%Y-%m-%d %H:%M"):
    """Convierte un timestamp ISO (UTC, como se guarda) a hora local de Chile."""
    try:
        return fmt_local(datetime.fromisoformat(iso_str), fmt)
    except Exception:
        return iso_str or "n/d"

import numpy as np

# --- Configuración -----------------------------------------------------------

IOTHUB_CONNECTION_STRING  = os.environ.get("OCEANKIND_IOTHUB_CONNECTION_STRING", "")
STORAGE_CONNECTION_STRING = os.environ.get("OCEANKIND_STORAGE_CONNECTION_STRING", "")
# Contenedor configurable: las unidades nuevas escriben v2 a un blob NUEVO,
# separado del contenedor de los prototipos (D-016).
STORAGE_CONTAINER         = os.environ.get("OCEANKIND_STORAGE_CONTAINER", "alerts")
# Modo banco/validación (R-9.4): si está definido, todos los "blobs" se escriben
# como archivos bajo este directorio, con los mismos paths del contrato, y el
# árbol resultante se valida con tools/validate_contract.py. Sin Azure.
OUTPUT_DIR                = os.environ.get("OCEANKIND_OUTPUT_DIR", "").strip()
DASHBOARD_URL             = os.environ.get("OCEANKIND_DASHBOARD_URL",
                                           "https://marfuturatest.z6.web.core.windows.net/index.html")
SOFTWARE_VERSION          = "2.0.0"
DEVICE_ID                 = os.environ.get("OCEANKIND_DEVICE_ID", "Rpi_casa")

# Sitio (contrato v2): TODO lo que este dispositivo escribe vive bajo
# sites/{SITE}/… — nada en la raíz del contenedor (R-5.1). Obligatorio cuando
# hay almacenamiento configurado; minúsculas, [a-z0-9_-].
SITE                      = os.environ.get("OCEANKIND_SITE", "").strip().strip("/").lower()

STORAGE_ENABLED           = bool(STORAGE_CONNECTION_STRING or OUTPUT_DIR)

# URL del web admin del modem ZTE (LAN, sin login). Si la API responde, se reporta señal.
MODEM_API_URL             = os.environ.get("OCEANKIND_MODEM_API", "http://192.168.0.1/goform/goform_get_cmd_process")

# Ubicación del sensor — SIN default en código (F-08). Se define por unidad en
# /etc/oceankind.env. Si falta, status.json publica null y el dashboard usa su
# propia tabla de sitios.
def _env_float(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


SENSOR_LAT           = _env_float("OCEANKIND_SENSOR_LAT")
SENSOR_LON           = _env_float("OCEANKIND_SENSOR_LON")
SENSOR_LOCATION_NAME = os.environ.get("OCEANKIND_SENSOR_LOCATION", DEVICE_ID)

# WhatsApp — Twilio. SIN defaults literales (F-04): un secreto como default llegó
# a un backup, dos .pyc y un remote de git. Vienen de /etc/oceankind.env o el
# servicio NO ARRANCA (ver validate_startup_config). Para banco de pruebas sin
# Twilio: OCEANKIND_ALLOW_NO_TWILIO=1 (queda declarado en health.degraded_reason).
TWILIO_ACCOUNT_SID   = os.environ.get("OCEANKIND_TWILIO_SID", "")
TWILIO_AUTH_TOKEN    = os.environ.get("OCEANKIND_TWILIO_TOKEN", "")
ALLOW_NO_TWILIO      = os.environ.get("OCEANKIND_ALLOW_NO_TWILIO", "0") == "1"
TWILIO_CONFIGURED    = bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN)
# Número de PRODUCCIÓN de WhatsApp Business (ya NO el sandbox).
# Envía SOLO con plantillas aprobadas (Content SID). Sin ventana de 24h ni "join tears-rising".
TWILIO_FROM          = os.environ.get("OCEANKIND_TWILIO_FROM", "whatsapp:+56926280872")
# Soporta lista separada por comas en OCEANKIND_TWILIO_TO para múltiples destinatarios.
# Cada alerta se envía a TODOS los números.
_TWILIO_TO_RAW       = os.environ.get("OCEANKIND_TWILIO_TO", "whatsapp:+56961987942")
TWILIO_TO_LIST       = [n.strip() for n in _TWILIO_TO_RAW.split(",") if n.strip()]
TWILIO_TO            = TWILIO_TO_LIST[0] if TWILIO_TO_LIST else ""  # compat con checks legacy


def _to_list(env_name: str) -> list:
    """Lista de destinatarios de un tipo de aviso, con fallback a OCEANKIND_TWILIO_TO."""
    raw = os.environ.get(env_name, "").strip() or _TWILIO_TO_RAW
    return [n.strip() for n in raw.split(",") if n.strip()]


# Destinatarios SEPARADOS POR TIPO. Como cada Pi tiene su propio /etc/oceankind.env,
# esto queda además separado por sitio automáticamente.
#   OCEANKIND_TO_ALERTA  → detecciones (lo que importa a quien fiscaliza)
#   OCEANKIND_TO_TECNICO → batería y heartbeat (ruido operacional, normalmente solo al equipo técnico)
# Si no se definen, ambos caen a OCEANKIND_TWILIO_TO y el comportamiento es el de siempre.
TO_ALERTA  = _to_list("OCEANKIND_TO_ALERTA")
TO_TECNICO = _to_list("OCEANKIND_TO_TECNICO")

# Content SIDs de las plantillas de WhatsApp aprobadas (Twilio Content Template Builder).
# Aprobadas por Meta el 28-jul-2026, con campo "Estación: {{1}}" (dicen MAR FUTURA, hora de Chile).
WA_TPL_ALERT     = os.environ.get("OCEANKIND_WA_TPL_ALERT",     "HX901414845dda773c146ce65d79f79863")  # alerta_deteccion_v5
WA_TPL_HEARTBEAT = os.environ.get("OCEANKIND_WA_TPL_HEARTBEAT", "HXe9015f651079907384ecabf3add8dd69")  # heartbeat_sistema_v3
WA_TPL_BATTERY   = os.environ.get("OCEANKIND_WA_TPL_BATTERY",   "HX9cad665cd4be3a5a5482162d1776d1ba")  # bateria_baja_v3
# Versión del juego de plantillas de WhatsApp en uso:
#   "2" = plantillas v2 (sin campo de estación) → la estación se antepone a {{1}}  [LEGACY]
#   "3" = plantillas v5/v3 (con "Estación: {{1}}") → el resto de variables se corre un lugar  [PRODUCCIÓN]
WA_TPL_VERSION   = os.environ.get("OCEANKIND_WA_TPL_VERSION", "3").strip()

# Llamada de voz ante CLÚSTER de detecciones (Twilio Voice). Se dispara cuando hay
# CALL_CLUSTER_COUNT o más detecciones (que ya pasaron el doble gate RMS+ML) dentro de
# CALL_CLUSTER_WINDOW_S segundos, con cooldown. Cuesta por minuto y es outbound.
CALL_ENABLED          = os.environ.get("OCEANKIND_CALL_ENABLED", "1") == "1"
CALL_FROM             = os.environ.get("OCEANKIND_CALL_FROM", "+15733060329")   # número de voz Twilio
CALL_TO_LIST          = [n.strip() for n in os.environ.get("OCEANKIND_CALL_TO", "+56961987942").split(",") if n.strip()]
CALL_CLUSTER_COUNT    = int(os.environ.get("OCEANKIND_CALL_CLUSTER_COUNT", "3"))        # "más de 2" = 3+
CALL_CLUSTER_WINDOW_S = float(os.environ.get("OCEANKIND_CALL_CLUSTER_WINDOW_S", "240")) # 4 minutos
CALL_COOLDOWN_S       = float(os.environ.get("OCEANKIND_CALL_COOLDOWN_S", "900"))       # 15 min entre llamadas

# Dispositivo de audio
AUDIO_DEVICE     = os.environ.get("OCEANKIND_AUDIO_DEVICE", "plughw:3,0")
SAMPLE_RATE      = 48000
CHANNELS         = 2
CAPTURE_SECONDS  = 5.0   # 5s para matchear el set de entrenamiento del modelo sklearn

# Detector — PSD tonal (ver bloque PSD_* más abajo). El RMS se sigue reportando
# para monitoreo. Nombres ML_* retirados (F-24): describían un clasificador que
# ya no corre. Los env legacy se aceptan como fallback con warning.
#   DETECTION_MODE: "psd" (solo detector PSD, gate doble RMS+score)
#                   "rms" (solo umbral RMS — fallback real, F-01)
#                   "auto" (PSD si clasifica, si no cae a RMS)
# "ml" se acepta como alias legacy de "psd".
# NOTA D-014: este selector se reemplaza por el registro ordenado de detectores
# (OCEANKIND_DETECTORS) en la Fase 3. No agregarle modos.
ALERT_THRESHOLD       = float(os.environ.get("OCEANKIND_ALERT_THRESHOLD", "0.08"))  # umbral RMS del modo "rms"
DETECTION_MODE        = os.environ.get("OCEANKIND_DETECTION_MODE", "psd").lower()
if DETECTION_MODE == "ml":
    DETECTION_MODE = "psd"


def _env_float_compat(name: str, legacy_name: str, default: str) -> float:
    """Lee un float de env con fallback al nombre legacy (con warning)."""
    if os.environ.get(name, "").strip():
        return float(os.environ[name])
    if os.environ.get(legacy_name, "").strip():
        print(f"AVISO: {legacy_name} es nombre legacy — renombrar a {name} en /etc/oceankind.env",
              file=sys.stderr)
        return float(os.environ[legacy_name])
    return float(default)


# Score mínimo del detector (fracción de segundos tonales, ex ML_THRESHOLD)
SCORE_MIN             = _env_float_compat("OCEANKIND_SCORE_MIN", "OCEANKIND_ML_THRESHOLD", "0.60")
ALERT_MIN_RMS         = float(os.environ.get("OCEANKIND_ALERT_MIN_RMS", "0.010"))  # RMS mínimo para alertar (filtra falsos positivos de ruido)

# ─── Salud del audio (detectar hidrófono desconectado / cable cortado) ───────
# Si el cable al A5 se corta, el RMS cae al piso de ruido del codec (mucho más
# bajo que cualquier ambiente acuático real). Mismo síntoma si el tmpfs se llena
# y arecord escribe WAVs vacíos (RMS=0). Se evalúa sobre una ventana móvil: si
# el pico de RMS en los últimos AUDIO_HEALTH_WINDOW_S segundos nunca supera
# AUDIO_FLOOR_RMS, el hidrófono está probablemente desconectado.
# OJO: el piso depende de la ganancia ALSA. Con Mic1=3/PGA=5 el A5 conectado da
# ~0.001-0.002 y el codec pelado ~0.0001; 0.0005 los separa bien. Recalibrar si
# se cambia la ganancia.
AUDIO_FLOOR_RMS       = float(os.environ.get("OCEANKIND_AUDIO_FLOOR_RMS", "0.0005"))
AUDIO_HEALTH_WINDOW_S = float(os.environ.get("OCEANKIND_AUDIO_HEALTH_WINDOW_S", "900"))  # 15 min

_rms_history: list = []  # [(monotonic_ts, rms), ...] ventana móvil


def record_rms(rms: float) -> None:
    """Registra el RMS del ciclo para evaluar salud del audio."""
    now = time.time()
    _rms_history.append((now, rms))
    cutoff = now - AUDIO_HEALTH_WINDOW_S
    while _rms_history and _rms_history[0][0] < cutoff:
        _rms_history.pop(0)


def audio_health() -> tuple[bool, float, int]:
    """(ok, peak_rms, n_muestras) según si la señal superó el piso en la ventana.

    ok=True si aún no hay datos suficientes (no alarmar al arrancar) o si el pico
    de RMS supera AUDIO_FLOOR_RMS. ok=False → probable hidrófono desconectado.
    """
    if len(_rms_history) < 3:
        return True, 0.0, len(_rms_history)
    peak = max(r for _, r in _rms_history)
    return (peak >= AUDIO_FLOOR_RMS), peak, len(_rms_history)
# Etiqueta del evento positivo en WhatsApp (ex ML_POSITIVE_LABEL, cuyo default
# "FILTRO" era un artefacto de pruebas con filtro de piscina que llegaba a
# alertas reales — F-24). El detector PSD encuentra firmas tonales de maquinaria.
DETECTION_LABEL = (os.environ.get("OCEANKIND_DETECTION_LABEL", "").strip()
                   or os.environ.get("OCEANKIND_ML_POSITIVE_LABEL", "").strip()
                   or "MOTOR")

ALERT_COOLDOWN        = 60.0    # 1 min entre alertas (WhatsApp Business sin cuota; ~$0.01/msg × 2 destinatarios)
HEARTBEAT_INTERVAL    = 60.0

# Cada cuántos segundos se chequea remote_config.json
CONFIG_CHECK_INTERVAL = 300.0   # 5 minutos

# Heartbeat WhatsApp — ping de vida con estadísticas del sistema
WHATSAPP_HEARTBEAT_INTERVAL = 43200.0  # 12 horas

# Carpeta local para clips temporales y alertas pendientes
CLIPS_DIR              = Path.home() / "oceankind" / "clips"
PENDING_ALERTS_FILE    = Path.home() / "oceankind" / "pending_alerts.json"

# ─── Archivo de muestras para análisis posterior (jul 2026) ─────────────────
# La detección corre CONTINUA (clips de 5s uno tras otro, como siempre).
# Además, 1 clip por minuto (ARCHIVE_INTERVAL) se guarda en ARCHIVE_DIR, de
# donde oceankind-drive-upload.timer lo sube a Google Drive cada 10 min.
# Los demás clips se descartan tras clasificar (como antes).
ARCHIVE_INTERVAL  = float(os.environ.get("OCEANKIND_ARCHIVE_INTERVAL", "60"))
ARCHIVE_DIR       = Path(os.environ.get("OCEANKIND_ARCHIVE_DIR", str(Path.home() / "oceankind" / "archive_queue")))
# Tope de la cola local si no hay internet para subir. OJO: ARCHIVE_DIR vive en
# el overlay (RAM) — el default anterior de 3000 (~2.9 GB) superaba los 2 GB de
# la placa (F-22). 300 ≈ 290 MB. Los descartes se cuentan en health.clips_dropped.
ARCHIVE_MAX_FILES = int(os.environ.get("OCEANKIND_ARCHIVE_MAX_FILES", "300"))

# --- Logging -----------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("oceankind")

# ─── Versionado del contrato de datos ────────────────────────────────────────
# Este dispositivo emite el contrato v2 (docs/DATA-CONTRACT.md): árbol
# sites/{site}/…, un blob por evento, sin manifest. Decidido 2026-08-13 (D-016):
# los prototipos quedan congelados en v1 en su contenedor viejo; las unidades
# nuevas escriben v2 a un contenedor nuevo. Sin migración.
SCHEMA_VERSION = 2
SITE_ID_RE = re.compile(r"^[a-z0-9_-]+$")


def validate_startup_config() -> None:
    """Valida configuración al arrancar. Secretos ausentes = NO ARRANCA (R-8.1).

    La única excepción es el banco de pruebas: OCEANKIND_ALLOW_NO_TWILIO=1
    permite correr sin credenciales, y esa condición queda publicada en
    health.degraded_reason — nunca silenciosa.
    """
    problems = []
    if not TWILIO_CONFIGURED and not ALLOW_NO_TWILIO:
        problems.append(
            "OCEANKIND_TWILIO_SID / OCEANKIND_TWILIO_TOKEN faltan en el entorno. "
            "Definir en /etc/oceankind.env, o OCEANKIND_ALLOW_NO_TWILIO=1 SOLO en banco de pruebas.")
    if DETECTION_MODE not in ("psd", "rms", "auto"):
        problems.append(f"OCEANKIND_DETECTION_MODE={DETECTION_MODE!r} inválido (psd|rms|auto)")
    if STORAGE_ENABLED:
        # Contrato v2: nada en la raíz del contenedor. Sin sitio no hay path.
        if not SITE:
            problems.append("OCEANKIND_SITE falta. En el contrato v2 todo vive bajo "
                            "sites/{site}/ — definir un id (p.ej. 'punta_norte').")
        elif not SITE_ID_RE.match(SITE):
            problems.append(f"OCEANKIND_SITE={SITE!r} inválido: solo [a-z0-9_-]")
        # _sites.json exige lat/lon numéricos: son de aprovisionamiento, no de código (F-08)
        if SENSOR_LAT is None or SENSOR_LON is None:
            problems.append("OCEANKIND_SENSOR_LAT / OCEANKIND_SENSOR_LON faltan — "
                            "el registro de sitios (_sites.json) los requiere.")
    if problems:
        for p in problems:
            log.critical("CONFIG INVÁLIDA: %s", p)
        log.critical("El servicio se niega a arrancar con configuración inválida (R-8.1).")
        sys.exit(1)
    if ALLOW_NO_TWILIO and not TWILIO_CONFIGURED:
        log.warning("⚠️  Sin credenciales Twilio (OCEANKIND_ALLOW_NO_TWILIO=1) — "
                    "NINGUNA alerta WhatsApp puede salir. Solo aceptable en banco de pruebas.")


# ─── Salud y honestidad (R-2.x): contadores publicados en status.json ────────
# El propósito del sistema es notar algo; un componente que se degrada sin
# decirlo lo derrota. Nada de esto decide alertas — solo dice la verdad.
_clips_dropped_total   = 0      # clips descartados por tope de cola de archivo
_suppressed_total      = 0      # detecciones registradas con notificación suprimida
_classify_fail_consec  = 0      # fallos consecutivos de classify_clip
_detector_alert_sent   = False  # dedup del aviso de detector degradado
_audio_alert_sent      = False  # dedup del aviso de hidrófono sin señal
DETECTOR_FAIL_LIMIT    = int(os.environ.get("OCEANKIND_DETECTOR_FAIL_LIMIT", "3"))


def detector_ok() -> bool:
    """False cuando el clasificador lleva DETECTOR_FAIL_LIMIT fallos seguidos (F-02)."""
    if DETECTION_MODE == "rms":
        return True     # el modo rms no usa el clasificador
    return _classify_fail_consec < DETECTOR_FAIL_LIMIT


# ─── Duty cycle medido, no afirmado (R-1.2, F-05) ────────────────────────────
# Dos auditorías entregadas discreparon sobre la tasa de pérdida y ninguna la
# midió. Ventana móvil de ciclos: (fin_ts, segundos_escuchados, segundos_ciclo).
DUTY_WINDOW_S       = float(os.environ.get("OCEANKIND_DUTY_WINDOW_S", "3600"))
_duty_history: list = []
_deaf_seconds_total = 0.0


def record_cycle(listened_s: float, cycle_s: float) -> None:
    global _deaf_seconds_total
    _deaf_seconds_total += max(0.0, cycle_s - listened_s)
    now = time.monotonic()
    _duty_history.append((now, listened_s, cycle_s))
    cutoff = now - DUTY_WINDOW_S
    while _duty_history and _duty_history[0][0] < cutoff:
        _duty_history.pop(0)


def duty_cycle_pct() -> float | None:
    """% del tiempo con el hidrófono realmente grabando, sobre la ventana móvil."""
    total = sum(c for _, _, c in _duty_history)
    if total <= 0:
        return None
    return round(sum(l for _, l, _ in _duty_history) / total * 100, 1)


def get_system_stats() -> dict:
    """Recopila estadísticas del sistema usando solo stdlib (sin psutil)."""
    stats = {}
    # Temperatura CPU (Raspberry Pi): /sys/class/thermal/thermal_zone0/temp expone el sensor
    # del SoC en milicelsius. El kernel lo lee del SoC vía registros internos del BCM2711.
    try:
        temp_raw = Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()
        stats["cpu_temp_c"] = round(int(temp_raw) / 1000, 1)
    except Exception:
        stats["cpu_temp_c"] = None
    # Uptime del sistema (desde el último boot, NO desde que arrancó el script)
    try:
        stats["system_uptime_s"] = int(float(Path("/proc/uptime").read_text().split()[0]))
    except Exception:
        stats["system_uptime_s"] = None
    # Uso de disco — preferimos la SD real (/media/root-ro) si overlay activo, si no /
    try:
        import shutil
        disk_path = "/media/root-ro" if Path("/media/root-ro").is_mount() else "/"
        du = shutil.disk_usage(disk_path)
        stats["disk_path"]     = disk_path
        stats["disk_used_pct"] = round(du.used / du.total * 100, 1)
        stats["disk_free_gb"]  = round(du.free / (1024 ** 3), 2)
        stats["disk_total_gb"] = round(du.total / (1024 ** 3), 1)
    except Exception:
        stats["disk_used_pct"] = None
        stats["disk_free_gb"]  = None
        stats["disk_total_gb"] = None
    # RAM
    try:
        meminfo = Path("/proc/meminfo").read_text()
        lines = {}
        for line in meminfo.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                lines[k.strip()] = int(v.split()[0])
        total = lines.get("MemTotal", 1)
        avail = lines.get("MemAvailable", 0)
        stats["ram_used_pct"] = round((total - avail) / total * 100, 1)
        # Además del %, reportar MB absolutos: un "14%" no se puede interpretar
        # sin saber el total de la placa (2/4/8 GB).
        stats["ram_used_mb"]  = round((total - avail) / 1024)
        stats["ram_total_mb"] = round(total / 1024)
    except Exception:
        stats["ram_used_pct"] = None
        stats["ram_used_mb"]  = None
        stats["ram_total_mb"] = None
    return stats


def level_bar(rms: float, threshold: float, width: int = 40) -> str:
    filled = int(min(rms / 0.3, 1.0) * width)
    bar = list("█" * filled + "░" * (width - filled))
    threshold_pos = int(min(threshold / 0.3, 1.0) * width)
    if threshold_pos < width:
        bar[threshold_pos] = "|"
    return "".join(bar)


# --- Captura de audio --------------------------------------------------------

def capture_audio(output_path: str) -> tuple[float, float, float]:
    """Graba un clip. Devuelve (rms_norm, peak_db, segundos_escuchados).

    segundos_escuchados alimenta el duty cycle medido (R-1.2): CAPTURE_SECONDS
    si la grabación fue exitosa, 0.0 si falló (ese ciclo fue 100% sordo).
    """
    cmd = [
        "arecord", "-D", AUDIO_DEVICE, "-f", "S16_LE",
        "-r", str(SAMPLE_RATE), "-c", str(CHANNELS),
        "-d", str(int(CAPTURE_SECONDS)), output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=CAPTURE_SECONDS + 5)
        if result.returncode != 0:
            log.warning("arecord error: %s", result.stderr.decode().strip())
            time.sleep(0.5)
            return 0.0, -180.0, 0.0
        with wave.open(output_path, "rb") as wf:
            raw = wf.readframes(wf.getnframes())

        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        if samples.size == 0:
            return 0.0, -180.0, 0.0   # piso finito (no -inf, que rompe JSON)

        rms      = float(np.sqrt(np.mean(samples ** 2)))
        rms_norm = rms / 32768.0
        peak_db  = float(20 * np.log10(rms_norm + 1e-9))
        # Clamp para evitar -inf en JSON (Python json.dumps los serializa como
        # "Infinity" literal, no permitido por la spec → JS no puede parsear)
        if not np.isfinite(peak_db):
            peak_db = -180.0
        return rms_norm, peak_db, CAPTURE_SECONDS

    except subprocess.TimeoutExpired:
        log.warning("arecord timeout")
        time.sleep(1.0)
        return 0.0, -180.0, 0.0
    except Exception as exc:
        log.warning("Error capturando audio: %s", exc)
        return 0.0, -180.0, 0.0


# --- Almacenamiento (contrato v2) --------------------------------------------
# Paths EXPLÍCITOS y completos, relativos al contenedor: "sites/{site}/…" o
# "_sites.json". Sin prefijos implícitos (el viejo namespacing por SITE se
# retiró con el manifest). Dos backends con los mismos paths:
#   - Azure Blob Storage (STORAGE_CONNECTION_STRING)
#   - Directorio local (OUTPUT_DIR) para banco y validación: el árbol resultante
#     debe pasar tools/validate_contract.py. Ese es el test del trabajo contratado.

def site_path(name: str) -> str:
    return f"sites/{SITE}/{name}"


def _get_blob_client(rel_path: str):
    from azure.storage.blob import BlobClient  # noqa: PLC0415
    return BlobClient.from_connection_string(
        STORAGE_CONNECTION_STRING,
        container_name=STORAGE_CONTAINER,
        blob_name=rel_path,
    )


def upload_clip(local_path: str, rel_path: str) -> bool:
    """Sube (o copia, en modo local) un WAV al path v2. True si quedó almacenado."""
    try:
        if OUTPUT_DIR:
            dest = Path(OUTPUT_DIR) / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            import shutil  # noqa: PLC0415
            shutil.copyfile(local_path, dest)
            return True
        from azure.storage.blob import ContentSettings  # noqa: PLC0415
        blob = _get_blob_client(rel_path)
        with open(local_path, "rb") as f:
            blob.upload_blob(f, overwrite=True,
                             content_settings=ContentSettings(content_type="audio/wav"))
        return True
    except Exception as exc:
        log.warning("Error subiendo clip %s: %s", rel_path, exc)
        return False


def _sanitize_for_json(obj):
    """Reemplaza floats no finitos (inf/nan) por None recursivamente.

    Python json.dumps por default serializa `float('inf')` como `Infinity` literal,
    que NO es JSON válido y rompe JSON.parse() en JavaScript (dashboard se queda
    en blanco). Aquí los convertimos a null antes de serializar.
    """
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    if isinstance(obj, float):
        import math  # noqa: PLC0415
        if not math.isfinite(obj):
            return None
    return obj


def upload_json(rel_path: str, data: dict) -> bool:
    """Escribe un blob JSON en el path v2. True si quedó almacenado."""
    payload = json.dumps(_sanitize_for_json(data), indent=2, allow_nan=False).encode()
    try:
        if OUTPUT_DIR:
            dest = Path(OUTPUT_DIR) / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(payload)
            return True
        from azure.storage.blob import ContentSettings  # noqa: PLC0415
        blob = _get_blob_client(rel_path)
        blob.upload_blob(payload, overwrite=True,
                         content_settings=ContentSettings(content_type="application/json"))
        return True
    except Exception as exc:
        log.warning("Error subiendo %s: %s", rel_path, exc)
        return False


def download_json(rel_path: str) -> dict | None:
    try:
        if OUTPUT_DIR:
            p = Path(OUTPUT_DIR) / rel_path
            return json.loads(p.read_text()) if p.exists() else None
        blob = _get_blob_client(rel_path)
        return json.loads(blob.download_blob().readall())
    except Exception:
        return None


def blob_exists(rel_path: str) -> bool | None:
    """True/False si se pudo determinar; None si no (p.ej. red caída).

    La distinción importa: los stubs de arranque solo se escriben cuando el blob
    con certeza NO existe, para no pisar datos de un productor externo por un
    error transitorio de red.
    """
    try:
        if OUTPUT_DIR:
            return (Path(OUTPUT_DIR) / rel_path).exists()
        return bool(_get_blob_client(rel_path).exists())
    except Exception:
        return None


# ─── Eventos: un blob por detección, append-only (R-4.1, F-14) ───────────────
# El manifest.json (descargar-modificar-resubir) está RETIRADO en v2. Cada
# detección es un blob inmutable bajo sites/{site}/events/YYYY/MM/DD/. Eso
# elimina la carrera del manifest por construcción y hace que registrar un
# evento suprimido cueste un PUT chico, no un ciclo completo de manifest.
#
# Si la subida falla, el evento se ENCOLA localmente (spool acotado) y se
# reintenta en cada heartbeat: ningún evento que produce un detector puede
# perderse por nuestra plomería (D-015), hasta el tope del spool — y los
# descartes del spool se publican (R-1.3).
EVENT_SPOOL_DIR = Path(os.environ.get("OCEANKIND_EVENT_SPOOL_DIR",
                                      str(Path.home() / "oceankind" / "event_spool")))
EVENT_SPOOL_MAX = int(os.environ.get("OCEANKIND_EVENT_SPOOL_MAX", "500"))
_events_dropped_total = 0


def event_rel_paths(captured_dt: datetime, event_id: str) -> tuple[str, str]:
    """(path del blob de evento, path del clip) según el esquema v2."""
    day = captured_dt.strftime("%Y/%m/%d")
    stamp = captured_dt.strftime("%Y-%m-%dT%H-%M-%S")
    return (site_path(f"events/{day}/{stamp}_{event_id}.json"),
            site_path(f"clips/{day}/{event_id}.wav"))


def build_event(event_id: str, captured_iso: str, event_type: str, detector: str,
                score: float, suppressed: bool, rms: float, peak_db: float,
                clip_rel: str, clip_uploaded: bool, detector_meta: dict | None = None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "site":           SITE,
        "device":         DEVICE_ID,
        "generated_utc":  datetime.now(timezone.utc).isoformat(),
        "event_id":       event_id,
        "captured_utc":   captured_iso,
        "uploaded_utc":   datetime.now(timezone.utc).isoformat(),
        "event_type":     event_type,
        "detector":       detector,
        "score":          round(min(1.0, max(0.0, score)), 4),
        "suppressed":     suppressed,
        "audio_level":    round(rms, 4),
        "peak_db":        round(peak_db, 1),
        "bearing_deg":    None,        # sin productor hoy; el campo existe por contrato
        "clip": {
            "path":        clip_rel,
            "sample_rate": SAMPLE_RATE,
            "channels":    CHANNELS,
            "duration_s":  CAPTURE_SECONDS,
            "uploaded":    clip_uploaded,
        },
        "detector_meta":  detector_meta or {},
    }


def write_event(event: dict, rel_path: str) -> None:
    """Sube el blob del evento; si falla, lo encola para reintento. Nunca lo pierde en silencio."""
    if upload_json(rel_path, event):
        log.info("  → evento registrado: %s%s", rel_path, "  [suprimido]" if event["suppressed"] else "")
        return
    _spool_event(event, rel_path)


def _spool_event(event: dict, rel_path: str) -> None:
    global _events_dropped_total
    try:
        EVENT_SPOOL_DIR.mkdir(parents=True, exist_ok=True)
        (EVENT_SPOOL_DIR / f"{event['event_id']}.json").write_text(
            json.dumps({"rel_path": rel_path, "event": _sanitize_for_json(event)}, allow_nan=False))
        queue = sorted(EVENT_SPOOL_DIR.glob("*.json"))
        if len(queue) > EVENT_SPOOL_MAX:
            dropped = len(queue) - EVENT_SPOOL_MAX
            for old in queue[:dropped]:
                old.unlink(missing_ok=True)
            _events_dropped_total += dropped
            log.error("spool de eventos lleno — %d evento/s más antiguo/s DESCARTADO/S (total: %d)",
                      dropped, _events_dropped_total)
        log.warning("  evento encolado localmente (%d pendientes)", min(len(queue), EVENT_SPOOL_MAX))
    except Exception as exc:
        _events_dropped_total += 1
        log.error("evento PERDIDO: no se pudo subir ni encolar (%s)", exc)


def drain_event_spool() -> None:
    """Reintenta subir eventos encolados. Se llama en cada heartbeat."""
    if not EVENT_SPOOL_DIR.is_dir():
        return
    for f in sorted(EVENT_SPOOL_DIR.glob("*.json")):
        try:
            item = json.loads(f.read_text())
        except Exception:
            f.unlink(missing_ok=True)
            continue
        if upload_json(item["rel_path"], item["event"]):
            f.unlink(missing_ok=True)
            log.info("  → evento pendiente subido: %s", item["rel_path"])
        else:
            break   # la red sigue caída; no insistir esta vuelta


def event_spool_len() -> int:
    try:
        return sum(1 for _ in EVENT_SPOOL_DIR.glob("*.json")) if EVENT_SPOOL_DIR.is_dir() else 0
    except Exception:
        return 0


# ─── Registro de sitios y blobs auxiliares ───────────────────────────────────

def publish_site_registry() -> None:
    """Inserta/actualiza la entrada de ESTE sitio en _sites.json (R-5.2).

    Las coordenadas viven aquí, no en status.json: eso las saca del path de
    lectura del dispositivo y cierra F-08 de verdad. Escritura rara (solo al
    arrancar), así que el read-modify-write es tolerable hasta que exista backend.
    """
    reg = download_json("_sites.json") or {}
    sites = [s for s in reg.get("sites", []) if isinstance(s, dict)]
    entry = {"id": SITE, "name": SENSOR_LOCATION_NAME, "lat": SENSOR_LAT,
             "lon": SENSOR_LON, "device": DEVICE_ID, "active": True}
    sites = [s for s in sites if s.get("id") != SITE] + [entry]
    ok = upload_json("_sites.json", {
        "schema_version": SCHEMA_VERSION,
        "generated_utc":  datetime.now(timezone.utc).isoformat(),
        "sites":          sorted(sites, key=lambda s: s.get("id", "")),
    })
    log.info("Registro de sitios %s (_sites.json, %d sitio/s)",
             "publicado" if ok else "NO publicado", len(sites))


def ensure_aux_blobs() -> None:
    """Escribe stubs conformes para los blobs que produce OTRO componente.

    acoustic_indicators.json y ocean_conditions.json son obligatorios por
    contrato pero sus productores reales no viven en este repo (dependencias
    de cliente 8 y 9). Un stub vacío-pero-conforme evita que el árbol sea
    inválido y que el dashboard rompa; el productor real lo sobreescribe.
    SOLO se escribe si el blob con certeza no existe (blob_exists() is False)
    para jamás pisar datos reales por un error transitorio.
    """
    envelope = {"schema_version": SCHEMA_VERSION, "site": SITE, "device": DEVICE_ID,
                "generated_utc": datetime.now(timezone.utc).isoformat()}
    stubs = {
        "acoustic_indicators.json": {**envelope, "latest": {}, "timeline": [], "diel": []},
        "ocean_conditions.json":    {**envelope, "location": {"name": SENSOR_LOCATION_NAME,
                                                              "lat": SENSOR_LAT, "lon": SENSOR_LON},
                                     "current": {}, "hourly": [], "daily": [], "thresholds": {}},
    }
    for name, stub in stubs.items():
        rel = site_path(name)
        if blob_exists(rel) is False:
            upload_json(rel, stub)
            log.info("Stub conforme escrito: %s (lo sobreescribe su productor real)", rel)


# ─── Detector PSD de peaks tonales (Emily Barosin / Integral Consulting) ─────
# Reemplaza al clasificador sklearn. Detecta firmas tonales persistentes
# (motores de embarcaciones, bombas) buscando peaks espectrales que sobresalen
# del background local en 55-1000 Hz — algoritmo de detector_psd.py.
#
# Score continuo (proba) = fracción de segundos del clip con ≥2 peaks tonales
# válidos → compatible con el slider del dashboard y el gate SCORE_MIN.
# Con clips de 5s: proba ∈ {0, 0.2, 0.4, 0.6, 0.8, 1.0}.
#   SCORE_MIN=0.90 → exige 5/5 segundos tonales
#   SCORE_MIN=0.80 → exige 4/5 (recomendado para empezar)
#
# OJO (F-21): este algoritmo necesita tonos SOSTENIDOS. Un evento impulsivo de
# menos de un segundo (explosión) puntúa a lo sumo 0.2 y NO puede disparar con
# SCORE_MIN>=0.4. Detecta maquinaria de embarcaciones, no explosiones. El
# registro de detectores de la Fase 3 (D-014) agrega el detector de impulsos
# como segundo miembro; no "arreglar" esto cambiando umbrales.

# threshold_db=4 (no el 0.5 original): la data FFT del SoundTrap de Emily venía
# promediada y suave; nuestro Welch de 1s tiene ±3 dB de rizado y con 0.5 dB
# hasta el ruido blanco marcaba tonal (validado con clips sintéticos + reales jul 2026).
# Umbrales calibrados con datos de campo de Zapallar (7-ago-2026).
# Ver Detector System/CALIBRACION_DETECTOR_2026-08-07.md antes de cambiarlos:
# con los valores previos se detectaban 2 de 6 ventanas con bote real.
PSD_THRESHOLD_DB  = float(os.environ.get("OCEANKIND_PSD_THRESHOLD_DB", "8"))
PSD_F_MIN         = float(os.environ.get("OCEANKIND_PSD_F_MIN", "55"))
PSD_F_MAX         = float(os.environ.get("OCEANKIND_PSD_F_MAX", "1000"))
PSD_DECIMATION    = int(os.environ.get("OCEANKIND_PSD_DECIMATION", "4"))
# nfft=4096 a fs decimado 12kHz → ~2.9 Hz/bin. El nfft=512 original daba 23 Hz/bin,
# demasiado grueso: la ventana de búsqueda de ±15 Hz quedaba en 0 bins y todo
# local-max pasaba como peak válido.
PSD_NFFT          = int(os.environ.get("OCEANKIND_PSD_NFFT", "4096"))
# Banda de guarda (bins) alrededor del peak excluida del background local.
# Sin esto, el hombro espectral del propio tono entra a la ventana de background
# y comprime la prominencia medida (un tono fuerte medía ~2 dB de prominencia).
PSD_GUARD_BINS    = int(os.environ.get("OCEANKIND_PSD_GUARD_BINS", "2"))
PSD_SEARCH_HZ     = float(os.environ.get("OCEANKIND_PSD_SEARCH_HZ", "15"))


def _record_classify_result(ok: bool, reason: str = "") -> None:
    """Contabiliza éxito/fallo del clasificador y ALERTA al degradarse (F-02).

    El patrón que produjo F-02 era devolver {} y seguir: el detector muere y los
    heartbeats siguen reportando la unidad sana. Ahora DETECTOR_FAIL_LIMIT fallos
    consecutivos ponen health.detector_ok=false y mandan UN aviso WhatsApp.
    """
    global _classify_fail_consec, _detector_alert_sent
    if ok:
        if _classify_fail_consec >= DETECTOR_FAIL_LIMIT:
            log.warning("Detector recuperado tras %d fallos", _classify_fail_consec)
        _classify_fail_consec = 0
        _detector_alert_sent = False
        return
    _classify_fail_consec += 1
    log.warning("classify_clip falló (%d consecutivos): %s", _classify_fail_consec, reason)
    if _classify_fail_consec >= DETECTOR_FAIL_LIMIT and not _detector_alert_sent:
        log.error("🔴 DETECTOR DEGRADADO: %d fallos consecutivos — la unidad NO está detectando", _classify_fail_consec)
        send_degraded_alert(f"detector caído ({_classify_fail_consec} fallos): {reason[:80]}")
        _detector_alert_sent = True


def classify_clip(wav_path: str) -> dict:
    """
    Clasifica un .wav con el detector PSD de peaks tonales.
    Devuelve dict con:
       pred  : int   — 1 si ≥ mitad de los segundos del clip son tonales
       proba : float — fracción de segundos con ≥2 peaks tonales [0..1]
       label : str   — DETECTION_LABEL o "background"
    Si falla, devuelve {} y lo contabiliza (nunca en silencio — F-02).
    """
    try:
        import scipy.io.wavfile as wavfile      # noqa: PLC0415
        from scipy import signal as sp_signal   # noqa: PLC0415
        import warnings as _w                   # noqa: PLC0415

        _w.filterwarnings("ignore", category=wavfile.WavFileWarning)
        fs, data = wavfile.read(wav_path)
        if data.ndim == 2:                      # estéreo dual-mono → mono
            data = data.mean(axis=1)
        data = data.astype(np.float32)
        if PSD_DECIMATION > 1:
            data = sp_signal.decimate(data, PSD_DECIMATION)
            fs //= PSD_DECIMATION

        # Algoritmo de detect_tonal_peaks() (detector_psd.py) por chunks de 1s,
        # con dos adaptaciones para Welch crudo: nfft alto (resolución) y banda
        # de guarda alrededor del peak al medir el background local (sin ella,
        # el hombro del propio tono comprime la prominencia).
        chunk = fs
        n_chunks = len(data) // chunk
        if n_chunks == 0:
            _record_classify_result(False, "clip vacío o más corto que 1s")
            return {}
        n_tonal = 0
        for i in range(n_chunks):
            seg = data[i * chunk:(i + 1) * chunk]
            freqs, psd = sp_signal.welch(seg, fs=fs, nperseg=min(PSD_NFFT, len(seg)), nfft=PSD_NFFT)
            psd_db = 10 * np.log10(psd + 1e-10)
            mask = (freqs >= PSD_F_MIN) & (freqs <= PSD_F_MAX)
            pf, ff = psd_db[mask], freqs[mask]
            if len(ff) < 3:
                continue
            df = float(np.mean(np.diff(ff)))
            search = max(1, int(PSD_SEARCH_HZ / df))
            peaks, _props = sp_signal.find_peaks(pf, distance=max(1, int(2 / df)))
            valid = 0
            for idx in peaks:
                lo = max(0, idx - search)
                hi = min(len(pf), idx + search + 1)
                bg = np.concatenate([
                    pf[lo:max(lo, idx - PSD_GUARD_BINS)],
                    pf[min(hi, idx + PSD_GUARD_BINS + 1):hi],
                ])
                if len(bg) == 0:
                    continue
                if pf[idx] - bg.max() >= PSD_THRESHOLD_DB:
                    valid += 1
                    if valid >= 2:
                        break
            if valid >= 2:
                n_tonal += 1

        proba = n_tonal / n_chunks
        pred = 1 if proba >= 0.5 else 0
        _record_classify_result(True)
        return {
            "pred":  pred,
            "proba": round(proba, 4),
            "label": DETECTION_LABEL if pred == 1 else "background",
        }
    except Exception as exc:
        _record_classify_result(False, str(exc))
        return {}


# Mapeo del campo CS (Charge State) de Victron VE.Direct.
# Spec: https://www.victronenergy.com/upload/documents/VE.Direct-Protocol-3.33.pdf
VICTRON_CHARGE_STATES = {
    0:   "Off",
    2:   "Fault",
    3:   "Bulk",
    4:   "Absorption",
    5:   "Float",
    7:   "Equalize",
    245: "Starting up",
    247: "Auto equalize",
    252: "External control",
}

# Path persistente del cable VE.Direct. Si configuras una udev rule:
#   SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6015", SYMLINK+="vedirect"
# entonces poné OCEANKIND_VEDIRECT_PORT=/dev/vedirect en /etc/oceankind.env.
VEDIRECT_PORT = os.environ.get("OCEANKIND_VEDIRECT_PORT", "")


def fetch_ve_direct() -> dict:
    """
    Lee un frame del Victron BlueSolar MPPT por VE.Direct (USB serial, 19200 8N1).
    Devuelve dict con keys (todas opcionales — sólo presentes si el cable responde):
      battery_voltage_v   : float — voltaje de la batería (V)
      battery_current_a   : float — corriente de carga (A, + cargando / - descargando)
      panel_voltage_v     : float — voltaje del panel solar (V)
      panel_power_w       : int   — potencia del panel (W)
      charge_state        : str   — "Off"|"Bulk"|"Absorption"|"Float"|...
      error_code          : int   — código de error (0 = sin error)
      yield_today_kwh     : float — energía generada hoy (kWh)
      yield_total_kwh     : float — energía total generada (kWh)
      max_power_today_w   : int   — pico de potencia hoy (W)
      device_label        : str   — modelo del controlador (e.g. "BlueSolar MPPT 75/15")

    Si el cable no está conectado o el dispositivo no responde, devuelve {}.
    """
    try:
        import serial   # pyserial
        import glob     # noqa: PLC0415
    except ImportError:
        return {}

    # Candidatos: el path explícito si está configurado, si no, todos los /dev/ttyUSB*
    candidates = [VEDIRECT_PORT] if VEDIRECT_PORT else sorted(glob.glob("/dev/ttyUSB*"))
    candidates = [p for p in candidates if p and Path(p).exists()]
    if not candidates:
        return {}

    fields: dict = {}
    for port in candidates:
        try:
            with serial.Serial(port, 19200, timeout=1.0) as ser:
                # Acumular hasta encontrar un Checksum (fin de frame).
                # VE.Direct emite un frame completo cada ~1s.
                buf = bytearray()
                deadline = time.time() + 3.0
                while time.time() < deadline:
                    chunk = ser.read(256)
                    if chunk:
                        buf.extend(chunk)
                        if b"Checksum" in buf:
                            break
                if b"Checksum" not in buf:
                    continue  # probablemente no es Victron, probar siguiente puerto

                # Parsear líneas "LABEL\tVALUE"
                tmp = {}
                for line in buf.decode("ascii", errors="ignore").split("\r\n"):
                    if "\t" in line:
                        k, _, v = line.partition("\t")
                        tmp[k.strip()] = v.strip()

                if "V" in tmp or "PPV" in tmp or "PID" in tmp:
                    fields = tmp
                    break
        except Exception as exc:
            log.debug("VE.Direct read failed on %s: %s", port, exc)
            continue

    if not fields:
        return {}

    out: dict = {}
    def _int(k):
        try: return int(fields[k])
        except (KeyError, ValueError): return None

    v_mv  = _int("V");    out["battery_voltage_v"] = round(v_mv / 1000.0, 2) if v_mv is not None else None
    i_ma  = _int("I");    out["battery_current_a"] = round(i_ma / 1000.0, 2) if i_ma is not None else None
    vp_mv = _int("VPV");  out["panel_voltage_v"]   = round(vp_mv / 1000.0, 1) if vp_mv is not None else None
    ppv   = _int("PPV");  out["panel_power_w"]     = ppv
    cs    = _int("CS")
    if cs is not None:
        out["charge_state"]    = VICTRON_CHARGE_STATES.get(cs, f"State {cs}")
        out["charge_state_id"] = cs
    err   = _int("ERR");  out["error_code"]        = err
    h20   = _int("H20");  out["yield_today_kwh"]   = round(h20 / 100.0, 2) if h20 is not None else None
    h19   = _int("H19");  out["yield_total_kwh"]   = round(h19 / 100.0, 1) if h19 is not None else None
    h21   = _int("H21");  out["max_power_today_w"] = h21
    if fields.get("PID"):
        out["device_label"] = fields.get("PID", "")
    # Quitar Nones para no enviar ruido
    return {k: v for k, v in out.items() if v is not None}


def _compute_system_load(solar: dict) -> float | None:
    """
    Estima consumo instantáneo del sistema en Watts a partir de Victron VE.Direct.

    Conservación de energía:
        panel_power = battery_charge_power + system_load + losses
        → system_load ≈ panel_power − (V_bat × I_bat)

    Si I_bat es positivo (cargando): load = panel − V·I
    Si I_bat es negativo (descargando): load = V·|I|  (panel ≈ 0 o insuficiente)

    Devuelve None si faltan datos del Victron.
    """
    v   = solar.get("battery_voltage_v")
    i   = solar.get("battery_current_a")
    ppv = solar.get("panel_power_w")
    if v is None or i is None:
        return None
    battery_power = v * i  # Watts (positivo = cargando, negativo = descargando)
    if ppv is None:
        ppv = 0.0
    load = ppv - battery_power
    if load < 0:
        load = 0.0  # nunca negativo (clamp); puede pasar por ruido de medición
    return round(load, 1)


# ── CSV histórico de energía + consumo ──────────────────────────────────────
# Se persiste en /boot/firmware (FAT32, sobrevive reboots, accesible si sacas
# la SD del Pi). Override con OCEANKIND_DATA_LOG.
DATA_LOG_PATH = Path(os.environ.get(
    "OCEANKIND_DATA_LOG",
    "/boot/firmware/oceankind_data.csv",
))
DATA_LOG_COLUMNS = [
    "timestamp_utc",
    "battery_voltage_v",
    "battery_current_a",
    "panel_voltage_v",
    "panel_power_w",
    "charge_state",
    "yield_today_kwh",
    "yield_total_kwh",
    "system_load_w",
    "cpu_temp_c",
    "ram_used_pct",
    "ram_used_mb",
    "ram_total_mb",
    "disk_used_pct",
    "signal_bars",
    "network_type",
    "alert_count_session",
    "last_rms",
]


def append_data_log(status: dict) -> None:
    """Agrega 1 fila al CSV histórico con los campos clave del status.

    Crea el header si el archivo no existe. Falla silencioso (solo warning) si
    no se puede escribir (e.g. partición read-only).
    """
    try:
        import csv  # noqa: PLC0415
        new_file = not DATA_LOG_PATH.exists()
        # Si el header del CSV existente no coincide con las columnas actuales (p. ej. se
        # agregaron columnas de RAM), rotar el archivo viejo para no desalinear filas.
        # El histórico previo queda archivado con fecha (FAT32, sobrevive reboots).
        if not new_file:
            expected = ",".join(DATA_LOG_COLUMNS)
            try:
                first = DATA_LOG_PATH.open("r").readline().strip()
            except Exception:
                first = ""
            if first != expected:
                rotated = DATA_LOG_PATH.with_name(
                    DATA_LOG_PATH.stem + "_" +
                    datetime.now(timezone.utc).strftime("%Y%m%d") + DATA_LOG_PATH.suffix)
                try:
                    DATA_LOG_PATH.rename(rotated)
                    log.info("CSV rotado por cambio de columnas → %s", rotated.name)
                except Exception:
                    pass
                new_file = True
        DATA_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DATA_LOG_PATH.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=DATA_LOG_COLUMNS, extrasaction="ignore")
            if new_file:
                w.writeheader()
            row = {col: status.get(col) for col in DATA_LOG_COLUMNS}
            row["timestamp_utc"] = status.get("last_seen", "")
            w.writerow(row)
    except Exception as exc:
        log.warning("No se pudo escribir CSV en %s: %s", DATA_LOG_PATH, exc)


POWER_HISTORY_HOURS    = 72        # ventana del gráfico del dashboard
POWER_HISTORY_BUCKET_S = 30 * 60   # buckets de 30 min — 144 puntos en 72h


def upload_power_history() -> None:
    """Lee el CSV histórico, agrega últimas 72h en buckets de 30 min, sube a Azure.

    Resultado en alerts/power_history.json — el dashboard lo lee y dibuja
    consumo (W), panel solar (W) y voltaje batería (V) de los últimos 3 días.

    Falla silencioso (warning) si el CSV no existe o no se puede subir.
    """
    if not DATA_LOG_PATH.exists():
        return
    try:
        import csv  # noqa: PLC0415
        cutoff = datetime.now(timezone.utc) - timedelta(hours=POWER_HISTORY_HOURS)
        buckets: dict[int, dict[str, list[float]]] = {}
        with DATA_LOG_PATH.open() as f:
            for row in csv.DictReader(f):
                try:
                    ts = datetime.fromisoformat(row["timestamp_utc"])
                    if ts < cutoff:
                        continue
                    key = int(ts.timestamp() // POWER_HISTORY_BUCKET_S)
                    b = buckets.setdefault(key, {"sys": [], "panel": [], "bat": []})
                    if row.get("system_load_w"):    b["sys"].append(float(row["system_load_w"]))
                    if row.get("panel_power_w"):   b["panel"].append(float(row["panel_power_w"]))
                    if row.get("battery_voltage_v"): b["bat"].append(float(row["battery_voltage_v"]))
                except (ValueError, KeyError):
                    continue

        history = []
        for key in sorted(buckets):
            b = buckets[key]
            ts_iso = datetime.fromtimestamp(key * POWER_HISTORY_BUCKET_S, tz=timezone.utc).isoformat()
            history.append({
                "ts":      ts_iso,
                "sys_w":   round(sum(b["sys"]) / len(b["sys"]), 2)   if b["sys"]   else None,
                "panel_w": round(sum(b["panel"]) / len(b["panel"]), 1) if b["panel"] else None,
                "bat_v":   round(sum(b["bat"]) / len(b["bat"]), 2)   if b["bat"]   else None,
            })
        # Buckets sin muestras se OMITEN (nunca null): los huecos son cómo el
        # dashboard reconstruye uptime entre reboots (R-6.3). No rellenar.
        upload_json(site_path("power_history.json"), {
            "schema_version": SCHEMA_VERSION,
            "site":           SITE,
            "device":         DEVICE_ID,
            "generated_utc":  datetime.now(timezone.utc).isoformat(),
            "bucket_s":       POWER_HISTORY_BUCKET_S,
            "window_h":       POWER_HISTORY_HOURS,
            "history":        history,
        })
    except Exception as exc:
        log.warning("No se pudo subir power_history.json: %s", exc)


# ─── Alerta de batería baja por WhatsApp ────────────────────────────────────
# Sistema 12V. Niveles típicos plomo-ácido:
#   12.6V+ = 100%   |  12.2V = 80%   |  11.8V = 50% (warning)
#   11.5V = 30%     |  11.2V = 10% (critical, riesgo daño)
#   10.8V = UVLO del convertidor, Pi a minutos de apagarse (emergency)
BATTERY_WARNING_V   = float(os.environ.get("OCEANKIND_BATT_WARNING_V",  "11.8"))
BATTERY_CRITICAL_V  = float(os.environ.get("OCEANKIND_BATT_CRITICAL_V", "11.2"))
BATTERY_EMERGENCY_V = float(os.environ.get("OCEANKIND_BATT_EMERGENCY_V", "10.8"))
BATTERY_RECOVERY_V  = float(os.environ.get("OCEANKIND_BATT_RECOVERY_V",  "12.2"))
# Debounce: voltaje tiene que permanecer en el nivel N ciclos consecutivos antes de mandar
# WhatsApp. Cada ciclo = 1 heartbeat = 60s. Default 10 → 10 min de persistencia.
# Sin esto: una baja momentánea (e.g. arranque del filtro consume pico) mandaba "batería crítica"
# y a los 2 min "batería recuperada" creando ruido.
BATTERY_DEBOUNCE_CYCLES = int(os.environ.get("OCEANKIND_BATT_DEBOUNCE_CYCLES", "10"))

# Estado persistido en disco para sobrevivir restarts del script (no del sistema).
# Sin esto, cada restart resetearía el last_level y mandaría alertas duplicadas.
_BATTERY_STATE_FILE = Path("/tmp/oceankind_battery_state.json")

_BATTERY_LEVEL_ORDER = {"warning": 1, "critical": 2, "emergency": 3}


def _load_battery_state() -> dict:
    """Devuelve dict con: alerted (último nivel alertado), pending (nivel evaluándose),
    pending_count (ciclos consecutivos en ese pending). Compatible con formato viejo."""
    try:
        s = json.loads(_BATTERY_STATE_FILE.read_text())
        # Formato viejo era {"level": "..."} — lo migramos
        if "alerted" not in s:
            s = {"alerted": s.get("level"), "pending": None, "pending_count": 0}
        return s
    except Exception:
        return {"alerted": None, "pending": None, "pending_count": 0}


def _save_battery_state(state: dict) -> None:
    try:
        _BATTERY_STATE_FILE.write_text(json.dumps(state))
    except Exception as exc:
        log.debug("no se pudo persistir battery state: %s", exc)


def check_battery_alert(solar: dict) -> None:
    """Si la batería cruza un umbral peligroso de manera SOSTENIDA, manda WhatsApp.

    Debounce: el voltaje debe permanecer en el mismo nivel BATTERY_DEBOUNCE_CYCLES
    ciclos consecutivos antes de mandar — evita ruido de bajadas momentáneas (e.g.
    pico de consumo al arrancar el filtro) seguidas de recuperación inmediata.

    Histéresis:
      - Solo alerta cuando BAJA a un nivel más severo que el último alertado
      - Reset del estado cuando voltaje supera BATTERY_RECOVERY_V (12.2V) por DEBOUNCE ciclos
      - Cuando se recupera de verdad (sostenido), manda mensaje de "batería recuperada"

    No-op si solar está vacío (no hay VE.Direct conectado).
    """
    v = solar.get("battery_voltage_v")
    if v is None:
        return

    state = _load_battery_state()
    alerted = state.get("alerted")  # último nivel alertado (None si nunca o ya recuperada)

    # Determinar el nivel "instantáneo" para este ciclo
    if v >= BATTERY_RECOVERY_V:
        current = "recovered"
    elif v <= BATTERY_EMERGENCY_V:
        current = "emergency"
    elif v <= BATTERY_CRITICAL_V:
        current = "critical"
    elif v <= BATTERY_WARNING_V:
        current = "warning"
    else:
        # Zona intermedia (entre WARNING y RECOVERY): no alerta, pero tampoco "recupera"
        # — reseteamos pending para no acumular cuenta inconsistente
        _save_battery_state({"alerted": alerted, "pending": None, "pending_count": 0})
        return

    # Acumular cuenta en pending
    if state.get("pending") == current:
        pending_count = state.get("pending_count", 0) + 1
    else:
        pending_count = 1
    state = {"alerted": alerted, "pending": current, "pending_count": pending_count}

    # ¿Llegamos al umbral de debounce?
    if pending_count < BATTERY_DEBOUNCE_CYCLES:
        log.info("  batería %s (%.2fV) ciclo %d/%d — esperando confirmación",
                 current, v, pending_count, BATTERY_DEBOUNCE_CYCLES)
        _save_battery_state(state)
        return

    # Recuperación sostenida
    if current == "recovered":
        if alerted is not None:
            _send_battery_recovery(v, solar)
        _save_battery_state({"alerted": None, "pending": None, "pending_count": 0})
        return

    # Bajada sostenida: solo alertar si es nivel más severo que el último alertado
    current_order = _BATTERY_LEVEL_ORDER[current]
    last_order = _BATTERY_LEVEL_ORDER.get(alerted, 0)
    if current_order <= last_order:
        # Ya alertamos este nivel (o uno peor) — no spamear, pero mantener pending
        _save_battery_state(state)
        return

    _send_battery_warning(v, current, solar)
    _save_battery_state({"alerted": current, "pending": current, "pending_count": pending_count})


def _send_battery_warning(voltage: float, level: str, solar: dict) -> None:
    """Manda WhatsApp con info de batería + tiempo restante estimado."""
    if not TWILIO_TO or "XXXXXXXXX" in TWILIO_TO:
        return
    panel_w = solar.get("panel_power_w") or 0
    load_w  = _compute_system_load(solar) or 4.0
    net_draw_w = max(0.1, load_w - panel_w)  # consumo neto desde batería (W)

    # Asume batería 12V × 30Ah = 360Wh nominal. Capacidad útil ~50% = 180Wh.
    # Tiempo restante estimado = (voltaje actual / mínimo) * capacidad útil / draw
    remaining_pct = max(0.0, (voltage - 10.5) / (12.6 - 10.5))  # estimación lineal
    remaining_wh  = remaining_pct * 180
    hours_left    = remaining_wh / net_draw_w
    eta_str = f"{hours_left:.1f} h" if hours_left < 24 else f"{hours_left/24:.1f} días"

    label = {"warning": "BAJA", "critical": "CRITICA", "emergency": "EMERGENCIA"}.get(level, level)
    action = (f"La estación se apagará en minutos si no llega sol (autonomía ~{eta_str})."
              if level == "emergency"
              else f"Revisar paneles y orientación; considerar carga manual (autonomía ~{eta_str}).")
    try:
        # prefix_v2=False: en la plantilla v2 {{1}} es un voltaje; anteponerle la estación
        # quedaría ilegible ("Matanzas · 11.40 V"). En v3 la estación va en su propio campo.
        _wa_send(WA_TPL_BATTERY, _wa_vars(
            f"{voltage:.2f}",   # voltaje
            label,              # nivel (BAJA/CRITICA/EMERGENCIA)
            action,             # acción recomendada
            prefix_v2=False,
        ), log_ok=f"🔋 alerta batería {level} (V={voltage:.2f}) → WhatsApp enviado",
           recipients=TO_TECNICO)
    except Exception as exc:
        log.warning("falló envío alerta batería: %s", exc)


def _send_battery_recovery(voltage: float, solar: dict) -> None:
    """Mensaje cuando batería se recupera (voltaje > RECOVERY)."""
    if not TWILIO_TO or "XXXXXXXXX" in TWILIO_TO:
        return
    # No hay plantilla aprobada para "recuperación", así que no se manda WhatsApp
    # business-initiated. Se registra en log; el próximo heartbeat ya mostrará la batería normal.
    log.info("🔋 batería recuperada (V=%.2f) — sin WhatsApp (no hay plantilla de recuperación)", voltage)


def fetch_modem_signal() -> dict:
    """
    Consulta el web admin del modem ZTE (sin login) y devuelve info de señal 4G.
    Endpoint: /goform/goform_get_cmd_process?multi_data=1&cmd=signalbar,network_type,modem_main_state

    Devuelve un dict con keys (todas opcionales):
      signal_bars  : int  (0-5)  — barras de señal (siempre disponible si el modem responde)
      network_type : str         — "LTE" / "WCDMA" / "GSM" / ...
      modem_state  : str         — e.g. "modem_init_complete"
      signal_rssi  : int  (dBm)  — aproximado, derivado de signal_bars (rough mapping)

    Si el modem no responde, devuelve {}.
    """
    try:
        import urllib.request, urllib.parse  # noqa: PLC0415
        params = {
            "multi_data": "1",
            "cmd":        "signalbar,network_type,modem_main_state,lte_rsrp,lte_rsrq,rssi",
            "isTest":     "false",
        }
        url = f"{MODEM_API_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Referer": "http://192.168.0.1/index.html"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        log.debug("modem signal fetch failed: %s", exc)
        return {}

    out = {}
    bars_str = data.get("signalbar", "")
    if bars_str.isdigit():
        bars = int(bars_str)
        out["signal_bars"] = bars
        # Mapeo aproximado bars → dBm (convención usual LTE):
        #   5 bars → ≥ -65 dBm (excelente) | 4 → -75 | 3 → -85 | 2 → -95 | 1 → -110 | 0 → sin señal
        out["signal_rssi"] = {5: -65, 4: -75, 3: -85, 2: -95, 1: -110, 0: None}.get(bars)
    if data.get("network_type"):
        out["network_type"] = data["network_type"]
    if data.get("modem_main_state"):
        out["modem_state"] = data["modem_main_state"]
    # Si el modem (con login) entrega dBm reales, los preferimos sobre el mapeo de bars
    for key_src, key_dst in (("lte_rsrp", "lte_rsrp"), ("lte_rsrq", "lte_rsrq"), ("rssi", "signal_rssi_real")):
        v = data.get(key_src, "")
        if v and v != "":
            try:
                out[key_dst] = int(v)
            except ValueError:
                pass
    return out


def _pending_alert_count() -> int:
    """Cuántas alertas WhatsApp esperan reintento en el buffer local."""
    try:
        return len(json.loads(PENDING_ALERTS_FILE.read_text())) if PENDING_ALERTS_FILE.exists() else 0
    except Exception:
        return 0


def _archive_queue_len() -> int:
    try:
        return sum(1 for _ in ARCHIVE_DIR.glob("clip_*.wav")) if ARCHIVE_DIR.exists() else 0
    except Exception:
        return 0


def build_health() -> dict:
    """El bloque fail-loud de status.json (R-2.1 a R-2.4).

    Se publica SIEMPRE completo, no solo cuando algo está mal: un contador que
    aparece únicamente en la falla es indistinguible de un contador que no existe.
    """
    audio_ok, peak_rms, _n = audio_health()
    det_ok = detector_ok()
    reasons = []
    if not det_ok:
        reasons.append(f"clasificador caído ({_classify_fail_consec} fallos consecutivos)")
    if not audio_ok:
        reasons.append(f"sin señal de audio (pico RMS {peak_rms:.4f} bajo el piso {AUDIO_FLOOR_RMS}) — revisar hidrófono/cable")
    if not TWILIO_CONFIGURED:
        reasons.append("sin credenciales Twilio — ninguna alerta WhatsApp puede salir (modo banco)")
    return {
        "detector_ok":     det_ok,
        "audio_ok":        audio_ok,
        "duty_cycle_pct":  duty_cycle_pct(),
        "deaf_seconds_total": round(_deaf_seconds_total, 1),
        "clips_dropped":   _clips_dropped_total,
        "suppressed_count": _suppressed_total,
        "upload_backlog":  event_spool_len(),      # eventos esperando subida
        "events_dropped":  _events_dropped_total,  # eventos perdidos por tope del spool
        "wa_pending":      _pending_alert_count(), # WhatsApps esperando reintento
        "archive_queue":   _archive_queue_len(),
        "degraded_reason": "; ".join(reasons) if reasons else None,
    }


def _active_detectors() -> list:
    return {"psd": ["psd_tonal"], "rms": ["rms"], "auto": ["psd_tonal", "rms"]}.get(DETECTION_MODE, [])


def upload_status(session_start: datetime, alert_count: int,
                  threshold: float, last_rms: float) -> None:
    """Sube sites/{site}/status.json con la forma v2 del contrato.

    Solo campos que el dashboard consume, más la superficie de salud. Sin
    "status": "online" (la vida se deriva de last_seen), sin coordenadas
    (viven en _sites.json — F-08), sin los quince campos v1 que nadie leía.
    """
    now = datetime.now(timezone.utc)
    uptime_s = int((now - session_start).total_seconds())
    modem = fetch_modem_signal()
    solar = fetch_ve_direct()
    stats = get_system_stats()
    # Alerta batería si cruza umbral peligroso (no-op si no hay Victron)
    check_battery_alert(solar)
    status = {
        "schema_version":    SCHEMA_VERSION,
        "site":              SITE,
        "device":            DEVICE_ID,
        "generated_utc":     now.isoformat(),

        "software_version":  SOFTWARE_VERSION,
        "last_seen":         now.isoformat(),
        "session_start":     session_start.isoformat(),
        "uptime_seconds":    uptime_s,
        "system_uptime_s":   stats.get("system_uptime_s"),

        # Salud: la superficie fail-loud (R-2.x). Una unidad corriendo pero sin
        # detectar tiene que poder decirlo.
        "health":            build_health(),

        # Qué corre de verdad y con qué umbrales (R-2.6, F-09: v1 publicaba un
        # umbral que no participaba en la decisión).
        "detection": {
            "mode":       DETECTION_MODE,
            "detectors":  _active_detectors(),
            "thresholds": {
                "score_min":        SCORE_MIN,
                "rms_min":          ALERT_MIN_RMS,
                "rms_threshold":    ALERT_THRESHOLD,
                "psd_threshold_db": PSD_THRESHOLD_DB,
                "psd_f_min":        PSD_F_MIN,
                "psd_f_max":        PSD_F_MAX,
            },
            "cooldown_s": ALERT_COOLDOWN,
            "last_rms":   round(last_rms, 4),
        },

        "audio":  {"device": AUDIO_DEVICE, "sample_rate": SAMPLE_RATE, "channels": CHANNELS},

        # Energía solar — Victron BlueSolar MPPT por VE.Direct (USB).
        # system_load_w derivado: panel − carga a batería; ignora pérdidas MPPT ~5%.
        "power": {
            "battery_voltage_v": solar.get("battery_voltage_v"),
            "battery_current_a": solar.get("battery_current_a"),
            "panel_voltage_v":   solar.get("panel_voltage_v"),
            "panel_power_w":     solar.get("panel_power_w"),
            "charge_state":      solar.get("charge_state"),
            "charge_state_id":   solar.get("charge_state_id"),
            "yield_today_kwh":   solar.get("yield_today_kwh"),
            "yield_total_kwh":   solar.get("yield_total_kwh"),
            "max_power_today_w": solar.get("max_power_today_w"),
            "system_load_w":     _compute_system_load(solar),
        },

        "network": {
            "signal_bars":  modem.get("signal_bars"),
            "signal_rssi":  modem.get("signal_rssi"),
            "network_type": modem.get("network_type"),
        },

        "system": {
            "cpu_temp_c":    stats.get("cpu_temp_c"),
            "disk_used_pct": stats.get("disk_used_pct"),
            "disk_free_gb":  stats.get("disk_free_gb"),
            "disk_total_gb": stats.get("disk_total_gb"),
            "ram_used_pct":  stats.get("ram_used_pct"),
            "ram_used_mb":   stats.get("ram_used_mb"),
            "ram_total_mb":  stats.get("ram_total_mb"),
        },
    }
    upload_json(site_path("status.json"), status)
    log.info("  → status.json actualizado (uptime %ds, %d alertas, señal %s/%s)",
             uptime_s, alert_count,
             modem.get("signal_bars", "?"), modem.get("network_type") or "?")
    # Histórico local en CSV para análisis fuera del dashboard. El CSV es plano,
    # así que se le pasa una vista aplanada de los campos que registra.
    flat = {"last_seen": status["last_seen"], "last_rms": round(last_rms, 4),
            "alert_count_session": alert_count,
            **status["power"], **status["network"], **status["system"]}
    append_data_log(flat)


def check_remote_config() -> dict | None:
    """Lee sites/{site}/remote_config.json y devuelve el dict si hay cambios.

    No lo produce el dispositivo (lo escribe la operación); se retira en favor
    del endpoint firmado del backend en la Fase 2 (F-10).
    """
    cfg = download_json(site_path("remote_config.json"))
    if cfg and cfg.get("version"):
        return cfg
    return None


# --- Azure IoT Hub -----------------------------------------------------------

def build_iot_client():
    if not IOTHUB_CONNECTION_STRING:
        log.warning("OCEANKIND_IOTHUB_CONNECTION_STRING no definida — continuando sin IoT Hub.")
        return None
    try:
        from azure.iot.device import IoTHubDeviceClient  # noqa: PLC0415
    except ImportError:
        log.warning("azure-iot-device no instalado — continuando sin IoT Hub.")
        return None
    try:
        client = IoTHubDeviceClient.create_from_connection_string(IOTHUB_CONNECTION_STRING)
        client.connect()
        log.info("Conectado a Azure IoT Hub ✓")
        return client
    except Exception as exc:
        log.warning("No se pudo conectar a IoT Hub: %s — continuando sin IoT Hub.", exc)
        return None


def send_message(client, audio_level: float, peak_db: float,
                 msg_type: str = "alert", audio_url: str | None = None,
                 threshold: float = ALERT_THRESHOLD) -> None:
    from azure.iot.device import Message  # noqa: PLC0415
    payload = {
        "type":        msg_type,
        "audio_level": round(audio_level, 6),
        "peak_db":     round(peak_db, 2),
        "alert_flag":  msg_type == "alert",
        "threshold":   threshold,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "device":      DEVICE_ID,
        "source":      "zoom_h5_builtin",
    }
    if audio_url:
        payload["audio_url"] = audio_url
    msg = Message(json.dumps(payload))
    msg.content_encoding = "utf-8"
    msg.content_type = "application/json"
    if msg_type == "alert":
        msg.custom_properties["alert"] = "true"
    client.send_message(msg)


# --- WhatsApp ----------------------------------------------------------------

def _wa_vars(*values, prefix_v2: bool = True) -> dict:
    """Arma el dict {"1": …, "2": …} de variables de plantilla según WA_TPL_VERSION.

    v2 (plantillas sin campo de estación): se antepone la estación al primer valor
        ("Matanzas · MOTOR"), salvo prefix_v2=False — p.ej. la de batería, donde {{1}}
        es un voltaje y anteponerle el nombre quedaría ilegible.
    v3 (plantillas nuevas): la estación ocupa {{1}} y todo lo demás se corre un lugar.
    """
    vals = list(values)
    if WA_TPL_VERSION == "3":
        vals = [SENSOR_LOCATION_NAME] + vals
    elif prefix_v2 and vals:
        vals[0] = f"{SENSOR_LOCATION_NAME} · {vals[0]}"
    return {str(i + 1): v for i, v in enumerate(vals)}


def _wa_send(content_sid: str, variables: dict, log_ok: str = "",
             recipients: list | None = None) -> None:
    """Envía un WhatsApp usando una plantilla aprobada (Content SID) desde el número de producción.

    variables: dict con keys "1","2",... que llenan {{1}},{{2}},... de la plantilla.
    No usa texto libre, así que funciona como mensaje business-initiated sin ventana de 24h.
    recipients: lista de destinatarios (TO_ALERTA o TO_TECNICO). Si es None usa
                TWILIO_TO_LIST, que es el comportamiento histórico.
    """
    if not TWILIO_CONFIGURED:
        log.error("WhatsApp NO enviado (sin credenciales Twilio — modo banco)")
        return
    from twilio.rest import Client  # noqa: PLC0415
    to_list = recipients if recipients else TWILIO_TO_LIST
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    variables_json = json.dumps(variables, ensure_ascii=False)
    sent = 0
    last_err = None
    for to_number in to_list:
        try:
            client.messages.create(
                from_=TWILIO_FROM, to=to_number,
                content_sid=content_sid,
                content_variables=variables_json,
            )
            sent += 1
        except Exception as exc:
            last_err = exc
            log.warning("WhatsApp falló para %s: %s", to_number, exc)
    if log_ok and sent > 0:
        log.info("%s (enviado a %d/%d)", log_ok, sent, len(to_list))
    if sent == 0 and last_err:
        raise last_err


def send_degraded_alert(reason: str) -> None:
    """Aviso WhatsApp de degradación (detector caído, sin audio). Nunca silencioso.

    Usa la plantilla de heartbeat (la única aprobada con un campo libre) con el
    motivo en el campo de uptime/audio. Best-effort: si Twilio no está
    configurado o falla, queda en el log y en health.degraded_reason igual.
    """
    if not TWILIO_CONFIGURED or not TWILIO_TO or "XXXXXXXXX" in TWILIO_TO:
        log.error("DEGRADADO (sin WhatsApp configurado): %s", reason)
        return
    try:
        _wa_send(WA_TPL_HEARTBEAT, _wa_vars(
            fmt_local(),                       # hora de Chile
            "n/d", "n/d", "n/d",               # batería / panel / señal
            f"⚠️ DEGRADADO: {reason}",         # campo uptime+audio
        ), log_ok=f"⚠️ aviso de degradación enviado: {reason}",
           recipients=TO_TECNICO)
    except Exception as exc:
        log.warning("no se pudo enviar aviso de degradación: %s", exc)


def maybe_alert_audio_health() -> None:
    """Si el hidrófono lleva la ventana completa sin superar el piso, avisa UNA vez (R-2.2)."""
    global _audio_alert_sent
    ok, peak, n = audio_health()
    if ok:
        if _audio_alert_sent:
            log.info("🎙️ señal de audio recuperada (pico %.4f)", peak)
        _audio_alert_sent = False
        return
    if not _audio_alert_sent:
        log.error("🔴 SIN SEÑAL DE AUDIO: pico %.4f bajo el piso %s en %d muestras — probable hidrófono desconectado",
                  peak, AUDIO_FLOOR_RMS, n)
        send_degraded_alert(f"sin señal de audio (pico {peak:.4f}) — revisar hidrófono/cable")
        _audio_alert_sent = True


def _save_pending_alert(rms: float, peak_db: float,
                        blob_name: str | None, ts: str,
                        label: str | None = None) -> None:
    """Guarda una alerta fallida localmente para reintentarla después."""
    pending = []
    if PENDING_ALERTS_FILE.exists():
        try:
            pending = json.loads(PENDING_ALERTS_FILE.read_text())
        except Exception:
            pending = []
    pending.append({
        "rms": rms, "peak_db": peak_db,
        "blob_name": blob_name, "timestamp": ts,
        "label": label or DETECTION_LABEL,
        "attempts": 1,
    })
    pending = pending[-50:]   # máximo 50 alertas en buffer
    PENDING_ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PENDING_ALERTS_FILE.write_text(json.dumps(pending, indent=2))
    log.info("  → Alerta guardada localmente (%d pendiente/s)", len(pending))


def retry_pending_whatsapp() -> None:
    """Reenvía alertas WhatsApp que fallaron y quedaron en buffer local."""
    if not PENDING_ALERTS_FILE.exists():
        return
    try:
        pending = json.loads(PENDING_ALERTS_FILE.read_text())
    except Exception:
        return
    if not pending:
        return

    log.info("Reintentando %d alerta/s pendiente/s...", len(pending))
    still_pending = []
    for alert in pending:
        try:
            audio_url = (f"{DASHBOARD_URL}?play={alert['blob_name']}"
                         if alert.get("blob_name") else DASHBOARD_URL)
            _wa_send(WA_TPL_ALERT, _wa_vars(
                alert.get("label") or DETECTION_LABEL,          # tipo de evento
                fmt_local_iso(alert.get("timestamp", "")),      # hora de Chile
                f"{alert['rms']:.4f}",                          # RMS
                "n/d",                                          # confianza (no guardada en el buffer)
                audio_url,                                      # link al audio
            ), recipients=TO_ALERTA)
            log.info("  → Alerta pendiente reenviada: %s", alert.get("timestamp"))
        except Exception as exc:
            alert["attempts"] = alert.get("attempts", 1) + 1
            if alert["attempts"] < 10:
                still_pending.append(alert)
            else:
                log.warning("  Alerta descartada tras 10 intentos: %s", alert.get("timestamp"))

    if still_pending:
        PENDING_ALERTS_FILE.write_text(json.dumps(still_pending, indent=2))
    else:
        PENDING_ALERTS_FILE.unlink(missing_ok=True)
    log.info("  → %d alerta/s aún pendiente/s", len(still_pending))


NETWORK_TYPE_LABELS = {
    # 5G (NR = New Radio)
    "NR":       "5G",   "NR5G":  "5G",  "ENDC":  "5G NSA",  "ENDC_NR5G": "5G NSA",
    # 4G LTE
    "LTE":      "4G",   "LTE+":  "4G+", "LTE-A": "4G+",     "LTE-CA":    "4G+",
    # 3G
    "WCDMA":    "3G",   "HSPA":  "3G",  "HSPA+": "3G+",     "UMTS":      "3G",
    "TD-SCDMA": "3G",
    # 2G
    "GSM":      "2G",   "GPRS":  "2G",  "EDGE":  "2G",
}


def friendly_network_type(raw: str | None) -> str:
    """Mapea 'LTE' → '4G', 'NR5G' → '5G', etc. Devuelve string vacío si raw está vacío."""
    if not raw:
        return ""
    return NETWORK_TYPE_LABELS.get(raw.upper(), raw)


def fmt_uptime(seconds: int | float | None) -> str:
    if seconds is None:
        return "?"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, _   = divmod(rem, 60)
    if d:
        return f"{d}d {h}h {m}m"
    return f"{h}h {m}m"


def send_whatsapp_heartbeat(session_start: datetime,
                             alert_count: int, last_rms: float) -> None:
    """Envía ping de vida por WhatsApp con estadísticas del sistema."""
    if not TWILIO_TO or "XXXXXXXXX" in TWILIO_TO:
        return
    try:
        stats   = get_system_stats()
        modem   = fetch_modem_signal()
        solar   = fetch_ve_direct()
        elapsed = (datetime.now(timezone.utc) - session_start).total_seconds()
        session_up = fmt_uptime(elapsed)
        system_up  = fmt_uptime(stats.get("system_uptime_s"))

        lines = [
            "✅ *OceanKind — Sistema activo*",
            f"📍 {SENSOR_LOCATION_NAME}  ({DEVICE_ID})",
            f"⏱️ Sesión: {session_up}  ·  Sistema: {system_up}",
            f"🔔 Alertas esta sesión: {alert_count}",
            f"🔊 Último RMS: {last_rms:.4f}",
        ]
        # Señal 4G/5G del modem
        if modem.get("signal_bars") is not None:
            bars   = max(0, min(5, int(modem["signal_bars"])))
            net    = friendly_network_type(modem.get("network_type")) or "?"
            filled = "▮" * bars + "▯" * (5 - bars)
            lines.append(f"📶 Señal: {filled} {bars}/5 · {net}")
        # Batería + panel solar (Victron VE.Direct)
        if solar.get("battery_voltage_v") is not None:
            v   = solar["battery_voltage_v"]
            i   = solar.get("battery_current_a")
            arrow = "↑" if (i is not None and i > 0.05) else ("↓" if (i is not None and i < -0.05) else "")
            i_str = f" {arrow}{abs(i):.2f}A" if i is not None else ""
            lines.append(f"🔋 Batería: {v} V{i_str}")
        if solar.get("panel_power_w") is not None:
            p   = solar["panel_power_w"]
            cs  = solar.get("charge_state") or ""
            cs_str = f" · {cs}" if cs else ""
            lines.append(f"☀️ Panel: {p} W{cs_str}")
        if solar.get("yield_today_kwh") is not None:
            lines.append(f"⚡ Generado hoy: {round(solar['yield_today_kwh'] * 1000)} Wh")
        # Consumo del sistema (Pi+modem+audio) derivado del Victron
        sys_load = _compute_system_load(solar)
        if sys_load is not None:
            lines.append(f"🔌 Consumo sistema: {sys_load} W (~{round(sys_load * 24)} Wh/día)")
        if stats.get("cpu_temp_c") is not None:
            lines.append(f"🌡️ CPU: {stats['cpu_temp_c']}°C")
        if stats.get("disk_used_pct") is not None:
            lines.append(
                f"💾 SD: {stats['disk_used_pct']}% usado "
                f"({stats['disk_free_gb']:.1f}/{stats['disk_total_gb']:.0f} GB)"
            )
        if stats.get("ram_used_pct") is not None:
            lines.append(f"🧠 RAM: {stats['ram_used_pct']}% usada")
        lines.append(f"🕐 {fmt_local()}")

        # Salud del audio → se pliega en el campo 5 (uptime) porque el template
        # aprobado no tiene un campo dedicado. Para una línea propia hace falta
        # un template nuevo aprobado por Meta.
        ok_audio, peak_rms, _n = audio_health()
        audio_str = (f"🎙️ grabando OK (pico {peak_rms:.4f})" if ok_audio
                     else f"⚠️ SIN AUDIO (pico {peak_rms:.4f}) — revisar hidrófono/cable")

        _wa_send(WA_TPL_HEARTBEAT, _wa_vars(
            fmt_local(),                                                                                  # hora de Chile
            (f"{solar['battery_voltage_v']}" if solar.get("battery_voltage_v") is not None else "n/d"),   # batería V
            (f"{solar['panel_power_w']}"     if solar.get("panel_power_w")     is not None else "n/d"),   # panel W
            (f"{int(modem['signal_bars'])}"  if modem.get("signal_bars")       is not None else "n/d"),   # señal
            f"{session_up}  ·  {audio_str}",                                                              # uptime + audio
        ), recipients=TO_TECNICO)
        log.info("  → Heartbeat WhatsApp enviado")
    except Exception as exc:
        log.warning("Error enviando heartbeat WhatsApp: %s", exc)


# ─── Llamada de voz ante clúster de detecciones ──────────────────────────────
_recent_alert_ts: list = []   # timestamps monotónicos de detecciones recientes
_last_call_ts = 0.0


def _place_call(to_number: str) -> None:
    """Genera una llamada telefónica con un mensaje de voz (TwiML <Say>)."""
    from twilio.rest import Client  # noqa: PLC0415
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    twiml = (
        '<Response>'
        '<Say voice="Polly.Lupe" language="es-MX">'
        f'Alerta del sistema acústico Mar Futura. Se registraron múltiples detecciones '
        f'en pocos minutos en {SENSOR_LOCATION_NAME}. '
        'Revisa el panel y coordina fiscalización según corresponda.'
        '</Say>'
        '<Pause length="1"/>'
        '<Say voice="Polly.Lupe" language="es-MX">'
        'Repito: múltiples detecciones acústicas. Atención.'
        '</Say>'
        '</Response>'
    )
    client.calls.create(to=to_number, from_=CALL_FROM, twiml=twiml)


def maybe_trigger_cluster_call() -> None:
    """Registra la detección y, si hubo CALL_CLUSTER_COUNT o más dentro de
    CALL_CLUSTER_WINDOW_S segundos, genera una LLAMADA de voz (con cooldown)."""
    global _last_call_ts
    if not CALL_ENABLED or not CALL_TO_LIST:
        return
    now = time.monotonic()
    _recent_alert_ts.append(now)
    cutoff = now - CALL_CLUSTER_WINDOW_S
    while _recent_alert_ts and _recent_alert_ts[0] < cutoff:
        _recent_alert_ts.pop(0)
    if len(_recent_alert_ts) < CALL_CLUSTER_COUNT:
        return
    if now - _last_call_ts < CALL_COOLDOWN_S:
        log.info("  Clúster de %d detecciones (<%.0fs), pero llamada en cooldown.",
                 len(_recent_alert_ts), CALL_CLUSTER_WINDOW_S)
        return
    _last_call_ts = now
    log.warning("🚨 CLÚSTER: %d detecciones en <%.0fs → generando LLAMADA de voz",
                len(_recent_alert_ts), CALL_CLUSTER_WINDOW_S)
    for to in CALL_TO_LIST:
        try:
            _place_call(to)
            log.warning("  📞 Llamada iniciada a %s", to)
        except Exception as exc:
            log.warning("  ✗ Falló la llamada a %s: %s", to, exc)


def send_whatsapp(rms: float, peak_db: float,
                  blob_name: str | None = None,
                  ml_result: dict | None = None,
                  label: str | None = None) -> None:
    """Envía alerta de detección por WhatsApp (plantilla aprobada).

    blob_name: nombre del archivo WAV en el contenedor (p.ej. 'alert_2026-05-04T15-30-00.wav').
    ml_result: dict opcional con pred/proba/label del detector.
    label:     etiqueta explícita del evento; si falta se toma de ml_result o DETECTION_LABEL.
    """
    if not TWILIO_CONFIGURED:
        log.error("WhatsApp NO enviado (sin credenciales Twilio — modo banco): detección RMS=%.4f", rms)
        return
    if not TWILIO_TO or "XXXXXXXXX" in TWILIO_TO:
        log.warning("WhatsApp: configura TWILIO_TO con tu número real.")
        return
    label = label or (ml_result.get("label") if ml_result else None) or DETECTION_LABEL
    try:
        conf  = (f"{ml_result.get('proba', 0)*100:.1f}%" if ml_result else "n/d")
        audio_url = (f"{DASHBOARD_URL}?play={blob_name}" if blob_name else DASHBOARD_URL)
        _wa_send(WA_TPL_ALERT, _wa_vars(
            label,                          # tipo de evento
            fmt_local(fmt="%H:%M:%S"),      # hora de Chile
            f"{rms:.4f}",                   # RMS
            conf,                           # confianza del modelo
            audio_url,                      # link al audio
        ), recipients=TO_ALERTA)
        log.info("  → WhatsApp enviado a %s", TWILIO_TO)
    except ImportError:
        log.warning("twilio no instalado — corre: pip3 install twilio --break-system-packages")
    except Exception as exc:
        log.warning("Error WhatsApp: %s — guardando en buffer local", exc)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        _save_pending_alert(rms, peak_db, blob_name, ts, label=label)


# --- Loop principal ----------------------------------------------------------

_last_archive_time = 0.0


def archive_or_delete_clip(clip_path: str, now: float) -> None:
    """Todo clip termina archivado o borrado, en TODOS los caminos (F-03).

    El directorio de clips es RAM: un archivo que sobrevive a su iteración es
    una fuga de memoria en una placa de 2 GB. Los descartes por tope de cola se
    cuentan en health.clips_dropped (R-1.3: publicar lo que se descarta).
    """
    global _last_archive_time, _clips_dropped_total
    p = Path(clip_path)
    if not p.exists():
        return
    if (now - _last_archive_time) >= ARCHIVE_INTERVAL:
        try:
            ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            p.rename(ARCHIVE_DIR / p.name)
            _last_archive_time = now
            queue = sorted(ARCHIVE_DIR.glob("clip_*.wav"))
            if len(queue) > ARCHIVE_MAX_FILES:
                dropped = len(queue) - ARCHIVE_MAX_FILES
                for old in queue[:dropped]:
                    old.unlink(missing_ok=True)
                _clips_dropped_total += dropped
                log.warning("cola de archivo llena — %d clips viejos borrados sin subir (descartados total: %d)",
                            dropped, _clips_dropped_total)
            return
        except OSError as exc:
            log.warning("no se pudo archivar clip: %s — se borra", exc)
    try:
        p.unlink()
    except OSError:
        pass


def main() -> None:
    global ALERT_THRESHOLD, ALERT_COOLDOWN, HEARTBEAT_INTERVAL, _suppressed_total

    validate_startup_config()

    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    # Barrido inicial (F-03): clips huérfanos de un run anterior ocupan RAM.
    stale = list(CLIPS_DIR.glob("*.wav"))
    for f in stale:
        try:
            f.unlink()
        except OSError:
            pass
    if stale:
        log.info("Barrido inicial: %d clip/s huérfano/s borrado/s de %s", len(stale), CLIPS_DIR)

    if STORAGE_ENABLED:
        # Contrato v2: este sitio se declara en _sites.json (R-5.2) y el árbol
        # queda completo con stubs conformes para los blobs de otros productores.
        publish_site_registry()
        ensure_aux_blobs()

    session_start   = datetime.now(timezone.utc)
    alert_count     = 0
    last_rms        = 0.0
    applied_config_version = None

    log.info("=== OceanKind %s — %s ===", SOFTWARE_VERSION, DEVICE_ID)
    log.info("Detección  : %s  |  Cooldown: %.0f s  |  Heartbeat IoT: %.0f s",
             DETECTION_MODE.upper(), ALERT_COOLDOWN, HEARTBEAT_INTERVAL)
    log.info("Detección continua  |  Archivo: 1 clip cada %.0fs → %s (tope %d clips)",
             ARCHIVE_INTERVAL, ARCHIVE_DIR, ARCHIVE_MAX_FILES)
    if DETECTION_MODE in ("psd", "auto"):
        log.info("Detector   : PSD tonal (Barosin/Integral)  |  thr=%.1f dB  |  banda %d-%d Hz  |  Score mínimo: %.2f  |  Label: %s",
                 PSD_THRESHOLD_DB, int(PSD_F_MIN), int(PSD_F_MAX), SCORE_MIN, DETECTION_LABEL)
    if DETECTION_MODE in ("rms", "auto"):
        log.info("Umbral RMS%s: %.3f", " (fallback si el clasificador cae)" if DETECTION_MODE == "auto" else "",
                 ALERT_THRESHOLD)
    log.info("Heartbeat WhatsApp: cada %.0f s (%.1f h)",
             WHATSAPP_HEARTBEAT_INTERVAL, WHATSAPP_HEARTBEAT_INTERVAL / 3600)
    log.info("Config remota: chequeo cada %.0f s", CONFIG_CHECK_INTERVAL)
    log.info("Escucha en curso — Ctrl+C para detener\n")

    # Reenviar alertas que quedaron pendientes del run anterior
    retry_pending_whatsapp()

    client = build_iot_client()

    last_alert_time              = 0.0
    last_heartbeat_time          = 0.0
    last_config_check            = 0.0
    last_whatsapp_heartbeat_time = 0.0
    last_power_history_time      = 0.0

    try:
        while True:
            cycle_start = time.monotonic()   # duty cycle medido, no afirmado (R-1.2)
            now = time.time()
            ts  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")

            # --- Chequear configuración remota ---
            if (now - last_config_check) >= CONFIG_CHECK_INTERVAL:
                cfg = check_remote_config()
                if cfg and cfg.get("version") != applied_config_version:
                    new_threshold = float(cfg.get("alert_threshold", ALERT_THRESHOLD))
                    new_cooldown  = float(cfg.get("cooldown_seconds", ALERT_COOLDOWN))
                    new_heartbeat = float(cfg.get("heartbeat_interval", HEARTBEAT_INTERVAL))
                    log.info("⚙️  Config remota aplicada: threshold=%.3f, cooldown=%.0f, heartbeat=%.0f",
                             new_threshold, new_cooldown, new_heartbeat)
                    ALERT_THRESHOLD   = new_threshold
                    ALERT_COOLDOWN    = new_cooldown
                    HEARTBEAT_INTERVAL = new_heartbeat
                    applied_config_version = cfg.get("version")
                last_config_check = now

            # --- Captura de audio ---
            clip_path    = str(CLIPS_DIR / f"clip_{ts}.wav")
            captured_dt  = datetime.now(timezone.utc)               # hora de CAPTURA (R-4.3)
            captured_iso = captured_dt.isoformat()
            listened_s   = 0.0
            try:
                rms, peak_db, listened_s = capture_audio(clip_path)
                last_rms = rms
                record_rms(rms)   # para el chequeo de salud del audio en el heartbeat

                # --- Clasificación y decisión (F-01: cada modo decide de verdad) ---
                #   "psd"  → doble gate: RMS >= ALERT_MIN_RMS y score >= SCORE_MIN.
                #            Si el clasificador cae, NO hay fallback — pero suena la
                #            alarma de detector caído (F-02), nunca silencio.
                #   "rms"  → solo RMS >= ALERT_THRESHOLD. Sí puede disparar (F-01).
                #   "auto" → PSD si clasifica; si el clasificador cae, fallback RMS real.
                ml_result = {}
                score = 0.0
                if DETECTION_MODE in ("psd", "auto") and Path(clip_path).exists():
                    ml_result = classify_clip(clip_path)
                    if ml_result:
                        score = ml_result.get("proba", 0.0)

                if DETECTION_MODE == "psd" or (DETECTION_MODE == "auto" and ml_result):
                    alert       = rms >= ALERT_MIN_RMS and score >= SCORE_MIN
                    decided_by  = "psd_tonal"          # lo que DE VERDAD decidió (F-01)
                    detector    = "psd_tonal"
                    event_type  = "vessel"             # firma tonal = maquinaria (D-014)
                    event_label = (ml_result.get("label") if ml_result else None) or DETECTION_LABEL
                else:
                    alert       = rms >= ALERT_THRESHOLD
                    decided_by  = "rms" if DETECTION_MODE == "rms" else "rms_fallback"
                    detector    = "rms"
                    event_type  = "unknown"            # el RMS no distingue tipo de evento
                    event_label = "RUIDO FUERTE"

                bar  = level_bar(rms, ALERT_THRESHOLD)
                tag  = ""
                if ml_result:
                    tag = f"  psd={ml_result.get('label','?')}({ml_result.get('proba',0):.2f})"
                flag = " *** ALERTA ***" if alert else ""
                log.info("[%s] RMS=%.4f  %.1f dB%s%s", bar, rms, peak_db, tag, flag)

                # Clúster de detecciones crudas → LLAMADA de voz.
                # Cuenta cada detección (no cada WhatsApp), por eso va ANTES del cooldown de
                # WhatsApp: así "3+ detecciones en 4 min" sí puede darse aunque WhatsApp esté en cooldown.
                if alert:
                    maybe_trigger_cluster_call()

                # score del evento (0..1 por contrato): la proba del PSD, o el RMS
                # normalizado cuando decidió el umbral RMS (el RMS ya es 0..1).
                event_score = score if detector == "psd_tonal" else rms
                detector_meta = ({"decided_by": decided_by, **({k: ml_result.get(k)
                                  for k in ("pred", "proba", "label")} if ml_result else {})})

                # --- Alerta notificada ---
                if alert and (now - last_alert_time) >= ALERT_COOLDOWN:
                    event_id = str(uuid.uuid4())
                    event_rel, clip_rel = event_rel_paths(captured_dt, event_id)
                    clip_uploaded = False

                    # 1) Subir el clip PRIMERO (F-13/R-4.5): notificar antes de subir
                    #    produce links muertos permanentes si el upload falla.
                    if STORAGE_ENABLED and Path(clip_path).exists():
                        log.info("  Subiendo clip...")
                        clip_uploaded = upload_clip(clip_path, clip_rel)
                        if clip_uploaded:
                            log.info("  → %s", clip_rel)

                    # 2) Registrar SIEMPRE, haya subido el clip o no (R-4.1: un blob
                    #    por evento, inmutable; R-4.4: el estado del clip se dice).
                    if STORAGE_ENABLED:
                        write_event(build_event(event_id, captured_iso, event_type, detector,
                                                event_score, False, rms, peak_db,
                                                clip_rel, clip_uploaded, detector_meta),
                                    event_rel)

                    # 3) Notificar DESPUÉS del upload. Si el clip no subió, el link
                    #    apunta al dashboard (no a un blob muerto).
                    send_whatsapp(rms, peak_db, clip_rel if clip_uploaded else None,
                                  ml_result=ml_result or None, label=event_label)
                    alert_count += 1
                    last_alert_time = now

                    if client:
                        try:
                            send_message(client, rms, peak_db, msg_type="alert",
                                         audio_url=clip_rel if clip_uploaded else None,
                                         threshold=ALERT_THRESHOLD)
                            log.info("  → Alerta enviada a IoT Hub")
                        except Exception as exc:
                            log.warning("  Error enviando alerta: %s", exc)

                # --- Detección dentro del cooldown: se registra IGUAL (R-4.2, D-008) ---
                # El cooldown limita NOTIFICACIONES. Nunca decide qué se registra: los
                # eventos son el registro científico y descartar eventos porque un
                # mensaje fue rate-limiteado lo corrompe (F-03). Sin WhatsApp, sin
                # upload de audio — solo el blob del evento, marcado suppressed.
                elif alert:
                    _suppressed_total += 1
                    log.info("  detección en cooldown — registrada como suprimida (%d en la sesión)",
                             _suppressed_total)
                    if STORAGE_ENABLED:
                        event_id = str(uuid.uuid4())
                        event_rel, clip_rel = event_rel_paths(captured_dt, event_id)
                        write_event(build_event(event_id, captured_iso, event_type, detector,
                                                event_score, True, rms, peak_db,
                                                clip_rel, False, detector_meta),
                                    event_rel)
            finally:
                # F-03: el clip se archiva o borra en TODOS los caminos, incluidos
                # errores. Este directorio es RAM.
                archive_or_delete_clip(clip_path, time.time())

            # --- Heartbeat IoT Hub (frecuente) ---
            if HEARTBEAT_INTERVAL > 0 and (now - last_heartbeat_time) >= HEARTBEAT_INTERVAL:
                maybe_alert_audio_health()   # R-2.2: hidrófono muerto suena, no se calla
                if client:
                    try:
                        send_message(client, rms, peak_db, msg_type="heartbeat",
                                     threshold=ALERT_THRESHOLD)
                        log.info("  → Heartbeat enviado a IoT Hub")
                    except Exception as exc:
                        log.warning("  Error en heartbeat IoT Hub: %s", exc)
                if STORAGE_ENABLED:
                    drain_event_spool()   # eventos que no pudieron subir en su momento
                    try:
                        upload_status(session_start, alert_count, ALERT_THRESHOLD, last_rms)
                    except Exception as exc:
                        log.warning("  Error subiendo status.json: %s", exc)
                    # Power history cada 10 min (no cada 60s — sería desperdicio)
                    if now - last_power_history_time >= 600:
                        try:
                            upload_power_history()
                            last_power_history_time = now
                        except Exception as exc:
                            log.warning("  Error subiendo power_history: %s", exc)
                last_heartbeat_time = now

            # --- Heartbeat WhatsApp (cada 6 horas) ---
            if (now - last_whatsapp_heartbeat_time) >= WHATSAPP_HEARTBEAT_INTERVAL:
                send_whatsapp_heartbeat(session_start, alert_count, last_rms)
                # Aprovechar el heartbeat para reintentar alertas pendientes
                retry_pending_whatsapp()
                last_whatsapp_heartbeat_time = now

            # --- Instrumentación del ciclo (R-1.2, F-05): medir, no afirmar ---
            record_cycle(listened_s, time.monotonic() - cycle_start)

    except KeyboardInterrupt:
        log.info("\nDetenido por usuario.")
    finally:
        if client:
            client.disconnect()
            log.info("Desconectado de IoT Hub.")


if __name__ == "__main__":
    main()
