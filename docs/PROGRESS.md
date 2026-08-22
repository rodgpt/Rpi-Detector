# Implementation progress — whole stack

Rollup across both codebases. Phase numbers are shared: Phase 3 means the same thing in `raspberry-pi/docs/PROGRESS.md` and `dashboard/docs/PROGRESS.md`. Check off here only when every codebase involved has checked off its part.

Detail lives in each codebase's own `PROGRESS.md`. This file answers one question: where is the project.

Work is driven by `FINDINGS.md` (what is broken) and `../DECISIONS.md` (what has been settled). A task with no finding and no decision behind it should not be here.

---

## Current status

**Phase 0 complete.** Repository restructured, documentation layer in place.
**Phase 1: device side code complete (2026-08-12).** Open: token rotation (client), 24 h baseline + clean provision (bench time), the three field questions, and all dashboard-side items.
**Phase 4 contract half: device side done early (2026-08-13, D-016).** v2 only, new storage, prototypes frozen, no migration. The dashboard v2 reader is now the critical path for the new fleet; the security half (SAS, signed config) still needs Azure access.

---

## Phase 0: Structure and context **COMPLETE**

- [x] Four-folder structure, each codebase a self-contained root
- [x] Twenty defects catalogued and verified against source (`FINDINGS.md`)
- [x] Reversal manifest for the restructure (`MOVES.md`)
- [x] Data contract extracted and documented (`DATA-CONTRACT.md`)
- [x] Decision register opened (`../DECISIONS.md`)
- [x] CLAUDE.md at umbrella and per codebase

---

## Phase 1: Truth and safety **NOT STARTED**

The system must stop being able to fail silently, and the record it produces must stop being wrong. Nothing here needs Azure access, so it is unblocked today. Highest risk removed per hour of anything in the project.

### raspberry-pi — **code complete 2026-08-12**
- [x] Twilio credentials and GPS out of source, loaded from `/etc/oceankind.env`, fail loud if absent (F-04, F-08)
- [ ] Twilio token rotated and purged from the git remote (F-04) — needs Twilio console access
- [x] Model load failure raises a WhatsApp alarm and sets `health.detector_ok: false` (F-02)
- [x] Real RMS fallback built, `DETECTION_MODE` actually gates the decision (F-01)
- [x] `decided_by` reports what happened (F-01)
- [x] Clips deleted on every path including error paths, plus a startup sweep (F-03)
- [x] Cooldown-suppressed detections recorded as `suppressed` rather than erased (F-03, D-008)
- [x] Loop instrumented, `duty_cycle_pct` and `deaf_seconds_total` published (F-05)
- [x] `schema_version` added to every blob written (contract debt 1)
- [x] Real `requirements.txt` written for the production system (F-11)
- [x] `setup.sh` `ExecStart` corrected (F-11)

### dashboard
- [ ] Dead `SAS_URL_KEY` constant removed (X-01)
- [ ] Suppressed detections displayed distinctly, not hidden (D-008)
- [ ] Unknown `schema_version` warns rather than breaks (contract debt 1)
- [ ] `detector_healthy: false` surfaced prominently (F-02)

### Field questions, blocking decisions
- [ ] Does the power chart render? Answers D-002 and F-16
- [ ] Which user does the service run as? Answers D-010, sets F-03 urgency
- [ ] Which ADC is installed? Answers D-009

### Contract changes in this phase
`decided_by` semantics, `schema_version` added, `suppressed` entries added, `detector_healthy` added. All require `DATA-CONTRACT.md` updated in the same change.

---

## Phase 2: Continuous capture **DEVICE CODE COMPLETE 2026-08-13**

The hydrophone stops going deaf: capture streams continuously on its own thread; classification and transport are parallel workers behind bounded queues.

### raspberry-pi — code complete, bench soak pending
- [x] Capture module ported from `legacy/modular-prototype/audio_capture.py` (D-006)
- [x] Audio device detected by name, not index (F-15)
- [x] Bounded queue with explicit drop policy and published drop counter
- [x] Classifier worker on its own thread
- [x] Transport worker, retries generalised to cover uploads not just WhatsApp
- [x] Upload before notify (F-13, carried over from Phase 1)
- [x] Telemetry moved off the read-only boot partition (F-16, F-19 — reboot persistence waits on D-002)
- [x] Monolith split into modules (`oceankind/` package)
- [x] Remote config: **converged with the backend 2026-08-22** (DATA-CONTRACT §Device configuration) — `config_version`, signature over whole document, no key = no remote config, unknown keys reject whole, `detection_mode` runtime-tunable (F-09, F-10). Pending: provision the shared HMAC key on both ends
- [ ] Soak test on the bench Zero 2W, memory measured — **needs bench**
- [ ] Duty cycle ≥99 % over 24 h on bench — **needs bench**

### dashboard
- [ ] Drop counter and duty cycle surfaced in the sensor tab (v2 reader work, deferred by client)

---

## Phase 3: Survivable deployment **NOT STARTED**

The node stops being one bad update away from unreachable.

### raspberry-pi
- [ ] SD protection method decided (D-002)
- [ ] A/B code directories with symlink switch
- [ ] Post-restart health check with automatic reversion (F-06)
- [ ] Rollback proven by deliberately failing an update on the bench unit
- [ ] Deployed layout reconciled with this repository (D-005)
- [ ] Deployed to production, observed 24 hours
- [ ] `RUNBOOK.md` written now that the decisions it depends on exist

---

## Phase 4: Lockdown and multi-device **REFRAMED BY D-016 — device contract side DONE 2026-08-13**

Client decision (D-016): prototypes frozen on v1 in the old blob, dashboard keeps reading it; the device emits **v2 only** to a new storage container. No migration, no dual-write, no deep-link preservation for old alerts.

### Both
- [x] Blob layout moved to per-site paths `sites/{site_id}/...` (D-007) — device side; conformance-tested
- [x] Append-only per-event blobs replace the shared manifest (F-14) — device side; manifest retired
- [x] ~~Migration script for existing history~~ — cancelled by D-016 (prototype data stays where it is)
- [x] ~~Old `?play=` deep links still resolve~~ — cancelled by D-016; NEW links need the dashboard v2 reader
- [ ] **Dashboard v2 reader against the new storage** — the new fleet is invisible until this exists

### raspberry-pi
- [ ] Container set private, Pi writes via scoped write SAS instead of the account key (F-07)
- [ ] Device configuration moved onto the signed, clamped backend endpoint; `remote_config.json` retired (F-10). Device and dashboard halves ship together
- [ ] Real detection parameters exposed to remote config (F-09)

### dashboard
- [ ] Reads via scoped read-only SAS (F-07)
- [ ] Device selector
- [ ] Windowed power fetch and paginated event list (F-18)

---

## Phase 5: Backend **NOT CONTRACTED — specified only**

Delivered as a written specification and quote, not as code. Depends on D-003 and D-004.

- [ ] Backend platform decided and written up as an analysis doc (D-003)
- [ ] Datastore decided (D-004)
- [ ] `BACKEND-SCHEME.md` filled in with the concrete scheme
- [ ] Estimate and scope note handed over

---

## Not in scope

Compute platform migration (D-011). Deferred until multi-unit numbers are confirmed.

Enterprise authentication, role hierarchies, OAuth. `SYSTEM_REVIEW.md` §5.4 is right that invite links suit a small team.

Anything in `legacy/`.
