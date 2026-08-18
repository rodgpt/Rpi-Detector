> **Note on this document.** Written 2 August 2026, before the folder restructure and
> against the original 36-40 hour budget. File paths below have been updated to the current
> structure. The budget is now 60 hours, so the gap analysis in section 2 is more pessimistic
> than the current figure warrants; the sequencing and the Phase 1 / Phase 2 split still hold.
> The verified defect register has since been extracted and expanded into `docs/FINDINGS.md`,
> which supersedes section 4 of this document.

# OceanKind / Mar Futura
## Assessment of the contracted scope, and a 20-day delivery plan

Prepared for: Futurity Systems
Date: 2 August 2026
Inputs reviewed: `raspberry-pi/src/marfutura_iot_audio.py` (1,308 lines), `https://github.com/rodgpt/Dashboard-Detector/blob/main/src/index.html` (1,779 lines), the deprecated modular tree (`main.py`, `detector.py`, `alert.py`, `legacy/modular-prototype/audio_capture.py`, `clip_saver.py`, `config.py`), `raspberry-pi/scripts/setup.sh`, `raspberry-pi/scripts/protect_sd.sh`, `raspberry-pi/scripts/update_oceankind.sh`, `requirements.txt`, `docs/INITIAL_REPORT.md`, `docs/IMPROVEMENT_REPORT.md`, `docs/SYSTEM_REVIEW.md` / `docs/SYSTEM_REVIEW_ES.md`, `docs/presupuesto.md`, `legacy/superseded-docs/PROJECT15.md`, `raspberry-pi/docs/HARDWARE.md`.

---

## 1. Verdict

The analysis is good. The budget is not.

The three review documents are better than most paid audits: the "pushback" structure in `docs/SYSTEM_REVIEW.md`, where every finding is argued against before it is accepted, is honest consulting and rare. The findings are largely correct and I verified them against source.

The problem is arithmetic. The same documents that were delivered to the client estimate the work at **24 to 41 days**. The budget sells **20 days and 36 to 40 hours**. Those numbers came from the same desk and they contradict each other in public. A client who reads both documents in sequence can do that subtraction, and that is a larger commercial risk than any technical risk in the repo.

Second problem: the budget omits the work its own analysis marked **FIX NOW**. Twilio credential rotation, GPS removal from source, and blob container lockdown appear in `docs/SYSTEM_REVIEW.md` as roughly 1.5 days of urgent work. They appear nowhere in `docs/presupuesto.md` as line items. They are the cheapest and highest-value hours in the entire project and they are unbilled and unscheduled.

Third problem, and the one that should change how you sequence: **both reports missed the most dangerous defects in the code.** I found four. Three of them can stop the system from detecting anything, or destroy the data record, without producing a single error signal. Section 4 details them.

Can the promises be delivered in the time offered? No, not as literally written. The scope is fixed and the contract is signed, so section 6 is a plan that delivers everything that actually protects the client inside 40 hours, and moves the rest into a declared, quoted Phase 2 that lands before the second unit arrives.

---

## 2. The arithmetic

`docs/presupuesto.md` maps to the review documents like this:

| Presupuesto item | Quoted | Same work, per your own reports |
|---|---|---|
| 1.1 Unify and modularize | 8-12 h combined for 1.1, 1.2, 1.3 | §3.3 two codebases: 2-3 days |
| 1.2 Async pipeline (deaf window) | " | §3.1 detection gaps: 5-10 days |
| 1.3 OTA safety net | " | §3.4 OTA rollback: 2-3 days |
| **Section 1 subtotal** | **8-12 hours** | **9-16 days** |
| 2.1 Security via API | 24-28 h combined for 2.1, 2.2 | §5.2 permanent API backend: 10-15 days |
| 2.2 Multi-device, users, admin panel | " | §4.1 dashboard rebuild: 5-10 days, on top of the API |
| **Section 2 subtotal** | **24-28 hours** | **15-25 days** |
| **Total** | **36-40 hours over 20 days** | **24-41 days** |

