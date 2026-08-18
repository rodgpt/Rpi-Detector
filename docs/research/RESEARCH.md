# Research, analysis and deployment docs

Technical research for the whole stack. Adapted from the Lynch Protocol three-stage pipeline.

Kept at the umbrella level rather than per codebase, because the choices that need researching here mostly cross both: an Azure service decision affects the device, the dashboard and the future backend at once.

---

## The pipeline

Every new library, service or significant technique goes through three stages.

1. **Research** — Evaluate using [HOW_TO_RESEARCH_PACKAGES.md](./HOW_TO_RESEARCH_PACKAGES.md), write it up with [RESEARCH_TEMPLATE.md](./RESEARCH_TEMPLATE.md).
2. **Analysis** — The decision and its rationale: why this, how it fits, what was rejected. Named `{topic}-analysis.md`. The outcome gets recorded in `../../../DECISIONS.md`.
3. **Deployment** — Integration, configuration, known issues, troubleshooting. Named `{topic}-deployment.md`.

Not everything needs all three. Some things only need a deployment doc. Any new third-party dependency needs at least an analysis doc.

**One addition specific to this project.** Read the vendor's current documentation, not training data and not these files after they age. Azure SDKs deprecate methods, librosa changes function signatures, and Chart.js restructured its options between major versions. An analysis doc records a decision at a point in time; it does not stay true on its own.

---

## Guides

| File | Purpose |
|---|---|
| [HOW_TO_RESEARCH_PACKAGES.md](./HOW_TO_RESEARCH_PACKAGES.md) | Step-by-step evaluation process |
| [RESEARCH_TEMPLATE.md](./RESEARCH_TEMPLATE.md) | Template for analysis docs |

Both copied unmodified from the protocol. Do not edit them here.

---

## Analysis docs

| File | Topic | Recommendation |
|---|---|---|
| — | none yet | — |

---

## Deployment docs

| File | Topic | Status |
|---|---|---|
| — | none yet | — |

---

## Queued

Written up here so the queue survives a context reset. Each maps to an open decision.

**Backend platform** (D-003). Azure Functions, Static Web Apps with a managed API, Container Apps, or something outside Azure. Static Web Apps is worth real attention because the dashboard is already static-hosted on Azure and the product bundles hosting, an API surface and managed authentication, which would close F-07 and F-12 together. Verify current capabilities and pricing from Microsoft's documentation. Blocks all of Phase 5.

**Backend datastore** (D-004). Table Storage, Cosmos DB, Azure SQL, or SQLite on an Azure Files share. Cosmos is over-specified at tens of units and `IMPROVEMENT_REPORT.md` §3.5.2 already concedes SQLite would do. Decide after the platform.

**Capture mechanism** (D-006). `sounddevice` with a callback queue, as `legacy/modular-prototype/audio_capture.py` already implements, versus a long-running `arecord` pipe. Needs a memory measurement on the 512 MB bench unit before committing, since librosa and scikit-learn are already resident.

**SD card protection** (D-002). Whole-root overlayfs as today, a dedicated writable partition, or read-write with write minimisation. Determines the OTA design and most of the runbook. Decide before Phase 3.

**Config signing.** HMAC against a shared secret in the environment file is the obvious cheap answer for F-10. Worth twenty minutes of checking whether anything better fits without adding a dependency to a device that should stay lean.

**Classifier validation.** Not a package decision, but it belongs in the queue because it is the largest unexamined technical question in the project: whether a model trained on 5-second clips detects distant, attenuated blasts at all. `IMPROVEMENT_REPORT.md` §2.5. Needs labelled distant recordings that may not exist.

---

## Quick links

- [Repository README](../../README.md)
- [FINDINGS.md](../FINDINGS.md) — what is broken
- [DECISIONS.md](../../../DECISIONS.md) — what is settled and what is open
- [DATA-CONTRACT.md](../DATA-CONTRACT.md) — the seam between the codebases
- [PROGRESS.md](../PROGRESS.md) — stack-level state

**Last updated:** 2026-08-02
