# Defect register

> **RE-BASELINED 2026-08-08.** This register was written against the monolith audited on
> 2 August (now at `../legacy/superseded-monolith/`). The device code has since been replaced
> with a newer build from the client. Line numbers below refer to the OLD file unless a finding
> says otherwise. Statuses have been updated. **Read F-21 first: the detector was replaced and
> may no longer be capable of detecting the thing this system exists to detect.**

Consolidated from three prior audits plus a line-by-line verification pass, deduplicated, verified against source, and ranked. Every entry was checked against the code as it exists today. Entries that came from the prior reports but did not survive verification are recorded as such rather than dropped.

Paths are post-restructure. Last verified: 2 August 2026 (F-01…F-24); later entries carry their own dates. Last edited 2026-10-24 (F-29…F-36, repo health review).

**Severity scale.** CRITICAL means the system can stop detecting, or can destroy its own record of detections, while continuing to report itself healthy. HIGH means a real exposure or a failure mode with no recovery path. MEDIUM means it degrades or misleads. LOW means it is worth knowing. INFO is context, not a defect.

---

## Summary

| ID | Severity | Finding | Source | Phase |
|---|---|---|---|---|
| F-01 | CRITICAL | The `rms` and `auto` detection modes can never fire an alert — **FIXED 2026-08-12** | New | 1 |
| F-02 | CRITICAL | Model load failure silently disables detection — **FIXED 2026-08-12** | Reports, confirmed | 1 |
| F-03 | CRITICAL | Cooldown-suppressed detections erased — **FIXED 2026-08-12** (clip leak fixed earlier) | New | 1 |
| F-04 | CRITICAL | Live Twilio credentials in source, backup and bytecode — **CLOSED 2026-10-24**: code side fixed 2026-08-12, token rotated 2026-10-24, and since D-020 the device holds no Twilio credential at all (copies of the old token remain only in the workspace-root `legacy/` tree, scheduled for deletion) | Reports, confirmed | 1 |
| F-05 | HIGH | Deaf window is longest immediately after a detection — **FIXED 2026-08-13** (continuous capture; bench soak pending) | New (refines reports) | 1 |
| F-06 | HIGH | OTA update can strand the node with no rollback | Reports, confirmed | 1 |
| F-07 | HIGH | Blob container is public; device holds the storage account key — **fix decided 2026-08-25 (D-017), not yet implemented** | Reports, confirmed | 1 |
| F-08 | HIGH | GPS coordinates hardcoded and published — **FIXED; fully closed 2026-08-13** (coords only in `_sites.json`) | Reports, confirmed | 1 |
| F-09 | HIGH | Remote config cannot change detection sensitivity — **FIXED 2026-08-13** (real params tunable, clamped, published) | New | 1 |
| F-10 | HIGH | Remote config is unsigned and unclamped — **FIXED; signature mandatory 2026-08-22** (no key = no remote config at all) | Reports, confirmed | 1 |
| F-11 | HIGH | Provisioning installs the abandoned prototype — **FIXED 2026-08-12** | Reports, confirmed | 1 |
| F-12 | HIGH | Dashboard has no authentication | Reports, confirmed | 2 |
| F-13 | MEDIUM | WhatsApp alert is sent before the clip exists — **FIXED 2026-08-12** | Reports, confirmed | 1 |
| F-14 | MEDIUM | Manifest race — **FIXED 2026-08-13**: manifest retired, append-only event blobs (D-016) | Reports, confirmed | 1 |
| F-15 | MEDIUM | Audio device index hardcoded — **FIXED 2026-08-13** (detected by name) | Reports, confirmed | 1 |
| F-16 | MEDIUM | Telemetry CSV writes to a read-only partition — **FIXED 2026-08-13** (nothing writes to `/boot/firmware`) | Reports, confirmed | 1 |
| F-17 | MEDIUM | Provisioning and SD protection disagree on service user — **FIXED 2026-08-12** | New | 1 |
| F-18 | MEDIUM | Dashboard downloads everything, every poll | Reports, confirmed | 1 / 2 |
| F-19 | LOW | Battery alert dedup state lost on reboot — **CLOSED 2026-10-24 (D-020)**: the battery alert is decided on the backend from the heartbeat's `power.battery_voltage_v`; the device keeps no alert state at all | Reports, confirmed | 1 |
| F-20 | LOW | Modem admin API queried without authentication | Reports, confirmed | Deferred |
| **F-21** | **CRITICAL** | **Detector replaced. Arithmetically cannot fire on a sub-second event** — direction confirmed 2026-08-12: keep PSD, second detector coming (D-014) | **New** | **0** |
| F-22 | HIGH | Archive queue cap exceeds the RAM it lives in — **FIXED 2026-08-12** | New | 1 |
| F-23 | MEDIUM | PSD algorithm duplicated — **FIXED 2026-08-26**: canonical in `detectors/psd_tonal.py`, duplicate retired to legacy | New | 1 |
| F-24 | MEDIUM | model.joblib is dead weight — **FULLY CLOSED 2026-08-26**: restored as the `ml_mfcc` registry detector | New | 1 |
| F-25 | LOW | WhatsApp heartbeat logged "enviado" for a send that never happened — **FIXED 2026-09-23** | Bench | — |
| F-26 | HIGH | No liveness signal at all for a unit with no storage configured — **FIXED 2026-09-23 (D-018)** | Bench | — |
| F-27 | CRITICAL | OTA could be cut short into a permanently bad or unprotected unit — **fixed in code 2026-09-30, unproven on hardware** | Fault injection | — |
| F-28 | HIGH | Codec Zero ships with AUX IN capture fully muted — **FIXED 2026-10-24** (bookworm); trixie re-proof pending | Bench | — |
| F-29 | CRITICAL | Dead capture stream reported 100 % duty, `audio_ok: true`, `detector_ok: true` — **FIXED 2026-10-24** | Review | — |
| F-30 | HIGH | The acceptance tests (`v2_conformance_test`, `pipeline_soak_test`) had not run since 2026-09-23 — **FIXED 2026-10-24**, `make test` added | Review | — |
| F-31 | HIGH | Detector failure accounting only worked for `psd_tonal`; `detector_ok` true with zero detectors — **FIXED 2026-10-24** | Review | — |
| F-32 | HIGH | Repeated upload failure was a counter nobody watched, never a degradation or alert — **FIXED 2026-10-24** | Review | — |
| F-33 | HIGH | Housekeeping drains and retries could starve the systemd watchdog; WhatsApp retry replayed forever — **FIXED 2026-10-24** | Review | — |
| F-34 | HIGH | Push-only units received no remote config; a malformed signed config crash-looped behind healthy heartbeats — **FIXED 2026-10-24** | Review | — |
| F-35 | MEDIUM | A single 401 stopped push and heartbeats until a restart nobody can trigger — **FIXED 2026-10-24** | Review | — |
| F-36 | MEDIUM | Bench mode shipped as the template default and was about to leave `degraded_reason` with no trace — **FIXED 2026-10-24 (D-019)**; the mechanism itself left with Twilio the same day (D-020) | Review | — |
| F-37 | HIGH | PortAudio's callback-thread creation hangs on Codec Zero (`PaErrorCode -9987`) — **FIXED 2026-10-24** (blocking `_read_loop`); committed as a duplicate "F-29", renumbered | Bench | — |
| F-38 | HIGH | The OTA launched from the service died with the service it restarted; the new instance relaunched it — a 7 s restart loop on the first assigned release — **FIXED 2026-10-24** (own systemd unit; persisted cooldown) | Bench | — |
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

