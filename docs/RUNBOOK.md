# Runbook

**Status: intentionally empty. Do not fill this in yet.**

This is the operational manual for the deployed unit: how to deploy, how to verify, how to recover, what to check when something looks wrong.

It is a stub on purpose. Writing it now would document decisions that have not been made, and a runbook that describes a procedure nobody chose is worse than no runbook, because someone will follow it.

---

## What has to be settled first

| Decision | Why the runbook needs it |
|---|---|
| **D-002** SD card protection method | Determines every recovery procedure, where state lives, and what an operator must do before and after an update |
| **D-005** Deployed layout reconciliation | Determines what "deploy" actually means and which paths appear in every command |
| **D-010** Which user the service runs as | Every command in this document runs as somebody |
| **F-06** OTA rollback | There is no point documenting a recovery procedure that does not exist. Today a failed update strands the node |

Write this after Phase 3, once the A/B update path exists and has been proven by deliberately failing an update on the bench unit. Before that, the honest runbook is one sentence: do not update the production unit.

---

## Intended contents

Left here so whoever writes it knows the shape.

**Deployment.** How to push a change to the production unit. What the health check verifies. How to confirm it took. How to roll back deliberately.

**Verification after deploy.** What a healthy unit looks like in `status.json` and on the dashboard within the first fifteen minutes. Specifically: `detector_healthy` true, `duty_cycle_pct` above 99, `clips_dropped` not climbing, `last_seen` fresh, power chart still drawing.

**Diagnosis.** A symptom-to-cause table. The dashboard is stale. Detections stopped. Battery alerts are duplicating. The power chart is empty. The spectrogram will not open. Each with the check that distinguishes causes, and each pointing at the relevant entry in `FINDINGS.md`.

**Recovery.** What to do when the unit is unreachable, when an update failed, when the SD card is suspected, when the audio device disappeared. Which of these can be done remotely and which require a site visit, stated plainly, because that is the question anyone reading this in an emergency actually has.

**Site visit checklist.** What to bring, what to check physically, what to photograph for `raspberry-pi/docs/HARDWARE.md`, which is currently missing any record of the physical installation.

**Provisioning a new unit.** Once Phase 4 lands and a second unit is real: hardware assembly, imaging, per-device configuration, key provisioning, registration, and the verification that it is actually reporting.

**Routine checks.** What is worth looking at weekly, and what would be worth alerting on automatically rather than checking by hand.

---

## Interim guidance

Until this document exists:

Do not run `raspberry-pi/scripts/update_oceankind.sh` against the production unit. It has no rollback and a failure between its two reboots leaves a solar node with no physical access in an undefined state (F-06).

Do not run `raspberry-pi/scripts/setup.sh` at all. It installs an entry point that now lives in `legacy/` (F-11).

Test everything on the bench Raspberry Pi Zero 2W first, including the failure paths.
