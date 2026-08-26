# CLAUDE.md — raspberry-pi

Rules for working on the device code. Every rule here exists because of a defect that has already happened or is one bad day away from happening.

Based on the Lynch Protocol `lyncHwareProtocol` variant, adapted. No server infrastructure doc, since deployment here is a solar node and a storage account rather than containers and ports.

---

## Before ANY task

**Read `../CLAUDE.md` first.** It covers the whole stack and it carries the one rule that matters most: the data contract couples this codebase to the dashboard and nothing validates it.

Then, in order:

1. **`README.md`** — Layout, what the system does, the traps that will surprise you
2. **`../docs/FINDINGS.md`** — 23 verified defects, most now fixed. Four can silently stop detection
3. **`docs/ARCHITECTURE.md`** — Process model, threading, state machine, power, filesystem. **Read before any design decision.**
4. **`../docs/DATA-CONTRACT.md`** — Everything this device writes is consumed by the dashboard
5. **`../DECISIONS.md`** — What is settled, what is open, what is blocked
6. **`docs/PROGRESS.md`** — What is built, what is next
7. **`docs/TODO.md`** — Known issues outside the roadmap
8. **`docs/HARDWARE.md`** — Hydrophone and ADC reference. Note its caveats in the README

Before touching hardware-facing code, `docs/HARDWARE.md` is not optional. Before touching threading or the main loop, `docs/ARCHITECTURE.md` is not optional.

---

## The premise

This code runs unattended, on solar power, on a coastline, with no physical access, and its only job is to notice a sound that lasts a fraction of a second. Two consequences follow and they govern everything else.

**Silence is the worst possible failure.** A crashed system gets noticed. A system that reports itself healthy while detecting nothing does not. Multiple existing defects have exactly this shape.

**Every change must be survivable.** There is no console, no keyboard, and no one to press a button. A change that can leave the unit unreachable is worse than the bug it fixes.

---

## Hard rules

**Never fail quietly.** Any condition that degrades or disables detection must reach a human. Set a flag in `status.json` and send a WhatsApp. This includes model load failure, capture device failure, queue overflow and repeated upload failure. Returning an empty dict and continuing is the pattern that produced F-01.

**Never write a secret as a literal default.** Configuration comes from `/etc/oceankind.env`. If a required secret is absent, log loudly and refuse to start. `os.environ.get("X", "actual_secret")` is how a live Twilio token ended up in source, in a backup and in a `.pyc`.

**Nothing may write to `/boot/firmware`.** It is read-only whenever SD protection is enabled. Runtime state goes to the tmpfs runtime directory and is deliberately flushed to a writable persistent location, never to the boot partition.

**Everything written to the clips directory must be deleted on every code path.** That directory is RAM. A path that creates a file and does not remove it on all branches, including error branches and early returns, is a memory leak on a 2 GB device.

**The capture path never blocks on the network.** No HTTP call, no blob upload, no Twilio request, and no serial read may sit between one audio buffer and the next. When the async pipeline lands, this is the invariant that matters.

**Bounded queues only, and publish what you drop.** On slower hardware an unbounded queue converts the deaf-window problem into a silent backlog, which is the same failure wearing a different hat. Every queue gets a maximum size, an explicit drop policy, and a counter in `status.json`.

**Every timeout is explicit.** The Twilio client currently has none. A network call with no timeout on a single-threaded loop is an unbounded deaf window.

**Detections are data, not just alerts.** An event that is suppressed by cooldown must still be recorded. The manifest is the scientific record of blast activity and it is used to reason about frequency. Discarding events because a notification was rate-limited corrupts that record. See F-03.

**No hardcoded device indices.** `plughw:3,0` breaks on USB re-enumeration. Detect by name.

---

## Testing

Bench first, always. The bench unit is a Raspberry Pi Zero 2W.

Two caveats about that unit. It has 512 MB of RAM against librosa, numpy and scikit-learn, so measure resident memory rather than assuming it fits. Feature extraction is slower than on the Pi 4, roughly 3 to 5 seconds per clip, which makes it a useful worst case for queue behaviour.

If the bench unit has no audio HAT, use `snd-aloop` to present recorded WAV files as an ALSA capture device. That exercises the threading and service lifecycle, which is where the bugs live.

Prove rollback by deliberately breaking an update on the bench unit. An untested rollback is not a safety net.

---

## Sequencing

Phases are defined in `docs/PROGRESS.md` and numbered consistently with the dashboard and the stack rollup.

Security and fail-loud work first, because it needs no Azure access and removes the worst risk per hour spent. Then the async pipeline. Then OTA safety. Then production deployment.

**Do not begin the async refactor before the fail-loud work is done.** Threading bugs and silent failures are indistinguishable in the field, and you will burn days separating them.

---

## After completing a feature

1. **`../docs/DATA-CONTRACT.md`** if any field written to blob storage changed. Not optional, and check `https://github.com/rodgpt/Dashboard-Detector/blob/main/docs/PROGRESS.md` for whether that side is ready
2. **`docs/PROGRESS.md`** — check items off
3. **`docs/TODO.md`** — add what you found, check off what you fixed
4. **`docs/ARCHITECTURE.md`** if the process model, threading, state machine or filesystem strategy changed
5. **`docs/HARDWARE.md`** if anything physical changed
6. **`../docs/FINDINGS.md`** — mark fixed defects. Do not delete them
7. **`../DECISIONS.md`** if a choice was made that affects the dashboard too
8. **`README.md`** if what runs or where it lives changed
9. **`../docs/research/RESEARCH.md`** if a new analysis doc was written

Do this before considering anything done. Context gets compressed, sessions end, the docs are what survives.

---

## Vocabulary

| Term | Meaning |
|---|---|
| Deaf window | Time when the hydrophone is not recording because the loop is doing something else |
| Silent-deaf | The system detects nothing but reports itself healthy |
| Cooldown | 600-second minimum interval between alerts. Currently discards the events it suppresses |
| Overlay | The read-only root filesystem set up by `scripts/protect_sd.sh`. Writes go to RAM |
| Bench unit | The Pi Zero 2W used for testing. Never the production node |
| Production node | The single deployed unit at Lagunillas, Navidad |
