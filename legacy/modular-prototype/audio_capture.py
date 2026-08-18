"""
OceanKind - Audio Capture

Captures audio from the HifiBerry ADC Pro (or any ALSA device) using
sounddevice (PortAudio wrapper). Audio blocks are pushed to a thread-safe
queue consumed by the main detection loop.
"""

import logging
import queue
import threading
from datetime import datetime, timezone
from typing import Optional, Tuple

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)

AudioBlock = Tuple[np.ndarray, datetime]  # (samples [N x channels], utc_timestamp)


class AudioCapture:

    def __init__(self, config):
        self.config = config
        self._queue: queue.Queue[AudioBlock] = queue.Queue(maxsize=200)
        self._stream: Optional[sd.InputStream] = None
        self._dropped_blocks = 0

    # ─── Device Discovery ─────────────────────────────────────────────────

    @staticmethod
    def list_devices() -> str:
        """Return a formatted string of all available audio input devices."""
        devices = sd.query_devices()
        lines = ["Available audio input devices:"]
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0:
                lines.append(f"  [{i:2d}] {d['name']}  ({d['max_input_channels']}ch in)")
        return "\n".join(lines)

    def find_hifiberry_device(self) -> Optional[int]:
        """
        Auto-detect a HifiBerry ADC device by name.
        Returns device index or None if not found.
        """
        for i, d in enumerate(sd.query_devices()):
            name = d["name"].lower()
            if d["max_input_channels"] >= 1 and (
                "hifiberry" in name or
                "sndrpihifiberry" in name or
                "dacplusadc" in name
            ):
                logger.info(f"HifiBerry ADC detected: '{d['name']}' (device index {i})")
                return i
        logger.warning("HifiBerry ADC not found — falling back to default input device")
        return None

    # ─── Stream Control ───────────────────────────────────────────────────

    def start(self):
        device_idx = self.find_hifiberry_device()

        logger.info(self.list_devices())

        self._stream = sd.InputStream(
            device=device_idx,
            channels=self.config.CHANNELS,
            samplerate=self.config.SAMPLE_RATE,
            blocksize=self.config.BLOCK_SIZE,
            dtype="float32",
            callback=self._callback,
            latency="low",
        )
        self._stream.start()
        logger.info(
            f"Audio stream started | device={'default' if device_idx is None else device_idx} | "
            f"{self.config.SAMPLE_RATE}Hz | {self.config.CHANNELS}ch"
        )

    def stop(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        logger.info(f"Audio stream stopped (total dropped blocks: {self._dropped_blocks})")

    # ─── Queue Interface ──────────────────────────────────────────────────

    def get_block(self, timeout: float = 1.0) -> AudioBlock:
        """
        Blocking get of the next audio block.
        Returns (None, None) on timeout — caller should check and continue.
        """
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None, None

    # ─── Internal Callback ────────────────────────────────────────────────

    def _callback(self, indata: np.ndarray, frames: int, time_info, status):
        if status:
            logger.warning(f"PortAudio status: {status}")

        ts = datetime.now(timezone.utc)
        try:
            self._queue.put_nowait((indata.copy(), ts))
        except queue.Full:
            self._dropped_blocks += 1
            if self._dropped_blocks % 100 == 1:
                logger.warning(
                    f"Audio queue full — {self._dropped_blocks} blocks dropped. "
                    "Detection loop may be too slow."
                )
