# Data contract v2

**Status: AS-BUILT on the device (2026-08-13, D-016).** The device emits this contract, and only this contract, to a **new storage container** — verified end to end by `raspberry-pi/tools/v2_conformance_test.py`, which drives the production emit code and passes `tools/validate_contract.py`. The prototypes' old blob stays frozen on v1 (its final shape is the "Superseded" section below); the dashboard keeps reading it unchanged until its v2 reader exists. **There is no migration** — new fleet, new storage, clean start.

Derived by walking the five dashboard tabs, listing every field each one consumes, and working backwards. Client has confirmed the blob shape is ours to define.

Last updated 2026-08-13.

**Device-side implementation notes (normative for consumers):**

- Event blob filenames are `{YYYY-MM-DDTHH-MM-SS}_{uuid4}.json` (capture time, dashes); clips are `{uuid4}.wav`. Both under date partitions derived from `captured_utc`.
- The `health` block carries four fields beyond the schema below — `deaf_seconds_total`, `suppressed_count`, `events_dropped`, `wa_pending`, `archive_queue` — additive, same fail-loud spirit.
- `score` for an RMS-decided event is the normalised RMS (already 0..1); the PSD detector's score is its tonal fraction. `detector_meta.decided_by` records `psd_tonal` | `rms` | `rms_fallback`.
- Suppressed events carry their would-be `clip.path` with `uploaded: false`; the audio is not kept (D-008).
- The device merges its own entry into `_sites.json` at startup (rare write; the read-modify-write is tolerable until the backend owns the registry).
- `acoustic_indicators.json` and `ocean_conditions.json` have producers outside this repo. The device writes **conformant empty stubs at startup, only when the blob verifiably does not exist**, so the tree is valid and the tabs render empty instead of broken. The real producers overwrite them and must now write to the per-site paths.
- If an event blob upload fails, the event is spooled locally (bounded at 500) and retried each heartbeat; spool overflow discards oldest and counts them in `health.events_dropped`. No event is lost silently.
- `sites/{site}/remote_config.json` is read (not written) by the device, polled every 5 minutes. **Payload (Phase 2, 2026-08-13):**

  ```jsonc
  {
    "version": "2026-08-13-01",          // any string/number; re-applied only when it changes
    "config": {                           // all optional; unknown keys ignored
      "score_min": 0.60,                  // clamp 0..1
      "alert_min_rms": 0.010,             // clamp 0..1
      "alert_threshold": 0.08,            // clamp 0..1 (rms mode)
      "cooldown_s": 60,                   // clamp 10..3600
      "heartbeat_s": 60,                  // clamp 30..3600
      "psd_threshold_db": 8,              // clamp 1..30
      "psd_f_min": 55, "psd_f_max": 1000, // clamp 10..2000 / 20..4000
      "window_hop_s": 5.0                 // clamp 1..5. Step between analysis windows:
                                          // 5.0 = back-to-back (calibrated behaviour);
                                          // h<5 = overlapped windows — an event up to
                                          // 5−h s always lands whole in some window.
                                          // CPU cost ×(5/h); measure on bench first
    },
    "signature": "<hex>"                  // HMAC-SHA256 over canonical {"version","config"}
  }
  ```

  Out-of-range values are clamped and logged (R-8.3); applied values appear in `status.json → detection.thresholds` (R-3.6 — this closes F-09's tuning half). If the device has `OCEANKIND_CONFIG_HMAC_KEY` set, an invalid or missing `signature` **rejects the whole payload loudly** (F-10); without the key it applies unsigned with a warning. The v1 flat keys (`alert_threshold`, `cooldown_seconds`, `heartbeat_interval`) are still accepted.

- **Phase 2 health additions:** `health.capture_overflows` (PortAudio overflows + block-queue drops) joins the extra health fields. `status.json → audio.device` now describes the *selection rule* (`by-name:<hints>` or `synthetic:<pattern>`), not an ALSA index — indexes were the F-15 defect.

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

One consequence flows the other way: the device no longer reads its configuration from storage. See **Device configuration** below.

---

## Superseded: v1 + Phase 1 additions (2026-08-12) — the frozen prototype shape

**No device writes this anymore** (D-016, 2026-08-13). It is the final shape of the data sitting in the prototypes' old blob, kept because the dashboard reads that blob today and any tooling that touches the old history needs the reference. For one day (2026-08-12) a Phase 1 build emitted these additions on the v1 paths; it never reached the field.

**Every blob now carries `schema_version: 1`.** The v2 layout will carry `2`. A consumer seeing an unknown version must warn visibly, not blank the page.

**`status.json` additions** (all v1 fields unchanged):

- `site` — the site prefix, or `null` for the legacy root layout
- `health` — the fail-loud surface, published complete on every heartbeat:
  `detector_ok` (bool, false after 3 consecutive classifier failures — F-02),
  `audio_ok` (bool, from the RMS-floor window), `duty_cycle_pct` (measured, rolling
  1 h window, `null` until data exists — F-05), `deaf_seconds_total` (cumulative,
  session), `clips_dropped` (archive-queue overflow discards), `suppressed_count`
  (session), `upload_backlog` (WhatsApp alerts awaiting retry), `archive_queue`
  (clips awaiting Drive upload), `degraded_reason` (human-readable string or `null`)
- `detection` — what actually runs and decides (F-09's honest-reporting half):
  `mode` (`psd` | `rms` | `auto`), `detectors` (list), `thresholds`
  (`score_min`, `rms_min`, `rms_threshold`, `psd_threshold_db`, `psd_f_min`,
  `psd_f_max` — the values actually in force), `cooldown_s`, `last_rms`
- `lat` / `lon` / `location_name` are now env-sourced and **`null` when unconfigured**
  (F-08). The dashboard's own site table is the source of truth for location.

**`manifest.json`**: top level gains `schema_version`. Entries gain:

- `captured_utc` / `uploaded_utc` — split per R-4.3. **Semantic change:** the legacy
  `timestamp` field now equals `captured_utc` (capture time); in v1 it was upload time.
- `suppressed` (bool) — cooldown-suppressed detections are now **recorded, not erased**
  (F-03, D-008). Suppressed entries have `audio_url: null`, `clip_uploaded: false`,
  and no notification was sent. Historical counts before this change undercount.
- `clip_uploaded` (bool) — truthful upload state (F-13/R-4.4). Upload now happens
  **before** notification; `audio_url` may be `null` on a notified alert whose upload
  failed.
- `detector` (`psd_tonal` | `rms`) and `event_type` (`vessel` | `unknown`) — per
  R-3.3/D-014, so the record can never again silently span two detector populations.
- `decided_by` now reports what actually decided: `psd_tonal` | `rms` | `rms_fallback`
  (was hardcoded `rms+ml` — F-01).

**`power_history.json`**: gains `schema_version`. Otherwise unchanged.

**Dashboard obligations created:** render `suppressed` entries distinctly (do not hide);
tolerate `audio_url: null`; surface `health.detector_ok=false` and `degraded_reason`
prominently; treat unknown `schema_version` as warn-not-break. These are the dashboard's
Phase 1 items in the stack `PROGRESS.md`.

---

## What v1 got wrong

Four things worth fixing now, because none of them get cheaper later.

**No version field.** A schema change is undetectable by either side. Every blob in v2 carries `schema_version`.

**Asymmetric site paths.** The first site writes to the container root and the second is namespaced under `matanzas/`. A third inherits an inconsistent scheme. In v2 nothing lives at the root.

**A rewritten manifest.** Every alert downloads the whole file, inserts, and re-uploads. That races against the retry path (F-14) and forces the dashboard to fetch the full history every 30 seconds (F-18). Per-event blobs remove both by construction rather than patching them.

**Write-only and read-only fields.** The device writes fifteen fields nothing reads. The dashboard reads three fields nothing writes. Both directions are live defects today.

---

## Live mismatches found while writing this

| Field | Situation |
|---|---|
| `ram_total_mb`, `ram_used_mb` | Dashboard renders them. Device only produces `ram_used_pct`. Broken now |
| `deg` | Dashboard draws detection bearings from it. **No code in the repository emits it** |
| `ndsi_med`, `ndsi_q1`, `ndsi_q3`, `click_med`, `click_q1`, `click_q3` | Dashboard expects medians and quartiles. `a5_indicators.py` emits raw per-clip `ndsi` and `click_rate_hz`. **The aggregator between them does not exist in this repository** |

Device fields written and never read: `status`, `alert_count_session`, `battery_voltage`, `battery_percent`, `solar_charging`, `modem_state`, `solar_error_code`, `solar_device`, `disk_path`, `lat`, `lon`, `location_name`, `bucket_s`.

The coordinates dropping out of the read path is incidentally good: the dashboard now takes location from its own site table, so F-08 is half-mitigated on the consumer side while the device still publishes them.

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

Five deliberate changes from the v1 manifest entry.

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

**`remote_config.json` is retired.** It was a blob in the same container the device writes to, unsigned and unclamped, read every five minutes and applied on a version bump (F-10). Anyone holding the storage key could halve a threshold, or set the cooldown to a week, and the network would go quiet while every unit kept reporting itself healthy. It is not migrated to v2. It is removed.

Configuration now comes from the backend, over HTTP, signed.

```
GET /api/devices/config
X-Device-Id:  Rpi_zapallar
X-Device-Key: <per-device secret>
```

The device credential is separate from any user session (dashboard R-6.1). A compromised browser session cannot reconfigure a device, and a stolen device key grants only this endpoint.

### Payload

```jsonc
{
  "schema_version":  2,
  "device_id":       "Rpi_zapallar",
  "site":            "zapallar",
  "config_version":  7,                        // monotonic. the device applies only what is newer
  "issued_utc":      "2026-08-12T14:00:00+00:00",
  "expires_utc":     "2026-08-13T14:00:00+00:00",

  "config": {
    "detection_mode":       "psd",
    "score_min":            0.60,
    "alert_min_rms":        0.010,
    "alert_threshold":      0.08,
    "psd_threshold_db":     8,
    "psd_f_min":            55,
    "psd_f_max":            1000,
    "cooldown_s":           60,
    "heartbeat_interval_s": 60
  },

  "signature": "9f2c..."                       // hex HMAC-SHA256
}
```

### Signature

HMAC-SHA256 over the payload with `signature` removed, serialised canonically: UTF-8, keys sorted, no whitespace (`json.dumps(payload, sort_keys=True, separators=(",", ":"))`). The key is `OCEANKIND_CONFIG_SIGNING_KEY`, held server-side and provisioned to the device in `/etc/oceankind.env`. It never appears in a blob, a log line or a response body.

The device recomputes and compares with `hmac.compare_digest`. A payload that fails verification is discarded whole, never partially applied, logged, and named in `health.degraded_reason`. Rejecting a config is a health event, not a debug line.

### Clamping

The client chooses thresholds; we bound them. A value outside its range is clamped to the nearest bound, applied, and reported in `detection.thresholds` at its clamped value, so `status.json` always shows what is actually in force (R-2.6). Clamping happens server-side, before signing, so the device and the operator see the same number.

| Parameter | Type | Range | Default | Why bounded |
|---|---|---|---|---|
| `detection_mode` | enum | `psd` \| `rms` \| `auto` | `psd` | Anything else is a typo that disables detection |
| `score_min` | float | 0.05 - 0.95 | 0.60 | 0 alerts on everything, 1.0 alerts on nothing. Both are silence |
| `alert_min_rms` | float | 0.0 - 0.20 | 0.010 | Above 0.20 no real signal clears the gate |
| `alert_threshold` | float | 0.005 - 0.50 | 0.08 | The `rms` mode's whole decision. 0 floods, 0.5 is deaf |
| `psd_threshold_db` | float | 3 - 30 | 8 | Below 3 dB every peak looks tonal; above 30 none do |
| `psd_f_min` | float | 20 - 2000 Hz | 55 | Must stay below `psd_f_max`. Inverted bounds are rejected, not clamped |
| `psd_f_max` | float | 100 - 20000 Hz | 1000 | Capped at Nyquist after decimation |
| `cooldown_s` | float | 10 - 3600 | 60 | Throttles notifications only. It never suppresses the record (F-03) |
| `heartbeat_interval_s` | float | 30 - 3600 | 60 | Longer than an hour and liveness detection stops working |

### Expiry and unreachability

`expires_utc` means *refresh me*, not *stop*.

**If the API is unreachable, the device keeps its last valid configuration.** It does not fall back to defaults. A network outage silently reverting thresholds would change detection behaviour at precisely the moment nobody can observe it, which is the failure class this project exists to remove.

**If the configuration is past `expires_utc`, the device keeps using it** and sets `health.degraded_reason` to name the staleness after two consecutive missed refreshes. Stale-and-detecting beats fresh-and-silent.

The device polls on the interval `CONFIG_CHECK_INTERVAL` already uses. The poll runs on the transport path, never the capture path, and carries an explicit timeout (R-5.5).

---

## Migration

**Cancelled by D-016 (2026-08-13).** There is no migration: the prototypes' blob is frozen on v1 with the dashboard reading it as-is, and the v2 fleet starts clean on new storage. The dual-write, backfill and deep-link-redirect plan below is retired.

Two residual truths: WhatsApp `?play=` links sent by *new* units carry v2 clip paths and resolve only once the dashboard's v2 reader exists; and if anyone ever wants the prototype history alongside new data, that becomes a one-off offline import into `sites/{id}/events/` with `event_type: "unknown"`, `detector: "unknown"` — an analysis task, not a device concern.

---

## Open questions

Everything here is in `CLIENT-DEPENDENCIES.md`.

What produces `ocean_conditions.json`, and what produces `acoustic_indicators.json` from `a5_indicators.py` rows.

Whether `bearing_deg` is real. The dashboard draws it, nothing emits it. Keep the field, allow null, and do not invent a producer.

Whether the local timezone for `diel` is fixed or per site.

Retention. Event blobs are append-only forever. At current volumes that is trivial, but nobody has stated a policy.

---

## Fixtures

The generator writes a full tree in this shape into a development storage account: two sites, one at `sites/zapallar/` and one at `sites/matanzas/`, several weeks of events across both `event_type` values including suppressed ones, matching clips, status, power history, acoustic indicators and ocean conditions.

This is what unblocks both ends. The dashboard builds against fixtures with no device in existence. The device's testable job becomes "produce blobs that validate against this schema", which is assertable without any detection science, and which is the whole of what we are contracted to deliver.
