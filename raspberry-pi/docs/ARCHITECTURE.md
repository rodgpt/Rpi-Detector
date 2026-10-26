# Device architecture

**Describes the `oceankind/` package as built (Phase 2).**
**Last updated:** 2026-10-24 (fail-loud hardening after the repo health review: F-29 capture stall, per-detector accounting, upload-backlog alerting, housekeeping time budgets, config delivery for push-only units, IoT Hub off by default)

Device-internal design. System-level architecture is in `../../docs/ARCHITECTURE.md`. Read this before making any design decision on the Pi.

---

## Embedded constraints

The hard limits. Every decision on this device is bounded by one of them.

| Resource | Production node | Bench unit |
|---|---|---|
| CPU | Pi 4B, 4x Cortex-A72 @ 1.5 GHz | Pi Zero 2W, 4x Cortex-A53 @ 1 GHz |
| RAM | 2 GB+ | **512 MB** |
| Storage | microSD, overlay filesystem, writes go to RAM | microSD |
| Power | ~3.5W from solar and a 12V LiFePO4 bank | USB |
| Network | 4G cellular, intermittent, metered | Whatever is on the bench |
| Access | **None. Remote coastal site.** | Physical |

Two of these dominate.

**Writes go to RAM.** The overlay filesystem means anything written at runtime consumes memory and vanishes on reboot. This is correct for SD card longevity and it makes any unbounded write a memory exhaustion path. F-03 is exactly this.

**There is no physical access.** A change that can leave the unit unreachable is worse than the bug it fixes. This is why OTA rollback (F-06) outranks features.

Feature extraction costs 1 to 3 seconds per clip on the Pi 4 and an estimated 3 to 5 on the Zero 2W. That difference is the reason the bench unit is a useful worst case for queue behaviour.

---

## Process model

**Since Phase 2 (2026-08-13):** one systemd unit, one Python process, four threads plus the housekeeping main thread. The code lives in the `oceankind/` package; `marfutura_iot_audio.py` is a thin launcher kept so the systemd unit never changes.

```
power on → kernel → systemd → oceankind.service → marfutura_iot_audio.py
    → oceankind.main
        ├── capture      sounddevice callback → bounded block queue (nothing else)
        ├── classify     5 s windows, detector on EVERY window, alert/suppress decision
        ├── transport    clip upload → event blob → push to the backend. All network lives here
        └── main thread  housekeeping: status.json, telemetry, remote config, OTA trigger,
                         spool drain, heartbeats — each on its own timer
```

**The device notifies nobody (2026-10-24, D-020).** Every WhatsApp and voice alert leaves from the backend, driven by what this process already sends it: the event push (detections, cluster), the heartbeat (`health.degraded_reason`, `power.battery_voltage_v`) and the absence of heartbeats. `notify.py`, Twilio, IoT Hub, the pending-alert spool and the battery debounce state are gone. "Never fail quietly" on this side now means: put the condition in `degraded_reason` and in the heartbeat, loudly and completely; the `flush_degraded_alerts` queue still exists so a degradation raised on the classify thread is logged from housekeeping, never inline.

**Heartbeat tick, restructured 2026-09-23 (D-018).** `main.py`'s status/telemetry step on the housekeeping timer used to be one function (`upload_status`), gated as a whole on `STORAGE_ENABLED` — which meant a unit with no storage skipped collecting status entirely, not just uploading it. Split in two: `collect_status()` (reads VE.Direct/modem/`/proc`, builds the `status.json` document) now runs on every tick unconditionally, wrapped in its own try/except so a sensor-read failure cannot take down the loop; `write_status_blob()` (the actual blob PUT) still runs only under `STORAGE_ENABLED`. The direct `POST /api/devices/heartbeat` (`push.py:post_heartbeat()`) runs right after collection, gated only on `push.enabled()` — independent of storage. Spool drain (`storage.drain_event_spool()`) stays unconditional on `STORAGE_ENABLED` but no longer coupled to collection succeeding, which it previously was by accident of both living in one function.

Shutdown (SIGTERM/SIGINT) is ordered: source stops, workers join, undelivered transport jobs are preserved as spooled events — the record survives a restart; only un-uploaded clip audio is lost, counted.

A hang is no longer invisible (R-2.7, 2026-08-26): under systemd, `watchdog.py` pings `WATCHDOG=1` only while the classify and transport threads prove alive (a heartbeat per loop iteration, empty ones included); a hung thread starves the ping and `WatchdogSec=120` restarts the service. Outside systemd (bench runs by hand) the watchdog is a no-op. The superseded monoliths are preserved in `legacy/superseded-monolith/`.

