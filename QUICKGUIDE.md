# Quick guide — add a new unit

Blank Raspberry Pi → measuring and reporting to the dashboard. About 1 hour, mostly waiting on the install.

**You need:** a Pi (Zero 2 W / 3 / 4), power supply, 16 GB+ SD card, Wi-Fi or Ethernet with internet (Pi Zero: **2.4 GHz only**), an admin login to the dashboard (which is also where alerts are configured — the unit itself holds no Twilio anything, D-020), and a Raspberry Pi Codec Zero HAT with the hydrophone circuit wired to it (skip both for a test unit — see step 5). Codec Zero is detected automatically in step 6, no config needed — confirmed working end to end, including a cold reboot, on `6.18.50+rpt-rpi-v8` (current default Raspberry Pi OS Lite) as of 2026-10-24.

---

## 1. Flash the SD card

In **Raspberry Pi Imager**:

- **OS:** Raspberry Pi OS **Lite** (the default option). 64-bit for Pi 3/4/Zero 2 W, 32-bit for the original Zero W (ARMv6 won't boot 64-bit).
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
3. Copy the **Secreto** immediately. It is shown **once** and cannot be recovered. If lost, delete the device and recreate it. It is the only credential the unit will ever hold: from it the unit derives its login key and the key that verifies its configuration (D-020).
4. Optional: under **Versiones** on the same page, set the *predeterminada* release (a git tag such as `v2.2.0`). A device created while one is set is assigned it at birth, so it updates itself to that version on first contact (D-021).

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

Installs packages, a Python venv at `~/oceankind/venv`, the systemd service, and a template `/etc/oceankind.env`. Takes 10–15 min on a Pi 4 and 30–60 min on a Zero W.

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

# ── Dashboard backend ──
OCEANKIND_BACKEND_URL=https://marfutura.buenalynch.com   # base URL only, no /api/...
OCEANKIND_DEVICE_SECRET=<secret from step 2>
```

That is every secret there is. No Twilio, no phone numbers, no bench flag: alerts to humans leave from the backend, from what this unit sends it (D-020).

**Remote tuning and updates need nothing more.** The unit verifies configuration documents with a key derived from that same secret, so **Admin → Dispositivos → Configurar** works as soon as it has connected once; push-only units fetch the signed document from `GET /api/devices/config` every 5 minutes. The same document carries the assigned release (step 7 below).

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
- **Estado del sensor** tab for its site: health, not just liveness — `capture_ok` true, `duty_cycle_pct` ≥ 99, `last_clip_age_s` under 10, `capture_overflows` ~0, `events_dropped` 0, no `degraded_reason`.
- The test event appears under its site, under **Detecciones**. If the backend has Twilio configured, the WhatsApp for it arrives from there.
- Clip playback returns 404 when audio storage isn't configured. That is expected, not a fault.
- **Versión** column in Admin → Dispositivos: what the unit runs. A freshly provisioned unit shows *sin registro* until its first OTA; once a release is assigned (next step) it shows `installed → assigned (pendiente)`, then the installed tag alone.

## 8. Updates: assign a release from the dashboard

Units never pull `main`. A version is published by creating a git tag on the repository (`git tag v2.2.0 && git push --tags`), and each unit takes it only when assigned (D-021):

1. **Admin → Dispositivos → Actualizar** on the unit → pick or type the tag → **Asignar**. Within 5 minutes the unit downloads that exact tag, installs it, restarts, watches itself for a minute and rolls back if it does not stay up. The column shows the result; red *falló … revertida* means the tag broke this unit and will not be retried on its own.
2. Stage it: bench first, then one site, then the rest. **Retener** freezes a unit so nothing moves it, whatever is assigned.
3. **Versiones → Predeterminada** is only for units created afterwards.

A daily timer on the unit repeats the convergence in case the service was down when the assignment changed. Nothing else updates a unit — not `git pull`, not re-running `setup.sh` (that is for a fresh provision or a changed env file).

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `<hostname>.local` not found | Wait a minute or use the IP from the router. Zero W: 2.4 GHz only |
| Service won't start | `journalctl -u oceankind -n 20` names the missing/invalid variable |
| Won't start, journal says `OCEANKIND_DEVICE_KEY es la variable anterior a D-020` | Unit provisioned before 2026-10-24. Delete the device in Admin → Dispositivos, create it again, put the new secret in `/etc/oceankind.env` as `OCEANKIND_DEVICE_SECRET` and remove the `OCEANKIND_DEVICE_KEY` line (Twilio/BENCH lines can go too). The panel marks such units *re-provisionar* |
| `degraded_reason` says `captura detenida` | No audio window for 60 s: card unplugged, mixer muted (F-28), or stream died. `arecord -l`, `amixer -c 0 contents`, then restart |
| No `last_seen` on the dashboard | Check `OCEANKIND_BACKEND_URL`/`OCEANKIND_DEVICE_SECRET` are set and the device isn't `401`/`403` (rows below) |
| Push log: `401` | ID/secret don't match an active device. Re-check the secret or recreate the device |
| Versión column red: `falló vX, revertida` | That tag broke this unit; it rolled back and will not retry it. Read `/tmp/oceankind/logs/update.log` on the unit, publish a fixed tag, assign that |
| Assigned a release, nothing happens after 10 min | Unit is *retenida* (Liberar), or the service is down (the daily timer will still converge), or the tag does not exist on GitHub (`update.log` says `no existe`) |
| Push log: `403` | Read the logged response body. JSON `site mismatch` = the site differs from the registered one; `error code: 1010` = blocked upstream, report it |
| `backend inalcanzable` | No internet or backend down. Events spool locally (max 500) and retry |
| No detections | Still on `device` source with no hydrophone. Set a synthetic source (step 5) |
| Different USB/ALSA hardware | Set `OCEANKIND_AUDIO_DEVICE_NAME` to a substring of the card name (`arecord -l`) |
| `arecord` fails with `Input/output error`, card shows fine in `arecord -l` | Not a kernel issue (confirmed F-28) — check `amixer -c 0 contents` for `ADC Switch`/`Aux Switch` stuck `off`. `setup.sh`'s `ExecStartPre` should set these on every start; re-run it if they're off |
| Python capture crashes with `PortAudioError: ... Wait timed out [PaErrorCode -9987]` | F-37 — fixed in `capture.py` (blocking read, not a PortAudio callback thread). The unit is running an old copy: assign it a current release (step 8) |

**After any env change:** `sudo systemctl restart oceankind`.
**After a code update:** assign the new release from the dashboard (step 8). Re-running `setup.sh` is for a fresh provision; it won't overwrite `/etc/oceankind.env` and it does not update the code of a provisioned unit any more reliably than the OTA does.

Deeper detail (soak testing, event-rate tuning, Cloudflare quirks): `raspberry-pi/docs/BENCH.md`.
