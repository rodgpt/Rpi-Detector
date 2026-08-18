"""Configuración: entorno, validación de arranque, y parámetros afinables en caliente.

Todo secreto viene de /etc/oceankind.env; un secreto ausente es un NO-ARRANQUE
(R-8.1), nunca un default literal (F-04). Los parámetros de detección viven en
`CONFIG` (RuntimeConfig), protegidos por lock y afinables por config remota con
rangos acotados (R-3.6, R-8.3, F-09/F-10).
"""

import hashlib
import hmac
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("oceankind")

# ── Hora local ───────────────────────────────────────────────────────────────
# Los WhatsApp van en hora de Chile; internamente todo se guarda en UTC.
try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo(os.getenv("OCEANKIND_TZ", "America/Santiago"))
except Exception:
    LOCAL_TZ = timezone(timedelta(hours=-4))


def fmt_local(dt=None, fmt="%Y-%m-%d %H:%M"):
    d = dt or datetime.now(timezone.utc)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(LOCAL_TZ).strftime(fmt)


def fmt_local_iso(iso_str, fmt="%Y-%m-%d %H:%M"):
    try:
        return fmt_local(datetime.fromisoformat(iso_str), fmt)
    except Exception:
        return iso_str or "n/d"


def _env_float(name: str):
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def _env_float_compat(name: str, legacy_name: str, default: str) -> float:
    if os.environ.get(name, "").strip():
        return float(os.environ[name])
    if os.environ.get(legacy_name, "").strip():
        print(f"AVISO: {legacy_name} es nombre legacy — renombrar a {name} en /etc/oceankind.env",
              file=sys.stderr)
        return float(os.environ[legacy_name])
    return float(default)


# ── Contrato de datos ────────────────────────────────────────────────────────
# v2 (D-016): árbol sites/{site}/…, un blob por evento, sin manifest.
SCHEMA_VERSION = 2
SITE_ID_RE = re.compile(r"^[a-z0-9_-]+$")

# ── Identidad y almacenamiento ───────────────────────────────────────────────
IOTHUB_CONNECTION_STRING  = os.environ.get("OCEANKIND_IOTHUB_CONNECTION_STRING", "")
STORAGE_CONNECTION_STRING = os.environ.get("OCEANKIND_STORAGE_CONNECTION_STRING", "")
STORAGE_CONTAINER         = os.environ.get("OCEANKIND_STORAGE_CONTAINER", "alerts")
# Modo banco/validación (R-9.4): blobs como archivos locales; validar con
# tools/validate_contract.py.
OUTPUT_DIR                = os.environ.get("OCEANKIND_OUTPUT_DIR", "").strip()
DASHBOARD_URL             = os.environ.get("OCEANKIND_DASHBOARD_URL",
                                           "https://marfuturatest.z6.web.core.windows.net/index.html")
DEVICE_ID                 = os.environ.get("OCEANKIND_DEVICE_ID", "Rpi_casa")
SITE                      = os.environ.get("OCEANKIND_SITE", "").strip().strip("/").lower()
STORAGE_ENABLED           = bool(STORAGE_CONNECTION_STRING or OUTPUT_DIR)

MODEM_API_URL = os.environ.get("OCEANKIND_MODEM_API",
                               "http://192.168.0.1/goform/goform_get_cmd_process")

# Ubicación del sensor — por unidad, en el env (F-08). Va a _sites.json, nunca
# a status.json ni al código.
SENSOR_LAT           = _env_float("OCEANKIND_SENSOR_LAT")
SENSOR_LON           = _env_float("OCEANKIND_SENSOR_LON")
SENSOR_LOCATION_NAME = os.environ.get("OCEANKIND_SENSOR_LOCATION", DEVICE_ID)

