# OTA fault testing — power and network lost at every step

Written 2026-09-30. Companion to `tools/ota_rollback_test.sh` (the happy rollback) and `scripts/update_oceankind.sh` (the thing under test). The premise from `CLAUDE.md`: a change that can leave the unit unreachable is worse than the bug it fixes, and there is no console. An OTA that dies halfway is the most likely way to make one.

## 1. The fault model

An update is a sequence of steps. Power or network can vanish before, during or after any of them. What matters is what state the unit is in **when it comes back**, and whether it gets itself out with nobody there.

| When it is cut | Power | Network |
|---|---|---|
| **Before** — checking for a version, fetching | nothing changed; must stay that way | retry tomorrow, no state change |
| **During install** — copying files, pip | torn tree: package half-copied, launcher missing | pip half-done: must not install code against missing deps |
| **During verification** — the 60 s settle window | a build nobody has judged is running; must not be accepted or forgotten | (irrelevant) |
| **During rollback** | the *safety net* is torn | pip/git unreachable exactly when the rollback needs them |
| **After** — committed, overlay being re-enabled | overlay left OFF = SD unprotected, silently | — |
| **Twice** — power lost again during the recovery | the recovery itself must be re-runnable at every step | |

Three unit states matter, and they behave differently: **bench** (root writable), **protected** (RAM overlay ON — every write to `/` is discarded at reboot), and **mid-maintenance** (overlay turned OFF for the update). The production nodes are protected.

## 2. What the workstation harness proves

`bash raspberry-pi/tools/ota_fault_test.sh` drives the real, unmodified `update_oceankind.sh` with every state-changing command (`git cp rm mv touch sleep systemctl raspi-config reboot pip tee`) wrapped so it can `SIGKILL` the script just before or just after call *k* — and `cp` can be cut **mid-copy**, leaving a torn install. It enumerates **every** cut point of a clean run, restores power (reboot: volatile state lost, RAM overlay reverted, boot hooks run), lets three nightly cron runs happen, and asserts:

| Invariant | Meaning |
|---|---|
| **I-BOOT** | After power returns and boot-time activity settles — *before* the next 03:00 run — the service tree is complete and not the bad build. A unit that stays down until the next cron is a day of deaf time |
| **I-HEALTH / I-FINAL** | After three nightly runs the tree is complete, healthy, and the expected version (good update landed; bad update never stuck) |
| **I-SHA** | `.installed_sha` never names a version that is not installed |
| **I-OVERLAY** | A protected unit ends with the overlay re-enabled |

| Scenario | What is cut |
|---|---|
| S1 / S2 | good / poisoned update, root writable — at every point, S2 including the rollback |
| S3 / S4 | the same on a RAM-overlay unit (two reboots, the maintenance window) |
| D1–D4 | the same four, then power lost **again** at every point of the recovery |
| F1 F1P | network down at fetch (root writable / overlay — overlay must not be touched) |
| F2 F3 | pip fails going forward / during rollback |
| F4 | disk full on the 1st, 2nd, 3rd `cp` |
| F5 | corrupt checkout (`HEAD` garbage) plus a stale `index.lock` |
| F6 F7 | two updaters at once / a lock left by a dead run |

The harness was checked for sensitivity, not just for passing: pointed at the pre-2026-09-30 script it fails 27/40 cuts (S2) and 12–14 of 18 (overlay); with the "write `.installed_sha` before verifying" bug reintroduced into the new script every S2 cut fails; with the lock removed F6 fails.

**What it does not prove** (see §5 for the hardware runs that do):

- Torn *single* writes and unsynced data — a real power cut can leave a zero-length file that `kill -9` never will. The script `sync`s at every commit point and writes state via temp + `sync` + `mv`, but only a real cut tests that the SD card and ext4 honour it.
- That `overlay_active` returns true on a real overlay Pi, and that `raspi-config nonint do_overlayfs` behaves as modelled.
- systemd ordering at boot, `sudo` from cron and from the boot unit, real `NRestarts` semantics.
- Reads of a half-written state file by *another* process (nothing else reads them).

