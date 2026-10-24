"""Arranque, cableado de hilos y housekeeping.

Modelo de proceso (Fase 2):
  captura       stream continuo → cola de bloques. Jamás bloquea (R-1.1).
  clasificador  ventanas de 5 s, detector sobre cada una, decide.
  transporte    subidas + push al backend. Toda la red. El backend avisa (D-020).
  housekeeping  (hilo principal) status.json, telemetría, config remota,
                disparador OTA (D-021), reintentos, heartbeats. Todo con
                timers propios (R-6.1).

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
from . import detectors as registry
from . import ota
from . import pipeline as pl
from . import push
from . import storage
from . import telemetry
from . import watchdog

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
    # Lo que DE VERDAD está cargado y corriendo (R-2.6), no lo configurado.
    detectors = registry.loaded_names(cfg)
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
        "ota":              ota.status_block(),      # D-021: qué corre vs qué se asignó
        "detection": {
            "mode":       cfg["detection_mode"],
            "detectors":  detectors,
            "thresholds": {
                "score_min":        cfg["score_min"],
                "rms_min":          cfg["alert_min_rms"],
                "rms_threshold":    cfg["alert_threshold"],
                "psd_threshold_db": cfg["psd_threshold_db"],
                "psd_f_min":        cfg["psd_f_min"],
                "psd_f_max":        cfg["psd_f_max"],
                "window_hop_s":     cfg["window_hop_s"],
                "ml_score_min":     C.ML_SCORE_MIN,   # device-local (ver TODO contrato)
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


def collect_status(session_start: datetime, alert_count: int, last_rms: float) -> tuple[dict, dict]:
    """Sensores + status.json v2, en un solo lugar, UNA vez por tick de
    heartbeat (D-018: antes esto vivía dentro de `upload_status`, que solo se
    llamaba con almacenamiento activo — el POST al backend §Device heartbeat
    no depende de eso y necesita los mismos datos, así que la recolección se
    separó de la subida). El resultado se reusa para el POST y el blob (si hay
    almacenamiento) — nunca se recolecta dos veces en el mismo tick. La alerta
    de batería la decide el backend a partir de power.battery_voltage_v (D-020)."""
    solar = telemetry.fetch_ve_direct()
    modem = telemetry.fetch_modem_signal()
    stats = telemetry.get_system_stats()
    status = build_status(session_start, alert_count, last_rms, solar, modem, stats)
    return status, {"solar": solar, "modem": modem, "stats": stats}


def write_status_blob(status: dict, alert_count: int, last_rms: float) -> None:
    """El camino durable-pero-opcional (§Device heartbeat: 'blob storage,
    si hay'). Solo corre con almacenamiento activo; el POST de más abajo no
    depende de esto en absoluto."""
    storage.upload_json(storage.site_path("status.json"), status)
    log.info("  → status.json (uptime %ds, %d alertas, duty %s%%)",
             status["uptime_seconds"], alert_count,
             status["health"]["duty_cycle_pct"] if status["health"]["duty_cycle_pct"] is not None else "?")
    telemetry.append_data_log({
        "timestamp_utc": status["last_seen"], "last_rms": round(last_rms, 4),
        "alert_count_session": alert_count,
        **status["power"], **status["network"], **status["system"],
    })


def main() -> None:
    C.validate_startup_config()

    cfg = C.CONFIG.snapshot()
    log.info("=== OceanKind %s — %s (sitio %s) ===", __version__, C.DEVICE_ID, C.SITE or "—")
    log.info("Modelo continuo: captura → clasificador → transporte. Modo %s | fuente %s",
             cfg["detection_mode"].upper(), C.AUDIO_SOURCE)
    log.info("Umbrales: score_min=%.2f rms_min=%.3f rms_thr=%.3f | PSD %g dB, %g-%g Hz | cooldown %.0fs",
             cfg["score_min"], cfg["alert_min_rms"], cfg["alert_threshold"],
             cfg["psd_threshold_db"], cfg["psd_f_min"], cfg["psd_f_max"], cfg["cooldown_s"])
    if not C.STORAGE_ENABLED:
        log.warning("Sin almacenamiento configurado (ni Azure ni OUTPUT_DIR) — "
                    "eventos solo en log y push al backend; la config remota "
                    "se consulta vía GET /api/devices/config")
    if push.enabled():
        log.info("Push de eventos y heartbeat al backend: ACTIVO → %s (timeout %.0fs). "
                 "Las alertas a humanos las manda el backend (D-020).",
                 C.BACKEND_URL, C.BACKEND_TIMEOUT_S)
    else:
        log.info("Push de eventos al backend: deshabilitado (sin OCEANKIND_BACKEND_URL) — "
                 "el índice del dashboard se actualiza solo por reconciliación")
    o = ota.status_block()
    log.info("OTA: instalada %s (%s) | asignada %s%s%s",
             o["installed_release"] or "?", o["installed_sha"] or "sin registro",
             o["assigned_release"] or "ninguna",
             " | RETENIDA" if o["hold"] else "",
             f" | falló {o['failed_release'] or o['failed_sha']}" if o["failed_sha"] else "")

    if C.STORAGE_ENABLED:
        storage.publish_site_registry()
        storage.ensure_aux_blobs()

    pipe = pl.Pipeline()
    source = capture.make_source(pipe.block_queue)

    stop = threading.Event()

    def _sig(_num, _frm):
        log.info("Señal de apagado recibida — cerrando ordenadamente…")
        stop.set()

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    # La captura arranca ANTES de cualquier red (F-33): nada de lo que sigue
    # puede dejar a la unidad sin escuchar un segundo.
    pipe.start()
    source.start()
    watchdog.arm()

    session_start = datetime.now(timezone.utc)
    last_status = last_power = last_config = 0.0
    applied_config_version = None
    rejected_config_version = None

    try:
        while not stop.is_set():
            now = time.time()
            hb = C.CONFIG.snapshot()["heartbeat_interval_s"]

            if now - last_status >= hb:
                health.maybe_alert_audio_health()   # R-2.2: hidrófono muerto suena
                health.maybe_alert_capture()        # F-29: stream muerto suena
                health.note_upload_backlog()        # subidas fallando suenan
                health.flush_degraded_alerts()
                watchdog.tick()
                # Drenados con presupuesto y keepalive: este hilo es el que
                # pingea al watchdog; una recuperación larga no puede parecer
                # un cuelgue.
                push.drain_push_spool(budget_s=C.HOUSEKEEPING_BUDGET_S, keepalive=watchdog.tick)

                # Recolección UNCONDICIONAL — el POST de heartbeat (§Device
                # heartbeat) no depende de almacenamiento; antes esto entero
                # vivía dentro de `if C.STORAGE_ENABLED`, que es exactamente
                # por qué una unidad sin Azure/OUTPUT_DIR nunca mandaba nada
                # de salud al dashboard (2026-09-23). try/except explícito:
                # antes esta recolección solo corría DENTRO del try de más
                # abajo (protegida); ahora corre siempre, así que necesita su
                # propia protección — un hipo de un sensor (serial VE.Direct,
                # batería) no puede tumbar captura/clasificación/transporte.
                status = sensors = None
                try:
                    status, sensors = collect_status(session_start, pipe.alert_count, pipe.last_rms)
                    if push.enabled():
                        push.post_heartbeat(status)
                except Exception as exc:
                    log.warning("  Error recolectando estado/heartbeat: %s", exc)
                if C.STORAGE_ENABLED:
                    # Independiente de la recolección de arriba (siempre corría
                    # así, incluso si upload_status fallaba — no acoplar).
                    try:
                        storage.drain_event_spool(budget_s=C.HOUSEKEEPING_BUDGET_S,
                                                  keepalive=watchdog.tick)
                    except Exception as exc:
                        log.warning("  Error drenando el spool de eventos: %s", exc)
                    if status is not None:
                        try:
                            write_status_blob(status, pipe.alert_count, pipe.last_rms)
                        except Exception as exc:
                            log.warning("  Error subiendo status.json: %s", exc)
                last_status = now

                if C.STORAGE_ENABLED and now - last_power >= 600:
                    telemetry.upload_power_history()
                    last_power = now

            if now - last_config >= C.CONFIG_CHECK_INTERVAL and (C.STORAGE_ENABLED or push.enabled()):
                # Todo el ciclo bajo try: un documento firmado pero malformado
                # (un valor no numérico, un JSON que no es objeto) levantaba
                # ValueError/AttributeError fuera de ConfigRejected, tumbaba el
                # proceso, systemd lo levantaba, el heartbeat salía sano y
                # volvía a caer — un crash loop que parecía salud.
                try:
                    if C.STORAGE_ENABLED:
                        payload = storage.download_json(storage.site_path("remote_config.json"))
                    else:
                        # Push-only: el mismo documento firmado, vía backend
                        # (D-020 lo sirve byte a byte). Sin esto, una unidad sin
                        # almacenamiento no era afinable sin firmware (D-014/15).
                        payload = push.fetch_config()
                    # Fuente inalcanzable o config_version sin cambios: se mantiene
                    # la última config válida. Jamás se cae a defaults (contrato:
                    # "stale and detecting beats fresh and silent").
                    if (isinstance(payload, dict)
                            and payload.get("config_version") != applied_config_version):
                        try:
                            values = C.verify_remote_config(payload)
                        except C.ConfigRejected as exc:
                            # Rechazo = evento de salud, no línea de debug.
                            health.set_config_error(str(exc))
                            if payload.get("config_version") != rejected_config_version:
                                log.error("⚙️  Config remota RECHAZADA entera: %s", exc)
                                rejected_config_version = payload.get("config_version")
                        else:
                            if values is not None:   # None = dirigida a otra unidad
                                changes = C.CONFIG.apply(values)
                                health.set_config_error(None)
                                applied_config_version = payload.get("config_version")
                                rejected_config_version = None
                                log.info("⚙️  Config remota %r aplicada: %s",
                                         applied_config_version,
                                         "; ".join(changes) if changes else "sin cambios efectivos")
                                # D-021: la asignación de release viaja en el
                                # mismo documento firmado. Se recuerda SIEMPRE
                                # que verifica (también si no cambió nada más).
                                ota.record_assignment(values.get("release"),
                                                      bool(values.get("hold", False)))
                except Exception as exc:    # noqa: BLE001
                    health.set_config_error(f"error procesando config remota: {exc}")
                    log.error("⚙️  Config remota: error inesperado (%s) — se mantiene la última "
                              "config válida", exc)
                # Converger a la release asignada (D-021). Fuera del try de
                # arriba a propósito: una config inalcanzable no impide actuar
                # sobre la última asignación verificada, y un fallo al lanzar
                # el OTA no puede contaminar el estado de la config.
                try:
                    decision = ota.maybe_trigger()
                    if decision not in ("none", "current", "hold", "cooldown", "launched"):
                        log.warning("OTA: asignada %s pero no se lanza (%s)",
                                    ota.status_block()["assigned_release"], decision)
                except Exception as exc:    # noqa: BLE001
                    log.error("OTA: error inesperado en el disparador (%s)", exc)
                last_config = now

            health.flush_degraded_alerts()   # avisos encolados por otros hilos (clasificador)
            watchdog.tick()   # pingea a systemd SOLO si los hilos laten (R-2.7)
            stop.wait(timeout=1.0)
    finally:
        source.stop()
        pipe.stop()
        log.info("Apagado limpio. Hasta la próxima.")


if __name__ == "__main__":
    main()