# ── Twilio ── SIN defaults literales (F-04) ──────────────────────────────────
TWILIO_ACCOUNT_SID = os.environ.get("OCEANKIND_TWILIO_SID", "")
TWILIO_AUTH_TOKEN  = os.environ.get("OCEANKIND_TWILIO_TOKEN", "")
ALLOW_NO_TWILIO    = os.environ.get("OCEANKIND_ALLOW_NO_TWILIO", "0") == "1"
TWILIO_CONFIGURED  = bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN)
TWILIO_FROM        = os.environ.get("OCEANKIND_TWILIO_FROM", "whatsapp:+56926280872")
_TWILIO_TO_RAW     = os.environ.get("OCEANKIND_TWILIO_TO", "whatsapp:+56961987942")
TWILIO_TO_LIST     = [n.strip() for n in _TWILIO_TO_RAW.split(",") if n.strip()]
TWILIO_TO          = TWILIO_TO_LIST[0] if TWILIO_TO_LIST else ""
# Timeout explícito de TODA llamada Twilio (R-5.5): una llamada sin timeout en
# el hilo de transporte es un backlog; en el viejo loop único era sordera.
TWILIO_TIMEOUT_S   = float(os.environ.get("OCEANKIND_TWILIO_TIMEOUT_S", "15"))


def _to_list(env_name: str) -> list:
    raw = os.environ.get(env_name, "").strip() or _TWILIO_TO_RAW
    return [n.strip() for n in raw.split(",") if n.strip()]


TO_ALERTA  = _to_list("OCEANKIND_TO_ALERTA")
TO_TECNICO = _to_list("OCEANKIND_TO_TECNICO")

# Plantillas WhatsApp aprobadas (Content SIDs) — no son secretos.
WA_TPL_ALERT     = os.environ.get("OCEANKIND_WA_TPL_ALERT",     "HX901414845dda773c146ce65d79f79863")
WA_TPL_HEARTBEAT = os.environ.get("OCEANKIND_WA_TPL_HEARTBEAT", "HXe9015f651079907384ecabf3add8dd69")
WA_TPL_BATTERY   = os.environ.get("OCEANKIND_WA_TPL_BATTERY",   "HX9cad665cd4be3a5a5482162d1776d1ba")
WA_TPL_VERSION   = os.environ.get("OCEANKIND_WA_TPL_VERSION", "3").strip()

# Llamada de voz ante clúster de detecciones
CALL_ENABLED          = os.environ.get("OCEANKIND_CALL_ENABLED", "1") == "1"
CALL_FROM             = os.environ.get("OCEANKIND_CALL_FROM", "+15733060329")
CALL_TO_LIST          = [n.strip() for n in os.environ.get("OCEANKIND_CALL_TO", "+56961987942").split(",") if n.strip()]
CALL_CLUSTER_COUNT    = int(os.environ.get("OCEANKIND_CALL_CLUSTER_COUNT", "3"))
CALL_CLUSTER_WINDOW_S = float(os.environ.get("OCEANKIND_CALL_CLUSTER_WINDOW_S", "240"))
CALL_COOLDOWN_S       = float(os.environ.get("OCEANKIND_CALL_COOLDOWN_S", "900"))

# ── Audio ────────────────────────────────────────────────────────────────────
SAMPLE_RATE     = 48000
CHANNELS        = 2
CAPTURE_SECONDS = 5.0
BLOCK_FRAMES    = int(os.environ.get("OCEANKIND_BLOCK_FRAMES", "4800"))   # 0.1 s por bloque
# Detección del dispositivo POR NOMBRE (F-15): substring, case-insensitive.
# Un índice ALSA cambia con la re-enumeración USB; un nombre no.
AUDIO_DEVICE_NAME = os.environ.get("OCEANKIND_AUDIO_DEVICE_NAME",
                                   "hifiberry,sndrpihifiberry,dacplusadc,codec")
# Fuente de audio: "device" (hardware) o "synthetic:<patrón>" para banco sin
# hidrófono (R-9.4). Patrones: tone | noise | impulse | silence.
AUDIO_SOURCE = os.environ.get("OCEANKIND_AUDIO_SOURCE", "device").strip().lower()

# Salud del audio (hidrófono desconectado)
AUDIO_FLOOR_RMS       = float(os.environ.get("OCEANKIND_AUDIO_FLOOR_RMS", "0.0005"))
AUDIO_HEALTH_WINDOW_S = float(os.environ.get("OCEANKIND_AUDIO_HEALTH_WINDOW_S", "900"))

# ── Detección (valores INICIALES; los afinables viven en CONFIG) ─────────────
# DETECTION_MODE se reemplaza por el registro ordenado de detectores en Fase 3
# (D-014). No agregarle modos.
DETECTION_MODE = os.environ.get("OCEANKIND_DETECTION_MODE", "psd").lower()
if DETECTION_MODE == "ml":       # alias legacy
    DETECTION_MODE = "psd"