## 3. Results, 2026-09-30

**Against the script as it was** (baseline, kept in this doc because it is the argument for the rewrite):

| | Cut points | Violations |
|---|---|---|
| S1 good update, root writable | 24 | 5 — torn/missing install until the next cron |
| S2 poisoned update, root writable | 40 | **27** — incl. bad build **permanent** |
| S3/S4 overlay | 18 / 18 | 12 / 14 — overlay left OFF forever |
| Named faults | 10 | 5 fail (F2, F4×2, F5, F6) |

**Against the rewrite:** S1 50, S2 62, S3 72, S4 84 single cuts — 0 violations; F1–F7 all pass. Double faults (power lost again at every point of the recovery): D1 94, D2 172, D3 896, D4 1285 combinations — 0 violations.

## 4. Defects found (all in the pre-2026-09-30 script)

1. **A bad build could become permanent.** `.installed_sha` was written *before* verification. Power cut anywhere between the file copy and the end of the 60 s settle left the poisoned build installed **and recorded as current**; the next night said "already up to date" and did nothing. The unit crash-looped until somebody drove there. *(S2: 8 of the 40 cut points end with the poisoned build permanently installed; the other 19 violations leave the unit down until the next cron or lying in `.installed_sha`.)*
2. **The install was not atomic.** `rm -rf oceankind/` then `cp -R` — a cut between them left no package at all; nothing repaired it until the next cron. Same for the `*.py` copy. *(S1, S2.)*
3. **`set -e` protected nothing.** It is disabled inside any function called from `||` or `if`, which is how every step was called. `pip` failing, `cp` failing (disk full) and `git reset` failing were all silently ignored, and the script announced `✓ Actualización completada`. *(F2, F4.)*
4. **A disk-full or network failure blacklisted a good commit forever**, because infra failure and build failure were the same code path and both wrote `.ota_failed_sha`. *(F4_1, F4_2: v2 never lands.)*
5. **One corrupt object or stale `index.lock` killed OTA permanently.** `ensure_repo` only checked that `.git` exists. *(F5.)*
6. **Two updaters could run at once** (cron plus the boot unit plus a human) and both mutated the install. *(F6.)*
7. **Rollback depended on the network.** It ran `git reset` + `pip install`. It only worked when pip failed because `set -e` (see 3) hid the failure — by accident, not design.
8. **Overlay two-phase path could not work as written.** Phase 1 wrote its `/var/tmp` flag and `oceankind-update.service` to the *RAM overlay* — discarded at the reboot the script then requested. Phase 2 never ran; the next night's cron then updated directly with the overlay left **OFF**, so the SD card was silently unprotected for good. *(S3/S4: modelled, not yet seen on hardware — see §5.)* If the flag had survived, a cut during phase 2 would have had the same effect, because the flag was deleted first.
9. **`User=${USER}`** in the generated unit: cron does not set `$USER`. Moot now (no unit is generated at run time), noted because it would have produced an invalid unit.

## 5. Real-hardware runbook (bench Pi Zero 2W — never the production node)

The workstation cannot answer the first block; **do it before trusting OTA on a unit nobody can reach.** Prerequisite: a switchable power source (smart plug or GPIO relay) you can trigger from a script, and a second machine to watch `journalctl -f` over SSH.

### 5.1 Facts to verify once

