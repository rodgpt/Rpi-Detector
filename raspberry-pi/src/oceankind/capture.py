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

    Patrones: tone (motor: 120+240 Hz, dispara SIEMPRE), noise (nunca dispara),
    impulse (ráfaga <1s por clip, dispara siempre), silence (nunca dispara),
    sporadic (silencio de fondo con ráfagas de tono espaciadas al azar —
    dispara el detector real de vez en cuando, sin ser un metrónomo; ver
    OCEANKIND_SYNTHETIC_SPORADIC_MEAN_S). time_scale>1 acelera la generación
    para tests.
    """

    def __init__(self, block_queue: queue.Queue, pattern: str = "tone",
                 time_scale: float = 1.0):
        self._queue = block_queue
        # Segunda línea de defensa: la validación de arranque ya rechaza los
        # patrones desconocidos, pero esta clase también se construye desde los
        # tests. Silencio por accidente es indistinguible de un hidrófono
        # muerto, así que se grita en vez de asumirlo.
        if pattern not in C.SYNTHETIC_PATTERNS:
            raise ValueError(
                f"patrón sintético {pattern!r} desconocido "
                f"(válidos: {'|'.join(C.SYNTHETIC_PATTERNS)})")
        self._pattern = pattern
        self._scale = max(0.01, time_scale)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._rng = np.random.default_rng(7)
        self._frame_pos = 0
        # "sporadic": una moneda por ventana de 5 s, no por bloque de 0.1 s —
        # si el sorteo fuera por bloque, un evento casi nunca llenaría una
        # ventana completa y el detector (que mira la ventana entera) lo
        # vería diluido, igual que F-XX diluye un blast corto repartido entre
        # ventanas. p por ventana ≈ CAPTURE_SECONDS / MEAN_S: aproximación
        # lineal de un proceso de Poisson, válida mientras MEAN_S » 5 s
        # (con el default de 720 s el error frente a 1-e^(-5/720) es ~0.03%).
        self._sporadic_window_idx = -1
        self._sporadic_is_event = False
        self._sporadic_p = min(1.0, C.CAPTURE_SECONDS
                               / max(C.CAPTURE_SECONDS, C.SYNTHETIC_SPORADIC_MEAN_S))

    def _block(self) -> np.ndarray:
        n = C.BLOCK_FRAMES
        block_start = self._frame_pos    # posición de ESTA muestra, antes de avanzar
        t = (np.arange(n) + block_start) / C.SAMPLE_RATE
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
        elif self._pattern == "sporadic":
            # Silencio (ruido de fondo) casi siempre; la ventana entera se
            # vuelve tono cuando el sorteo de esta ventana sale evento. El
            # sorteo se hace UNA vez al cruzar a una ventana nueva, no en cada
            # bloque, para que la ventana completa quede consistente — así el
            # clasificador real ve exactamente lo que vería con un blast real.
            # OJO: el índice de ventana se calcula con block_start (inicio de
            # ESTE bloque), no con self._frame_pos ya avanzado — con el valor
            # avanzado el sorteo se adelantaba un bloque, contaminando el
            # último bloque de la ventana saliente con la decisión de la
            # entrante (encontrado por simulación, 2026-09-22).
            clip_len = int(C.CAPTURE_SECONDS * C.SAMPLE_RATE)
            window_idx = block_start // clip_len
            if window_idx != self._sporadic_window_idx:
                self._sporadic_window_idx = window_idx
                self._sporadic_is_event = self._rng.random() < self._sporadic_p
            if self._sporadic_is_event:
                mono = (0.30 * np.sin(2 * np.pi * 120 * t)
                        + 0.25 * np.sin(2 * np.pi * 240 * t)
                        + 0.01 * self._rng.standard_normal(n))
            else:
                mono = 0.05 * self._rng.standard_normal(n)
        else:  # silence — el único patrón restante; el resto ya se rechazó
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
        # Mismo parser que usa validate_startup_config: si llegamos aquí el
        # patrón ya está validado contra C.SYNTHETIC_PATTERNS.
        return SyntheticSource(block_queue, pattern=C.synthetic_pattern(),
                               time_scale=time_scale)
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
