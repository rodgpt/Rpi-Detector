#!/usr/bin/env python3
"""
OceanKind — Audio real a Azure IoT Hub con detección por umbral + Blob Storage
Graba continuamente; cuando el nivel supera ALERT_THRESHOLD guarda el clip WAV,
lo sube a Azure Blob Storage, actualiza manifest.json y envía el link al IoT Hub.
Lee remote_config.json cada CONFIG_CHECK_INTERVAL segundos para aplicar cambios
de configuración enviados desde el dashboard.

Uso:
    export OCEANKIND_IOTHUB_CONNECTION_STRING="HostName=..."
    export OCEANKIND_STORAGE_CONNECTION_STRING="DefaultEndpointsProtocol=..."
    python3 marfutura_iot_audio.py

Dependencias:
    pip3 install --break-system-packages azure-iot-device azure-storage-blob numpy
"""

import json
import logging
import os
import subprocess
import sys
import time
import wave
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np

# --- Configuración -----------------------------------------------------------

IOTHUB_CONNECTION_STRING  = os.environ.get("OCEANKIND_IOTHUB_CONNECTION_STRING", "")
STORAGE_CONNECTION_STRING = os.environ.get("OCEANKIND_STORAGE_CONNECTION_STRING", "")
STORAGE_CONTAINER         = "alerts"
STORAGE_ACCOUNT_NAME      = "marfuturatest"
DASHBOARD_URL             = "https://marfuturatest.z6.web.core.windows.net/index.html"
SOFTWARE_VERSION          = "1.1.0"
DEVICE_ID                 = os.environ.get("OCEANKIND_DEVICE_ID", "Rpi_casa")

# URL del web admin del modem ZTE (LAN, sin login). Si la API responde, se reporta señal.
MODEM_API_URL             = os.environ.get("OCEANKIND_MODEM_API", "http://192.168.0.1/goform/goform_get_cmd_process")

# Ubicación del sensor (coordenadas fijas)
SENSOR_LAT           = -33.986582
SENSOR_LON           = -71.860006
SENSOR_LOCATION_NAME = "Lagunillas, Navidad"

# WhatsApp — Twilio
TWILIO_ACCOUNT_SID   = os.environ.get("OCEANKIND_TWILIO_SID",   "")
TWILIO_AUTH_TOKEN    = os.environ.get("OCEANKIND_TWILIO_TOKEN", "")
# Número de PRODUCCIÓN de WhatsApp Business (ya NO el sandbox).
# Envía SOLO con plantillas aprobadas (Content SID). Sin ventana de 24h ni "join tears-rising".
TWILIO_FROM          = os.environ.get("OCEANKIND_TWILIO_FROM", "whatsapp:+56926280872")
# Soporta lista separada por comas en OCEANKIND_TWILIO_TO para múltiples destinatarios.
# Cada alerta se envía a TODOS los números.
_TWILIO_TO_RAW       = os.environ.get("OCEANKIND_TWILIO_TO", "whatsapp:+56961987942")
TWILIO_TO_LIST       = [n.strip() for n in _TWILIO_TO_RAW.split(",") if n.strip()]
TWILIO_TO            = TWILIO_TO_LIST[0] if TWILIO_TO_LIST else ""  # compat con checks legacy

# Content SIDs de las plantillas de WhatsApp aprobadas (Twilio Content Template Builder).
WA_TPL_ALERT     = os.environ.get("OCEANKIND_WA_TPL_ALERT",     "HX748d718e6995035b7d8584ec85a1ee07")  # alerta_deteccion_v2
WA_TPL_HEARTBEAT = os.environ.get("OCEANKIND_WA_TPL_HEARTBEAT", "HX0cb8c93d1f76d76a4ce701e312b4a258")  # heartbeat_sistema_v2
WA_TPL_BATTERY   = os.environ.get("OCEANKIND_WA_TPL_BATTERY",   "HXcfa0b69e86ea1566f3a06e434f175a80")  # bateria_baja_v2

# Dispositivo de audio
AUDIO_DEVICE     = "plughw:3,0"
SAMPLE_RATE      = 48000
CHANNELS         = 2
CAPTURE_SECONDS  = 5.0   # 5s para matchear el set de entrenamiento del modelo sklearn

