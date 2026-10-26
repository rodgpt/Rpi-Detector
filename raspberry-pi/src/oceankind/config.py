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
from pathlib import Path

log = logging.getLogger("oceankind")


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
STORAGE_CONNECTION_STRING = os.environ.get("OCEANKIND_STORAGE_CONNECTION_STRING", "")
STORAGE_CONTAINER         = os.environ.get("OCEANKIND_STORAGE_CONTAINER", "alerts")
# Modo banco/validación (R-9.4): blobs como archivos locales; validar con
# tools/validate_contract.py.
OUTPUT_DIR                = os.environ.get("OCEANKIND_OUTPUT_DIR", "").strip()
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

# ── Alertas: el dispositivo NO avisa a nadie (D-020) ─────────────────────────
# Toda alerta WhatsApp/voz sale del backend, con la única credencial Twilio del
# sistema, a partir de lo que este dispositivo ya le manda: el push de eventos,
# el heartbeat (health.degraded_reason, power.battery_voltage_v) y la AUSENCIA
# de heartbeats — la única alerta que un equipo muerto jamás podría mandar
# sobre sí mismo. Aquí no hay Twilio, ni IoT Hub, ni números de teléfono, ni
# banco declarado: nada de eso tiene ya qué describir.

# ── Audio ────────────────────────────────────────────────────────────────────
SAMPLE_RATE     = 48000
CHANNELS        = 2
CAPTURE_SECONDS = 5.0
BLOCK_FRAMES    = int(os.environ.get("OCEANKIND_BLOCK_FRAMES", "4800"))   # 0.1 s por bloque
# Detección del dispositivo POR NOMBRE (F-15): substring, case-insensitive.
# Un índice ALSA cambia con la re-enumeración USB; un nombre no.
# Codec Zero es el único HAT del proyecto (D-009). El nombre exacto de tarjeta
# ALSA NO es estable entre versiones de kernel/driver — visto en hardware real
# 2026-10-21 como "IQaudIOCODEC" y luego, tras una actualización, como
# "Zero [RPi Codec Zero]". "codec" es el único substring común a ambos.
AUDIO_DEVICE_NAME = os.environ.get("OCEANKIND_AUDIO_DEVICE_NAME", "codec")
# Fuente de audio: "device" (hardware) o "synthetic:<patrón>" para banco sin
# hidrófono (R-9.4).
AUDIO_SOURCE = os.environ.get("OCEANKIND_AUDIO_SOURCE", "device").strip().lower()

# Patrones sintéticos implementados. ÚNICA definición: `capture.make_source` la
# usa para construir la fuente y `validate_startup_config` para rechazar
# cualquier otra cosa. Con dos listas separadas esto vuelve a divergir.
#
# tone/impulse disparan SIEMPRE (cada ventana de 5 s) y noise/silence NUNCA:
# con una señal sintética constante no hay término medio. "sporadic" es el
# único que dispara el detector real de vez en cuando — silencio de fondo con
# ráfagas de tono a intervalos aleatorios, en vez de una señal inyectada que
# se salta captura/clasificación (ver `tools/inject_event.py`, que hace
# exactamente eso y es la herramienta correcta cuando lo que se quiere
# probar es transporte/backend, no el pipeline entero).
SYNTHETIC_PATTERNS = ("tone", "noise", "impulse", "silence", "sporadic")

# Intervalo medio, en segundos, entre ráfagas del patrón "sporadic". Llegadas
# tipo Poisson (ver capture.SyntheticSource), así que en una corrida larga se
# comporta como una tasa fija sin ser un metrónomo. 720 s = 12 min.
SYNTHETIC_SPORADIC_MEAN_S = float(os.environ.get("OCEANKIND_SYNTHETIC_SPORADIC_MEAN_S", "720"))


def synthetic_pattern(source: str | None = None) -> str:
    """Patrón de "synthetic[:<patrón>]". Sin sufijo → tone."""
    src = AUDIO_SOURCE if source is None else source
    return (src.split(":", 1) + ["tone"])[1].strip() or "tone"

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

# ── Detector ml_mfcc (registro D-014, opcional) ──────────────────────────────
# Umbral del segundo detector. DEVICE-LOCAL por ahora: el documento de config
# firmado no lo conoce y rechaza claves desconocidas — agregarlo es un cambio
# de contrato pendiente de convergencia con el backend (ver docs/TODO.md).
ML_MODEL_PATH = os.environ.get("OCEANKIND_ML_MODEL_PATH",
                               str(Path.home() / "oceankind" / "model.joblib"))
