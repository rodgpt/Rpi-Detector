#!/usr/bin/env python3
"""
Validate a produced blob tree against the v2 data contract.

This is the test for the contracted work. It answers one question with an exit
code: does this device produce blobs the dashboard can consume?

    python3 tools/validate_contract.py ./out
    python3 tools/validate_contract.py ./out --strict     # warnings become failures

No detection science involved. It checks shape, types, required fields, path
layout and internal consistency. It does not care whether a detection is correct.

Standard library only. Exit 0 = conformant.
"""

import argparse, json, re, sys
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 2
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)$")
UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
EVENT_PATH = re.compile(r"^sites/[a-z0-9_-]+/events/\d{4}/\d{2}/\d{2}/[^/]+\.json$")

NUM = (int, float)

class Report:
    def __init__(self): self.errors, self.warnings, self.checked = [], [], 0
    def err(self, where, msg): self.errors.append(f"{where}: {msg}")
    def warn(self, where, msg): self.warnings.append(f"{where}: {msg}")


# ── primitives ────────────────────────────────────────────────────────────────

def req(r, where, obj, key, types, nullable=False):
    """Required key of a given type. Numeric fields may be null by contract."""
    if key not in obj:
        r.err(where, f"missing required field '{key}'"); return None
    v = obj[key]
    if v is None:
        if not nullable: r.err(where, f"'{key}' is null but not nullable")
        return None
    if not isinstance(v, types):
        r.err(where, f"'{key}' is {type(v).__name__}, expected {getattr(types,'__name__',types)}")
    return v

def iso_ok(r, where, obj, key):
    v = req(r, where, obj, key, str)
    if v and not ISO.match(v): r.err(where, f"'{key}' is not ISO-8601 UTC: {v!r}")
    return v

def no_nonfinite(r, where, obj, path=""):
    """Infinity and NaN are not valid JSON and blank the dashboard. Must be null."""
    if isinstance(obj, dict):
        for k, v in obj.items(): no_nonfinite(r, where, v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj): no_nonfinite(r, where, v, f"{path}[{i}]")
    elif isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            r.err(where, f"non-finite float at {path}. Must be serialised as null")

def envelope(r, where, d, expect_site=None):
    v = req(r, where, d, "schema_version", int)
    if v is not None and v != SCHEMA_VERSION:
        r.err(where, f"schema_version {v}, expected {SCHEMA_VERSION}")
    site = req(r, where, d, "site", str)
    req(r, where, d, "device", str)
    iso_ok(r, where, d, "generated_utc")
    if expect_site and site and site != expect_site:
        r.err(where, f"site '{site}' does not match its path '{expect_site}'")
    no_nonfinite(r, where, d)

def load(r, p: Path):
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as e:
        r.err(str(p), f"invalid JSON: {e}"); return None
    except OSError as e:
        r.err(str(p), f"unreadable: {e}"); return None


# ── per-blob checks ───────────────────────────────────────────────────────────

def check_sites(r, root: Path):
    p = root / "_sites.json"
    if not p.exists():
        r.err("_sites.json", "missing. The dashboard reads the site list from here"); return []
    d = load(r, p)
    if not d: return []
    r.checked += 1
    if req(r, "_sites.json", d, "schema_version", int) != SCHEMA_VERSION:
        pass
    iso_ok(r, "_sites.json", d, "generated_utc")
    sites = req(r, "_sites.json", d, "sites", list) or []
    ids = []
    for i, s in enumerate(sites):
        w = f"_sites.json[{i}]"
        sid = req(r, w, s, "id", str)
        req(r, w, s, "name", str)
        req(r, w, s, "lat", NUM); req(r, w, s, "lon", NUM)
        req(r, w, s, "device", str); req(r, w, s, "active", bool)
        if sid: ids.append(sid)
    if not ids: r.err("_sites.json", "no sites declared")
    return ids


