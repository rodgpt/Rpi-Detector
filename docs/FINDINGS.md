# Defect register

> **RE-BASELINED 2026-08-08.** This register was written against the monolith audited on
> 2 August (now at `../legacy/superseded-monolith/`). The device code has since been replaced
> with a newer build from the client. Line numbers below refer to the OLD file unless a finding
> says otherwise. Statuses have been updated. **Read F-21 first: the detector was replaced and
> may no longer be capable of detecting the thing this system exists to detect.**

Consolidated from three prior audits plus a line-by-line verification pass, deduplicated, verified against source, and ranked. Every entry was checked against the code as it exists today. Entries that came from the prior reports but did not survive verification are recorded as such rather than dropped.

Paths are post-restructure. Last verified: 2 August 2026.

**Severity scale.** CRITICAL means the system can stop detecting, or can destroy its own record of detections, while continuing to report itself healthy. HIGH means a real exposure or a failure mode with no recovery path. MEDIUM means it degrades or misleads. LOW means it is worth knowing. INFO is context, not a defect.

---

## Summary

| ID | Severity | Finding | Source | Phase |
|---|---|---|---|---|
| F-01 | CRITICAL | The `rms` and `auto` detection modes can never fire an alert — **FIXED 2026-08-12** | New | 1 |
| F-02 | CRITICAL | Model load failure silently disables detection — **FIXED 2026-08-12** | Reports, confirmed | 1 |
| F-03 | CRITICAL | Cooldown-suppressed detections erased — **FIXED 2026-08-12** (clip leak fixed earlier) | New | 1 |
| F-04 | CRITICAL | Live Twilio credentials in source, backup and bytecode — **code side FIXED 2026-08-12; rotation still pending** | Reports, confirmed | 1 |
| F-05 | HIGH | Deaf window is longest immediately after a detection — **FIXED 2026-08-13** (continuous capture; bench soak pending) | New (refines reports) | 1 |
| F-06 | HIGH | OTA update can strand the node with no rollback | Reports, confirmed | 1 |
| F-07 | HIGH | Blob container is public and unauthenticated | Reports, confirmed | 1 |
| F-08 | HIGH | GPS coordinates hardcoded and published — **FIXED; fully closed 2026-08-13** (coords only in `_sites.json`) | Reports, confirmed | 1 |
| F-09 | HIGH | Remote config cannot change detection sensitivity — **FIXED 2026-08-13** (real params tunable, clamped, published) | New | 1 |
| F-10 | HIGH | Remote config is unsigned and unclamped — **FIXED 2026-08-13** (clamps always; HMAC enforced once key provisioned) | Reports, confirmed | 1 |
| F-11 | HIGH | Provisioning installs the abandoned prototype — **FIXED 2026-08-12** | Reports, confirmed | 1 |
| F-12 | HIGH | Dashboard has no authentication | Reports, confirmed | 2 |
| F-13 | MEDIUM | WhatsApp alert is sent before the clip exists — **FIXED 2026-08-12** | Reports, confirmed | 1 |
| F-14 | MEDIUM | Manifest race — **FIXED 2026-08-13**: manifest retired, append-only event blobs (D-016) | Reports, confirmed | 1 |
| F-15 | MEDIUM | Audio device index hardcoded — **FIXED 2026-08-13** (detected by name) | Reports, confirmed | 1 |
| F-16 | MEDIUM | Telemetry CSV writes to a read-only partition — **FIXED 2026-08-13** (nothing writes to `/boot/firmware`) | Reports, confirmed | 1 |
| F-17 | MEDIUM | Provisioning and SD protection disagree on service user — **FIXED 2026-08-12** | New | 1 |
| F-18 | MEDIUM | Dashboard downloads everything, every poll | Reports, confirmed | 1 / 2 |
| F-19 | LOW | Battery alert dedup state lost on reboot — **improved 2026-08-13**; reboot persistence waits on D-002 | Reports, confirmed | 1 |
| F-20 | LOW | Modem admin API queried without authentication | Reports, confirmed | Deferred |
| **F-21** | **CRITICAL** | **Detector replaced. Arithmetically cannot fire on a sub-second event** — direction confirmed 2026-08-12: keep PSD, second detector coming (D-014) | **New** | **0** |
| F-22 | HIGH | Archive queue cap exceeds the RAM it lives in — **FIXED 2026-08-12** | New | 1 |
| F-23 | MEDIUM | PSD algorithm duplicated: inlined in monolith and in detector_psd.py | New | 1 |
| F-24 | MEDIUM | model.joblib is dead weight; loader is a stub — **naming/stub FIXED 2026-08-12; model restored as detector in Phase 3** | New | 1 |
| X-01 | — | Report claim that did not survive verification | Correction | Now |

