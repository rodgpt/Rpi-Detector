"""
OceanKind - Alert Sender

Sends blast detection alerts via three transports (choose one or combine):

  mqtt     — paho-mqtt → local Mosquitto broker (default for dev/testing)
  http     — direct HTTP POST to a REST endpoint
  iothub   — Azure IoT Hub (recommended for production / Project 15 platform)

Azure IoT Hub is the recommended path for production.  It integrates directly
with Microsoft Project 15 — the open-source conservation IoT platform — which
provides Stream Analytics, Cosmos DB, and a Power BI dashboard out of the box.

Transport resilience:
  - MQTT: queues alerts in memory while disconnected; flushes on reconnect.
  - IoT Hub: uses the SDK's built-in retry policy (exponential back-off).
  - HTTP: fire-and-forget with a 10s timeout; logs errors but does not retry.
"""

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Optional transport imports ─────────────────────────────────────────────────

try:
    import paho.mqtt.client as mqtt
    _MQTT_OK = True
except ImportError:
    _MQTT_OK = False

try:
    import requests as _requests
    _HTTP_OK = True
except ImportError:
    _HTTP_OK = False

try:
    from azure.iot.device import IoTHubDeviceClient, Message as IoTMessage
    _IOTHUB_OK = True
except ImportError:
    _IOTHUB_OK = False


# ── AlertSender ────────────────────────────────────────────────────────────────

