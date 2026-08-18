"""Telemetría: Victron VE.Direct, modem 4G, stats de sistema, batería, CSV.

Corre en el hilo housekeeping, jamás en el de captura. El CSV vive en el
directorio de estado (tmpfs — NUNCA en /boot/firmware, F-16) y se recorta para
no crecer sin límite. Bajo overlay se pierde al reiniciar: los huecos que eso
deja en power_history son la señal con la que el dashboard detecta reinicios.
"""

import csv
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config as C
from . import notify
from . import storage

log = logging.getLogger("oceankind")

VICTRON_CHARGE_STATES = {
    0: "Off", 2: "Fault", 3: "Bulk", 4: "Absorption", 5: "Float",
    7: "Equalize", 245: "Starting up", 247: "Auto equalize", 252: "External control",
}


def get_system_stats() -> dict:
    stats = {}
    try:
        temp_raw = Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()
        stats["cpu_temp_c"] = round(int(temp_raw) / 1000, 1)
    except Exception:
        stats["cpu_temp_c"] = None
    try:
        stats["system_uptime_s"] = int(float(Path("/proc/uptime").read_text().split()[0]))
    except Exception:
        stats["system_uptime_s"] = None
    try:
        import shutil  # noqa: PLC0415
        disk_path = "/media/root-ro" if Path("/media/root-ro").is_mount() else "/"
        du = shutil.disk_usage(disk_path)
        stats["disk_used_pct"] = round(du.used / du.total * 100, 1)
        stats["disk_free_gb"]  = round(du.free / (1024 ** 3), 2)
        stats["disk_total_gb"] = round(du.total / (1024 ** 3), 1)
    except Exception:
        stats["disk_used_pct"] = stats["disk_free_gb"] = stats["disk_total_gb"] = None
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
        stats["ram_used_mb"]  = round((total - avail) / 1024)
        stats["ram_total_mb"] = round(total / 1024)
    except Exception:
        stats["ram_used_pct"] = stats["ram_used_mb"] = stats["ram_total_mb"] = None
    return stats


def fetch_ve_direct() -> dict:
    """Un frame del Victron BlueSolar MPPT (VE.Direct serial, 19200 8N1, timeout 1s)."""
    try:
        import serial  # noqa: PLC0415
        import glob    # noqa: PLC0415
    except ImportError:
        return {}
    candidates = [C.VEDIRECT_PORT] if C.VEDIRECT_PORT else sorted(glob.glob("/dev/ttyUSB*"))
    candidates = [p for p in candidates if p and Path(p).exists()]
    if not candidates:
        return {}
    fields: dict = {}
    for port in candidates:
        try:
            with serial.Serial(port, 19200, timeout=1.0) as ser:
                buf = bytearray()
                deadline = time.time() + 3.0
                while time.time() < deadline:
                    chunk = ser.read(256)
                    if chunk:
                        buf.extend(chunk)
                        if b"Checksum" in buf:
                            break
                if b"Checksum" not in buf:
                    continue
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
        try:
            return int(fields[k])
        except (KeyError, ValueError):
            return None

    v_mv = _int("V");   out["battery_voltage_v"] = round(v_mv / 1000.0, 2) if v_mv is not None else None
    i_ma = _int("I");   out["battery_current_a"] = round(i_ma / 1000.0, 2) if i_ma is not None else None
    vp   = _int("VPV"); out["panel_voltage_v"]   = round(vp / 1000.0, 1) if vp is not None else None
    out["panel_power_w"] = _int("PPV")
    cs = _int("CS")
    if cs is not None:
        out["charge_state"]    = VICTRON_CHARGE_STATES.get(cs, f"State {cs}")
        out["charge_state_id"] = cs
    h20 = _int("H20"); out["yield_today_kwh"]   = round(h20 / 100.0, 2) if h20 is not None else None
    h19 = _int("H19"); out["yield_total_kwh"]   = round(h19 / 100.0, 1) if h19 is not None else None
    out["max_power_today_w"] = _int("H21")
    return {k: v for k, v in out.items() if v is not None}


def compute_system_load(solar: dict) -> float | None:
    """load ≈ panel − (V·I hacia batería). Ignora pérdidas MPPT ~5%: sirve para
    tendencia, no contabilidad."""
    v, i, ppv = solar.get("battery_voltage_v"), solar.get("battery_current_a"), solar.get("panel_power_w")
    if v is None or i is None:
        return None
    load = (ppv or 0.0) - v * i
    return round(max(load, 0.0), 1)


