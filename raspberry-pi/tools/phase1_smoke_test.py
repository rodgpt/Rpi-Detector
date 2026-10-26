"""Fail-loud smoke test — no hardware, no Azure, no Twilio (R-9.4).

Exercises the pure-function surface of the oceankind package:
  1. detector: synthetic tonal clip scores high, impulse scores <= 0.2 (F-21)
  2. detector failure counting flips detector_ok and composes degraded_reason
  3. frame-based duty-cycle arithmetic
  4. health block always publishes every counter
  5. non-finite float sanitisation (R-4.6)
  6. decide(): every mode can genuinely fire (F-01)
  7. overlapping analysis windows (window_hop_s)
  8. signed remote config, contract-converged (F-10, D-020): accept/reject matrix
  9. watchdog; 10. startup validation (D-020); 11-12. spool; 13. OTA trigger (D-021)
"""
import os
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

_INSTALL = tempfile.mkdtemp(prefix="ok_install_")
os.environ.update({
    "OCEANKIND_STATE_DIR": tempfile.mkdtemp(prefix="ok_state_"),
    "OCEANKIND_INSTALL_DIR": _INSTALL,
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
check("no alerting state in health any more (D-020: the backend alerts)",
      "alerts_enabled" not in h and "wa_pending" not in h and "Twilio" not in (h["degraded_reason"] or ""))
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
            "upload_backlog", "events_dropped", "archive_queue", "degraded_reason"):
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

print("8. remote config: contract-converged verification (F-10, D-020 per-device key)")
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


# One secret, two derived keys — the contract's exact formulas (§Provisioning secret)
SECRET = "test-secret-0123456789abcdef"
_auth, _cfgkey = C.derive_keys(SECRET)
check("derive_keys: auth = HMAC(secret, 'oceankind:auth:v1')",
      _auth == _hm.new(SECRET.encode(), b"oceankind:auth:v1", _hl.sha256).hexdigest())
check("derive_keys: config = HMAC(secret, 'oceankind:config:v1')",
      _cfgkey == _hm.new(SECRET.encode(), b"oceankind:config:v1", _hl.sha256).hexdigest())
check("the two keys differ (wire key never signs config)", _auth != _cfgkey and len(_auth) == 64)

try:
    C.verify_remote_config(_signed_doc(_cfgkey, {"score_min": 0.7}))
    check("no secret -> whole doc rejected", False)
except C.ConfigRejected as e:
    check("no secret -> whole doc rejected", "DEVICE_SECRET" in str(e))

C.CONFIG_KEY = _cfgkey
try:
    vals = C.verify_remote_config(_signed_doc(_cfgkey, {"score_min": 0.7, "window_hop_s": 2.5}))
    check("valid signature over whole doc accepted", vals == {"score_min": 0.7, "window_hop_s": 2.5})
except C.ConfigRejected as e:
    check("valid signature over whole doc accepted", False, f"({e})")

for name, doc in (
    ("tampered doc rejected", _signed_doc(_cfgkey, {"score_min": 0.7}, tamper=True)),
    ("wrong key rejected", _signed_doc("other-key", {"score_min": 0.7})),
    ("signed with the AUTH key rejected (keys are not interchangeable)", _signed_doc(_auth, {"score_min": 0.7})),
    ("unknown config key rejects WHOLE doc", _signed_doc(_cfgkey, {"score_mim": 0.7})),
    ("bad detection_mode rejected", _signed_doc(_cfgkey, {"detection_mode": "ml"})),
    ("inverted PSD band rejected", _signed_doc(_cfgkey, {"psd_f_min": 1500, "psd_f_max": 300})),
    ("release that is not a tag rejected (D-021)", _signed_doc(_cfgkey, {"release": "main; rm -rf /"})),
    ("non-boolean hold rejected (D-021)", _signed_doc(_cfgkey, {"hold": "yes"})),
):
    try:
        C.verify_remote_config(doc)
        check(name, False)
    except C.ConfigRejected:
        check(name, True)

vals = C.verify_remote_config(_signed_doc(_cfgkey, {"score_min": 0.7, "release": "v2.2.0", "hold": False}))
check("release/hold accepted inside config (D-021)", vals["release"] == "v2.2.0" and vals["hold"] is False)
C.CONFIG.apply(vals)
check("release/hold never reach RuntimeConfig", "release" not in C.CONFIG.snapshot()
      and not hasattr(C.CONFIG, "release"))
other = C.verify_remote_config(_signed_doc(_cfgkey, {"score_min": 0.9}, device_id="Rpi_otra"))
check("doc for another device_id skipped, not rejected", other is None)
changes = C.CONFIG.apply({"detection_mode": "auto", "score_min": 0.99})
check("apply: mode switches, out-of-range clamped to 0.95",
      C.CONFIG.snapshot()["detection_mode"] == "auto"
      and C.CONFIG.snapshot()["score_min"] == 0.95, f"({changes})")
C.CONFIG.apply({"detection_mode": "psd", "score_min": 0.6})
C.CONFIG_KEY = ""

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

print("10. startup validation (D-020: one secret, no Twilio anywhere on the device)")
check("no Twilio/bench settings exist any more",
      not any(hasattr(C, n) for n in ("TWILIO_CONFIGURED", "ALLOW_NO_TWILIO", "BENCH",
                                      "CONFIG_HMAC_KEY", "DEVICE_KEY", "IOTHUB_ENABLED")))
try:
    C.validate_startup_config()
    check("validate passes with no backend and no storage (bench by hand)", True)
except SystemExit:
    check("validate passes with no backend and no storage (bench by hand)", False)


def _refuses():
    try:
        C.validate_startup_config()
        return False
    except SystemExit:
        return True


C.BACKEND_URL, C.SITE = "https://backend.example", "banco"
C.DEVICE_SECRET = ""
check("BACKEND_URL without DEVICE_SECRET refuses to start", _refuses())
C.DEVICE_SECRET = SECRET
check("…and starts with the secret", not _refuses())
# A unit provisioned before D-020: old key present, no secret. Must refuse and
# name the fix — it could authenticate but never verify a config or a release.
C.DEVICE_SECRET, C.LEGACY_DEVICE_KEY = "", "old-device-key"
check("legacy OCEANKIND_DEVICE_KEY without secret refuses to start (re-provision)", _refuses())
C.BACKEND_URL = ""
check("…even with no backend URL (the variable itself is the signal)", _refuses())
C.LEGACY_DEVICE_KEY, C.SITE = "", ""

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

print("13. OTA trigger converges to the ASSIGNED release and nothing else (D-021)")
from oceankind import ota as _ota  # noqa: E402
import subprocess as _sp  # noqa: E402

_launched = []
_real_popen = _sp.Popen
_sp.Popen = lambda argv, **kw: _launched.append(argv)   # the script is not run here
blk = _ota.status_block()
check("ota block has every key, all null/false before anything happened",
      set(blk) == {"installed_sha", "installed_release", "failed_sha", "failed_release",
                   "assigned_release", "hold", "in_progress"}
      and blk["installed_sha"] is None and blk["in_progress"] is False)
check("no assignment -> nothing launched", _ota.maybe_trigger() == "none" and not _launched)
_ota.record_assignment("v2.2.0", True)
check("hold -> nothing launched even with a release", _ota.maybe_trigger() == "hold" and not _launched)
check("assignment persisted for the daily timer",
      (Path(_INSTALL) / ".assigned_release").read_text().split() == ["v2.2.0", "1"])
_ota.record_assignment("v2.2.0", False)
(Path(_INSTALL) / ".installed_release").write_text("v2.2.0\n")
check("already on the assigned release -> current", _ota.maybe_trigger() == "current" and not _launched)
(Path(_INSTALL) / ".installed_release").write_text("v2.1.0\n")
(Path(_INSTALL) / ".ota_failed_release").write_text("v2.2.0\n")
check("release that already failed here -> never relaunched", _ota.maybe_trigger() == "failed" and not _launched)
(Path(_INSTALL) / ".ota_failed_release").unlink()
check("no script installed -> loud, not launched", _ota.maybe_trigger() == "no-script" and not _launched)
(Path(_INSTALL) / "update_oceankind.sh").write_text("#!/bin/bash\nexit 0\n")
check("launched with --ref <tag> as argv, no shell",
      _ota.maybe_trigger() == "launched" and _launched
      and _launched[-1][1:] == [str(C.OTA_SCRIPT), "--ref", "v2.2.0"])
check("same assignment inside the cooldown is not relaunched", _ota.maybe_trigger() == "cooldown" and len(_launched) == 1)
# The OTA restarts this very service, so the cooldown must survive a restart: it
# is persisted and reloaded at import. Simulate the restart by forgetting the
# in-memory copy and reloading (F-38: without this the bench looped every 7 s).
_ota._last_trigger_mono, _ota._last_trigger_ref = 0.0, None
_ota._load_last_trigger()
check("cooldown persisted across a service restart", _ota.maybe_trigger() == "cooldown" and len(_launched) == 1)
# Under systemd the launch is a separate unit, never a child (F-38). A failing
# `systemctl start` is an error and must NOT fall back to Popen.
_ota.UNDER_SYSTEMD = True
_ota._last_trigger_mono = 0.0
_ran = []
_real_run = _sp.run
_sp.run = lambda argv, **kw: (_ran.append(argv), type("R", (), {"returncode": 1, "stderr": "no unit", "stdout": ""})())[1]
check("systemd: `systemctl start oceankind-ota@<tag>` is what gets called",
      _ota.maybe_trigger() == "error" and _ran and _ran[-1][:4] == ["sudo", "-n", "systemctl", "start"]
      and _ran[-1][-1] == "oceankind-ota@v2.2.0.service" and len(_launched) == 1)
_sp.run = lambda argv, **kw: (_ran.append(argv), type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})())[1]
_ota._last_trigger_mono = 0.0
check("systemd: success reports launched without any Popen",
      _ota.maybe_trigger() == "launched" and len(_launched) == 1)
_sp.run = _real_run
_ota.UNDER_SYSTEMD = False
_ota._last_trigger_mono = 0.0
C.OTA_LOCK_DIR.mkdir(parents=True, exist_ok=True)
(C.OTA_LOCK_DIR / "pid").write_text(str(os.getpid()))
check("another OTA holding the lock -> busy", _ota.maybe_trigger() == "busy" and len(_launched) == 1)
(C.OTA_LOCK_DIR / "pid").write_text("999999")
check("orphan lock (dead pid) does not count as busy", _ota.maybe_trigger() == "launched" and len(_launched) == 2)
import shutil as _sh  # noqa: E402
_sh.rmtree(C.OTA_LOCK_DIR)
_sp.Popen = _real_popen
check("status block reflects the assignment", _ota.status_block()["assigned_release"] == "v2.2.0"
      and _ota.status_block()["installed_release"] == "v2.1.0")

print()
if FAILURES:
    print(f"FAILED: {FAILURES}")
    sys.exit(1)
print("ALL PASS")
