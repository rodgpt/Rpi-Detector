# Restructure manifest

Every file moved on 2 August 2026, with source and destination. There is no version control in this repository, so this file is the record and the reversal instructions.

**Nothing was deleted, overwritten or edited.** Only `mv`, plus new files that did not previously exist.

---

## Before

Thirty items in a single flat directory. Production code, an abandoned prototype, a one-off utility, provisioning scripts, offline tools, three audits in two languages, a budget, two Word documents, two document generators, a compiled bytecode cache and a backup file, with nothing indicating which of the two Python systems was live.

## After

```
marFutura/
├── README.md              new
├── raspberry-pi/          project root, lynchDev
├── dashboard/             project root, lyncHtmlDev
├── legacy/                archive, not a project root
└── docs/                  flat
```

---

## Moves

### To `raspberry-pi/`

| From | To |
|---|---|
| `marfutura_iot_audio.py` | `raspberry-pi/src/marfutura_iot_audio.py` |
| `model.joblib` | `raspberry-pi/models/model.joblib` |
| `setup.sh` | `raspberry-pi/scripts/setup.sh` |
| `protect_sd.sh` | `raspberry-pi/scripts/protect_sd.sh` |
| `update_oceankind.sh` | `raspberry-pi/scripts/update_oceankind.sh` |
| `predict.py` | `raspberry-pi/tools/predict.py` |
| `test_model_on_alerts.py` | `raspberry-pi/tools/test_model_on_alerts.py` |
| `HARDWARE.md` | `raspberry-pi/docs/HARDWARE.md` |

`scripts/` is code that runs on the Pi. `tools/` is code that runs on a workstation and must never be deployed. That distinction did not exist before and it matters, because `predict.py` and `test_model_on_alerts.py` pull in the same heavy dependencies as the production system for offline work.

### To `dashboard/`

| From | To |
|---|---|
| `dashboard/index.html` | `https://github.com/rodgpt/Dashboard-Detector/blob/main/src/index.html` |

The folder already existed. Adding `src/` makes it a project root consistent with `raspberry-pi/`, and leaves room for `docs/` and, later, assets.

### To `legacy/`

| From | To |
|---|---|
| `main.py` | `legacy/modular-prototype/main.py` |
| `config.py` | `legacy/modular-prototype/config.py` |
| `detector.py` | `legacy/modular-prototype/detector.py` |
| `alert.py` | `legacy/modular-prototype/alert.py` |
| `audio_capture.py` | `legacy/modular-prototype/audio_capture.py` |
| `clip_saver.py` | `legacy/modular-prototype/clip_saver.py` |
| `requirements.txt` | `legacy/modular-prototype/requirements.txt` |
| `marfutura_record_gdrive.py` | `legacy/gdrive-recorder/marfutura_record_gdrive.py` |
| `PROJECT15.md` | `legacy/superseded-docs/PROJECT15.md` |
| `marfutura_iot_audio.py.bak_20260624_144323` | `legacy/build-artifacts/` |
| `__pycache__/` | `legacy/build-artifacts/__pycache__/` |

Two of these deserve explanation.

**`requirements.txt` moved to legacy.** It lists `sounddevice`, `paho-mqtt` and `RPi.GPIO`, which are dependencies of the prototype, and omits every library the production system needs. Sitting at the repository root it read as the manifest for the whole project and it was wrong. In `legacy/modular-prototype/` it is accurate. `raspberry-pi/` now has no manifest, which is a visible gap rather than a misleading file, and writing a correct one is Phase 1 work.

**`HARDWARE.md` did not move to legacy**, though it describes the prototype, because its hydrophone and ADC specifications are still accurate and still needed. It carries a caveat in `raspberry-pi/README.md` instead.

### To `docs/`

| From | To |
|---|---|
| `INITIAL_REPORT.md` | `docs/INITIAL_REPORT.md` |
| `IMPROVEMENT_REPORT.md` | `docs/IMPROVEMENT_REPORT.md` |
| `SYSTEM_REVIEW.md` | `docs/SYSTEM_REVIEW.md` |
| `_SYSTEM_REVIEW_ES.md` | `docs/SYSTEM_REVIEW_ES.md` |
| `_presupuesto.md` | `docs/presupuesto.md` |
| `MarFutura_System_Review.docx` | `docs/MarFutura_System_Review.docx` |
| `OceanKind_Improvement_Report.docx` | `docs/OceanKind_Improvement_Report.docx` |
| `system_review.js` | `docs/system_review.js` |
| `oceankind_improvement_report.js` | `docs/oceankind_improvement_report.js` |