DETECTION_LABEL = (os.environ.get("OCEANKIND_DETECTION_LABEL", "").strip()
                   or os.environ.get("OCEANKIND_ML_POSITIVE_LABEL", "").strip()
                   or "MOTOR")

_INIT_SCORE_MIN        = _env_float_compat("OCEANKIND_SCORE_MIN", "OCEANKIND_ML_THRESHOLD", "0.60")
_INIT_ALERT_MIN_RMS    = float(os.environ.get("OCEANKIND_ALERT_MIN_RMS", "0.010"))
_INIT_ALERT_THRESHOLD  = float(os.environ.get("OCEANKIND_ALERT_THRESHOLD", "0.08"))
_INIT_COOLDOWN         = float(os.environ.get("OCEANKIND_ALERT_COOLDOWN_S", "60"))
_INIT_HEARTBEAT        = float(os.environ.get("OCEANKIND_HEARTBEAT_S", "60"))
_INIT_PSD_THRESHOLD_DB = float(os.environ.get("OCEANKIND_PSD_THRESHOLD_DB", "8"))
_INIT_PSD_F_MIN        = float(os.environ.get("OCEANKIND_PSD_F_MIN", "55"))
_INIT_PSD_F_MAX        = float(os.environ.get("OCEANKIND_PSD_F_MAX", "1000"))
# Paso entre ventanas de análisis. 5.0 = ventanas pegadas sin solape (el
# comportamiento calibrado). Menor que 5 = ventanas SOLAPADAS: un evento corto
# que cae en el borde entre dos ventanas ya no se diluye (con hop h, un evento
# de hasta 5−h segundos siempre cae entero en alguna ventana). Costo: CPU de
# clasificación × (5/h). Elegir el valor es ciencia (dependencia de cliente 13);
# habilitarlo es configuración, no firmware (D-015).
_INIT_WINDOW_HOP       = float(os.environ.get("OCEANKIND_WINDOW_HOP_S", "5.0"))

# PSD fijos (estructurales, no afinables en caliente)
PSD_DECIMATION = int(os.environ.get("OCEANKIND_PSD_DECIMATION", "4"))
PSD_NFFT       = int(os.environ.get("OCEANKIND_PSD_NFFT", "4096"))
PSD_GUARD_BINS = int(os.environ.get("OCEANKIND_PSD_GUARD_BINS", "2"))
PSD_SEARCH_HZ  = float(os.environ.get("OCEANKIND_PSD_SEARCH_HZ", "15"))

DETECTOR_FAIL_LIMIT = int(os.environ.get("OCEANKIND_DETECTOR_FAIL_LIMIT", "3"))

# ── Cadencias y rutas ────────────────────────────────────────────────────────
WHATSAPP_HEARTBEAT_INTERVAL = float(os.environ.get("OCEANKIND_WA_HEARTBEAT_S", "43200"))
CONFIG_CHECK_INTERVAL       = float(os.environ.get("OCEANKIND_CONFIG_CHECK_S", "300"))
POWER_HISTORY_HOURS         = 72
POWER_HISTORY_BUCKET_S      = 30 * 60
DUTY_WINDOW_S               = float(os.environ.get("OCEANKIND_DUTY_WINDOW_S", "3600"))

# Estado de runtime. HOY es tmpfs (RAM) bajo la protección SD — misma ruta que
# crea la regla tmpfiles de protect_sd.sh. Cuando D-002 decida una partición
# persistente, cambiar SOLO esta variable.
STATE_DIR = Path(os.environ.get("OCEANKIND_STATE_DIR", "/tmp/oceankind"))
# CSV de telemetría: NUNCA en /boot/firmware (regla dura; F-16). Bajo overlay
# se pierde al reiniciar: los huecos resultantes en power_history son señal
# (así se detectan reinicios), no defecto.
DATA_LOG_PATH = Path(os.environ.get("OCEANKIND_DATA_LOG", str(STATE_DIR / "oceankind_data.csv")))
DATA_LOG_MAX_ROWS = int(os.environ.get("OCEANKIND_DATA_LOG_MAX_ROWS", "6000"))

