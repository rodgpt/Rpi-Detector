"""Detector de nivel RMS. El modo de respaldo REAL que F-01 exigía.

Sin ciencia: dispara cuando el nivel del clip supera alert_threshold. No
distingue tipo de evento — event_type "unknown" — pero SÍ puede disparar,
que es todo su punto: es el detector que queda en pie cuando el clasificador
está caído (modo auto) o cuando se corre solo (modo rms).

score = RMS normalizado (ya es 0..1). No hay confianza que inventar.
"""

EVENT_TYPE = "unknown"


def detect(fs: int, samples, cfg: dict, rms: float) -> dict | None:
    if rms >= cfg["alert_threshold"]:
        return {"type": EVENT_TYPE, "score": rms, "label": "RUIDO FUERTE",
                "meta": {"rms": round(rms, 4), "threshold": cfg["alert_threshold"]}}
    return None
