# Bench runbook — Pi Zero soak

From blank SD card to the Phase 1+2 acceptance numbers, **writing to real Azure
Blob Storage**. No hydrophone and no Twilio: the unit runs the synthetic source,
writes the **v2 tree** (the only format this device speaks — D-016) straight to
a private container, and reports its own duty cycle and memory in `status.json`.

Self-contained. Everything you need is in this file, except the Azure resources
themselves — see the gate immediately below.

> ## ⛔ STOP — create the Azure resources first
>
> This runbook writes to Azure from the first smoke check. **You cannot start
> until these exist:**
>
> | Thing | Value used throughout this file |
> |---|---|
> | Resource group | `rg-oceankind-dev` |
> | Storage account | `stoceankinddev01` (GPv2, Standard_LRS, Hot, public access **off**) |
> | Container | `alerts` (private) |
> | Versioning + soft delete | enabled |
> | Connection string | copied, ready to paste into `/etc/oceankind.env` |
>
> **Full step-by-step to create them:
> [`../../docs/research/azure-storage-deployment.md`](../../docs/research/azure-storage-deployment.md)**
> — steps 1 to 4. It takes about 15 minutes and needs `brew install azure-cli`.
>
> Then read **§0.1 Write volume and cost** below *before* you start the soak.
> A worst-case 24 h run writes far more than the free monthly allowance, and
> what happens when you exceed it depends on whether your trial credit is still
> live. That is a decision to make deliberately, not to discover.
>
> If Azure is not ready and you want to make progress anyway, §4 option (b)
> runs the identical soak against a local directory instead. The measurements
> are just as valid; only the transport is untested.

> **Actual bench hardware (confirmed 2026-08-13): Raspberry Pi Zero W v1.1** —
> single-core ARMv6 @ 1 GHz, 512 MB. The docs elsewhere assumed a Zero 2W
> (quad-core ARMv8). The v1.1 is a *harsher* test than planned: it MUST run the
> **32-bit** OS (64-bit does not boot on ARMv6), installs are slow, and the
> single core may not classify 5 s windows in real time. That last outcome is
> **data, not failure**: the production nodes are Pi 4s (4 cores, each several
> times faster). The bounded queues and counters are built to make any
> shortfall visible and honest (`clips_dropped`, `capture_overflows`).

What this run proves:

- **Clean provision** (Phase 1): `setup.sh` takes a blank image to a working unit.
- **Duty cycle over 24 h** (R-1.1/R-1.2): measured by the device with the
  detector firing constantly. ≥99 % on this chip closes the Phase 2 gate
  outright; a lower number with climbing drop counters is the honest floor of
  the weakest hardware and the gate measurement moves to a Pi-4-class unit.
- **Resident memory vs 512 MB** (R-7.5, feeds D-011).
- **CPU headroom** for the window-overlap decision (client dependency 13).

**Do NOT run `protect_sd.sh` for this soak.** The overlay would put the output
tree in RAM and lose it on any reboot. SD protection gets proven separately in
Phase 5.

---

## 0. Before you start

**Physical checklist**

- Raspberry Pi Zero W v1.1, power supply, and a way to reach its SD slot
- SD card, 16 GB minimum (32 GB fine)
- A **2.4 GHz** Wi-Fi network. The Zero W cannot see 5 GHz at all, and a
  5 GHz-only SSID fails silently — the unit simply never appears
- Internet from that network that stays up for 24 hours. The soak now uploads
  continuously; a dropped link means events divert to the local spool
  (`EVENT_SPOOL_MAX`, 500) and the oldest are discarded once it fills

**Azure checklist** — all five rows of the gate at the top of this file must be
done. On the Mac you also need `azure-cli` (`brew install azure-cli`, `az login`)
for the verification steps in §5 and §6.

**Timing.** About 20 minutes of your attention, spread across a 30–60 minute
provisioning wait, then 24 hours of walking away. Create the Azure resources
first — the run cannot start without them.

## 0.1 Write volume and cost — read before starting

`synthetic:tone` fires the detector on **every** window. That is deliberate: it
is the worst case, and worst case is what the duty-cycle measurement is for.
It also means roughly **17,000 event blobs per day**, plus clips. Cooldown does
not reduce this — suppressed detections are still recorded (D-008, F-03);
cooldown throttles notifications, never the record.

The free monthly allowance is about **10,000 write operations**. A worst-case
soak exceeds a month of it in well under a day. What happens next depends
entirely on your account state:

