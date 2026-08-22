# Data contract v2

**Status: NORMATIVE. v2 is the only contract, and it is the build target for both codebases.**

The device emits it, verified end to end by `raspberry-pi/tools/v2_conformance_test.py`, which drives the production emit code and passes `tools/validate_contract.py`. The dashboard backend reads it and nothing else.

**There is no v1 anywhere in the build.** The prototype units' old blob is frozen, in a different container, and is not read by anything we ship. No dual-write, no migration, no compatibility layer, no normalization on the way in. If code in either repository transforms an older shape, that code is out of date, not this document.

Derived by walking the five dashboard tabs, listing every field each one consumes, and working backwards. Client has confirmed the blob shape is ours to define.

Last updated 2026-08-22.

**Implementation notes, normative for consumers:**

- Event blob filenames are `{YYYY-MM-DDTHH-MM-SS}_{uuid4}.json` (capture time, dashes); clips are `{uuid4}.wav`. Both under date partitions derived from `captured_utc`.
- The `health` block carries fields beyond the schema below: `deaf_seconds_total`, `suppressed_count`, `events_dropped`, `wa_pending`, `archive_queue`, `capture_overflows` (PortAudio overflows plus block-queue drops). Additive, same fail-loud spirit. A consumer must tolerate more health fields than it knows.
- `score` for an RMS-decided event is the normalised RMS, already 0..1. The PSD detector's score is its tonal fraction. `detector_meta.decided_by` records `psd_tonal` | `rms` | `rms_fallback`.
- Suppressed events carry their would-be `clip.path` with `uploaded: false`. The audio is not kept (D-008).
- `status.json → audio.device` describes the *selection rule*, `by-name:<hints>` or `synthetic:<pattern>`, never an ALSA index. Hardcoded indexes were the F-15 defect.
- The device merges its own entry into `_sites.json` at startup. A rare read-modify-write, tolerable until the backend owns the registry.
- If an event blob upload fails the event is spooled locally, bounded at 500, and retried each heartbeat. Spool overflow discards oldest and counts them in `health.events_dropped`. No event is lost silently.
- Device configuration arrives as a signed blob. Full specification in **Device configuration** below, which is the only description of it in this document.

---

## Who reads this

Four hops, and this document governs the first two:

```
device  ->  blob storage  ->  backend API  ->  browser
        \____ this contract ____/   \__ API-CONTRACT.md __/
```

Until the backend existed, the browser read storage directly and every rule below was a rule about what the browser had to tolerate. That is no longer true. **The backend is the only consumer of these blobs.** It reads storage with a credential the browser never sees, validates what it finds against this contract, and serves a paginated HTTP API described in `API-CONTRACT.md`.

The schemas do not change. Who is obliged by them does. Three rules in particular moved:

- **The null rule** (non-finite floats serialise as `null`) now binds the backend's parser. It must accept `null` in every numeric field and pass it through as `null`, never coerce it to zero. A zero on a chart is a reading; a null is an absence.
- **Omitted power-history buckets** are read by the backend and forwarded as gaps. The backend must not backfill, interpolate or densify. Gaps are how outages are detected (dashboard R-8.6).
- **An unknown `schema_version`** is now the backend's problem. It degrades visibly, serves what it understood, and reports the mismatch through the API so the browser can render it. It must not return an empty page and it must not 500.

Traffic runs the other way for exactly one blob: the device **reads** its configuration from storage, signed. See **Device configuration** below.

---

## Why v2 is shaped this way

Four defects in the prototype layout drove the design. They are recorded because they explain the schema, not because anything still emits them.

**No version field.** A schema change is undetectable by either side. Every blob in v2 carries `schema_version`.

**Asymmetric site paths.** The first site writes to the container root and the second is namespaced under `matanzas/`. A third inherits an inconsistent scheme. In v2 nothing lives at the root.

**A rewritten manifest.** Every alert downloads the whole file, inserts, and re-uploads. That races against the retry path (F-14) and forces the dashboard to fetch the full history every 30 seconds (F-18). Per-event blobs remove both by construction rather than patching them.

