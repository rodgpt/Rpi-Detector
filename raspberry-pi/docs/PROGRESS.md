# Implementation progress — device

Phased build plan. Each phase produces something runnable. Requirement IDs refer to `../../REQUIREMENTS.md`, defect IDs to `../../docs/FINDINGS.md`, decision IDs to `../../DECISIONS.md`.

**All development must conform to the actual library documentation.** librosa, scipy, the Azure SDKs and pyserial all change APIs. Read the real docs, not memory.

---

## Current status

**Phase 0 complete.** Repository self-contained, conformance validator working.
**Phase 1 code complete (2026-08-12).** Open: token rotation (client), clean provision (bench time).
**Phase 2 code complete (2026-08-13).** Continuous capture: `oceankind/` package, four threads, capture never blocks. Verified by `tools/pipeline_soak_test.py`. Open: Zero 2W soak (memory + 24 h duty ≥99 %) — **the next thing that needs the bench unit**.
**Phase 4 device side done early (2026-08-13, D-016).** v2 only, new storage, prototypes frozen. Verified by `tools/v2_conformance_test.py`. Open: index tags (needs Azure), dashboard v2 reader (their side, deferred by client).

Entry point unchanged for systemd: `marfutura_iot_audio.py` → `oceankind.main`. Next in queue: **Phase 3, detector registry** (the two-model harness), now unblocked because detection runs on its own worker.

---

## Phase 0: Workable repository **COMPLETE**

- [x] Latest client device tree merged, superseded copies in `legacy/`
- [x] 24 defects verified against source and ranked
- [x] v2 contract designed from what the dashboard consumes
- [x] `tools/validate_contract.py` passing against a reference tree
- [x] Requirements written

---

## Phase 1: Stop failing quietly **CODE COMPLETE 2026-08-12**

Needs no Azure, no hardware, no client. Highest risk removed per hour in the whole project.

### Secrets and configuration
- [x] Twilio SID and token out of source, loaded from `/etc/oceankind.env`, refuse to start if absent (R-8.1, F-04) — `OCEANKIND_ALLOW_NO_TWILIO=1` bench escape hatch, declared in `degraded_reason`
- [x] Site coordinates out of source (R-8.2, F-08) — unset publishes `null`
- [ ] Rotate the token and purge it from the remote — **blocked on client Twilio access**

### Health and honesty
- [x] `health` block in `status.json`: `detector_ok`, `audio_ok`, `duty_cycle_pct`, `clips_dropped`, `upload_backlog`, `degraded_reason` (R-2.1 to R-2.4) — plus `deaf_seconds_total`, `suppressed_count`, `archive_queue`
- [x] Detector load failure raises an alert and sets `detector_ok: false` (R-2.1, F-02) — 3 consecutive classifier failures → WhatsApp degradation alert, deduped
- [x] Remove or repair the `rms` and `auto` modes. A mode that cannot fire must not ship (R-2.5, F-01) — modes are now `psd`/`rms`/`auto`, each genuinely decides; `decided_by` truthful
- [x] Publish the thresholds actually in force (R-2.6, F-09) — `detection.thresholds` block
- [x] Retire the ML-era naming and the stubbed model loader (R-3.5, F-24) — `SCORE_MIN`, `DETECTION_LABEL`; legacy env names honoured with warning

### The record
- [x] Record cooldown-suppressed detections as `suppressed: true` instead of discarding them (R-4.2, F-03, D-008)
- [x] Delete clips on every path including error paths and early returns (F-03) — per-iteration `finally` + startup sweep
- [x] Lower `ARCHIVE_MAX_FILES` or move `ARCHIVE_DIR` off the overlay (R-5.4, F-22) — default 3000 → 300; move stays with D-002
- [x] Split `captured_utc` from `uploaded_utc` (R-4.3) — legacy `timestamp` now equals capture time
- [x] Upload before notify, set `clip.uploaded` truthfully (R-4.4, R-4.5, F-13) — `clip_uploaded` field
- [x] `schema_version` on every blob (R-4.7)

