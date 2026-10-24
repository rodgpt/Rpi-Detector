"""Fail-loud smoke test — no hardware, no Azure, no Twilio (R-9.4).

Exercises the pure-function surface of the oceankind package:
  1. detector: synthetic tonal clip scores high, impulse scores <= 0.2 (F-21)
  2. detector failure counting flips detector_ok and composes degraded_reason
  3. frame-based duty-cycle arithmetic
  4. health block always publishes every counter
  5. non-finite float sanitisation (R-4.6)
  6. decide(): every mode can genuinely fire (F-01)
  7. overlapping analysis windows (window_hop_s)
  8. signed remote config, contract-converged (F-10): accept/reject matrix
"""
import os
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

os.environ.update({
    "OCEANKIND_ALLOW_NO_TWILIO": "1",
    "OCEANKIND_STATE_DIR": tempfile.mkdtemp(prefix="ok_state_"),
})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from oceankind import config as C            # noqa: E402
from oceankind import detector, health, storage  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name} {detail}")
    if not cond:
        FAILURES.append(name)


def write_wav(path, data, fs=48000):
    pcm = (np.clip(data, -1, 1) * 32000).astype(np.int16)
    stereo = np.column_stack([pcm, pcm]).ravel()
    with wave.open(path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(fs)
        wf.writeframes(stereo.tobytes())


fs = 48000
t = np.arange(5 * fs) / fs
rng = np.random.default_rng(7)
tonal = 0.30 * np.sin(2 * np.pi * 120 * t) + 0.25 * np.sin(2 * np.pi * 240 * t) + 0.01 * rng.standard_normal(len(t))
impulse = 0.005 * rng.standard_normal(len(t))
impulse[fs * 2: fs * 2 + fs // 4] += 0.9 * rng.standard_normal(fs // 4)

with tempfile.TemporaryDirectory() as td:
    tonal_p, impulse_p = f"{td}/tonal.wav", f"{td}/impulse.wav"
    write_wav(tonal_p, tonal)
    write_wav(impulse_p, impulse)
    r_tonal = detector.classify_clip(tonal_p)
    r_imp = detector.classify_clip(impulse_p)

print("1. detector")
check("tonal clip fires", bool(r_tonal) and r_tonal["proba"] >= C.CONFIG.snapshot()["score_min"] and r_tonal["pred"] == 1,
      f"(proba={r_tonal.get('proba')}, label={r_tonal.get('label')})")
check("tonal label is DETECTION_LABEL", r_tonal.get("label") == C.DETECTION_LABEL, f"({C.DETECTION_LABEL})")
check("impulse cannot fire (F-21 arithmetic)", bool(r_imp) and r_imp["proba"] <= 0.2,
      f"(proba={r_imp.get('proba')})")

print("2. detector_ok / failure accounting")
check("detector_ok true after successes", health.detector_ok())
for _ in range(C.DETECTOR_FAIL_LIMIT):
    detector.classify_clip("/nonexistent/clip.wav")
check("detector_ok false after consecutive failures", not health.detector_ok())
h = health.build_health()
check("degraded_reason names the detector", h["detector_ok"] is False and "clasificador" in (h["degraded_reason"] or ""))
check("bench mode is a boolean, NOT a degradation", h["alerts_enabled"] is False
      and "Twilio" not in (h["degraded_reason"] or ""))
health.record_classify_result(True)
check("recovery resets", health.detector_ok() and health.build_health()["detector_ok"] is True)

print("2b. per-detector accounting (D-014): one detector failing cannot hide behind another")
health.set_loaded_detectors(["psd_tonal", "ml_mfcc"])
for _ in range(C.DETECTOR_FAIL_LIMIT):
    health.record_classify_result(True, detector="psd_tonal")          # el primero va bien…
    health.record_classify_result(False, "boom", detector="ml_mfcc")   # …el segundo muere en cada clip
h = health.build_health()
check("second detector failing flips detector_ok", h["detector_ok"] is False)
check("reason names the failing detector only", "ml_mfcc" in (h["degraded_reason"] or "")
      and "psd_tonal (" not in (h["degraded_reason"] or ""), f"({h['degraded_reason']})")
health.record_classify_result(True, detector="ml_mfcc")
check("per-detector recovery", health.detector_ok())
health.set_loaded_detectors([])
h = health.build_health()
check("zero detectors loaded -> detector_ok false (contract)", h["detector_ok"] is False
      and "ningún detector" in (h["degraded_reason"] or ""))
health.set_loaded_detectors(["psd_tonal"])
check("alerts from the classify thread are QUEUED, not sent inline",
      health.flush_degraded_alerts() >= 1)   # the ml_mfcc escalation above queued one

print("3. duty cycle (frame-based)")
health.mark_capture_started()
import time as _time
_t0 = _time.monotonic()
while _time.monotonic() - _t0 < 2.0:
    _time.sleep(0.1)
    health.record_frames(int(0.1 * C.SAMPLE_RATE))
d = health.duty_cycle_pct()
check("duty_cycle_pct ~100 when frames keep up", d is not None and 80.0 <= d <= 100.0, f"({d})")
check("deaf_seconds small when healthy", health.deaf_seconds_total() < 1.0,
      f"({health.deaf_seconds_total()})")

print("3b. dead capture stream is LOUD, not 100 % (F-29)")
health.record_clip()
_saved = (C.DUTY_WINDOW_S, C.CAPTURE_STALL_S)
C.DUTY_WINDOW_S, C.CAPTURE_STALL_S = 1.1, 0.8   # window > 1 s: duty returns None at span <= 1 s
_time.sleep(1.3)                        # frames stop arriving; nothing calls record_frames
d = health.duty_cycle_pct()
check("duty_cycle drops to 0 when frames stop (window trimmed on READ)", d == 0.0, f"({d})")
check("capture_stalled after CAPTURE_STALL_S without a clip", health.capture_stalled())
h = health.build_health()
check("capture_ok false + degraded_reason says captura detenida",
      h["capture_ok"] is False and "captura detenida" in (h["degraded_reason"] or ""),
      f"({h['degraded_reason']})")
health.maybe_alert_capture()
check("one alert queued for the stall", health.flush_degraded_alerts() == 1)
health.maybe_alert_capture()
check("no duplicate alert while still stalled", health.flush_degraded_alerts() == 0)
C.DUTY_WINDOW_S, C.CAPTURE_STALL_S = _saved
health.record_frames(int(10 * C.SAMPLE_RATE))   # the stream is back: plenty of audio for the span
health.record_clip()
check("recovery: capture_ok true again", health.build_health()["capture_ok"] is True,
      f"({health.build_health()['degraded_reason']})")

print("3c. stale RMS history cannot keep audio_ok true forever")
_saved_w = C.AUDIO_HEALTH_WINDOW_S
for _ in range(5):
    health.record_rms(0.05)
C.AUDIO_HEALTH_WINDOW_S = 0.2
_time.sleep(0.3)
ok, peak, n = health.audio_health()
check("rms window trimmed on READ (stale samples gone)", n == 0 and peak == 0.0, f"(n={n})")
C.AUDIO_HEALTH_WINDOW_S = _saved_w

print("4. health block completeness")
h = health.build_health()
for key in ("detector_ok", "audio_ok", "capture_ok", "duty_cycle_pct", "deaf_seconds_total",
            "last_clip_age_s", "clips_dropped", "capture_overflows", "suppressed_count",
            "upload_backlog", "events_dropped", "wa_pending", "archive_queue",
            "alerts_enabled", "degraded_reason"):
    check(f"health.{key} present", key in h)

print("5. non-finite floats (R-4.6)")
s = storage.sanitize_for_json({"a": float("inf"), "b": [float("nan"), 1.5], "c": {"d": float("-inf")}})
check("inf/nan -> None", s == {"a": None, "b": [None, 1.5], "c": {"d": None}})

print("6. registry: every mode can genuinely fire (F-01, D-014)")
from oceankind import detectors as registry  # noqa: E402
from oceankind.detectors import psd_tonal as _pt  # noqa: E402


def _stereo(mono):
    pcm = (np.clip(mono, -1, 1) * 32000).astype(np.int16)
    return np.column_stack([pcm, pcm])


cfg = dict(C.CONFIG.snapshot())
tonal_clip, impulse_clip = _stereo(tonal), _stereo(impulse)

cfg["detection_mode"] = "psd"
dets = registry.run(48000, tonal_clip, cfg, rms=0.2)
check("psd mode fires vessel on tonal", len(dets) == 1 and dets[0]["type"] == "vessel"
      and dets[0]["decided_by"] == "psd_tonal", f"({dets})")

cfg["detection_mode"] = "rms"
dets = registry.run(48000, impulse_clip, cfg, rms=0.2)
check("rms mode fires on loud input", len(dets) == 1 and dets[0]["type"] == "unknown"
      and dets[0]["score"] == 0.2)

cfg["detection_mode"] = "auto"
_orig_analyze = _pt.analyze
_pt.analyze = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("clasificador roto"))
dets = registry.run(48000, impulse_clip, cfg, rms=0.2)
_pt.analyze = _orig_analyze
check("auto falls back to REAL rms when classifier dies",
      len(dets) == 1 and dets[0]["decided_by"] == "rms_fallback")
health.record_classify_result(True)   # limpiar el contador para lo que sigue

print("7. overlapping windows (window_hop_s)")
import queue as _queue  # noqa: E402
from oceankind import capture as _capture  # noqa: E402


def _run_assembler(hop_s: float, seconds: float) -> int:
    C.CONFIG.apply({"window_hop_s": hop_s})
    q = _queue.Queue(maxsize=10000)
    block = np.zeros((C.BLOCK_FRAMES, C.CHANNELS), dtype=np.int16)
    for _ in range(int(seconds * C.SAMPLE_RATE / C.BLOCK_FRAMES)):
        q.put_nowait(block)
    asm = _capture.ClipAssembler(q)
    clips = 0
    while True:
        clip = asm.next_clip(timeout=0.01)
        if clip is not None:
            clips += 1
            check_len = len(clip) == int(C.CAPTURE_SECONDS * C.SAMPLE_RATE)
            if not check_len:
                FAILURES.append("clip length wrong")
        elif q.empty():
            break
    return clips


n_nohop = _run_assembler(5.0, 15.0)
n_hop   = _run_assembler(2.5, 15.0)
check("hop=5.0: 15s -> 3 ventanas pegadas", n_nohop == 3, f"({n_nohop})")
check("hop=2.5: 15s -> 5 ventanas solapadas", n_hop == 5, f"({n_hop})")
clamped = C.CONFIG.apply({"window_hop_s": 0.1})   # bajo el mínimo → clamp a 1.0
check("hop clampeado a >=1.0", C.CONFIG.snapshot()["window_hop_s"] == 1.0, f"({clamped})")
C.CONFIG.apply({"window_hop_s": 5.0})

print("8. remote config: contract-converged verification (F-10)")
import hashlib as _hl
import hmac as _hm
import json as _json


def _signed_doc(key, cfg, version="2026-08-22-01", device_id=None, tamper=False):
    doc = {"schema_version": 2, "config_version": version, "site": "banco",
           "device_id": device_id, "issued_utc": "2026-08-22T14:00:00+00:00",
           "config": cfg}
    body = _json.dumps(doc, sort_keys=True, separators=(",", ":"))
    doc["signature"] = _hm.new(key.encode(), body.encode(), _hl.sha256).hexdigest()
    if tamper:
        doc["config"] = {**cfg, "score_min": 0.05}
    return doc


try:
    C.verify_remote_config(_signed_doc("k", {"score_min": 0.7}))
    check("no key -> whole doc rejected", False)
except C.ConfigRejected as e:
    check("no key -> whole doc rejected", "HMAC_KEY" in str(e))

C.CONFIG_HMAC_KEY = "test-key-123"
try:
    vals = C.verify_remote_config(_signed_doc("test-key-123", {"score_min": 0.7, "window_hop_s": 2.5}))
    check("valid signature over whole doc accepted", vals == {"score_min": 0.7, "window_hop_s": 2.5})
except C.ConfigRejected as e:
    check("valid signature over whole doc accepted", False, f"({e})")

for name, doc in (
    ("tampered doc rejected", _signed_doc("test-key-123", {"score_min": 0.7}, tamper=True)),
    ("wrong key rejected", _signed_doc("other-key", {"score_min": 0.7})),
    ("unknown config key rejects WHOLE doc", _signed_doc("test-key-123", {"score_mim": 0.7})),
    ("bad detection_mode rejected", _signed_doc("test-key-123", {"detection_mode": "ml"})),
    ("inverted PSD band rejected", _signed_doc("test-key-123", {"psd_f_min": 1500, "psd_f_max": 300})),
):
    try:
        C.verify_remote_config(doc)
        check(name, False)
    except C.ConfigRejected:
        check(name, True)

other = C.verify_remote_config(_signed_doc("test-key-123", {"score_min": 0.9}, device_id="Rpi_otra"))
check("doc for another device_id skipped, not rejected", other is None)
changes = C.CONFIG.apply({"detection_mode": "auto", "score_min": 0.99})
check("apply: mode switches, out-of-range clamped to 0.95",
      C.CONFIG.snapshot()["detection_mode"] == "auto"
      and C.CONFIG.snapshot()["score_min"] == 0.95, f"({changes})")
C.CONFIG.apply({"detection_mode": "psd", "score_min": 0.6})
C.CONFIG_HMAC_KEY = ""

print("9. watchdog: pings while threads beat, starves when one hangs (R-2.7)")
import socket as _socket  # noqa: E402
import time as _t2  # noqa: E402
from oceankind import watchdog as _wd  # noqa: E402

_sock_path = os.path.join(tempfile.mkdtemp(), "n.sock")
_rx = _socket.socket(_socket.AF_UNIX, _socket.SOCK_DGRAM)
_rx.bind(_sock_path)
_rx.settimeout(0.5)
os.environ["NOTIFY_SOCKET"] = _sock_path
check("arms with NOTIFY_SOCKET", _wd.arm() is True)

C.WATCHDOG_PING_S = 0.0            # pingear en cada tick para el test
health.beat("classify")
health.beat("transport")
_wd.tick()
try:
    check("ping WATCHDOG=1 sent while threads beat", _rx.recv(64) == b"WATCHDOG=1")
except TimeoutError:
    check("ping WATCHDOG=1 sent while threads beat", False, "(timeout)")

health._beats["classify"] = _t2.monotonic() - 9999   # hilo "colgado"
_wd._last_ping = 0.0
_wd.tick()
try:
    _rx.recv(64)
    check("ping WITHHELD when a thread hangs", False)
except TimeoutError:
    check("ping WITHHELD when a thread hangs", True)

health.beat("classify")            # el hilo "vuelve"
_wd._last_ping = 0.0
_wd.tick()
try:
    check("ping resumes on recovery", _rx.recv(64) == b"WATCHDOG=1")
except TimeoutError:
    check("ping resumes on recovery", False, "(timeout)")
_rx.close()
del os.environ["NOTIFY_SOCKET"]

print("10. startup validation")
check("bench escape hatch allows start", C.TWILIO_CONFIGURED is False and C.ALLOW_NO_TWILIO is True)
try:
    C.validate_startup_config()
    check("validate passes with ALLOW_NO_TWILIO (no backend, no storage)", True)
except SystemExit:
    check("validate passes with ALLOW_NO_TWILIO (no backend, no storage)", False)

# D-019: a unit that reaches the backend but cannot reach a human must declare
# itself a bench (OCEANKIND_BENCH=1) or refuse to start.
C.BACKEND_URL, C.DEVICE_KEY, C.SITE = "https://backend.example", "k", "banco"
try:
    C.validate_startup_config()
    check("ALLOW_NO_TWILIO + BACKEND_URL without OCEANKIND_BENCH refuses to start", False)
except SystemExit:
    check("ALLOW_NO_TWILIO + BACKEND_URL without OCEANKIND_BENCH refuses to start", True)
C.BENCH = True
try:
    C.validate_startup_config()
    check("…and starts once OCEANKIND_BENCH=1 is declared", True)
except SystemExit:
    check("…and starts once OCEANKIND_BENCH=1 is declared", False)
C.BENCH, C.BACKEND_URL, C.DEVICE_KEY, C.SITE = False, "", "", ""

print("11. upload backlog is a degradation after UPLOAD_BACKLOG_ALERT_TICKS heartbeats")
_saved_spool = C.EVENT_SPOOL_DIR
C.EVENT_SPOOL_DIR = Path(tempfile.mkdtemp(prefix="ok_spool_"))
C.EVENT_SPOOL_DIR.mkdir(exist_ok=True)
(C.EVENT_SPOOL_DIR / "stuck.json").write_text('{"rel_path":"x","event":{}}')
for _ in range(C.UPLOAD_BACKLOG_ALERT_TICKS):
    health.note_upload_backlog()
h = health.build_health()
check("degraded_reason names the failing uploads", "subidas" in (h["degraded_reason"] or ""),
      f"({h['degraded_reason']})")
check("exactly one alert queued", health.flush_degraded_alerts() == 1)
(C.EVENT_SPOOL_DIR / "stuck.json").unlink()
health.note_upload_backlog()
check("drained spool clears the reason", "subidas" not in (health.build_health()["degraded_reason"] or ""))
C.EVENT_SPOOL_DIR = _saved_spool

print("12. spool drain: unreadable files are COUNTED, not silently deleted")
from oceankind import storage as _storage  # noqa: E402
C.EVENT_SPOOL_DIR = Path(tempfile.mkdtemp(prefix="ok_spool2_"))
C.EVENT_SPOOL_DIR.mkdir(exist_ok=True)
(C.EVENT_SPOOL_DIR / "a.json").write_text("not json")
(C.EVENT_SPOOL_DIR / "b.json").write_text('{"event": {}}')          # falta rel_path
_before = health.build_health()["events_dropped"]
_storage.drain_event_spool()
check("2 unreadable spool files counted as dropped",
      health.build_health()["events_dropped"] == _before + 2 and not list(C.EVENT_SPOOL_DIR.glob("*.json")))
C.EVENT_SPOOL_DIR = _saved_spool

print()
if FAILURES:
    print(f"FAILED: {FAILURES}")
    sys.exit(1)
print("ALL PASS")
