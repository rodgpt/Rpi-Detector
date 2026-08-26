# raspberry-pi

Everything that runs on the device in the field.

The name says Raspberry Pi because that is what is deployed: a Pi 4 Model B in the field design, a Pi Zero 2W (512 MB) on the bench as the worst case. If the board changes, the folder name stays and this paragraph gets updated.

---

## Layout

```
raspberry-pi/
├── src/
│   ├── oceankind/               THE production system. Ten modules, ~2,400 lines
│   │   ├── main.py              entrypoint, housekeeping thread
│   │   ├── capture.py           audio thread, bounded queues, never blocks
│   │   ├── pipeline.py          classify → decide → record → notify
│   │   ├── detector.py          PSD tonal + RMS decision (harness, not science)
│   │   ├── storage.py           v2 blob layout, spool + retry, local mode
│   │   ├── telemetry.py         VE.Direct, modem, power history
│   │   ├── notify.py            WhatsApp, voice calls, IoT Hub heartbeat
│   │   ├── health.py            the fail-loud surface in status.json
│   │   └── config.py            env + signed remote config verification
│   ├── marfutura_iot_audio.py   thin launcher; kept so the systemd unit never changes
│   └── detector_psd.py          reference copy of the PSD algorithm
├── models/model.joblib          unused today; returns as a registry detector (Phase 3)
├── scripts/                     setup.sh (fixed, F-11), protect_sd.sh, update_oceankind.sh (no rollback — Phase 5)
├── tools/                       workstation-only: v2_conformance_test.py, phase1_smoke_test.py, predict.py
├── requirements.txt             real dependencies of the package (F-11 fixed)
└── docs/                        ARCHITECTURE, HARDWARE, PROGRESS, TODO, BENCH
```

## What it does

Four threads plus housekeeping (see `docs/ARCHITECTURE.md`). Capture never blocks; classification and transport run off bounded queues with published drop counts; every detection is recorded as one append-only v2 blob, suppressed ones included; health is published complete on every heartbeat. Configuration arrives as a signed blob and is rejected whole on any verification failure.

The v1 description of this folder — one 1,308-line file, strictly sequential, no requirements.txt — is history. Those monoliths are in `../legacy/superseded-monolith/`.

## Prove it works

```bash
python3 tools/v2_conformance_test.py     # drives the real emit code → CONFORMANT
python3 tools/phase1_smoke_test.py       # fail-loud + signed-config behaviour
```

Bench procedure, from blank SD to acceptance numbers against real Azure: `docs/BENCH.md`.