---

## CRITICAL

### F-01 — The `rms` and `auto` detection modes can never fire an alert

`raspberry-pi/src/marfutura_iot_audio.py:1215`

```python
alert = rms >= ALERT_MIN_RMS and proba >= ML_THRESHOLD
```

`proba` is initialised to `0.0` at line 1212 and assigned only at line 1214, guarded by `if ml_result:`. `ml_result` is populated only at line 1208, guarded by `if DETECTION_MODE in ("ml", "auto")`. Set `DETECTION_MODE="rms"` and the expression reduces to `rms >= 0.02 and 0.0 >= 0.5`, which is always false.

The startup banner at line 1159 prints "Umbral RMS (fallback)" and the comment at line 1204 documents a fallback that does not exist. `decided_by` is hardcoded to `"rms+ml"` at line 1216, so the system also misreports how it decided.

**Why it matters.** The documented safety mode is a kill switch. Everything else keeps working: the 12-hour heartbeat still fires, and `check_battery_alert` (lines 630-696) still sends battery warnings through the same WhatsApp path regardless of detection mode. The operator receives regular, plausible traffic from a unit that cannot raise a detection alert.

**Nuance.** The failure is absolute only while `ML_THRESHOLD > 0`, the default being 0.5. Setting `OCEANKIND_ML_THRESHOLD=0` turns it into a pure RMS detector with no ML gate at all, which is a different wrong answer.

**Fix.** Build the fallback. Make `DETECTION_MODE` actually gate the decision. Correct `decided_by`. Roughly 2 hours.

### F-02 — Model load failure silently disables detection

`raspberry-pi/src/marfutura_iot_audio.py:284-303, 327-357`

`classify_clip` returns `{}` when the model is missing or throws. `proba` stays `0.0`, no alert ever fires, and heartbeats keep reporting the unit online. `_load_ml_model` caches its own failure through `_ml_load_attempted` at lines 287-289, so a model that fails to load once is never retried for the life of the process.

`docs/SYSTEM_REVIEW.md` §5.2 identified this and proposed falling back to RMS detection. Per F-01, that remedy does not exist and must be built rather than enabled.

**Fix.** Model load failure raises a WhatsApp alarm and sets `detector_healthy: false` in `status.json`. Combine with F-01. Roughly 2 hours together.

### F-03 — Cooldown-suppressed detections are erased, and their clips fill RAM

`raspberry-pi/src/marfutura_iot_audio.py:1226, 1263-1267`

```python
if alert and (now - last_alert_time) >= ALERT_COOLDOWN:
    ...
elif Path(clip_path).exists() and not alert:
    Path(clip_path).unlink()
```

When a detection fires inside the 600-second cooldown, `alert` is true, so the `if` fails on the cooldown test and the `elif` fails on `not alert`. Neither branch executes.

**Data loss.** No upload, no manifest entry, no counter increment, no local record. A blast sequence appears in the data as a single event. Any frequency statistic derived from `manifest.json` undercounts, and undercounts worst during the episodes that matter most. For a conservation enforcement tool this is a data-integrity defect, not an alerting defect.

**Memory exhaustion.** The only two `unlink` calls in the file are line 997, for the pending-alerts buffer, and line 1265. Clips that raise an alert are never deleted either. `protect_sd.sh` lines 88-95 delete `~/oceankind/clips` and symlink it to `/tmp/oceankind/clips`, and lines 126 and 135-137 enable the RAM-backed overlay, so clips land in RAM by two independent mechanisms. The tmpfiles rule at lines 61-73 has `-` in the age field, so systemd creates the directory and never ages anything out. There is no cron, no timer and no logrotate anywhere in the repository.

