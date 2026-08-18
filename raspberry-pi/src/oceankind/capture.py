"""Captura continua: el hidrófono nunca deja de escuchar (R-1.1).

Un stream de sounddevice (PortAudio) empuja bloques de ~0.1 s a una cola
acotada desde su callback. El callback NO toca red, disco, locks lentos ni CPU
pesada — copia el bloque, lo encola, cuenta lo que pierde. Todo lo demás pasa
en otros hilos.

Portado de legacy/modular-prototype/audio_capture.py (D-006), con la detección
del dispositivo POR NOMBRE (F-15): un índice ALSA cambia con la re-enumeración
USB; un nombre no.

Fuente sintética (R-9.4): mismo contrato de bloques, sin hardware. Patrones
tone|noise|impulse|silence, con time_scale para tests acelerados.
"""

import logging
import queue
import threading
import time

import numpy as np

from . import config as C
from . import health

log = logging.getLogger("oceankind")


class AudioCapture:
    """Stream continuo del dispositivo real → cola de bloques int16."""

    def __init__(self, block_queue: queue.Queue):
        self._queue = block_queue
        self._stream = None
        self._dropped = 0

    @staticmethod
    def list_devices() -> str:
        import sounddevice as sd  # noqa: PLC0415
        lines = ["Dispositivos de entrada disponibles:"]
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                lines.append(f"  [{i:2d}] {d['name']}  ({d['max_input_channels']}ch in)")
        return "\n".join(lines)

    @staticmethod
    def find_device() -> int | None:
        """Primer dispositivo de entrada cuyo nombre contenga alguno de los
        substrings de OCEANKIND_AUDIO_DEVICE_NAME. None = default del sistema."""
        import sounddevice as sd  # noqa: PLC0415
        hints = [h.strip().lower() for h in C.AUDIO_DEVICE_NAME.split(",") if h.strip()]
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] < 1:
                continue
            name = d["name"].lower()
            if any(h in name for h in hints):
                log.info("Dispositivo de audio detectado por nombre: '%s' (índice %d)", d["name"], i)
                return i
        log.warning("Ningún dispositivo coincide con %r — usando la entrada default", C.AUDIO_DEVICE_NAME)
        return None

    def start(self) -> None:
        import sounddevice as sd  # noqa: PLC0415
        device_idx = self.find_device()
        log.info(self.list_devices())
        self._stream = sd.InputStream(
            device=device_idx,
            channels=C.CHANNELS,
            samplerate=C.SAMPLE_RATE,
            blocksize=C.BLOCK_FRAMES,
            dtype="int16",
            callback=self._callback,
            latency="low",
        )
        self._stream.start()
        health.mark_capture_started()
        log.info("Captura continua iniciada | device=%s | %d Hz | %d ch | bloques de %d frames",
                 "default" if device_idx is None else device_idx,
                 C.SAMPLE_RATE, C.CHANNELS, C.BLOCK_FRAMES)

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        log.info("Captura detenida (bloques perdidos: %d)", self._dropped)

    def _callback(self, indata, frames, time_info, status) -> None:
        # En el callback: copiar, encolar, contar. Nada más. (R-1.1)
        if status and status.input_overflow:
            health.count_capture_overflow()
        try:
            self._queue.put_nowait(indata.copy())
        except queue.Full:
            # Política explícita (R-1.3): descartar el bloque MÁS VIEJO y
            # conservar el nuevo — detección cercana al presente. Contado.
            self._dropped += 1
            health.count_capture_overflow()
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(indata.copy())
            except (queue.Empty, queue.Full):
                pass
            if self._dropped % 100 == 1:
                log.warning("Cola de bloques llena — %d bloques descartados. "
                            "El clasificador no da abasto.", self._dropped)