**Write-only and read-only fields.** The prototype device wrote fifteen fields nothing read, and the dashboard read three fields nothing wrote. v2 carries only fields with a producer and a consumer, listed below.

---

## Fields with no producer

Defined in the schemas below, consumed by the dashboard, and **not produced by anything we build**. They are in the contract so the shape is settled; they arrive as `null` or as empty stubs until the client supplies a producer. Full list in `CLIENT-DEPENDENCIES.md`.

| Field | Situation |
|---|---|
| `bearing_deg` | The detections map draws it. Nothing emits it. Allow `null`, never invent a producer |
| `acoustic_indicators.json` medians and quartiles | `raspberry-pi/tools/a5_indicators.py` emits raw per-clip `ndsi` and `click_rate_hz`. The aggregator between them exists in neither repository |
| `ocean_conditions.json` | A marine forecast pull. No component in either repository produces it |

The device writes conformant empty stubs for the last two at startup, and only when the blob verifiably does not exist, so the tree validates and the tabs render empty instead of broken. The real producers overwrite them, and must write to the per-site paths.

`ram_used_mb` and `ram_total_mb` were a v1 mismatch and are now produced. They are in `status.json` below.

---

## Path scheme

```
alerts/                                   (container, unchanged)
  _sites.json                             site registry, replaces the hardcoded table
  sites/
    {site_id}/                            zapallar | matanzas | …  nothing at the root
      status.json                         current device state, overwritten
      power_history.json                  bucketed telemetry, overwritten
      acoustic_indicators.json            soundscape rollup, overwritten
      ocean_conditions.json               marine forecast, overwritten, non-device producer
      events/
        {YYYY}/{MM}/{DD}/
          {ISO8601}_{event_id}.json       one blob per detection, append only
      clips/
        {YYYY}/{MM}/{DD}/
          {event_id}.wav
```

Date-partitioned event paths make "this site, last 24 hours" a prefix listing. No index, no query engine, no database (D-004). Clips sit under a parallel tree so a listing of events never drags audio into the response.

`{event_id}` is a UUID. It appears in both the event blob name and the clip name so the two are joinable without parsing timestamps.

**Index tags on event blobs**, for the filtering that paths cannot express:

| Tag | Example | Note |
|---|---|---|
| `site` | `zapallar` | |
| `event_type` | `vessel` | |
| `detector` | `psd_tonal` | |
| `score` | `0850` | Zero-padded to 4 digits. Comparison is lexicographic |
| `suppressed` | `0` \| `1` | |

Five of the ten permitted tags, leaving headroom.

---

## Common envelope

Every blob the device writes:

```jsonc
{
  "schema_version": 2,
  "site":           "zapallar",
  "device":         "Rpi_casa",
  "generated_utc":  "2026-08-08T14:32:01.123456+00:00",
  … payload …
}
```

A consumer seeing an unknown `schema_version` must warn visibly and render what it understands. It must not blank the page.

**Serialisation rule, carried over from v1 and still load-bearing.** Python emits `Infinity` and `NaN` for non-finite floats, which is not valid JSON, and one unguarded `JSON.parse` blanked the dashboard in production. Every non-finite float is written as `null`. Consequently **any numeric field in any schema below may be `null`.** Not zero, not absent. Null.

---

## `_sites.json`

Replaces the hardcoded `SITES` table in the dashboard, so adding a unit stops being a code change.

```jsonc
{
  "schema_version": 2,
  "generated_utc": "…",
  "sites": [
    { "id": "zapallar", "name": "Zapallar", "lat": -32.552665, "lon": -71.465068,
      "device": "Rpi_zapallar", "active": true }
  ]
}
```

Coordinates live here rather than in `status.json`, which takes them off the device and closes F-08 properly rather than by accident.

---

## Event blob

One per detection. Written once, never modified.