### F-07 — Blob container is public; device holds the storage account key

Azure storage account `marfuturatest`, container `alerts`; `raspberry-pi/src/oceankind/storage.py:35-43`

Two halves, one root cause.

*Read side.* Set to public anonymous read deliberately, because the static dashboard has no backend and no other way to read. Exposes full detection history, all audio recordings, live telemetry, and exact GPS coordinates. Verified downloadable with no credential.

*Write side.* The device authenticates with `BlobClient.from_connection_string`, i.e. the storage account key: full read, write, delete and list across every container and every site, sitting in `/etc/oceankind.env` on an unattended node nobody can reach. Blast radius is the obvious problem. The one that decides the fix is that **a shared account key on unreachable devices cannot be rotated** — rotating it silences the whole fleet with no way to redistribute the replacement, so there is no incident response short of losing the fleet.

The absence of a backend is the root cause of most of the security findings here.

**Fix — decided 2026-08-25, see D-017. Not yet implemented.**

Read side: `--allow-blob-public-access false` at account level, dashboard reads via a scoped read-only SAS until the Phase 2 API (D-003).

Write side: one Entra service principal per device, RBAC scoped to the container, narrowed by an ABAC path condition on `sites/{site}/*`. Verified against Microsoft's docs that blob path conditions need no hierarchical namespace, so the v2 tree survives unchanged. Needs a custom role — no built-in role is write-only. Never delete, never list, never read another site. Three blobs stay readable by necessity (`remote_config.json`, `_sites.json`, the two aux stubs); a literal write-only credential would kill remote tuning silently, which would be a self-inflicted silent-deaf mode.