The housekeeping thread is the one that pings, so nothing on it may run unbounded (2026-10-24, F-33). Every drain it performs — event spool, push spool — takes a time budget (`OCEANKIND_HOUSEKEEPING_BUDGET_S`, 30 s) and calls `watchdog.tick()` between items; what does not fit waits for the next heartbeat. Before this, a 500-event drain after an outage (500 × 60 s read timeout) or 50 pending WhatsApp alerts (× 15 s × recipients) could exceed `WatchdogSec` and systemd would restart a *healthy* unit in mid-recovery; worse, the WhatsApp retry only persisted its attempt counter after the loop, so the kill replayed the same alerts forever. Capture starts before any network call for the same reason. (The WhatsApp retry and the IoT Hub connect left with D-020 the same day.) Degradation alerts raised on the classify thread are queued in `health` and logged by housekeeping (`flush_degraded_alerts`), never inline.

**The OTA trigger lives here too (D-021).** After each configuration poll, `ota.maybe_trigger()` compares the release the verified document assigned with what `update_oceankind.sh` recorded as installed (`.installed_release`) and failed (`.ota_failed_release`); when they differ, the unit is not held, no OTA holds the lock and the last launch is older than `OTA_RETRIGGER_S`, it starts the script **as its own systemd unit** (`sudo systemctl start --no-block oceankind-ota@<tag>.service`, a template `setup.sh` installs; output to `/tmp/oceankind/logs/update.log`) — in another cgroup, because the script restarts this very service to verify the build. The first version launched it as a detached child (`start_new_session`) and that was wrong: a new session is still inside the service's cgroup, `systemctl restart oceankind` killed the script mid-verification, the new service instance relaunched it, and the bench looped every seven seconds (F-38, 2026-10-24, the first assignment on hardware). The last launch time is persisted in tmpfs so `OTA_RETRIGGER_S` holds across service restarts as a second guard. Outside systemd (bench by hand, tests) it falls back to the detached child, which survives there. Nothing here runs git; everything the process knows about versions is read from the files the script writes, and published in `status.json → ota`.

---

## Threading model

**Implemented 2026-08-13 (Phase 2).** The invariant: **capture never blocks on anything.** Not on network, not on disk, not on serial, not on a lock held by anyone doing those things. The sounddevice callback copies the block, enqueues it, counts what it drops — nothing else.

| Thread | Owns | Touches | Never does |
|---|---|---|---|
| capture (`capture.py`) | PortAudio stream, block queue | Audio hardware | Network, disk, heavy CPU |
| classify (`pipeline.py`) | Window assembly (5 s, optionally overlapped via `window_hop_s`), the ordered detector registry (`detectors/`, D-014), alert decision, archive sampling | CPU, small local writes | Network |
| transport (`pipeline.py`) | Blob uploads, direct event push to the backend (`push.py`) — the push is also how a detection reaches a human (D-020) | Network | Blocks anything upstream |
| housekeeping (main thread) | status.json, VE.Direct serial, modem HTTP, `/proc`, config poll, OTA trigger (`ota.py`), spool drains | Serial, network, sysfs | Touches the audio device |

One deliberate deviation from the original five-thread design: telemetry and the config poller are folded into the housekeeping main thread. They are all timer-driven, none touches the audio path, and fewer threads is cheaper on the 512 MB bench unit. The isolation guarantee that matters — nothing between two audio blocks — is unchanged.

Shared state and its protection:

| Resource | Access | Synchronisation |
|---|---|---|
| Block queue (≈20 s of audio) | capture W, classify R | Bounded `queue.Queue`; full → drop OLDEST block, count |
| Transport queue | classify W, transport R | Bounded; full → event straight to disk spool, clip audio dropped and counted |
| Tunables (`config.CONFIG`) | housekeeping W, all R | `threading.Lock`, snapshot per clip |
| Health counters (`health.py`) | all W, housekeeping R | Single module lock |
| Event spool / push spool / WA retry files | transport + housekeeping | Idempotent file ops, bounded. Blob-spool drops are a real loss (counted, near-sacred); push-spool drops are tolerable — the blob holds the record |
| Battery alert state | housekeeping only | Owned solely by housekeeping |

**Queues are bounded, always**, each with an explicit drop policy and a published counter (`health.clips_dropped`, `health.capture_overflows`, `health.events_dropped`). An event is never dropped: when a queue overflows, the event JSON goes to the disk spool and only clip audio is sacrificed, counted. A system that reports what it dropped is honest.