ML_SCORE_MIN  = float(os.environ.get("OCEANKIND_ML_SCORE_MIN", "0.60"))
ML_LABEL      = os.environ.get("OCEANKIND_ML_LABEL", "IMPULSO")

# ── Cadencias y rutas ────────────────────────────────────────────────────────
CONFIG_CHECK_INTERVAL       = float(os.environ.get("OCEANKIND_CONFIG_CHECK_S", "300"))
POWER_HISTORY_HOURS         = 72
POWER_HISTORY_BUCKET_S      = 30 * 60
DUTY_WINDOW_S               = float(os.environ.get("OCEANKIND_DUTY_WINDOW_S", "3600"))

# ── Fail-loud de captura (F-29) ──────────────────────────────────────────────
# Un stream que deja de entregar bloques no lanza excepción: PortAudio
# simplemente deja de llamar al callback. Lo único que lo delata es que no se
# arma ninguna ventana de audio. Pasado CAPTURE_STALL_S sin ventana → degradado
# y aviso. Con hop 5 s, 60 s son ~12 ventanas perdidas: margen amplio contra un
# clasificador lento, estrecho contra una tarde entera de sordera.
CAPTURE_STALL_S             = float(os.environ.get("OCEANKIND_CAPTURE_STALL_S", "60"))
# Duty cycle medido por debajo de esto también es degradación (mismo umbral que
# usa el dashboard para pintar "degradado", así ambos lados cuentan lo mismo).
CAPTURE_DUTY_MIN_PCT        = float(os.environ.get("OCEANKIND_CAPTURE_DUTY_MIN_PCT", "90"))
# Subidas al almacenamiento fallando: tantos heartbeats seguidos con cola > 0
# → degradado y aviso (antes era solo un contador que nadie miraba).
UPLOAD_BACKLOG_ALERT_TICKS  = int(os.environ.get("OCEANKIND_UPLOAD_BACKLOG_ALERT_TICKS", "3"))
# Presupuesto de tiempo por pasada de cada drenado/reintento del hilo
# housekeeping. Ese hilo es el que pingea al watchdog de systemd (WatchdogSec
# 120 s): un drenado de 500 eventos sin tope lo dejaba sin pingear y systemd
# reiniciaba una unidad SANA en plena recuperación.
HOUSEKEEPING_BUDGET_S       = float(os.environ.get("OCEANKIND_HOUSEKEEPING_BUDGET_S", "30"))

# Estado de runtime. HOY es tmpfs (RAM) bajo la protección SD — misma ruta que
# crea la regla tmpfiles de protect_sd.sh. Cuando D-002 decida una partición
# persistente, cambiar SOLO esta variable.
STATE_DIR = Path(os.environ.get("OCEANKIND_STATE_DIR", "/tmp/oceankind"))
# CSV de telemetría: NUNCA en /boot/firmware (regla dura; F-16). Bajo overlay
# se pierde al reiniciar: los huecos resultantes en power_history son señal
# (así se detectan reinicios), no defecto.
DATA_LOG_PATH = Path(os.environ.get("OCEANKIND_DATA_LOG", str(STATE_DIR / "oceankind_data.csv")))
DATA_LOG_MAX_ROWS = int(os.environ.get("OCEANKIND_DATA_LOG_MAX_ROWS", "6000"))

# ── OTA (D-021): lo que update_oceankind.sh escribe y este proceso lee ────────
# ÚNICA definición de rutas compartida con el script: setup.sh instala en
# ~/oceankind y el script define el mismo layout en su cabecera.
INSTALL_DIR           = Path(os.environ.get("OCEANKIND_INSTALL_DIR", str(Path.home() / "oceankind")))
OTA_SCRIPT            = INSTALL_DIR / "update_oceankind.sh"
INSTALLED_SHA_FILE    = INSTALL_DIR / ".installed_sha"
INSTALLED_REF_FILE    = INSTALL_DIR / ".installed_release"
FAILED_SHA_FILE       = INSTALL_DIR / ".ota_failed_sha"
FAILED_REF_FILE       = INSTALL_DIR / ".ota_failed_release"
ASSIGNED_RELEASE_FILE = INSTALL_DIR / ".assigned_release"
OTA_LOCK_DIR          = STATE_DIR / "ota.lock"        # el mismo mkdir-lock del script
OTA_LOG_FILE          = STATE_DIR / "logs" / "update.log"
# Último lanzamiento, persistido en tmpfs: OTA_RETRIGGER_S vale a través de
# reinicios del servicio (el OTA nos reinicia; sin esto, cada arranque era un
# lanzamiento nuevo).
OTA_LAST_TRIGGER_FILE = STATE_DIR / "ota.last_trigger"
# Entre dos lanzamientos del OTA por la misma asignación. El script es
# idempotente y barato sin red, pero un bucle cada 5 min por un tag que no
# existe sería ruido; media hora deja margen a arreglarlo en el panel.
OTA_RETRIGGER_S       = float(os.environ.get("OCEANKIND_OTA_RETRIGGER_S", "1800"))
# Lo que puede ser un nombre de release. Va a la línea de comandos del script:
# esta expresión es lo que hace que eso sea seguro (contrato §Device configuration).
RELEASE_RE = re.compile(r"^v?[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")