Backstop regardless of credential model: blob versioning and soft delete on, so an overwrite by a compromised credential is recoverable.

Estimate revised from 3 hours to roughly 8 to 12: the earlier figure assumed a SAS string swap. Per-device service principals add provisioning work, a custom role definition, `azure-identity` on the device, and a clock-skew failure path that must be loud — a Pi Zero has no RTC, and Entra token acquisition fails on a bad clock in a way indistinguishable from a network fault.

Permanent fix is still the Phase 2 API.

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

**F-10 — fixed; signature mandatory since 2026-08-22.** Converged with the backend on the contract's §Device configuration: HMAC-SHA256 over the whole document minus `signature`, `config_version` as the version key, no v1 flat names. A device with **no key configured refuses all remote config** (it keeps running on its env-file values) — the earlier apply-unsigned-with-warning behaviour was the F-10 hole reopening and is gone. Unknown config keys, bad `detection_mode` enums and inverted PSD bands reject the document whole, and every rejection is a health event: named in `health.degraded_reason`, ERROR-logged once per `config_version`. Verified by smoke test §8 against independently-signed documents. Remaining: provision the shared key on both ends (fails safe until then — backend won't publish, device won't apply).

**F-15 — fixed.** Device selected by name substrings (`OCEANKIND_AUDIO_DEVICE_NAME`), never by ALSA index. Ported from the legacy prototype per D-006.

**F-16 — fixed.** Nothing writes to `/boot/firmware`. The telemetry CSV lives in `STATE_DIR` (tmpfs), bounded by row-trimming. Under the overlay it does not survive reboot — the resulting gaps in `power_history.json` are the signal the dashboard uses to detect reboots, and the D-002 decision gives all of `STATE_DIR` a persistent home with one env var.

