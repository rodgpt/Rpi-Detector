"""Arranque, cableado de hilos y housekeeping.

Modelo de proceso (Fase 2):
  captura       stream continuo → cola de bloques. Jamás bloquea (R-1.1).
  clasificador  ventanas de 5 s, detector sobre cada una, decide.
  transporte    subidas + notificaciones. Toda la red.
  housekeeping  (hilo principal) status.json, telemetría, batería, config
                remota, reintentos, heartbeats. Todo con timers propios (R-6.1).

Apagado: SIGTERM/SIGINT → los hilos se cierran y los trabajos de transporte
pendientes se preservan en el spool. systemd reinicia; el spool drena al volver.
"""

import logging
import signal
import threading
import time
from datetime import datetime, timezone

from . import __version__
from . import capture
from . import config as C
from . import health
from . import notify
from . import pipeline as pl
from . import storage
from . import telemetry

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("oceankind")


def build_status(session_start: datetime, alert_count: int, last_rms: float,
                 solar: dict, modem: dict, stats: dict) -> dict:
    """sites/{site}/status.json, forma v2 del contrato. Solo campos que el
    consumidor usa, más la superficie de salud (R-2.x)."""
    now = datetime.now(timezone.utc)
    cfg = C.CONFIG.snapshot()
    detectors = {"psd": ["psd_tonal"], "rms": ["rms"],
                 "auto": ["psd_tonal", "rms"]}.get(C.DETECTION_MODE, [])
    return {
        "schema_version":   C.SCHEMA_VERSION,
        "site":             C.SITE,
        "device":           C.DEVICE_ID,
        "generated_utc":    now.isoformat(),
        "software_version": __version__,
        "last_seen":        now.isoformat(),
        "session_start":    session_start.isoformat(),
        "uptime_seconds":   int((now - session_start).total_seconds()),
        "system_uptime_s":  stats.get("system_uptime_s"),
        "health":           health.build_health(),
        "detection": {
            "mode":       C.DETECTION_MODE,
            "detectors":  detectors,
            "thresholds": {
                "score_min":        cfg["score_min"],
                "rms_min":          cfg["alert_min_rms"],
                "rms_threshold":    cfg["alert_threshold"],
                "psd_threshold_db": cfg["psd_threshold_db"],
                "psd_f_min":        cfg["psd_f_min"],
                "psd_f_max":        cfg["psd_f_max"],
                "window_hop_s":     cfg["window_hop_s"],
            },
            "cooldown_s": cfg["cooldown_s"],
            "last_rms":   round(last_rms, 4),
        },
        "audio": {
            "device":      (C.AUDIO_SOURCE if C.AUDIO_SOURCE != "device"
                            else f"by-name:{C.AUDIO_DEVICE_NAME}"),
            "sample_rate": C.SAMPLE_RATE,
            "channels":    C.CHANNELS,
        },
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
            "system_load_w":     telemetry.compute_system_load(solar),
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


def upload_status(session_start: datetime, alert_count: int, last_rms: float) -> dict:
    """Recolecta sensores, sube status.json, alimenta el CSV. Devuelve lo
    recolectado para reuso (heartbeat WhatsApp)."""
    solar = telemetry.fetch_ve_direct()
    modem = telemetry.fetch_modem_signal()
    stats = telemetry.get_system_stats()
    telemetry.check_battery_alert(solar)
    status = build_status(session_start, alert_count, last_rms, solar, modem, stats)
    storage.upload_json(storage.site_path("status.json"), status)
    log.info("  → status.json (uptime %ds, %d alertas, duty %s%%)",
             status["uptime_seconds"], alert_count,
             status["health"]["duty_cycle_pct"] if status["health"]["duty_cycle_pct"] is not None else "?")
    telemetry.append_data_log({
        "timestamp_utc": status["last_seen"], "last_rms": round(last_rms, 4),
        "alert_count_session": alert_count,
        **status["power"], **status["network"], **status["system"],
    })
    return {"solar": solar, "modem": modem, "stats": stats}


def main() -> None:
    C.validate_startup_config()

    log.info("=== OceanKind %s — %s (sitio %s) ===", __version__, C.DEVICE_ID, C.SITE or "—")
    log.info("Modelo continuo: captura → clasificador → transporte. Modo %s | fuente %s",
             C.DETECTION_MODE.upper(), C.AUDIO_SOURCE)
    cfg = C.CONFIG.snapshot()
    log.info("Umbrales: score_min=%.2f rms_min=%.3f rms_thr=%.3f | PSD %g dB, %g-%g Hz | cooldown %.0fs",
             cfg["score_min"], cfg["alert_min_rms"], cfg["alert_threshold"],
             cfg["psd_threshold_db"], cfg["psd_f_min"], cfg["psd_f_max"], cfg["cooldown_s"])
    if not C.STORAGE_ENABLED:
        log.warning("Sin almacenamiento configurado (ni Azure ni OUTPUT_DIR) — "
                    "eventos solo en log y WhatsApp")

    if C.STORAGE_ENABLED:
        storage.publish_site_registry()
        storage.ensure_aux_blobs()

    iot = notify.build_iot_client()
    pipe = pl.Pipeline(iot)
    source = capture.make_source(pipe.block_queue)

    stop = threading.Event()

    def _sig(_num, _frm):
        log.info("Señal de apagado recibida — cerrando ordenadamente…")
        stop.set()

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    pipe.start()
    source.start()
    notify.retry_pending_whatsapp()

    session_start = datetime.now(timezone.utc)
    last_status = last_power = last_config = last_wa_hb = 0.0
    applied_config_version = None

    try:
        while not stop.is_set():
            now = time.time()
            hb = C.CONFIG.snapshot()["heartbeat_s"]

            if now - last_status >= hb:
                health.maybe_alert_audio_health()   # R-2.2: hidrófono muerto suena
                sensors = None
                if C.STORAGE_ENABLED:
                    storage.drain_event_spool()
                    try:
                        sensors = upload_status(session_start, pipe.alert_count, pipe.last_rms)
                    except Exception as exc:
                        log.warning("  Error subiendo status.json: %s", exc)
                if iot:
                    try:
                        notify.send_iot_message(iot, pipe.last_rms, pipe.last_peak_db,
                                                msg_type="heartbeat",
                                                threshold=C.CONFIG.snapshot()["alert_threshold"])
                    except Exception as exc:
                        log.warning("  Error en heartbeat IoT Hub: %s", exc)
                last_status = now

                if C.STORAGE_ENABLED and now - last_power >= 600:
                    telemetry.upload_power_history()
                    last_power = now

                if now - last_wa_hb >= C.WHATSAPP_HEARTBEAT_INTERVAL:
                    s = sensors or {"solar": telemetry.fetch_ve_direct(),
                                    "modem": telemetry.fetch_modem_signal(),
                                    "stats": telemetry.get_system_stats()}
                    notify.send_whatsapp_heartbeat(
                        session_start, pipe.alert_count, pipe.last_rms,
                        s["stats"], s["modem"], s["solar"],
                        telemetry.compute_system_load(s["solar"]),
                        health.audio_status_str())
                    notify.retry_pending_whatsapp()
                    last_wa_hb = now

            if C.STORAGE_ENABLED and now - last_config >= C.CONFIG_CHECK_INTERVAL:
                payload = storage.download_json(storage.site_path("remote_config.json"))
                if payload and payload.get("version") != applied_config_version:
                    values = C.verify_remote_config(payload)
                    if values is not None:
                        changes = C.CONFIG.apply(values)
                        applied_config_version = payload.get("version")
                        if changes:
                            log.info("⚙️  Config remota v%s aplicada: %s",
                                     applied_config_version, "; ".join(changes))
                        else:
                            log.info("⚙️  Config remota v%s sin cambios efectivos",
                                     applied_config_version)
                last_config = now

            stop.wait(timeout=1.0)
    finally:
        source.stop()
        pipe.stop()
        if iot:
            try:
                iot.disconnect()
                log.info("Desconectado de IoT Hub.")
            except Exception:
                pass
        log.info("Apagado limpio. Hasta la próxima.")


if __name__ == "__main__":
    main()
