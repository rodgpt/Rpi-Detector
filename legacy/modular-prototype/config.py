"""
OceanKind - Bomb Fishing Detector
Configuration file — edit this for each deployment unit.
"""
import os


class Config:
    # ─── Audio ───────────────────────────────────────────────────────────────
    SAMPLE_RATE = 44100         # Hz — sufficient for blast detection (blasts are broadband < 5kHz)
    CHANNELS = 2                # 1 or 2 hydrophones
    BLOCK_SIZE = 1024           # Samples per processing block (~23ms at 44.1kHz)

    # ─── STA/LTA Detector ────────────────────────────────────────────────────
    # STA (Short-Term Average) captures the current energy spike.
    # LTA (Long-Term Average) captures the background noise floor.
    # When STA/LTA ratio > TRIGGER_RATIO, a blast is detected.
    STA_WINDOW = 0.05           # seconds (50ms) — short enough to catch a sharp impulse
    LTA_WINDOW = 5.0            # seconds (5s)  — long enough to represent ambient noise
    TRIGGER_RATIO = 4.0         # STA/LTA ratio to open a detection event
    DETRIGGER_RATIO = 1.5       # STA/LTA ratio to close the event
    MIN_EVENT_DURATION = 0.01   # seconds — ignore < 10ms spikes (electrical transients)
    MAX_EVENT_DURATION = 2.0    # seconds — ignore sustained sounds (boat engines, etc.)

    # ─── Frequency Analysis ──────────────────────────────────────────────────
    # Explosions are broadband (energy spread across many frequencies).
    # Boat engines, rain etc. are narrow-band or tonal.
    MIN_FREQ = 20               # Hz
    MAX_FREQ = 5000             # Hz — most bomb energy is in 20Hz–5kHz range
    BROADBAND_THRESHOLD = 0.5   # Fraction of frequency bins elevated above median

    # ─── Alert Settings ──────────────────────────────────────────────────────
    # Options: "mqtt", "http", "iothub", or "both" (mqtt+http)
    # Use "iothub" to send telemetry directly to Microsoft Azure IoT Hub
    # (recommended for Project 15 / production deployments)
    ALERT_METHOD = os.getenv("OCEANKIND_ALERT_METHOD", "mqtt")

    # MQTT (local broker via Mosquitto — relays to server)
    MQTT_BROKER = "localhost"
    MQTT_PORT = 1883
    MQTT_TOPIC = "oceankind/detections"

    # HTTP (direct POST to your server)
    HTTP_ENDPOINT = os.getenv("OCEANKIND_HTTP_ENDPOINT", "http://your-server/api/detections")
    HTTP_TOKEN = os.getenv("OCEANKIND_API_TOKEN", "")

    # Azure IoT Hub — Project 15 integration
    # Get this connection string from Azure Portal → IoT Hub → Devices → your device
    # Format: HostName=<hub>.azure-devices.net;DeviceId=<id>;SharedAccessKey=<key>
    IOTHUB_CONNECTION_STRING = os.getenv("OCEANKIND_IOTHUB_CONNECTION_STRING", "")

    # ─── Device Identity & Location ──────────────────────────────────────────
    # Update these for each physical deployment unit
    DEVICE_ID = os.getenv("OCEANKIND_DEVICE_ID", "unit_01")
    LATITUDE = float(os.getenv("OCEANKIND_LAT", "-12.046374"))   # Lima as default
    LONGITUDE = float(os.getenv("OCEANKIND_LON", "-77.042793"))

    # ─── Storage ─────────────────────────────────────────────────────────────
    LOG_DIR = os.path.expanduser("~/oceankind/logs")
    SAVE_AUDIO_CLIPS = True
    AUDIO_CLIPS_DIR = os.path.expanduser("~/oceankind/clips")
    PRE_TRIGGER_SECONDS = 1.0   # Audio saved before the trigger point
    POST_TRIGGER_SECONDS = 3.0  # Audio saved after the trigger point
