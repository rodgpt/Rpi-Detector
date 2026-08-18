"""Almacenamiento v2: paths explícitos, dos backends, spool de eventos.

Paths completos relativos al contenedor ("sites/{site}/…", "_sites.json").
Backends: Azure Blob (STORAGE_CONNECTION_STRING) o directorio local
(OUTPUT_DIR, R-9.4) — mismo árbol, validable con tools/validate_contract.py.

Ningún evento se pierde en silencio: si la subida falla, se encola en un spool
acotado y se reintenta; el desborde se cuenta y publica (R-1.3, D-015).
"""

import io
import json
import logging
import math
import shutil
import wave
from datetime import datetime, timezone
from pathlib import Path

from . import config as C

log = logging.getLogger("oceankind")

# Timeouts explícitos del SDK de Azure (R-5.5). connection_timeout /
# read_timeout son kwargs del cliente; el kwarg timeout de cada operación es
# el timeout de servidor.
AZ_CONNECT_TIMEOUT_S = 10
AZ_READ_TIMEOUT_S    = 60


def site_path(name: str) -> str:
    return f"sites/{C.SITE}/{name}"


def _get_blob_client(rel_path: str):
    from azure.storage.blob import BlobClient  # noqa: PLC0415
    return BlobClient.from_connection_string(
        C.STORAGE_CONNECTION_STRING,
        container_name=C.STORAGE_CONTAINER,
        blob_name=rel_path,
        connection_timeout=AZ_CONNECT_TIMEOUT_S,
        read_timeout=AZ_READ_TIMEOUT_S,
    )


def sanitize_for_json(obj):
    """floats no finitos → None. `Infinity` no es JSON válido y dejó el
    dashboard en blanco una vez (R-4.6). Cicatriz real, no teoría."""
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def upload_bytes(rel_path: str, payload: bytes, content_type: str) -> bool:
    try:
        if C.OUTPUT_DIR:
            dest = Path(C.OUTPUT_DIR) / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(payload)
            return True
        from azure.storage.blob import ContentSettings  # noqa: PLC0415
        blob = _get_blob_client(rel_path)
        blob.upload_blob(payload, overwrite=True,
                         content_settings=ContentSettings(content_type=content_type))
        return True
    except Exception as exc:
        log.warning("Error subiendo %s: %s", rel_path, exc)
        return False


def upload_json(rel_path: str, data: dict) -> bool:
    payload = json.dumps(sanitize_for_json(data), indent=2, allow_nan=False).encode()
    return upload_bytes(rel_path, payload, "application/json")


def upload_clip_file(local_path: str, rel_path: str) -> bool:
    try:
        if C.OUTPUT_DIR:
            dest = Path(C.OUTPUT_DIR) / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local_path, dest)
            return True
        return upload_bytes(rel_path, Path(local_path).read_bytes(), "audio/wav")
    except Exception as exc:
        log.warning("Error subiendo clip %s: %s", rel_path, exc)
        return False


