# Bench runbook — Pi Zero 2W soak

From blank SD card to the Phase 1+2 acceptance numbers. No hydrophone, no
Azure, no Twilio needed: the unit runs the synthetic source and writes the v2
tree to its own SD card, then reports its own duty cycle and memory in
`status.json`.

What this run proves:

- **Clean provision** (Phase 1): `setup.sh` takes a blank image to a working unit.
- **Duty cycle ≥ 99 % over 24 h** (R-1.1/R-1.2): measured by the device, on the
  worst-case hardware, with the detector firing constantly.
- **Resident memory vs 512 MB** (R-7.5, feeds D-011).
- **CPU headroom** for the window-overlap decision (client dependency 13).

**Do NOT run `protect_sd.sh` for this soak.** The overlay would put the output
tree in RAM and lose it on any reboot. SD protection gets proven separately in
Phase 5.

---

## 1. Flash the SD card (on the Mac, ~10 min)

1. Install **Raspberry Pi Imager**: `brew install --cask raspberry-pi-imager`
   (or download from raspberrypi.com/software). Card: 16 GB minimum, 32 GB fine.
2. In Imager:
   - **Device**: Raspberry Pi Zero 2 W
   - **OS**: Raspberry Pi OS **Lite (64-bit)** — no desktop. (If the soak shows
     memory is tight, re-flashing with 32-bit Lite is itself a useful data point.)
   - **Storage**: the SD card
3. When asked to apply **OS customisation**, click **Edit settings** and set:
   - Hostname: `oceankind-bench`
   - Username: **`marfutura`** + a password — this matters: the provisioning
     scripts resolve the service user from who runs them, and `marfutura` is
     the documented default (F-17)
   - Wi-Fi: your SSID + password. **The Zero 2W only sees 2.4 GHz networks** —
     a 5 GHz-only SSID will silently never connect
   - Wireless LAN country, locale/timezone
   - Services tab: **enable SSH** (password auth is fine for the bench)
4. Write, wait, eject. Insert into the Pi, power it, give it ~2 min to first-boot.

## 2. Reach it and copy the code (~5 min)

From the Mac (repo root `_Rpi-Detector/`):

```bash
ssh marfutura@oceankind-bench.local        # accept fingerprint, then exit
rsync -av --exclude .git --exclude legacy --exclude '__pycache__' \
    ./ marfutura@oceankind-bench.local:~/Rpi-Detector/
```

The rsync includes `raspberry-pi/oceankind.env` (the gitignored live config) —
that is fine for provisioning, and the bench profile below overrides the parts
that must not fire real notifications.

## 3. Provision (~10 min, mostly apt/pip)

```bash
ssh marfutura@oceankind-bench.local
sudo bash ~/Rpi-Detector/raspberry-pi/scripts/setup.sh
```

The script installs system packages (incl. `libportaudio2`), the Python
dependency set, copies the `oceankind/` package, writes the systemd unit, and
installs `raspberry-pi/oceankind.env` to `/etc/oceankind.env`.

## 4. Bench profile (~2 min)

`sudo nano /etc/oceankind.env` — make it read (only the lines that matter for
the bench; leave the rest):

```bash
OCEANKIND_DEVICE_ID=Rpi_bench
OCEANKIND_SITE=banco
OCEANKIND_SENSOR_LOCATION=Banco Zero2W
OCEANKIND_SENSOR_LAT=-33.0
OCEANKIND_SENSOR_LON=-71.0

# CRITICAL for the bench: either allow-no-twilio, or point TWILIO_TO at YOUR
# own number. The shipped env carries PRODUCTION recipients — a 24 h synthetic
# soak must not message them.
OCEANKIND_ALLOW_NO_TWILIO=1
#OCEANKIND_TWILIO_SID=
#OCEANKIND_TWILIO_TOKEN=

# v2 tree to the SD card (no Azure)
OCEANKIND_OUTPUT_DIR=/home/marfutura/oceankind/out

# Worst-case load: the tone pattern makes the detector fire on EVERY window
OCEANKIND_AUDIO_SOURCE=synthetic:tone
# 1 notified event per hour; the rest recorded as suppressed (~17k tiny JSONs/day)
OCEANKIND_ALERT_COOLDOWN_S=3600
```

## 5. Smoke-check, then start the soak (~10 min)

```bash
sudo systemctl restart oceankind
journalctl -u oceankind -f     # watch for ~2 min
```

You should see: the startup banner, `Fuente sintética iniciada`,
`Captura continua iniciada`… then every ~5 s a level bar with
`psd=MOTOR(1.00) *** ALERTA ***`, one WhatsApp-suppressed log line per window,
and `→ evento registrado … [suprimido]`. After ~60 s, `→ status.json …`.

Sanity-check the tree and the numbers:

```bash
python3 ~/Rpi-Detector/tools/validate_contract.py ~/oceankind/out       # CONFORMANT
python3 -m json.tool ~/oceankind/out/sites/banco/status.json | grep -A14 '"health"'
```

If that looks right: walk away. Leave it running 24 hours.

## 6. Read the verdict (after 24 h)

```bash
ssh marfutura@oceankind-bench.local
python3 -m json.tool ~/oceankind/out/sites/banco/status.json
```

| Field | Pass |
|---|---|
| `health.duty_cycle_pct` | **≥ 99.0** (the Phase 2 acceptance number, R-1.1) |
| `health.deaf_seconds_total` | ~0 relative to 86 400 s |
| `health.capture_overflows` | 0, or near-0 and not growing |
| `health.clips_dropped`, `events_dropped` | 0 |
| `system.ram_used_mb` | whole-system; headroom vs 512 total (R-7.5, D-011) |
| `uptime_seconds` | ~86 400 (no crashes/restarts; cross-check `systemctl status oceankind`) |

Process-level memory and CPU (for D-011 and the window-overlap decision):

```bash
ps -o rss=,pcpu= -p $(pgrep -f marfutura_iot_audio)   # KB resident, %CPU
uptime                                                 # load averages
```

Also run `validate_contract.py` again — a tree with a full day of events is a
much stronger conformance sample than the 60-second one.

**CPU number for client dependency 13:** if `%CPU` is comfortably under ~40 %,
window overlap at hop 2.5 (×2 work) fits on the worst-case hardware; under
~20 %, hop 2.0 (×2.5) fits. Record the number in `docs/CLIENT-DEPENDENCIES.md`.

## 7. Record the results

Per protocol: check the two bench items off in `docs/PROGRESS.md` (device and
stack), note the measured numbers there, update F-05's entry in
`../../docs/FINDINGS.md` with the measured duty cycle, and add the CPU number
to client dependency 13. If anything fails the table above, that is a finding —
write it down before touching anything.

---

## Troubleshooting

- **`oceankind-bench.local` not found** — mDNS can be slow; try again in a
  minute, or find the IP in your router's client list. Remember: 2.4 GHz only.
- **Service refuses to start** — that is the R-8.1 fail-loud check doing its
  job; `journalctl -u oceankind -n 20` prints exactly which variable is
  missing. Fix `/etc/oceankind.env`, `sudo systemctl restart oceankind`.
- **pip is slow on scipy/numpy** — normal on first install (aarch64 wheels are
  ~40 MB); on a 32-bit image they come prebuilt from piwheels instead.
- **You rebooted mid-soak** — duty cycle and counters are per-session; the
  24 h clock restarts. (The output tree survives — it is on the SD, not tmpfs.)