---

## State machine

**Implemented 2026-08-12 (Phase 1)** as the `health` block in `status.json`, not as an explicit state enum. Same semantics:

```
[BOOT] → [HEALTHY] ⇄ [DEGRADED] → [HEALTHY]
             ↓            ↓
         [SHUTDOWN]   (still running, still capturing, saying so)
```

`DEGRADED` means any of: `health.detector_ok: false` (no detector loaded, or 3 consecutive failures of any one loaded detector — counted per detector since 2026-10-24, F-31), `health.audio_ok: false` (peak RMS below the floor across the window), `health.capture_ok: false` (no audio window assembled for `CAPTURE_STALL_S`, or duty cycle under 90 % — F-29), the event spool failing to drain for 3 heartbeats (F-32), a rejected or malformed remote config, a 401 from the backend, or 3 failed heartbeats in a row. Each sets `health.degraded_reason` to something a human can act on; the **backend** sends one WhatsApp when the reason appears or changes between two heartbeats and one when it clears (D-020 — the device used to send it, and `alerts_enabled`/`BENCH` from D-019 left with that). The device keeps doing everything it still can — in `auto` mode a dead classifier falls back to real RMS detection.

The health windows (duty cycle, RMS peak) are trimmed when they are *read*, not only when new samples arrive. Trimmed only on write, a stream that dies leaves both frozen at their last healthy value — 100 % duty, `audio_ok: true` — which is the silent-deaf shape exactly (F-29, found by the 2026-10-24 review; the same shape F-28 showed on the bench).

Additionally, invalid configuration (missing secrets, unknown detection mode) is a refuse-to-start, not a degraded state: systemd restart-loops loudly rather than the unit running misprovisioned.

The point is not the diagram. The point is that "detecting nothing" is now a state the system can be in and can name.

---

## Power management

12V LiFePO4 bank, 40 to 100W panel, Victron BlueSolar MPPT read over VE.Direct serial at 19200 8N1.

Battery alerting already has debounce and hysteresis, which is correct and should be preserved through the refactor. Thresholds follow lead-acid style levels: roughly 12.6V full, 12.2V at 80 percent, 11.8V as a warning.

**Known defect.** The dedup state file lives in `/tmp`, which is RAM under the overlay, so every reboot re-arms the alerts and duplicates the warning (F-19). Fix alongside the telemetry persistence work.

System load is derived rather than measured: panel power minus what enters the battery when charging, or voltage times current when discharging. It ignores MPPT losses of roughly 5 percent. Good enough for trend, not for accounting.

---

## Filesystem strategy

**This is the least settled part of the system. See D-002.**

Current state: `scripts/protect_sd.sh` enables the whole-root overlay via `raspi-config nonint do_overlayfs 0`, sets `/boot` read-only via `do_boot_ro 0`, writes a tmpfiles rule creating `/tmp/oceankind/{logs,clips}`, and symlinks `~/oceankind/clips` to the tmpfs path.

What that produces:

| Path | Backing | Survives reboot | Status |
|---|---|---|---|
| `/` | Overlay, upper layer in RAM | No | Intended |
| `/boot/firmware` | SD, read-only | Yes | **Nothing writes here anymore** (F-16 fixed 2026-08-13) |
| `/tmp/oceankind` (`STATE_DIR`) | tmpfs (RAM) | No | Telemetry CSV (trimmed), battery state, event spool. One env var (`OCEANKIND_STATE_DIR`) moves all of it when D-002 lands a persistent partition |
| `~/oceankind/archive_queue` | Overlay (RAM) | No | Bounded at 300 clips, drops counted (F-22) |
| `/etc/oceankind.env` | Overlay | Only if written with overlay disabled | Fine, provisioned once |

The tmpfiles rule has `-` in the age field, so systemd creates the directory and never expires anything in it. There is no cron, no timer, no logrotate anywhere in the repository. Combined with code that deletes clips on exactly one of three paths, this is the memory exhaustion path.

Three rules regardless of how D-002 lands. Nothing writes to `/boot/firmware`. Everything written to a RAM-backed path is deleted on every code path including error paths. Anything that must survive a reboot goes to a deliberately chosen persistent location, not wherever the code happens to be pointing.

### The overlay maintenance window (redesigned 2026-09-30)