The presupuesto defines its own conversion: `horas/2 = días`. Apply it consistently and the reports' 24-41 days become 48-82 hours. The budget sells 36-40. That is the most charitable reading available and it is still 25% to 100% short.

If the client reads "days" as normal working days, which is the default reading anywhere outside that one line of the presupuesto, the reports describe 192-328 hours of work. The budget then reads as 5x to 8x under.

My own bottom-up estimate for the scope exactly as written in the presupuesto, assuming a developer who already knows this codebase:

| Work | Realistic hours |
|---|---|
| Unify codebases, fix provisioning, modularize the monolith | 10-14 |
| Async capture pipeline, written and validated on hardware | 18-28 |
| OTA A/B with health check and proven rollback | 6-10 |
| API backend, auth, index, five endpoints, deployment | 20-30 |
| Admin panel (users, devices, assignment) | 15-25 |
| Dashboard rebuilt against the API, device selector, pagination | 15-25 |
| **Total** | **84-132** |

Against 36-40 sold, with meetings and corrections declared inside those hours.

Two consequences worth stating plainly. First, the effective build budget is closer to 32 hours once meetings are deducted, because "las reuniones y correcciones son parte de las horas declaradas" is an uncapped commitment against a small number. On a 40-hour engagement, four client meetings and two rounds of corrections is 15-20% of the contract. Second, at 2 hours per day the project has no absorption capacity. One bad day is 5% of the budget. There is no slack anywhere in this plan and that has to be managed by scope, not by effort.

---

## 3. What the reports got right

Verified against source, these hold:

- Twilio SID and auth token are literal defaults at lines 49-50. The token also survives in the `.bak` and the `.pyc`.
- Sensor coordinates are hardcoded at lines 44-45 and uploaded in every `status.json`.
- The blob container is public by design and the dashboard reads `https://marfuturatest.blob.core.windows.net/alerts/*` directly with no token (lines 782-784, 1555).
- The main loop is strictly sequential. Capture blocks for 5 seconds, classification runs after it, network calls run after that.
- `raspberry-pi/scripts/setup.sh` line 91 installs `main.py`, which is the deprecated tree. The production monolith is never launched by the provisioning script.
- `requirements.txt` lists `sounddevice`, `paho-mqtt`, `RPi.GPIO` for the deprecated code, and omits every dependency the production monolith actually needs: `azure-storage-blob`, `twilio`, `librosa`, `scikit-learn`, `joblib`, `pyserial`. A fresh provision from this repo produces a unit that cannot run.
- `AUDIO_DEVICE = "plughw:3,0"` is hardcoded at line 66. USB re-enumeration breaks capture.
- `DATA_LOG_PATH` defaults to `/boot/firmware/oceankind_data.csv` (line 496) while `raspberry-pi/scripts/protect_sd.sh` line 121 runs `do_boot_ro 0`. Those two cannot both be in effect.
- WhatsApp is sent at line 1232, before the clip upload at line 1237. `alert_count` and the manifest only update if the upload succeeds.
- `raspberry-pi/scripts/update_oceankind.sh` has no rollback. A failure in phase two strands a solar node with no physical access.

One useful diagnostic falls out of the CSV conflict: **the dashboard's power chart tells you which mitigation is not in effect.** If `power_history` renders on the live dashboard, the overlay filesystem is off and the SD card is unprotected. If it is empty, the overlay is on and telemetry has been silently discarded since it was enabled. Check this before day one; the answer changes what you prioritise.

---

## 4. What both reports missed

These are the findings that justify re-sequencing the project. Each is verified against the current source.

### 4.1 The `rms` and `auto` detection modes cannot fire an alert. Ever.

The decision is line 1215:

```python
alert = rms >= ALERT_MIN_RMS and proba >= ML_THRESHOLD
```

`proba` is populated only from `classify_clip`, and `classify_clip` runs only when `DETECTION_MODE in ("ml", "auto")` (line 1207). Set `DETECTION_MODE="rms"` and `proba` stays `0.0`, so `alert` is permanently `False`. The startup banner at line 1159 prints "Umbral RMS (fallback)" and the comment at line 1204 documents a fallback that does not exist in the code.