Each clip is 48 kHz stereo 16-bit for 5 seconds: 960 KB. Cooldown-suppressed clips are rate-limited by nothing and arrive up to once per loop iteration, roughly every 5 to 7 seconds. Worst case is about 11 MB per minute against 2 GB of RAM. Sustained false positives from rain or engine noise, which the comment at line 1211 confirms occur, exhaust memory in hours.

**Fix.** Delete clips on every path including error paths, record suppressed detections as suppressed entries, add a startup sweep of the clips directory. Roughly 1 hour.

### F-04 — Live Twilio credentials in source, backup and bytecode

`raspberry-pi/src/marfutura_iot_audio.py:49-50`, `legacy/build-artifacts/`

Account SID and auth token are literal default values in `os.environ.get(...)` calls. The same token appears in `legacy/build-artifacts/marfutura_iot_audio.py.bak_20260624_144323` and in the compiled `.pyc` alongside it.

`raspberry-pi/scripts/update_oceankind.sh` runs `git pull origin main` against a remote, so all three have been synchronised to a git remote outside this working copy.

**Fix.** Rotate the token in the Twilio console, move to `/etc/oceankind.env`, fail loudly at startup if absent, purge from the remote's history. Blocked on Twilio console access. Roughly 3 hours once unblocked.

**Update 2026-08-18.** A remaining literal copy in `legacy/superseded-monolith/marfutura_iot_audio_v1.1.0_audited-20260802.py:49-50` was caught by GitHub push protection and redacted (defaults now `""`). `legacy/build-artifacts/` (backup + `.pyc`) is gitignored and never pushed. The token itself is still live and still compromised — **rotation remains pending**.

---

## HIGH

### F-05 — Deaf window is longest immediately after a detection

`raspberry-pi/src/marfutura_iot_audio.py:1196-1268`

The main loop is strictly sequential. A quiet cycle is 5 seconds of `arecord` plus 1 to 3 seconds of librosa, so roughly 60 to 80 percent duty cycle. An alert cycle adds a Twilio call with no explicit timeout, a 960 KB upload over cellular, a full `manifest.json` download and re-upload growing toward multi-megabyte at the 5,000-entry ceiling, and an IoT Hub send. Plausibly 30 to 60 seconds of deafness.

Blast fishing produces sequences, not isolated events. The system is at its blindest in the seconds after the first detection, and F-03 then discards whatever it does catch for the next 10 minutes.

**Open contradiction between the delivered reports.** `IMPROVEMENT_REPORT.md` §3.1 says the system "may miss 30-60% of actual blasts". `SYSTEM_REVIEW.md` §3.1 says "the probability of missing a specific blast is low". Both went to the client. Resolve with a measurement, not with an edit.

**Fix.** Instrument the loop and publish `duty_cycle_pct` and `deaf_seconds_total` before refactoring anything, so the async pipeline can be evaluated against a real baseline. Then the async pipeline. Roughly 2 hours to instrument, 18 to 28 to rebuild and validate.

### F-06 — OTA update can strand the node with no rollback

`raspberry-pi/scripts/update_oceankind.sh`

Two-phase update: disable overlay, reboot, `git pull` plus `pip install`, re-enable overlay, reboot. A failure between the reboots leaves the node with SD protection defeated or a half-updated tree. No health check, no rollback, no physical access, solar power.

**Fix.** A/B code directories with a symlink switch, post-restart health check, automatic reversion. Prove it by deliberately failing an update on the bench Pi Zero 2W. Roughly 6 to 10 hours.

### F-07 — Blob container is public and unauthenticated

Azure storage account `marfuturatest`, container `alerts`

Set to public anonymous read deliberately, because the static dashboard has no backend and no other way to read. Exposes full detection history, all audio recordings, live telemetry, and exact GPS coordinates. Verified downloadable with no credential.

The absence of a backend is the root cause of most of the security findings here.

**Fix.** Container to private, dashboard reads via scoped read-only SAS, device writes via scoped write SAS instead of the storage account key. Blocked on Azure access. Roughly 3 hours once unblocked. Permanent fix is the Phase 2 API.

### F-08 — Sensor GPS coordinates hardcoded and published

`raspberry-pi/src/marfutura_iot_audio.py:44-45`, uploaded in every `status.json`

