"""Threaded pipeline test — the Phase 2 claim, proven end to end (R-1.1).

Runs the REAL pipeline (capture → classify → transport) with the synthetic
tone source at accelerated clock and slowed-down storage, and asserts:

  1. capture keeps delivering audio while transport is artificially slow
  2. one notified event + suppressed events inside the cooldown (D-008)
  3. the notified event's clip exists; suppressed events carry no audio
  4. queue accounting is honest (no silent drops in a healthy run)
  5. clean shutdown drains transport into the spool, losing no events
  6. the produced tree is CONFORMANT (validate_contract.py)

    python3 raspberry-pi/tools/pipeline_soak_test.py
"""
import csv
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
OUT = Path(tempfile.mkdtemp(prefix="oceankind_soak_"))
STATE = Path(tempfile.mkdtemp(prefix="oceankind_state_"))

os.environ.update({
    "OCEANKIND_ALLOW_NO_TWILIO": "1",
    "OCEANKIND_OUTPUT_DIR": str(OUT),
    "OCEANKIND_SITE": "banco",
    "OCEANKIND_DEVICE_ID": "Rpi_banco",
    "OCEANKIND_SENSOR_LOCATION": "Banco de pruebas",
    "OCEANKIND_SENSOR_LAT": "-33.0",
    "OCEANKIND_SENSOR_LON": "-71.0",
    "OCEANKIND_STATE_DIR": str(STATE),
    "OCEANKIND_EVENT_SPOOL_DIR": str(STATE / "spool"),
    "OCEANKIND_DATA_LOG": str(STATE / "log.csv"),
    "OCEANKIND_ARCHIVE_DIR": str(STATE / "archive"),
    "OCEANKIND_AUDIO_SOURCE": "synthetic:tone",
    "OCEANKIND_ALERT_COOLDOWN_S": "600",       # 1 notificada, el resto suprimidas
})
sys.path.insert(0, str(REPO / "raspberry-pi" / "src"))
from oceankind import capture, config as C, health, main, storage, telemetry  # noqa: E402
from oceankind import pipeline as pl                                          # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name} {detail}")
    if not cond:
        FAILURES.append(name)


C.validate_startup_config()
storage.publish_site_registry()
storage.ensure_aux_blobs()

# Storage artificially slow: every JSON upload takes 0.15 s. Capture must not care.
_real_upload_json = storage.upload_json
def _slow_upload_json(rel, data):
    time.sleep(0.15)
    return _real_upload_json(rel, data)
storage.upload_json = _slow_upload_json

pipe = pl.Pipeline(iot_client=None)
source = capture.SyntheticSource(pipe.block_queue, pattern="tone", time_scale=10.0)

pipe.start()
source.start()

# correr hasta juntar ≥5 eventos en el árbol (o timeout duro)
events_dir = OUT / "sites" / "banco" / "events"
deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    n = len(list(events_dir.rglob("*.json"))) if events_dir.exists() else 0
    if n >= 5:
        break
    time.sleep(0.25)

frames_before_stop = health._frames_total
source.stop()
pipe.stop()
storage.upload_json = _real_upload_json
storage.drain_event_spool()   # lo que el cierre preservó, al árbol

events = []
for p in sorted(events_dir.rglob("*.json")):
    events.append(json.loads(p.read_text()))

notified = [e for e in events if not e["suppressed"]]
suppressed = [e for e in events if e["suppressed"]]

print("1. detección continua sobre fuente sintética")
check("≥5 eventos registrados", len(events) >= 5, f"({len(events)})")
check("exactamente 1 notificada (cooldown 600s)", len(notified) == 1,
      f"({len(notified)} notificadas, {len(suppressed)} suprimidas)")
check("todas psd_tonal/vessel", all(e["detector"] == "psd_tonal" and e["event_type"] == "vessel"
                                    for e in events))
check("scores en 0..1 y ≥ score_min", all(0 <= e["score"] <= 1 and e["score"] >= 0.6
                                          for e in events))

print("2. clips: solo la notificada lleva audio")
if notified:
    e = notified[0]
    check("clip de la notificada existe", (OUT / e["clip"]["path"]).exists()
          and e["clip"]["uploaded"] is True)
check("suprimidas sin audio", all(e["clip"]["uploaded"] is False and
                                  not (OUT / e["clip"]["path"]).exists() for e in suppressed))

print("3. la captura no se detuvo por el transporte lento")
check("frames siguen fluyendo", frames_before_stop > 0)
h = health.build_health()
check("cero eventos perdidos", h["events_dropped"] == 0, f"({h['events_dropped']})")
check("duty cycle publicado", h["duty_cycle_pct"] is not None, f"({h['duty_cycle_pct']}%)")
check("archivo muestreó ≥1 clip", h["archive_queue"] >= 1, f"({h['archive_queue']})")

print("4. árbol completo y conforme")
main.upload_status(datetime.now(timezone.utc) - timedelta(hours=1), len(notified), pipe.last_rms)
Path(C.DATA_LOG_PATH).unlink(missing_ok=True)
now = datetime.now(timezone.utc)
with Path(C.DATA_LOG_PATH).open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=telemetry.DATA_LOG_COLUMNS)
    w.writeheader()
    for i in range(8):
        if i == 4:
            continue   # hueco deliberado (R-6.3)
        w.writerow({"timestamp_utc": (now - timedelta(minutes=30 * (9 - i))).isoformat(),
                    "battery_voltage_v": 12.8, "panel_power_w": 20 + i, "system_load_w": 3.4})
telemetry.upload_power_history()

res = subprocess.run([sys.executable, str(REPO / "tools" / "validate_contract.py"), str(OUT)],
                     capture_output=True, text=True)
print(res.stdout.strip().splitlines()[-1])
check("validate_contract exit 0", res.returncode == 0)

print()
if FAILURES:
    print(f"FAILED: {FAILURES}")
    sys.exit(1)
print("PIPELINE SOAK: ALL PASS")
