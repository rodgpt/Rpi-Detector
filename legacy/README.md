# legacy

**Nothing in this folder runs. Do not develop here. Do not import from here.**

This folder exists so that the rest of the repository can be trusted. Every file here was, at some point, indistinguishable from live code sitting in the same directory as live code. That ambiguity is the single largest source of confusion this project has produced, and moving these files is how it gets resolved.

Files are kept rather than deleted because two of them are still useful as reference and one is the only record of a prior state. Nothing here is on a path to being revived as-is.

---

## What is here and why

### `modular-prototype/`

An earlier, cleaner, modular design of the detection system. Six Python modules plus its dependency manifest.

| File | What it was |
|---|---|
| `main.py` | Entry point and orchestration |
| `config.py` | Configuration |
| `detector.py` | STA/LTA detector plus frequency analysis |
| `alert.py` | Alert transports: MQTT, HTTP, Azure IoT Hub |
| `audio_capture.py` | Continuous capture via sounddevice with a callback and a queue |
| `clip_saver.py` | Pre-trigger and post-trigger WAV clip saving |
| `requirements.txt` | Dependencies for the above, and only for the above |

**Superseded by** `raspberry-pi/src/marfutura_iot_audio.py`, which took a different approach: `arecord` for capture, a scikit-learn classifier instead of STA/LTA, Azure Blob Storage instead of MQTT.

**Why it is still worth reading.** This prototype does two things better than the production system. `audio_capture.py` implements continuous, non-blocking capture with a callback and a thread-safe queue, which is exactly the architecture the production system needs and does not have. It also detects the audio device by name rather than hardcoding an index. Both prior audits recommend recovering this module as the foundation for the Phase 1 async pipeline. When that happens, the code moves into `raspberry-pi/src/` as new work, and this copy stays here as history.

**The trap this created.** `raspberry-pi/scripts/setup.sh` line 91 still sets the systemd `ExecStart` to `main.py`. A clean provision from this repository installs this dead prototype as the service. That is a Phase 1 fix. Until then, do not run that script.

**The second trap.** The `requirements.txt` in here used to sit at the repository root, where it read as the dependency manifest for the whole project. It lists `sounddevice`, `paho-mqtt` and `RPi.GPIO`, and omits every library the production system actually needs. It is here because this is what it truthfully describes.

### `gdrive-recorder/`

`marfutura_record_gdrive.py`. A standalone utility that records continuously and uploads to Google Drive. No detection, no classification, no connection to the dashboard.

Not part of the detection path, not superseded by anything, and not obviously abandoned either. It was probably a field data-collection tool. If you need bulk audio for retraining the classifier, this is where to look first.

### `superseded-docs/`

`PROJECT15.md`. An integration guide for Microsoft Project 15, describing Azure IoT Hub routing, Stream Analytics, Cosmos DB and a Power BI dashboard.

It describes the prototype in `modular-prototype/`, not the production system, and the architecture it lays out was never built. The production code does hold an IoT Hub client and does send messages to it, but nothing consumes them and none of the Project 15 pipeline exists.

Kept because Phase 2 needs a backend and this document contains genuinely useful groundwork: the telemetry payload schema, the message-routing approach, and a note that Microsoft gives nonprofits Azure credits. Read it as a proposal that was never executed, not as documentation of anything.

Note that `HARDWARE.md` also describes this prototype, but it stayed in `raspberry-pi/docs/` because its hydrophone and ADC specifications are still accurate and still needed.

### `build-artifacts/`

**Contains live credentials. Handle accordingly.**

| File | What it is |
|---|---|
| `marfutura_iot_audio.py.bak_20260624_144323` | A snapshot of the production monolith from 24 June 2026 |
| `__pycache__/marfutura_iot_audio.cpython-310.pyc` | Compiled bytecode of the same |

Both contain the Twilio account SID and auth token as literal values, exactly as the current production source still does.

There is no version control in this repository, which makes the `.bak` file the only record of the previous state of the production system. That is why it has not been removed. It is also why it is a liability: a credential lives in three separate files here and rotating the token is the only thing that makes any of them safe.

Once the Twilio token is rotated, these two files carry no secret and the `.bak` can be deleted or kept purely as history, as you prefer.

---

## Rules

Do not add anything to `raspberry-pi/` or `dashboard/` that imports from this folder.

Do not fix bugs here. If something in here is worth having, port it forward as new work in the folder where it belongs and leave this copy untouched.

When a file here stops being useful, delete it rather than reorganising it. This folder should shrink over time.