# Detector — ahora basado en clasificador ML (sklearn) en vez de RMS puro.
# El RMS se sigue reportando para monitoreo pero NO decide la alerta.
ALERT_THRESHOLD       = 0.08    # legacy, solo se usa si DETECTION_MODE="rms" (fallback)
DETECTION_MODE        = os.environ.get("OCEANKIND_DETECTION_MODE", "ml").lower()  # "ml" | "rms" | "auto"
ML_MODEL_PATH         = Path(os.environ.get("OCEANKIND_ML_MODEL", str(Path.home() / "oceankind" / "model.joblib")))
ML_THRESHOLD          = float(os.environ.get("OCEANKIND_ML_THRESHOLD", "0.5"))  # proba mínima para alertar
ALERT_MIN_RMS         = float(os.environ.get("OCEANKIND_ALERT_MIN_RMS", "0.02"))  # RMS mínimo para alertar (filtra falsos positivos de ruido)
ML_POSITIVE_LABEL     = os.environ.get("OCEANKIND_ML_POSITIVE_LABEL", "FILTRO")  # label para WhatsApp

ALERT_COOLDOWN        = 600.0   # 10 min entre alertas — para test con filtro de piscina (no saturar cuota Twilio sandbox)
HEARTBEAT_INTERVAL    = 60.0
MANIFEST_MAX_ALERTS   = 5000

# Cada cuántos segundos se chequea remote_config.json
CONFIG_CHECK_INTERVAL = 300.0   # 5 minutos

# Heartbeat WhatsApp — ping de vida con estadísticas del sistema
WHATSAPP_HEARTBEAT_INTERVAL = 43200.0  # 12 horas

# Carpeta local para clips temporales y alertas pendientes
CLIPS_DIR              = Path.home() / "oceankind" / "clips"
PENDING_ALERTS_FILE    = Path.home() / "oceankind" / "pending_alerts.json"

# --- Logging -----------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("oceankind")


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
    except Exception:
        stats["ram_used_pct"] = None
    return stats


def level_bar(rms: float, threshold: float, width: int = 40) -> str:
    filled = int(min(rms / 0.3, 1.0) * width)
    bar = list("█" * filled + "░" * (width - filled))
    threshold_pos = int(min(threshold / 0.3, 1.0) * width)
    if threshold_pos < width:
        bar[threshold_pos] = "|"
    return "".join(bar)


# --- Captura de audio --------------------------------------------------------

def capture_audio(output_path: str) -> tuple[float, float]:
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
            return 0.0, float("-inf")

        with wave.open(output_path, "rb") as wf:
            raw = wf.readframes(wf.getnframes())

        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        if samples.size == 0:
            return 0.0, -180.0   # piso finito (no -inf, que rompe JSON)

        rms      = float(np.sqrt(np.mean(samples ** 2)))
        rms_norm = rms / 32768.0
        peak_db  = float(20 * np.log10(rms_norm + 1e-9))
        # Clamp para evitar -inf en JSON (Python json.dumps los serializa como
        # "Infinity" literal, no permitido por la spec → JS no puede parsear)
        if not np.isfinite(peak_db):
            peak_db = -180.0
        return rms_norm, peak_db

    except subprocess.TimeoutExpired:
        log.warning("arecord timeout")
        time.sleep(1.0)
        return 0.0, float("-inf")
    except Exception as exc:
        log.warning("Error capturando audio: %s", exc)
        return 0.0, float("-inf")


# --- Azure Blob Storage ------------------------------------------------------

def _blob_url(blob_name: str) -> str:
    return f"https://{STORAGE_ACCOUNT_NAME}.blob.core.windows.net/{STORAGE_CONTAINER}/{blob_name}"


def _get_blob_client(blob_name: str):
    from azure.storage.blob import BlobClient  # noqa: PLC0415
    return BlobClient.from_connection_string(
        STORAGE_CONNECTION_STRING,
        container_name=STORAGE_CONTAINER,
        blob_name=blob_name,
    )


def upload_clip(local_path: str, blob_name: str) -> str | None:
    try:
        from azure.storage.blob import ContentSettings  # noqa: PLC0415
        blob = _get_blob_client(blob_name)
        with open(local_path, "rb") as f:
            blob.upload_blob(f, overwrite=True,
                             content_settings=ContentSettings(content_type="audio/wav"))
        return _blob_url(blob_name)
    except Exception as exc:
        log.warning("Error subiendo clip: %s", exc)
        return None


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


def upload_json(blob_name: str, data: dict) -> None:
    try:
        from azure.storage.blob import ContentSettings  # noqa: PLC0415
        blob = _get_blob_client(blob_name)
        blob.upload_blob(
            json.dumps(_sanitize_for_json(data), indent=2, allow_nan=False).encode(),
            overwrite=True,
            content_settings=ContentSettings(content_type="application/json"),
        )
    except Exception as exc:
        log.warning("Error subiendo %s: %s", blob_name, exc)