ARCHIVE_INTERVAL  = float(os.environ.get("OCEANKIND_ARCHIVE_INTERVAL", "60"))
ARCHIVE_DIR       = Path(os.environ.get("OCEANKIND_ARCHIVE_DIR", str(Path.home() / "oceankind" / "archive_queue")))
# El archivo vive en RAM (overlay): 300 ≈ 290 MB (F-22)
ARCHIVE_MAX_FILES = int(os.environ.get("OCEANKIND_ARCHIVE_MAX_FILES", "300"))

EVENT_SPOOL_DIR = Path(os.environ.get("OCEANKIND_EVENT_SPOOL_DIR", str(STATE_DIR / "event_spool")))
EVENT_SPOOL_MAX = int(os.environ.get("OCEANKIND_EVENT_SPOOL_MAX", "500"))

# ── Push directo al backend del dashboard (contrato §Event upload) ───────────
# Segundo camino por evento, ADEMÁS del blob (jamás en vez de). Vacío = sin
# push: el índice del dashboard se entera solo por su pase de reconciliación.
BACKEND_URL       = os.environ.get("OCEANKIND_BACKEND_URL", "").strip()
# EL secreto del dispositivo (D-020): uno solo, emitido una vez por el panel al
# crear la unidad, pegado en /etc/oceankind.env. De él se derivan DOS claves con
# etiquetas fijas, idénticas a las del backend (core/security.derive_keys):
#   AUTH_KEY   — lo que va en X-Device-Key en cada petición
#   CONFIG_KEY — con lo que se verifica la firma de la config remota
# Así la clave que viaja en cada request no es la que firma configuración, y un
# equipo robado entrega claves que sólo valen para ese equipo. Sin default
# literal (F-04); obligatorio con BACKEND_URL.
DEVICE_SECRET     = os.environ.get("OCEANKIND_DEVICE_SECRET", "").strip()
# La variable anterior a D-020. Si está y el secreto no, la unidad se niega a
# arrancar: hay que re-crear el dispositivo en el panel y pegar el secreto nuevo.
LEGACY_DEVICE_KEY = os.environ.get("OCEANKIND_DEVICE_KEY", "").strip()
_AUTH_LABEL   = b"oceankind:auth:v1"
_CONFIG_LABEL = b"oceankind:config:v1"


def derive_keys(secret: str) -> tuple[str, str]:
    """(auth_key, config_key): HMAC-SHA256 hex de una etiqueta fija bajo el
    secreto. Cambiar esto es cambiar el contrato (§Provisioning secret)."""
    k = secret.encode()
    return (hmac.new(k, _AUTH_LABEL, hashlib.sha256).hexdigest(),
            hmac.new(k, _CONFIG_LABEL, hashlib.sha256).hexdigest())


AUTH_KEY, CONFIG_KEY = derive_keys(DEVICE_SECRET) if DEVICE_SECRET else ("", "")
BACKEND_TIMEOUT_S = float(os.environ.get("OCEANKIND_BACKEND_TIMEOUT_S", "10"))
PUSH_SPOOL_DIR    = Path(os.environ.get("OCEANKIND_PUSH_SPOOL_DIR", str(STATE_DIR / "push_spool")))
PUSH_SPOOL_MAX    = int(os.environ.get("OCEANKIND_PUSH_SPOOL_MAX", "500"))
# Tras un 401 el push se detiene, pero NO para siempre: cada AUTH_RETRY_S se
# vuelve a probar una vez. Un 401 transitorio (backend desplegando con la BD
# caída) dejaba la unidad muda hasta un reinicio que nadie puede provocar.
AUTH_RETRY_S      = float(os.environ.get("OCEANKIND_AUTH_RETRY_S", "3600"))

