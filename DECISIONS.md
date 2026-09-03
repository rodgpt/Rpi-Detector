# Decisions

Stack-wide decisions live here, at the root, because they feed downward. A decision recorded here becomes concrete tasks in `raspberry-pi/docs/PROGRESS.md` and `dashboard/docs/PROGRESS.md`. Never record a stack-level decision inside one codebase.

Domain-only choices that affect nothing outside their folder can live in that folder's `PROGRESS.md` instead. If in doubt, put it here.

**Statuses.** `OPEN` means nobody has decided. `BLOCKED` means it cannot be decided until something external happens. `PROPOSED` means there is a recommendation waiting for a yes. `DECIDED` means it is settled and has trickled down. `DEFERRED` means deliberately not now.

**Format.** Context, options, decision, consequences, and what it trickles into. A decision with no "trickles into" has not actually landed.

---

> **Decision numbering is per repository, not global.** `Rpi-Detector` and `Dashboard-Detector` each keep their own sequence, and the same number can mean different things in each. **Always qualify a citation from the other repository** (`Rpi-Detector D-017`), never a bare number. Known divergences are flagged in the entries themselves: D-016 and D-017.

## Index

| ID | Status | Decision | Blocks |
|---|---|---|---|
| D-001 | DECIDED | Folder structure and documentation layout | — |
| D-002 | OPEN | SD card protection method | Runbook, OTA design |
| D-003 | BLOCKED | Backend platform | Phase 2 entirely |
| D-004 | PROPOSED | No datastore. Storage layout plus blob index tags | Phase 2 data model |
| D-005 | CLOSED | Reconciling the deployed layout — moot, prototypes frozen (D-016) | — |
| D-006 | PROPOSED | Capture mechanism for the async pipeline | Phase 1 core work |
| D-007 | DECIDED | Multi-device blob layout — device side implemented via D-016 | Dashboard v2 reader |
| D-008 | DECIDED | What happens to cooldown-suppressed detections | Detection semantics |
| D-009 | BLOCKED | Which ADC is actually installed | Any hardware change |
| D-010 | BLOCKED | Which user the service runs as | Provisioning, F-03 severity |
| D-011 | DEFERRED | Compute platform migration | Nothing yet |
| D-012 | OPEN | Documentation language | All future docs |
| D-013 | DECIDED | Two repositories, split by deployment target | Layout, cross-references, CI |
| D-014 | DECIDED | Detector registry, not a selector. Both detectors kept | Phase 2, data contract |
| D-015 | DECIDED | Scope boundary: we build plumbing, client provides detection science | Everything |
| D-016 | DECIDED | v2 cutover now: new units write v2 to a new blob; prototypes frozen on v1 | Contract, Phase 4, D-005, D-007 |
| D-017 | DECIDED | One credential per device, write-scoped, revocable. No account key on any node | F-07, provisioning, dashboard read path |

---

## D-001 — Folder structure and documentation layout

**Status:** DECIDED, 2026-08-02

**Context.** Thirty files in one flat directory, with two Python systems that looked equally live and a provisioning script that installed the wrong one.

**Decision.** Four top-level folders: `raspberry-pi/`, `dashboard/`, `legacy/`, `docs/`. Each code folder is a self-contained root with its own `README.md`, `CLAUDE.md` and `docs/`. Shared concerns live in the top-level `docs/` umbrella. `DECISIONS.md` sits at the root.

**Consequences.** `requirements.txt` moved to `legacy/` because it described the prototype, so the production system currently has no dependency manifest. `raspberry-pi/scripts/setup.sh` still points at an entry point that is now in `legacy/`.

**Trickles into.** `raspberry-pi/docs/PROGRESS.md` Phase 1: write a real manifest, fix the provisioning script.

---

## D-002 — SD card protection method

**Status:** OPEN

**Context.** `protect_sd.sh` currently enables the Raspberry Pi OS whole-root overlay via `raspi-config nonint do_overlayfs 0` and also sets `/boot` read-only. This collides with two things: the telemetry CSV is written to `/boot/firmware` and therefore fails silently (F-16), and the clips directory is symlinked into RAM with no cleanup, which is a memory exhaustion path (F-03).

