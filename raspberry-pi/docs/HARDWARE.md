# OceanKind — Hardware Guide

## Full System Diagram

```
[Solar Panel]
     │
     ▼
[Solar Charge Controller] ── [12V LiPo/LiFePO4 Battery]
     │
     ├──→ [MT3608 Boost Module] → 9V DC
     │          │
     │          ▼
     │    [Bias-T Circuit]  ←──── RG59 Coax ←── [Aquarian H5 Hydrophone #1]
     │    [Bias-T Circuit]  ←──── RG59 Coax ←── [Aquarian H5 Hydrophone #2]
     │          │ (AC audio out)
     │          ▼
     │    [HifiBerry ADC Pro HAT]
     │          │ (I2S)
     │          ▼
     ├──→ [Raspberry Pi 4 Model B]
     │          │ Ethernet
     │          ▼
     │    [Router / Switch] ──── Internet ──── [OceanKind Server]
     │
     └──→ (future) [SX1276 LoRa module via SPI]
```

---

## Components List

| Component | Model | Notes |
|-----------|-------|-------|
| Single-board computer | Raspberry Pi 4 Model B (2GB+) | Main processor |
| Audio input HAT | HifiBerry DAC+ ADC Pro | Stereo 24-bit 192kHz I2S input |
| Hydrophone × 2 | Aquarian H5 | RG59 coax, internal preamp, needs 9–12V bias |
| Boost converter | MT3608 module | In: 12V, Out: 9V regulated, ~$1–2 |
| Solar charge controller | Any PWM or MPPT, 10A+ | 12V system |
| Battery | LiFePO4, 20–50Ah, 12V | Sufficient for cloudy days |
| Solar panel | 40–100W, 12V | Size depends on duty cycle |
| Enclosure | IP67 waterproof junction box | For electronics |

---

## Bias-T Sub-Circuit (per hydrophone channel)

The Aquarian H5 has an internal preamp that needs ~9–12V DC injected
through the center conductor of the RG59 coaxial cable.
The audio signal rides on top of this DC — the Bias-T separates them.

### Schematic (one channel)

```
9V DC ──┬── R1 (10kΩ, ¼W) ──┬────────────── TO RG59 CENTER (→ Hydrophone)
        │                    │
       GND                  L1 (100µH inductor, optional — improves filtering)
                             │
                      ───────┤
                      │      │
                     C1      │ ←── signal rides here
                    (10µF)   │
                      │      │
                     C2    GND (RG59 shield)
                    (100nF)
                      │
                      └──────→ AUDIO OUT (to HifiBerry ADC Pro RCA input)
```

### Component values

| Part | Value | Purpose |
|------|-------|---------|
| R1 | 10 kΩ, ¼W | Feeds DC to hydrophone; high value isolates audio path from power supply noise |
| L1 | 100 µH (optional) | RF choke — cleaner DC injection; omit if hard to source |
| C1 | 10 µF electrolytic, 25V+ | Low-frequency coupling; blocks DC from audio output |
| C2 | 100 nF ceramic (0.1µF) | High-frequency coupling; together with C1 covers full audio range |
| TVS1 | P6KE15A or similar | Transient voltage suppressor — protects ADC input from voltage spikes |

### Total cost per channel
~$2–4 in components. Build two (one per hydrophone) on a small perfboard
or have a custom PCB made (KiCad files can be added to this repo).

### Power for the 9V rail
Use an MT3608 boost converter module (widely available, < $2):
- Input: 12V from battery
- Output: Set to exactly 9.0V using the trimmer potentiometer
- Current: < 10 mA per hydrophone (H5 draws very little)

---

## HifiBerry ADC Pro — Connection

The HifiBerry DAC+ ADC Pro has RCA (cinch) input connectors.

Connect the audio output of each Bias-T circuit to one RCA input:
- Hydrophone #1 Bias-T audio out → Left RCA (channel 0 in software)
- Hydrophone #2 Bias-T audio out → Right RCA (channel 1 in software)

The HifiBerry stacks directly onto the Pi 4 GPIO header.
No soldering needed for the HAT — only the Bias-T circuit requires soldering.

### Raspberry Pi /boot/config.txt (done automatically by setup.sh)
```
dtoverlay=hifiberry-dacplusadcpro
# dtparam=audio=on   ← must be disabled
```

---

## Power Budget (medium solar setup)

| Component | Current draw |
|-----------|-------------|
| Raspberry Pi 4 (idle, detection running) | ~600 mA @ 5V = 3.0 W |
| HifiBerry ADC Pro | ~50 mA @ 5V = 0.25 W |
| 2× Aquarian H5 (biased) | ~20 mA @ 9V = 0.18 W |
| MT3608 boost (losses ~10%) | +0.02 W |
| **Total** | **~3.5 W** |

A 40W solar panel with a 20Ah LiFePO4 battery can run this system
24/7 at a location receiving 4–5 peak sun hours/day.

---

## Future LoRa Expansion (SX1276)

Add a LoRa module via SPI (e.g. RYLR998 or Hope RF95):
- Connect to Pi SPI0 (pins: MOSI=19, MISO=21, SCLK=23, CS=24, RST=22)
- Install: `pip install pyLoRa`
- The detector already builds compact JSON alert payloads suitable for LoRa's
  limited bandwidth (~50 bytes per event, well within LoRa's payload limits)

---

## Weatherproofing

All electronics go in an IP67 enclosure.
- Use waterproof cable glands for RG59 entry and Ethernet exit.
- The Aquarian H5 is rated for underwater deployment.
- Keep the enclosure out of direct sun (heat accelerates battery aging).
- Mount the enclosure vertically to aid drainage from glands.