class AlertSender:

    def __init__(self, config):
        self.config = config
        self._mqtt_client: Optional["mqtt.Client"] = None
        self._iothub_client: Optional["IoTHubDeviceClient"] = None
        self._pending_mqtt: list[dict] = []   # Queued while MQTT is offline
        self._iothub_lock = threading.Lock()  # IoT Hub client is not thread-safe

        method = config.ALERT_METHOD

        if method in ("mqtt", "both"):
            if _MQTT_OK:
                self._init_mqtt()
            else:
                logger.warning("paho-mqtt not installed — MQTT disabled. "
                               "Run: pip install paho-mqtt --break-system-packages")

        if method == "iothub":
            if _IOTHUB_OK:
                self._init_iothub()
            else:
                logger.warning("azure-iot-device not installed — IoT Hub disabled. "
                               "Run: pip install azure-iot-device --break-system-packages")

        if method in ("http", "both") and not _HTTP_OK:
            logger.warning("requests not installed — HTTP disabled. "
                           "Run: pip install requests --break-system-packages")

    # ─── MQTT ─────────────────────────────────────────────────────────────────

    def _init_mqtt(self):
        self._mqtt_client = mqtt.Client(
            client_id=f"oceankind_{self.config.DEVICE_ID}",
            protocol=mqtt.MQTTv5,
        )
        self._mqtt_client.on_connect = self._on_mqtt_connect
        self._mqtt_client.on_disconnect = self._on_mqtt_disconnect
        try:
            self._mqtt_client.connect(
                self.config.MQTT_BROKER,
                self.config.MQTT_PORT,
                keepalive=60,
            )
            self._mqtt_client.loop_start()
        except Exception as exc:
            logger.error(f"MQTT connect failed: {exc} — will retry automatically")

    def _on_mqtt_connect(self, client, userdata, flags, rc, props=None):
        if rc == 0:
            logger.info(f"MQTT connected to {self.config.MQTT_BROKER}:{self.config.MQTT_PORT}")
            for payload in self._pending_mqtt:
                self._mqtt_publish(payload)
            self._pending_mqtt.clear()
        else:
            logger.error(f"MQTT connect refused (rc={rc})")

    def _on_mqtt_disconnect(self, client, userdata, rc, props=None):
        logger.warning(f"MQTT disconnected (rc={rc}) — auto-reconnect active")

    def _mqtt_publish(self, payload: dict):
        if self._mqtt_client and self._mqtt_client.is_connected():
            self._mqtt_client.publish(
                self.config.MQTT_TOPIC,
                json.dumps(payload),
                qos=1,
                retain=False,
            )
            logger.info(f"MQTT → {self.config.MQTT_TOPIC} | {payload['event_id']}")
        else:
            self._pending_mqtt.append(payload)
            logger.warning(f"MQTT offline — alert queued ({len(self._pending_mqtt)} pending)")

    # ─── Azure IoT Hub ────────────────────────────────────────────────────────

    def _init_iothub(self):
        cs = self.config.IOTHUB_CONNECTION_STRING
        if not cs:
            logger.error(
                "OCEANKIND_IOTHUB_CONNECTION_STRING is empty — IoT Hub disabled.\n"
                "Get it from: Azure Portal → IoT Hub → Devices → your device → "
                "Primary Connection String"
            )
            return
        try:
            self._iothub_client = IoTHubDeviceClient.create_from_connection_string(
                cs,
                # Built-in retry: exponential back-off, indefinite retries
                # This keeps the device connected through network interruptions.
            )
            self._iothub_client.connect()
            logger.info(f"Azure IoT Hub connected | device={self.config.DEVICE_ID}")
        except Exception as exc:
            logger.error(f"IoT Hub connect failed: {exc}")
            self._iothub_client = None

    def _iothub_send(self, payload: dict):
        if not self._iothub_client:
            logger.warning("IoT Hub client not initialised — alert dropped")
            return

        with self._iothub_lock:
            try:
                # Build IoT Hub message with correct content metadata so
                # Stream Analytics can deserialise it without extra mapping.
                msg = IoTMessage(json.dumps(payload))
                msg.content_type = "application/json"
                msg.content_encoding = "utf-8"

                # Custom properties let IoT Hub routing filter by event type
                # without parsing the body — e.g. route only "blast" events.
                msg.custom_properties["event_type"] = payload.get("event_type", "unknown")
                msg.custom_properties["device_id"] = payload.get("device_id", "")

                self._iothub_client.send_message(msg)
                logger.info(f"IoT Hub ✓ | {payload['event_id']} ({payload['event_type']})")
            except Exception as exc:
                logger.error(f"IoT Hub send failed: {exc}")

    # ─── HTTP ─────────────────────────────────────────────────────────────────

    def _http_post(self, payload: dict):
        if not _HTTP_OK:
            return
        try:
            resp = _requests.post(
                self.config.HTTP_ENDPOINT,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.config.HTTP_TOKEN}",
                },
                timeout=10,
            )
            resp.raise_for_status()
            logger.info(f"HTTP POST OK ({resp.status_code}) | {payload['event_id']}")
        except Exception as exc:
            logger.error(f"HTTP POST failed: {exc}")

    # ─── Public Interface ─────────────────────────────────────────────────────

    def send(self, event, freq_profile: dict) -> dict:
        """
        Build and dispatch an alert for a DetectionEvent.
        Returns the alert payload dict (useful for logging/testing).

        Payload schema (aligns with Project 15 telemetry conventions):
        {
          "event_id":       UUID string,
          "device_id":      string,
          "timestamp_utc":  ISO-8601 string,
          "channel":        int (0 = left hydrophone, 1 = right),
          "event_type":     "blast" | "possible_blast" | "transient",
          "duration_s":     float,
          "peak_amplitude": float (0–1 normalised),
          "sta_lta_ratio":  float,
          "frequency": {
            "peak_freq_hz":     float,
            "broadband_score":  float (0–1),
            "spectral_flatness":float,
            "is_broadband":     bool
          },
          "location": { "latitude": float, "longitude": float }
        }
        """
        payload = {
            "event_id": str(uuid.uuid4()),
            "device_id": self.config.DEVICE_ID,
            "timestamp_utc": event.timestamp.astimezone(timezone.utc).isoformat(),
            "channel": event.channel,
            "event_type": event.event_type,
            "duration_s": round(event.duration_s, 4),
            "peak_amplitude": round(float(event.peak_amplitude), 6),
            "sta_lta_ratio": round(float(event.peak_sta_lta_ratio), 3),
            "frequency": freq_profile,
            "location": {
                "latitude": self.config.LATITUDE,
                "longitude": self.config.LONGITUDE,
            },
        }

        # Log level reflects confidence
        level = logging.WARNING if event.is_probable_blast else logging.INFO
        logger.log(
            level,
            f"[{event.event_type.upper()}] ch={event.channel} "
            f"dur={event.duration_s:.3f}s "
            f"STA/LTA={event.peak_sta_lta_ratio:.1f} "
            f"broadband={freq_profile.get('broadband_score', 0):.2f}"
        )

        method = self.config.ALERT_METHOD
        if method in ("mqtt", "both"):
            self._mqtt_publish(payload)
        if method in ("http", "both"):
            self._http_post(payload)
        if method == "iothub":
            self._iothub_send(payload)

        return payload

    def shutdown(self):
        """Cleanly disconnect all transports. Call before process exit."""
        if self._mqtt_client:
            self._mqtt_client.loop_stop()
            self._mqtt_client.disconnect()
        if self._iothub_client:
            with self._iothub_lock:
                try:
                    self._iothub_client.disconnect()
                    logger.info("Azure IoT Hub disconnected cleanly")
                except Exception:
                    pass
