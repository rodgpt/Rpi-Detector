"""Phase 3 acceptance — the detector registry (D-014, R-3.x).

Done-when, per PROGRESS: a synthetic tonal input fires the vessel detector, a
synthetic impulse fires the fallback path, both labelled correctly in the
output — plus the harness guarantees: ordered multi-detector chain, one typed
event per detection, no event lost, a requested-but-unloadable detector is a
HEALTH event, and the interface accepts an event type no detector produces
today (R-3.1).

ml_mfcc science note: whether model.joblib fires on a synthetic impulse is the
client's question (F-21, client deps 2/3) — here we assert only that the
harness loads and runs it when its deps exist, and fails LOUDLY when they
don't. Skips gracefully on machines without librosa/sklearn.

    python3 raspberry-pi/tools/registry_test.py
"""
import json
import os
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
OUT = Path(tempfile.mkdtemp(prefix="ok_reg_out_"))

os.environ.update({
    "OCEANKIND_ALLOW_NO_TWILIO": "1",
    "OCEANKIND_OUTPUT_DIR": str(OUT),
    "OCEANKIND_SITE": "banco",
    "OCEANKIND_DEVICE_ID": "Rpi_bench",
    "OCEANKIND_SENSOR_LOCATION": "Banco",
    "OCEANKIND_SENSOR_LAT": "-33.0",
    "OCEANKIND_SENSOR_LON": "-71.0",
    "OCEANKIND_STATE_DIR": tempfile.mkdtemp(prefix="ok_reg_state_"),
    "OCEANKIND_ALERT_COOLDOWN_S": "600",
    "OCEANKIND_ML_MODEL_PATH": str(REPO / "raspberry-pi" / "models" / "model.joblib"),
})
sys.path.insert(0, str(REPO / "raspberry-pi" / "src"))
import numpy as np                                     # noqa: E402
from oceankind import config as C                      # noqa: E402
from oceankind import detectors as registry            # noqa: E402
from oceankind import health, pipeline                 # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name} {detail}")
    if not cond:
        FAILURES.append(name)


fs = C.SAMPLE_RATE
t = np.arange(int(5 * fs)) / fs
rng = np.random.default_rng(7)


def stereo(mono):
    pcm = (np.clip(mono, -1, 1) * 32000).astype(np.int16)
    return np.column_stack([pcm, pcm])


tonal = stereo(0.30 * np.sin(2 * np.pi * 120 * t) + 0.25 * np.sin(2 * np.pi * 240 * t)
               + 0.01 * rng.standard_normal(len(t)))
