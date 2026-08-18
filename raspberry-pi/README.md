# raspberry-pi

Everything that runs on the device in the field.

The name says Raspberry Pi because that is what is deployed today: a Raspberry Pi 4 Model B with an audio HAT, solar power and a 4G modem. A migration to a Raspberry Pi Zero 2W is recommended in `../docs/IMPROVEMENT_REPORT.md` and a bench Zero 2W is available. If the board changes, the folder name stays and this paragraph gets updated.

---

## Layout

```
raspberry-pi/
├── src/
│   └── marfutura_iot_audio.py   THE production system. 1,308 lines. One file.
├── models/
│   └── model.joblib             StandardScaler + LogisticRegression. 52 features, 53 parameters, 3 KB.
├── scripts/                     Run on the Pi, by a human or by systemd
│   ├── setup.sh                 First-time provisioning. BROKEN, see below.
│   ├── protect_sd.sh            Enables the read-only overlay filesystem
│   └── update_oceankind.sh      Over-the-air update. No rollback. See below.
├── tools/                       Run on a workstation, never on the Pi
│   ├── predict.py               Classify a WAV from the command line
│   └── test_model_on_alerts.py  Evaluate the model against saved alerts
└── docs/
    └── HARDWARE.md              Hydrophone and ADC reference. Some claims are stale, see below.
```

There is no `requirements.txt` here, and that is deliberate rather than an oversight. The file that used to sit at the repository root listed `sounddevice`, `paho-mqtt` and `RPi.GPIO`, which are dependencies of the abandoned prototype. It described `legacy/modular-prototype/` and it has been moved there. Writing a correct one is Phase 1 work.

What the production system actually imports: `numpy`, `librosa`, `scikit-learn`, `joblib`, `azure-storage-blob`, `azure-iot-device`, `twilio`, `pyserial`. Plus `arecord` from `alsa-utils` at the system level.

---

## What it does, in order

The main loop is strictly sequential on one thread.

1. Every 5 minutes, download `remote_config.json` from blob storage and apply it.
2. Record a 5-second clip with `arecord` to a WAV file. This blocks.
3. Load the clip with librosa, resample it, extract 52 features, classify it.
4. If it is a detection and the 600-second cooldown has elapsed: send WhatsApp, upload the clip, rewrite `manifest.json`, send to IoT Hub.
5. Every 60 seconds: poll the modem for signal, read the Victron solar controller over serial, upload `status.json`, append a CSV row.
6. Every 12 hours: send a WhatsApp heartbeat.

Step 2 is the only time the hydrophone is listening. Everything else is deaf time. That is the central architectural problem and it is the main target of Phase 1.

---

## Things that will surprise you

**`setup.sh` installs the wrong code.** Line 91 sets `ExecStart` to `main.py`, which is the abandoned prototype and now lives in `legacy/modular-prototype/`. A clean provision from this repository produces a unit that does not run the production system. Do not run this script until it is fixed.

**`setup.sh` and `protect_sd.sh` disagree about the service user.** `setup.sh` hardcodes `/home/pi` and `SERVICE_USER="pi"`. `protect_sd.sh` defaults to `marfutura`. If they ran as different users, runtime state landed somewhere other than where the SD protection expects it. Confirm which user the live service runs as before touching either.

**`update_oceankind.sh` can strand the node.** It disables the overlay, reboots, pulls, reinstalls, re-enables the overlay, reboots again. A failure between those two reboots leaves a remote solar node in an undefined state with no rollback and no physical access. Treat every invocation as dangerous until A/B updates land.

**The clips directory is in RAM.** `protect_sd.sh` symlinks `~/oceankind/clips` to `/tmp/oceankind/clips` and enables the RAM-backed overlay. Nothing in the code or the OS ever deletes clips. See `../docs/FINDINGS.md` F-03.

**`/boot/firmware` is read-only.** `protect_sd.sh` runs `do_boot_ro 0`, and the code writes its telemetry CSV to `/boot/firmware/oceankind_data.csv`. Those two cannot both be in effect. Whichever is true on the live unit tells you whether the SD card is unprotected or the telemetry is being discarded.

**`HARDWARE.md` describes the prototype.** It was written for the STA/LTA detector in `legacy/modular-prototype/`, not for the ML classifier that runs today. The hydrophone and ADC specifications in it are still accurate and useful. The software description is not. There is also an unresolved contradiction about whether the deployed ADC is a HifiBerry DAC+ ADC Pro or a Raspberry Pi Codec Zero.

---

## Before you change anything

Read `CLAUDE.md` in this folder. It is short and every rule in it comes from a defect that has already happened or is waiting to happen.

Read `../docs/FINDINGS.md`. Nineteen catalogued defects, four of them capable of silently disabling detection.

Test on the bench Pi Zero 2W first. The production node is on solar power in a remote location and there is currently no way to recover it remotely from a bad update.