PENDING_ALERTS_FILE = Path(os.environ.get("OCEANKIND_PENDING_ALERTS",
                                          str(Path.home() / "oceankind" / "pending_alerts.json")))
BATTERY_STATE_FILE  = Path(os.environ.get("OCEANKIND_BATTERY_STATE",
                                          str(STATE_DIR / "battery_state.json")))

ARCHIVE_INTERVAL  = float(os.environ.get("OCEANKIND_ARCHIVE_INTERVAL", "60"))
ARCHIVE_DIR       = Path(os.environ.get("OCEANKIND_ARCHIVE_DIR", str(Path.home() / "oceankind" / "archive_queue")))
# El archivo vive en RAM (overlay): 300 ≈ 290 MB (F-22)
ARCHIVE_MAX_FILES = int(os.environ.get("OCEANKIND_ARCHIVE_MAX_FILES", "300"))

EVENT_SPOOL_DIR = Path(os.environ.get("OCEANKIND_EVENT_SPOOL_DIR", str(STATE_DIR / "event_spool")))
EVENT_SPOOL_MAX = int(os.environ.get("OCEANKIND_EVENT_SPOOL_MAX", "500"))

# Colas acotadas (R-1.3). Dimensionadas para 512 MB: un clip estéreo int16 de
# 5 s son ~960 KB.
BLOCK_QUEUE_MAX     = int(os.environ.get("OCEANKIND_BLOCK_QUEUE_MAX", "200"))   # ~20 s de audio
TRANSPORT_QUEUE_MAX = int(os.environ.get("OCEANKIND_TRANSPORT_QUEUE_MAX", "16"))

VEDIRECT_PORT = os.environ.get("OCEANKIND_VEDIRECT_PORT", "")

# Umbrales batería
BATTERY_WARNING_V       = float(os.environ.get("OCEANKIND_BATT_WARNING_V",  "11.8"))
BATTERY_CRITICAL_V      = float(os.environ.get("OCEANKIND_BATT_CRITICAL_V", "11.2"))
BATTERY_EMERGENCY_V     = float(os.environ.get("OCEANKIND_BATT_EMERGENCY_V", "10.8"))
BATTERY_RECOVERY_V      = float(os.environ.get("OCEANKIND_BATT_RECOVERY_V",  "12.2"))
BATTERY_DEBOUNCE_CYCLES = int(os.environ.get("OCEANKIND_BATT_DEBOUNCE_CYCLES", "10"))

# Firma HMAC de la config remota (F-10). Si la clave está definida, un payload
# sin firma válida se RECHAZA. Sin clave, se aplica con warning (clamps igual).
CONFIG_HMAC_KEY = os.environ.get("OCEANKIND_CONFIG_HMAC_KEY", "")


# ── Parámetros afinables en caliente ─────────────────────────────────────────

# Rangos de seguridad para la config remota (R-8.3). Fuera de rango se CLAMPEA
# y se loguea; elegir el valor dentro del rango es ciencia y es del cliente.
CLAMPS = {
    "score_min":        (0.0, 1.0),
    "alert_min_rms":    (0.0, 1.0),
    "alert_threshold":  (0.0, 1.0),
    "cooldown_s":       (10.0, 3600.0),
    "heartbeat_s":      (30.0, 3600.0),
    "psd_threshold_db": (1.0, 30.0),
    "psd_f_min":        (10.0, 2000.0),
    "psd_f_max":        (20.0, 4000.0),
    "window_hop_s":     (1.0, 5.0),   # 1.0 = solape máximo (5× CPU). Medir en banco antes de bajar
}

# Nombres v1 del remote_config.json → nombres actuales (compat de lectura)
_LEGACY_KEYS = {"alert_threshold": "alert_threshold",
                "cooldown_seconds": "cooldown_s",
                "heartbeat_interval": "heartbeat_s"}