### Measure before refactoring
- [x] Instrument the loop, publish `duty_cycle_pct` and `deaf_seconds_total` (R-1.2, F-05)
- [ ] Record a 24-hour baseline. This is what Phase 2 gets judged against — **needs bench time**

### Provisioning
- [x] Real `requirements.txt`: numpy, scipy, azure-storage-blob, azure-iot-device, twilio, pyserial (R-7.2, F-11)
- [x] Fix `scripts/setup.sh` entry point (R-7.2, F-11) — installs `src/marfutura_iot_audio.py`, real env template
- [x] Make provisioning and SD protection agree on the service user (R-7.4, F-17) — identical resolution expression in both
- [ ] Clean provision on the bench Zero 2W produces a working unit — **needs bench time**

**Done when:** the device can no longer detect nothing while reporting itself healthy, and every event it detects is recorded. *Code satisfies both; the bench run proves it.*

---

## Phase 2: Continuous capture **CODE COMPLETE 2026-08-13**

Verified by `tools/pipeline_soak_test.py`: real threaded pipeline, synthetic source, artificially slow storage — capture never pauses, one notified + N suppressed events, zero events lost, tree CONFORMANT.

- [x] Capture thread, continuous, never blocking (R-1.1) — sounddevice callback: copy, enqueue, count. Nothing else
- [x] Port the callback-and-queue capture from `legacy/modular-prototype/audio_capture.py` (D-006)
- [x] Detect the audio device by name (R-7.3, F-15) — `OCEANKIND_AUDIO_DEVICE_NAME` substring match
- [x] Bounded queues, explicit drop policy, published counts (R-1.3) — block queue drops oldest; transport overflow spools the event and sacrifices only clip audio
- [x] Classifier worker thread — every 5 s window classified, gapless
- [x] Transport worker with generalised retry covering uploads, not just WhatsApp (R-5.3) — event spool + heartbeat drain; shutdown preserves undelivered jobs
- [x] Telemetry on its own timer (R-6.1) — housekeeping thread; folded with the config poller (deliberate deviation, see ARCHITECTURE)
- [x] Remote config with HMAC verification, clamped ranges, version gating (R-8.3, F-09, F-10) — **converged with the backend 2026-08-22** per `docs/DATA-CONTRACT.md` §Device configuration: `config_version`, signature over the whole document, no key = no remote config, unknown keys reject whole, `detection_mode` runtime-tunable. Verified by smoke test §8. Pending: provision the shared HMAC key on both ends
- [x] Explicit timeouts on every network call (R-5.5) — Twilio `TwilioHttpClient(timeout=15)` (verified against the installed SDK), Azure connection/read timeouts, modem 3 s, serial 1 s
- [x] Telemetry off `/boot/firmware` (R-6.2, F-16) — CSV in `STATE_DIR` (tmpfs), trimmed, nothing writes to the boot partition
- [x] Battery dedup state out of hardcoded `/tmp` (R-6.4, F-19) — in `STATE_DIR`; survives service restarts. Reboot persistence needs the D-002 partition (one env var when it lands)
- [x] Split the monolith into modules — `oceankind/` package (config, storage, capture, detector, pipeline, telemetry, notify, health, main); launcher name kept for systemd
- [x] Synthetic audio source so the pipeline runs with no hydrophone (R-9.4) — `synthetic:tone|noise|impulse|silence`, time-scalable for tests
- [x] Overlapping analysis windows (`window_hop_s`, 2026-08-13) — boundary-straddling short events no longer diluted by phase luck; remotely tunable, clamped 1–5 s, default 5.0 (calibrated no-overlap behaviour). Choosing a value is the client's call (CLIENT-DEPENDENCIES 13); bench CPU numbers feed that decision
- [ ] Soak on the Zero 2W, resident memory measured against 512 MB (R-7.5) — **procedure: `docs/BENCH.md`**
- [ ] Duty cycle above 99 percent over 24 hours (R-1.1) — **procedure: `docs/BENCH.md`**; measured on-Mac at 100 % under slowed storage

---

