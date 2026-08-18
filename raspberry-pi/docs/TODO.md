# TODO — raspberry-pi

Pending items that are not part of the phase roadmap in `PROGRESS.md`. Anything phased goes there instead.

Add TODOs here the moment they occur. Not later. Context gets compressed at unpredictable intervals and an undocumented TODO is a lost one. This costs ten seconds and saves an hour.

Cross-cutting items, or anything that also touches the dashboard, go in `../../docs/TODO.md`.

Categories: `[HW]` `[SW]` `[Audio]` `[Power]` `[Config]` `[OTA]` `[Net]` `[Sec]` `[DX]`

---

## Pending

- [ ] **[Audio] Confirm channel handling** — Capture is 2-channel at 48 kHz, the model was trained at 22.05 kHz mono, and `librosa.load(..., mono=True)` downmixes both hydrophones into one signal. Two hydrophones are being averaged into a single channel before classification. Whether that is intended, and what it costs in detection sensitivity, is unexamined. `src/marfutura_iot_audio.py:343`

- [ ] **[Audio] Two hydrophones, no spatial use** — Two Aquarian H5 units are deployed and nothing uses the difference between them. Time delay between channels is a bearing estimate for free, and would also be a strong false-positive filter, since a real distant blast arrives at both with a consistent delay and local noise does not.

- [ ] **[SW] IoT Hub limb is dead weight** — The Pi connects to Azure IoT Hub and sends alerts and heartbeats. Nothing consumes them. It costs a network call inside the deaf window on every alert and every heartbeat. Either revive it as part of the D-003 backend decision or remove it. `src/marfutura_iot_audio.py:868-908`

- [ ] **[SW] IoT Hub payload still v1-ish** — `send_message` sends flat alert/heartbeat JSON that predates the v2 contract. Nothing consumes it (see the dead-limb item), so it was left alone in the cutover; if the D-003 backend revives IoT Hub, redesign the payload then.

- [ ] **[Audio] Pre-roll for event clips** — Client idea (2026-08-13): attach the seconds *before* the trigger to an event clip for context. Cheap now that all audio streams through memory (keep N blocks in a ring, prepend on event). Held back deliberately: clip length is what the detector is calibrated on, so changing it is a client/science decision (`docs/CLIENT-DEPENDENCIES.md` item 12 territory) and a contract change (`clip.duration_s`). Ask before building.

- [ ] **[SW] Runtime state lives on tmpfs pending D-002** — `STATE_DIR` (`/tmp/oceankind`: event spool, battery state, telemetry CSV) evaporates on reboot; a reboot during a long outage loses spooled events and the 72 h power window restarts. All of it moves with ONE env var (`OCEANKIND_STATE_DIR`) once D-002 lands a persistent partition. Note it in D-002 when decided.

- [ ] **[Sec] `.bak` and `.pyc` still hold the live token** — In `../../legacy/build-artifacts/`. Harmless the moment the token is rotated, dangerous until then. There is no version control here, so the `.bak` is currently the only record of the pre-June state. Delete it after rotation, or keep it knowingly.

- [ ] **[DX] Extend the smoke test into a Phase 2 regression check** — `tools/phase1_smoke_test.py` (added 2026-08-12) covers classify_clip on synthetic tonal/impulse audio, detector-failure accounting, duty-cycle arithmetic, the health block and JSON sanitisation. Before the Phase 2 refactor, capture its classifier outputs as golden values so the module split provably does not change classification.

- [ ] **[DX] `tools/` shares the production dependency set** — `predict.py` and `test_model_on_alerts.py` pull librosa and scikit-learn. Fine on a workstation. Worth a note in the future `requirements.txt` so nobody installs the tools' needs onto a 512 MB device.

- [ ] **[HW] Enclosure, cabling and thermal are undocumented** — Nothing in this repository describes the physical installation. If someone has to visit the site, there is no reference for what is there. Belongs in `HARDWARE.md` once someone with access can photograph it.

---

## Done

<!--
  Format: - [x] **[Category] Title** DONE (YYYY-MM-DD) — how it was resolved.
-->

- [x] **[Config] `ALERT_THRESHOLD` is dead weight after F-09** DONE (2026-08-12) — repurposed instead of removed: it is now the real threshold of the working `rms` mode (F-01 fix), env-configurable as `OCEANKIND_ALERT_THRESHOLD`, and published truthfully in `detection.thresholds.rms_threshold`. Contract updated.
- [x] **[DX] No tests of any kind** DONE (2026-08-12) — minimal ask satisfied by `tools/phase1_smoke_test.py`; superseded by the regression-check item above.
- [x] **[SW] Manifest cap silently discards history** DONE (2026-08-13) — dissolved by D-016: the manifest is retired. Events are append-only immutable blobs; nothing truncates. Retention policy remains an open client question (contract §Open questions).
- [x] **[SW] Suppressed-event manifest writes lengthen the deaf window** DONE (2026-08-13) — dissolved by D-016: recording a suppressed event is now one ~1 KB PUT, not a manifest download+re-upload cycle.
- [x] **[Net] Twilio client has no timeout** DONE (2026-08-13) — `TwilioHttpClient(timeout=15)` on every Twilio call (messages, calls), parameter verified against the installed SDK. All network now lives off the capture path anyway (Phase 2).
