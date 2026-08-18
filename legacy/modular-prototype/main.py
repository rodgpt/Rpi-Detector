#!/usr/bin/env python3
"""
OceanKind - Bomb Fishing Detector
──────────────────────────────────
Hardware: Raspberry Pi 4 + HifiBerry ADC Pro + Aquarian H5 Hydrophones
          Bias-T sub-circuit (9V via MT3608 boost converter on coax)

Run:
    python main.py

Stop:
    Ctrl+C  (or SIGTERM from systemd)
"""

import logging
import os
import signal
import sys
from datetime import datetime

import numpy as np

from config import Config
from audio_capture import AudioCapture
from detector import STALTADetector, FrequencyAnalyzer
from alert import AlertSender
from clip_saver import ClipSaver


# ─── Logging Setup ────────────────────────────────────────────────────────────

def setup_logging(config: Config):
    os.makedirs(config.LOG_DIR, exist_ok=True)
    log_file = os.path.join(config.LOG_DIR, f"oceankind_{datetime.utcnow():%Y%m%d}.log")

    fmt = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)

    # Reduce noise from sounddevice internals
    logging.getLogger("sounddevice").setLevel(logging.WARNING)


# ─── Main Application ─────────────────────────────────────────────────────────

class OceanKindApp:

    def __init__(self):
        self.config = Config()
        setup_logging(self.config)
        self.log = logging.getLogger("main")

        self.capture = AudioCapture(self.config)
        self.detectors = [
            STALTADetector(self.config, channel=ch)
            for ch in range(self.config.CHANNELS)
        ]
        self.freq_analyzer = FrequencyAnalyzer(self.config)
        self.alerts = AlertSender(self.config)
        self.clips = ClipSaver(self.config) if self.config.SAVE_AUDIO_CLIPS else None

        self._running = False
        self._blocks_total = 0
        self._detections_total = 0

    def start(self):
        self._print_banner()
        self._running = True

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        self.capture.start()
        self.log.info("Listening for underwater blasts...")

        try:
            self._loop()
        finally:
            self._shutdown()

    def _loop(self):
        HEARTBEAT_BLOCKS = int(60 * self.config.SAMPLE_RATE / self.config.BLOCK_SIZE)

        while self._running:
            block, ts = self.capture.get_block(timeout=1.0)
            if block is None:
                continue

            self._blocks_total += 1

            # Heartbeat log every ~60 seconds
            if self._blocks_total % HEARTBEAT_BLOCKS == 0:
                self.log.info(
                    f"Heartbeat | blocks={self._blocks_total} "
                    f"detections={self._detections_total}"
                )

            for ch in range(self.config.CHANNELS):
                ch_audio = block[:, ch] if block.ndim > 1 else block

                # Feed post-trigger audio to clip saver
                if self.clips:
                    self.clips.feed(ch, ch_audio)

                # Run STA/LTA
                event = self.detectors[ch].process(ch_audio, ts)

                if event is not None:
                    # Frequency analysis on the triggering block
                    freq = self.freq_analyzer.analyze(ch_audio)
                    event.frequency_profile = freq

                    # Send alert
                    self.alerts.send(event, freq)
                    self._detections_total += 1

                    # Start saving clip (pre + post trigger)
                    if self.clips:
                        pre = self.detectors[ch].pre_trigger_audio
                        self.clips.notify_detection(ch, pre)

    def _shutdown(self):
        self.log.info("Shutting down...")
        self.capture.stop()
        self.alerts.shutdown()
        self.log.info(
            f"Session summary: {self._blocks_total} blocks processed, "
            f"{self._detections_total} detections."
        )

    def _handle_signal(self, signum, frame):
        self.log.info(f"Signal {signum} received")
        self._running = False

    def _print_banner(self):
        cfg = self.config
        banner = f"""
╔══════════════════════════════════════════════════════╗
║           OceanKind — Bomb Fishing Detector          ║
╠══════════════════════════════════════════════════════╣
║  Device ID : {cfg.DEVICE_ID:<39}║
║  Location  : {cfg.LATITUDE:.4f}, {cfg.LONGITUDE:.4f}{'':<22}║
║  Channels  : {cfg.CHANNELS:<39}║
║  Sample rate: {cfg.SAMPLE_RATE} Hz{'':<33}║
║  STA window: {cfg.STA_WINDOW*1000:.0f} ms   LTA window: {cfg.LTA_WINDOW:.0f} s{'':<14}║
║  Trigger ratio: {cfg.TRIGGER_RATIO}   De-trigger: {cfg.DETRIGGER_RATIO}{'':<15}║
║  Alert method: {cfg.ALERT_METHOD:<37}║
╚══════════════════════════════════════════════════════╝
"""
        self.log.info(banner)


if __name__ == "__main__":
    OceanKindApp().start()
