# OceanKind × Microsoft Project 15 — Integration Guide

## What is Project 15?

[Microsoft Project 15](https://github.com/microsoft/project15) is an open-source conservation IoT platform built on Azure.  
You deploy it once to your Azure subscription and it gives you:

| Component | What it does |
|-----------|--------------|
| **Azure IoT Hub** | Receives telemetry from every Pi unit in the field |
| **Azure Stream Analytics** | Runs SQL-like queries on the live data stream (filter by event type, aggregate, alert) |
| **Cosmos DB** | Stores every detection event as a JSON document, queryable by time, device, location |
| **Azure Functions** | Serverless triggers — e.g. send an SMS/email when a blast is confirmed |
| **Power BI dashboard** | Real-time map of detections, event counts, alert history |
| **Azure Maps** | Geographic visualisation of device locations and detection hotspots |

**For OceanKind**: deploy Project 15 once → plug each Pi unit in as a device → every blast detection appears on a live dashboard with location, time, confidence score, and a link to the saved audio clip. No custom server to write or maintain.

---

## Architecture: How OceanKind Fits In

```
[Aquarian H5 Hydrophones]
         │
         ▼
[Raspberry Pi 4 + HifiBerry ADC Pro]
         │
    STA/LTA detector runs locally (real-time, no internet required)
         │
    On detection:
         ▼
[alert.py — IoTHubDeviceClient]
         │  (HTTPS / AMQP, TLS encrypted)
         ▼
[Azure IoT Hub]  ←── OceanKind device registered here
         │
         ├──→ [Stream Analytics]  ──→  filter "blast" events
         │           │
         │           ▼
         │    [Azure Function]  ──→  SMS / email alert
         │
         ├──→ [Cosmos DB]  ──→  long-term event storage
         │
         └──→ [Power BI / Project 15 Web App]
                      │
                      ▼
              Live map of detections
```

The Pi **does not need a persistent internet connection** to detect blasts — everything runs locally.  
It only needs internet when sending the alert payload (~200 bytes per event).  
If the connection drops, the Azure SDK retries automatically with exponential back-off.

---

## Step 1 — Deploy Project 15 to Azure (one-time)

1. Go to [github.com/microsoft/project15](https://github.com/microsoft/project15)
2. Click **Deploy to Azure** button in the README
3. Fill in: subscription, resource group, region, admin password
4. Deployment takes ~10 minutes and creates all Azure resources automatically

> **Cost**: Project 15 runs on free / low tiers. For a small number of devices  
> (< 10 units, < 8,000 messages/day) the IoT Hub **free tier** is sufficient.  
> Microsoft gives nonprofits **$2,000/year in Azure credits** via  
> [Microsoft for Nonprofits](https://www.microsoft.com/en-us/nonprofits/azure) — OceanKind likely qualifies.

---

## Step 2 — Register OceanKind Devices in IoT Hub

After Project 15 deploys:

1. Azure Portal → your IoT Hub → **Devices** → **+ Add Device**
2. Device ID: use your `OCEANKIND_DEVICE_ID` value (e.g. `unit_01`, `unit_paracas_01`)
3. Authentication: **Symmetric key** (auto-generated)
4. Click Save → open the device → copy **Primary Connection String**

The connection string looks like:
```
HostName=oceankind-hub.azure-devices.net;DeviceId=unit_01;SharedAccessKey=abc123...==
```

---

## Step 3 — Configure the Pi Unit

Edit `/etc/oceankind.env` on the Pi:

```bash
OCEANKIND_DEVICE_ID=unit_01
OCEANKIND_LAT=-13.8411       # Paracas coordinates
OCEANKIND_LON=-76.2506
OCEANKIND_ALERT_METHOD=iothub
OCEANKIND_IOTHUB_CONNECTION_STRING=HostName=oceankind-hub.azure-devices.net;DeviceId=unit_01;SharedAccessKey=YOUR_KEY
```

Then restart the service:
```bash
sudo systemctl restart oceankind
sudo journalctl -u oceankind -f   # watch the log
```

You should see: `Azure IoT Hub connected | device=unit_01`

---

## Step 4 — Verify in the Portal

1. Azure Portal → IoT Hub → **Overview** → watch "Messages received" counter
2. Use **IoT Hub Explorer** or Azure CLI to monitor live:
   ```bash
   az iot hub monitor-events --hub-name oceankind-hub --output table
   ```
3. Open the Project 15 web app URL (from deployment output) — your device appears on the map

---

## Telemetry Payload

Each detection sends one JSON message (~250 bytes):

```json
{
  "event_id": "f3a2c1d0-...",
  "device_id": "unit_01",
  "timestamp_utc": "2026-04-27T14:32:01.123456+00:00",
  "channel": 0,
  "event_type": "blast",
  "duration_s": 0.18,
  "peak_amplitude": 0.847,
  "sta_lta_ratio": 8.3,
  "frequency": {
    "peak_freq_hz": 420.0,
    "broadband_score": 0.81,
    "spectral_flatness": 0.72,
    "is_broadband": true
  },
  "location": {
    "latitude": -13.8411,
    "longitude": -76.2506
  }
}
```

IoT Hub **message routing** can filter on `event_type` without parsing the body —  
e.g. only forward `"blast"` events to the Azure Function that sends SMS alerts.

---

## Phase 2 — Edge Impulse ML (Optional Upgrade)

The current detector uses **STA/LTA + frequency analysis** — a rule-based system.  
It works well but requires manual threshold tuning for each deployment environment.

**Edge Impulse** lets you train a neural network on real labeled audio from your hydrophones,  
then run it directly on the Pi alongside (or replacing) STA/LTA.

### Why it matters
- Learns the acoustic signature of *your specific environment* (Paracas, boat noise, wave patterns)
- Can distinguish bomb fishing from speargun, boat engine, or thunderclap with higher accuracy
- Runs at ~5ms inference on Pi 4 — negligible extra CPU

### How it would work

```
Current:   audio block → STA/LTA ratio > 4.0 → blast?
Phase 2:   audio block → STA/LTA (pre-filter) → MFCC features → Edge Impulse model → blast?
```

1. **Collect data**: run the current system, save audio clips of confirmed blasts and false alarms
2. **Label**: upload clips to [edgeimpulse.com](https://edgeimpulse.com) and label them  
   ("blast" / "background" / "boat" / etc.)
3. **Train**: Edge Impulse automatically builds an MFCC → neural network pipeline
4. **Deploy**: download the Python/C++ library and add ~20 lines to `detector.py`
5. **Result**: lower false positive rate, higher confidence scores

> Edge Impulse is **free for conservation/environmental projects**.  
> Contact them — they have a nonprofit program.

---

## Summary: What Changes, What Doesn't

| | Before | After |
|--|--------|-------|
| Detection algorithm | STA/LTA | STA/LTA (unchanged) |
| Alert transport | MQTT (local broker) | Azure IoT Hub |
| Server infrastructure | None / custom | Project 15 on Azure |
| Dashboard | None | Project 15 web app |
| Data storage | Pi local logs only | Cosmos DB (queryable) |
| Code change | — | ~5 lines in `/etc/oceankind.env` |

The Pi code itself barely changes. All the power of Project 15 is in the cloud side —  
and the OceanKind codebase already sends exactly the right JSON payload.