def download_json(blob_name: str) -> dict | None:
    try:
        blob = _get_blob_client(blob_name)
        data = blob.download_blob().readall()
        return json.loads(data)
    except Exception:
        return None


def update_manifest(alert_entry: dict, current_manifest: dict | None = None) -> dict:
    if current_manifest is None:
        current_manifest = download_json("manifest.json") or {"alerts": []}
    current_manifest["alerts"].insert(0, alert_entry)
    current_manifest["alerts"] = current_manifest["alerts"][:MANIFEST_MAX_ALERTS]
    current_manifest["updated"] = datetime.now(timezone.utc).isoformat()
    upload_json("manifest.json", current_manifest)
    log.info("  → manifest.json actualizado (%d alertas)", len(current_manifest["alerts"]))
    return current_manifest


# ─── Clasificador ML (scikit-learn + librosa) ────────────────────────────────
# Reemplaza el detector basado en RMS con un clasificador entrenado.
# El modelo (model.joblib) es un bundle con keys: model, sr, n_mfcc.
# Features = MFCC (media+std) + centroide/bw/rolloff/flatness/zcr/rms (media+std).

_ml_bundle: dict | None = None     # cache del bundle cargado
_ml_load_attempted = False         # para no spam-ear logs si falla


def _load_ml_model() -> dict | None:
    """Carga model.joblib una sola vez. Devuelve None si no está disponible."""
    global _ml_bundle, _ml_load_attempted
    if _ml_load_attempted:
        return _ml_bundle
    _ml_load_attempted = True
    try:
        import joblib  # noqa: PLC0415
        bundle = joblib.load(ML_MODEL_PATH)
        # Sanity check del bundle
        for key in ("model", "sr", "n_mfcc"):
            if key not in bundle:
                raise ValueError(f"bundle inválido, falta key '{key}'")
        _ml_bundle = bundle
        log.info("✓ Modelo ML cargado: %s (sr=%d Hz, n_mfcc=%d)",
                 ML_MODEL_PATH.name, bundle["sr"], bundle["n_mfcc"])
    except Exception as exc:
        log.warning("No se pudo cargar modelo ML (%s): %s", ML_MODEL_PATH, exc)
        _ml_bundle = None
    return _ml_bundle


def _extract_features(audio: np.ndarray, sr: int, n_mfcc: int) -> np.ndarray:
    """Extrae features compatibles con predict_sklearn.py (52 features)."""
    import librosa  # noqa: PLC0415
    mfcc    = librosa.feature.mfcc(y=audio, sr=sr, n_mfcc=n_mfcc)
    cent    = librosa.feature.spectral_centroid(y=audio, sr=sr)[0]
    bw      = librosa.feature.spectral_bandwidth(y=audio, sr=sr)[0]
    rolloff = librosa.feature.spectral_rolloff(y=audio, sr=sr)[0]
    flat    = librosa.feature.spectral_flatness(y=audio)[0]
    zcr     = librosa.feature.zero_crossing_rate(audio)[0]
    rms_ar  = librosa.feature.rms(y=audio)[0]
    extras  = np.array([
        cent.mean(),    cent.std(),
        bw.mean(),      bw.std(),
        rolloff.mean(), rolloff.std(),
        flat.mean(),    flat.std(),
        zcr.mean(),     zcr.std(),
        rms_ar.mean(),  rms_ar.std(),
    ])
    return np.concatenate([mfcc.mean(axis=1), mfcc.std(axis=1), extras])