**Options.**

Whole-root overlayfs as today. Simplest, already working, but every persistent write needs a deliberate escape hatch and the OTA process has to disable and re-enable it, which is the two-reboot dance that can strand the node.

A dedicated writable partition with a read-only root. More setup, but telemetry and state get a real home, and the OTA process no longer has to toggle anything.

Read-write root with aggressive write minimisation. Simplest mentally, relies entirely on discipline, and one careless log line degrades the card.

**Why it is still open.** This choice determines the OTA design, where runtime state lives, and half the runbook. It should be decided before the OTA work in Phase 1, not during.

**Trickles into.** `docs/RUNBOOK.md` (currently a stub, waiting on this), `raspberry-pi/docs/ARCHITECTURE.md` filesystem strategy, `raspberry-pi/docs/PROGRESS.md` Phase 3.

---

## D-003 — Backend platform

**Status:** BLOCKED on Azure access

**Context.** Phase 2 needs a backend. The dashboard currently reads public blobs directly, which is the root cause of most security findings. What matters for tracking is the **scheme**, not the product: what the endpoints are, what authenticates, what holds the credentials. The product choice should be made late and recorded here.

**Options under consideration.** Azure Functions as a standalone HTTP API. Azure Static Web Apps with a managed API, which is interesting because the dashboard is already static-hosted on Azure and this bundles hosting, an API surface and a managed authentication layer in one product, potentially closing two findings at once. Azure Container Apps if the API outgrows serverless. Something non-Azure if there is a reason, though staying inside the existing subscription is worth real weight given the storage account, the IoT Hub and the static hosting already live there.

**Superseded 2026-08-25: it IS decided.** `Dashboard-Detector` D-019 built FastAPI + Postgres + React in three containers, tested. This entry stays as the record that the platform was chosen deliberately — by building and proving, not by default — and must no longer steer readers away from the answer that shipped.

**What to do before deciding.** Verify current capabilities and pricing from Microsoft's own documentation rather than from memory, per the research pipeline in `docs/research/RESEARCH.md`. Write it up as an analysis doc.

**Trickles into.** `docs/BACKEND-SCHEME.md`, and Phase 2 in both `PROGRESS.md` files.

---

## D-004 — Backend datastore

**Status:** REOPENED and superseded 2026-08-22 by `Dashboard-Detector` D-021. Was: PROPOSED, revised 2026-08-08, likely answer no datastore at all. **No device work follows; see the end of this entry.**

**Context.** The assumption was that filtering and pagination require an index, and `docs/IMPROVEMENT_REPORT.md` §3.5.2 proposed Cosmos DB while conceding SQLite would suffice. Checking what Azure Blob Storage actually does server-side changes that conclusion.

**What storage already provides.**

*Path sharding.* One blob per event under a per-device, per-date prefix makes "device X, last 24 hours" a prefix listing. No index, no query engine, no cost. This is the same change D-007 requires to kill the manifest race, so it is already on the roadmap and the filtering capability comes free with it. Time and device slicing is a path problem, not a query problem.

*Blob index tags.* Up to 10 tags per blob, keys 1-128 characters, values up to 256, 768 bytes per tag. Find Blobs by Tags supports `=`, `>`, `>=`, `<`, `<=`, `AND` and `@container` scoping. Enough for device, event type and confidence filtering server-side. Constraints that matter: comparison is lexicographic so numeric values must be zero-padded; there is no `OR` and no ordering in the query; indexing is usually sub-second but can lag up to ten minutes, which rules it out as the path for a live alert view; requires a general-purpose v2 account; small fixed monthly cost per tag.

*Query Blob Contents.* SQL over a single CSV or JSON blob, returning only matching rows. Block blobs only, 256 KiB maximum query expression, billed on data scanned and data returned. Good fit for the telemetry CSV behind the power chart. Scoped to one blob, so it does not span files.

