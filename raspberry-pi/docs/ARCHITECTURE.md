# Device architecture

**Version:** v1.1.0 as deployed
**Last updated:** 2026-08-02

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
        ├── transport    clip upload → event blob → WhatsApp/IoT. All network lives here
        └── main thread  housekeeping: status.json, telemetry, battery, remote config,
                         spool drain, WhatsApp retry, heartbeats — each on its own timer
```

Shutdown (SIGTERM/SIGINT) is ordered: source stops, workers join, undelivered transport jobs are preserved as spooled events — the record survives a restart; only un-uploaded clip audio is lost, counted.

No watchdog yet: a process that hangs rather than crashes is still invisible (R-2.7, Phase 5). The superseded monoliths are preserved in `legacy/superseded-monolith/`.

---

## Threading model

**Implemented 2026-08-13 (Phase 2).** The invariant: **capture never blocks on anything.** Not on network, not on disk, not on serial, not on a lock held by anyone doing those things. The sounddevice callback copies the block, enqueues it, counts what it drops — nothing else.

| Thread | Owns | Touches | Never does |
|---|---|---|---|
| capture (`capture.py`) | PortAudio stream, block queue | Audio hardware | Network, disk, heavy CPU |
| classify (`pipeline.py`) | Window assembly (5 s, optionally overlapped via `window_hop_s`), detector, alert decision, archive sampling | CPU, small local writes | Network |
| transport (`pipeline.py`) | Blob uploads, WhatsApp, IoT, cluster call | Network | Blocks anything upstream |
| housekeeping (main thread) | status.json, VE.Direct serial, modem HTTP, `/proc`, config poll, retries | Serial, network, sysfs | Touches the audio device |

One deliberate deviation from the original five-thread design: telemetry and the config poller are folded into the housekeeping main thread. They are all timer-driven, none touches the audio path, and fewer threads is cheaper on the 512 MB bench unit. The isolation guarantee that matters — nothing between two audio blocks — is unchanged.

Shared state and its protection:

| Resource | Access | Synchronisation |
|---|---|---|
| Block queue (≈20 s of audio) | capture W, classify R | Bounded `queue.Queue`; full → drop OLDEST block, count |
| Transport queue | classify W, transport R | Bounded; full → event straight to disk spool, clip audio dropped and counted |
| Tunables (`config.CONFIG`) | housekeeping W, all R | `threading.Lock`, snapshot per clip |
| Health counters (`health.py`) | all W, housekeeping R | Single module lock |
| Event spool / WA retry files | transport + housekeeping | Idempotent file ops, bounded |
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

`DEGRADED` means any of: `health.detector_ok: false` (3 consecutive classifier failures), `health.audio_ok: false` (peak RMS below the floor across the window), or running without Twilio credentials (bench mode). Each sets `health.degraded_reason` to something a human can act on, and the detector/audio cases each send one WhatsApp degradation alert, deduped until recovery. The device keeps doing everything it still can — in `auto` mode a dead classifier falls back to real RMS detection.

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

---

## Error handling and recovery

What exists and works: graceful degradation nearly everywhere, a pending-alert buffer with bounded retries for flaky cellular, non-finite float sanitisation before JSON upload, battery debounce and hysteresis.

Status after Phase 1 (2026-08-12):

| Failure | Now | Remaining |
|---|---|---|
| Classifier fails (F-02) | **Fixed:** counted, alarmed, `detector_ok: false`; RMS fallback in `auto` mode; retried every clip | — |
| Capture device vanishes | Warn and retry the same dead index (F-15); `audio_ok: false` + alarm after the health window | Re-detect by name (Phase 2) |
| Process hangs without crashing | Nothing notices | Watchdog (Phase 5) |
| Upload fails after notify (F-13) | **Fixed:** upload first, `clip_uploaded` recorded truthfully, no dead links | — |
| Bad update | Node unreachable (F-06) | A/B, health check, auto-revert (Phase 5) |
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
**Tradeoff given up.** Persistence, and a simple update path. The OTA process has to disable and re-enable the overlay, which is the two-reboot dance that can strand the node.
**Status.** Under review. See D-002.

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
