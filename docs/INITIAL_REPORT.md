# OceanKind / Mar Futura — Architecture & Risk Assessment

**Scope:** Full-stack review of the underwater acoustic monitoring system: Python capture/detection code, the ML classifier path, the HTML dashboard, Azure Blob and Twilio integrations, and the deployment/OTA shell scripts.
**Date:** 2026-07-06
**Reviewed at commit state:** working copy in `marFutura/` (no `.git` present locally; OTA implies a git remote at `~/oceankind/code`)

---

## 1. Verdict

The system works and shows real field-hardening instincts, but it is **not solid** yet. Two problems dominate:

1. A **live Twilio secret committed in source** (and in the backup and the compiled `.pyc`).
2. A **single-threaded capture loop that is deaf for large fractions of every cycle**, which is a design flaw for a system whose job is catching sub-second blasts.

On top of that, there is a deep split between what is documented and installed versus what actually runs.

Priority: rotate the secret today, then decouple capture from processing, then reconcile the two codebases.

---

## 1a. What is in this folder (read this first)

This repository contains **three distinct things**. Being precise about which is which matters, because the deprecated code is the one currently wired into deployment.

### A. Raspberry Pi code — IN USE
The production detector that actually feeds the dashboard.

| File | Role |
|------|------|
| `marfutura_iot_audio.py` | The live monolith (v1.1.0). Capture via `arecord`, ML classification, Azure Blob upload, Twilio WhatsApp, Victron solar telemetry, remote config. **This is the running system.** |
| `model.joblib` | Trained scikit-learn classifier used by the monolith. |
| `predict.py` | Standalone CLI to classify WAVs with the same model. |
| `test_model_on_alerts.py` | Offline evaluation of the model against saved alerts. |
| `requirements.txt` | Python deps. |
| `setup.sh` | Pi provisioning (note: currently installs the DEPRECATED code as the service — see below). |
| `protect_sd.sh` | Enables overlay filesystem to protect the SD card. |
| `update_oceankind.sh` | OTA update via git pull + two-phase reboot. |
| `marfutura_record_gdrive.py` | Separate utility: continuous record-to-Google-Drive, no detection. Not part of the detection path. |

### B. Raspberry Pi code — DEPRECATED
An earlier, cleaner modular design that nothing in the live path uses. It does **not** feed the dashboard. Kept only for reference.

| File | Role |
|------|------|
| `main.py` | Entry point for the modular system. |
| `config.py` | Config for the modular system. |
| `detector.py` | STA/LTA detector + frequency analyzer. |
| `alert.py` | MQTT / HTTP / Azure IoT Hub alert transports. |
| `audio_capture.py` | Streaming capture (sounddevice + queue). |
| `clip_saver.py` | Pre/post-trigger WAV clip saver. |
| `HARDWARE.md`, `PROJECT15.md` | Docs that describe THIS deprecated system, not the live one. |

### C. Dashboard
| File | Role |
|------|------|
| `dashboard/index.html` | Single self-contained web client (HTML + CSS + JS). Hosted on Azure static hosting; reads the JSON blobs the monolith writes. No backend of its own. |

**The trap:** the folder does not label any of this. `setup.sh` still installs the **deprecated** `main.py` as the systemd service, and `HARDWARE.md` / `PROJECT15.md` describe the **deprecated** system as if it were current. A fresh deployment from this folder would run the wrong code. This is expanded in Section 2.

---

## 2. The biggest structural problem: two systems pretending to be one

There are two independent codebases in the same folder that do not talk to each other.

**The "clean" modular system** — `main.py`, `config.py`, `detector.py`, `alert.py`, `audio_capture.py`, `clip_saver.py`:
continuous audio via `sounddevice`, a proper STA/LTA detector, a thread-safe queue, and MQTT/HTTP/IoT Hub alerts. This is what `HARDWARE.md` and `PROJECT15.md` describe, and it is what `setup.sh` installs as the systemd service (`ExecStart=.../main.py`).

**The production monolith** — `marfutura_iot_audio.py` (v1.1.0, most recently edited):
`arecord` subprocess capture, a scikit-learn + librosa classifier, Azure Blob Storage, Twilio WhatsApp, Victron solar telemetry, cellular-modem polling, and remote config. This is what the dashboard is actually wired to (`marfuturatest.blob.core.windows.net`).