def fetch_modem_signal() -> dict:
    """Señal 4G del web admin del modem ZTE (LAN, sin login, timeout 3s)."""
    try:
        import urllib.request, urllib.parse  # noqa: PLC0415
        params = {"multi_data": "1",
                  "cmd": "signalbar,network_type,modem_main_state,lte_rsrp,lte_rsrq,rssi",
                  "isTest": "false"}
        url = f"{C.MODEM_API_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Referer": "http://192.168.0.1/index.html"})
        import json as _json  # noqa: PLC0415
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = _json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        log.debug("modem signal fetch failed: %s", exc)
        return {}
    out = {}
    bars_str = data.get("signalbar", "")
    if bars_str.isdigit():
        bars = int(bars_str)
        out["signal_bars"] = bars
        out["signal_rssi"] = {5: -65, 4: -75, 3: -85, 2: -95, 1: -110, 0: None}.get(bars)
    if data.get("network_type"):
        out["network_type"] = data["network_type"]
    return out


# ─── Alerta de batería (debounce + histéresis, preservado de v1) ─────────────

_BATTERY_LEVEL_ORDER = {"warning": 1, "critical": 2, "emergency": 3}


def _load_battery_state() -> dict:
    import json  # noqa: PLC0415
    try:
        s = json.loads(C.BATTERY_STATE_FILE.read_text())
        if "alerted" not in s:
            s = {"alerted": s.get("level"), "pending": None, "pending_count": 0}
        return s
    except Exception:
        return {"alerted": None, "pending": None, "pending_count": 0}


def _save_battery_state(state: dict) -> None:
    import json  # noqa: PLC0415
    try:
        C.BATTERY_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        C.BATTERY_STATE_FILE.write_text(json.dumps(state))
    except Exception as exc:
        log.debug("no se pudo persistir battery state: %s", exc)


def check_battery_alert(solar: dict) -> None:
    """Alerta si la batería cruza un umbral de manera SOSTENIDA (debounce), con
    histéresis para no re-alertar el mismo nivel. No-op sin Victron."""
    v = solar.get("battery_voltage_v")
    if v is None:
        return
    state = _load_battery_state()
    alerted = state.get("alerted")
    if v >= C.BATTERY_RECOVERY_V:
        current = "recovered"
    elif v <= C.BATTERY_EMERGENCY_V:
        current = "emergency"
    elif v <= C.BATTERY_CRITICAL_V:
        current = "critical"
    elif v <= C.BATTERY_WARNING_V:
        current = "warning"
    else:
        _save_battery_state({"alerted": alerted, "pending": None, "pending_count": 0})
        return
    pending_count = state.get("pending_count", 0) + 1 if state.get("pending") == current else 1
    state = {"alerted": alerted, "pending": current, "pending_count": pending_count}
    if pending_count < C.BATTERY_DEBOUNCE_CYCLES:
        log.info("  batería %s (%.2fV) ciclo %d/%d — esperando confirmación",
                 current, v, pending_count, C.BATTERY_DEBOUNCE_CYCLES)
        _save_battery_state(state)
        return
    if current == "recovered":
        if alerted is not None:
            log.info("🔋 batería recuperada (V=%.2f) — sin WhatsApp (no hay plantilla)", v)
        _save_battery_state({"alerted": None, "pending": None, "pending_count": 0})
        return
    if _BATTERY_LEVEL_ORDER[current] <= _BATTERY_LEVEL_ORDER.get(alerted, 0):
        _save_battery_state(state)
        return
    _send_battery_warning(v, current, solar)
    _save_battery_state({"alerted": current, "pending": current, "pending_count": pending_count})


def _send_battery_warning(voltage: float, level: str, solar: dict) -> None:
    if not C.TWILIO_CONFIGURED or not C.TWILIO_TO or "XXXXXXXXX" in C.TWILIO_TO:
        log.error("BATERÍA %s (%.2fV) — sin WhatsApp configurado", level, voltage)
        return
    panel_w = solar.get("panel_power_w") or 0
    load_w  = compute_system_load(solar) or 4.0
    net_draw_w = max(0.1, load_w - panel_w)
    remaining_pct = max(0.0, (voltage - 10.5) / (12.6 - 10.5))
    hours_left = remaining_pct * 180 / net_draw_w
    eta_str = f"{hours_left:.1f} h" if hours_left < 24 else f"{hours_left/24:.1f} días"
    label = {"warning": "BAJA", "critical": "CRITICA", "emergency": "EMERGENCIA"}.get(level, level)
    action = (f"La estación se apagará en minutos si no llega sol (autonomía ~{eta_str})."
              if level == "emergency"
              else f"Revisar paneles y orientación; considerar carga manual (autonomía ~{eta_str}).")
    try:
        notify._wa_send(C.WA_TPL_BATTERY, notify._wa_vars(
            f"{voltage:.2f}", label, action, prefix_v2=False,
        ), log_ok=f"🔋 alerta batería {level} (V={voltage:.2f}) → WhatsApp enviado",
           recipients=C.TO_TECNICO)
    except Exception as exc:
        log.warning("falló envío alerta batería: %s", exc)