Consequence: the documented safety mode is a kill switch. Anyone who switches to RMS mode believing it is the safe option silently disables detection.

What makes this hard to notice in the field is that **everything else keeps working**. The 12-hour "sistema activo" heartbeat still fires. `check_battery_alert` (lines 630-696) still sends battery warnings and recovery messages through the same WhatsApp path on every heartbeat, independent of `DETECTION_MODE`. The operator receives regular, correct-looking traffic from a unit that has not been capable of raising a detection alert since the moment the mode was changed.

Two refinements from verification. The failure is absolute only while `ML_THRESHOLD > 0`, which is the default of 0.5; setting `OCEANKIND_ML_THRESHOLD=0` flips the behaviour to a pure RMS detector with no ML gate at all, which is a different wrong answer rather than a fix. And `_load_ml_model` caches its own failure through `_ml_load_attempted` (lines 287-289), so in `auto` mode a model that fails to load once is never retried for the life of the process.

This compounds §5.2 of `docs/SYSTEM_REVIEW.md`. That report noted the system goes deaf if `raspberry-pi/models/model.joblib` fails to load, and proposed "fall back to RMS detection" as the remedy. The proposed remedy does not work. It has to be built, not enabled.

### 4.2 Remote configuration cannot change detection sensitivity

`remote_config.json` sets `alert_threshold`, which writes to `ALERT_THRESHOLD` (line 1190). `ALERT_THRESHOLD` appears in the level bar, in the IoT Hub payload, and in `status.json`. It does not appear in the alert decision. The two values that do decide, `ML_THRESHOLD` and `ALERT_MIN_RMS`, are environment-only and are never read from remote config.

Consequence: an operator can tune the threshold from the dashboard, watch the reported value change in `status.json`, and have zero effect on the system. Changing real sensitivity requires editing `/etc/oceankind.env` and restarting, which means an OTA update, which is the one operation that can brick the node. The safe knob is inert and the real knob requires the dangerous operation.

There is also no config editor in `https://github.com/rodgpt/Dashboard-Detector/blob/main/src/index.html`. `remote_config.json` is being written by hand or by some path outside this repo.

### 4.3 Detections suppressed by cooldown are neither recorded nor cleaned up

Lines 1226 and 1263:

```python
if alert and (now - last_alert_time) >= ALERT_COOLDOWN:
    ...
elif Path(clip_path).exists() and not alert:
    Path(clip_path).unlink()
```

When a detection fires inside the 600-second cooldown, `alert` is `True`, so the first branch fails on the cooldown test and the `elif` fails on `not alert`. Neither branch runs. Two things follow.

**The event is erased.** No upload, no manifest entry, no counter increment, no local record. A blast sequence produces exactly one row in `manifest.json` and the rest of the sequence does not exist in the data. For a conservation enforcement tool, that is a data-integrity defect, not just an alerting defect. Any frequency statistic the client derives from this manifest undercounts, and undercounts worst during the events that matter most.

**The clip is never deleted.** Alerted clips are not deleted either. The only two `unlink` calls in the entire file are line 997 for the pending-alerts buffer and line 1265 inside that `elif`. Every clip that either raises an alert or is suppressed by cooldown is retained permanently.

Where those clips land makes this worse than a disk-space problem. `raspberry-pi/scripts/protect_sd.sh` lines 88-95 run `rm -rf ~/oceankind/clips` and then symlink it to `/tmp/oceankind/clips`, and lines 126 and 135-137 enable the RAM-backed overlay. `CLIPS_DIR` therefore resolves into RAM by two independent mechanisms, which is deliberate and correct for SD card protection, and catastrophic combined with no cleanup. The `/etc/tmpfiles.d/oceankind.conf` rule written at lines 61-73 has `-` in the age field, so systemd creates the directory and never ages anything out of it. There is no cron job, no timer, and no logrotate anywhere in the repo.