impulse_mono = 0.005 * rng.standard_normal(len(t))
impulse_mono[fs * 2: fs * 2 + fs // 4] += 0.9 * rng.standard_normal(fs // 4)
impulse = stereo(impulse_mono)

from oceankind.capture import rms_and_peak             # noqa: E402
rms_tonal, _ = rms_and_peak(tonal)
rms_imp, _ = rms_and_peak(impulse)

print("1. acceptance: tonal fires the vessel detector, labelled")
cfg = dict(C.CONFIG.snapshot())
cfg["detection_mode"] = "psd"
dets = registry.run(fs, tonal, cfg, rms_tonal)
check("vessel, score 1.0, label MOTOR",
      len(dets) == 1 and dets[0]["type"] == "vessel" and dets[0]["score"] == 1.0
      and dets[0]["label"] == "MOTOR" and dets[0]["detector"] == "psd_tonal", f"({dets})")

print("2. acceptance: impulse does NOT fool the vessel detector; rms catches it")
check("psd stays silent on impulse (F-21 arithmetic)",
      registry.run(fs, impulse, cfg, rms_imp) == [])
cfg["detection_mode"] = "rms"
dets = registry.run(fs, impulse, cfg, rms_imp)
check("rms fires typed 'unknown' on the impulse",
      len(dets) == 1 and dets[0]["type"] == "unknown" and dets[0]["detector"] == "rms",
      f"(rms={rms_imp:.3f})")

print("3. ordered multi-detector chain -> one typed event each, through the real pipeline")
registry.DETECTORS_ENV[:] = ["psd_tonal", "rms"]
dets = registry.run(fs, tonal, dict(cfg, detection_mode="psd"), rms_tonal)
check("both fire on loud tonal, registry order kept",
      [d["detector"] for d in dets] == ["psd_tonal", "rms"]
      and [d["type"] for d in dets] == ["vessel", "unknown"])

pipe = pipeline.Pipeline(iot_client=None)
pipe._process_clip(tonal)
jobs = []
while not pipe.transport_queue.empty():
    jobs.append(pipe.transport_queue.get_nowait())
for j in jobs:
    pipe._process_job(j)
events = [json.loads(p.read_text()) for p in sorted((OUT / "sites" / "banco" / "events").rglob("*.json"))]
check("two events from one clip", len(events) == 2, f"({len(events)})")
by_type = {e["event_type"]: e for e in events}
check("vessel notified with clip; unknown suppressed without",
      by_type["vessel"]["suppressed"] is False and by_type["vessel"]["clip"]["uploaded"] is True
      and by_type["unknown"]["suppressed"] is True and by_type["unknown"]["clip"]["uploaded"] is False)
check("distinct event_ids, same capture instant",
      by_type["vessel"]["event_id"] != by_type["unknown"]["event_id"]
      and by_type["vessel"]["captured_utc"] == by_type["unknown"]["captured_utc"])
check("decided_by stamped per detector",
      by_type["vessel"]["detector_meta"]["decided_by"] == "psd_tonal"
      and by_type["unknown"]["detector_meta"]["decided_by"] == "rms")

print("4. a requested detector that cannot load is a HEALTH event (F-02)")
registry.AVAILABLE["broken"] = ".does_not_exist"
registry.DETECTORS_ENV[:] = ["psd_tonal", "broken"]
dets = registry.run(fs, tonal, dict(cfg, detection_mode="psd"), rms_tonal)
check("chain keeps running without the broken one",
      len(dets) == 1 and dets[0]["detector"] == "psd_tonal")
h = health.build_health()
check("degraded_reason names the unloadable detector", "broken" in (h["degraded_reason"] or ""))
del registry.AVAILABLE["broken"]
registry._loaded.pop("broken", None)
registry._registry_errors.clear()
health.set_registry_error(None)

print("5. interface expresses ANY event type (R-3.1): plugin via register()")
stub = types.ModuleType("stub_detector")
stub.detect = lambda fs_, s, cfg_, rms_: {"type": "narwhal_click", "score": 0.9,
                                          "label": "NARVAL", "meta": {"stub": True}}
registry.register("stub", stub)
registry.DETECTORS_ENV[:] = ["stub"]
dets = registry.run(fs, impulse, dict(cfg, detection_mode="psd"), rms_imp)
check("unforeseen event type flows through untouched",
      len(dets) == 1 and dets[0]["type"] == "narwhal_click" and dets[0]["label"] == "NARVAL")
registry.DETECTORS_ENV[:] = []

print("6. ml_mfcc: loads and runs where its deps exist; loud where they don't")
registry.DETECTORS_ENV[:] = ["ml_mfcc"]
try:
    import librosa, joblib, sklearn  # noqa: F401,E401
    deps = True
except ImportError:
    deps = False
dets_t = registry.run(fs, tonal, dict(cfg, detection_mode="psd"), rms_tonal)
dets_i = registry.run(fs, impulse, dict(cfg, detection_mode="psd"), rms_imp)
h = health.build_health()
if deps and registry._loaded.get("ml_mfcc"):
    check("ml_mfcc runs without error", True)
    for name, d in (("tonal", dets_t), ("impulse", dets_i)):
        if d:
            check(f"ml_mfcc {name}: well-formed blast detection",
                  d[0]["type"] == "blast" and 0 <= d[0]["score"] <= 1)
        else:
            print(f"        info: ml_mfcc did not fire on {name} — that is the model's "
                  "call (client science, F-21/dep 2), not a harness failure")
else:
    check("missing deps/model surface as a health event, never silence",
          "ml_mfcc" in (h["degraded_reason"] or ""), f"(deps={deps})")
registry.DETECTORS_ENV[:] = []
health.set_registry_error(None)

print()
if FAILURES:
    print(f"FAILED: {FAILURES}")
    sys.exit(1)
print("REGISTRY TEST: ALL PASS")