`SENSOR_LAT = -33.986582`, `SENSOR_LON = -71.860006`. The precise location of unattended solar hardware, published without authentication, in a system whose adversaries have a direct interest in it. Theft and vandalism are the concrete risk.

**Fix.** Move to `/etc/oceankind.env`, set per unit at provisioning. Roughly 30 minutes.

### F-09 — Remote config cannot change detection sensitivity

`raspberry-pi/src/marfutura_iot_audio.py:1185-1193`

`remote_config.json` sets `alert_threshold`, which is assigned to the global `ALERT_THRESHOLD`. Every use of that variable is display or reporting: `level_bar` at 1218, the IoT Hub payload field at 1258 and 1274, and `status.json` at 1280. It never appears in the alert decision at line 1215.

The two values that do decide, `ML_THRESHOLD` and `ALERT_MIN_RMS`, are environment-only, are not declared global, and are never read from remote config.

**Why it matters.** An operator can tune sensitivity, see the new value reflected in `status.json`, and change nothing. Changing real sensitivity requires editing the environment file and restarting the service, which means an OTA update, which is F-06. The safe knob is inert and the real knob requires the dangerous operation.

There is also no configuration editor in `https://github.com/rodgpt/Dashboard-Detector/blob/main/src/index.html`. Whatever writes `remote_config.json` lives outside this repository.

**Fix.** Expose the real parameters through remote config, with clamping. Combine with F-10. Roughly 2 hours.

### F-10 — Remote config is unsigned and unclamped

`raspberry-pi/src/marfutura_iot_audio.py:858-863, 1182-1194`

The device downloads a JSON blob and applies its values with no signature check and no range validation. Write access to the container is enough to alter system behaviour.

**Fix.** HMAC signature against a shared secret in the environment file, plus clamped ranges. Combine with F-09.

### F-11 — Provisioning installs the abandoned prototype

`raspberry-pi/scripts/setup.sh:91`

`ExecStart=/usr/bin/python3 ${OCEANKIND_DIR}/main.py`. That entry point is the prototype, now in `legacy/modular-prototype/`. A clean provision produces a unit that does not run the production system.

The dependency manifest had the same problem: it listed the prototype's libraries and omitted every one the production system needs. It now sits in `legacy/modular-prototype/` where it is truthful, and `raspberry-pi/` has no manifest until a correct one is written.

**Partially resolved by the folder restructure.** The ambiguity that caused this is gone. The script itself is still wrong.

**Fix.** Correct `ExecStart`, write a real `requirements.txt` for the production system. Roughly 2 hours.

### F-12 — Dashboard has no authentication

`https://github.com/rodgpt/Dashboard-Detector/blob/main/src/index.html`

Static page, no login, no token. Anyone with the URL has full read access to detection history, telemetry and sensor location.

**Fix.** Phase 1 mitigation is a read-only SAS token embedded in the client, which is obscurity rather than authentication and must be described that way. Real authentication is Phase 2.

---

## MEDIUM

### F-13 — WhatsApp alert is sent before the clip exists

`raspberry-pi/src/marfutura_iot_audio.py:1232` precedes `1237`

The message carries a `?play=<blob>` link and goes out before the upload. If the upload then fails, the link is permanently dead, `alert_count` never increments, and the manifest never updates, but the notification has already been delivered. Split-brain state between what the operator was told and what the record shows.

**Fix.** Upload first, then notify. Roughly 1 hour.

### F-14 — Manifest read-modify-write has no locking

`raspberry-pi/src/marfutura_iot_audio.py:264-272`

Each alert downloads the whole manifest, inserts at position zero, and re-uploads with `overwrite=True`. The retry path and the main loop can clobber each other today. With a second device it becomes silent data loss.

**Fix.** Append-only per-event blobs under per-device paths, which removes the race rather than mitigating it. Roughly 4 hours, combined with the multi-device layout work.

### F-15 — Audio device index is hardcoded

`raspberry-pi/src/marfutura_iot_audio.py:66`

`AUDIO_DEVICE = "plughw:3,0"`. USB re-enumeration changes the card number and capture fails until someone physically intervenes, on a node with no physical access.

