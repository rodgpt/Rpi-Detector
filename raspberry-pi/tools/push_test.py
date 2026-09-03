"""Event-push test (contract §Event upload) — runs a fake backend locally.

Asserts, against a real HTTP server enforcing the spec:
  1. the POST: path, headers, body BYTE-IDENTICAL to the blob
  2. idempotent re-post is plain success (202, no special handling)
  3. backend down -> spooled; recovery -> whole spool drains next heartbeat
  4. 400/403 -> loud, counted, never retried; event stays in the blob
  5. 401 -> push stops entirely, surfaced in health.degraded_reason
  6. the invariant: blob written regardless of push outcome, via the real
     transport worker path (_process_job)
  7. URL without device key refuses to start (R-8.1)

    python3 raspberry-pi/tools/push_test.py
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
OUT = Path(tempfile.mkdtemp(prefix="ok_push_out_"))
STATE = Path(tempfile.mkdtemp(prefix="ok_push_state_"))

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name} {detail}")
    if not cond:
        FAILURES.append(name)


# ── fake backend ─────────────────────────────────────────────────────────────
class Fake(BaseHTTPRequestHandler):
    mode = "ok"                  # ok | down | auth | badreq | forbidden
    requests: list = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        Fake.requests.append({"path": self.path,
                              "device_id": self.headers.get("X-Device-Id"),
                              "device_key": self.headers.get("X-Device-Key"),
                              "content_type": self.headers.get("Content-Type"),
                              "body": body})
        code = {"ok": 202, "down": 503, "auth": 401,
                "badreq": 400, "forbidden": 403}[Fake.mode]
        self.send_response(code)
        self.end_headers()

    def log_message(self, *a):    # silencio
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}"

os.environ.update({
    "OCEANKIND_ALLOW_NO_TWILIO": "1",
    "OCEANKIND_OUTPUT_DIR": str(OUT),
    "OCEANKIND_SITE": "banco",
    "OCEANKIND_DEVICE_ID": "Rpi_bench",
    "OCEANKIND_SENSOR_LOCATION": "Banco",
    "OCEANKIND_SENSOR_LAT": "-33.0",
    "OCEANKIND_SENSOR_LON": "-71.0",
    "OCEANKIND_STATE_DIR": str(STATE),
    "OCEANKIND_BACKEND_URL": BASE,
    "OCEANKIND_DEVICE_KEY": "test-device-key-abc",
    "OCEANKIND_BACKEND_TIMEOUT_S": "5",
})
sys.path.insert(0, str(REPO / "raspberry-pi" / "src"))
import numpy as np                               # noqa: E402
from oceankind import config as C                # noqa: E402
from oceankind import health, pipeline, push, storage  # noqa: E402

C.validate_startup_config()


def make_event():
    now = datetime.now(timezone.utc)
    eid = str(uuid.uuid4())
    event_rel, clip_rel = storage.event_rel_paths(now, eid)
    return storage.build_event(eid, now.isoformat(), "vessel", "psd_tonal", 0.8,
                               False, 0.0847, -21.4, clip_rel, False,
                               {"decided_by": "psd_tonal"}), event_rel


print("1. the POST itself")
ev, ev_rel = make_event()
storage.write_event(ev, ev_rel)          # el blob primero, como en producción
push.push_event(ev)
check("one request", len(Fake.requests) == 1)
r = Fake.requests[-1]
check("path /api/devices/events", r["path"] == "/api/devices/events")
check("X-Device-Id header", r["device_id"] == "Rpi_bench")
check("X-Device-Key header", r["device_key"] == "test-device-key-abc")
check("Content-Type json", r["content_type"] == "application/json")
check("body byte-identical to the blob", r["body"] == (OUT / ev_rel).read_bytes())
check("captured_utc carries UTC offset", "+00:00" in json.loads(r["body"])["captured_utc"])
check("nothing spooled on 202", push.spool_len() == 0)

print("2. idempotent re-post")
push.push_event(ev)
check("re-post is plain success", len(Fake.requests) == 2 and push.spool_len() == 0)

print("3. backend down -> spool -> drain on recovery")
Fake.mode = "down"
ev2, ev2_rel = make_event()
storage.write_event(ev2, ev2_rel)
push.push_event(ev2)
check("spooled on 503", push.spool_len() == 1)
ev3, _ = make_event()
push.push_event(ev3)
check("second event spools too (down is sticky this cycle)", push.spool_len() == 2)
n_before = len(Fake.requests)
Fake.mode = "ok"
push.drain_push_spool()
check("whole spool drained next heartbeat", push.spool_len() == 0
      and len(Fake.requests) == n_before + 2)

print("4. terminal rejections: loud, counted, not retried")
Fake.mode = "badreq"
push.push_event(make_event()[0])
Fake.mode = "forbidden"
push.push_event(make_event()[0])
h = health.build_health()
check("400/403 counted in push_rejected", h["push_rejected"] == 2, f"({h['push_rejected']})")
check("terminal rejections never spooled", push.spool_len() == 0)

print("5. 401 stops the push entirely, as a health event")
Fake.mode = "auth"
push.push_event(make_event()[0])
check("auth_failed latched", push.auth_failed())
check("401 event kept in spool for after the fix", push.spool_len() == 1)
n_before = len(Fake.requests)
push.push_event(make_event()[0])
push.drain_push_spool()
check("no further requests while revoked", len(Fake.requests) == n_before)
h = health.build_health()
check("degraded_reason names the 401", "401" in (h["degraded_reason"] or ""))
check("push_backlog visible", h["push_backlog"] == 2, f"({h['push_backlog']})")
with push._lock:                     # reset para las secciones siguientes
    push._auth_failed = False
for f in C.PUSH_SPOOL_DIR.glob("*.json"):
    f.unlink()

print("6. the invariant, through the real transport worker")
Fake.mode = "ok"
n_before = len(Fake.requests)
pipe = pipeline.Pipeline(iot_client=None)
tone = (0.3 * np.sin(2 * np.pi * 120 * np.arange(48000) / 48000) * 32000).astype(np.int16)
clip = np.column_stack([tone, tone])


def make_job(suppressed=False):
    now = datetime.now(timezone.utc)
    eid = str(uuid.uuid4())
    event_rel, clip_rel = storage.event_rel_paths(now, eid)
    return {"event_id": eid, "captured_iso": now.isoformat(),
            "event_rel": event_rel, "clip_rel": clip_rel,
            "clip": None if suppressed else clip, "suppressed": suppressed,
            "rms": 0.21, "peak_db": -13.5,
            "detection": {"type": "vessel", "score": 1.0, "label": "MOTOR",
                          "meta": {"pred": 1, "proba": 1.0, "label": "MOTOR"},
                          "detector": "psd_tonal", "decided_by": "psd_tonal"}}


job = make_job()
pipe._process_job(job)
check("blob written", (OUT / job["event_rel"]).exists())
check("clip uploaded", (OUT / job["clip_rel"]).exists())
check("pushed once", len(Fake.requests) == n_before + 1)
check("push body == blob bytes", Fake.requests[-1]["body"] == (OUT / job["event_rel"]).read_bytes())

Fake.mode = "down"
job2 = make_job(suppressed=True)
pipe._process_job(job2)
check("backend down: blob STILL written (the invariant)", (OUT / job2["event_rel"]).exists())
check("backend down: push spooled, nothing lost", push.spool_len() == 1)
check("suppressed events push too", json.loads((OUT / job2["event_rel"]).read_text())["suppressed"] is True)

print("7. URL without device key refuses to start (R-8.1)")
res = subprocess.run([sys.executable, "-c", (
    "import os,sys; os.environ.update({'OCEANKIND_ALLOW_NO_TWILIO':'1',"
    f"'OCEANKIND_BACKEND_URL':'{BASE}','OCEANKIND_DEVICE_KEY':''}});"
    f"sys.path.insert(0,{str(REPO / 'raspberry-pi' / 'src')!r});"
    "from oceankind import config as C\n"
    "try:\n C.validate_startup_config(); print('STARTED')\n"
    "except SystemExit: print('REFUSED')"
)], capture_output=True, text=True)
check("refuses to start", "REFUSED" in res.stdout, f"({res.stdout.strip()!r})")

server.shutdown()
print()
if FAILURES:
    print(f"FAILED: {FAILURES}")
    sys.exit(1)
print("PUSH TEST: ALL PASS")
