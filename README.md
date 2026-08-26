# OceanKind Device

Underwater acoustic monitoring for a marine protected area on the Chilean coast. A solar-powered Raspberry Pi listens through hydrophones, classifies short clips, and publishes detections and telemetry to Azure Blob Storage. Alerts go out over WhatsApp, and clustered detections trigger a voice call.

Companion repository: [`Dashboard-Detector`](https://github.com/rodgpt/Dashboard-Detector), the web client that consumes everything this produces.

**We build the plumbing. The client provides the detection science.**

---

## Prove it works in 60 seconds

No hydrophone, no Azure, no detection science:

```bash
python3 raspberry-pi/tools/v2_conformance_test.py     # → CONFORMANT
```

That drives the **production emit code** into a local tree (site registry, notified/suppressed/fallback events, a real clip, status, power history, the retry spool) and runs `tools/validate_contract.py` over the result. Exit 0 answers the one question that is the contracted work: does this device emit blobs the dashboard can consume?

To validate any other tree — a bench run with `OCEANKIND_OUTPUT_DIR=./out`, or a real container's contents:

```bash
python3 tools/validate_contract.py ./out
```

---

## Hardware platform

| Component | Current |
|---|---|
| Board | Raspberry Pi 4 Model B, 2 GB |
| Bench unit | Raspberry Pi Zero 2W, **512 MB** |
| OS | Raspberry Pi OS, overlay filesystem enabled |
| Audio ADC | HifiBerry DAC+ ADC Pro **or** Raspberry Pi Codec Zero. Unconfirmed, see `docs/CLIENT-DEPENDENCIES.md` |
| Hydrophones | 2 × Aquarian H5 |
| Capture | 48 kHz, 2 channels, 16-bit, 5-second clips |
| Power | 40–100 W solar, 12 V LiFePO4, Victron BlueSolar MPPT over VE.Direct |
| Connectivity | ZTE 4G modem, cellular only |
| Draw | ~3.5 W |
| Access | **None.** Remote coastal site, no physical access, no working rollback |

Two sites are live: Zapallar and Matanzas. They may be running different builds.

---

## Layout

```
├── REQUIREMENTS.md          what this must do, numbered and testable
├── CLAUDE.md                rules for AI assistants. read before writing code
├── DECISIONS.md             stack-wide decisions, feeds both repositories
├── raspberry-pi/
│   ├── src/                 the production system
│   ├── models/              model.joblib (orphaned, see F-24)
│   ├── scripts/             provisioning, SD protection, OTA
│   ├── tools/               offline tools. never deployed
│   └── docs/                ARCHITECTURE, HARDWARE, PROGRESS, TODO
├── docs/
│   ├── DATA-CONTRACT.md     canonical. the dashboard mirrors this
│   ├── FINDINGS.md          24 verified defects, ranked
│   ├── CLIENT-DEPENDENCIES.md   what only the client can answer
│   ├── ARCHITECTURE.md      whole-stack view
│   └── research/            three-stage pipeline for new libraries
├── tools/
│   └── validate_contract.py the conformance test
└── legacy/                  nothing here runs. reference only
```

---

## What it produces

Everything under `sites/{site_id}/`, nothing at the container root.

```
_sites.json                                   site registry
sites/{site}/status.json                      device state, health, thresholds
sites/{site}/power_history.json               solar and battery, bucketed
sites/{site}/acoustic_indicators.json         soundscape rollup
sites/{site}/events/YYYY/MM/DD/*.json         one blob per detection, append only
sites/{site}/clips/YYYY/MM/DD/*.wav           audio
```

Schemas in `docs/DATA-CONTRACT.md`. **This repository holds the canonical copy; the dashboard mirrors it and CI enforces the match. Changing it changes both, and both ship together.**

---

## Things that will surprise you

**The PSD detector cannot fire on a sub-second event — by design it finds vessels.** `classify_clip()` runs a PSD tonal-peak algorithm that needs three of five seconds to be tonal; a sub-second broadband blast scores at most 0.2 against a 0.60 threshold. Client confirmed (2026-08-12) the PSD detector stays and a second detector is coming; the Phase 3 registry (D-014) is built for both. Read F-21 for the full picture.

**`model.joblib` is currently unused.** Nothing loads it. The ML-era naming and stub loader were retired in Phase 1 (`SCORE_MIN`, `DETECTION_LABEL` — the `FILTRO` pool-filter label is gone); the model returns as the second registry detector in Phase 3.

**The service refuses to start without secrets.** Since Phase 1, missing Twilio credentials are a startup failure, not a silent default (F-04). Bench units without Twilio set `OCEANKIND_ALLOW_NO_TWILIO=1`, which is declared in `health.degraded_reason`. Detection modes are `psd` | `rms` | `auto`, and every one of them can genuinely fire (F-01 fixed).

**`scripts/update_oceankind.sh` can strand the node.** Two reboots, no rollback, no physical access. Treat every invocation as dangerous.

**Writes go to RAM.** The overlay filesystem means anything written at runtime consumes memory and vanishes on reboot. The clip archive caps at 300 files (~290 MB) since Phase 1; discards are published in `health.clips_dropped`.

**`/boot/firmware` is read-only.** Nothing writes there anymore — the telemetry path was the F-16 defect, fixed in Phase 2. Runtime state goes to tmpfs and is flushed deliberately.

**The deployed units are prototypes, and this code no longer speaks their format.** Since D-016 (2026-08-13) the device emits the **v2 contract only** (`sites/{site}/…`, one blob per event, no manifest) to a **new** storage container. The prototypes at Zapallar and Matanzas keep writing v1 to the old blob, the dashboard keeps reading it, and nothing from this tree deploys to them. The new fleet is invisible to the dashboard until its v2 reader exists.

---

## Before you write code

`CLAUDE.md`, then `REQUIREMENTS.md`, then `docs/FINDINGS.md`, then `raspberry-pi/docs/ARCHITECTURE.md`. In that order.

Test on the bench Zero 2W before the production node. There is currently no way to recover a bad update remotely.