def classify_clip(wav_path: str) -> dict:
    """
    Clasifica un .wav. Devuelve dict con:
       pred  : int   — 0 (background) | 1 (positivo)
       proba : float — probabilidad de la clase positiva
       label : str   — "FILTRO" o "background"
    Si el modelo no está disponible o falla, devuelve {}.
    """
    bundle = _load_ml_model()
    if not bundle:
        return {}
    try:
        import librosa  # noqa: PLC0415
        sr     = bundle["sr"]
        n_mfcc = bundle["n_mfcc"]
        # librosa.load resamplea al sr del modelo (no asumimos que arecord lo capturó así)
        audio, _ = librosa.load(wav_path, sr=sr, mono=True)
        if len(audio) < int(0.5 * sr):
            # clip muy corto: padding con ceros
            audio = np.pad(audio, (0, int(0.5 * sr) - len(audio)))
        feats = _extract_features(audio, sr, n_mfcc).reshape(1, -1)
        pred  = int(bundle["model"].predict(feats)[0])
        proba = float(bundle["model"].predict_proba(feats)[0, 1])
        return {
            "pred":  pred,
            "proba": round(proba, 4),
            "label": ML_POSITIVE_LABEL if pred == 1 else "background",
        }
    except Exception as exc:
        log.warning("classify_clip falló: %s", exc)
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
        new_file = not DATA_LOG_PATH.exists()
        DATA_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        import csv  # noqa: PLC0415
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
        upload_json("power_history.json", {"history": history, "bucket_s": POWER_HISTORY_BUCKET_S})
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
        _wa_send(WA_TPL_BATTERY, {
            "1": f"{voltage:.2f}",
            "2": label,
            "3": action,
        }, log_ok=f"🔋 alerta batería {level} (V={voltage:.2f}) → WhatsApp enviado")
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


def upload_status(session_start: datetime, alert_count: int,
                  threshold: float, last_rms: float) -> None:
    """Sube status.json para que el dashboard muestre el estado del dispositivo."""
    now = datetime.now(timezone.utc)
    uptime_s = int((now - session_start).total_seconds())
    modem = fetch_modem_signal()
    solar = fetch_ve_direct()
    stats = get_system_stats()
    # Alerta batería si cruza umbral peligroso (no-op si no hay Victron)
    check_battery_alert(solar)
    status = {
        "device":            DEVICE_ID,
        "software_version":  SOFTWARE_VERSION,
        "status":            "online",
        "last_seen":         now.isoformat(),
        "session_start":     session_start.isoformat(),
        "uptime_seconds":    uptime_s,
        "alert_count_session": alert_count,
        "current_threshold": round(threshold, 4),
        "current_cooldown":  ALERT_COOLDOWN,
        "last_rms":          round(last_rms, 4),
        "audio_device":      AUDIO_DEVICE,
        "sample_rate":       SAMPLE_RATE,
        # Energía solar — Victron BlueSolar MPPT por VE.Direct (USB)
        "battery_voltage_v": solar.get("battery_voltage_v"),
        "battery_current_a": solar.get("battery_current_a"),
        "panel_voltage_v":   solar.get("panel_voltage_v"),
        "panel_power_w":     solar.get("panel_power_w"),
        "charge_state":      solar.get("charge_state"),
        "charge_state_id":   solar.get("charge_state_id"),
        "yield_today_kwh":   solar.get("yield_today_kwh"),
        "yield_total_kwh":   solar.get("yield_total_kwh"),
        "max_power_today_w": solar.get("max_power_today_w"),
        "solar_error_code":  solar.get("error_code"),
        "solar_device":      solar.get("device_label"),
        # Consumo instantáneo del sistema (derivado: panel − carga a batería)
        # battery_current_a > 0 = cargando → load = PPV − V·I
        # battery_current_a < 0 = descargando → load = V·|I| (panel no contribuye)
        # Aproximación: ignora pérdidas MPPT (~5%) y conversión DC-DC.
        "system_load_w": _compute_system_load(solar),
        # Placeholders legacy (compat con dashboard viejo)
        "battery_voltage":   solar.get("battery_voltage_v"),
        "battery_percent":   None,
        "solar_charging":    (solar.get("panel_power_w") or 0) > 0 if solar else None,
        # Señal del modem 4G (vacíos si el modem no responde)
        "signal_bars":       modem.get("signal_bars"),
        "signal_rssi":       modem.get("signal_rssi"),
        "network_type":      modem.get("network_type"),
        "modem_state":       modem.get("modem_state"),
        # Sistema (CPU temp, disco SD, RAM, uptime del sistema)
        "cpu_temp_c":        stats.get("cpu_temp_c"),
        "system_uptime_s":   stats.get("system_uptime_s"),
        "disk_used_pct":     stats.get("disk_used_pct"),
        "disk_free_gb":      stats.get("disk_free_gb"),
        "disk_total_gb":     stats.get("disk_total_gb"),
        "ram_used_pct":      stats.get("ram_used_pct"),
        # Ubicación
        "lat":               SENSOR_LAT,
        "lon":               SENSOR_LON,
        "location_name":     SENSOR_LOCATION_NAME,
    }
    upload_json("status.json", status)
    log.info("  → status.json actualizado (uptime %ds, %d alertas, señal %s/%s)",
             uptime_s, alert_count,
             status["signal_bars"] if status["signal_bars"] is not None else "?",
             status["network_type"] or "?")
    # Histórico local en CSV para análisis fuera del dashboard
    append_data_log(status)