An OTA on an overlay unit still needs the overlay off to write anything that must survive — that constraint didn't change. What changed is where the state tracking that constraint lives, after fault injection (`tools/ota_fault_test.sh`, F-27) found the original two-phase handoff could never actually complete: the phase-1 flag and its one-shot systemd unit were both *written while the overlay was still on*, so they landed on the RAM upper layer and were discarded by the very reboot meant to carry them to phase 2 — the overlay went off and silently never came back on.

The fix removes the reboot-surviving flag entirely rather than trying to make one survive. `oceankind-ota-boot.service` is a permanent unit, written once by `setup.sh` to the real (not overlaid) root, that runs `update_oceankind.sh --boot` on **every** boot via `oneshot`/`multi-user.target`. What it does depends only on state that is itself persistent and readable fresh each time:

- `overlay_active()` checks the live mount (`findmnt -o FSTYPE /`, falling back to `/proc/cmdline`) — the actual current state, not `raspi-config`'s configured-for-next-boot value, because toggling that config only takes effect after a reboot that hasn't happened yet.
- `.sd_protection` — a marker `protect_sd.sh` writes once, while root is still writable, before it ever enables the overlay. `protect_sd.sh` now refuses to enable the overlay at all if `oceankind-ota-boot.service` isn't already installed, rather than protecting a unit with no way to recover.

So "we are mid-maintenance" is never a flag that has to survive a reboot — it's the overlay being off *and* `.sd_protection` existing, both true before the reboot and both still true after. On a normal protected boot (overlay on) the boot unit's only job is a cheap journal check (below) and it exits. Cron-side, `ota_phase1()` disables the overlay and reboots only when a non-blacklisted target SHA is actually found. The maintenance window is bounded to `OCEANKIND_OTA_MAX_MAINT` boots (default 3, `.ota_attempts`) and **always** re-enables the overlay on the way out via `finish_maintenance()` — whether the update succeeded, failed, or the attempt budget ran out — so an update that is taking too long leaves the SD protected rather than exposed. `touch ~/oceankind/.hold_maintenance` is the manual override for deliberate overlay-off work.

**The update itself is now journaled and locally reversible.** `~/oceankind/.ota_journal` holds one of `installing` / `verifying` / `rollingback`, written atomically (temp file + `sync` + rename + `sync`) at each phase of `deploy()`/`rollback()` in `update_oceankind.sh`; its absence means the tree is settled, its presence is the sole signal of a half-finished update, checked by `oceankind-ota-boot.service` on every boot regardless of overlay state. Before anything is touched, `deploy()` takes a full local snapshot of the current install (`~/oceankind/.snapshot/`, finalised with its own atomic rename) — rollback restores from that snapshot with no git or network call, which is what makes recovery possible even mid-outage. `.installed_sha` is written only after `restart_and_verify()` succeeds, never before, so a build that never got verified can never be recorded as current. A single `mkdir`-based lock (`$RUN_DIR/ota.lock`, PID-checked and self-clearing if the owning process is dead) keeps two updaters from running at once. Full fault-injection campaign and remaining hardware-only gaps: `docs/OTA-FAULT-TESTING.md`.

**The target is an assigned release, never a branch (2026-10-24, D-021).** The script takes `--ref <tag>` from the service, or with no argument reads `.assigned_release` (what the last verified configuration assigned, `"<tag|-> <hold>"`) and, when that file is absent — an overlay unit loses it across the reboot into the maintenance window — asks `GET /api/devices/config` itself with the credential derived from `/etc/oceankind.env` (`openssl dgst -hmac`, `curl`, `python3` for the JSON; the signature is not re-verified in bash because the value only selects a tag that already exists in the repository the unit trusts, and it still has to match the tag regex). No assignment or `hold` means the script exits without touching anything. `fetch --tags` replaces `fetch origin main`; on commit it records `.installed_release` next to `.installed_sha`, on a failed verification `.ota_failed_release` next to `.ota_failed_sha`, and the service compares tag names, so neither side ever needs git to know what runs. `oceankind-ota.timer` (daily, 03:30 ± 30 min, `Persistent=true`) is the safety net for a unit whose service is down and therefore not polling. `tools/ota_rollback_test.sh` (eight scenarios, including no-assignment and hold) and `tools/ota_fault_test.sh` (with a stubbed `curl` standing in for the backend) both pass against the rewritten script.

---

## Error handling and recovery

What exists and works: graceful degradation nearly everywhere, a pending-alert buffer with bounded retries for flaky cellular, non-finite float sanitisation before JSON upload, battery debounce and hysteresis.