# ─── CSV histórico + power_history.json ──────────────────────────────────────

DATA_LOG_COLUMNS = [
    "timestamp_utc", "battery_voltage_v", "battery_current_a", "panel_voltage_v",
    "panel_power_w", "charge_state", "yield_today_kwh", "yield_total_kwh",
    "system_load_w", "cpu_temp_c", "ram_used_pct", "ram_used_mb", "ram_total_mb",
    "disk_used_pct", "signal_bars", "network_type", "alert_count_session", "last_rms",
]


def append_data_log(row_source: dict) -> None:
    """Agrega una fila al CSV y lo recorta si superó DATA_LOG_MAX_ROWS."""
    try:
        C.DATA_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        new_file = not C.DATA_LOG_PATH.exists()
        if not new_file:
            expected = ",".join(DATA_LOG_COLUMNS)
            try:
                first = C.DATA_LOG_PATH.open("r").readline().strip()
            except Exception:
                first = ""
            if first != expected:
                C.DATA_LOG_PATH.unlink(missing_ok=True)   # tmpfs: sin histórico que preservar
                new_file = True
        with C.DATA_LOG_PATH.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=DATA_LOG_COLUMNS, extrasaction="ignore")
            if new_file:
                w.writeheader()
            w.writerow({col: row_source.get(col) for col in DATA_LOG_COLUMNS})
        _trim_data_log()
    except Exception as exc:
        log.warning("No se pudo escribir CSV en %s: %s", C.DATA_LOG_PATH, exc)


def _trim_data_log() -> None:
    """El CSV vive en RAM: acotado o es una fuga (misma lección que F-03)."""
    try:
        lines = C.DATA_LOG_PATH.read_text().splitlines()
        if len(lines) - 1 > C.DATA_LOG_MAX_ROWS:
            keep = lines[0:1] + lines[-(C.DATA_LOG_MAX_ROWS):]
            C.DATA_LOG_PATH.write_text("\n".join(keep) + "\n")
    except Exception:
        pass


def upload_power_history() -> None:
    """Últimas 72 h del CSV en buckets de 30 min → sites/{site}/power_history.json.

    Buckets sin muestras se OMITEN (nunca null): los huecos son cómo el
    dashboard reconstruye uptime entre reboots (R-6.3). No rellenar.
    """
    if not C.DATA_LOG_PATH.exists():
        return
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=C.POWER_HISTORY_HOURS)
        buckets: dict = {}
        with C.DATA_LOG_PATH.open() as f:
            for row in csv.DictReader(f):
                try:
                    ts = datetime.fromisoformat(row["timestamp_utc"])
                    if ts < cutoff:
                        continue
                    key = int(ts.timestamp() // C.POWER_HISTORY_BUCKET_S)
                    b = buckets.setdefault(key, {"sys": [], "panel": [], "bat": []})
                    if row.get("system_load_w"):     b["sys"].append(float(row["system_load_w"]))
                    if row.get("panel_power_w"):     b["panel"].append(float(row["panel_power_w"]))
                    if row.get("battery_voltage_v"): b["bat"].append(float(row["battery_voltage_v"]))
                except (ValueError, KeyError):
                    continue
        history = []
        for key in sorted(buckets):
            b = buckets[key]
            ts_iso = datetime.fromtimestamp(key * C.POWER_HISTORY_BUCKET_S, tz=timezone.utc).isoformat()
            history.append({
                "ts":      ts_iso,
                "sys_w":   round(sum(b["sys"]) / len(b["sys"]), 2)     if b["sys"]   else None,
                "panel_w": round(sum(b["panel"]) / len(b["panel"]), 1) if b["panel"] else None,
                "bat_v":   round(sum(b["bat"]) / len(b["bat"]), 2)     if b["bat"]   else None,
            })
        storage.upload_json(storage.site_path("power_history.json"), {
            "schema_version": C.SCHEMA_VERSION,
            "site":           C.SITE,
            "device":         C.DEVICE_ID,
            "generated_utc":  datetime.now(timezone.utc).isoformat(),
            "bucket_s":       C.POWER_HISTORY_BUCKET_S,
            "window_h":       C.POWER_HISTORY_HOURS,
            "history":        history,
        })
    except Exception as exc:
        log.warning("No se pudo subir power_history.json: %s", exc)