*Conditional requests.* ETag and `If-None-Match` return 304 with no body when nothing changed. Not filtering, but the cheapest available reduction in polling cost and directly relevant to the Static Web Apps bandwidth cap.

**Proposal.** No datastore. Per-device, per-date blob paths, index tags for the few genuinely queryable attributes, conditional requests on the status endpoint, and client-side filtering within a bounded fetched window. At a few hundred detections a year this carries the system at a hundred times current volume.

**Reopen when.** A requirement appears for `OR` conditions, sorting on arbitrary fields, cross-device aggregates, or joins. None are on the roadmap.

**REOPENED 2026-08-22, on that condition.** Superseded by `Dashboard-Detector` D-021: the dashboard indexes detection events in Postgres and serves queries from there. Two of the four triggers fired. Cross-site views are on the roadmap, which is cross-device aggregates; and the volume assumption above, "a few hundred detections a year... a hundred times current volume", is wrong by roughly three orders of magnitude. It predated D-008, which records cooldown-suppressed detections rather than discarding them, so one alerting window is one blob and a single ten-minute boat pass is around 120 of them. The projection is millions a year at fleet scale, not tens of thousands.

The reasoning in this decision was sound and its storage analysis still holds; the input was wrong.

**No device work follows from it.** The device does not know the index exists and writes exactly what `docs/DATA-CONTRACT.md` already specifies. The contract gained one paragraph, under **Path scheme**, stating that partitions are keyed on `captured_utc` so a spooled event arrives late in an earlier day's prefix. That is a description of behaviour the device already has, not a change to it. The index tags this decision proposed were never implemented and are not needed; D-021 records why.

Nothing here reopens for the device unless the dashboard asks it to write a field it does not already write, and D-021 explicitly commits that it will not.

**Consequence for the backend.** It shrinks. The backend is not there to be a query engine; it is there because there is nowhere else to put a secret. Note that Find Blobs by Tags needs a SAS carrying the Filter permission, so the backend still wants to be the caller rather than the browser, but it needs no database behind it.

**Trickles into.** `docs/BACKEND-SCHEME.md`, D-003, D-007, and F-18 on the dashboard side.

---

## D-005 — Reconciling the deployed layout with this one

**Status:** CLOSED, 2026-08-13 — moot under D-016. The deployed units are frozen prototypes; nothing deploys to them from this tree. New units provision cleanly from `setup.sh`.

**Context.** `update_oceankind.sh` runs `git pull origin main` against a remote at `~/oceankind/code` on the device. This working copy has no version control and no link to that remote. After the restructure the two layouts have diverged.

**Options.** Restructure the remote to match this and update the OTA paths, which is clean but means the next update is a layout migration on a node with no rollback. Or keep the deployed layout flat and treat this repository as the source that gets flattened on deploy, which is safer short term and permanently confusing. Or fold this into the A/B OTA work, so the first A/B deployment is also the layout migration and the rollback path covers it.

**Position.** The third is the only one that does not require a risky one-way step. It does mean nothing deploys until the OTA work is done.

**Trickles into.** `raspberry-pi/docs/PROGRESS.md` Phase 3, `docs/RUNBOOK.md`.

---

## D-006 — Capture mechanism for the async pipeline

**Status:** PROPOSED

**Context.** The main loop calls `arecord` as a blocking subprocess per clip. The whole point of Phase 1 is that capture never stops.

**Options.** A long-running `arecord` piped to stdin, read by a capture thread into a ring buffer. Keeps the existing tool, no new dependency, straightforward to reason about. Or `sounddevice` with a callback and a queue, which `legacy/modular-prototype/audio_capture.py` already implements correctly, including detecting the device by name, and which both prior audits recommend recovering.

**Proposal.** Port the `sounddevice` approach forward, because the working code exists, it solves the hardcoded device index (F-15) in the same move, and callback-driven capture is the right shape. Verify memory behaviour on the 512 MB bench unit before committing.

**Consequence either way.** A bounded queue with an explicit drop policy and a published drop counter. On slower hardware an unbounded queue turns a deaf window into a silent backlog, which is the same failure wearing a different hat.