def check_event(r, p: Path, rel: str, site: str, clip_index: set):
    d = load(r, p)
    if not d: return
    r.checked += 1
    w = rel
    envelope(r, w, d, expect_site=site)

    eid = req(r, w, d, "event_id", str)
    if eid and not UUID4.match(eid): r.warn(w, f"event_id is not a UUID4: {eid!r}")

    cap = iso_ok(r, w, d, "captured_utc")
    upl = iso_ok(r, w, d, "uploaded_utc")
    if cap and upl:
        try:
            if datetime.fromisoformat(upl) < datetime.fromisoformat(cap):
                r.err(w, "uploaded_utc precedes captured_utc")
        except ValueError:
            pass

    et = req(r, w, d, "event_type", str)
    if et and et not in ("vessel", "blast", "unknown"):
        r.warn(w, f"event_type '{et}' outside the known set")
    req(r, w, d, "detector", str)

    sc = req(r, w, d, "score", NUM)
    if isinstance(sc, NUM) and not (0.0 <= sc <= 1.0):
        r.err(w, f"score {sc} outside 0..1")

    req(r, w, d, "suppressed", bool)
    req(r, w, d, "audio_level", NUM, nullable=True)
    req(r, w, d, "peak_db", NUM, nullable=True)
    if "bearing_deg" not in d:
        r.err(w, "missing 'bearing_deg' (may be null, must be present)")

    clip = req(r, w, d, "clip", dict)
    if isinstance(clip, dict):
        cp = req(r, f"{w}.clip", clip, "path", str)
        req(r, f"{w}.clip", clip, "sample_rate", int)
        req(r, f"{w}.clip", clip, "channels", int)
        req(r, f"{w}.clip", clip, "duration_s", NUM)
        up = req(r, f"{w}.clip", clip, "uploaded", bool)
        if cp:
            if not cp.startswith(f"sites/{site}/clips/"):
                r.err(f"{w}.clip", f"path outside this site's clip tree: {cp}")
            if up is True and cp not in clip_index:
                r.err(f"{w}.clip", f"uploaded=true but no file at {cp}")
            if up is False and cp in clip_index:
                r.warn(f"{w}.clip", "uploaded=false but the file exists")
    if "detector_meta" not in d:
        r.warn(w, "missing 'detector_meta' (may be empty, should be present)")


def check_status(r, p: Path, rel: str, site: str):
    d = load(r, p)
    if not d: return
    r.checked += 1
    envelope(r, rel, d, expect_site=site)
    req(r, rel, d, "software_version", str)
    iso_ok(r, rel, d, "last_seen")
    iso_ok(r, rel, d, "session_start")
    req(r, rel, d, "uptime_seconds", int)
    req(r, rel, d, "system_uptime_s", int, nullable=True)

    if "status" in d:
        r.warn(rel, "'status' present. v1 always wrote the literal 'online'; liveness comes from last_seen")

    h = req(r, rel, d, "health", dict)
    if isinstance(h, dict):
        w = f"{rel}.health"
        req(r, w, h, "detector_ok", bool)
        req(r, w, h, "audio_ok", bool)
        req(r, w, h, "duty_cycle_pct", NUM, nullable=True)
        req(r, w, h, "clips_dropped", int)
        req(r, w, h, "upload_backlog", int)
        if "degraded_reason" not in h:
            r.err(w, "missing 'degraded_reason' (may be null, must be present)")
        ok = h.get("detector_ok") and h.get("audio_ok")
        if ok is False and h.get("degraded_reason") in (None, ""):
            r.err(w, "reports degraded but gives no degraded_reason. Failing loudly means saying why")

    det = req(r, rel, d, "detection", dict)
    if isinstance(det, dict):
        w = f"{rel}.detection"
        req(r, w, det, "detectors", list)
        th = req(r, w, det, "thresholds", dict)
        if isinstance(th, dict) and not th:
            r.err(w, "thresholds empty. Published thresholds must be the ones actually in force")
        req(r, w, det, "cooldown_s", NUM)
        req(r, w, det, "last_rms", NUM, nullable=True)

    for block, keys in (("audio", ("device", "sample_rate", "channels")),
                        ("power", ("battery_voltage_v", "panel_power_w", "system_load_w")),
                        ("network", ("signal_bars", "network_type")),
                        ("system", ("cpu_temp_c", "ram_used_pct", "ram_used_mb", "ram_total_mb"))):
        b = req(r, rel, d, block, dict)
        if isinstance(b, dict):
            for k in keys:
                if k not in b: r.err(f"{rel}.{block}", f"missing '{k}'")

    for gone in ("lat", "lon", "location_name"):
        if gone in d:
            r.warn(rel, f"'{gone}' present. Coordinates belong in _sites.json, not on the device")


def check_power_history(r, p: Path, rel: str, site: str):
    d = load(r, p)
    if not d: return
    r.checked += 1
    envelope(r, rel, d, expect_site=site)
    req(r, rel, d, "bucket_s", int)
    hist = req(r, rel, d, "history", list) or []
    last = None
    for i, pt in enumerate(hist):
        w = f"{rel}.history[{i}]"
        ts = iso_ok(r, w, pt, "ts")
        for k in ("sys_w", "panel_w", "bat_v"):
            if k not in pt: r.err(w, f"missing '{k}'")
            elif pt[k] is not None and not isinstance(pt[k], NUM):
                r.err(w, f"'{k}' is {type(pt[k]).__name__}")
        if ts:
            try:
                t = datetime.fromisoformat(ts)
                if last and t <= last: r.err(w, "history is not strictly oldest-to-newest")
                last = t
            except ValueError:
                pass
    bs = d.get("bucket_s")
    if isinstance(bs, int) and bs > 0 and len(hist) > 40:
        stamps = []
        for pt in hist:
            try: stamps.append(datetime.fromisoformat(pt["ts"]).timestamp())
            except Exception: pass
        spans = [b - a for a, b in zip(stamps, stamps[1:])]
        if spans and max(spans) <= bs * 1.5:
            r.warn(rel, "perfectly contiguous. Absent buckets are how the dashboard finds "
                        "outages; make sure gaps are omitted, not backfilled")


