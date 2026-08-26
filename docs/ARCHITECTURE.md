# System architecture — as built

**Last updated: 2026-08-25.** Describes what exists and runs today. The previous revision of this file (10 Aug) described the pre-engagement system — no backend, public container, static-hosted single file — and none of that is true anymore. Device internals: `raspberry-pi/docs/ARCHITECTURE.md`. Decisions cited: device register unless qualified.

---

## The three parts

```
┌─────────────────────┐   writes v2 blobs    ┌──────────────────────┐
│ DEVICE               │ ───────────────────► │ AZURE BLOB STORAGE   │
│ Raspberry Pi, solar, │                      │ private container    │
│ 4G. oceankind/ pkg,  │ ◄─────────────────── │ sites/{site}/…       │
│ 4 threads + main     │   polls signed       │ one blob per event   │
└─────────────────────┘   remote_config.json  └──────────┬───────────┘
                                                          │ reads (only reader)
                                                          ▼
┌─────────────────────┐    HTTPS + session    ┌──────────────────────┐
│ BROWSER              │ ◄──────────────────► │ BACKEND + FRONTEND   │
│ React SPA, served by │                      │ FastAPI · Postgres   │
│ nginx container      │                      │ 3 containers (D-019) │
└─────────────────────┘                      └──────────────────────┘
```

**Device** (`raspberry-pi/src/oceankind/`, ~2,400 lines, ten modules): capture thread with bounded queues, classifier worker, transport worker, telemetry, housekeeping main thread. Emits the v2 contract only (D-016): `sites/{site}/events/YYYY/MM/DD/`, append-only, one blob per detection, suppressed events recorded. Fail-loud health surface in `status.json`. Verified by `raspberry-pi/tools/v2_conformance_test.py` driving the real emit code through `tools/validate_contract.py`.

**Storage** is the seam and the only coupling. The device writes without the backend existing — that property is deliberate and load-bearing (an unattended solar node must not depend on our uptime to record a detection). Storage is also the archive: clips at terabyte scale over years belong here, not in a database. Schemas: `docs/DATA-CONTRACT.md`, canonical here, mirrored to the dashboard, enforced by validator and `make contract`.

**Backend and frontend** (`Dashboard-Detector`): FastAPI owning users (argon2, throttled login), roles, per-site access enforced per request, per-device API credentials, all secrets, and every storage read. Postgres holds users/roles/sites/device records — and, per Dashboard D-021, a derived index of detection events (the blob store is the record, never the query engine). React + Vite frontend served by nginx; the browser never holds a storage credential; clips are proxied.

**Configuration flows the other way**, backend → storage → device: the backend clamps, signs (HMAC-SHA256, `OCEANKIND_CONFIG_HMAC_KEY`) and writes `sites/{site}/remote_config.json`; the device polls, verifies, and rejects whole on any failure (D-020, F-10 closed). Convergence of both implementations verified by independent recompute on 2026-08-22.

---

## Security, current state

Built: authentication for every data route, per-device API keys (hashed, shown once, revocable), signed device config with mandatory verification, refuse-to-start on missing secrets, private container on the new storage account, coordinates out of source (`_sites.json`).

Remaining: device still authenticates to storage with a connection string — write-scoped per-device credential is device D-017 (decided 2026-08-25, not yet built). Twilio token rotation blocked on client console access (F-04, code side fixed). Watchdog for hangs is Phase 5 (R-2.7).

**The prototypes at Zapallar and Matanzas are frozen** (D-016): still v1, still the old public blob, unreachable, nothing deploys to them from either repo. Their exposure is inherited, documented, and closed only by decommissioning.

---

## Failure modes

| Failure | Behaviour today |
|---|---|
| Backend down | Device unaffected; keeps recording to storage. Dashboard shows failed fetches visibly (R-7.1) |
| Storage unreachable from device | Events spool locally, bounded at 500, drain on return; overflow counted in `health.events_dropped` |
| Clip upload fails | Event recorded anyway, `clip.uploaded: false`, upload before notify (F-13) |
| Config blob tampered/unsigned | Rejected whole, named in `health.degraded_reason`; last valid config stays in force |
| Detector fails to load | `health.detector_ok: false` + alert (F-02). Silence is never healthy |
| One data source malformed | That panel degrades; the rest of the dashboard stands (R-7.3) |
| Index drifts from storage | Dashboard R-12.5: measured per day/site, surfaced as a fault, rebuildable by replay |

---

## What this file is not

Not the device's threading model (`raspberry-pi/docs/ARCHITECTURE.md`), not the API surface (`Dashboard-Detector/docs/API-CONTRACT.md`), not the blob schemas (`docs/DATA-CONTRACT.md`), not deployment (`Dashboard-Detector/docs/SERVER-INFRASTRUCTURE.md`, `raspberry-pi/docs/BENCH.md`).