**Trickles into.** `raspberry-pi/docs/PROGRESS.md` Phase 2, `raspberry-pi/docs/ARCHITECTURE.md` threading model, `docs/DATA-CONTRACT.md` for the new status fields.

---

## D-007 — Multi-device blob layout

**Status:** DECIDED — implemented device-side 2026-08-13 via D-016 (`sites/{site_id}/…`, append-only per-event blobs, no manifest). The dashboard's v2 reader against the new storage is the remaining half. Original proposal below; the `devices/` prefix became `sites/` in the v2 contract, and the migration/deep-link concerns were dissolved by D-016 (no migration).

**Context.** Everything writes to a single flat container. `manifest.json` is downloaded, modified and re-uploaded on every alert, which already races against the retry path and will silently lose data with a second device (F-14). A second unit is confirmed for roughly six months out.

**Proposal.** Move to `devices/{device_id}/...` and replace the shared manifest with append-only per-event blobs. This removes the race rather than mitigating it, and it makes the second unit a configuration change rather than a rewrite.

**Consequences.** This is a deliberate, coordinated break of the data contract. The Pi and the dashboard have to change together. Existing history needs a migration script. Every WhatsApp alert already sent contains a `?play=` deep link built on the old naming, and those links must keep resolving.

**Trickles into.** Both `PROGRESS.md` files, Phase 4. `docs/DATA-CONTRACT.md` needs a versioned before-and-after.

---

## D-008 — What happens to cooldown-suppressed detections

**Status:** DECIDED, 2026-08-12. Implemented device-side per the proposal below (manifest entries flagged `suppressed: true`, no notification, clip deleted on every path). Dashboard-side distinct display still pending — see the stack `PROGRESS.md` Phase 1.

**Context.** F-03. A detection inside the 600-second cooldown currently falls through both branches: no upload, no manifest entry, no counter, and the clip is never deleted. A blast sequence appears in the data as a single event.

**The real question.** The cooldown exists to avoid spamming WhatsApp. It should never have governed whether an event is recorded. Notification rate-limiting and data retention are different concerns that got fused.

**Proposal.** Separate them. Suppressed detections are recorded as entries flagged `suppressed`, with no notification sent. The clip is deleted on every path regardless. The dashboard shows suppressed events distinctly rather than hiding them, since a hidden default filter on a blast-detection list is a way to mislead someone about how often blasts happened.

**Consequences.** The manifest becomes a truthful record, which changes what any frequency statistic derived from it says. That is the point, and it is worth telling the client explicitly, because their historical numbers undercount.

**Trickles into.** `raspberry-pi/docs/PROGRESS.md` Phase 1, `dashboard/docs/PROGRESS.md`, `docs/DATA-CONTRACT.md` manifest schema.

---

## D-009 — Which ADC is actually installed

**Status:** BLOCKED on field confirmation

**Context.** The code references a HifiBerry DAC+ ADC Pro. A separate system diagram shows a Raspberry Pi Codec Zero. Both prior reports flag the contradiction and neither resolves it.

**Why it matters.** Capture quality determines whether distant, heavily attenuated blasts are detectable at all, and it constrains any future board change. `docs/IMPROVEMENT_REPORT.md` §2.5 argues ADC quality matters more than compute for detection range.

**What is needed.** Someone with physical or SSH access runs `aplay -l` and `arecord -l` on the unit and reports the card name.

**Trickles into.** `raspberry-pi/docs/HARDWARE.md`, D-011.

---

## D-010 — Which user the service runs as

**Status:** BLOCKED on field confirmation

**Context.** F-17. `setup.sh` hardcodes `/home/pi` and `SERVICE_USER="pi"`. `protect_sd.sh` defaults to `marfutura` and creates the clips symlink in that user's home. If they ran as different users, `Path.home()` at runtime resolves somewhere the SD protection does not cover.

**Why it matters.** It determines whether F-03 fills RAM or wears the SD card. Different urgency, different fix.

