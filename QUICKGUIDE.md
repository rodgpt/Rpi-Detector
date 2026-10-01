# Quick guide — add a new unit

Blank Raspberry Pi → measuring and reporting to the dashboard. About 1 hour, mostly waiting on the install.

**You need:** a Pi (Zero 2 W / 3 / 4), power supply, 16 GB+ SD card, Wi-Fi or Ethernet with internet (Pi Zero: **2.4 GHz only**), an admin login to the dashboard, and the hydrophone/ADC board attached (skip for a test unit — see step 5).

---

## 1. Flash the SD card

In **Raspberry Pi Imager**:

- **OS:** Raspberry Pi OS **Lite**. 64-bit for Pi 3/4/Zero 2 W. **32-bit** for the original Zero W (ARMv6 won't boot 64-bit).
- **Edit settings:**
  - Hostname: e.g. `oceankind-zapallar`
  - Username: **`marfutura`** (required — the scripts assume it) + a password
  - Wi-Fi SSID/password + country
  - Services → **Enable SSH**

Write, insert, power on, wait ~2 min.

## 2. Register the unit on the dashboard

Log in as admin at **https://marfutura.buenalynch.com**.

1. **Admin → Sitios** — confirm the site exists (e.g. `zapallar`). Create it if not. Do this first; a device for an unknown site is rejected.
2. **Admin → Dispositivos → Crear:**
   - `device_id`: unique, 3–64 chars, letters/digits/`_`/`-` (e.g. `Rpi_zapallar`)
   - `site_id`: the site from above
3. Copy the **Clave** (key) immediately. It is shown **once** and cannot be recovered. If lost, delete the device and recreate it.

## 3. Get the code onto the Pi

SSH in (`ssh marfutura@<hostname>.local`, or use the IP from your router), then either:

**Clone** (Pi has internet):

```bash
git clone https://github.com/rodgpt/Rpi-Detector.git ~/Rpi-Detector
```

**or rsync** from your computer (repo root):

```bash
rsync -av --exclude .git --exclude legacy --exclude '__pycache__' \
    ./ marfutura@<hostname>.local:~/Rpi-Detector/
```

> If you rsync from the maintainers' checkout, `raspberry-pi/oceankind.env` comes with it and contains **live production Twilio credentials and recipients**. Delete it first (`rm raspberry-pi/oceankind.env`) so the installer writes a blank template instead. A fresh `git clone` doesn't include it.

## 4. Provision

```bash
sudo bash ~/Rpi-Detector/raspberry-pi/scripts/setup.sh
```

Installs packages, a Python venv at `~/oceankind/venv`, the systemd service, and a template `/etc/oceankind.env`. Takes 10–15 min on a Pi 4 and 30–60 min on a Zero W. If pip starts **"Building wheel for numpy/scipy"**, Ctrl-C and ask for help: the prebuilt wheel lookup failed.

## 5. Configure `/etc/oceankind.env`

```bash
sudo nano /etc/oceankind.env
```

Set (leave everything else alone):

```bash
# Identity — must match what you created in step 2
OCEANKIND_DEVICE_ID=Rpi_zapallar
OCEANKIND_SITE=zapallar
OCEANKIND_SENSOR_LOCATION=Zapallar
OCEANKIND_SENSOR_LAT=-32.55
OCEANKIND_SENSOR_LON=-71.46

# Dashboard
OCEANKIND_BACKEND_URL=https://marfutura.buenalynch.com   # base URL only, no /api/...
OCEANKIND_DEVICE_KEY=<key from step 2>

# Where the local record goes (required — without it nothing is written)
OCEANKIND_OUTPUT_DIR=/home/marfutura/oceankind/out

# WhatsApp/calls: leave off unless you have your own Twilio credentials
OCEANKIND_ALLOW_NO_TWILIO=1
OCEANKIND_TWILIO_SID=
OCEANKIND_TWILIO_TOKEN=
```

**No hydrophone yet?** Add a synthetic source so you can test the whole chain:

```bash
OCEANKIND_AUDIO_SOURCE=synthetic:sporadic          # realistic: a detection every ~12 min on average
OCEANKIND_SYNTHETIC_SPORADIC_MEAN_S=720
```

For a real unit, leave `OCEANKIND_AUDIO_SOURCE` unset (default `device`; the sound card is found by name, no index needed). **Remove any `synthetic:*` line before deployment.**

The file is root-only (mode 600). Any edit needs `sudo`.

## 6. Start and verify

```bash
sudo systemctl restart oceankind
journalctl -u oceankind -f          # Ctrl-C to leave
```

Look for `Captura continua iniciada`, then `status.json` after ~60 s. A missing variable makes the service **refuse to start** and print which one: fix the env and restart.

Check the dashboard connection:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -X POST https://marfutura.buenalynch.com/api/devices/events
# 422 = reachable and healthy. HTML or a timeout = network/proxy problem.
```

Send a test event (works with any audio source):

```bash
sudo ~/oceankind/venv/bin/python ~/Rpi-Detector/raspberry-pi/tools/inject_event.py --count 1
journalctl -u oceankind -n 30 | grep -i push
```

## 7. Confirm on the dashboard

- **Admin → Dispositivos:** the unit has a fresh **last_seen**.
- The test event appears under its site.
- Clip playback returns 404 when audio storage isn't configured. That is expected, not a fault.

On the Pi, health lives in `status.json`:

```bash
python3 -m json.tool ~/oceankind/out/sites/<site>/status.json | grep -A14 '"health"'
```

Healthy = `duty_cycle_pct` ≥ 99, `capture_overflows` ~0, `events_dropped` 0, no `degraded_reason`.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `<hostname>.local` not found | Wait a minute or use the IP from the router. Zero W: 2.4 GHz only |
| Service won't start | `journalctl -u oceankind -n 20` names the missing/invalid variable |
| Nothing written | `OCEANKIND_OUTPUT_DIR` not set |
| Push log: `401` | ID/key don't match an active device. Re-check the key or recreate the device |
| Push log: `403` | Read the logged response body. JSON `site mismatch` = the site differs from the registered one; `error code: 1010` = blocked upstream, report it |
| `backend inalcanzable` | No internet or backend down. Events spool locally (max 500) and retry |
| No detections | Still on `device` source with no hydrophone. Set a synthetic source (step 5) |
| Different USB/ALSA hardware | Set `OCEANKIND_AUDIO_DEVICE_NAME` to a substring of the card name (`arecord -l`) |

**After any env change:** `sudo systemctl restart oceankind`.
**After a code update:** re-rsync/`git pull`, then re-run `setup.sh`. It won't overwrite `/etc/oceankind.env`.

Deeper detail (soak testing, event-rate tuning, Cloudflare quirks): `raspberry-pi/docs/BENCH.md`.