Each clip is 48 kHz stereo 16-bit for 5 seconds: 960 KB. My first estimate of 138 MB/day assumed the alert ceiling, and that was wrong, because the dominant contributor is cooldown-suppressed clips, which are not rate-limited by anything. They arrive at up to one per loop iteration, roughly one per 5 to 7 seconds. **Worst case is about 11 MB per minute.** On a 2 GB Pi that is a few hours of sustained false positives from rain or engine noise, which the code comment at line 1211 confirms occur, between a healthy node and memory exhaustion.

This is a twenty-minute fix and it is not in any report or in the budget.

### 4.6 Provisioning and SD protection disagree about which user owns the system

`raspberry-pi/scripts/setup.sh` hardcodes `/home/pi` and `SERVICE_USER="pi"` (line 8). `raspberry-pi/scripts/protect_sd.sh` defaults to user `marfutura` (line 30) and creates the clips symlink in that user's home. If the two scripts ran as different users, the symlink into `/tmp` sits in one home directory while `Path.home()` at runtime resolves to the other, and clips silently accumulate on the overlay's upper layer instead of the intended tmpfs.

Confirm which user the production service actually runs as before touching either script. This also needs resolving on day 4 as part of the provisioning fix, because a clean install from this repo currently cannot be trusted to place runtime state where the SD protection expects it.

### 4.4 The deaf window is worst at the exact moment it matters

Both reports treat the deaf window as a uniform duty-cycle problem. It is not uniform. It is correlated with detection.

A quiet cycle is 5 seconds of capture plus 1-3 seconds of librosa, so roughly 60-80% duty cycle. An alert cycle adds a Twilio API call with no explicit timeout, a 960 KB clip upload over cellular, a full `manifest.json` download and re-upload that grows toward multi-megabyte at the 5,000-entry ceiling, and an IoT Hub send. That is plausibly 30 to 60 seconds of deafness, immediately following a confirmed blast.

Blast fishing does not produce isolated events. It produces sequences. The system is at its blindest in the seconds after it detects the first one, and then §4.3 discards whatever it does catch for the next 10 minutes.

`docs/IMPROVEMENT_REPORT.md` §3.1 says the system "may miss 30-60% of actual blasts". `docs/SYSTEM_REVIEW.md` §3.1 says "the probability of missing a specific blast is low". Both documents went to the client. Reconcile them with a measurement, not with an adjustment to the prose.

### 4.5 One report claim does not survive checking

`docs/SYSTEM_REVIEW.md` §4.3 states: "The dashboard stashes a write-capable SAS URL in browser `localStorage` (`oceankind_write_sas_url`); one leaked browser lets someone set the threshold so high the station never alerts again."

`SAS_URL_KEY` is declared at line 789 of `https://github.com/rodgpt/Dashboard-Detector/blob/main/src/index.html` and referenced nowhere else. It is a dead constant. There is no write path in the dashboard.

Fix this before the client's own developer finds it. A client who checks one claim and finds it overstated discounts the other twelve, including the ones that are true and urgent.

---

## 5. What 40 hours actually buys

Given the answers you gave me: scope fixed with Phase 2 addable, second unit confirmed within six months, a Pi Zero 2W available as a bench unit, and no Azure access yet.

**The Azure access gap is the schedule's critical path.** Section 2 of the presupuesto is 24-28 of the 40 contracted hours and every hour of it requires portal permissions you do not have. If that request takes two weeks to clear, more than half the contract is unstartable while the clock runs. Request it today, in writing, with the specific roles named. Section 6 front-loads everything that needs no Azure so that a slow grant costs you sequence rather than budget.

**The bench unit is worth more than it cost.** A Pi Zero 2W lets you deliberately fail an OTA update and prove the rollback, which you cannot responsibly do on the only production node. It also happens to be the exact platform `docs/IMPROVEMENT_REPORT.md` §6.3 recommends for hardware migration, so every hour spent validating the async pipeline on it is free intelligence for a Phase 3 hardware decision. If you have no second audio HAT, use `snd-aloop` to feed recorded WAVs into ALSA as a synthetic capture device; that covers the threading and lifecycle work, which is where the bugs live.