```jsonc
{
  "schema_version": 2,
  "site":     "zapallar",
  "device":   "Rpi_zapallar",
  "event_id": "f3a2c1d0-…",

  "captured_utc": "2026-08-08T14:32:01.123456+00:00",   // when the audio was recorded
  "uploaded_utc": "2026-08-08T14:32:09.881000+00:00",   // when this blob was written

  "event_type": "vessel",        // vessel | blast | unknown.  D-014
  "detector":   "psd_tonal",     // which detector fired.      D-014
  "score":      0.85,            // detector confidence 0..1
  "suppressed": false,           // true = real detection, notification withheld by cooldown

  "audio_level": 0.0847,         // RMS 0..1
  "peak_db":     -21.4,          // dBFS, floored at -180
  "bearing_deg": null,           // 0..360 or null. see open questions

  "clip": {
    "path":         "sites/zapallar/clips/2026/08/08/f3a2c1d0-….wav",
    "sample_rate":  48000,
    "channels":     2,
    "duration_s":   5.0,
    "uploaded":     true         // false = notification sent, upload pending or failed
  },

  "detector_meta": { }           // free-form, detector-specific. never interpreted by the dashboard
}
```

Five fields exist because the prototype layout got them wrong. The reasoning, not a compatibility note:

`captured_utc` and `uploaded_utc` are separate. v1 conflated them, so every displayed timestamp was actually upload time, wrong by however long the cellular upload took.

`suppressed` exists, which is F-03's data-loss half. A cooldown suppresses the *notification*, never the record. Those are different concerns and v1 fused them.

`event_type` and `detector` are recorded per event because the detector already changed once silently around mid-July, leaving the manifest spanning two populations with no way to tell them apart. That must not be able to recur.

`clip.uploaded` makes the split-brain state in F-13 explicit rather than implied by a dead link.

`clip.path` is a container-relative path, not an absolute URL. v1 stored a full URL in the manifest and a bare blob name in the WhatsApp deep link, and the dashboard reconciled the two by string matching. One representation.

---

## `status.json`

Overwritten each heartbeat. Only fields the dashboard actually consumes, plus the health fields it needs and does not yet have.

```jsonc
{
  "schema_version": 2, "site": "…", "device": "…", "generated_utc": "…",

  "software_version": "2.0.0",
  "last_seen":        "…",          // liveness is derived from this, not from a status string
  "session_start":    "…",
  "uptime_seconds":   191700,       // process
  "system_uptime_s":  432000,       // since boot

  "health": {                       // NEW. the fail-loud surface
    "detector_ok":    true,         // false when no detector loaded. F-02
    "audio_ok":       true,         // false when peak RMS stayed below floor. already on device
    "duty_cycle_pct": 99.4,         // F-05. measured, not asserted
    "clips_dropped":  0,            // bounded-queue drops
    "upload_backlog": 0,            // events awaiting upload
    "degraded_reason": null         // human-readable when anything above is false
  },

  "detection": {                    // what is actually running, not ML-era names
    "detectors":   ["psd_tonal"],
    "thresholds":  { "psd_threshold_db": 8, "psd_f_min": 55, "psd_f_max": 1000,
                     "score_min": 0.60, "rms_min": 0.010 },
    "cooldown_s":  600,
    "last_rms":    0.0142
  },

  "audio":  { "device": "plughw:3,0", "sample_rate": 48000, "channels": 2 },

  "power":  { "battery_voltage_v": 12.84, "battery_current_a": 1.42,
              "panel_voltage_v": 18.20, "panel_power_w": 26,
              "charge_state": "Bulk", "charge_state_id": 3,
              "yield_today_kwh": 0.31, "yield_total_kwh": 84.6,
              "max_power_today_w": 58, "system_load_w": 3.4 },

  "network": { "signal_bars": 4, "signal_rssi": -71, "network_type": "LTE" },

  "system": { "cpu_temp_c": 48.3, "disk_used_pct": 31.2, "disk_free_gb": 18.44,
              "disk_total_gb": 29.0,
              "ram_used_pct": 42.1, "ram_used_mb": 862, "ram_total_mb": 2048 }
}
```

`ram_used_mb` and `ram_total_mb` are added because the dashboard already renders them.