class RuntimeConfig:
    """Parámetros de detección afinables sin reinicio (R-3.6).

    Un solo escritor (el poller de config del hilo housekeeping), muchos
    lectores (clasificación, status). Lock para snapshots coherentes.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.score_min        = _INIT_SCORE_MIN
        self.alert_min_rms    = _INIT_ALERT_MIN_RMS
        self.alert_threshold  = _INIT_ALERT_THRESHOLD
        self.cooldown_s       = _INIT_COOLDOWN
        self.heartbeat_s      = _INIT_HEARTBEAT
        self.psd_threshold_db = _INIT_PSD_THRESHOLD_DB
        self.psd_f_min        = _INIT_PSD_F_MIN
        self.psd_f_max        = _INIT_PSD_F_MAX
        self.window_hop_s     = min(5.0, max(1.0, _INIT_WINDOW_HOP))

    def snapshot(self) -> dict:
        with self._lock:
            return {k: getattr(self, k) for k in CLAMPS}

    def apply(self, values: dict) -> list:
        """Aplica valores clampeados. Devuelve descripciones de lo que cambió."""
        changes = []
        with self._lock:
            for key, (lo, hi) in CLAMPS.items():
                if key not in values:
                    continue
                try:
                    raw = float(values[key])
                except (TypeError, ValueError):
                    log.warning("config remota: %s=%r no numérico — ignorado", key, values[key])
                    continue
                val = min(hi, max(lo, raw))
                if val != raw:
                    log.warning("config remota: %s=%s fuera de rango [%s, %s] — clampeado a %s",
                                key, raw, lo, hi, val)
                if getattr(self, key) != val:
                    changes.append(f"{key}: {getattr(self, key)} → {val}")
                    setattr(self, key, val)
            # coherencia interna de la banda PSD
            if self.psd_f_max <= self.psd_f_min:
                self.psd_f_max = self.psd_f_min + 10.0
                changes.append(f"psd_f_max ajustado a {self.psd_f_max} (debe superar psd_f_min)")
        return changes


CONFIG = RuntimeConfig()


def verify_remote_config(payload: dict) -> dict | None:
    """Valida un remote_config.json v2 y devuelve los valores a aplicar, o None.

    Formato: {"version": <str|int>, "config": {...}, "signature": "<hex hmac>"}.
    Compat v1: claves planas (alert_threshold, cooldown_seconds, heartbeat_interval).
    Con OCEANKIND_CONFIG_HMAC_KEY definida, la firma es obligatoria:
    HMAC-SHA256(key, json canónico de {"version":…, "config":{…}}).
    """
    values = payload.get("config")
    if not isinstance(values, dict):
        values = {new: payload[old] for old, new in _LEGACY_KEYS.items() if old in payload}
    if CONFIG_HMAC_KEY:
        canonical = json.dumps({"version": payload.get("version"),
                                "config": payload.get("config", values)},
                               sort_keys=True, separators=(",", ":"))
        expected = hmac.new(CONFIG_HMAC_KEY.encode(), canonical.encode(),
                            hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, str(payload.get("signature", ""))):
            log.error("config remota RECHAZADA: firma HMAC inválida o ausente (versión %r)",
                      payload.get("version"))
            return None
    else:
        log.warning("config remota SIN verificación de firma (OCEANKIND_CONFIG_HMAC_KEY no definida) — "
                    "los rangos se clampean igual")
    return values


def validate_startup_config() -> None:
    """Config inválida = NO ARRANCA (R-8.1). systemd reinicia en loop, a gritos."""
    problems = []
    if not TWILIO_CONFIGURED and not ALLOW_NO_TWILIO:
        problems.append(
            "OCEANKIND_TWILIO_SID / OCEANKIND_TWILIO_TOKEN faltan en el entorno. "
            "Definir en /etc/oceankind.env, o OCEANKIND_ALLOW_NO_TWILIO=1 SOLO en banco de pruebas.")
    if DETECTION_MODE not in ("psd", "rms", "auto"):
        problems.append(f"OCEANKIND_DETECTION_MODE={DETECTION_MODE!r} inválido (psd|rms|auto)")
    if AUDIO_SOURCE != "device" and not AUDIO_SOURCE.startswith("synthetic"):
        problems.append(f"OCEANKIND_AUDIO_SOURCE={AUDIO_SOURCE!r} inválido (device|synthetic:<patrón>)")
    if STORAGE_ENABLED:
        if not SITE:
            problems.append("OCEANKIND_SITE falta. En el contrato v2 todo vive bajo "
                            "sites/{site}/ — definir un id (p.ej. 'punta_norte').")
        elif not SITE_ID_RE.match(SITE):
            problems.append(f"OCEANKIND_SITE={SITE!r} inválido: solo [a-z0-9_-]")
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