## Phase 3: Detector registry **CODE COMPLETE 2026-08-26**

Harness only. No detection science. Verified by `tools/registry_test.py`.

- [x] `oceankind/detectors/` package, `detect(fs, samples, cfg, rms) -> {type, score, label, meta} | None` (R-3.1, D-014) — proven to carry an event type no detector produces today (`register()` plugin path)
- [x] Ordered registry driven by `OCEANKIND_DETECTORS` (R-3.2, F-01) — with a nuance forced by the config contract: the signed remote-config document only knows `detection_mode` and rejects unknown keys, so `DETECTION_MODE` survives as the *remote* surface (mapped: psd→[psd_tonal], rms→[rms], auto→[psd_tonal + real rms fallback on classifier error]) and the explicit list is device-local env until the `detectors` key is added to the contract (proposal in `docs/TODO.md`)
- [x] PSD algorithm's canonical home is `detectors/psd_tonal.py`; the standalone duplicate retired to `legacy/superseded-monolith/` (R-3.4, F-23)
- [x] MFCC path restored as `detectors/ml_mfcc.py` (F-24) — feature extractor ported verbatim from `tools/predict.py`, deps (librosa/sklearn/joblib) lazy and OPTIONAL, load failure is a health event, never silence. **Empirical note (2026-08-26):** run against synthetics, the model fires "blast" on a *tonal* clip and stays silent on an impulse — consistent with its FILTRO (pool-filter) training and strong evidence for client dependency 2. Also: bundle pickled with scikit-learn 1.6.1 (version-warns on newer) — pin sklearn on any unit enabling it, or have the client re-export
- [x] `event_type` and `detector` stamped on every event (R-3.3) — one typed event per detection; a chain firing twice on one clip yields two events, first notified with audio, rest suppressed
- [x] Every threshold settable through remote config, clamped (R-3.6) — except `ml_score_min`, device-local pending the same contract addition

**Done when:** a synthetic tonal input fires the vessel detector ✓, a synthetic impulse fires a detector and is labelled correctly ✓ (the `rms` member catches it, typed `unknown`; whether `ml_mfcc` should fire on impulses is exactly the open science of F-21/dep 2 — the harness runs whatever model the client supplies).

---

## Phase 4: Contract and storage **DEVICE SIDE DONE EARLY — 2026-08-13 (D-016)**

Pulled forward by the client's cutover decision: v2 only, new storage, prototypes frozen, no migration.

- [x] Write under `sites/{site_id}/`, nothing at the root (R-5.1) — `OCEANKIND_SITE` required, refuse-to-start without it
- [x] One blob per event, date-partitioned, append only (R-4.1, F-14) — manifest retired; bounded local spool with heartbeat retry for failed uploads
- [x] Publish `_sites.json` (R-5.2) — device merges its own entry at startup; coordinates live here (F-08 closed)
- [x] Omit empty buckets from power history (R-6.3) — verified with a deliberate gap in the conformance test
- [x] Non-finite floats as null, verified (R-4.6)
- [x] Direct event push to the backend, in parallel with the blob (contract §Event upload, dashboard D-022; 2026-08-26) — `push.py` on the transport worker: byte-identical body, idempotent, full status-code table, 401 as a health event, bounded push spool with heartbeat drain, blob invariant proven with the backend down. Verified by `tools/push_test.py` (27 checks vs a local fake backend). **Disabled until the client provisions `OCEANKIND_BACKEND_URL` + per-device `OCEANKIND_DEVICE_KEY`** (and registers the unit to its site — pushes 403 otherwise)
- [ ] Index tags on event blobs: site, event_type, detector, score, suppressed — **needs the real Azure account** (GPv2 + tag permissions unverifiable in local mode)
- [x] **`tools/validate_contract.py` passes with no errors** (R-5.6) — `tools/v2_conformance_test.py` drives the production emit code and validates: CONFORMANT. Rerun against a real bench run when hardware is available
- [ ] Dashboard v2 reader against the new storage — **now the dashboard's critical path**; until it exists the new fleet is invisible and new WhatsApp `?play=` links do not resolve

