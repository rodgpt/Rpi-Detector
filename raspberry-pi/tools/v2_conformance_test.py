"""v2 contract conformance test (R-5.6) — no hardware, no Azure, no Twilio.

Drives the PRODUCTION emit code (oceankind package) in local-output mode to
produce a full v2 tree, then runs tools/validate_contract.py against it.
Exit 0 means: this device produces blobs the dashboard can consume.

    python3 raspberry-pi/tools/v2_conformance_test.py
"""
import csv
import json
import os
import subprocess
import sys
import tempfile
import uuid
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
OUT = Path(tempfile.mkdtemp(prefix="oceankind_v2_"))
SPOOL = Path(tempfile.mkdtemp(prefix="oceankind_spool_"))
STATE = Path(tempfile.mkdtemp(prefix="oceankind_state_"))
CSVLOG = STATE / "data_log.csv"

os.environ.update({
    "OCEANKIND_ALLOW_NO_TWILIO": "1",
    "OCEANKIND_OUTPUT_DIR": str(OUT),
    "OCEANKIND_SITE": "punta_norte",
    "OCEANKIND_DEVICE_ID": "Rpi_punta_norte",
    "OCEANKIND_SENSOR_LOCATION": "Punta Norte",
    "OCEANKIND_SENSOR_LAT": "-33.10",
    "OCEANKIND_SENSOR_LON": "-71.70",
    "OCEANKIND_EVENT_SPOOL_DIR": str(SPOOL),
    "OCEANKIND_STATE_DIR": str(STATE),
    "OCEANKIND_DATA_LOG": str(CSVLOG),
})
sys.path.insert(0, str(REPO / "raspberry-pi" / "src"))
import numpy as np                              # noqa: E402
from oceankind import config as C               # noqa: E402
from oceankind import health, main, storage, telemetry  # noqa: E402

assert C.SCHEMA_VERSION == 2 and C.STORAGE_ENABLED and C.SITE == "punta_norte"
C.validate_startup_config()

# ── tree scaffolding: site registry + aux stubs ──────────────────────────────
storage.publish_site_registry()
storage.publish_site_registry()   # idempotente: no debe duplicar el sitio
storage.ensure_aux_blobs()

reg = json.loads((OUT / "_sites.json").read_text())
assert [s["id"] for s in reg["sites"]] == ["punta_norte"], "registry merge not idempotent"

# ── a real clip ──────────────────────────────────────────────────────────────
fs = C.SAMPLE_RATE
t = np.arange(int(C.CAPTURE_SECONDS * fs)) / fs
tone = (0.3 * np.sin(2 * np.pi * 120 * t) * 32000).astype(np.int16)
clip = np.column_stack([tone, tone])

now = datetime.now(timezone.utc)

# 1. notified vessel event, clip uploaded first (F-13 path)
eid = str(uuid.uuid4())
event_rel, clip_rel = storage.event_rel_paths(now, eid)
assert storage.upload_bytes(clip_rel, storage.wav_bytes(clip), "audio/wav")
storage.write_event(storage.build_event(eid, now.isoformat(), "vessel", "psd_tonal", 0.8,
                                        False, 0.0847, -21.4, clip_rel, True,
                                        {"decided_by": "psd_tonal", "proba": 0.8}), event_rel)

# 2. suppressed event: recorded, no notification, no clip (D-008)
eid2 = str(uuid.uuid4())
event_rel2, clip_rel2 = storage.event_rel_paths(now - timedelta(minutes=9), eid2)
storage.write_event(storage.build_event(eid2, (now - timedelta(minutes=9)).isoformat(), "vessel",
                                        "psd_tonal", 1.0, True, 0.09, -20.0, clip_rel2, False,
                                        {"decided_by": "psd_tonal"}), event_rel2)

# 3. rms-fallback event: event_type unknown, rms as the 0..1 score
eid3 = str(uuid.uuid4())
event_rel3, clip_rel3 = storage.event_rel_paths(now - timedelta(minutes=5), eid3)
storage.write_event(storage.build_event(eid3, (now - timedelta(minutes=5)).isoformat(), "unknown",
                                        "rms", 0.12, False, 0.12, -18.0, clip_rel3, False,
                                        {"decided_by": "rms_fallback"}), event_rel3)

# 4. spool path (R-5.3): spool an event, then drain it into the tree
eid4 = str(uuid.uuid4())
event_rel4, clip_rel4 = storage.event_rel_paths(now - timedelta(minutes=2), eid4)
storage.spool_event(storage.build_event(eid4, (now - timedelta(minutes=2)).isoformat(), "vessel",
                                        "psd_tonal", 0.6, False, 0.05, -25.0, clip_rel4, False, {}),
                    event_rel4)
assert storage.event_spool_len() == 1
storage.drain_event_spool()
assert storage.event_spool_len() == 0, "spool did not drain"
assert (OUT / event_rel4).exists(), "drained event missing from tree"

# ── status.json through the real builder (modem/solar absent → nulls) ────────
health.mark_capture_started()
health.record_frames(int(5 * fs))
main.upload_status(now - timedelta(hours=2), 2, 0.0142)

# ── power_history.json from a synthetic telemetry CSV ────────────────────────
CSVLOG.unlink(missing_ok=True)   # upload_status appended a live row; test wants a known set
with CSVLOG.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=telemetry.DATA_LOG_COLUMNS)
    w.writeheader()
    for i in range(12):
        ts = now - timedelta(minutes=30 * (13 - i))
        if i == 6:
            continue   # hueco deliberado: los buckets omitidos son señal (R-6.3)
        w.writerow({"timestamp_utc": ts.isoformat(), "battery_voltage_v": 12.8,
                    "panel_power_w": 25 + i, "system_load_w": 3.4})
telemetry.upload_power_history()

# ── the verdict: the contract validator itself ───────────────────────────────
print(f"\ntree at {OUT}\n")
res = subprocess.run([sys.executable, str(REPO / "tools" / "validate_contract.py"), str(OUT)])
if res.returncode == 0:
    print("\nV2 CONFORMANCE: PASS")
sys.exit(res.returncode)
