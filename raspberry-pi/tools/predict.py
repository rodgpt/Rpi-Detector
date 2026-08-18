"""
Clasifica archivos .wav usando el modelo entrenado.

Uso:
    python3 predict.py archivo.wav [otro.wav ...]
    python3 predict.py carpeta/
"""
import sys
from pathlib import Path
import numpy as np
import librosa
import joblib

BASE = Path(__file__).parent
MODEL_PATH = BASE / "model.joblib"


def extract_features(path: Path, sr: int, n_mfcc: int) -> np.ndarray:
    y, _ = librosa.load(str(path), sr=sr, mono=True)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=n_mfcc)
    mfcc_mean = mfcc.mean(axis=1)
    mfcc_std = mfcc.std(axis=1)
    cent = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    bw = librosa.feature.spectral_bandwidth(y=y, sr=sr)[0]
    rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)[0]
    flat = librosa.feature.spectral_flatness(y=y)[0]
    zcr = librosa.feature.zero_crossing_rate(y)[0]
    rms = librosa.feature.rms(y=y)[0]
    extras = np.array([
        cent.mean(), cent.std(),
        bw.mean(), bw.std(),
        rolloff.mean(), rolloff.std(),
        flat.mean(), flat.std(),
        zcr.mean(), zcr.std(),
        rms.mean(), rms.std(),
    ])
    return np.concatenate([mfcc_mean, mfcc_std, extras])


def gather_files(args):
    files = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            files.extend(sorted(p.glob("*.wav")))
        elif p.is_file():
            files.append(p)
        else:
            print(f"[warn] no existe: {a}", file=sys.stderr)
    return files


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    bundle = joblib.load(MODEL_PATH)
    model = bundle["model"]
    sr = bundle["sr"]
    n_mfcc = bundle["n_mfcc"]

    files = gather_files(sys.argv[1:])
    if not files:
        print("No se encontraron archivos .wav")
        sys.exit(1)

    print(f"{'archivo':<60} {'pred':<10} {'prob_positivo':>14}")
    print("-" * 86)
    for f in files:
        feats = extract_features(f, sr, n_mfcc).reshape(1, -1)
        pred = model.predict(feats)[0]
        proba = model.predict_proba(feats)[0, 1]
        label = "FILTRO" if pred == 1 else "background"
        print(f"{str(f)[-60:]:<60} {label:<10} {proba:>14.3f}")


if __name__ == "__main__":
    main()