class SyntheticSource:
    """Genera bloques como si fuera el hardware. Para banco y tests (R-9.4).

    Patrones: tone (motor: 120+240 Hz), noise, impulse (ráfaga <1s por clip),
    silence. time_scale>1 acelera la generación para tests.
    """

    def __init__(self, block_queue: queue.Queue, pattern: str = "tone",
                 time_scale: float = 1.0):
        self._queue = block_queue
        self._pattern = pattern
        self._scale = max(0.01, time_scale)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._rng = np.random.default_rng(7)
        self._frame_pos = 0

    def _block(self) -> np.ndarray:
        n = C.BLOCK_FRAMES
        t = (np.arange(n) + self._frame_pos) / C.SAMPLE_RATE
        self._frame_pos += n
        if self._pattern == "tone":
            mono = (0.30 * np.sin(2 * np.pi * 120 * t)
                    + 0.25 * np.sin(2 * np.pi * 240 * t)
                    + 0.01 * self._rng.standard_normal(n))
        elif self._pattern == "noise":
            mono = 0.05 * self._rng.standard_normal(n)
        elif self._pattern == "impulse":
            mono = 0.005 * self._rng.standard_normal(n)
            # una ráfaga de 0.25 s al inicio de cada ventana de 5 s
            clip_len = int(C.CAPTURE_SECONDS * C.SAMPLE_RATE)
            pos = self._frame_pos % clip_len
            if pos < int(0.25 * C.SAMPLE_RATE):
                mono += 0.9 * self._rng.standard_normal(n)
        else:  # silence
            mono = np.zeros(n)
        pcm = (np.clip(mono, -1, 1) * 32000).astype(np.int16)
        return np.column_stack([pcm] * C.CHANNELS)

    def start(self) -> None:
        health.mark_capture_started()
        self._thread = threading.Thread(target=self._run, name="synthetic-source", daemon=True)
        self._thread.start()
        log.info("Fuente sintética iniciada (patrón=%s, escala=%.0fx)", self._pattern, self._scale)

    def _run(self) -> None:
        block_period = (C.BLOCK_FRAMES / C.SAMPLE_RATE) / self._scale
        next_t = time.monotonic()
        while not self._stop.is_set():
            try:
                self._queue.put(self._block(), timeout=1.0)
            except queue.Full:
                health.count_capture_overflow()
            next_t += block_period
            delay = next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)


def make_source(block_queue: queue.Queue, time_scale: float = 1.0):
    """Fábrica según OCEANKIND_AUDIO_SOURCE: device | synthetic:<patrón>."""
    if C.AUDIO_SOURCE.startswith("synthetic"):
        pattern = (C.AUDIO_SOURCE.split(":", 1) + ["tone"])[1] or "tone"
        return SyntheticSource(block_queue, pattern=pattern, time_scale=time_scale)
    return AudioCapture(block_queue)


class ClipAssembler:
    """Arma ventanas de CAPTURE_SECONDS a partir del stream de bloques.

    Corre en el hilo clasificador (el consumidor de la cola): la captura
    entrega bloques crudos y no espera a nadie.

    Ventanas SOLAPADAS: `window_hop_s` (afinable por config remota) es el paso
    entre ventanas. 5.0 = pegadas sin solape (comportamiento calibrado). Con
    hop h < 5, tras emitir una ventana se retienen los últimos 5−h segundos,
    así un evento corto en el borde entre dos ventanas cae entero en alguna
    (garantizado hasta 5−h s de duración). Costo: clasificación × (5/h).
    """

    def __init__(self, block_queue: queue.Queue):
        self._queue = block_queue
        self._chunks: list = []
        self._frames = 0
        self._target = int(C.CAPTURE_SECONDS * C.SAMPLE_RATE)

    def next_clip(self, timeout: float = 1.0) -> np.ndarray | None:
        """Bloquea hasta armar la próxima ventana [target, canales], o None si
        no llegó nada en `timeout` (el llamador chequea el stop y sigue)."""
        try:
            block = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        self._chunks.append(block)
        self._frames += len(block)
        health.record_frames(len(block))
        if self._frames < self._target:
            return None
        data = np.concatenate(self._chunks, axis=0)
        clip = data[:self._target]
        # El hop se lee en cada emisión: cambiarlo por config remota surte
        # efecto en la ventana siguiente, sin reinicio (R-3.6).
        hop_frames = int(C.CONFIG.snapshot()["window_hop_s"] * C.SAMPLE_RATE)
        rest = data[max(1, hop_frames):]
        self._chunks = [rest] if len(rest) else []
        self._frames = len(rest)
        return clip


def rms_and_peak(clip: np.ndarray) -> tuple[float, float]:
    """RMS normalizado 0..1 y 'peak' en dBFS (misma métrica que v1: RMS en dB)."""
    samples = clip.astype(np.float32)
    if samples.size == 0:
        return 0.0, -180.0
    rms = float(np.sqrt(np.mean(samples ** 2))) / 32768.0
    peak_db = float(20 * np.log10(rms + 1e-9))
    if not np.isfinite(peak_db):
        peak_db = -180.0
    return rms, peak_db