def check_acoustic(r, p: Path, rel: str, site: str):
    d = load(r, p)
    if not d: return
    r.checked += 1
    envelope(r, rel, d, expect_site=site)
    req(r, rel, d, "latest", dict)
    tl = req(r, rel, d, "timeline", list) or []
    for i, e in enumerate(tl[:200]):
        w = f"{rel}.timeline[{i}]"
        iso_ok(r, w, e, "ts")
        for k in ("ndsi_med", "ndsi_q1", "ndsi_q3", "click_med", "click_q1", "click_q3"):
            if k not in e: r.err(w, f"missing '{k}'")
    diel = req(r, rel, d, "diel", list) or []
    if diel and len(diel) != 24:
        r.err(rel, f"diel has {len(diel)} entries, expected 24 (hours 0-23)")


def check_ocean(r, p: Path, rel: str, site: str):
    d = load(r, p)
    if not d: return
    r.checked += 1
    envelope(r, rel, d, expect_site=site)
    req(r, rel, d, "location", dict)
    req(r, rel, d, "current", dict)
    hourly = req(r, rel, d, "hourly", list) or []
    fcst = 0
    for i, pt in enumerate(hourly[:400]):
        w = f"{rel}.hourly[{i}]"
        iso_ok(r, w, pt, "ts")
        for k in ("swell_m", "swell_period_s", "wind_kmph", "wind_deg", "is_forecast"):
            if k not in pt: r.err(w, f"missing '{k}'")
        if pt.get("is_forecast"): fcst += 1
    if hourly and fcst == 0:
        r.warn(rel, "no points flagged is_forecast. The dashboard draws the observation/forecast boundary from it")


# ── walk ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="directory holding the produced blob tree")
    ap.add_argument("--strict", action="store_true", help="treat warnings as failures")
    a = ap.parse_args()

    root = Path(a.root)
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr); return 2

    r = Report()

    stray = [p for p in root.glob("*.json") if p.name != "_sites.json"]
    for p in stray:
        r.err(p.name, "blob at the container root. Everything belongs under sites/{site_id}/")

    declared = check_sites(r, root)
    found = sorted(d.name for d in (root / "sites").iterdir() if d.is_dir()) if (root / "sites").is_dir() else []
    if not found:
        r.err("sites/", "no site directories found")
    for s in found:
        if declared and s not in declared:
            r.err(f"sites/{s}", "directory exists but the site is not declared in _sites.json")
    for s in declared:
        if s not in found:
            r.warn(f"sites/{s}", "declared in _sites.json but no data directory")

    for site in found:
        base = root / "sites" / site
        clips = {str(c.relative_to(root)) for c in (base / "clips").rglob("*.wav")}

        for name, fn in (("status.json", check_status),
                         ("power_history.json", check_power_history),
                         ("acoustic_indicators.json", check_acoustic),
                         ("ocean_conditions.json", check_ocean)):
            p = base / name
            rel = f"sites/{site}/{name}"
            if p.exists(): fn(r, p, rel, site)
            else: r.err(rel, "missing")

        events = sorted((base / "events").rglob("*.json")) if (base / "events").is_dir() else []
        if not events:
            r.err(f"sites/{site}/events/", "no event blobs. Detections are one blob per event")
        for p in events:
            rel = str(p.relative_to(root))
            if not EVENT_PATH.match(rel):
                r.err(rel, "event path must be sites/{site}/events/YYYY/MM/DD/*.json")
            check_event(r, p, rel, site, clips)

    print(f"\nchecked {r.checked} blob(s) across {len(found)} site(s)\n")
    for w in r.warnings: print(f"  WARN  {w}")
    if r.warnings: print()
    for e in r.errors: print(f"  FAIL  {e}")
    if r.errors: print()

    bad = len(r.errors) + (len(r.warnings) if a.strict else 0)
    if bad == 0:
        print(f"CONFORMANT{' (with ' + str(len(r.warnings)) + ' warnings)' if r.warnings else ''}")
        return 0
    print(f"NOT CONFORMANT: {len(r.errors)} error(s), {len(r.warnings)} warning(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
