# Technical requirements — OceanKind device

What the field device must do. Every requirement is testable and states how. Derived from the v2 data contract, the defect register, and the audited device code.

`MUST` is contracted. `SHOULD` is expected unless there's a reason. `WON'T` is explicitly out of scope, recorded so it doesn't get built by accident.

Last updated 2026-08-08.


> **Requirement numbering is per repository.** 41 R-IDs exist in both repos meaning different things (our R-1.1 is "never stop capturing"; the dashboard's is "one container"). **Always qualify a citation of the other repo's requirements** (`dashboard R-5.1`), never a bare number. Same convention as the decision registers.

---

## Scope

A solar-powered Raspberry Pi on a remote coastline listens through hydrophones, classifies short audio clips, and publishes detections and telemetry to Azure Blob Storage. Alerts go out over WhatsApp and, on clustered detections, by voice call.

**We build the plumbing. The client provides the detection science.**

Ours: capture, queueing, the classification harness, transport, retry, storage layout, security, updates, telemetry, health reporting. Everything that carries a detection from hydrophone to human.

Theirs: what signal to look for, whether a detector works, threshold values on scientific grounds, the model, labelled validation audio.

That boundary only holds if the plumbing never silently constrains the water, which produces three obligations of our own: R-3.1, R-3.6 and R-8.3.

**We cannot reach the production units.** Development and testing run against our own hardware and our own Azure subscription. Deployment is the client's.

---

## R-1 The core invariant

**R-1.1 MUST** never stop capturing audio. No network call, disk write, serial read, classification or lock may sit between one audio buffer and the next.

*Test:* `duty_cycle_pct` stays above 99 over 24 continuous hours, measured and published by the device itself, while uploads are failing.

**R-1.2 MUST** measure and publish its own duty cycle rather than asserting one. Two delivered audits disagree about the current miss rate and neither measured it.

**R-1.3 MUST** use bounded queues everywhere, with an explicit drop policy and a published drop count. An unbounded queue converts a deaf window into a silent backlog, which is the same failure wearing different clothes.

*Test:* feed the pipeline faster than it can drain; `clips_dropped` rises, memory does not.

---

## R-2 Never fail quietly

The system's entire purpose is to notice something. A component that degrades without saying so defeats it. Four catalogued defects have exactly this shape.

**R-2.1 MUST** report `health.detector_ok: false` and raise an alert when no detector loads. Returning empty and continuing is the pattern that produced F-02.

**R-2.2 MUST** report `health.audio_ok: false` when peak RMS stays below the floor across the health window, indicating a disconnected hydrophone.

**R-2.3 MUST** populate `health.degraded_reason` with something a human can act on whenever any health flag is false. Failing loudly means saying why.

**R-2.4 MUST** publish `health.clips_dropped` and `health.upload_backlog` continuously, not only when they are non-zero.

**R-2.5 MUST NOT** ship a detection mode that cannot produce an alert. `DETECTION_MODE=rms` and `auto` currently cannot fire under any input, while the startup banner advertises them as a fallback (F-01).

*Test:* for each configured detector, a synthetic input that should trigger it does trigger it.

**R-2.6 MUST** report which detectors are actually loaded in `detection.detectors`, and the thresholds actually in force in `detection.thresholds`. v1 published a threshold that did not participate in the alert decision (F-09).

**R-2.7 SHOULD** detect a hung process, not only a crashed one. systemd restarts crashes; nothing currently notices a hang.

---

## R-3 Detection harness

We build the harness. We do not build detectors or choose thresholds.

**R-3.1 MUST** expose a detector interface capable of expressing any event type, including ones no current detector produces. `detect(clip) -> {type, score, meta} | None`.

**R-3.2 MUST** run detectors as an ordered registry configured by one list, not a mutually exclusive selector. A blast is impulsive and broadband, a vessel is sustained and narrowband; they are complementary, not alternatives (D-014).

**R-3.3 MUST** stamp `event_type` and `detector` on every event. The detector changed once silently in mid-July, leaving the record spanning two populations with no way to tell them apart. That must not be able to recur.

**R-3.4 MUST** keep detector implementations in one place. The PSD algorithm currently exists twice, inlined in the monolith and in an unimported module, so the copy a reader opens is not the one that runs (F-23).

**R-3.5 MUST NOT** carry naming that describes a detector that no longer runs. `ML_THRESHOLD`, `ML_POSITIVE_LABEL` defaulting to `FILTRO`, `ML_MODEL_PATH` and a stubbed model loader all survive from a classifier that was removed (F-24).

**R-3.6 MUST** make every detector threshold settable through remote configuration, clamped to safe ranges. A system the client cannot tune without us is one where we have taken their half of the work by accident.

*Test:* change a threshold through remote config; the new value takes effect and appears in `detection.thresholds` without a restart or a firmware update.

---

## R-4 The detection record

**R-4.1 MUST** write one blob per detection, append-only, at `sites/{site}/events/YYYY/MM/DD/`. Never a shared file that is downloaded, modified and re-uploaded (F-14).

**R-4.2 MUST** record every detection, including ones whose notification is suppressed by cooldown, flagged `suppressed: true`. A cooldown throttles notifications; it must never decide what is recorded. v1 fused the two and silently discarded events (F-03, D-008).

*Test:* two detections inside the cooldown window produce two event blobs and one notification.

**R-4.3 MUST** record `captured_utc` and `uploaded_utc` separately. v1 conflated them, so every recorded time was actually upload time.

**R-4.4 MUST** set `clip.uploaded` truthfully, so a notification referencing a clip that never uploaded is detectable rather than appearing as a dead link (F-13).

**R-4.5 MUST** upload the clip before sending the notification.

**R-4.6 MUST** serialise every non-finite float as `null`. Python emits `Infinity`, which is not valid JSON and blanks the dashboard. This is a scar from a real outage.

**R-4.7 MUST** stamp `schema_version` on every blob written.

---

## R-5 Storage and transport

**R-5.1 MUST** write only under `sites/{site_id}/`. Nothing at the container root. The current layout puts the first site at the root and namespaces the second, so a third inherits an inconsistent scheme.

**R-5.2 MUST** publish `_sites.json` so the dashboard's site list is data, not code.

**R-5.3 MUST** queue events locally when the network is unavailable and drain the queue when it returns, with bounded retries and no data loss up to the bound.

**R-5.4 MUST** bound the local queue by a size the device can actually hold. `ARCHIVE_MAX_FILES` is 3,000, roughly 2.9 GB of clips, in a RAM-backed directory on a 2 GB device (F-22).

*Test:* fill the queue past its bound; the oldest are dropped, the count is published, memory holds.

**R-5.5 MUST** set an explicit timeout on every network call. The Twilio client currently has none, and an untimed call on the capture thread is an unbounded deaf window.

**R-5.6 MUST** produce output that passes `tools/validate_contract.py` with no errors.

---

## R-6 Telemetry

**R-6.1 MUST** publish `status.json` on a timer independent of the capture path.

**R-6.2 MUST** write runtime state only to locations that are actually writable. Telemetry currently goes to `/boot/firmware`, which SD protection makes read-only, so it fails silently and the power chart never populates (F-16).

**R-6.3 MUST** omit empty buckets from `power_history.json` rather than emitting nulls. Gaps are how the dashboard reconstructs uptime across reboots.

**R-6.4 MUST** persist state that should survive a reboot outside RAM. Battery alert de-duplication lives in `/tmp`, so every reboot re-arms it and duplicates warnings (F-19).

**R-6.5 MUST NOT** publish a self-reported liveness string. Liveness is derived from `last_seen`.

---

## R-7 Deployment and recovery

**R-7.1 MUST** provide an update path that cannot leave the unit unreachable. A/B directories, a post-restart health check, automatic reversion on failure (F-06).

*Test:* deliberately break an update on the bench unit; it reverts unattended and comes back on the previous version.

**R-7.2 MUST** provision correctly from a clean image with no manual intervention. `setup.sh` currently installs an entry point that lives in `legacy/`, and the dependency manifest describes an abandoned prototype (F-11).

**R-7.3 MUST** detect the audio device by name. A hardcoded ALSA index breaks on USB re-enumeration, on a node nobody can reach (F-15).

**R-7.4 MUST** agree with itself about which user owns the service. Provisioning assumes `pi`, SD protection assumes `marfutura`, and runtime state lands wherever `Path.home()` resolves (F-17).

**R-7.5 SHOULD** run on the Raspberry Pi Zero 2W as well as the Pi 4, and MUST record memory and feature-extraction timings on the Zero 2W so the hardware migration decision has data (D-011).

---

## R-8 Configuration and secrets

**R-8.1 MUST** load every secret from `/etc/oceankind.env` and refuse to start if a required one is absent. No literal defaults. `os.environ.get("X", "<actual secret>")` is how a live Twilio token reached source, a backup, two bytecode caches and a git remote (F-04).

**R-8.2 MUST NOT** carry site coordinates in source. They belong in `_sites.json` (F-08).

**R-8.3 MUST** validate, clamp and authenticate remote configuration before applying it. It is currently unsigned and unclamped (F-10).

*Test:* an out-of-range threshold is clamped and logged; an unsigned payload is rejected.

**R-8.4 MUST** write to storage using a scoped credential rather than the storage account key, once the container is private (F-07).

---

## R-9 Non-functional

**R-9.1 MUST** run unattended on solar power with no physical access. Every change must be survivable; a change that can leave the unit unreachable is worse than the bug it fixes.

**R-9.2 MUST** stay within the power budget. Roughly 3.5W today, on a panel and battery sized for it.

**R-9.3 MUST** treat the SD card as consumable. Minimise writes, keep runtime state off it, and never write to a partition that protection has made read-only.

**R-9.4 MUST** be testable end to end with no hydrophone, no detection science and no Azure account, using a synthetic audio source and a local output directory.

**R-9.5 SHOULD** keep dependencies light enough for a 512 MB device.

---

## R-10 Contract conformance

**R-10.1 MUST** keep `docs/DATA-CONTRACT.md` canonical. The dashboard repository mirrors it and CI enforces the match.

**R-10.2 MUST** update the contract in the same change as any code that emits a new field, and check the dashboard's progress before assuming a consumer exists.

---

## Out of scope

Detection science: what to look for, whether a detector works, threshold values, the model, labelled audio. Deploying to production units. Anything requiring physical access. Retraining or validating a classifier.

---

## Open, blocked on the client

Full list in `docs/CLIENT-DEPENDENCIES.md`. The ones that shape this codebase: whether the target is vessels, blasts or both (F-21); whether `model.joblib` was ever trained on real blasts; what produces `ocean_conditions.json` and the acoustic aggregator; and whether `bearing_deg` has a producer anywhere.

None of them block R-1 through R-8. The contract carries `event_type` precisely so the answer to F-21 changes configuration rather than architecture.