- [ ] `sudo -n true` as the service user succeeds (no password) — from an interactive shell **and** from a cron job **and** from `oceankind-ota-boot.service`.
- [ ] `findmnt -n -o FSTYPE /` prints `overlay` with protection ON, `ext4` with it OFF. If it does not, fix `overlay_active()` in the script — a wrong answer either way is dangerous (it would try to write to RAM, or refuse maintenance).
- [ ] `raspi-config nonint do_overlayfs 1` then reboot really leaves the overlay OFF, and `do_overlayfs 0` + reboot turns it back ON.
- [ ] `oceankind-ota-boot.service` exists, is enabled, and runs at boot (`systemctl status`, `journalctl -u oceankind-ota-boot`). `.sd_protection` exists.
- [ ] Prove the §4 item 8 hypothesis: with the overlay ON, `touch /var/tmp/x`, reboot, confirm it is gone. (If it is *not* gone the design still works — it just wasn't needed.)

### 5.2 Power-cut campaign

Script the cut: start the update, sleep *T* seconds, drop power, restore after 10 s, log the time. Repeat for **T** chosen to land in each phase — read the phases off `journalctl -f` on a clean run, and cover at least: during `fetch`, during `pip install`, during the file copy, the first 5 s of the verify window, the last 5 s, during a deliberately-bad build's rollback, and during the overlay toggle/reboots. Do it twice for each: once on a bench (overlay OFF) unit, once protected. **Run the poisoned-build variant** (push a commit that crashes on start) — the most dangerous case is a good unit that trusts a bad build after an outage.

For each cut, wait for the unit to settle, then check:

| Check | How |
|---|---|
| Service running, not crash-looping | `systemctl is-active oceankind`; `systemctl show -p NRestarts oceankind` stable for 2 min |
| Installed tree is one version | `cat ~/oceankind/.installed_sha`; compare `git -C ~/oceankind/code log -1` and the file contents |
| No journal left | `ls ~/oceankind/.ota_journal` — must not exist once quiet |
| Overlay restored (protected unit) | `findmnt -n -o FSTYPE /` → `overlay`; no `.ota_attempts` left |
| Detection resumed | `status.json` `health.duty_cycle_pct` moving; a fresh heartbeat reaches the backend |

Pass = every cut ends in a healthy unit on either the old or the new version, with no human action. Also measure how long it took to get there — that is the deaf time.

### 5.3 Network faults

- Pull the router / `iptables -A OUTPUT -j DROP` (a *hang*, not a refusal, is the harder case — do that one) during: the phase-1 fetch, the maintenance-boot fetch, `pip install`. Confirm the script exits within its timeouts instead of holding the lock, the overlay is re-enabled, and the next night retries.
- Drop the network at the moment a poisoned build is being rolled back — rollback must complete with no network.

### 5.4 Disk and SD

- Fill the root filesystem (`fallocate`) during the copy — expect a clean restore and a logged error, not a torn tree.
- Pull the SD card for a second mid-write only if you can afford to re-flash the bench unit.

### 5.5 Soak

Leave the bench unit on a nightly cron for two weeks with a script that alternates good and poisoned commits and cuts power at random once per week. The claim to validate is not "each cut is survived" but "nothing accumulates" — no growing `.snapshot`, no stale `.ota_attempts`, no drifting `.installed_sha`.

## 6. Residual risks — known, not fixed

- **Verification happens with the overlay OFF.** A build that only fails once the root is read-only again is caught by nothing (unchanged from the earlier TODO). Mitigation would be a post-reboot verification plus a second maintenance window to roll back.
- **The OTA script cannot update itself.** `install` copies `src/*.py` and the package, never `scripts/update_oceankind.sh` — a bug in this script needs a hands-on fix, so it must be proven on the bench (this doc) before it is trusted.
- **Dependency rollback is best-effort.** Code is restored from the local snapshot with no network; the venv is restored from `pip freeze` only if the network allows. A build that upgrades a dependency incompatibly can leave a rollback that starts and then fails verification. The failure is loud (critical log, journal kept, retried every boot) but not self-healing.
- **A dead unit cannot report itself dead.** A critical rollback failure is logged and leaves the journal, but the only outward signal is the *absence* of heartbeats (D-018). That is the right fail-loud path; it just is not fast.
- **Units already deployed** (Zapallar, Matanzas) have neither `oceankind-ota-boot.service` nor `.sd_protection`. The new script *refuses* to disable their overlay (§ header) rather than stranding them — which also means they cannot take this OTA over the air. They need a one-time hands-on step.
- **Torn writes / unsynced data** — §2. Only §5.2 tests it.
