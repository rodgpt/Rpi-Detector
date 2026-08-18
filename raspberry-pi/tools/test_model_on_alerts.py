#!/usr/bin/env python3
"""
Descarga los clips de alerta del container Azure 'alerts' y los clasifica con model.joblib.

Útil para evaluar qué tan bien el modelo distingue detonaciones reales de falsos positivos
sobre datos reales del campo (no del set de entrenamiento).

Uso:
    OCEANKIND_STORAGE_CONNECTION_STRING="..." python3 test_model_on_alerts.py
    OCEANKIND_STORAGE_CONNECTION_STRING="..." python3 test_model_on_alerts.py --limit 20
    OCEANKIND_STORAGE_CONNECTION_STRING="..." python3 test_model_on_alerts.py --threshold 0.7
"""
import argparse
import os
import sys
import tempfile
from pathlib import Path

import joblib
import numpy as np
from azure.storage.blob import ContainerClient

# Reutilizar el extractor de features de predict.py
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from predict import extract_features  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="Procesar máximo N clips (0=todos)")
    ap.add_argument("--threshold", type=float, default=0.5, help="Umbral de proba para marcar como FILTRO")
    ap.add_argument("--model", default=str(HERE / "model.joblib"))
    args = ap.parse_args()

    cs = os.environ.get("OCEANKIND_STORAGE_CONNECTION_STRING")
    if not cs:
        sys.exit("ERROR: OCEANKIND_STORAGE_CONNECTION_STRING no está seteada")

    # Cargar modelo
    bundle = joblib.load(args.model)
    model = bundle["model"]
    sr = bundle["sr"]
    n_mfcc = bundle["n_mfcc"]
    print(f"Modelo cargado: sr={sr}Hz, n_mfcc={n_mfcc}")
    print()

    container = ContainerClient.from_connection_string(cs, container_name="alerts")

    blobs = sorted(
        [b for b in container.list_blobs() if b.name.startswith("alert_") and b.name.endswith(".wav")],
        key=lambda b: b.name,
        reverse=True,  # más recientes primero
    )
    if args.limit:
        blobs = blobs[: args.limit]
    print(f"Clips a evaluar: {len(blobs)}")
    print()

    print(f"{'archivo':<48} {'RMS':>8} {'pred':<8} {'prob_blast':>10} {'verdict':>9}")
    print("-" * 90)

    n_pred_blast = 0
    n_high_proba = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for b in blobs:
            local = tmp / b.name
            try:
                blob_client = container.get_blob_client(b.name)
                with open(local, "wb") as f:
                    f.write(blob_client.download_blob().readall())
                feats = extract_features(local, sr, n_mfcc).reshape(1, -1)
                # RMS rápido del audio crudo (para comparar con el RMS del detector)
                import wave
                with wave.open(str(local), "rb") as w:
                    frames = w.readframes(w.getnframes())
                    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
                    rms = float(np.sqrt(np.mean(samples ** 2)))
                pred = int(model.predict(feats)[0])
                proba = float(model.predict_proba(feats)[0, 1])
                label = "FILTRO" if pred == 1 else "bg"
                verdict = "✓ BLAST" if proba >= args.threshold else "—"
                if pred == 1:
                    n_pred_blast += 1
                if proba >= args.threshold:
                    n_high_proba += 1
                print(f"{b.name[-48:]:<48} {rms:>8.4f} {label:<8} {proba:>10.3f} {verdict:>9}")
            except Exception as exc:
                print(f"{b.name[-48:]:<48}   ERROR: {exc}")

    print()
    print(f"Total clasificados como FILTRO (modelo): {n_pred_blast}/{len(blobs)}")
    print(f"Total con prob >= {args.threshold}:           {n_high_proba}/{len(blobs)}")


if __name__ == "__main__":
    main()