def check_remote_config() -> dict | None:
    """Lee remote_config.json y devuelve el dict si hay cambios que aplicar."""
    cfg = download_json("remote_config.json")
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

def _wa_send(content_sid: str, variables: dict, log_ok: str = "") -> None:
    """Envía un WhatsApp usando una plantilla aprobada (Content SID) desde el número de producción.

    variables: dict con keys "1","2",... que llenan {{1}},{{2}},... de la plantilla.
    No usa texto libre, así que funciona como mensaje business-initiated sin ventana de 24h.
    Envía a TODOS los destinatarios de TWILIO_TO_LIST.
    """
    from twilio.rest import Client  # noqa: PLC0415
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    variables_json = json.dumps(variables, ensure_ascii=False)
    sent = 0
    last_err = None
    for to_number in TWILIO_TO_LIST:
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
        log.info("%s (enviado a %d/%d)", log_ok, sent, len(TWILIO_TO_LIST))
    if sent == 0 and last_err:
        raise last_err


def _save_pending_alert(rms: float, peak_db: float,
                        blob_name: str | None, ts: str) -> None:
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
            _wa_send(WA_TPL_ALERT, {
                "1": ML_POSITIVE_LABEL,
                "2": f"{alert.get('timestamp','')} UTC",
                "3": f"{alert['rms']:.4f}",
                "4": "n/d",
                "5": audio_url,
            })
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
        from twilio.rest import Client  # noqa: PLC0415
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

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
        lines.append(
            f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )

        _wa_send(WA_TPL_HEARTBEAT, {
            "1": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "2": (f"{solar['battery_voltage_v']}" if solar.get("battery_voltage_v") is not None else "n/d"),
            "3": (f"{solar['panel_power_w']}" if solar.get("panel_power_w") is not None else "n/d"),
            "4": (f"{int(modem['signal_bars'])}" if modem.get("signal_bars") is not None else "n/d"),
            "5": session_up,
        })
        log.info("  → Heartbeat WhatsApp enviado")
    except Exception as exc:
        log.warning("Error enviando heartbeat WhatsApp: %s", exc)


def send_whatsapp(rms: float, peak_db: float,
                  blob_name: str | None = None,
                  ml_result: dict | None = None) -> None:
    """Envía alerta por WhatsApp via Twilio sandbox.

    blob_name: nombre del archivo WAV en el contenedor (p.ej. 'alert_2026-05-04T15-30-00.wav').
    ml_result: dict opcional con pred/proba/label del clasificador (si fue una alerta ML).
    """
    if not TWILIO_TO or "XXXXXXXXX" in TWILIO_TO:
        log.warning("WhatsApp: configura TWILIO_TO con tu número real.")
        return
    try:
        from twilio.rest import Client  # noqa: PLC0415
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        label = (ml_result.get("label", ML_POSITIVE_LABEL) if ml_result else ML_POSITIVE_LABEL)
        conf  = (f"{ml_result.get('proba', 0)*100:.1f}%" if ml_result else "n/d")
        audio_url = (f"{DASHBOARD_URL}?play={blob_name}" if blob_name else DASHBOARD_URL)
        _wa_send(WA_TPL_ALERT, {
            "1": label,
            "2": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
            "3": f"{rms:.4f}",
            "4": conf,
            "5": audio_url,
        })
        log.info("  → WhatsApp enviado a %s", TWILIO_TO)
    except ImportError:
        log.warning("twilio no instalado — corre: pip3 install twilio --break-system-packages")
    except Exception as exc:
        log.warning("Error WhatsApp: %s — guardando en buffer local", exc)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        _save_pending_alert(rms, peak_db, blob_name, ts)


# --- Loop principal ----------------------------------------------------------

