"""Notificaciones: WhatsApp (plantillas aprobadas), llamadas de voz, IoT Hub.

Todo con timeout explícito (R-5.5). Corre en los hilos de transporte y
housekeeping — NUNCA en el de captura. Si Twilio no está configurado (banco),
cada envío falla A GRITOS en el log, jamás en silencio.
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config as C

log = logging.getLogger("oceankind")


def _twilio_client():
    from twilio.rest import Client                      # noqa: PLC0415
    from twilio.http.http_client import TwilioHttpClient  # noqa: PLC0415
    return Client(C.TWILIO_ACCOUNT_SID, C.TWILIO_AUTH_TOKEN,
                  http_client=TwilioHttpClient(timeout=C.TWILIO_TIMEOUT_S))


def _wa_vars(*values, prefix_v2: bool = True) -> dict:
    """Variables de plantilla según WA_TPL_VERSION (v2 antepone la estación,
    v3 la pone en {{1}} y corre el resto un lugar)."""
    vals = list(values)
    if C.WA_TPL_VERSION == "3":
        vals = [C.SENSOR_LOCATION_NAME] + vals
    elif prefix_v2 and vals:
        vals[0] = f"{C.SENSOR_LOCATION_NAME} · {vals[0]}"
    return {str(i + 1): v for i, v in enumerate(vals)}


def _wa_send(content_sid: str, variables: dict, log_ok: str = "",
             recipients: list | None = None) -> None:
    if not C.TWILIO_CONFIGURED:
        log.error("WhatsApp NO enviado (sin credenciales Twilio — modo banco)")
        return
    to_list = recipients if recipients else C.TWILIO_TO_LIST
    client = _twilio_client()
    variables_json = json.dumps(variables, ensure_ascii=False)
    sent, last_err = 0, None
    for to_number in to_list:
        try:
            client.messages.create(from_=C.TWILIO_FROM, to=to_number,
                                   content_sid=content_sid,
                                   content_variables=variables_json)
            sent += 1
        except Exception as exc:
            last_err = exc
            log.warning("WhatsApp falló para %s: %s", to_number, exc)
    if log_ok and sent > 0:
        log.info("%s (enviado a %d/%d)", log_ok, sent, len(to_list))
    if sent == 0 and last_err:
        raise last_err


def send_degraded_alert(reason: str) -> None:
    """Aviso de degradación (detector caído, sin audio). Nunca silencioso.

    Usa la plantilla de heartbeat (la única aprobada con campo libre) con el
    motivo en el campo de uptime/audio. Best-effort: si falla, queda en el log
    y en health.degraded_reason igual.
    """
    if not C.TWILIO_CONFIGURED or not C.TWILIO_TO or "XXXXXXXXX" in C.TWILIO_TO:
        log.error("DEGRADADO (sin WhatsApp configurado): %s", reason)
        return
    try:
        _wa_send(C.WA_TPL_HEARTBEAT, _wa_vars(
            C.fmt_local(), "n/d", "n/d", "n/d", f"⚠️ DEGRADADO: {reason}",
        ), log_ok=f"⚠️ aviso de degradación enviado: {reason}",
           recipients=C.TO_TECNICO)
    except Exception as exc:
        log.warning("no se pudo enviar aviso de degradación: %s", exc)


# ─── Alertas de detección + buffer de reintentos ─────────────────────────────

def _save_pending_alert(rms: float, peak_db: float, clip_rel: str | None,
                        ts: str, label: str | None = None) -> None:
    pending = []
    if C.PENDING_ALERTS_FILE.exists():
        try:
            pending = json.loads(C.PENDING_ALERTS_FILE.read_text())
        except Exception:
            pending = []
    pending.append({"rms": rms, "peak_db": peak_db, "blob_name": clip_rel,
                    "timestamp": ts, "label": label or C.DETECTION_LABEL, "attempts": 1})
    pending = pending[-50:]
    C.PENDING_ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    C.PENDING_ALERTS_FILE.write_text(json.dumps(pending, indent=2))
    log.info("  → Alerta guardada localmente (%d pendiente/s)", len(pending))


def pending_alert_count() -> int:
    try:
        return len(json.loads(C.PENDING_ALERTS_FILE.read_text())) if C.PENDING_ALERTS_FILE.exists() else 0
    except Exception:
        return 0


def retry_pending_whatsapp() -> None:
    if not C.PENDING_ALERTS_FILE.exists():
        return
    try:
        pending = json.loads(C.PENDING_ALERTS_FILE.read_text())
    except Exception:
        return
    if not pending:
        return
    log.info("Reintentando %d alerta/s pendiente/s...", len(pending))
    still_pending = []
    for alert in pending:
        try:
            audio_url = (f"{C.DASHBOARD_URL}?play={alert['blob_name']}"
                         if alert.get("blob_name") else C.DASHBOARD_URL)
            _wa_send(C.WA_TPL_ALERT, _wa_vars(
                alert.get("label") or C.DETECTION_LABEL,
                C.fmt_local_iso(alert.get("timestamp", "")),
                f"{alert['rms']:.4f}",
                "n/d",
                audio_url,
            ), recipients=C.TO_ALERTA)
            log.info("  → Alerta pendiente reenviada: %s", alert.get("timestamp"))
        except Exception:
            alert["attempts"] = alert.get("attempts", 1) + 1
            if alert["attempts"] < 10:
                still_pending.append(alert)
            else:
                log.warning("  Alerta descartada tras 10 intentos: %s", alert.get("timestamp"))
    if still_pending:
        C.PENDING_ALERTS_FILE.write_text(json.dumps(still_pending, indent=2))
    else:
        C.PENDING_ALERTS_FILE.unlink(missing_ok=True)


def send_whatsapp(rms: float, peak_db: float, clip_rel: str | None = None,
                  ml_result: dict | None = None, label: str | None = None) -> None:
    """Alerta de detección. clip_rel es el path v2 del clip, o None si no subió
    (el link cae al dashboard, nunca a un blob muerto — R-4.4)."""
    if not C.TWILIO_CONFIGURED:
        log.error("WhatsApp NO enviado (sin credenciales Twilio — modo banco): detección RMS=%.4f", rms)
        return
    if not C.TWILIO_TO or "XXXXXXXXX" in C.TWILIO_TO:
        log.warning("WhatsApp: configura TWILIO_TO con tu número real.")
        return
    label = label or (ml_result.get("label") if ml_result else None) or C.DETECTION_LABEL
    try:
        conf = (f"{ml_result.get('proba', 0)*100:.1f}%" if ml_result else "n/d")
        audio_url = (f"{C.DASHBOARD_URL}?play={clip_rel}" if clip_rel else C.DASHBOARD_URL)
        _wa_send(C.WA_TPL_ALERT, _wa_vars(
            label, C.fmt_local(fmt="%H:%M:%S"), f"{rms:.4f}", conf, audio_url,
        ), recipients=C.TO_ALERTA)
        log.info("  → WhatsApp enviado a %s", C.TWILIO_TO)
    except ImportError:
        log.warning("twilio no instalado — corre: pip3 install twilio --break-system-packages")
    except Exception as exc:
        log.warning("Error WhatsApp: %s — guardando en buffer local", exc)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        _save_pending_alert(rms, peak_db, clip_rel, ts, label=label)


def send_whatsapp_heartbeat(session_start: datetime, alert_count: int,
                            last_rms: float, stats: dict, modem: dict, solar: dict,
                            system_load_w, audio_status_str: str) -> None:
    """Ping de vida. Los datos llegan por parámetro: este módulo no toca
    hardware ni sensores (eso es de telemetry, en su propio hilo)."""
    if not C.TWILIO_TO or "XXXXXXXXX" in C.TWILIO_TO:
        return
    try:
        elapsed = (datetime.now(timezone.utc) - session_start).total_seconds()
        session_up = fmt_uptime(elapsed)
        _wa_send(C.WA_TPL_HEARTBEAT, _wa_vars(
            C.fmt_local(),
            (f"{solar['battery_voltage_v']}" if solar.get("battery_voltage_v") is not None else "n/d"),
            (f"{solar['panel_power_w']}"     if solar.get("panel_power_w")     is not None else "n/d"),
            (f"{int(modem['signal_bars'])}"  if modem.get("signal_bars")       is not None else "n/d"),
            f"{session_up}  ·  {audio_status_str}",
        ), recipients=C.TO_TECNICO)
        log.info("  → Heartbeat WhatsApp enviado (alertas=%d, rms=%.4f, load=%sW)",
                 alert_count, last_rms, system_load_w if system_load_w is not None else "?")
    except Exception as exc:
        log.warning("Error enviando heartbeat WhatsApp: %s", exc)


def fmt_uptime(seconds) -> str:
    if seconds is None:
        return "?"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    return f"{d}d {h}h {m}m" if d else f"{h}h {m}m"


# ─── Llamada de voz ante clúster de detecciones ──────────────────────────────
_recent_alert_ts: list = []
_last_call_ts = 0.0


def _place_call(to_number: str) -> None:
    client = _twilio_client()
    twiml = (
        '<Response>'
        '<Say voice="Polly.Lupe" language="es-MX">'
        f'Alerta del sistema acústico Mar Futura. Se registraron múltiples detecciones '
        f'en pocos minutos en {C.SENSOR_LOCATION_NAME}. '
        'Revisa el panel y coordina fiscalización según corresponda.'
        '</Say>'
        '<Pause length="1"/>'
        '<Say voice="Polly.Lupe" language="es-MX">'
        'Repito: múltiples detecciones acústicas. Atención.'
        '</Say>'
        '</Response>'
    )
    client.calls.create(to=to_number, from_=C.CALL_FROM, twiml=twiml)


def maybe_trigger_cluster_call() -> None:
    """Cuenta CADA detección (incluidas suprimidas) y llama ante un clúster."""
    global _last_call_ts
    if not C.CALL_ENABLED or not C.CALL_TO_LIST or not C.TWILIO_CONFIGURED:
        return
    now = time.monotonic()
    _recent_alert_ts.append(now)
    cutoff = now - C.CALL_CLUSTER_WINDOW_S
    while _recent_alert_ts and _recent_alert_ts[0] < cutoff:
        _recent_alert_ts.pop(0)
    if len(_recent_alert_ts) < C.CALL_CLUSTER_COUNT:
        return
    if now - _last_call_ts < C.CALL_COOLDOWN_S:
        log.info("  Clúster de %d detecciones (<%.0fs), pero llamada en cooldown.",
                 len(_recent_alert_ts), C.CALL_CLUSTER_WINDOW_S)
        return
    _last_call_ts = now
    log.warning("🚨 CLÚSTER: %d detecciones en <%.0fs → generando LLAMADA de voz",
                len(_recent_alert_ts), C.CALL_CLUSTER_WINDOW_S)
    for to in C.CALL_TO_LIST:
        try:
            _place_call(to)
            log.warning("  📞 Llamada iniciada a %s", to)
        except Exception as exc:
            log.warning("  ✗ Falló la llamada a %s: %s", to, exc)


# ─── IoT Hub (rama muerta conocida — nada la consume; ver TODO) ──────────────

def build_iot_client():
    if not C.IOTHUB_CONNECTION_STRING:
        log.warning("OCEANKIND_IOTHUB_CONNECTION_STRING no definida — continuando sin IoT Hub.")
        return None
    try:
        from azure.iot.device import IoTHubDeviceClient  # noqa: PLC0415
    except ImportError:
        log.warning("azure-iot-device no instalado — continuando sin IoT Hub.")
        return None
    try:
        client = IoTHubDeviceClient.create_from_connection_string(C.IOTHUB_CONNECTION_STRING)
        client.connect()
        log.info("Conectado a Azure IoT Hub ✓")
        return client
    except Exception as exc:
        log.warning("No se pudo conectar a IoT Hub: %s — continuando sin IoT Hub.", exc)
        return None


def send_iot_message(client, audio_level: float, peak_db: float,
                     msg_type: str = "alert", audio_url: str | None = None,
                     threshold: float = 0.0) -> None:
    from azure.iot.device import Message  # noqa: PLC0415
    payload = {
        "type": msg_type, "audio_level": round(audio_level, 6),
        "peak_db": round(peak_db, 2), "alert_flag": msg_type == "alert",
        "threshold": threshold,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "device": C.DEVICE_ID, "source": "hydrophone",
    }
    if audio_url:
        payload["audio_url"] = audio_url
    msg = Message(json.dumps(payload))
    msg.content_encoding = "utf-8"
    msg.content_type = "application/json"
    if msg_type == "alert":
        msg.custom_properties["alert"] = "true"
    client.send_message(msg)