`legacy/modular-prototype/audio_capture.py` already detects by name. Port that logic forward.

**Fix.** Detect by name. Roughly 1 hour, naturally part of the capture module work.

### F-16 — Telemetry CSV writes to a read-only partition

`raspberry-pi/src/marfutura_iot_audio.py:496` against `raspberry-pi/scripts/protect_sd.sh:121`

The code appends to `/boot/firmware/oceankind_data.csv` every 60 seconds. `protect_sd.sh` runs `do_boot_ro 0`, making `/boot` read-only. `upload_power_history` reads that same CSV, so `power_history.json` never populates when protection is on.

**Useful diagnostic.** The dashboard's power chart tells you which of the two mitigations is not in effect. If it renders, the SD card is unprotected. If it is empty, telemetry has been discarded since protection was enabled. It cannot currently be both. Confirm this on the live unit before prioritising.

**Fix.** Write to the tmpfs runtime directory and flush deliberately to a writable persistent location. Roughly 2 hours.

### F-17 — Provisioning and SD protection disagree on service user

`raspberry-pi/scripts/setup.sh:8` against `raspberry-pi/scripts/protect_sd.sh:30`

`setup.sh` hardcodes `/home/pi` and `SERVICE_USER="pi"`. `protect_sd.sh` defaults to `marfutura` and creates the clips symlink in that user's home. If they ran as different users, the symlink into `/tmp` sits in one home while `Path.home()` resolves to the other at runtime, and clips accumulate on the overlay's upper layer instead of the intended tmpfs.

**Fix.** Confirm which user the live service runs as, then make both scripts agree. Roughly 1 hour.

### F-18 — Dashboard downloads everything, every poll

`dashboard/src/index.html:1057, 1078, 1758`

Full `manifest.json`, `status.json` and `power_history.json` every 30 seconds. No pagination, no time window, no conditional request, no filtering. Fine for one device and a short history. First thing to break when the second unit arrives.

**Fix.** Windowed and paginated fetches in Phase 1. Conditional requests with ETag in Phase 2 when there is an API to serve them.

---

## LOW

### F-19 — Battery alert dedup state lost on reboot

`raspberry-pi/src/marfutura_iot_audio.py:605`

`_BATTERY_STATE_FILE = Path("/tmp/oceankind_battery_state.json")`. Under the overlay that is RAM, so every reboot re-arms the battery alerts and the operator gets duplicate warnings.

**Fix.** Move to persistent storage alongside the F-16 work.

### F-20 — Modem admin API queried without authentication

`raspberry-pi/src/marfutura_iot_audio.py:41`

`http://192.168.0.1/goform/goform_get_cmd_process`, noted in the code as "sin login". LAN-scoped, so low risk. Recorded for completeness.

---

## Correction

### X-01 — A report claim that did not survive verification

`docs/SYSTEM_REVIEW.md` §4.3 states that the dashboard stores a write-capable SAS URL in browser `localStorage` under `oceankind_write_sas_url`, and that one leaked browser lets an attacker raise the detection threshold so the station never alerts again.

`SAS_URL_KEY` is declared at `dashboard/src/index.html:789` and referenced nowhere else in the file. It is a dead constant. There is no write path in the dashboard.

This should be corrected before the client's side finds it. A client who checks one claim and finds it overstated discounts the ones that are true and urgent.

---

## Not defects

**Raspberry Pi 4 is over-specified.** True, and irrelevant at one unit. Revisit only for a multi-unit rollout. `docs/IMPROVEMENT_REPORT.md` §2 covers the migration options.

**SD card fragility.** A known risk, already mitigated by the overlay filesystem. It becomes critical only in combination with F-06.

**The decoupled architecture.** The Pi and the dashboard never talking directly, with blob storage between them, is a genuine strength. It is also why the container is public. Fix the exposure without collapsing the decoupling.

---

## Things this codebase gets right

Worth recording, because the register above is one-sided and the person who wrote this system clearly understood field conditions.

Error handling degrades gracefully nearly everywhere. The `inf` and `nan` JSON sanitisation is a scar from a real dashboard outage and it is handled correctly. Battery alerting has proper debounce and hysteresis. The pending-alert local buffer with bounded retries is the right pattern for flaky cellular. The overlay filesystem instinct is correct for a remote solar deployment. The dashboard's uptime reconstruction survives device reboots by reading the power history rather than trusting a counter.