**What is needed.** `systemctl cat oceankind` on the unit, and `ls -la ~/oceankind/` for the user it names.

**Related free diagnostic.** Does the dashboard's power chart render? If yes, the SD is unprotected. If empty, protection is on and telemetry has been discarded. It cannot currently be both.

**Trickles into.** `raspberry-pi/docs/PROGRESS.md` Phase 1, D-002.

---

## D-011 — Compute platform migration

**Status:** DEFERRED

**Context.** `docs/IMPROVEMENT_REPORT.md` §2 argues the Pi 4 is over-specified: 3.5W for a workload that is one blocking recording, a small FFT and a 53-parameter dot product. It recommends the Pi Zero 2W, or an ESP32-S3 if a simpler ADC suffices.

**Decision.** Not now. It matters only at ten or more units, the ESP32 path means porting feature extraction to C, and the current unit works.

**Free intelligence.** The bench unit is a Zero 2W, which is exactly the recommended target. Every hour of Phase 1 validation on it de-risks this decision at no extra cost. Record memory and feature-extraction timings while working there.

**Reopen when.** Multi-unit rollout is confirmed with numbers, and D-009 is answered.

---

## D-012 — Documentation language

**Status:** OPEN

**Context.** The dashboard interface is Spanish. Code comments in the production monolith are Spanish. The client-facing scope note is Spanish. The audits exist in both. Internal documentation is currently English.

**The question.** Whether internal docs stay English while everything client-facing is Spanish, or the whole repository moves to Spanish. Mixed is the current state and it is the least defensible of the three, because nobody knows which to write next.

**Constraint either way.** The dashboard UI stays Spanish. That is not in question.

**Trickles into.** Everything written from here on. Worth settling early and cheaply.

---

## D-013 — Two repositories, split by deployment target

**Status:** DECIDED, 2026-08-08

**Context.** The client owns two repositories: `github.com/rodgpt/Rpi-Detector` and `github.com/rodgpt/Dashboard-Detector`. Development and deployment both happen there.

**Decision.** Two repositories, split along the deployment boundary. The dashboard deploys via GitHub Actions to Azure static hosting. The device pulls its own repository over cellular. A monorepo would make the Pi clone a dashboard it never runs, onto an SD card over a metered link, and would fire Actions on every device commit without path filters. Deployment mechanics outweigh documentation convenience, and an earlier recommendation for a single repository was wrong on this point.

**Layout.**

`Rpi-Detector` is the current tree minus `dashboard/`: umbrella `CLAUDE.md`, `DECISIONS.md`, `docs/`, `raspberry-pi/`, `legacy/`. Every existing `../docs/...` reference from inside `raspberry-pi/` continues to resolve, so this costs no documentation churn.

`Dashboard-Detector` is the current `dashboard/` folder with its own `CLAUDE.md`, `src/` and `docs/`.

Locally, clone `Dashboard-Detector` into `Rpi-Detector/dashboard/` and gitignore that path. The working tree then looks identical to today, both relative chains resolve during development, and each half pushes to its own remote.

**Consequence: the data contract needs enforcement.** `docs/DATA-CONTRACT.md` is canonical in `Rpi-Detector` and mirrored into `Dashboard-Detector/docs/`. A step in the dashboard's Actions workflow fetches the canonical copy and fails the build on any difference. This turns the unenforced coupling described in `docs/ARCHITECTURE.md` into a hard gate, and it is only available because the repositories are separate.

The dashboard's remaining outward references become GitHub URLs rather than relative paths.

**Still open.** Whether the repositories are public. The Twilio token is present in the code that would be committed, so if public, F-04 becomes an emergency at first push rather than a serious item.

**Trickles into.** `docs/MOVES.md`, the dashboard's relative links, `dashboard/docs/PROGRESS.md` (add the CI contract check), D-005.

---

## D-014 — Detector registry, not a selector

