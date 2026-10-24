# Quick guide — add a new unit

Blank Raspberry Pi → measuring and reporting to the dashboard. About 1 hour, mostly waiting on the install.

**You need:** a Pi (Zero 2 W / 3 / 4), power supply, 16 GB+ SD card, Wi-Fi or Ethernet with internet (Pi Zero: **2.4 GHz only**), an admin login to the dashboard, and a Raspberry Pi Codec Zero HAT with the hydrophone circuit wired to it (skip both for a test unit — see step 5). Codec Zero is detected automatically in step 6, no config needed — **but see step 1 for a kernel bug that currently breaks capture on the default OS image.**

---

## 1. Flash the SD card

In **Raspberry Pi Imager**:

- **OS:** Raspberry Pi OS **(Legacy) Lite** — Debian 12 (bookworm), kernel 6.12. **Not the default "Raspberry Pi OS Lite"** (Debian 13/trixie, kernel 6.18.x): that kernel has a confirmed, currently-unfixed regression that breaks Codec Zero audio *capture* specifically (card still enumerates fine, `arecord` fails with `Input/output error` — [raspberrypi/linux#7692](https://github.com/raspberrypi/linux/issues/7692)). In Imager: **Choose OS → Raspberry Pi OS (other) → Raspberry Pi OS (Legacy, 64-bit or 32-bit) Lite**. 64-bit for Pi 3/4/Zero 2 W, 32-bit for the original Zero W (ARMv6 won't boot 64-bit).
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

SSH in (`ssh marfutura@<hostname>.local`, or use the IP from your router):

```bash
sudo apt-get update && sudo apt-get install -y git   # NOT installed by default on Lite
git clone https://github.com/rodgpt/Rpi-Detector.git ~/Rpi-Detector
```

## 4. Provision

```bash
sudo bash ~/Rpi-Detector/raspberry-pi/scripts/setup.sh
```

Installs packages, a Python venv at `~/oceankind/venv`, the systemd service, and a template `/etc/oceankind.env`. Takes 10–15 min on a Pi 4 and 30–60 min on a Zero W. If pip starts **"Building wheel for numpy/scipy"**, Ctrl-C and ask for help: the prebuilt wheel lookup failed.

## 5. Configure `/etc/oceankind.env`

```bash
sudo nano /etc/oceankind.env
```

The fields below are listed **in the same top-to-bottom order they appear in the real file** `setup.sh` generates, so you can go through it once, start to finish, instead of hunting for each line:

```bash
# ── Identity — must match what you created in step 2 ──
OCEANKIND_DEVICE_ID=name
OCEANKIND_SITE=name
OCEANKIND_SENSOR_LOCATION=name
# LAT/LON: leave blank. Only used when Azure storage is configured, which
# this guide doesn't set up — they do nothing for this flow.
OCEANKIND_SENSOR_LAT=
OCEANKIND_SENSOR_LON=

# ── Secrets — leave SID/TOKEN blank unless you have your own Twilio credentials, in that case, comment line 68.
OCEANKIND_ALLOW_NO_TWILIO=1
OCEANKIND_TWILIO_SID=
OCEANKIND_TWILIO_TOKEN=


# ── Dashboard backend ──
OCEANKIND_BACKEND_URL=https://marfutura.buenalynch.com   # base URL only, no /api/...
OCEANKIND_DEVICE_KEY=<key from step 2>
```

**No hydrophone yet?** Further down the file, under `── Audio ──`, two lines are already there, commented out. Uncomment both to test the whole chain with a realistic synthetic detection every ~12 min:

```bash
OCEANKIND_AUDIO_SOURCE=synthetic:sporadic
OCEANKIND_SYNTHETIC_SPORADIC_MEAN_S=720
```

Leave them commented out for a real deployment.

The file is root-only (mode 600). Any edit needs `sudo`.

## 6. Start and verify

```bash
sudo systemctl restart oceankind
journalctl -u oceankind -f          # Ctrl-C to leave
```

Look for `Captura continua iniciada`. A missing variable makes the service **refuse to start** and print which one: fix the env and restart. **A successful heartbeat prints nothing** — silence after ~60 s is the good outcome, not a sign of nothing happening; a failed one logs `heartbeat: ...` once. The dashboard check below is what actually confirms it.

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
- **Estado del sensor** tab for its site: health, not just liveness — `duty_cycle_pct` ≥ 99, `capture_overflows` ~0, `events_dropped` 0, no `degraded_reason`.
- The test event appears under its site, under **Detecciones**.
- Clip playback returns 404 when audio storage isn't configured. That is expected, not a fault.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `<hostname>.local` not found | Wait a minute or use the IP from the router. Zero W: 2.4 GHz only |
| Service won't start | `journalctl -u oceankind -n 20` names the missing/invalid variable |
| No `last_seen` on the dashboard | Check `OCEANKIND_BACKEND_URL`/`OCEANKIND_DEVICE_KEY` are set and the device isn't `401`/`403` (rows below) |
| Push log: `401` | ID/key don't match an active device. Re-check the key or recreate the device |
| Push log: `403` | Read the logged response body. JSON `site mismatch` = the site differs from the registered one; `error code: 1010` = blocked upstream, report it |
| `backend inalcanzable` | No internet or backend down. Events spool locally (max 500) and retry |
| No detections | Still on `device` source with no hydrophone. Set a synthetic source (step 5) |
| Different USB/ALSA hardware | Set `OCEANKIND_AUDIO_DEVICE_NAME` to a substring of the card name (`arecord -l`) |
| `arecord` fails with `Input/output error`, card shows fine in `arecord -l` | Check `uname -r` — `6.18.39`/`6.18.50` means the trixie kernel bug (step 1). Re-flash with Raspberry Pi OS (Legacy) Lite |

**After any env change:** `sudo systemctl restart oceankind`.
**After a code update:** `git pull` in `~/Rpi-Detector`, then re-run `setup.sh`. It won't overwrite `/etc/oceankind.env`.

Deeper detail (soak testing, event-rate tuning, Cloudflare quirks): `raspberry-pi/docs/BENCH.md`.