**F-19 — improved.** Battery dedup state moved to `STATE_DIR`: survives service restarts (the original complaint's practical case). True reboot persistence is physically impossible under the whole-root overlay and lands with D-002.

**R-5.5 — closed.** Every network call now carries an explicit timeout: Twilio via `TwilioHttpClient(timeout=15)` (verified against the installed SDK signature), Azure client connection/read timeouts, modem HTTP 3 s, VE.Direct serial 1 s.

---

## Phase 3 fixes (2026-08-26) — detector registry

D-014 implemented: `oceankind/detectors/` runs an ordered chain, each detection becomes its own typed event. Verified by `tools/registry_test.py`.

**F-23 — fixed.** One copy of the PSD algorithm exists, in `detectors/psd_tonal.py`, and it is the one that runs. The unimported third-party original moved to `legacy/superseded-monolith/detector_psd_original-integral.py` as reference.

**F-24 — fully closed.** `model.joblib` is live again as `detectors/ml_mfcc.py` (event_type `blast`), with the feature extractor ported verbatim from `tools/predict.py` — the model was trained on those exact librosa features, so no reimplementation. Its heavy deps are optional and lazy; a requested-but-unloadable detector reaches `health.degraded_reason` (F-02 class, never silent).

**New empirical evidence for F-21 / client dependency 2 (2026-08-26).** Run against synthetics through the harness, `model.joblib` **fires "blast" on a sustained tonal clip and does not fire on a sub-second impulse** — precisely the behaviour of a model trained on a humming pool filter (`FILTRO`). Until the client retrains on real blasts, the fleet effectively carries two tonal-machinery detectors and no impulse detector beyond the RMS level gate. Raise with the client alongside dependency 3 (labelled audio).

**Operational note.** The bundle was pickled with scikit-learn 1.6.1; newer sklearn loads it with an `InconsistentVersionWarning`. Any unit enabling `ml_mfcc` should pin sklearn ~1.6, or the client re-exports the bundle with the deployed version.

---

## Bench-found, 2026-09-23

### F-25 — WhatsApp heartbeat logged "enviado" for a send that never happened — fixed

`send_whatsapp_heartbeat()` was missing the `TWILIO_CONFIGURED` guard its sibling `send_whatsapp()` already has. `_wa_send()` logs the real failure internally and returns without raising, so the unconditional success log below it ran regardless of whether anything actually sent — found on the bench via `journalctl` showing `WhatsApp NO enviado (sin credenciales Twilio — modo banco)` immediately followed by `→ Heartbeat WhatsApp enviado`, same attempt. Same shape as F-01/F-02: a failed operation reporting itself as succeeded. Cosmetic under `ALLOW_NO_TWILIO=1` (no real recipient expects the message), but the pattern is exactly the one this whole register exists to catch. Fixed with the identical guard `send_whatsapp()` uses. See `raspberry-pi/docs/TODO.md`.

### F-26 — No liveness signal at all for a unit with no storage configured — fixed (D-018)

Silence is the worst failure mode this system has, and this defect meant a unit could hit it and never be noticed. `status.json` was written only to blob storage; a unit provisioned without Azure and without a local storage bridge (an accepted bench configuration, 2026-09-07) never wrote it anywhere at all. Consequence, found by tracing both sides: the dashboard showed nothing for the unit, and — the sharper half — `silence.py`'s device-silence alerting reads `status.json → last_seen` and had no blob to read, so a unit in this state could go dark and the alerting system built specifically to catch that would stay silent about it too.

Fixed by `POST /api/devices/heartbeat` (D-018): the device POSTs the same `status.json` document directly to the backend on every heartbeat, unconditional on storage being configured, with no retry (a stale heartbeat has no recovery value) and backend-enforced monotonicity (a stale or out-of-order POST can never regress what is stored). `GET /api/sites/{site_id}/status` and `silence.py` both now read whichever of blob storage / the direct POST is fresher, so a unit with only one of the two signals is still fully covered. Full detail: `docs/DATA-CONTRACT.md` §Device heartbeat, `DECISIONS.md` D-018.

---

## Found by fault injection, 2026-09-30

### F-27 — The OTA could be cut short into a permanently bad or unprotected unit — fixed in code, unproven on hardware

`tools/ota_fault_test.sh` cuts the real `update_oceankind.sh` at every command boundary (power and network, singly and twice) and checks the unit afterwards. The script that shipped on 2026-09-04 failed 27 of 40 cut points on a root-writable unit and 12–14 of 18 on an overlay unit. Nine defects; the ones that matter, in order:

1. **A bad build could become permanent.** `.installed_sha` was written before verification, so power lost during the 60 s settle window left a poisoned build installed *and recorded as current*. Every later run said "already up to date". Silent-deaf shaped: the unit crash-loops and nothing ever retries.
2. **The install was not atomic** (`rm -rf` the package, then `cp -R`): a cut between them left no package, repaired by nothing until the next cron.
3. **`set -e` protected nothing.** It is disabled inside functions called from `||`/`if`; pip, cp and git failures were swallowed and the script printed `✓ Actualización completada`.
4. An infrastructure failure (disk full, no network) blacklisted a good commit in `.ota_failed_sha` forever.
5. A corrupt git object or stale `index.lock` disabled OTA permanently; two updaters could run concurrently.
6. Rollback needed the network (`git` + `pip`) — worked only because (3) hid pip's failure.
7. The overlay two-phase handoff wrote its flag and one-shot unit to the RAM overlay, which the requested reboot discards: phase 2 never ran and the overlay stayed **OFF** — SD unprotected, silently.

Fix: journal (`installing → verifying → rollingback`, atomic writes) + local snapshot (rollback needs no network) + `.installed_sha` only on confirmation + boot-time recovery (`oceankind-ota-boot.service`, installed by `setup.sh`) + explicit error handling + single-instance lock + a maintenance window driven by the overlay state itself (`.sd_protection`), bounded to three boots and always ending with the overlay re-enabled. The script refuses to disable the overlay on a unit that lacks the boot unit. After the fix: 0 violations across every single cut and every double cut, and the harness fails when any of these defects is reintroduced. **Not closed:** torn writes and the overlay path on real hardware — `raspberry-pi/docs/OTA-FAULT-TESTING.md` §5. Deployed units need a one-time hands-on step to take the new script.

### F-28 — Codec Zero ships with AUX IN capture fully muted — fixed

Found 2026-10-24, provisioning a Codec Zero + Pi Zero 2W with `QUICKGUIDE.md` as written at the time. `arecord -l` showed the card enumerated correctly, `setup.sh`'s `dtoverlay=rpi-codeczero` was present and correct, `OCEANKIND_AUDIO_DEVICE_NAME=codec` matched it by name — every check this project had short of actually reading samples passed. Then `arecord -D hw:0,0 ...` failed with `read error: Input/output error`, and `dmesg` showed `bcm2835-i2s ...: FIFO clear timed out: no PCM clock` / `I2S SYNC error!`, repeating.

**First suspected an upstream kernel regression** ([raspberrypi/linux#7692](https://github.com/raspberrypi/linux/issues/7692), a near-identical report, open and unreplied at the time) — reasonable from the evidence available, but wrong, and treated as more certain than it was before checking the cheaper explanation. The real cause: `amixer -c 0 contents` showed `Aux Switch`, `Mixin Left/Right Aux Switch`, `Mixin PGA Switch` and — critically — `ADC Switch` itself all **off** by default. Nothing in ALSA, `arecord`, PortAudio, or this project's own code ever enables them; the DA7213 codec simply boots with the whole AUX-in signal path muted, which is why the card enumerates and even opens, but the ADC never starts generating the clock the Pi's I2S peripheral is waiting for — manifesting as exactly the same "no PCM clock" / "SYNC error" symptom the kernel issue describes. **Confirmed identical on both the original kernel (`6.18.50`, trixie) and bookworm (`6.12`)** once the mixer state was actually checked — the kernel was never the variable.

Proof it's fixed, not just quieter: `arecord` after enabling the six controls ran clean (no I/O error), and a deliberate test signal (finger-touch on AUX IN) moved RMS from `1` to `16686` — the ADC is sampling real external signal, not muted silence.

Fix: `setup.sh`'s generated `oceankind.service` unit now runs the six `amixer -c 0 cset ...` calls as `ExecStartPre=`, unconditionally (not `-`-guarded) — a missing control means the hardware no longer matches what D-009 committed to, and that should fail the service loudly, not start it muted. Verified surviving a full `systemctl daemon-reload` + restart, with the mixer state logged in the journal on every start.

**Closed, 2026-10-24.** Re-tested on the original trixie image, same kernel (`6.18.50+rpt-rpi-v8`) the upstream issue was filed against: the mixer fix holds there too, confirmed across a cold reboot. The kernel was never the cause. `QUICKGUIDE.md` reverted to the default Raspberry Pi OS Lite image; the Legacy bookworm detour is no longer needed. [raspberrypi/linux#7692](https://github.com/raspberrypi/linux/issues/7692) is still open upstream and still unexplained on the reporter's end, but it is not this project's bug.

### F-37 — PortAudio's callback-thread creation hangs on Codec Zero — fixed

*(Renumbered 2026-10-24: this entry was committed as a second "F-29" while the review's F-29 below — the dead stream reporting itself healthy — already held the number. Same day, same board, different defect.)*

Found immediately after F-28 was fixed: with the mixer correctly enabled and raw `arecord` capturing clean real signal, the actual service still crashed on `source.start()` with `sounddevice.PortAudioError: Error starting stream: Wait timed out [PaErrorCode -9987]`, traced to `PaUnixThread_New(...)` timing out in `pa_linux_alsa.c`.

Ruled out one at a time, each with a real test, not assumed: not the sample rate (44.1 kHz failed identically to 48 kHz — a Raspberry Pi forum report had suggested this for the same board, but it didn't hold here); not real-time scheduling priority (`ulimit -r` was `0` and no `/etc/security/limits.d/` policy granted it, a real finding, but `systemd-run --property=LimitRTPRIO=95` still failed identically, so this wasn't it either). What isolated it: `sd.InputStream(...)` **without** a `callback=` argument, read manually via `.read()`, opened and read cleanly — `(4800, 2)`, no overflow. Only callback mode — where PortAudio creates its own dedicated thread to invoke the callback — hangs on this driver. Blocking mode, where the calling code pulls data itself, does not.

Fix: `capture.py`'s `AudioCapture` no longer passes `callback=` to `sd.InputStream`. It owns its own `threading.Thread` running a `_read_loop()` that calls `.read()` and hands blocks to the same `_handle_block()` logic the callback used to run — same contract (R-1.1: copy, enqueue, count, nothing else), different mechanism for getting the next block. Verified in isolation with a fake stream (thread exits cleanly on a read exception, `stop()` joins without hanging, drop-counting behaves correctly under a saturated queue) before testing on real hardware, where it then ran clean: `Captura continua iniciada`, no crash, surviving a cold reboot, with a real-signal test (finger-touch on AUX IN) producing a visible `RMS=` jump in the classifier's own per-window log — proof through the actual pipeline, not just that the stream opened.

---

## Found by the repository health review, 2026-10-24

Full review in `../../FABLE_REPORT.md` (root of the workspace, not in this repo). Everything below was fixed in the same session ("Batch A"); each fix is covered by `tools/phase1_smoke_test.py` and `make test` runs all three offline tools.

**F-04 status note.** The Twilio token was rotated on 2026-10-24 (client). The old credential is still present in plaintext and bytecode in the *root* `legacy/` tree outside this repo and in no file of this repo; deleting those copies is scheduled (review §2, R-03).

### F-29 — A dead capture stream reported itself healthy — fixed

`health.py` trimmed the duty-cycle window only inside `record_frames()` and the RMS window only inside `record_rms()`. When frames stop arriving nothing trims, so after an hour of uptime `duty_cycle_pct()` computed `span = min(now − start, 3600)` over a deque still holding 3600 s of frames: **frozen at ~100 % for as long as the process lived.** `audio_health()` took `max()` over stale samples → `audio_ok: true`. The PortAudio callback has no error path and `stream.active` was never checked; the classify thread beat the watchdog on every *empty* loop, so the watchdog saw a live thread; `detector_ok` counted only exceptions, of which a stream with no clips produces none. `ARCHITECTURE.md` stated the opposite ("a stalled capture callback reaches the fail-loud path"). This is the F-28 scenario (card enumerates, stream opens, no samples) wearing the health block's own clothes, and it is the exact failure the project exists to prevent.

Fix: both windows are trimmed on read (`duty_cycle_pct`, `audio_health`); `duty_cycle_pct` returns `0.0`, not `null`, for a started-but-dead stream; `record_clip()` marks every assembled window and `capture_health()` degrades when `last_clip_age_s ≥ CAPTURE_STALL_S` (60 s) or duty < 90 %; `health.capture_ok` and `health.last_clip_age_s` are published (contract updated); `maybe_alert_capture()` sends one WhatsApp per episode. Smoke test §3b/§3c prove the windows go to zero and the reason appears.

### F-30 — The acceptance tests did not run — fixed

`tools/v2_conformance_test.py:98` and `tools/pipeline_soak_test.py:121` called `main.upload_status()`, removed on 2026-09-23 when `collect_status`/`write_status_blob` replaced it for the heartbeat split. `AttributeError` before any assertion, for a month, while `DATA-CONTRACT.md`, `README.md`, `BENCH.md` and `PROGRESS.md` cited them as the proof of the contracted work. Nothing ran them automatically. Fix: both tools updated; new `Makefile` (`make venv`, `make test`) runs compile + smoke + conformance + soak; `.venv/` gitignored. Run before every commit.

### F-31 — Detector failure accounting only worked for `psd_tonal` — fixed

`detectors/__init__.py` recorded success only for `psd_tonal` and failure for any detector into one shared counter, so `psd_tonal`'s success reset `ml_mfcc`'s failures on every clip: a second detector could throw on 100 % of clips and never escalate. With `OCEANKIND_DETECTORS=ml_mfcc` alone, three transient failures latched `detector_ok: false` forever. `detector_ok()` ignored whether anything had loaded; zero detectors gave `detectors: []` with `detector_ok: true`, against the contract's "false when no detector loaded". Fix: per-detector consecutive-failure counters and alert dedupe; the registry reports what loaded (`set_loaded_detectors`); `detector_ok` = at least one loaded and none over the limit; `degraded_reason` names the failing detector. Smoke §2b.

### F-32 — Repeated upload failure never alarmed — fixed

`storage.upload_bytes` caught everything and `log.warning`ed; `upload_backlog` was published and never entered `degraded_reason` or triggered an alert, although `raspberry-pi/CLAUDE.md` lists "repeated upload failure" as a must-alert and `BENCH.md` admitted the gap. A revoked storage credential meant 500 spooled events, then discards, with no human told. Fix: `note_upload_backlog()` on each heartbeat — backlog > 0 for 3 heartbeats, or ≥ half the spool cap, is a degradation with one alert per episode. Unreadable spool files are now counted in `events_dropped` instead of being deleted silently, and a malformed spool item can no longer raise out of the drain. Smoke §11/§12.

### F-33 — Housekeeping could starve the watchdog into a restart loop — fixed

`watchdog.tick()` ran once per housekeeping iteration against `WatchdogSec=120`. On that same path: `retry_pending_whatsapp()` (up to 50 alerts × recipients × 15 s), `drain_event_spool()` (500 × 60 s), `drain_push_spool()` (500 × 10 s) and an untimed IoT Hub `connect()` *before* capture started. Any of them could exceed 120 s on a bad link and systemd would kill a healthy unit in mid-recovery; the WhatsApp retry persisted its attempt counter only after the loop, so the kill replayed the same 50 alerts, forever, while Twilio was down. The OTA's `restart_and_verify` compares `NRestarts`, so one such kill in its 60 s window rolled back a good build. Fix: all three take a time budget (`HOUSEKEEPING_BUDGET_S`, 30 s) and tick the watchdog between items; the WhatsApp attempt is persisted before each send; IoT Hub is off by default (`OCEANKIND_IOTHUB_ENABLED=1` to enable) and connects in a helper thread with a 10 s timeout, after capture has started; degradation alerts from the classify thread are queued and sent by housekeeping.

### F-34 — Push-only units were not remotely tunable; a malformed config crash-looped — fixed

`main.py` polled remote configuration only `if C.STORAGE_ENABLED`, so every unit provisioned by `QUICKGUIDE.md` (push-only, no Azure, no `OUTPUT_DIR`) received no remote configuration at all, against the "every threshold remotely tunable without a firmware update" obligation (D-014, D-015). Separately only `ConfigRejected` was caught; a signed document with a non-numeric value (`float()` → `ValueError`) or a non-object body (`.get` → `AttributeError`) exited the process; systemd restarted it 15 s later; the heartbeat branch runs first and published `degraded_reason: null`; then it crashed again — a crash loop that looked like health. Fix: push-only units poll `GET /api/devices/config` (`push.fetch_config`), the same signed document byte for byte; the whole poll is under `except Exception` → `set_config_error`. Contract updated (§Device configuration).

### F-35 — A single 401 silenced the unit until a restart — fixed

`push._auth_failed` latched forever; `post_heartbeat` returned immediately while set. A transient 401 (backend deploying with its database down) left the unit mute with no signal but absence. Fix: the latch lifts once per `AUTH_RETRY_S` (3600 s) for a single probe and re-latches on another 401.

### F-38 — The service-launched OTA was killed by the restart it performed; the bench looped — fixed

Found 2026-10-24 on the bench, first release ever assigned from the panel (D-021). `ota.py` launched `update_oceankind.sh --ref v0.9.1` with `subprocess.Popen(..., start_new_session=True)` and its docstring claimed that put the script out of systemd's reach. It does not: a new session is a new process group, still inside `oceankind.service`'s cgroup, and `KillMode=control-group` (the default) kills every process in it on `systemctl restart`. Sequence in the journal: clone, pip, install (the banner already read `OceanKind 0.9.1`), journal `verifying`, `sudo systemctl restart oceankind` — which killed the script before its 60 s verification could commit. The new service instance polled immediately on start, saw no `.installed_release` and an orphaned lock, launched the script again; the script read the journal in `verifying`, restarted the service to re-verify, and died again. Every seven seconds, indefinitely. The in-memory re-trigger cooldown reset with each process, so it never engaged. The hardware proved what the fault-injection harness could not: its `systemctl` is a stub that kills nothing.

Not silent, at least: the service was up (and listening) between restarts, the journal was full of `OTA lanzada`, and the panel showed the assignment pending. But a unit restarting every seven seconds is deaf for a good fraction of every minute, and a remote unit would have stayed in that loop until somebody noticed the heartbeat flapping.

Fix: under systemd (`INVOCATION_ID` set) the service now runs `sudo systemctl start --no-block oceankind-ota@<tag>.service`, a template unit `setup.sh` installs, so the script lives in its own cgroup; if that fails it reports an error and does **not** fall back to a child process. The last launch time is persisted in tmpfs (`ota.last_trigger`) and reloaded at import, so `OTA_RETRIGGER_S` holds across the restarts the OTA itself causes. `setup.sh` also writes an explicit sudoers drop-in for exactly the five commands the OTA path needs, instead of relying on the imager's blanket NOPASSWD. Smoke §13 covers both the persisted cooldown and the systemd launch path. Recovery on the bench: *Retener* in the panel stopped the loop within one cycle; `bash ~/oceankind/update_oceankind.sh --ref v0.9.1` from an SSH shell finished the journaled verification and committed.

### F-36 — Bench mode as the default, with its last trace about to disappear — fixed (D-019)

`setup.sh`'s template shipped `OCEANKIND_ALLOW_NO_TWILIO=1` **on**, and the pending `health.py` change (now landed) removed the "sin credenciales Twilio" `degraded_reason` in favour of the `alerts_enabled` boolean. A production unit left on the template default would have reported `degraded_reason: null` with no way to reach a human, and correctness would have depended entirely on the dashboard rendering one boolean. Fix: `OCEANKIND_BENCH=1` declares a bench; `ALLOW_NO_TWILIO=1` combined with a backend URL or Azure storage **refuses to start** unless `BENCH=1` is set. The `setup.sh` template ships the two lines together with the instruction to delete both when Twilio credentials arrive, and step 7a appends `BENCH=1` to an existing env file that has `ALLOW_NO_TWILIO=1` without it, so re-running `setup.sh` migrates older units; the OTA path does not (see `raspberry-pi/docs/TODO.md`).