`status: "online"` is gone. It was always the literal string `"online"`, written by the device, so its presence only ever proved the device wrote it. Liveness comes from `last_seen`, which is what the dashboard already does.

The `detection` block replaces `current_threshold`, which reported a value that did not participate in the alert decision (F-09). What is published is now what is actually in force.

`health` is the fail-loud surface. A unit that is running but not detecting must be able to say so, which is the entire F-01/F-02 class.

Coordinates, the legacy battery duplicates, `alert_count_session`, `modem_state`, `solar_error_code`, `solar_device` and `disk_path` are dropped. Nothing reads them.

---

## `power_history.json`

Structurally unchanged, plus the envelope.

```jsonc
{
  "schema_version": 2, "site": "…", "generated_utc": "…",
  "bucket_s": 1800,
  "window_h": 72,
  "history": [ { "ts": "…", "sys_w": 3.41, "panel_w": 26.3, "bat_v": 12.84 } ]
}
```

Oldest to newest. **Buckets with no samples are omitted entirely, never emitted with nulls.** Gaps in this array are how the dashboard reconstructs uptime across reboots. That is load-bearing. Do not backfill.

---

## `acoustic_indicators.json`

Consumed by the Monitoreo Acústico tab. **Producer does not exist in this repository.** `raspberry-pi/tools/a5_indicators.py` emits raw per-clip rows; something must aggregate them into medians and quartiles. Schema below is inferred from the consumer and needs confirming.

```jsonc
{
  "schema_version": 2, "site": "…", "generated_utc": "…",
  "latest":   { "click_rate_hz": 12.4, "ndsi": 0.31 },
  "timeline": [ { "ts": "…", "ndsi_med": 0.31, "ndsi_q1": 0.22, "ndsi_q3": 0.44,
                              "click_med": 12.4, "click_q1": 8.1, "click_q3": 19.0 } ],
  "diel":     [ { "hour": 0,  "ndsi_med": 0.28, "ndsi_q1": 0.19, "ndsi_q3": 0.40,
                              "click_med": 9.2,  "click_q1": 6.0, "click_q3": 14.1 } ]
}
```

`diel` is 24 entries, hour 0 to 23, local time. Per-clip fields available from `a5_indicators.py` and not currently surfaced: `ndsi_wideband`, `anthro_energy_300_700Hz`, `bio_energy_2_5kHz`, `bio_energy_2_20kHz`.

---

## `ocean_conditions.json`

Consumed by Condiciones del mar and Análisis. **Not produced by the device and not by anything in this repository.** Presumably a marine forecast API pulled and uploaded by an unidentified component. Schema inferred from the consumer.

```jsonc
{
  "schema_version": 2, "site": "…", "generated_utc": "…",
  "location":   { "name": "Zapallar", "lat": -32.552665, "lon": -71.465068 },
  "current":    { … same shape as an hourly point … },
  "hourly":     [ { "ts": "…", "swell_m": 1.8, "swell_period_s": 12.0,
                    "swell_dir": "SW", "wind_kmph": 14.0, "wind_deg": 220,
                    "wind_dir": "SW", "gust_kmph": 22.0, "wave_m": 2.1,
                    "water_temp_c": 14.2, "cloud_pct": 40, "weather_desc": "Parcial",
                    "is_forecast": false } ],
  "daily":      [ … ],
  "thresholds": { … dive-window defaults … }
}
```

`is_forecast` splits observation from forecast, which the charts render as a boundary. Roughly seven days of coverage, and that window constrains the Análisis correlation.

---

## Device configuration

One signed blob, written by the backend, read by the device. It is in this document rather than in `API-CONTRACT.md` because the transport is storage, like everything else here.

```
sites/{site_id}/remote_config.json        written by the backend. read-only to the device
```

The device polls it every `CONFIG_CHECK_INTERVAL` (300 s), on the transport path, never the capture path, with an explicit timeout (R-5.5). It applies a document only when `config_version` differs from the one in force.

### Document

