# System architecture — whole stack

**Version:** v1.1.0 as deployed
**Last updated:** 2026-08-02

How the pieces fit. Device-internal design (threading, state machine, power, filesystem) is in `raspberry-pi/docs/ARCHITECTURE.md`. This file covers the system.

---

## Philosophy

Three principles the existing system already follows, mostly by instinct rather than by decision. Worth stating because Phase 1 and Phase 2 have to preserve them.

**The two halves are decoupled and stay that way.** The Pi has no HTTP server. The dashboard has no backend. Neither knows the other's address. Blob storage is the entire interface. This means either side can be down, updated or replaced without the other noticing, which on a cellular-connected solar node is worth a great deal. It is also why the container is public, which is the root of most security findings. The fix is to authenticate the seam, not to collapse it.

**The physical world fails constantly and silently.** Cellular drops. The Victron cable is unplugged. USB re-enumerates and the audio device index changes. The modem stops answering. The existing code degrades gracefully almost everywhere, which is correct. What it does not do is *say* it has degraded, which is the central defect class.

**Silence is the worst failure.** The system exists to notice a sound lasting a fraction of a second. A crash gets noticed. A unit reporting "online" every twelve hours while detecting nothing does not. Four catalogued defects have this shape.

---

## Layers

```
┌──────────────────────────────────────────────────────────────┐
│  PHYSICAL                                                     │
│  2x Aquarian H5 hydrophones · audio HAT (D-009: which?)       │
│  40-100W solar · 12V LiFePO4 · Victron MPPT · ZTE 4G modem    │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────┐
│  EDGE — raspberry-pi/                                         │
│  Raspberry Pi 4B, Raspberry Pi OS, overlay filesystem         │
│  capture → features (librosa) → classify (sklearn, 53 params) │
│  telemetry: VE.Direct serial, modem HTTP, /proc, /sys         │
│  Single thread today. Phase 2 makes it four.                  │
└──────────────────────────────────────────────────────────────┘
              ↓ HTTPS                    ↓ HTTPS
┌───────────────────────────┐  ┌──────────────────────────────┐
│  AZURE BLOB STORAGE       │  │  TWILIO → WhatsApp           │
│  marfuturatest / alerts   │  │  detection, heartbeat,       │
│  PUBLIC READ (F-07)       │  │  battery templates           │
│  THE CONTRACT             │  │  deep link back to dashboard │
└───────────────────────────┘  └──────────────────────────────┘
              ↓ HTTPS, 30s poll, no auth
┌──────────────────────────────────────────────────────────────┐
│  CLIENT — dashboard/                                          │
│  One static HTML file on Azure Static Web Hosting             │
│  Chart.js · Leaflet · client-side FFT spectrogram             │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│  AZURE IOT HUB                                                │
│  The Pi connects and sends alerts and heartbeats.             │
│  NOTHING CONSUMES THEM. Dead limb, kept for Phase 5.          │
└──────────────────────────────────────────────────────────────┘
```

The IoT Hub connection deserves emphasis because it misleads readers. `docs/legacy` describes a full Microsoft Project 15 pipeline: routing, Stream Analytics, Cosmos, Power BI. None of it exists. The Pi holds a client and sends messages into a hub nobody reads. It is not load-bearing and it is not free: it is one more network call inside the deaf window.

---

## The seam

Everything that crosses between the two codebases is specified in `DATA-CONTRACT.md`. Read it before changing anything on either side.

The property that matters architecturally: **the contract is unenforced.** No schema, no version field, no validation, no test. Coupling this tight with verification this weak is the main structural risk in the system, ahead of any individual defect. Phase 1 adds a `schema_version` field, which is the cheapest thing that makes the Phase 4 layout migration survivable.

---

## Control flow today

One thread, strictly sequential. This is the architecture Phase 2 replaces.

```
loop:
  every 300s   download remote_config.json, apply           (network, unbounded)
  always       arecord -d 5                                  BLOCKING 5s  ← only listening happens here
  always       librosa load + 52 features + predict          1-3s
  if alert     Twilio send (no timeout)                      seconds to unbounded
               upload clip (960 KB over cellular)            2-20s
               download + rewrite manifest (grows to MB)     seconds
               IoT Hub send                                  seconds
  every 60s    modem HTTP (3s timeout)                       up to 3s
               VE.Direct serial read                         up to 3s
               upload status.json                            1-2s
               append CSV                                    fails silently (F-16)
  every 600s   rebuild + upload power_history.json           seconds
  every 12h    WhatsApp heartbeat                            seconds
```