---

## Findings added at the 2026-08-08 re-baseline

Against `raspberry-pi/src/marfutura_iot_audio.py` as received from the client, 1,578 lines.

### F-21 — The detector was replaced and cannot fire on a sub-second event

**CRITICAL. Raise with the client before any other work.**

`classify_clip()` no longer runs the scikit-learn MFCC classifier. `_load_ml_model()` is now a two-line stub returning `{"detector": "psd_tonal"}` and never opens `model.joblib`. `_extract_features()` was deleted.

The replacement is a Welch PSD tonal-peak detector, inlined from `detector_psd.py`, authored by Emily Barosin at Integral Consulting Inc., February 2026, whose header describes it as "Detector functions for MPA management system."

How it scores a clip: decimate by 4, split the 5-second clip into 1-second chunks, compute a Welch PSD per chunk, find peaks between `PSD_F_MIN=55` Hz and `PSD_F_MAX=1000` Hz whose prominence over local background is at least `PSD_THRESHOLD_DB=8` dB, and count a chunk as tonal if it holds two or more such peaks. Then:

```
proba = n_tonal / n_chunks          # n_chunks = 5 for a 5-second clip
alert = rms >= 0.010 and proba >= 0.60
```

**The arithmetic.** `proba` can only take the values 0, 0.2, 0.4, 0.6, 0.8 or 1.0. Clearing 0.60 requires at least three of the five seconds to contain sustained narrowband tones. A blast is impulsive and broadband, lasting a fraction of a second, and would score at most 0.2. **A single blast cannot trigger this detector.** That is arithmetic, not interpretation.

What a 55-1000 Hz tonal peak detector does find is sustained narrowband harmonics, which is the signature of vessel machinery: shaft rate, blade rate and engine harmonics.

**Two readings, and someone needs to say which is true.** Either the system's purpose has shifted from detecting blasts to detecting vessels inside a marine protected area, which the third-party header supports and which would be a reasonable programme change nobody told us about. Or the detector is mismatched to the stated purpose and the system has stopped detecting what it was built for.

Everything in this repository, the three audits, the presupuesto and the client-facing scope note describes a blast-fishing detector. If reading one is correct, all of it needs rewriting and the acceptance criteria change. If reading two is correct, this outranks every other finding here.

**Do not proceed past this question.**

### F-22 — Archive queue cap exceeds the memory it lives in

`ARCHIVE_MAX_FILES` defaults to 3,000 and `ARCHIVE_DIR` defaults to `~/oceankind/archive_queue`. At roughly 960 KB per clip that is about 2.9 GB, in a directory that sits on the RAM-backed overlay, on a 2 GB device.

The bounded queue is the right idea and it fixes the unbounded leak in F-03. The bound is simply set above the resource it protects. Either lower it to something the overlay can hold, or move `ARCHIVE_DIR` to persistent storage, which is D-002.

### F-23 — The PSD algorithm exists in two places

`detector_psd.py` is not imported. Its algorithm was copy-pasted into `classify_clip()` with adaptations noted in the comments (higher nfft, guard bins around the peak). Two copies of the detection logic that can now drift apart, and the one that runs is not the one a reader would open.

Either import the module or delete it and keep the inline version as canonical. A third-party file that looks authoritative and is not is worse than no file.

### F-24 — model.joblib and the ML vocabulary are dead weight

`model.joblib` is still shipped, `ML_MODEL_PATH` is still defined, and `_load_ml_model` still exists as a stub. Nothing loads the model. Meanwhile every knob is still named for the classifier that no longer runs: `ML_THRESHOLD` (now a tonal fraction), `ML_POSITIVE_LABEL` (defaulting to `FILTRO`, a pool-filter test artifact that reaches real WhatsApp alerts), `DETECTION_MODE`.

Cosmetic individually. Together they mean nobody reading this file can tell what it does.

### Status changes at re-baseline

**Fixed.** The unbounded clip leak in F-03, via the bounded archive queue, subject to F-22. The cross-device manifest race in F-14, by accident, because per-site prefixes give each unit its own manifest.