Aux blobs (`acoustic_indicators.json`, `ocean_conditions.json`): the device writes conformant empty stubs when absent; the real producers (client dependencies 8, 9) must write to the per-site paths.

---

## Phase 5: Survivable deployment **NOT STARTED**

- [ ] Decide the SD protection method (D-002)
- [x] ~~A/B directories with a symlink switch~~ — **superseded 2026-09-04**: rollback is by git commit, not by parallel directories. The checkout at `~/oceankind/code` is the source of truth, `.installed_sha` records what is actually installed, and reverting is `git reset --hard <previous>` + reinstall. Same guarantee, no symlink to leave dangling, and it gives version identity (which commit is this unit running?) that A/B directories never did
- [x] Post-restart health check, automatic reversion (R-7.1, F-06) — 2026-09-04: `update_oceankind.sh` verifies every update for `SETTLE_S` (60 s default) requiring the service active **and** `NRestarts` unchanged — `Restart=always` means "active" alone proves nothing, a crash loop reads as active between restarts. Failure reverts to the previous commit and re-verifies; the bad SHA goes in `.ota_failed_sha` so an unattended unit never retries a commit that already broke it
- [x] **Prove it: deliberately break an update and watch it revert** — 2026-09-04: `tools/ota_rollback_test.sh`, five scenarios against the real script with stubbed systemd, runs on a workstation. It caught two defects review missed (baseline `NRestarts` sampled before the restart; version identity taken from the checkout instead of the install)
- [ ] **Re-prove it on the bench unit** — the harness stubs systemd, so two paths are still unproven on hardware: passwordless sudo from cron, and the overlayfs two-phase reboot sequence. See the open TODO item
- [x] Watchdog for the hang case (R-2.7) — 2026-08-26: `watchdog.py` + `WatchdogSec=120`/`NotifyAccess=main` in the unit; pings gated on classify/transport thread heartbeats, so a hang restarts and a degraded-but-alive state does not. Verified with a real notify socket (smoke test §9). Bench soak exercises it for real under systemd
- [ ] Scoped write credential instead of the storage account key (R-8.4, F-07) — **mechanism decided, D-017**: one Entra service principal per device, custom write-only role, ABAC condition on `sites/{site}/*`, never delete, never list. Sub-tasks:
  - [ ] Custom RBAC role definition (`blobs/write` + `blobs/add/action` only) and the ABAC condition, scripted so provisioning is repeatable
  - [ ] `_get_blob_client` accepts a credential other than a connection string (`azure-identity`); connection string stays available for bench and local runs
  - [ ] Clock-skew failure is loud — a Pi Zero has no RTC, and Entra auth failure must reach `health.degraded_reason` and `status.json`, not look like a network fault
  - [ ] Read allowlist verified against the three blobs the device actually reads (`remote_config.json`, `_sites.json`, the aux stubs) — a literal write-only credential kills remote tuning silently
  - [ ] Blob versioning and soft delete on, so a compromised credential's overwrite is recoverable
  - [ ] **Prove it: revoke one device's credential on the bench and confirm the failure is loud, bounded and drains from the spool on re-provision**
- [ ] Per-device credential in `/etc/oceankind.env` and the config poll live against the deployed backend (R-8.3, F-10). Unreachable API means keep the last valid config, never fall back to defaults
- [ ] Write `docs/RUNBOOK.md` now that the decisions it documents exist
- [ ] Handover procedure. **We do not deploy; the client does** (D-015)

---

## Deferred

Compute platform migration (D-011). Record Zero 2W measurements during Phase 2 so the decision has data when it reopens.

Classifier validation against labelled distant recordings. Not ours, and arguably the largest open question in the project. See `CLIENT-DEPENDENCIES.md`.

---

## Blocked on the client

F-21 above all: whether the target is vessels, blasts or both. The contract carries `event_type` so the answer changes configuration rather than architecture, which is why none of the phases above wait on it.

Full list in `CLIENT-DEPENDENCIES.md`.