Everything below the `arecord` line is deaf time. On a quiet cycle that is roughly 20 to 40 percent. On an alert cycle it is plausibly 30 to 60 seconds, immediately after a detection, which is exactly when a blast sequence continues. See F-05.

The two delivered audits disagree about how much this costs in missed events. Neither measured it. Phase 1 instruments the loop so the argument is replaced by a number before Phase 2 changes anything.

---

## Control flow after Phase 2

Four threads, one invariant: **capture never blocks on anything.**

```
capture thread        continuous, ring buffer, device detected by name
     ↓ bounded queue, explicit drop policy, drop counter published
classify thread       features + inference, CPU only
     ↓ bounded queue
transport thread      upload → then notify → then manifest. retries internally
telemetry thread      own timer. serial, modem, system stats
config thread         poll, validate, clamp, apply under lock
```

The bounded queues are not incidental. On slower hardware, an unbounded queue converts the deaf-window problem into a silent backlog, which is the same failure in different clothing. A dropped clip that is counted and published is honest. A queue quietly growing is not.

Detail in `raspberry-pi/docs/ARCHITECTURE.md`.

---

## Failure modes and what happens

| Failure | Today | After Phase 1 and 2 |
|---|---|---|
| Model fails to load | Detects nothing forever, reports online (F-02) | Alarm out, `detector_healthy: false`, RMS fallback |
| `DETECTION_MODE` set to `rms` | Detects nothing forever, silently (F-01) | Works as documented |
| Cellular down | Alerts buffered, capture blocked during timeouts | Buffered, capture unaffected |
| Upload fails after alert sent | Dead link, manifest never updated (F-13) | Upload first, then notify |
| Two detections inside cooldown | Second erased, clip leaks into RAM (F-03) | Recorded as suppressed, clip deleted |
| USB re-enumerates | Capture dead until physical intervention (F-15) | Device re-detected by name |
| Bad OTA update | Node unreachable, no rollback (F-06) | A/B revert on failed health check |
| Power loss mid-write | Overlay protects root; boot partition already read-only | Unchanged, plus D-002 |
| Second device added | Manifest writes race, data silently lost (F-14) | Per-device append-only paths |

The pattern in the left column: every one of them is silent. That is the thing Phase 1 fixes, and it is worth more than any single bug on the list.

---

## Security posture

Current state, honestly: there is none. No authentication at any layer. The container is public by design because the dashboard has no backend. Credentials are literals in source. Sensor coordinates are published. Remote configuration is unsigned.

The chain matters more than any single item. There is no backend, therefore the container must be public, therefore the coordinates and the audio and the detection history are public, therefore the only mitigations available are obscurity. Every finding traces back to the same missing piece.

Phase 4 breaks the chain cheaply: container private, scoped SAS on both sides, config signed. That is real improvement and it is not authentication. A SAS token embedded in client JavaScript is obscurity, and it should be described that way to anyone who asks, including the client.

Phase 5 fixes it properly by putting a backend at the seam. See `BACKEND-SCHEME.md`.

---

## Known limitations

Single unit, single region, no redundancy. If the Pi is down, there is no monitoring and nothing says so except the dashboard going stale after three minutes.

The classifier was trained on 5-second clips at 22.05 kHz and its detection range is unvalidated. `IMPROVEMENT_REPORT.md` §2.5 raises the question of whether distant, attenuated blasts are detectable at all, and nobody has tested it against labelled distant recordings. This is a bigger open question than anything in the software.

The manifest is capped at 5000 entries and truncates from the tail. Long-run history is lost with no archive.

The bench unit has 512 MB of RAM against librosa, numpy and scikit-learn. Whether the Phase 2 architecture fits there is measurable, not assumable, and the answer feeds D-011.

---

## Where the future backend goes

`api/` as a fourth top-level folder, its own root, alongside the existing three. Nothing in the current structure moves to accommodate it. It sits at the seam: the Pi posts to it, the dashboard reads from it, and it holds every credential that currently lives on the device or in the browser.

Scheme in `BACKEND-SCHEME.md`. Platform is D-003 and is deliberately undecided.