```jsonc
{
  "schema_version":  2,
  "config_version":  "2026-08-22-01",     // any string or number. re-applied only when it changes
  "site":            "zapallar",
  "device_id":       "Rpi_zapallar",      // optional. null means the whole site
  "issued_utc":      "2026-08-22T14:00:00+00:00",

  "config": {                              // all keys optional. missing keys keep their default
    "detection_mode":       "psd",
    "score_min":            0.60,
    "alert_min_rms":        0.010,
    "alert_threshold":      0.08,
    "psd_threshold_db":     8,
    "psd_f_min":            55,
    "psd_f_max":            1000,
    "cooldown_s":           60,
    "heartbeat_interval_s": 60,
    "window_hop_s":         5.0
  },

  "signature": "9f2c…"                     // hex HMAC-SHA256
}
```

**An unknown key inside `config` is an error, not an omission.** A typo that silently tunes nothing is the quiet failure this system exists to remove. The whole document is rejected and the reason reaches `health.degraded_reason`.

### Signature

HMAC-SHA256 over **the entire document with `signature` removed**, serialised canonically: UTF-8, keys sorted, no whitespace.

```python
body = {k: v for k, v in document.items() if k != "signature"}
canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
signature = hmac.new(key.encode(), canonical.encode(), hashlib.sha256).hexdigest()
```

The key is `OCEANKIND_CONFIG_HMAC_KEY`, shared between the backend and the device, provisioned to the device in `/etc/oceankind.env`. It is not the storage credential, which is the point: whoever can write the container still cannot forge a configuration (F-10).

Verification is mandatory. The device compares with `hmac.compare_digest`, and a document that fails is discarded **whole**, never partially applied, logged, and named in `health.degraded_reason`. Rejecting a config is a health event, not a debug line. A device with no key configured refuses to apply any remote config at all, rather than accepting unsigned input.

### Clamping

The client chooses thresholds. We bound them. Clamping happens in the backend, before signing, so the device and the operator see the same number. A clamped value is applied and reported, never silently accepted and never rejected: a tuning mistake must not strand a unit on stale config. `detection_mode` is the exception, rejected rather than clamped, because an enum typo would disable detection.

Applied values appear in `status.json → detection.thresholds`, which closes F-09's tuning half (R-3.6).

| Parameter | Type | Range | Default | Why bounded |
|---|---|---|---|---|
| `detection_mode` | enum | `psd` \| `rms` \| `auto` | `psd` | Rejected, not clamped. A typo here disables detection |
| `score_min` | float | 0.05 – 0.95 | 0.60 | 0 alerts on everything, 1.0 on nothing. Both are silence |
| `alert_min_rms` | float | 0.0 – 0.20 | 0.010 | Above 0.20 no real signal clears the gate |
| `alert_threshold` | float | 0.005 – 0.50 | 0.08 | The `rms` mode's whole decision. 0 floods, 0.5 is deaf |
| `psd_threshold_db` | float | 3 – 30 | 8 | Below 3 dB every peak looks tonal; above 30 none do |
| `psd_f_min` | float | 20 – 2000 Hz | 55 | Must stay below `psd_f_max`. Inverted bounds are rejected |
| `psd_f_max` | float | 100 – 20000 Hz | 1000 | Capped at Nyquist after decimation |
| `cooldown_s` | float | 10 – 3600 | 60 | Throttles notifications only. Never suppresses the record (F-03) |
| `heartbeat_interval_s` | float | 30 – 3600 | 60 | Longer than an hour and liveness detection stops working |
| `window_hop_s` | float | 1.0 – 5.0 | 5.0 | Step between analysis windows. 5.0 is back-to-back, the calibrated behaviour. Below 5 the windows overlap so an event up to 5−h s always lands whole in one, at CPU cost ×(5/h). Measure on the bench before lowering |

### If the blob is unreachable or unchanged

**The device keeps its last valid configuration.** It never falls back to defaults. A network outage silently reverting thresholds would change detection behaviour at precisely the moment nobody can observe it, which is the failure class this project exists to remove.

There is no expiry. A configuration stays in force until a document with a different `config_version` verifies. Stale and detecting beats fresh and silent.

### Both implementations must converge on this