def wav_bytes(samples) -> bytes:
    """Serializa un array int16 [N] o [N, canales] a WAV en memoria.

    Sin archivos temporales: el directorio de clips era RAM y cada archivo
    olvidado era una fuga (F-03). En memoria no hay nada que olvidar.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(C.CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(C.SAMPLE_RATE)
        wf.writeframes(samples.tobytes())
    return buf.getvalue()


def download_json(rel_path: str) -> dict | None:
    try:
        if C.OUTPUT_DIR:
            p = Path(C.OUTPUT_DIR) / rel_path
            return json.loads(p.read_text()) if p.exists() else None
        blob = _get_blob_client(rel_path)
        return json.loads(blob.download_blob().readall())
    except Exception:
        return None


def blob_exists(rel_path: str) -> bool | None:
    """True/False con certeza; None si no se pudo determinar (p.ej. red caída)."""
    try:
        if C.OUTPUT_DIR:
            return (Path(C.OUTPUT_DIR) / rel_path).exists()
        return bool(_get_blob_client(rel_path).exists())
    except Exception:
        return None


# ─── Eventos: un blob por detección, append-only (R-4.1, F-14) ───────────────

def event_rel_paths(captured_dt: datetime, event_id: str) -> tuple[str, str]:
    day = captured_dt.strftime("%Y/%m/%d")
    stamp = captured_dt.strftime("%Y-%m-%dT%H-%M-%S")
    return (site_path(f"events/{day}/{stamp}_{event_id}.json"),
            site_path(f"clips/{day}/{event_id}.wav"))


def build_event(event_id: str, captured_iso: str, event_type: str, detector: str,
                score: float, suppressed: bool, rms: float, peak_db: float,
                clip_rel: str, clip_uploaded: bool, detector_meta: dict | None = None) -> dict:
    return {
        "schema_version": C.SCHEMA_VERSION,
        "site":           C.SITE,
        "device":         C.DEVICE_ID,
        "generated_utc":  datetime.now(timezone.utc).isoformat(),
        "event_id":       event_id,
        "captured_utc":   captured_iso,
        "uploaded_utc":   datetime.now(timezone.utc).isoformat(),
        "event_type":     event_type,
        "detector":       detector,
        "score":          round(min(1.0, max(0.0, score)), 4),
        "suppressed":     suppressed,
        "audio_level":    round(rms, 4),
        "peak_db":        round(peak_db, 1),
        "bearing_deg":    None,   # sin productor hoy; el campo existe por contrato
        "clip": {
            "path":        clip_rel,
            "sample_rate": C.SAMPLE_RATE,
            "channels":    C.CHANNELS,
            "duration_s":  C.CAPTURE_SECONDS,
            "uploaded":    clip_uploaded,
        },
        "detector_meta":  detector_meta or {},
    }


def write_event(event: dict, rel_path: str) -> None:
    if upload_json(rel_path, event):
        log.info("  → evento registrado: %s%s", rel_path,
                 "  [suprimido]" if event["suppressed"] else "")
        return
    spool_event(event, rel_path)


def spool_event(event: dict, rel_path: str) -> None:
    from . import health  # noqa: PLC0415
    try:
        C.EVENT_SPOOL_DIR.mkdir(parents=True, exist_ok=True)
        (C.EVENT_SPOOL_DIR / f"{event['event_id']}.json").write_text(
            json.dumps({"rel_path": rel_path, "event": sanitize_for_json(event)}, allow_nan=False))
        queue = sorted(C.EVENT_SPOOL_DIR.glob("*.json"))
        if len(queue) > C.EVENT_SPOOL_MAX:
            dropped = len(queue) - C.EVENT_SPOOL_MAX
            for old in queue[:dropped]:
                old.unlink(missing_ok=True)
            health.count_events_dropped(dropped)
            log.error("spool de eventos lleno — %d evento/s más antiguo/s DESCARTADO/S", dropped)
        log.warning("  evento encolado localmente (%d pendientes)", event_spool_len())
    except Exception as exc:
        health.count_events_dropped(1)
        log.error("evento PERDIDO: no se pudo subir ni encolar (%s)", exc)


def drain_event_spool() -> None:
    if not C.EVENT_SPOOL_DIR.is_dir():
        return
    for f in sorted(C.EVENT_SPOOL_DIR.glob("*.json")):
        try:
            item = json.loads(f.read_text())
        except Exception:
            f.unlink(missing_ok=True)
            continue
        if upload_json(item["rel_path"], item["event"]):
            f.unlink(missing_ok=True)
            log.info("  → evento pendiente subido: %s", item["rel_path"])
        else:
            break   # la red sigue caída; no insistir esta vuelta


def event_spool_len() -> int:
    try:
        return sum(1 for _ in C.EVENT_SPOOL_DIR.glob("*.json")) if C.EVENT_SPOOL_DIR.is_dir() else 0
    except Exception:
        return 0


# ─── Registro de sitios y blobs auxiliares ───────────────────────────────────

def publish_site_registry() -> None:
    """Inserta/actualiza la entrada de ESTE sitio en _sites.json (R-5.2).

    Las coordenadas viven aquí, no en status.json (F-08). Escritura rara (solo
    al arrancar); el read-modify-write es tolerable hasta que exista backend.
    """
    reg = download_json("_sites.json") or {}
    sites = [s for s in reg.get("sites", []) if isinstance(s, dict)]
    entry = {"id": C.SITE, "name": C.SENSOR_LOCATION_NAME, "lat": C.SENSOR_LAT,
             "lon": C.SENSOR_LON, "device": C.DEVICE_ID, "active": True}
    sites = [s for s in sites if s.get("id") != C.SITE] + [entry]
    ok = upload_json("_sites.json", {
        "schema_version": C.SCHEMA_VERSION,
        "generated_utc":  datetime.now(timezone.utc).isoformat(),
        "sites":          sorted(sites, key=lambda s: s.get("id", "")),
    })
    log.info("Registro de sitios %s (_sites.json, %d sitio/s)",
             "publicado" if ok else "NO publicado", len(sites))


def ensure_aux_blobs() -> None:
    """Stubs conformes para blobs de OTROS productores (dependencias 8 y 9).

    Solo si el blob con certeza no existe (blob_exists() is False), para jamás
    pisar datos reales por un error transitorio de red.
    """
    envelope = {"schema_version": C.SCHEMA_VERSION, "site": C.SITE, "device": C.DEVICE_ID,
                "generated_utc": datetime.now(timezone.utc).isoformat()}
    stubs = {
        "acoustic_indicators.json": {**envelope, "latest": {}, "timeline": [], "diel": []},
        "ocean_conditions.json":    {**envelope, "location": {"name": C.SENSOR_LOCATION_NAME,
                                                              "lat": C.SENSOR_LAT, "lon": C.SENSOR_LON},
                                     "current": {}, "hourly": [], "daily": [], "thresholds": {}},
    }
    for name, stub in stubs.items():
        rel = site_path(name)
        if blob_exists(rel) is False:
            upload_json(rel, stub)
            log.info("Stub conforme escrito: %s (lo sobreescribe su productor real)", rel)