One caution on that unit: 512 MB of RAM against librosa, numpy and scikit-learn is tight, and a ring buffer plus a processing queue adds pressure. Measure resident memory during the soak test. Related design point: on slower hardware an async pipeline converts a deaf window into an unbounded backlog if the queue is not bounded. Use a bounded queue with an explicit drop policy and publish the dropped-clip count in `status.json`. A system that reports what it dropped is honest; one that silently lags is the same failure you are being paid to remove.

**What fits.** All of section 1, done properly and proven on hardware. All of the *security* content of 2.1, delivered through the cheapest correct mechanism rather than through a full API. The *foundation* of 2.2, meaning a per-device data layout and a dashboard that can address more than one unit, which is what makes the second unit possible in six months.

**What does not fit, and must be declared now rather than on day 20.** The backend API as an independent service. User accounts with differentiated access rules. The administration panel for adding and removing users and assigning devices. Dashboard login. Those are presupuesto lines 29 and 31 and they are approximately 50-80 hours on their own.

Declaring this in week one costs you a difficult conversation. Discovering it in week four costs you the client.

---

## 6. The 20-day plan

Twenty working days at 2 hours. Four hours are reserved for meetings and corrections, which the presupuesto places inside the contracted total. Days 10 and 13 double as absorption.

### Week 1: stop the bleeding, and make the system honest

**Day 1.** Send both access requests in writing. Azure: Contributor on the resource group plus Storage Blob Data Owner, or Owner. Twilio: console access sufficient to rotate the auth token. Then, needing neither: strip the Twilio and GPS defaults from source, load from `/etc/oceankind.env`, and make the process fail loudly at startup if they are absent rather than falling back to a literal.

**Day 2.** Kill the silent-deaf class. Model load failure raises an alarm through WhatsApp and sets `detector_healthy: false` in `status.json`. Build the RMS fallback that §4.1 shows does not currently exist, and make `DETECTION_MODE` actually gate the decision. Fix `decided_by`, which is hardcoded to `"rms+ml"` at line 1216 and therefore reports fiction.

**Day 3.** Fix §4.3: delete clips on every path, record cooldown-suppressed detections as `suppressed` entries so the event count is truthful, and add a startup sweep of `CLIPS_DIR`. Instrument the main loop with per-stage timing and publish `duty_cycle_pct` and `deaf_seconds_total`. The deaf window stops being an argument between two of your own documents and becomes a number.

**Day 4.** Codebase reconciliation. Archive the deprecated tree, keeping `legacy/modular-prototype/audio_capture.py` as the seed for the capture module. Rewrite `requirements.txt` against the monolith's real imports. Fix `raspberry-pi/scripts/setup.sh` line 91. Write the README that says which file runs.

**Day 5.** Extract `config.py` and `telemetry.py` from the monolith as mechanical moves with no logic rewrite. Stand the bench Pi Zero 2W up on the new tree.

End of week 1: no secrets in source, the system can no longer go deaf without saying so, the data record is truthful, the duty cycle is measured, and a fresh provision produces a working unit. This is the cheapest risk reduction in the whole engagement and none of it needs Azure.

### Week 2: the async pipeline

**Days 6-9 (8 h).** `capture.py` with continuous capture, device auto-detection by name, and a ring buffer. Bounded queue with a drop policy and a published drop counter. Classifier worker. Transport worker with the existing pending-alert buffer generalised to cover uploads, not just WhatsApp. Upload before notify, so the link in the message is live when it arrives.

**Day 10 (2 h).** Soak on the bench unit. Re-measure duty cycle against the day 3 baseline. Absorption day.

### Week 3: OTA safety, then production

**Days 11-12 (4 h).** A/B code directories with a symlink switch, a post-restart health check, and automatic reversion. Then deliberately break an update on the bench Pi and prove the rollback. Untested rollback is not a safety net.

**Day 13 (2 h).** Deploy to the production node. Observe for 24 hours. Absorption day.

