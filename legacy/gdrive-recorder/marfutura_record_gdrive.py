#!/usr/bin/env python3
"""
OceanKind — Grabación continua a Google Drive
Graba clips de audio en loop y los sube a Google Drive via rclone.
No hace detección ni análisis — solo graba y guarda.

Uso:
    python3 marfutura_record_gdrive.py

Detener:
    Ctrl+C

Dependencias:
    - arecord (ALSA, ya instalado en Pi)
    - rclone configurado con remote "gdrive"

Config rclone (una sola vez en la Pi):
    rclone mkdir gdrive:OceanKind/grabaciones
"""

import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

# ─── Configuración ────────────────────────────────────────────────────────────

AUDIO_DEVICE    = "plughw:3,0"   # USB GS3 — cambiar si el dispositivo cambia
SAMPLE_RATE     = 48000
CHANNELS        = 2
CLIP_SECONDS    = 5              # Duración de cada clip en segundos

GDRIVE_REMOTE   = "gdrive"
GDRIVE_FOLDER   = "OceanKind/grabaciones"

# Carpeta local temporal (los clips se borran tras subir)
LOCAL_DIR = Path.home() / "oceankind" / "grabaciones_tmp"

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("record")


def record_clip(output_path: str) -> bool:
    """Graba CLIP_SECONDS segundos de audio. Devuelve True si OK."""
    cmd = [
        "arecord",
        "-D", AUDIO_DEVICE,
        "-f", "S16_LE",
        "-r", str(SAMPLE_RATE),
        "-c", str(CHANNELS),
        "-d", str(CLIP_SECONDS),
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=CLIP_SECONDS + 10)
        if result.returncode != 0:
            log.warning("arecord error: %s", result.stderr.decode().strip())
            return False
        return True
    except subprocess.TimeoutExpired:
        log.warning("arecord timeout")
        return False
    except Exception as exc:
        log.warning("Error grabando: %s", exc)
        return False


def upload_clip(local_path: str, filename: str) -> bool:
    """Sube el clip a Google Drive via rclone. Devuelve True si OK."""
    dest = f"{GDRIVE_REMOTE}:{GDRIVE_FOLDER}/{filename}"
    cmd = ["rclone", "copyto", local_path, dest, "--progress"]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if result.returncode != 0:
            log.warning("rclone error: %s", result.stderr.decode().strip())
            return False
        return True
    except subprocess.TimeoutExpired:
        log.warning("rclone timeout")
        return False
    except Exception as exc:
        log.warning("Error subiendo: %s", exc)
        return False


def main():
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)

    log.info("=== OceanKind — Grabación continua ===")
    log.info("Dispositivo : %s  |  %d Hz  |  %d ch", AUDIO_DEVICE, SAMPLE_RATE, CHANNELS)
    log.info("Clip        : %d segundos", CLIP_SECONDS)
    log.info("Destino     : %s:%s", GDRIVE_REMOTE, GDRIVE_FOLDER)
    log.info("Ctrl+C para detener\n")

    clip_count  = 0
    error_count = 0

    try:
        while True:
            ts       = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            filename = f"{ts}.wav"
            local_path = str(LOCAL_DIR / filename)

            # 1. Grabar
            log.info("⏺  Grabando %s ...", filename)
            ok = record_clip(local_path)

            if not ok:
                error_count += 1
                log.warning("  Clip fallido (total errores: %d)", error_count)
                time.sleep(2)
                continue

            size_mb = os.path.getsize(local_path) / 1_048_576
            log.info("  ✓ Grabado (%.1f MB)", size_mb)

            # 2. Subir a Google Drive
            log.info("  ☁  Subiendo a Google Drive...")
            uploaded = upload_clip(local_path, filename)

            if uploaded:
                clip_count += 1
                log.info("  ✓ Subido — total clips: %d", clip_count)
            else:
                log.warning("  ✗ No se pudo subir — clip guardado localmente: %s", local_path)
                continue  # No borrar si no se subió

            # 3. Borrar local
            try:
                Path(local_path).unlink()
            except OSError:
                pass

    except KeyboardInterrupt:
        log.info("\nDetenido por usuario. Clips grabados: %d", clip_count)


if __name__ == "__main__":
    main()