**Status:** DECIDED, 2026-08-08. **IMPLEMENTED 2026-08-26** (`oceankind/detectors/`, verified by `tools/registry_test.py`): ordered chain via `OCEANKIND_DETECTORS`, one typed event per detection, `psd_tonal` + `rms` + the restored `ml_mfcc`, plugin registration for the client's next model, unloadable-detector-is-a-health-event. One deviation from the original proposal, forced by a newer decision: the signed remote-config contract (converged 2026-08-22) carries `detection_mode` and rejects unknown keys, so `DETECTION_MODE` was **not** deleted — it remains the remote-tunable surface, mapped onto registry compositions, while the explicit detector list is device-local env until a `detectors` key is added to the config contract with backend convergence (proposed in `docs/TODO.md`).

**Client confirmed 2026-08-12:** the PSD detector stays, and a second model is expected soon — the registry is its slot.

**Context.** F-21: the device detector was replaced with a PSD tonal-peak algorithm that cannot fire on a sub-second broadband event, while every project document describes a blast detector. The client is unavailable to resolve it and work should not stop.

**Decision.** Keep both detection paths and run them as an ordered chain that emits typed events. Not a selector.

A selector implies the detectors are alternatives. They are not: a blast is impulsive and broadband, a vessel is sustained and narrowband. They look for opposite characteristics in the same audio, so the useful operation is to run both and label what came out, not to choose.

There is also an existing selector, `DETECTION_MODE` with `ml`/`rms`/`auto`, which cannot work (F-01). It is replaced, not supplemented. Two broken selectors is the failure mode to avoid.

**Shape.**

```
raspberry-pi/src/detectors/
    __init__.py      registry, ordered execution, common interface
    psd_tonal.py     detect(clip) -> {type:"vessel", score, meta} | None
    ml_mfcc.py       detect(clip) -> {type:"blast",  score, meta} | None
```

Configuration is one ordered list, not a choice: `OCEANKIND_DETECTORS="psd_tonal,ml_mfcc"`. Every hit carries `event_type` and `detector` into the manifest entry. Both fields are contract additions and go into `docs/DATA-CONTRACT.md` in the same change.

`detector` is recorded per event specifically because the detector already changed once silently, around mid-July, and the manifest now spans two populations with no way to tell them apart. This must not be able to happen again without leaving a trace.

**Constraints this places on us.** The interface must express any event type the client later wants, including ones neither detector produces today. No detector may be able to lose an event through our plumbing. Every threshold must be remotely tunable and clamped, so the client can tune without a firmware update, which is the direct enabler for D-015.

**Cost.** Running both means both feature paths execute per clip. Irrelevant once the async pipeline lands, since detection is a worker; meaningful before then, because it lengthens the deaf window. Sequence the registry with or after the Phase 2 refactor, not before.

**Explicitly not decided here.** Which detector is correct, whether either works, and what the thresholds should be. See D-015 and `docs/CLIENT-DEPENDENCIES.md`.

**Hold.** Do not change which detector runs on a live unit while the client is away. Building the capability is safe; altering the behaviour of a deployed monitoring system without its owner is not.

**Trickles into.** `raspberry-pi/docs/PROGRESS.md` Phase 2, `docs/DATA-CONTRACT.md`, F-01, F-21, F-23, F-24.

---

## D-015 — Scope boundary: plumbing, not water

**Status:** DECIDED, 2026-08-08

**Context.** F-21 raises questions about detection correctness that are not answerable by software work, and the engagement has finite hours.

**Decision.** We build the plumbing. The client provides the water.

Ours: capture, queueing, the classification harness, transport, retry, storage layout, security, updates, telemetry, health reporting, the dashboard. Everything that carries a detection from hydrophone to human.

Theirs: what signal to look for, whether a detector works, threshold values on scientific grounds, the model, labelled validation audio.

**The obligation this creates.** The boundary is only honest if the plumbing never silently constrains the water. Three things stay ours because of that: the detector interface must express any event type, no event a detector produces may be lost by our code, and every threshold must be remotely tunable without a firmware update. A system where the client cannot tune without us is one where we have taken their half by accident.