def main() -> None:
    global ALERT_THRESHOLD, ALERT_COOLDOWN, HEARTBEAT_INTERVAL

    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    session_start   = datetime.now(timezone.utc)
    alert_count     = 0
    last_rms        = 0.0
    applied_config_version = None

    log.info("=== OceanKind %s — %s ===", SOFTWARE_VERSION, DEVICE_ID)
    log.info("Detección  : %s  |  Cooldown: %.0f s  |  Heartbeat IoT: %.0f s",
             DETECTION_MODE.upper(), ALERT_COOLDOWN, HEARTBEAT_INTERVAL)
    if DETECTION_MODE in ("ml", "auto"):
        log.info("Modelo ML  : %s  |  Umbral proba: %.2f  |  Label positivo: %s",
                 ML_MODEL_PATH, ML_THRESHOLD, ML_POSITIVE_LABEL)
        _load_ml_model()  # eager-load para reportar problemas al arranque
    if DETECTION_MODE in ("rms", "auto"):
        log.info("Umbral RMS (fallback): %.3f", ALERT_THRESHOLD)
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
            clip_path = str(CLIPS_DIR / f"clip_{ts}.wav")
            rms, peak_db = capture_audio(clip_path)
            last_rms = rms

            # --- Clasificación ---
            # DETECTION_MODE controla cómo decidimos si hay alerta:
            #   "ml"   → solo modelo ML (default ahora — el RMS es informativo)
            #   "rms"  → modo legacy (solo RMS > ALERT_THRESHOLD)
            #   "auto" → ML si está disponible, si no fallback a RMS
            ml_result = {}
            if DETECTION_MODE in ("ml", "auto") and Path(clip_path).exists():
                ml_result = classify_clip(clip_path)

            # Decisión: doble gate. Necesita RMS >= ALERT_MIN_RMS Y confianza ML >= ML_THRESHOLD.
            # Sin el segundo gate llegan al WhatsApp ruidos no-filtro fuertes (lluvia, motores cercanos).
            proba = 0.0
            if ml_result:
                proba = ml_result.get("proba", 0.0)
            alert = rms >= ALERT_MIN_RMS and proba >= ML_THRESHOLD
            decided_by = "rms+ml"

            bar  = level_bar(rms, ALERT_THRESHOLD)
            tag  = ""
            if ml_result:
                tag = f"  ml={ml_result.get('label','?')}({ml_result.get('proba',0):.2f})"
            flag = " *** ALERTA ***" if alert else ""
            log.info("[%s] RMS=%.4f  %.1f dB%s%s", bar, rms, peak_db, tag, flag)

            # --- Alerta ---
            if alert and (now - last_alert_time) >= ALERT_COOLDOWN:
                audio_url = None
                blob_name = f"alert_{ts}.wav"

                # WhatsApp inmediato — enviamos links al dashboard y espectrograma
                # aunque el upload aún no haya terminado (el link tardará ~1 min en activarse)
                send_whatsapp(rms, peak_db, blob_name, ml_result=ml_result)
                last_alert_time = now

                if STORAGE_CONNECTION_STRING and Path(clip_path).exists():
                    log.info("  Subiendo clip...")
                    audio_url = upload_clip(clip_path, blob_name)
                    if audio_url:
                        log.info("  → %s", audio_url)
                        manifest_entry = {
                            "timestamp":   datetime.now(timezone.utc).isoformat(),
                            "audio_level": round(rms, 4),
                            "peak_db":     round(peak_db, 1),
                            "audio_url":   audio_url,
                            "device":      DEVICE_ID,
                            "decided_by":  decided_by,
                        }
                        if ml_result:
                            manifest_entry["model_label"] = ml_result.get("label")
                            manifest_entry["model_proba"] = ml_result.get("proba")
                            manifest_entry["model_pred"]  = ml_result.get("pred")
                        update_manifest(manifest_entry)
                        alert_count += 1

                if client:
                    try:
                        send_message(client, rms, peak_db, msg_type="alert",
                                     audio_url=audio_url, threshold=ALERT_THRESHOLD)
                        log.info("  → Alerta enviada a IoT Hub")
                    except Exception as exc:
                        log.warning("  Error enviando alerta: %s", exc)

            elif Path(clip_path).exists() and not alert:
                try:
                    Path(clip_path).unlink()
                except OSError:
                    pass

            # --- Heartbeat IoT Hub (frecuente) ---
            if HEARTBEAT_INTERVAL > 0 and (now - last_heartbeat_time) >= HEARTBEAT_INTERVAL:
                if client:
                    try:
                        send_message(client, rms, peak_db, msg_type="heartbeat",
                                     threshold=ALERT_THRESHOLD)
                        log.info("  → Heartbeat enviado a IoT Hub")
                    except Exception as exc:
                        log.warning("  Error en heartbeat IoT Hub: %s", exc)
                if STORAGE_CONNECTION_STRING:
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

    except KeyboardInterrupt:
        log.info("\nDetenido por usuario.")
    finally:
        if client:
            client.disconnect()
            log.info("Desconectado de IoT Hub.")


if __name__ == "__main__":
    main()