Two files were renamed. Leading underscores on `_SYSTEM_REVIEW_ES.md` and `_presupuesto.md` were sorting hacks for a flat directory and are no longer needed. No other file was renamed, so every cross-reference in the audits still resolves.

---

## New files

None of these existed before. Nothing was overwritten.

| File | Purpose |
|---|---|
| `README.md` | Repository entry point and folder map |
| `raspberry-pi/README.md` | Device domain: layout, behaviour, known traps |
| `raspberry-pi/CLAUDE.md` | Device rules for the Lynch context pass |
| `dashboard/README.md` | Web client domain: layout, data sources, known traps |
| `dashboard/CLAUDE.md` | Web client rules for the Lynch context pass |
| `legacy/README.md` | What each archived item is and what replaced it |
| `docs/README.md` | Document index and reading order |
| `docs/FINDINGS.md` | Consolidated verified defect register |
| `docs/ASSESSMENT_AND_PLAN.md` | Scope analysis and delivery plan |
| `docs/ALCANCE_FASE1_FASE2.md` | Client-facing scope note, Spanish |
| `docs/MOVES.md` | This file |

---

## Left untouched

`.DS_Store` at the repository root. macOS regenerates it; removing it achieves nothing.

---

## Reversing this

Every row in the tables above run backwards, then remove the four directories and the new files listed. No file content changed, so a reversal is byte-identical to the original state.

---

## What still points at the old layout

The restructure moved files. It did not update anything that references them. These are Phase 1 work and are tracked in `FINDINGS.md`.

**`raspberry-pi/scripts/setup.sh`** assumes a flat layout and sets `ExecStart` to `main.py`, which is now in `legacy/`. It was already installing the wrong entry point before the move; the move makes that visible rather than causing it. See F-11.

**`raspberry-pi/scripts/update_oceankind.sh`** does `git pull` against a remote whose layout is whatever was last pushed. The deployed unit's directory structure is unchanged by anything done here, since this working copy has no version control and no link to that remote. Reconciling the deployed layout with this one is a deliberate decision to make during Phase 1, not a side effect of this pass.

**The deployed unit is unaffected.** Nothing in this restructure touched the Pi.

---

## Re-baseline, 8 August 2026

The client supplied a newer device tree as `latest_raspberrypi/`. Checksums showed only the
monolith differed; every other file was byte-identical to what was already in place, and
`detector emily.py` hashed identical to `detector_psd.py`.

| From | To |
|---|---|
| `raspberry-pi/src/marfutura_iot_audio.py` (audited 2 Aug, v1.1.0) | `legacy/superseded-monolith/marfutura_iot_audio_v1.1.0_audited-20260802.py` |
| `latest_raspberrypi/marfutura_iot_audio.py` | `raspberry-pi/src/marfutura_iot_audio.py` |
| `latest_raspberrypi/detector_psd.py` | `raspberry-pi/src/detector_psd.py` |
| `latest_raspberrypi/a5_indicators.py` | `raspberry-pi/tools/a5_indicators.py` |
| `latest_raspberrypi/detector emily.py` | `legacy/build-artifacts/detector_emily_dupe-of-detector_psd.py` |
| `latest_raspberrypi/__pycache__/*.pyc` | `legacy/build-artifacts/__pycache__-latest/` |
| `latest_raspberrypi/` (remainder) | `legacy/_to_delete_verified_duplicates/` |

Everything in `legacy/_to_delete_verified_duplicates/` is a verified byte-identical duplicate of a
file already present elsewhere in the tree. Safe to delete. It is moved rather than removed because
this session cannot delete files on the device.

`a5_indicators.py` went to `tools/` rather than `src/` because nothing imports it. It is a
standalone CLI, and whatever produces `acoustic_indicators.json` by running it is not in this
repository.

`detector_psd.py` went to `src/` even though nothing imports it either, because its algorithm was
copy-pasted into `classify_clip()`. See F-23.

## Naming

`_Rpi-Detector/` and `_Dashboard-Detector/` are the client-owned GitHub repositories, referred to
as **prod** from here on. Nothing in this working tree writes to them.
