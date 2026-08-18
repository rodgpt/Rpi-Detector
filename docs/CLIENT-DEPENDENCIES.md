# What we need from the client

**Scope boundary: we build the plumbing, the client provides the water.**

We own everything that carries a detection from the hydrophone to a human: capture, queues, classification *plumbing*, transport, storage layout, security, updates, telemetry and the dashboard. We do not own the detection science. Not which signal to look for, not whether a detector works, not the thresholds on scientific grounds, not the model.

That boundary only holds if the plumbing never silently constrains the water. Three obligations stay ours because of it: the detector interface must express any event type the client wants, events a detector produces must never be lost by us, and every threshold must be tunable remotely without a firmware update. See D-014.

This file is the list of things only the client can answer or supply. Nothing here is a task we can complete. Keep it current, and send it as a batch rather than dripping questions out.

---

## Blocking, and the client is away

### 1. What is the system supposed to detect?

**F-21.** The detector was replaced with a PSD tonal-peak algorithm that arithmetically cannot fire on a sub-second broadband event. It finds sustained narrowband tones between 55 and 1000 Hz, which is vessel machinery. Every document in this project describes a blast-fishing detector.

Ask: has the target changed to vessels, is it now both, or is this a mismatch nobody noticed?

Consequence either way: acceptance criteria and all client-facing framing change. We are proceeding on the assumption of **both**, with a detector registry, so that work is not wasted whichever answer comes back.

### 2. Was `model.joblib` ever trained on real blasts?

`ML_POSITIVE_LABEL` defaults to `FILTRO`, a swimming-pool filter used as a proxy signal during testing. If the model was never retrained against real blast recordings, then keeping both detectors means keeping a vessel detector and a pool-filter detector, and blast detection exists in neither.

### 3. Labelled audio for validation

Neither detector has ever been validated. `docs/IMPROVEMENT_REPORT.md` §2.5 raised this in July and it was not answered.

We need roughly 50 labelled clips per class: confirmed blast, confirmed vessel, confirmed background, and ideally confirmed false-positive causes such as rain and nearby engines. `raspberry-pi/tools/test_model_on_alerts.py` already exists to score a detector against them.

This is science, so it is theirs. We will build the harness and the archive that collects candidates.

### 4. When did the detector change?

`detector_psd.py` is dated 10 July, the current monolith 7 August. Detections recorded before and after that boundary mean different things, and the dashboard's dive-window analysis currently correlates across it. Any conclusion drawn from that chart today mixes two populations.

We need the date the PSD detector actually shipped to each unit, so historical data can be segmented honestly.

---

## Access

### 5. Azure

Contributor on the resource group plus Storage Blob Data Owner, or Owner. Needed for production only; development and testing run against our own free subscription, so this no longer blocks us, but it blocks anything reaching the real units.

### 6. Twilio console

The account SID and auth token have been literal defaults in source since before the first audit, are present in a `.bak` and two `.pyc` files, and `update_oceankind.sh` has pushed all of it to a git remote. Five weeks of active development have passed without rotation.

This is the single most urgent item in the project and it takes minutes.

### 7. Are the prod GitHub repositories public?

`rodgpt/Rpi-Detector` and `rodgpt/Dashboard-Detector`. If public, item 6 stops being urgent and becomes an emergency at first push.

---

## Field facts nobody has confirmed

We cannot physically reach the units, so these need someone who can, or SSH access.

| Question | Command | Resolves |
|---|---|---|
| Which code is actually running, per unit | `md5sum ~/oceankind/code/marfutura_iot_audio.py` | Whether the tree we have matches either device |
| Python version on each unit | `python3 --version` | The bytecode shows 3.14 development against a likely 3.10 field runtime |
| Service user and entry point | `systemctl cat oceankind` | D-010, F-17 |
| Which ADC is installed | `arecord -l` | D-009. HifiBerry or Codec Zero, the docs disagree |
| Is the overlay filesystem on | `raspi-config nonint get_overlayfs` | D-002, F-16, and whether F-22 is urgent |
| Is OTA automatic | `crontab -l && sudo crontab -l` | Whether pushing to main deploys itself at 03:00 |
| What does the Pi actually pull from | `cd ~/oceankind/code && git remote -v` | The deployment source is still unknown |

Both units, separately. Matanzas and Zapallar may be running different builds.

---

## Undocumented components someone else owns

> **v2 note (2026-08-13, D-016):** on the new fleet's storage these two blobs live at
> `sites/{site_id}/acoustic_indicators.json` and `sites/{site_id}/ocean_conditions.json`.
> The device writes conformant empty stubs so the dashboard renders "no data" instead of
> breaking; whoever owns the real producers must point them at the per-site paths.

### 8. What produces `ocean_conditions.json`?

Hourly swell height, swell period, wind speed and wind direction, roughly seven days. Nothing in this repository writes it and the Pi does not produce it. Presumably something pulls a marine forecast API and uploads. It is a live dependency of the dashboard's Análisis tab with its own failure mode and nobody has described it.

### 9. What produces `acoustic_indicators.json`?

`a5_indicators.py` is a standalone CLI that nothing invokes. Something runs it on a schedule and uploads the result. Where does it run, against which clips, how often?

### 10. What emits the `deg` bearing field?

The dashboard renders detection bearings as direction arrows. No code in this repository produces that field. Either it is aspirational, or a third component exists that we have not seen.

---

## Deployment and handover

### 11. Who deploys, and when?

We cannot touch the production units. The contracted work can therefore be completed in full without any of it reaching the field, at which point none of the value is realised.

This needs naming now, not in the final week. Either someone with access runs the handover procedure with us, or access is granted, or the engagement delivers a proven mechanism plus documentation and deployment is explicitly the client's.

Whichever it is, it changes what "done" means for Phase 3 and belongs in the acceptance criteria.

### 12. Detector thresholds

`psd_threshold_db`, `psd_f_min`, `psd_f_max`, `score_min`, `alert_min_rms`. All remotely tunable and clamped as of 2026-08-13 (Phase 2). Choosing their values is science and it is theirs.

### 13. Analysis-window overlap (`window_hop_s`)

Detection runs on back-to-back 5-second windows. A short tonal event (~3–4 s) that straddles a window boundary can be diluted below `score_min` in both halves and missed — pure phase luck. The plumbing fix ships (2026-08-13): `window_hop_s` makes windows overlap (hop *h* guarantees any event up to 5−*h* s lands whole in some window), remotely tunable, default 5.0 = the calibrated no-overlap behaviour.

What only the client can decide: **whether short marginal events matter enough to pay the CPU** (×5/*h* classification work) and what hop value to use — it interacts with the 2026-08-07 calibration, which assumed 5 s windows. We will attach the Zero 2W bench measurement (CPU headroom at hop 2.5 and 2.0) so the decision has data. Related: attaching pre-roll audio to event clips uses the same buffer and the same kind of decision (clip length is what the detector was calibrated on).