**Day 14 (2 h).** Azure lockdown, assuming access has cleared. Container to private. Dashboard reads via a read-only SAS. The Pi writes via a scoped write SAS instead of the storage account key. If access has not cleared, this is where the schedule absorbs it, and Phase 2 starts with a debt.

### Week 4: multi-device foundation and handover

**Day 15 (2 h).** Harden remote config: HMAC signature against a shared secret, clamped ranges, and expose `ML_THRESHOLD` and `ALERT_MIN_RMS` so the remote knob is the real knob. This closes §4.2 and §5.5 of `docs/SYSTEM_REVIEW.md` together.

**Days 16-17 (4 h).** Multi-device data layout. Move to `devices/{device_id}/...` and replace the `manifest.json` read-modify-write with append-only per-event blobs, which removes the race condition permanently rather than mitigating it. Migration script for the existing history.

**Days 18-19 (4 h).** Dashboard: device selector, reads the new layout through the SAS, windowed power fetch, paginated event list. No login; that is Phase 2 and it is declared as such.

**Day 20 (2 h).** Handover. Runbook, README, and the Phase 2 specification and quote delivered as a document, not a conversation.

---

## 7. Phase 2

Deliver this as a written, priced specification on day 20 rather than discussing it. It is the natural continuation and it lands well before the second unit.

| Item | Estimate |
|---|---|
| API service: five read endpoints, index, per-device keys, deployment | 20-30 h |
| Authentication: invite-link tokens, session JWT, two roles | 10-15 h |
| Administration panel: users, devices, assignment, key rotation | 15-25 h |
| Dashboard migration to API, login gate, conditional polling | 15-25 h |
| Secrets to Key Vault, Twilio moved server-side off the device | 5-8 h |
| **Total** | **65-103 h** |

Two design notes to carry in. Skip Cosmos DB; SQLite on an Azure Files share or Table Storage carries tens of units at a fraction of the operational cost, and `docs/IMPROVEMENT_REPORT.md` §3.5.2 already concedes SQLite is sufficient at this scale. Skip OAuth and three-tier RBAC; `docs/SYSTEM_REVIEW.md` §5.4 is right that invite links suit a small team, and the presupuesto's own "distintas reglas de acceso" is satisfied by viewer and operator.

---

## 8. Commercial terms to correct before day 1

The scope is fixed. These are not scope changes and they are worth raising in the same message as the access request.

**Cap the meetings.** "Las reuniones y correcciones son parte de las horas declaradas" is unbounded against a 40-hour total. Propose four hours included, further sessions billed at the same rate. This is a normal clarification and it protects roughly 15% of the contract.

**Name the access dependency as a dependency.** Put it in writing that section 2 cannot start before Azure permissions are granted, and that delay shifts delivery rather than compressing it. Without that sentence, a slow grant becomes your problem on day 20.

**Define done per item.** The presupuesto has no acceptance criteria. Attach one measurable outcome to each line: duty cycle above 99% measured over 24 hours; a rollback demonstrated on video; the container returning 404 to an unauthenticated request. Measurable acceptance protects you more than it protects the client.

**Say who pays for Azure resources**, and confirm the client's Microsoft for Nonprofits credit status, which `legacy/superseded-docs/PROJECT15.md` line 67 already flags as likely available.

**Add a change-request clause.** There is none. At 2 hours a day, one unplanned request consumes a full day of the contract.

---

## 9. Day 1, concretely

1. Send the Azure access request with the roles named, and the Twilio console request.
2. Send the scope note (separate document, Spanish) declaring Phase 1 and Phase 2.
3. Ask one diagnostic question: does the power chart currently render on the live dashboard? The answer tells you whether the SD card is unprotected or the telemetry is being discarded.
4. Correct the write-SAS claim in `docs/SYSTEM_REVIEW.md` before the client's side finds it.
5. Rotate the Twilio token the moment console access lands. It is live in source, in the `.bak`, and in the `.pyc`, and `raspberry-pi/scripts/update_oceankind.sh` pushes all three to a git remote.

---

*Futurity Systems, August 2026*