**Consequence for deployment.** We cannot physically reach the production units. Development and testing run against our own hardware and our own Azure subscription. The contracted work can therefore be completed in full without any of it reaching the field. Who deploys, and when, is a client-side dependency that must be named before the final week. See `docs/CLIENT-DEPENDENCIES.md` item 11.

**Trickles into.** Phase 3 acceptance criteria, `docs/CLIENT-DEPENDENCIES.md`, `CLAUDE.md`.

---

## D-016 — v2 cutover now, on fresh storage. Prototypes frozen

**Status:** DECIDED, 2026-08-13 (client). **This is the stack-level decision** and the one `DATA-CONTRACT.md` cites.

`Dashboard-Detector` carries a different, now-superseded D-016 about a removable v1 adapter. When either repo says D-016 without qualification, it means this one.

**Context.** The v2 contract existed as a design with a Phase 4 migration plan: dual-write, history backfill, deep-link preservation, coordinated cutover with the dashboard. The client reframed it: the deployed units are prototypes, their data stays where it is, and the next unit starts clean.

**Decision.** The device emits **v2 only**, effective immediately. New units write to a **new storage account/container** (`OCEANKIND_STORAGE_CONTAINER`, new connection string), never to the prototypes' blob. The old blob stays frozen on v1 and the dashboard keeps reading it unchanged. **There is no migration**: no dual-write, no backfill, no v1 deep-link preservation on the new fleet.

**What this bought.** The entire Phase 4 migration burden disappeared. The manifest is retired, which removes the read-modify-write race (F-14) by construction and makes a suppressed-event record cost one small PUT instead of a full manifest cycle. Coordinates left `status.json` for `_sites.json`, closing F-08 completely. D-005 (reconciling the deployed layout) is moot — the deployed layout belongs to the frozen prototypes. D-007 (multi-device layout) is implemented device-side.

**What it costs.** The new fleet is invisible to the dashboard until the dashboard grows a v2 reader pointed at the new storage — that is now the dashboard's critical path. WhatsApp `?play=` links from new units carry v2 clip paths and will not resolve until that reader exists. `acoustic_indicators.json` and `ocean_conditions.json` are required by the contract but produced outside this repo; the device writes conformant empty stubs (only when the blob verifiably does not exist) so the tree validates and the tabs render empty rather than broken — the real producers overwrite them (client dependencies 8 and 9, now per-site paths).

**Proof.** `raspberry-pi/tools/v2_conformance_test.py` drives the production emit code into a local tree and runs `tools/validate_contract.py` over it: CONFORMANT. That validator is the acceptance test for the contracted work (R-5.6).

**Trickles into.** `docs/DATA-CONTRACT.md` (as-built section), `raspberry-pi/docs/PROGRESS.md` Phase 4, the stack `PROGRESS.md`, dashboard Phase 4 (v2 reader against new storage), F-08, F-14, D-005 (closed), D-007 (device side done).

---

## D-017 — One credential per device, write-scoped and revocable

**Status:** DECIDED, 2026-08-25

**Not the same as `Dashboard-Detector` D-017**, which covers provisioning the backend's per-device *API* key onto a unit at the bench. This one is about the *storage* credential. They are complementary and both apply; the numbers collided because the registers are independent.

**Context.** The device authenticates to blob storage with a storage account connection string (`OCEANKIND_STORAGE_CONNECTION_STRING`, `storage.py:35-43`). That is the account key: full read, write, delete and list over every container, every site. It sits in `/etc/oceankind.env` on an unattended node on a coastline, in a system whose adversaries have a direct interest in the hardware (F-08).

Blast radius is the obvious problem. The sharper one is that **a shared account key on unreachable devices cannot be rotated.** If it leaks, the options are to rotate it and silence the entire fleet with no way to redistribute the new key, or to leave it compromised. Neither is an incident response. This must be settled before the next batch of units lands, because credentials cannot be retrofitted onto hardware nobody can reach.

**Decision.** Three rules, effective for the new storage account and every unit provisioned against it.

1. **One credential per device.** Never shared. Revoking one unit affects only that unit; every other node keeps running.
2. **Write-scoped, never delete, never list, never read another site.** A device has no legitimate reason to delete a blob or enumerate a container.
3. **No storage account key on any node, ever.** The account key exists on developer machines for bootstrap and validation only.

