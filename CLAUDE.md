# CLAUDE.md — OceanKind Device

Rules for working in this repository. Based on the Lynch Protocol `lyncHwareProtocol` variant, adapted: deployment here is a solar node and a storage account, not containers and ports.

Self-contained. Everything referenced is in this repository.

---

## Before ANY task

1. **`REQUIREMENTS.md`** — what this must do. Numbered, testable. The spec
2. **`docs/FINDINGS.md`** — 23 verified defects, most now fixed with dates. **Read F-21 first**; the detector may not detect the thing this system exists to detect
3. **`raspberry-pi/docs/ARCHITECTURE.md`** — process model, threading, state machine, power, filesystem. **Read before any design decision**
4. **`docs/DATA-CONTRACT.md`** — everything this device writes is consumed by the dashboard. Canonical copy, the dashboard mirrors it
5. **`DECISIONS.md`** — what is settled, what is open, what is blocked
6. **`raspberry-pi/docs/PROGRESS.md`** — what's built, what phase the work is in
7. **`raspberry-pi/docs/TODO.md`** and **`raspberry-pi/docs/HARDWARE.md`**

Before touching hardware-facing code, `HARDWARE.md` is not optional. Before touching threading or the main loop, `ARCHITECTURE.md` is not optional.

---

## The premise

This runs unattended, on solar power, on a coastline, with no physical access, and its only job is to notice a sound lasting a fraction of a second.

**Silence is the worst possible failure.** A crash gets noticed. A unit reporting itself healthy while detecting nothing does not. Four catalogued defects have exactly that shape.

**Every change must be survivable.** No console, no keyboard, nobody to press a button. A change that can leave the unit unreachable is worse than the bug it fixes.

---

## Scope: plumbing, not water

We build everything that carries a detection from hydrophone to human. We do not decide what counts as a detection.

Ours: capture, queues, the classification harness, transport, retry, storage layout, security, updates, telemetry, health.

Theirs: what signal to look for, whether a detector works, thresholds on scientific grounds, the model, labelled audio.

When a question is about signal science, it goes to `docs/CLIENT-DEPENDENCIES.md`, not into an implementation.

The boundary is only honest if the plumbing never silently constrains the water. Three things stay ours because of it: the detector interface must express any event type, no event a detector produces may be lost by our code, and every threshold must be remotely tunable without a firmware update.

---

## Hard rules

**Capture never blocks.** No network call, disk write, serial read or lock between one audio buffer and the next. This is the invariant everything else serves.

**Bounded queues only, and publish what you drop.** An unbounded queue turns a deaf window into a silent backlog. Same failure, different clothes. Maximum size, explicit drop policy, counter in `status.json`.

**Never fail quietly.** Any condition that degrades or disables detection reaches a human: a flag in `status.json` and an alert. Returning an empty dict and continuing is the pattern that produced F-02.

**Never ship a mode that cannot fire.** `DETECTION_MODE=rms` is advertised at startup and is arithmetically incapable of producing an alert. If a configuration is offered, it must work.

**Never write a secret as a literal default.** Configuration comes from `/etc/oceankind.env`. If a required secret is absent, log loudly and refuse to start. `os.environ.get("X", "<actual secret>")` is how a live Twilio token reached source, a backup, two bytecode caches and a git remote.

**Nothing writes to `/boot/firmware`.** It is read-only whenever SD protection is on. Runtime state goes to the tmpfs runtime directory and is flushed deliberately to a writable persistent location.

**Everything written to a RAM-backed path is deleted on every code path.** Including error paths and early returns. That directory is memory on a 2 GB device.

**Detections are data, not just alerts.** A cooldown throttles notifications. It must never decide what gets recorded. The manifest is the scientific record of activity and is used to reason about frequency; discarding events because a notification was rate-limited corrupts it.

**Every timeout is explicit.** An untimed network call on the capture thread is an unbounded deaf window.

**No hardcoded device indices.** `plughw:3,0` breaks on USB re-enumeration, on a node nobody can reach. Detect by name.

**Non-finite floats serialise as `null`.** Python emits `Infinity`, which is not valid JSON and blanks the dashboard. This is a scar from a real outage.

---

## Testing

**Bench first, always.** The bench unit is a Raspberry Pi Zero 2W. Two caveats: 512 MB of RAM against scipy and numpy, so measure resident memory rather than assuming it fits; and feature extraction is slower than on the Pi 4, which makes it a useful worst case for queue behaviour.

**No hydrophone needed.** Use a synthetic audio source, or `snd-aloop` to present recorded WAVs as an ALSA capture device. That exercises threading and service lifecycle, which is where the bugs live.

**No Azure needed.** Write to a local output directory and validate it:

```bash
python3 tools/validate_contract.py ./out
```

That is the test for the contracted work. Exit 0 means the device produces blobs the dashboard can consume. A reference tree in the same shape comes from the dashboard repository's fixture generator.

**Prove rollback by breaking an update on purpose.** An untested rollback is not a safety net.

---

## Sequencing

Phases are in `raspberry-pi/docs/PROGRESS.md`, numbered consistently with the dashboard.

Fail-loud work first, because it needs no Azure and removes the worst risk per hour. Then continuous capture. Then the detector registry. Then contract and storage. Then deployment.

Fail-loud (Phase 1) and continuous capture (Phase 2) are **done** — the code is the threaded `oceankind/` package, not the monolith. Sequencing that still binds: prove rollback on the bench before any OTA work touches a real unit, and no Phase 4 security work without the real Azure account.

---

## After completing a feature

1. **`docs/DATA-CONTRACT.md`** if any emitted field changed. Not optional. This is the canonical copy and the dashboard mirrors it
2. **`raspberry-pi/docs/PROGRESS.md`** — check items off
3. **`raspberry-pi/docs/TODO.md`** — add what you found, check off what you fixed
4. **`raspberry-pi/docs/ARCHITECTURE.md`** if the process model, threading, state machine or filesystem strategy changed
5. **`raspberry-pi/docs/HARDWARE.md`** if anything physical changed
6. **`docs/FINDINGS.md`** — mark fixed defects. Do not delete them
7. **`DECISIONS.md`** if a choice was made that affects the dashboard too
8. **`REQUIREMENTS.md`** if the spec itself changed, which should be rare and deliberate
9. **`docs/research/RESEARCH.md`** if a new analysis doc was written

Do it before considering anything done. Context gets compressed, sessions end, the docs are what survives.

**Do not run git commands** unless explicitly asked.

---

## Vocabulary

| Term | Meaning |
|---|---|
| Deaf window | Time when the hydrophone is not recording because the loop is busy |
| Silent-deaf | Detects nothing, reports itself healthy. The worst failure mode |
| The contract | `docs/DATA-CONTRACT.md`. The only coupling to the dashboard |
| Bench unit | The Pi Zero 2W. Never the production node |
| Production node | The deployed units at Zapallar and Matanzas. We cannot reach either |
| Plumbing / water | What we build / what the client provides |
