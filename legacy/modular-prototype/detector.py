"""
OceanKind - STA/LTA Blast Detector + Frequency Analyzer

STA/LTA (Short-Term Average / Long-Term Average) is the standard algorithm
used in seismology for detecting sudden transients — it self-normalizes to
ambient noise levels, so it works whether the sea is quiet or rough.
"""

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class DetectionEvent:
    """Represents a confirmed blast detection."""
    timestamp: datetime
    channel: int
    duration_s: float
    peak_amplitude: float
    peak_sta_lta_ratio: float
    frequency_profile: dict = field(default_factory=dict)
    device_id: str = ""

    @property
    def is_probable_blast(self) -> bool:
        """True if frequency profile also looks like an explosion."""
        return self.frequency_profile.get("is_broadband", False)

    @property
    def event_type(self) -> str:
        return "probable_blast" if self.is_probable_blast else "loud_transient"


class STALTADetector:
    """
    Single-channel STA/LTA detector.

    How it works:
      STA = mean(energy) over a short window (e.g. 50ms)  — current loudness
      LTA = mean(energy) over a long window  (e.g.  5s)   — background level
      ratio = STA / LTA

      ratio > TRIGGER_RATIO  → event starts
      ratio < DETRIGGER_RATIO → event ends
      event duration within [MIN, MAX] → valid blast candidate
    """

    def __init__(self, config, channel: int = 0):
        self.config = config
        self.channel = channel
        sr = config.SAMPLE_RATE

        self._sta_buf = deque(maxlen=int(config.STA_WINDOW * sr))
        self._lta_buf = deque(maxlen=int(config.LTA_WINDOW * sr))

        # Keep audio samples for pre-trigger clip saving
        pre_samples = int(config.PRE_TRIGGER_SECONDS * sr)
        self._audio_buf = deque(maxlen=pre_samples)

        self._in_event = False
        self._event_start: Optional[datetime] = None
        self._peak_amp = 0.0
        self._peak_ratio = 0.0
        self._lta_min_fill = int(config.LTA_WINDOW * sr * 0.2)  # Need 20% of LTA filled first

    def process(self, samples: np.ndarray, timestamp: datetime) -> Optional[DetectionEvent]:
        """
        Feed one block of float32 audio samples.
        Returns a DetectionEvent when an event closes (de-triggers), else None.
        """
        event = None

        for s in samples:
            energy = float(s * s)
            self._sta_buf.append(energy)
            self._lta_buf.append(energy)
            self._audio_buf.append(float(s))

            # Wait until LTA is meaningfully filled
            if len(self._lta_buf) < self._lta_min_fill:
                continue

            lta = float(np.mean(self._lta_buf))
            if lta < 1e-12:
                continue  # Silence / disconnected hydrophone

            sta = float(np.mean(self._sta_buf))
            ratio = sta / lta

            if not self._in_event:
                if ratio >= self.config.TRIGGER_RATIO:
                    self._in_event = True
                    self._event_start = timestamp
                    self._peak_amp = float(np.sqrt(sta))
                    self._peak_ratio = ratio
                    logger.debug(f"Ch{self.channel} TRIGGER ON  ratio={ratio:.2f}")
            else:
                # Track peak while in event
                amp = float(np.sqrt(sta))
                if amp > self._peak_amp:
                    self._peak_amp = amp
                if ratio > self._peak_ratio:
                    self._peak_ratio = ratio

                if ratio <= self.config.DETRIGGER_RATIO:
                    duration = (timestamp - self._event_start).total_seconds()
                    self._in_event = False
                    logger.debug(f"Ch{self.channel} TRIGGER OFF duration={duration:.3f}s")

                    if self.config.MIN_EVENT_DURATION <= duration <= self.config.MAX_EVENT_DURATION:
                        event = DetectionEvent(
                            timestamp=self._event_start,
                            channel=self.channel,
                            duration_s=round(duration, 4),
                            peak_amplitude=round(self._peak_amp, 6),
                            peak_sta_lta_ratio=round(self._peak_ratio, 2),
                            device_id=self.config.DEVICE_ID,
                        )
                    else:
                        logger.debug(
                            f"Ch{self.channel} event rejected: duration={duration:.3f}s "
                            f"outside [{self.config.MIN_EVENT_DURATION}, {self.config.MAX_EVENT_DURATION}]"
                        )
        return event

    @property
    def pre_trigger_audio(self) -> np.ndarray:
        """Returns buffered audio from before the last trigger (for clip saving)."""
        return np.array(self._audio_buf, dtype=np.float32)

    def reset(self):
        self._sta_buf.clear()
        self._lta_buf.clear()
        self._in_event = False


class FrequencyAnalyzer:
    """
    Classifies whether a detected event looks like an explosion
    by checking if its frequency spectrum is broadband (energy spread
    across many bins) vs tonal (engine hum, rain, etc.).

    Key metrics:
      - spectral_flatness: Wiener entropy — near 1 = white-noise-like (broadband)
      - broadband_score: fraction of bins above median energy
      - peak_freq: dominant frequency in Hz
    """

    FFT_SIZE = 4096

    def __init__(self, config):
        self.config = config
        self._freqs = np.fft.rfftfreq(self.FFT_SIZE, 1.0 / config.SAMPLE_RATE)
        self._band_mask = (
            (self._freqs >= config.MIN_FREQ) &
            (self._freqs <= config.MAX_FREQ)
        )

    def analyze(self, samples: np.ndarray) -> dict:
        """
        Returns a dict with spectral features and an is_broadband flag.
        """
        n = len(samples)
        if n < self.FFT_SIZE:
            samples = np.pad(samples, (0, self.FFT_SIZE - n))
        else:
            samples = samples[:self.FFT_SIZE]

        # Apply Hanning window to reduce spectral leakage
        window = np.hanning(self.FFT_SIZE)
        spectrum = np.abs(np.fft.rfft(samples * window))

        band = spectrum[self._band_mask]
        band_freqs = self._freqs[self._band_mask]

        if len(band) == 0:
            return {"is_broadband": False, "broadband_score": 0.0,
                    "spectral_flatness": 0.0, "peak_freq_hz": 0.0}

        # Spectral flatness (Wiener entropy): geometric_mean / arithmetic_mean
        log_mean = np.mean(np.log(band + 1e-10))
        geo_mean = np.exp(log_mean)
        arith_mean = float(np.mean(band))
        flatness = float(geo_mean / (arith_mean + 1e-10))

        # Broadband score: fraction of bins above 1.5× median
        median = float(np.median(band))
        broadband_score = float(np.mean(band > median * 1.5))

        # Peak frequency
        peak_idx = int(np.argmax(band))
        peak_freq = float(band_freqs[peak_idx])

        is_broadband = (
            flatness > 0.08 and
            broadband_score > self.config.BROADBAND_THRESHOLD
        )

        return {
            "is_broadband": bool(is_broadband),
            "broadband_score": round(broadband_score, 3),
            "spectral_flatness": round(flatness, 4),
            "peak_freq_hz": round(peak_freq, 1),
        }