**Mechanism.** Microsoft Entra service principal per device, RBAC scoped to the container, narrowed with an ABAC path condition on `sites/{site}/*`. Verified against Microsoft's documentation: blob path conditions do **not** require hierarchical namespace and work on a plain StorageV2 account, so the v2 tree in `docs/DATA-CONTRACT.md` is preserved unchanged. No built-in role is write-only — `Storage Blob Data Contributor` includes read and delete — so this needs a custom role carrying only `blobs/write` and `blobs/add/action`. Credential is a certificate rather than a client secret, so it is per-device rotatable and does not leak through logs.

Directory-scoped SAS (`sr=d`) was considered and rejected: it requires hierarchical namespace. Container-scoped SAS was rejected because it forces one container per site, which breaks the `sites/{site}/…` layout. IoT Hub file upload brokering was rejected because the hub rewrites the blob path to `{deviceId}/{blobName}` and the prefix cannot be controlled; bending the contract to suit a credential mechanism is the wrong trade.

**"Write-only" is not literal — there is a read allowlist.** The device reads three things today and a literal write-only credential would break two of them silently:

| Blob | Read by | Disposition |
|---|---|---|
| `sites/{site}/remote_config.json` | `main.py:204` | **Read required.** D-015 makes remote tuning without a firmware update non-negotiable |
| `_sites.json` | `storage.py:225`, read-modify-write at startup | Read + write required at container root until the backend owns the registry |
| `sites/{site}/acoustic_indicators.json`, `ocean_conditions.json` | `storage.py:255`, existence check | Read may be dropped; `ensure_aux_blobs` must degrade rather than fail |

Everything else stays denied. A stolen credential can append to one site's prefix and read that site's config. It cannot read another site, enumerate the container, or destroy anything.

**Defence in depth, independent of the credential model.** Blob versioning and soft delete on, so an overwrite by a compromised credential is recoverable and the scientific record is append-only in practice. `--allow-blob-public-access false` at account level, which closes the other half of F-07 by construction.

**What it costs.** `azure-identity` on the device and a rewrite of `_get_blob_client`. Entra token acquisition requires a correct clock: a Pi Zero has no RTC, and a long brownout followed by unreachable NTP produces an authentication failure that is indistinguishable from a network fault. That failure path must be loud (`health.degraded_reason`, `status.json`), or this fix introduces a silent-deaf mode of its own. Provisioning grows a per-device step that cannot be a copy-paste of one env file.

**Accepted risk.** If a unit is compromised but still standing, revoking its credential also removes its `remote_config.json` channel, so it cannot be re-provisioned remotely — it waits for a site visit. Events spool locally (`EVENT_SPOOL_MAX`) and drain on reconnection, so a bounded outage loses nothing; an unbounded one drops oldest-first and counts the drops. This is acceptable precisely because the credential is per-device: the same event under a shared key costs the entire fleet.

**Not now.** A secure element (ATECC608A, or a TPM module) would make the key physically unextractable from the SD card. That is a board revision and belongs to the hardware conversation for the next batch, not to F-07.

**The read half of F-07 is settled elsewhere.** `Dashboard-Detector` D-019 (2026-08-21) builds a FastAPI backend, and its `docs/SERVER-INFRASTRUCTURE.md` states the blob credential is *"held here and nowhere else"* — the browser never reaches storage, and clips are proxied through `/api/`. That is a better answer than the browser-held read-only SAS this decision originally proposed, and it needs no CORS. D-017 therefore governs the **device write** credential only. Confirm with that side that the backend's own credential is scoped rather than the account key, and that it points at the new account. Tracked in `docs/TODO.md`.

**Trickles into.** F-07 (this is its fix), `raspberry-pi/docs/PROGRESS.md`, `raspberry-pi/docs/TODO.md`, `docs/research/azure-storage-deployment.md`, provisioning scripts, `docs/RUNBOOK.md`.
