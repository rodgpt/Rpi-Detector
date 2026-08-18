"""
OceanKind - Audio Clip Saver

Saves WAV clips around each detected event:
  [PRE_TRIGGER_SECONDS of audio] + [POST_TRIGGER_SECONDS of audio]

These clips are invaluable for:
  - Manual verification of detections
  - Building a labelled dataset for future ML models
  - Tuning the STA/LTA thresholds
"""

import logging
import os
from datetime import datetime
from collections import deque

import numpy as np
import scipy.io.wavfile as wavfile

logger = logging.getLogger(__name__)


class ClipSaver:

    def __init__(self, config):
        self.config = config
        os.makedirs(config.AUDIO_CLIPS_DIR, exist_ok=True)
        self._post_samples = int(config.POST_TRIGGER_SECONDS * config.SAMPLE_RATE)

        # Per-channel post-trigger collection
        self._collecting: dict[int, list] = {}

    def notify_detection(self, channel: int, pre_trigger_audio: np.ndarray):
        """
        Call immediately when a detection event opens (trigger ON).
        pre_trigger_audio: the buffered samples from before the trigger.
        """
        self._collecting[channel] = [pre_trigger_audio.copy()]

    def feed(self, channel: int, samples: np.ndarray) -> bool:
        """
        Feed post-trigger audio. Returns True when the clip is complete and saved.
        """
        if channel not in self._collecting:
            return False

        self._collecting[channel].append(samples.copy())
        total = sum(len(b) for b in self._collecting[channel])

        if total >= self._post_samples:
            self._save(channel)
            del self._collecting[channel]
            return True
        return False

    def _save(self, channel: int):
        try:
            audio = np.concatenate(self._collecting[channel]).astype(np.float32)
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            fname = os.path.join(
                self.config.AUDIO_CLIPS_DIR,
                f"blast_{self.config.DEVICE_ID}_{ts}_ch{channel}.wav",
            )
            # scipy wavfile only supports float32 if we write it as such
            wavfile.write(fname, self.config.SAMPLE_RATE, audio)
            logger.info(f"Clip saved: {fname} ({len(audio)/self.config.SAMPLE_RATE:.2f}s)")
        except Exception as exc:
            logger.error(f"Failed to save clip for ch{channel}: {exc}")