| Account state | What happens |
|---|---|
| Trial credit still live (first 30 days, USD 200) | Writes bill against credit. A soak costs a small fraction of it. **No interruption** — this is the good case |
| Credit expired, spending limit **on** | Subscription is **disabled** on exceeding free limits. Uploads start failing. Today that is a `log.warning` in `storage.py:70-72` plus the spool — not an alarm |
| Credit expired, spending limit **off** | Writes bill to your card. Small, but confirm the figure yourself on the [pricing calculator](https://azure.microsoft.com/pricing/calculator/) — the pricing page renders its numbers client-side and cannot be quoted reliably second-hand |

**Pick one, deliberately:**

- **Run inside the trial credit window.** Simplest, and the reason to do this
  soak now rather than in a month.
- **Reduce the load.** Set `OCEANKIND_AUDIO_SOURCE=synthetic:impulse` instead of
  `tone`. Far fewer detections, so far fewer writes. You keep the transport
  proof and the memory numbers, but you **lose the worst-case duty cycle** —
  which is the main thing this soak exists to measure. Say so in the results.
- **Split it.** 24 h against `OCEANKIND_OUTPUT_DIR` for the measurements (§4
  option b), then a short tone run against Azure for the transport proof. Costs
  nothing, proves everything, takes one extra hour.

Whichever you choose, **write it down in the results** — a duty cycle from an
impulse run is not comparable to one from a tone run.

**Pre-flight: blank the Twilio secrets before you copy anything.**

Step 2 rsyncs `raspberry-pi/oceankind.env` onto the Pi, and step 3 installs it
to `/etc/oceankind.env`. That file carries the **unrotated** Twilio token (F-04),
which already exists in `legacy/build-artifacts/` twice and on a git remote.
Copying it puts the same live credential on a third artefact — an SD card that
gets handled casually.

The bench sets `OCEANKIND_ALLOW_NO_TWILIO=1` anyway (§4), so it never needs
them. On the **Mac**, before step 2, blank these two lines in
`raspberry-pi/oceankind.env`:

```
OCEANKIND_TWILIO_SID=
OCEANKIND_TWILIO_TOKEN=
```

Put them back when the token is rotated. Setting `TWILIO_TO` to your own number
protects the production recipients but does **not** remove the token from the
card — those are different risks and only blanking solves the second.

**What else is unset in that file, and whether it matters**

| Variable | State in the repo copy | For the bench |
|---|---|---|
| `OCEANKIND_SITE` | empty | Set to `banco` in §4. Empty is fine until storage is on — `config.py:385` only demands it then |
| `OCEANKIND_OUTPUT_DIR` | commented out | **Must be set** in §4, or the device writes nothing at all |
| `OCEANKIND_AUDIO_SOURCE` | commented out | Defaults to `device`. No hydrophone here, so §4's `synthetic:tone` is required |
| `OCEANKIND_CONFIG_HMAC_KEY` | empty | **Correct as-is.** No key means remote config is refused entirely (F-10, by design). `main.py:210-215` catches it, records a health event, and does not crash |
| `OCEANKIND_STORAGE_CONNECTION_STRING` | empty | Leave empty for the 24 h soak. §8 fills it for the short second run |

Set all of these on the Pi in `/etc/oceankind.env` (§4), not in the repo copy —
the repo copy stays production-shaped.

---

## 1. Flash the SD card (on the Mac, ~10 min)

1. Install **Raspberry Pi Imager**: `brew install --cask raspberry-pi-imager`
   (or download from raspberrypi.com/software). Card: 16 GB minimum, 32 GB fine.
2. In Imager:
   - **Device**: Raspberry Pi Zero W (or "No filtering")
   - **OS**: Raspberry Pi OS **Lite (32-bit)** — no desktop. **Not 64-bit**:
     the Zero W v1.1 is ARMv6 and a 64-bit image will not boot at all.
   - **Storage**: the SD card
3. When asked to apply **OS customisation**, click **Edit settings** and set:
   - Hostname: `oceankind-bench`
   - Username: **`marfutura`** + a password — this matters: the provisioning
     scripts resolve the service user from who runs them, and `marfutura` is
     the documented default (F-17)
   - Wi-Fi: your SSID + password. **The Zero W only sees 2.4 GHz networks** —
     a 5 GHz-only SSID will silently never connect
   - Wireless LAN country, locale/timezone
   - Services tab: **enable SSH** (password auth is fine for the bench)
4. Write, wait, eject. Insert into the Pi, power it, give it ~2 min to first-boot.

## 2. Reach it and copy the code (~5 min)

Do the pre-flight in §0 first — the Twilio blanking happens before this command,
not after.

From the Mac (repo root `_Rpi-Detector/`):

```bash
ssh marfutura@oceankind-bench.local        # accept fingerprint, then exit
rsync -av --exclude .git --exclude legacy --exclude '__pycache__' \
    ./ marfutura@oceankind-bench.local:~/Rpi-Detector/
```

The rsync includes `raspberry-pi/oceankind.env` (the gitignored live config) —
that is fine for provisioning, and the bench profile in §4 overrides the parts
that must not fire real notifications.

## 3. Provision (30–60 min on the single-core Zero W — start it and walk away)

```bash
ssh marfutura@oceankind-bench.local
sudo bash ~/Rpi-Detector/raspberry-pi/scripts/setup.sh
```

The script installs system packages (incl. `libportaudio2`), the Python
dependency set, copies the `oceankind/` package, writes the systemd unit, and
installs `raspberry-pi/oceankind.env` to `/etc/oceankind.env`.

On the 32-bit image, numpy/scipy come **prebuilt from piwheels** (Raspberry Pi
OS ships pip preconfigured for it). If pip ever says "Building wheel for
scipy/numpy" and starts compiling, **stop (Ctrl-C) and report it** — a source
build takes many hours on this chip and means the wheel lookup failed; do not
wait it out.

## 4. Bench profile (~2 min)

`sudo nano /etc/oceankind.env`. Ready to paste — these are the only lines that
matter for the bench; leave everything else in the file alone:

```bash
# ── Bench identity ────────────────────────────────────────────────────────
OCEANKIND_DEVICE_ID=Rpi_bench
OCEANKIND_SITE=banco
OCEANKIND_SENSOR_LOCATION=Banco ZeroW
OCEANKIND_SENSOR_LAT=-33.0
OCEANKIND_SENSOR_LON=-71.0

# ── Notifications OFF ─────────────────────────────────────────────────────
# CRITICAL: the shipped env carries PRODUCTION recipients. A 24 h synthetic
# soak fires on every window and must not message them. Either allow-no-twilio
# (preferred — the bench does not need Twilio at all) or point TWILIO_TO at
# your OWN number. The SID/TOKEN should already be blank from §0.
OCEANKIND_ALLOW_NO_TWILIO=1
OCEANKIND_TWILIO_SID=
OCEANKIND_TWILIO_TOKEN=

# ── Where the v2 tree goes — pick ONE ─────────────────────────────────────
# (a) AZURE — the default for this runbook. Paste the connection string from
#     azure-storage-deployment.md §5. Leave OUTPUT_DIR unset/commented: if BOTH
#     are set, OUTPUT_DIR wins and nothing reaches Azure (storage.py:60).
OCEANKIND_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=stoceankinddev01;...
OCEANKIND_STORAGE_CONTAINER=alerts
#OCEANKIND_OUTPUT_DIR=

# (b) LOCAL fallback — identical tree on the SD card, no network, no cost.
#     Use if Azure is not ready, or for the split approach in §0.1.
#     Comment out the two Azure lines above and uncomment this one:
#OCEANKIND_OUTPUT_DIR=/home/marfutura/oceankind/out

# ── Load ──────────────────────────────────────────────────────────────────
# Worst case on purpose: the tone pattern makes the detector fire on EVERY window.
# ~17k event blobs/day — see §0.1 before running this against Azure.
OCEANKIND_AUDIO_SOURCE=synthetic:tone
# 1 notified event per hour; the rest recorded as suppressed. Cooldown throttles
# NOTIFICATIONS ONLY — it does not reduce the blob count (D-008, F-03).
OCEANKIND_ALERT_COOLDOWN_S=3600
```

**The one mistake that silently wastes the run:** leaving `OCEANKIND_OUTPUT_DIR`
set *and* filling in the connection string. `storage.py:60` checks `OUTPUT_DIR`
first, so everything goes to the SD card and Azure stays empty — with no error,
because nothing failed. Comment it out.

## 5. Smoke-check, then start the soak (~10 min)

```bash
sudo systemctl restart oceankind
journalctl -u oceankind -f     # watch for ~2 min
```

You should see: the startup banner, `Fuente sintética iniciada`,
`Captura continua iniciada`… then every ~5 s a level bar with
`psd=MOTOR(1.00) *** ALERTA ***`, one WhatsApp-suppressed log line per window,
and `→ evento registrado … [suprimido]`. After ~60 s, `→ status.json …`.

There must be **no** `Error subiendo …` lines. If uploads are failing you will
see those plus `evento encolado localmente` — stop and fix it before soaking,
because 24 hours of spooling proves nothing about transport.

Sanity-check what actually reached Azure. Run this **on the Mac** (the Pi has no
`az` CLI), from the repo root:

```bash
KEY=$(az storage account keys list -g rg-oceankind-dev -n stoceankinddev01 --query "[0].value" -o tsv)

rm -rf ./out && mkdir -p ./out
az storage blob download-batch -s alerts -d ./out \
  --account-name stoceankinddev01 --account-key "$KEY"

python3 tools/validate_contract.py ./out                                # CONFORMANT
python3 -m json.tool ./out/sites/banco/status.json | grep -A14 '"health"'
```

`CONFORMANT` here is stronger than the local-directory version: it proves real
serialisation, real blob paths and real overwrite semantics, not just the shape
of the tree (R-5.6).

*(Running option (b) instead? Both commands work directly on the Pi against
`~/oceankind/out` — no `az`, no download.)*

If that looks right: walk away. Leave it running 24 hours.

## 6. Read the verdict (after 24 h)

Pull the day's tree down and read it, **on the Mac**:

```bash
KEY=$(az storage account keys list -g rg-oceankind-dev -n stoceankinddev01 --query "[0].value" -o tsv)

rm -rf ./out && mkdir -p ./out
az storage blob download-batch -s alerts -d ./out \
  --account-name stoceankinddev01 --account-key "$KEY"

python3 -m json.tool ./out/sites/banco/status.json
python3 tools/validate_contract.py ./out       # full-day sample, much stronger
```

*(Option (b): `ssh marfutura@oceankind-bench.local` then
`python3 -m json.tool ~/oceankind/out/sites/banco/status.json`.)*

| Field | Reading it on the Zero W v1.1 |
|---|---|
| `health.duty_cycle_pct` | **≥ 99.0 closes the Phase 2 gate outright.** Lower = the single ARMv6 core cannot classify in real time; record the number — it is the fleet's honest floor, and the gate measurement moves to Pi-4-class hardware |
| `health.deaf_seconds_total` | consistent with the duty % (out of 86 400 s) |
| `health.capture_overflows` | 0 ideally; steadily climbing = classifier not keeping up (see above — data, not failure) |
| `health.clips_dropped`, `events_dropped` | `events_dropped` must be **0** regardless (events are sacred); `clips_dropped` may climb only via archive/queue policy |
| `system.ram_used_mb` | whole-system; headroom vs 512 total (R-7.5, D-011) |
| `uptime_seconds` | ~86 400 (no crashes/restarts; cross-check `systemctl status oceankind`) |

Process-level memory and CPU (for D-011 and the window-overlap decision):

```bash
ps -o rss=,pcpu= -p $(pgrep -f marfutura_iot_audio)   # KB resident, %CPU
uptime                                                 # load averages
```

**Upload health over the 24 h** — new to the Azure run, check it explicitly:

```bash
python3 -m json.tool ./out/sites/banco/status.json | grep -iE "spool|dropped|degraded"
journalctl -u oceankind --since "24 hours ago" | grep -c "Error subiendo"
```

A non-zero spool length or any `Error subiendo` count means transport was not
clean. Record the number — a soak that spooled for six hours is a different
result from one that uploaded continuously, even if the duty cycle matches.

**CPU number for client dependency 13:** on this single-core chip, `%CPU`
under ~40 % means window overlap at hop 2.5 (×2 work) would fit even here;
near 100 % means the Zero W v1.1 is saturated at hop 5 and the overlap decision
should be based on Pi-4 numbers instead (per-core several times faster, ×4
cores). Record whatever it is in `docs/CLIENT-DEPENDENCIES.md`.

## 7. Record the results

Per protocol: check the two bench items off in `docs/PROGRESS.md` (device and
stack), note the measured numbers there, update F-05's entry in
`../../docs/FINDINGS.md` with the measured duty cycle, and add the CPU number
to client dependency 13. If anything fails the table above, that is a finding —
write it down before touching anything.

---

## 8. After the run: credentials and cleanup

**Stop the service** once you have the numbers, so it stops consuming write
quota:

```bash
ssh marfutura@oceankind-bench.local 'sudo systemctl stop oceankind'
```

**Credential warning — the important one.** This runbook puts the **storage
account key** on the bench Pi. That key is full read, write, delete and list over
the entire storage account. It is acceptable here for exactly one reason: this is
a bench unit sitting on your desk that you can physically reach.

Per **D-017**, the account key never goes onto anything deployed. Deployed units
get a per-device, write-scoped, revocable credential — one Entra service
principal each, RBAC scoped to the container, narrowed by an ABAC path condition
on `sites/{site}/*`, never delete, never list, never read another site. Read
D-017 in full before provisioning any unit that leaves the desk.

Note also that D-017's mechanism is under review against `Dashboard-Detector`
R-6.3, which specifies devices uploading through the backend API *"so the device
stops needing storage credentials of its own"*. That question is open; it does
not affect this bench run either way.

**Clearing test data.** The soak leaves ~17,000 blobs under `sites/banco/`. They
count against the 5 GB capacity allowance, and soft delete keeps them for the
retention window after deletion:

```bash
az storage blob delete-batch -s alerts --pattern "sites/banco/*" \
  --account-name stoceankinddev01 --account-key "$KEY"
```

Leave `_sites.json` alone — the registry entry for `banco` is harmless and
proves `publish_site_registry()` worked.

---

## Troubleshooting

- **`oceankind-bench.local` not found** — mDNS can be slow; try again in a
  minute, or find the IP in your router's client list. Remember: 2.4 GHz only.
- **Service refuses to start** — that is the R-8.1 fail-loud check doing its
  job; `journalctl -u oceankind -n 20` prints exactly which variable is
  missing. Fix `/etc/oceankind.env`, `sudo systemctl restart oceankind`.
- **Starts, but writes nothing** — `OCEANKIND_OUTPUT_DIR` is unset and no
  storage connection string is present, so `STORAGE_ENABLED` is false and the
  device runs without emitting anything. §4 sets it.
- **Runs fine, Azure container stays empty** — `OCEANKIND_OUTPUT_DIR` is still
  set. `storage.py:60` checks it before Azure, so everything went to the SD card
  and nothing failed. Comment it out, restart. See the warning at the end of §4.
- **`Error subiendo …` on every event** — the upload path is failing and events
  are going to the spool. Check in this order: connection string pasted whole
  (they are long and easy to truncate); container name is `alerts` and it exists;
  the Pi has internet (`ping -c3 microsoft.com`); the account has not hit its
  quota and disabled the subscription (§0.1). The device does **not** alarm on
  this today — it is a `log.warning` plus spooling, which is why §5 tells you to
  check the log before walking away.
- **`AuthorizationFailure` / `AuthenticationFailed` in the log** — the key was
  rotated, or the connection string belongs to a different account. Re-run
  `az storage account show-connection-string` and paste it again.
- **`az` commands fail with `AuthorizationPermissionMismatch`** — data-plane
  operation with a control-plane role. Use `--account-key "$KEY"` as every
  command in this file does, not `--auth-mode login`.
- **Events stop partway through the soak** — check the spool
  (`health.event_spool_len` in `status.json`). If it is at `EVENT_SPOOL_MAX`
  (500) the oldest events were discarded and `events_dropped` is non-zero, which
  invalidates the run's "events are sacred" check. Most likely cause is the
  subscription disabling on quota (§0.1) or the Wi-Fi dropping.
- **`OCEANKIND_SITE` errors on start** — expected the moment storage is enabled;
  the v2 contract puts everything under `sites/{site}/`. Set it to `banco`.
- **No detections in the log** — `OCEANKIND_AUDIO_SOURCE` is still the default
  `device` and there is no hydrophone. Set `synthetic:tone`.
- **pip is slow on scipy/numpy** — normal on first install (aarch64 wheels are
  ~40 MB); on a 32-bit image they come prebuilt from piwheels instead. If it
  says "Building wheel", that is the failure case — Ctrl-C, see §3.
- **You rebooted mid-soak** — duty cycle and counters are per-session; the
  24 h clock restarts. (The output tree survives — it is on the SD, not tmpfs.)
- **Remote config never applies** — expected. `OCEANKIND_CONFIG_HMAC_KEY` is
  empty, and without it the device refuses all remote config (F-10). It is
  recorded in `health.degraded_reason`, not silently ignored.