# Colas acotadas (R-1.3). Dimensionadas para 512 MB: un clip estéreo int16 de
# 5 s son ~960 KB.
BLOCK_QUEUE_MAX     = int(os.environ.get("OCEANKIND_BLOCK_QUEUE_MAX", "200"))   # ~20 s de audio
TRANSPORT_QUEUE_MAX = int(os.environ.get("OCEANKIND_TRANSPORT_QUEUE_MAX", "16"))

VEDIRECT_PORT = os.environ.get("OCEANKIND_VEDIRECT_PORT", "")

# Watchdog (R-2.7): cadencia del ping WATCHDOG=1 y máxima edad tolerada del
# latido de cada hilo vigilado. WatchdogSec de la unidad systemd (120 s) debe
# ser cómodamente mayor que ambos.
WATCHDOG_PING_S     = float(os.environ.get("OCEANKIND_WATCHDOG_PING_S", "20"))
WATCHDOG_BEAT_MAX_S = float(os.environ.get("OCEANKIND_WATCHDOG_BEAT_MAX_S", "60"))

# Umbrales de batería: viven en el backend desde D-020 (los decide a partir de
# power.battery_voltage_v del heartbeat, con el mismo debounce e histéresis).
# Aquí sólo se mide y se publica.


# ── Parámetros afinables en caliente ─────────────────────────────────────────

# Nyquist tras decimación: psd_f_max jamás puede superarlo (contrato: "capped
# at Nyquist after decimation").
_NYQUIST_HZ = SAMPLE_RATE / PSD_DECIMATION / 2.0

# Rangos de seguridad (R-8.3), ALINEADOS con la tabla normativa de
# docs/DATA-CONTRACT.md §Device configuration. El backend clampea antes de
# firmar; el dispositivo re-clampea como defensa en profundidad (mismo rango,
# así operador y unidad ven el mismo número). Elegir el valor dentro del rango
# es ciencia y es del cliente.
CLAMPS = {
    "score_min":            (0.05, 0.95),
    "alert_min_rms":        (0.0, 0.20),
    "alert_threshold":      (0.005, 0.50),
    "cooldown_s":           (10.0, 3600.0),
    "heartbeat_interval_s": (30.0, 3600.0),
    "psd_threshold_db":     (3.0, 30.0),
    "psd_f_min":            (20.0, 2000.0),
    "psd_f_max":            (100.0, min(20000.0, _NYQUIST_HZ)),
    "window_hop_s":         (1.0, 5.0),   # 1.0 = solape máximo (5× CPU). Medir en banco antes de bajar
}

# detection_mode viaja en el mismo objeto config pero NO se clampea: un typo en
# un enum deshabilitaría la detección, así que se RECHAZA el documento entero.
CONFIG_KEYS = set(CLAMPS) | {"detection_mode"}
DETECTION_MODES = ("psd", "rms", "auto")
# release/hold (D-021) viajan en el mismo objeto firmado pero no son umbrales:
# no entran a RuntimeConfig, los consume el disparador OTA (ota.py). Se validan
# aquí (tag con forma de tag, hold booleano) y un valor inválido rechaza el
# documento entero, como cualquier otra clave.
RELEASE_KEYS = {"release", "hold"}


class ConfigRejected(Exception):
    """Documento de config remota rechazado ENTERO (jamás aplicado a medias).
    Es un evento de salud: llega a health.degraded_reason, no solo al log."""