**CONVERGED both sides as of 2026-08-22.** Device convergence is verified by `raspberry-pi/tools/phase1_smoke_test.py` §8: an independently-signed document is accepted; tampered, wrong-key, unknown-key, bad-enum and inverted-band documents are rejected whole; a no-key device rejects everything; a document addressed to another `device_id` is skipped without being treated as an error.

| | Device (`oceankind/config.py`) | Backend (`routers/devices.py`) | Contract |
|---|---|---|---|
| Transport | reads `sites/{site}/remote_config.json` | **writes the blob.** `GET /api/devices/config` is a read-only debug view returning it byte for byte | the blob |
| Version key | `config_version` ✓ | `config_version` | `config_version` |
| Signature covers | whole document minus `signature` ✓ | whole document minus `signature` | whole document minus `signature` |
| Key name | `OCEANKIND_CONFIG_HMAC_KEY` | `OCEANKIND_CONFIG_HMAC_KEY` | `OCEANKIND_CONFIG_HMAC_KEY` |
| No key configured | refuses to apply ✓ | refuses to publish, 503 | refuses to apply |
| v1 flat key names | not accepted ✓ | not accepted | not accepted |
| Unknown `config` key | rejects whole document, reason in `health.degraded_reason` ✓ | n/a (validated before signing) | rejects whole document |
| Rejection surface | `health.degraded_reason` + ERROR log, deduped per `config_version` ✓ | 4xx to the operator | a health event, never a debug line |

Device additions beyond the four convergence items, per the prose above: `detection_mode` is runtime-tunable (rejected on bad enum, never clamped), clamp ranges match this document's table exactly (including the Nyquist-after-decimation cap on `psd_f_max`, 6 kHz at the default decimation), inverted PSD bounds are evaluated against the *merged* result of document + in-force values, and a device with no key still runs on its env-file configuration — it simply accepts no remote changes.

The remaining provisioning step: **generate the shared key and put it in both places** (backend secret store, device `/etc/oceankind.env`). Until then the backend refuses to publish and the device refuses to apply, which fails safe on both ends.

The blob is normative because the device side is verified and shipping, and because it needs no device credential to exist first (D-017, D-018 are about a different problem). `GET /api/devices/config` may stay as a read-only debugging view, but it must return this exact document, and the backend must gain the writer that puts it in storage.

---

## There is no migration

The prototype units' data stays where it is, in its own container, frozen. Nothing we build reads it. New units write v2 to the new container from their first heartbeat, and the dashboard reads that container and only that container.

No dual-write, no backfill, no deep-link preservation across the boundary, no compatibility layer on either side. If the prototype history is ever wanted alongside new data, that is a one-off offline import into `sites/{id}/events/` carrying `event_type: "unknown"` and `detector: "unknown"`, because those were never recorded. It is an analysis task, and it is not part of either codebase.

---

## Open questions

Everything here is in `CLIENT-DEPENDENCIES.md`.

What produces `ocean_conditions.json`, and what produces `acoustic_indicators.json` from `a5_indicators.py` rows.

Whether `bearing_deg` is real. The dashboard draws it, nothing emits it. Keep the field, allow null, and do not invent a producer.

Whether the local timezone for `diel` is fixed or per site.

Retention. Event blobs are append-only forever. At current volumes that is trivial, but nobody has stated a policy.

---

## Fixtures

`tools/generate_fixtures.py` in the dashboard repository writes a full tree in this shape, and it is the only shape either codebase is built against: two sites, one at `sites/zapallar/` and one at `sites/matanzas/`, several weeks of events across both `event_type` values including suppressed ones, matching clips, status, power history, acoustic indicators and ocean conditions.

This is what unblocks both ends. The dashboard builds against fixtures with no device in existence. The device's testable job is "produce blobs that validate against this schema", assertable with no detection science, no hydrophone and no cloud account:

```bash
python3 tools/validate_contract.py ./out          # device side. exit 0 = conformant
python3 raspberry-pi/tools/v2_conformance_test.py # drives the real emit code through it
```

Both sides are checked against the same document, which is the whole of what we are contracted to deliver.