**Improved but not fixed.** F-08 and F-15 are now environment-configurable, but the literals remain as defaults, so an unconfigured unit behaves exactly as before.

**Unchanged.** F-01 is byte-identical, the `rms` and `auto` modes still cannot fire. F-02 still applies, and `audio_health()` catches a dead hydrophone rather than a dead detector. F-04, the same Twilio token, still literal, after five weeks of development. F-13 and F-16 untouched.

**Worse in effect.** The data-loss half of F-03. `maybe_trigger_cluster_call()` now counts every detection including cooldown-suppressed ones, so the system knows enough about suppressed events to place a phone call and still does not write them to the manifest.

---

## Phase 1 fixes (2026-08-12)

All in `raspberry-pi/src/marfutura_iot_audio.py` unless noted. Verified by `raspberry-pi/tools/phase1_smoke_test.py` (synthetic audio, no hardware, no Azure, no Twilio) plus a refuse-to-start check. Emitted-field changes are documented in `docs/DATA-CONTRACT.md` §"As-built".

**F-01 — fixed.** `DETECTION_MODE` is now `psd` | `rms` | `auto` (`ml` accepted as a legacy alias) and every mode genuinely decides: `rms` alerts on `rms >= ALERT_THRESHOLD` with no score gate, `auto` falls back to RMS when the classifier fails. `decided_by` reports what actually decided (`psd_tonal` | `rms` | `rms_fallback`), never a hardcoded string. Invalid modes refuse to start.

**F-02 — fixed.** Classifier failures are counted; three consecutive failures set `health.detector_ok: false`, populate `degraded_reason`, log at ERROR and send one WhatsApp degradation alert (deduped until recovery). The stub `_load_ml_model` is deleted. In `auto` mode a dead classifier also falls back to RMS detection.

**F-03 — fixed (data-loss half; clip leak was fixed at re-baseline).** Detections inside the cooldown are recorded in the manifest with `suppressed: true` (no notification, no audio upload — per D-008). Clip cleanup now runs in a per-iteration `finally`, covering error paths, and a startup sweep clears orphans from a crashed run. Archive-queue discards are counted and published as `health.clips_dropped`.

**F-04 — code side fixed; rotation still blocked.** No literal credential defaults remain in source. Secrets load from the environment (`/etc/oceankind.env` via systemd) and the service refuses to start when they are absent (R-8.1), with `OCEANKIND_ALLOW_NO_TWILIO=1` as a bench-only escape hatch that is declared in `health.degraded_reason`. The token itself is still live in `legacy/build-artifacts/` and on the git remote until the client rotates it — that half stays open.

*2026-08-13, client decision:* the exposed token **stays in service** until console access is granted. It now lives in `raspberry-pi/oceankind.env` — gitignored, flagged for rotation in the file itself — which `setup.sh` installs to `/etc/oceankind.env`. This keeps the unit alerting through the refuse-to-start change without putting the secret back into anything tracked. Rotation remains the single most urgent pending action.

**F-05 — instrumented (the fix itself is Phase 2).** `health.duty_cycle_pct` (rolling 1 h window) and `health.deaf_seconds_total` are measured and published. The 24-hour bench baseline is pending bench time; the async pipeline gets judged against it.

**F-08 — fixed.** No coordinate literals in source; unset means `null` in `status.json`, and the dashboard's site table is the location source of truth.

**F-09 — reporting half fixed.** `status.json` now publishes a `detection` block with the thresholds actually in force (`score_min`, `rms_min`, `rms_threshold`, PSD band). Making them remotely tunable and clamped remains Phase 4 (with F-10).

**F-11 — fixed.** `setup.sh` installs `src/marfutura_iot_audio.py` as the entry point, installs the production dependency set from the new `raspberry-pi/requirements.txt`, and writes an env template matching the real variables. The prototype and mosquitto are gone from provisioning.

**F-13 — fixed.** The clip uploads before the notification goes out; the manifest entry records `clip_uploaded` truthfully; a notified alert whose upload failed links to the dashboard, not to a dead blob.

**F-17 — fixed.** Both scripts resolve the service user with the identical expression (`${SERVICE_USER:-${SUDO_USER:-marfutura}}`).