Everything the dashboard shows comes from the monolith. Nothing the docs describe (Project 15, IoT Hub routing, MQTT, STA/LTA) is in the path that feeds the dashboard. The installed service runs `main.py`; the real capabilities live in a file `setup.sh` never launches.

**Action:** choose one codebase as canonical, archive or delete the other, and rewrite the docs to match what actually runs.

---

## 3. How it talks to the internet (the real path)

The monolith's main loop runs on a single thread, in sequence:

1. Poll `remote_config.json` from the public blob container (every 5 min).
2. `arecord` records a 5-second WAV to disk (blocking).
3. librosa loads/resamples the file, extracts 52 features, sklearn classifies.
4. On alert: Twilio WhatsApp send → blob upload of the clip → manifest rewrite → IoT Hub message.
5. Heartbeat: fetch modem signal (3s timeout) → read VE.Direct serial (up to 3s) → upload `status.json` → append CSV → periodically rebuild `power_history.json`.

The dashboard is a static page polling three public JSON blobs (`manifest.json`, `status.json`, `power_history.json`) every 30s. Alerts also fan out over WhatsApp with a deep link into the dashboard's client-side spectrogram view.

---

## 4. Security issues (fix before anything else)

### 4.1 Live Twilio credentials hardcoded in source — CRITICAL
`TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` are literal defaults (lines ~49–50 of `marfutura_iot_audio.py`). The same token also appears in:
- `marfutura_iot_audio.py.bak_20260624_144323`
- `__pycache__/marfutura_iot_audio.cpython-310.pyc`

Because `update_oceankind.sh` runs `git pull origin main`, these are pushed to a git remote. Anyone with repo or `.pyc` access can send WhatsApp on the account and drain the Twilio balance.

**Action:** rotate the token now; move it to `/etc/oceankind.env`; purge it from git history, the `.bak`, and the `.pyc`.

### 4.2 Public, unauthenticated blob container — HIGH
The dashboard reads `manifest.json`, `status.json`, and raw WAV clips over plain HTTPS with no token. That publicly exposes exact sensor coordinates, audio recordings, disk/RAM/CPU stats, battery voltage, and cellular signal. For an anti-illegal-fishing sensor, publishing precise location and live recordings is an operational risk.

**Action:** make the container private; serve the dashboard's reads through a scoped read-only SAS or a small proxy; at minimum stop exposing coordinates and audio publicly.

### 4.3 Untrusted remote config — HIGH
The device applies `remote_config.json` from the same public container (threshold, cooldown, heartbeat) with no signature and no sanity clamp. The dashboard stashes a write-capable SAS URL in browser `localStorage` (`oceankind_write_sas_url`); one leaked browser lets someone set the threshold so high the station never alerts again.

**Action:** sign or authenticate config; clamp incoming values to safe ranges; never hold a write SAS client-side.

### 4.4 Modem admin API with no auth — LOW (LAN-only)
`http://192.168.0.1/goform/...` is queried without login (the code notes "sin login"). LAN-scoped, so low risk, but worth noting.

---

## 5. Reliability issues ("could break at any moment")

### 5.1 Detection gaps are the core flaw — CRITICAL
One thread does record → classify → upload sequentially. During the 5s record, the 1–3s librosa pass, and every network call (Twilio has no explicit timeout; blob and IoT Hub add seconds), the hydrophone is not listening. A blast in that window is lost. Blast detection needs continuous capture, which the unused modular code already implements via callback + queue.

**Action:** decouple capture from processing/upload (ring buffer + worker thread), or adopt the modular capture path.

### 5.2 Silent-deaf failure mode — HIGH
Default `DETECTION_MODE="ml"`, and an alert requires `proba >= ML_THRESHOLD`. If `model.joblib` fails to load, `classify_clip` returns `{}`, `proba` stays 0, and **no alert ever fires**, while heartbeats keep reporting "online." No RMS fallback, no alarm on model-load failure.

**Action:** make model-load failure loud (heartbeat flag + WhatsApp), and fall back to RMS detection.