Status after Phase 1 (2026-08-12):

| Failure | Now | Remaining |
|---|---|---|
| Classifier fails (F-02) | **Fixed:** counted, alarmed, `detector_ok: false`; RMS fallback in `auto` mode; retried every clip | — |
| Capture device vanishes or the stream silently stops | Warn and retry the same dead index (F-15); `audio_ok: false` + alarm after the health window | Re-detect by name (Phase 2); since 2026-10-24 `capture_ok: false` + alarm within `CAPTURE_STALL_S` (60 s), duty cycle reads 0, not 100 (F-29) |
| Process hangs without crashing | **Fixed 2026-08-26 (R-2.7):** systemd `WatchdogSec=120` + pings gated on thread heartbeats — a hung main/classify/transport thread starves the ping and systemd restarts the service. Deliberate boundary: degraded-but-running states (dead hydrophone, network down) do NOT restart — they fail loud instead; a restart loop would mask them. A stalled capture *callback* reaches the fail-loud path (`capture_ok`, duty, `last_clip_age_s`), not the watchdog — true only since 2026-10-24; before, the windows froze at their last healthy value (F-29) | — |
| Upload fails after notify (F-13) | **Fixed:** upload first, `clip_uploaded` recorded truthfully, no dead links | — |
| Bad update | **Fixed, 2026-09-04 then hardened 2026-09-30 (F-06, F-27):** journaled install, local snapshot, post-restart health gate, boot-time recovery. `tools/ota_fault_test.sh` cuts the real script at every command boundary (power/network, single and double faults) — 0 violations after the rewrite | Proof on real hardware: passwordless sudo from the boot unit, the real overlay and power-cut campaign (`docs/OTA-FAULT-TESTING.md` §5) |
| Archive queue backs up | **Fixed:** bounded at 300, drops counted in `health.clips_dropped` | Capture/transport queues arrive with Phase 2 |

The old `_load_ml_model` stub (which cached its own failure for the life of the process) is deleted; classification failures are now counted per clip and retried on the next one, with the alarm firing at three consecutive failures.

---

## Design decisions

### Capture via `arecord` subprocess — REPLACED 2026-08-13

**Context.** Original implementation. Simple, no Python audio dependency.
**Tradeoff given up.** Continuous capture. Each invocation opened and closed the ALSA device, and the call blocked for its full duration.
**Replaced by** the `sounddevice` callback stream (D-006), ported from the legacy prototype, with device selection by name (F-15). Audio never touches disk except the 1/min archive sample and event clips; clips are serialised to WAV in memory. A synthetic source (`OCEANKIND_AUDIO_SOURCE=synthetic:<pattern>`) provides the same block contract with no hardware (R-9.4).

### Whole-root overlay for SD protection

**Context.** SD cards die from write cycles and corrupt on power loss. Remote solar node.
**Tradeoff given up.** Persistence, and a simple update path. The OTA process has to disable and re-enable the overlay, which was a two-reboot dance that could strand the node — and did, in testing, until the 2026-09-30 redesign (see **The overlay maintenance window**, above): the window is now bounded, state lives off the overlay rather than on it, and the overlay is always re-enabled on the way out regardless of outcome.
**Status.** The stranding risk this entry originally flagged is mitigated on a workstation harness; real-hardware proof is still open. The broader choice of filesystem strategy (this overlay vs. a dedicated writable partition vs. discipline) is unchanged and still open. See D-002.

### ML classifier over STA/LTA

**Context.** The prototype in `legacy/` used STA/LTA with frequency analysis. Production replaced it with a 53-parameter logistic regression over 52 MFCC and spectral features.
**Tradeoff given up.** A rule-based detector needs no training data and degrades predictably. The classifier is more accurate on its training distribution and fails completely and silently outside it.
**Unvalidated.** Whether it detects distant, attenuated blasts at all. See `../../../docs/IMPROVEMENT_REPORT.md` §2.5. This is a bigger open question than anything in the software.

---

## Known limitations

Detection duty cycle is well below 100 percent. Since Phase 1 (2026-08-12) it is measured and published as `health.duty_cycle_pct` / `health.deaf_seconds_total`; the 24-hour bench baseline is still pending, and Phase 2 is judged against it.

Classifier range is unvalidated against labelled distant recordings.

No watchdog. A hang is invisible.

Single unit, no redundancy, no remote recovery.

The bench unit's 512 MB may not fit the Phase 2 architecture alongside librosa and scikit-learn. Measurable, not assumable, and the answer feeds D-011.
