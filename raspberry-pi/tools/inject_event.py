#!/usr/bin/env python3
"""Emit one event by hand, through the real code path.

    ~/oceankind/venv/bin/python raspberry-pi/tools/inject_event.py
    ~/oceankind/venv/bin/python raspberry-pi/tools/inject_event.py --count 5 --interval 2
    ~/oceankind/venv/bin/python raspberry-pi/tools/inject_event.py --dry-run

Why this exists: `synthetic:tone` fires on EVERY 5 s window (~17 280 events a
day) and there is no knob that makes it fire less often — the only levers are
all-or-nothing (see the header of BENCH.md §4). So the usable bench setup is a
quiet source plus events on demand: run the soak on `synthetic:noise` to prove
capture, duty cycle and health, and inject events here when you want some.

It calls `storage.build_event`, `storage.event_rel_paths`, `storage.write_event`
and `push.push_event` — the same functions the transport worker calls — so the
blob it writes is contract-identical to a real one and lands in the same place
(Azure or OUTPUT_DIR, whichever /etc/oceankind.env selects). It does NOT send
WhatsApp: notification is the service's job and firing one from a test tool is
how a bench run pages a real recipient.

Must run under the service's venv (`~/oceankind/venv/bin/python`): the system
python3 has no numpy.
"""
import argparse
import os
import random
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent          # raspberry-pi/
DEFAULT_ENV = "/etc/oceankind.env"


def load_env_file(path: str) -> int:
    """KEY=VALUE into os.environ, without overriding what is already set.

    The config module reads the environment at IMPORT time, so this has to run
    before `from oceankind import ...`. Values are never logged: this file holds
    the Twilio token (F-04).
    """
    p = Path(path)
    if not p.is_file():
        return 0
    loaded = 0
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val
            loaded += 1
    return loaded


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=1, help="how many events (default 1)")
    ap.add_argument("--interval", type=float, default=0.0,
                    help="seconds between events when --count > 1")
    ap.add_argument("--label", default="MOTOR", help="event_type (default MOTOR)")
    ap.add_argument("--detector", default="manual",
                    help="detector name recorded in the event (default 'manual' — "
                         "keeps injected events distinguishable from real ones)")
    ap.add_argument("--score", type=float, default=0.95, help="0..1 (default 0.95)")
    ap.add_argument("--suppressed", action="store_true",
                    help="mark as cooldown-suppressed (no clip, as the service does)")
    ap.add_argument("--no-clip", action="store_true", help="do not synthesise or upload audio")
    ap.add_argument("--env-file", default=DEFAULT_ENV,
                    help=f"config to load (default {DEFAULT_ENV})")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the event JSON, write nothing, push nothing")
    args = ap.parse_args()

    n = load_env_file(args.env_file)
    print(f"config: {args.env_file} ({n} vars loaded)" if n
          else f"config: {args.env_file} not readable — using the current environment")

    sys.path.insert(0, str(REPO / "src"))
    try:
        import numpy as np
        from oceankind import config as C, push, storage
    except ImportError as exc:
        print(f"ERROR: {exc}\nRun this with ~/oceankind/venv/bin/python — "
              "the system python3 has no numpy.", file=sys.stderr)
        return 1

    if C.STORAGE_ENABLED and not C.SITE:
        print("ERROR: OCEANKIND_SITE is empty and storage is enabled. Everything "
              "lives under sites/{site}/ in the v2 contract — set it in the env file.",
              file=sys.stderr)
        return 1

    dest = (f"OUTPUT_DIR {C.OUTPUT_DIR}" if C.OUTPUT_DIR
            else f"Azure container '{C.STORAGE_CONTAINER}'" if C.STORAGE_ENABLED
            else "nowhere (storage disabled)")
    print(f"site={C.SITE or '(unset)'}  device={C.DEVICE_ID}  destination: {dest}")
    print(f"backend push: {'enabled' if push.enabled() else 'disabled'}")
    if args.dry_run:
        print("DRY RUN — nothing will be written or pushed")

    written = 0
    for i in range(args.count):
        if i and args.interval:
            time.sleep(args.interval)

        event_id = uuid.uuid4().hex
        captured = datetime.now(timezone.utc)
        event_rel, clip_rel = storage.event_rel_paths(captured, event_id)

        # Plausible levels rather than round numbers, so an injected event does
        # not stand out as obviously synthetic in the record — the --detector
        # field is what marks it, deliberately and visibly.
        rms = round(random.uniform(0.05, 0.30), 4)
        peak_db = round(20 * np.log10(max(rms, 1e-9)), 1)

        clip_uploaded = False
        want_clip = not args.no_clip and not args.suppressed
        if want_clip and not args.dry_run and C.STORAGE_ENABLED:
            frames = int(C.CAPTURE_SECONDS * C.SAMPLE_RATE)
            t = np.arange(frames) / C.SAMPLE_RATE
            mono = 0.30 * np.sin(2 * np.pi * 120 * t) + 0.25 * np.sin(2 * np.pi * 240 * t)
            pcm = (np.clip(mono, -1, 1) * 32000).astype(np.int16)
            samples = np.column_stack([pcm] * C.CHANNELS)
            clip_uploaded = storage.upload_bytes(clip_rel, storage.wav_bytes(samples),
                                                 "audio/wav")
            print(f"  clip: {clip_rel} {'ok' if clip_uploaded else 'FAILED'}")

        event = storage.build_event(
            event_id, captured.isoformat(), args.label, args.detector,
            args.score, args.suppressed, rms, peak_db, clip_rel, clip_uploaded,
            {"decided_by": "inject_event.py", "injected": True})

        if args.dry_run:
            import json
            print(json.dumps(event, indent=2))
            continue

        # Same order as the transport worker: blob first, push after and
        # independent of it (contract §Event upload).
        if C.STORAGE_ENABLED:
            storage.write_event(event, event_rel)
        else:
            print("  storage disabled — event built but not written")
        push.push_event(event)
        written += 1
        print(f"  [{i + 1}/{args.count}] {event_rel}")

    if not args.dry_run:
        print(f"\n{written} event(s) emitted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