### 5.3 WhatsApp fires before the upload exists — MEDIUM
The alert send (with a `?play=<blob>` link) happens before the clip upload. If the upload later fails, the link is permanently dead, `alert_count` and the manifest never update, but the notification already went. Split-brain state.

**Action:** upload first (or hold the link until upload confirms), then notify.

### 5.4 Manifest read-modify-write with no locking — MEDIUM
Each alert downloads the whole manifest, inserts, and re-uploads with `overwrite=True`. The retry path and the main loop can clobber each other. At up to 5,000 entries, that is a multi-MB pull+push per alert, and the browser re-downloads it every 30s.

**Action:** append-only storage or a lease/etag on write; paginate what the dashboard fetches.

### 5.5 Hardcoded audio device `plughw:3,0` — MEDIUM
USB re-enumeration changes the card number and capture fails until someone intervenes physically. The modular code auto-detects; the monolith does not.

**Action:** auto-detect the capture device by name, as `audio_capture.py` already does.

### 5.6 OTA update can strand a remote node — HIGH
`update_oceankind.sh` does a two-reboot dance: disable overlay → reboot → git pull + `pip install` → re-enable overlay → reboot. If phase 2 dies mid-way, the node is left with overlay off (SD protection defeated) or a half-updated tree, and `pip install` has no rollback. On a solar node with no physical access, this is the scariest failure in the repo.

**Action:** add a health check after restart with automatic rollback, or A/B the code directory so a bad pull can't brick the unit.

### 5.7 CSV logging fights the SD protection — MEDIUM
The monolith appends to `/boot/firmware/oceankind_data.csv` every 60s, but `protect_sd.sh` sets `/boot` read-only (`do_boot_ro 0`). On protected units those writes silently fail, so `power_history.json` may never populate. On unprotected units it is constant small SD writes.

**Action:** write telemetry to the RAM-backed runtime dir and flush to a writable persistent location deliberately, not to a read-only boot partition.

### 5.8 Battery-alert dedup lost on reboot — LOW
State lives in `/tmp` (RAM under the overlay), so every reboot re-arms the alerts. The code comment already admits it survives script restarts but not system reboots.

---

## 6. What is genuinely good

- Error handling degrades gracefully almost everywhere.
- The `inf`/`nan` JSON sanitization is clearly a scar from a real dashboard-blanking bug, and it is handled correctly.
- Battery alerting has proper debounce and hysteresis.
- The pending-alert local buffer with bounded retries is the right pattern for flaky cellular.
- The overlay-FS instinct to protect the SD card is correct for remote solar deployments.
- The dashboard is polished: client-side FFT spectrogram, uptime reconstruction that survives reboots via power history, sensible color coding.

The person who wrote the monolith clearly understands field conditions.

---

## 7. Top five fixes, in order

1. Rotate the Twilio token, move it to env, and purge it from the `.bak`, the `.pyc`, and git history.
2. Lock down the blob container (private + scoped read SAS) and clamp/sign remote config values.
3. Decouple capture from processing/upload so the mic never stops (queue + worker thread, or adopt the modular capture code).
4. Make model-load failure loud and add an RMS fallback so the system cannot go silently deaf.
5. Reconcile the two codebases and the docs, and make an OTA path that cannot strand a remote node (health check + auto-rollback, or A/B).

---

## 8. Severity summary

| # | Issue | Type | Severity |
|---|-------|------|----------|
| 4.1 | Hardcoded live Twilio credentials | Security | Critical |
| 5.1 | Single-threaded capture: detection gaps | Reliability | Critical |
| 4.2 | Public blob exposes location + audio | Security | High |
| 4.3 | Untrusted remote config / client-side write SAS | Security | High |
| 5.2 | Silent-deaf on model-load failure | Reliability | High |
| 5.6 | OTA can strand a remote node | Reliability | High |
| 2 | Two divergent codebases vs docs | Maintainability | High |
| 5.3 | WhatsApp fires before upload exists | Reliability | Medium |
| 5.4 | Manifest read-modify-write race | Reliability | Medium |
| 5.5 | Hardcoded audio device index | Reliability | Medium |
| 5.7 | CSV logging vs read-only boot partition | Reliability | Medium |
| 4.4 | Modem admin API no auth (LAN) | Security | Low |
| 5.8 | Battery dedup lost on reboot | Reliability | Low |