**F-22 — fixed.** `ARCHIVE_MAX_FILES` default lowered 3000 → 300 (~290 MB in RAM), discards counted in `health.clips_dropped`. Moving the archive off the overlay entirely remains with D-002.

**F-24 — fixed (naming and stub).** `ML_THRESHOLD` → `SCORE_MIN`, `ML_POSITIVE_LABEL` (default `FILTRO`) → `DETECTION_LABEL` (default `MOTOR`), `ML_MODEL_PATH` and the stub loader deleted; legacy env names still honoured with a deprecation warning. `model.joblib` stays in `models/` awaiting its Phase 3 return as the second registry detector (client confirmed 2026-08-12 that a second model is coming).

---

## v2 cutover fixes (2026-08-13, D-016)

The client decided the deployed units are prototypes: their blob freezes on v1, and the device now emits the **v2 contract only**, to new storage. Proven by `raspberry-pi/tools/v2_conformance_test.py` → `tools/validate_contract.py` → CONFORMANT.

**F-14 — fixed by construction.** `manifest.json` is retired. One immutable blob per event under `sites/{site}/events/YYYY/MM/DD/`; there is no read-modify-write left to race, for any number of devices. Failed event uploads spool locally (bounded, 500) and drain each heartbeat; spool overflow is counted in `health.events_dropped`.

**F-08 — fully closed.** Coordinates left `status.json` entirely; they live only in `_sites.json`, sourced from `/etc/oceankind.env` at provisioning. The refuse-to-start check requires them when storage is configured.

**Consequence for F-18.** The old dashboard still downloads the old blob wholesale — unchanged. For the v2 fleet, "site X, last 24 hours" is a date-prefix listing by construction, so the dashboard's v2 reader can be cheap from day one.

---

## Phase 2 fixes (2026-08-13) — continuous capture

The monolith became the `oceankind/` package: capture / classify / transport threads plus a housekeeping main thread. Proven by `raspberry-pi/tools/pipeline_soak_test.py`: real threaded pipeline over a synthetic source with artificially slow storage — capture never pauses, no event lost, tree CONFORMANT. The superseded monolith is preserved in `legacy/superseded-monolith/`.

**F-05 — fixed (code).** Capture is a sounddevice callback stream; classification and every network operation moved off the audio path. The deaf window after a detection is gone by construction: a 60-second upload delays *other uploads*, never listening. `health.duty_cycle_pct` is now frame-based (audio delivered vs wall clock). The 24 h ≥99 % bench measurement remains the acceptance gate.

**F-09 — fully fixed.** The parameters that actually decide (`score_min`, `alert_min_rms`, `alert_threshold`, PSD band, cooldown) are settable through `sites/{site}/remote_config.json` without restart, clamped to safe ranges, version-gated, and the applied values are published in `detection.thresholds`. Payload spec in `docs/DATA-CONTRACT.md`.

**F-10 — fixed, one provisioning step pending.** All remote values are clamped unconditionally. HMAC-SHA256 signature verification is implemented and enforced whenever `OCEANKIND_CONFIG_HMAC_KEY` is set; until the key is provisioned on both ends, unsigned payloads apply with a loud warning. (The config source moves from blob to backend endpoint when D-003 produces one.)

**F-15 — fixed.** Device selected by name substrings (`OCEANKIND_AUDIO_DEVICE_NAME`), never by ALSA index. Ported from the legacy prototype per D-006.

**F-16 — fixed.** Nothing writes to `/boot/firmware`. The telemetry CSV lives in `STATE_DIR` (tmpfs), bounded by row-trimming. Under the overlay it does not survive reboot — the resulting gaps in `power_history.json` are the signal the dashboard uses to detect reboots, and the D-002 decision gives all of `STATE_DIR` a persistent home with one env var.

**F-19 — improved.** Battery dedup state moved to `STATE_DIR`: survives service restarts (the original complaint's practical case). True reboot persistence is physically impossible under the whole-root overlay and lands with D-002.

**R-5.5 — closed.** Every network call now carries an explicit timeout: Twilio via `TwilioHttpClient(timeout=15)` (verified against the installed SDK signature), Azure client connection/read timeouts, modem HTTP 3 s, VE.Direct serial 1 s.
