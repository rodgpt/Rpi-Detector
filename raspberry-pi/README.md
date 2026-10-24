# raspberry-pi

Everything that runs on the device in the field.

The name says Raspberry Pi because that is what is deployed: a Pi 4 Model B in the field design, a Pi Zero 2W (512 MB) on the bench as the worst case. If the board changes, the folder name stays and this paragraph gets updated.

---

## Layout

```
raspberry-pi/
├── src/
│   ├── oceankind/               THE production system. Ten modules, ~2,300 lines
│   │   ├── main.py              entrypoint, housekeeping thread
│   │   ├── capture.py           audio thread, bounded queues, never blocks
│   │   ├── pipeline.py          classify → decide → record → push (the backend notifies, D-020)
│   │   ├── detector.py          PSD tonal + RMS decision (harness, not science)
│   │   ├── storage.py           v2 blob layout, spool + retry, local mode
│   │   ├── push.py              event push, heartbeat, config fetch — one derived key
│   │   ├── telemetry.py         VE.Direct, modem, power history
│   │   ├── ota.py               converge to the release assigned from the panel (D-021)
│   │   ├── health.py            the fail-loud surface in status.json
│   │   └── config.py            env, one secret → two keys, signed remote config verification
│   ├── marfutura_iot_audio.py   thin launcher; kept so the systemd unit never changes
│   └── detector_psd.py          reference copy of the PSD algorithm
├── models/model.joblib          unused today; returns as a registry detector (Phase 3)
├── scripts/                     setup.sh (fixed, F-11), protect_sd.sh, update_oceankind.sh (journaled, rolls back, converges to the assigned release)
├── tools/                       workstation-only: `make test` (smoke, conformance, soak), push_test.py, ota_rollback_test.sh, ota_fault_test.sh, predict.py
├── requirements.txt             real dependencies of the package (F-11 fixed)
└── docs/                        ARCHITECTURE, HARDWARE, PROGRESS, TODO, BENCH, OTA-FAULT-TESTING
```

## What it does

Four threads plus housekeeping (see `docs/ARCHITECTURE.md`). Capture never blocks; classification and transport run off bounded queues with published drop counts; every detection is recorded as one append-only v2 blob, suppressed ones included; health is published complete on every heartbeat. Configuration arrives as a signed blob and is rejected whole on any verification failure.

The v1 description of this folder — one 1,308-line file, strictly sequential, no requirements.txt — is history. Those monoliths are in `../legacy/superseded-monolith/`.

## Prove it works

```bash
# anywhere with numpy — cepelynvault, or the Pi's venv. NOT the dev Mac,
# which has no numpy: both of these import the oceankind package.
python3 tools/v2_conformance_test.py     # drives the real emit code → CONFORMANT
python3 tools/phase1_smoke_test.py       # fail-loud + signed-config behaviour

# stdlib / bash only — runs anywhere, including the Mac
bash    tools/ota_rollback_test.sh       # breaks an update on purpose → ALL PASS
bash    tools/ota_fault_test.sh          # cuts the OTA at every step (power, network, twice) → ALL PASS

python3 ../tools/validate_contract.py ./out

# on the bench Pi (needs the service venv, not system python3):
~/oceankind/venv/bin/python tools/inject_event.py --count 3   # emit events by hand
```

Bench procedure, from blank SD to acceptance numbers against real Azure: `docs/BENCH.md`.
Day-to-day bench commands (restart, change the event rate, inject events, read
the output) are in the **cheat sheet at the top of that file** — start there
rather than re-deriving them.