class RuntimeConfig:
    """Parámetros de detección afinables sin reinicio (R-3.6).

    Un solo escritor (el poller de config del hilo housekeeping), muchos
    lectores (clasificación, status). Lock para snapshots coherentes.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.detection_mode       = DETECTION_MODE
        self.score_min            = _INIT_SCORE_MIN
        self.alert_min_rms        = _INIT_ALERT_MIN_RMS
        self.alert_threshold      = _INIT_ALERT_THRESHOLD
        self.cooldown_s           = _INIT_COOLDOWN
        self.heartbeat_interval_s = _INIT_HEARTBEAT
        self.psd_threshold_db     = _INIT_PSD_THRESHOLD_DB
        self.psd_f_min            = _INIT_PSD_F_MIN
        self.psd_f_max            = _INIT_PSD_F_MAX
        self.window_hop_s         = min(5.0, max(1.0, _INIT_WINDOW_HOP))

    def snapshot(self) -> dict:
        with self._lock:
            return {k: getattr(self, k) for k in CONFIG_KEYS}

    def apply(self, values: dict) -> list:
        """Aplica valores ya verificados (verify_remote_config), clampeando
        numéricos. Devuelve descripciones de lo que cambió."""
        changes = []
        with self._lock:
            if "detection_mode" in values and values["detection_mode"] != self.detection_mode:
                changes.append(f"detection_mode: {self.detection_mode} → {values['detection_mode']}")
                self.detection_mode = values["detection_mode"]
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
        return changes


CONFIG = RuntimeConfig()


def verify_remote_config(payload: dict) -> dict | None:
    """Valida un documento de config remota contra el contrato (§Device
    configuration de docs/DATA-CONTRACT.md). Convergido 2026-08-22 con el
    backend, que ESCRIBE este blob.

    Devuelve el objeto `config` a aplicar (umbrales Y release/hold), o None si
    el documento va dirigido a otra unidad (`device_id` distinto). Levanta
    ConfigRejected — evento de salud — ante: secreto no aprovisionado, firma
    inválida, objeto config ausente, claves desconocidas, enum inválido, banda
    PSD invertida, release que no es un tag o hold no booleano. Un documento
    rechazado se descarta ENTERO; jamás se aplica a medias.
    """
    # Sin secreto, el dispositivo NO acepta config remota alguna. Aplicar sin
    # firma "con warning" era el agujero F-10 reabierto.
    if not CONFIG_KEY:
        raise ConfigRejected("OCEANKIND_DEVICE_SECRET no aprovisionado — "
                             "el dispositivo rechaza toda config remota sin firma")

    # Firma: HMAC-SHA256 sobre el DOCUMENTO ENTERO menos `signature`, JSON
    # canónico (claves ordenadas, sin espacios), con la clave DE ESTE equipo
    # (D-020). Idéntico byte a byte al canonicalizador del backend — dos lados
    # hasheando objetos distintos es exactamente la falla que la tabla de
    # convergencia documenta.
    body = {k: v for k, v in payload.items() if k != "signature"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    expected = hmac.new(CONFIG_KEY.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(payload.get("signature") or "")):
        raise ConfigRejected(f"firma HMAC inválida (config_version {payload.get('config_version')!r})")

    # Documento firmado y válido dirigido a otra unidad del sitio: no es error.
    target = payload.get("device_id")
    if target not in (None, "", DEVICE_ID):
        log.info("config remota %r dirigida a %r — ignorada por %s",
                 payload.get("config_version"), target, DEVICE_ID)
        return None

    values = payload.get("config")
    if not isinstance(values, dict):
        raise ConfigRejected("documento sin objeto 'config'")

    # Clave desconocida = error, no omisión: un typo que afina nada en
    # silencio es la falla silenciosa que este sistema existe para eliminar.
    unknown = sorted(set(values) - CONFIG_KEYS - RELEASE_KEYS)
    if unknown:
        raise ConfigRejected(f"claves desconocidas en config: {unknown}")

    if "detection_mode" in values and values["detection_mode"] not in DETECTION_MODES:
        raise ConfigRejected(f"detection_mode inválido: {values['detection_mode']!r} "
                             f"(un typo aquí deshabilita la detección)")

    # D-021: el nombre de release acaba en la línea de comandos del OTA. Sólo
    # pasa lo que tiene forma de tag; `hold` sólo si es booleano de verdad.
    rel = values.get("release")
    if rel is not None and not (isinstance(rel, str) and RELEASE_RE.match(rel)):
        raise ConfigRejected(f"release inválida: {rel!r} (debe ser un tag git, p.ej. v2.2.0)")
    if "hold" in values and not isinstance(values["hold"], bool):
        raise ConfigRejected(f"hold inválido: {values['hold']!r} (debe ser true/false)")

    # Banda PSD invertida se rechaza (contrato), evaluada sobre el resultado
    # de fusionar el documento con los valores vigentes.
    current = CONFIG.snapshot()
    f_min = float(values.get("psd_f_min", current["psd_f_min"]))
    f_max = float(values.get("psd_f_max", current["psd_f_max"]))
    if f_min >= f_max:
        raise ConfigRejected(f"banda PSD invertida: psd_f_min={f_min} >= psd_f_max={f_max}")

    return values


def validate_startup_config() -> None:
    """Config inválida = NO ARRANCA (R-8.1). systemd reinicia en loop, a gritos."""
    problems = []
    if DETECTION_MODE not in ("psd", "rms", "auto"):
        problems.append(f"OCEANKIND_DETECTION_MODE={DETECTION_MODE!r} inválido (psd|rms|auto)")
    if AUDIO_SOURCE != "device" and not AUDIO_SOURCE.startswith("synthetic"):
        problems.append(f"OCEANKIND_AUDIO_SOURCE={AUDIO_SOURCE!r} inválido (device|synthetic:<patrón>)")
    elif AUDIO_SOURCE.startswith("synthetic"):
        # El patrón se valida, no solo el prefijo. `synthetic:tonee` pasaba el
        # prefijo y caía en el `else` de capture._block: silencio puro. Una
        # soak de 24 h con duty cycle perfecto, cero detecciones y ningún
        # error — sordo-silencioso en el camino que valida todo lo demás.
        pat = synthetic_pattern()
        if pat not in SYNTHETIC_PATTERNS:
            problems.append(
                f"OCEANKIND_AUDIO_SOURCE={AUDIO_SOURCE!r}: patrón {pat!r} no existe. "
                f"Válidos: {'|'.join(SYNTHETIC_PATTERNS)}. Un patrón desconocido "
                "generaría silencio y el banco no detectaría nada sin avisar.")
    if LEGACY_DEVICE_KEY and not DEVICE_SECRET:
        # La unidad fue aprovisionada antes de D-020. Su clave vieja seguiría
        # autenticando, pero nunca podría verificar una config ni una release:
        # exactamente la unidad a medias que el panel no puede gobernar.
        problems.append(
            "OCEANKIND_DEVICE_KEY es la variable anterior a D-020 y ya no se usa. "
            "Re-crear el dispositivo en Admin → Dispositivos, pegar el secreto nuevo como "
            "OCEANKIND_DEVICE_SECRET en /etc/oceankind.env y borrar OCEANKIND_DEVICE_KEY.")
    if BACKEND_URL and not DEVICE_SECRET:
        problems.append("OCEANKIND_BACKEND_URL definida pero falta OCEANKIND_DEVICE_SECRET — "
                        "el secreto por dispositivo lo emite el panel al crear la unidad.")
    if BACKEND_URL and not BACKEND_URL.startswith(("http://", "https://")):
        problems.append(f"OCEANKIND_BACKEND_URL={BACKEND_URL!r} debe empezar con http:// o https://")
    # SITE lo exige CUALQUIERA de los dos destinos, no solo el almacenamiento.
    # El backend rechaza con 403 todo evento cuyo `site` no coincida con el del
    # dispositivo registrado, así que en una unidad push-only sin SITE arrancaba
    # limpia y fallaba TODOS los push. Un aprovisionamiento incompleto no debe
    # arrancar (R-8.1) en vez de descubrirse evento a evento.
    if STORAGE_ENABLED or BACKEND_URL:
        if not SITE:
            problems.append("OCEANKIND_SITE falta. En el contrato v2 todo vive bajo "
                            "sites/{site}/, y el backend rechaza (403) los eventos "
                            "cuyo site no coincide — definir un id (p.ej. 'punta_norte').")
        elif not SITE_ID_RE.match(SITE):
            problems.append(f"OCEANKIND_SITE={SITE!r} inválido: solo [a-z0-9_-]")
    if STORAGE_ENABLED:
        if SENSOR_LAT is None or SENSOR_LON is None:
            problems.append("OCEANKIND_SENSOR_LAT / OCEANKIND_SENSOR_LON faltan — "
                            "el registro de sitios (_sites.json) los requiere.")
    if problems:
        for p in problems:
            log.critical("CONFIG INVÁLIDA: %s", p)
        log.critical("El servicio se niega a arrancar con configuración inválida (R-8.1).")
        sys.exit(1)
    if not BACKEND_URL:
        # Sin backend nadie recibe heartbeats, y desde D-020 el backend es el
        # único que avisa a un humano: esta unidad no puede alertar a nadie.
        log.warning("⚠️  Sin OCEANKIND_BACKEND_URL — ningún humano recibirá alertas de esta "
                    "unidad (D-020: avisa el backend). Sólo aceptable en banco de pruebas.")
