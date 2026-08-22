"""Los dos trabajadores del modelo continuo.

  clasificador : arma clips de 5 s del stream y corre el detector sobre CADA
                 uno, sin huecos. Decide alerta/supresión. No toca la red.
  transporte   : sube clip + evento, notifica WhatsApp/IoT. Toda la red vive
                 acá; una subida lenta atrasa OTRAS subidas, jamás la escucha.

Entre ambos, una cola acotada con política explícita (R-1.3): si transporte se
atasca, el EVENTO se preserva (spool en disco) y solo el audio del clip se
descarta, contado y publicado. Un evento no se pierde por plomería (D-015).
"""

import logging
import queue
import threading
import time
import uuid
from datetime import datetime, timezone

from . import capture
from . import config as C
from . import detector
from . import health
from . import notify
from . import storage

log = logging.getLogger("oceankind")


def _level_bar(rms: float, threshold: float, width: int = 40) -> str:
    filled = int(min(rms / 0.3, 1.0) * width)
    bar = list("█" * filled + "░" * (width - filled))
    pos = int(min(threshold / 0.3, 1.0) * width)
    if pos < width:
        bar[pos] = "|"
    return "".join(bar)


class Pipeline:
    def __init__(self, iot_client=None):
        self.block_queue     = queue.Queue(maxsize=C.BLOCK_QUEUE_MAX)
        self.transport_queue = queue.Queue(maxsize=C.TRANSPORT_QUEUE_MAX)
        self.stop_event      = threading.Event()
        self.iot_client      = iot_client
        self.alert_count     = 0      # alertas NOTIFICADAS esta sesión
        self.last_rms        = 0.0
        self.last_peak_db    = -180.0
        self._last_alert     = 0.0    # solo lo toca el hilo clasificador
        self._last_archive   = 0.0
        self._threads: list[threading.Thread] = []

    # ─── ciclo de vida ───────────────────────────────────────────────────────

    def start(self) -> None:
        for name, fn in (("classify", self._classify_loop),
                         ("transport", self._transport_loop)):
            t = threading.Thread(target=fn, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self.stop_event.set()
        for t in self._threads:
            t.join(timeout=10)
        # Trabajos que quedaron en la cola: preservar el EVENTO en el spool.
        # El audio del clip se pierde (contado); el registro científico no.
        drained = 0
        while True:
            try:
                job = self.transport_queue.get_nowait()
            except queue.Empty:
                break
            if C.STORAGE_ENABLED:
                event = self._build_job_event(job, clip_uploaded=False)
                storage.spool_event(event, job["event_rel"])
            if job.get("clip") is not None:
                health.count_clips_dropped()
            drained += 1
        if drained:
            log.warning("cierre: %d trabajo/s de transporte preservados en el spool", drained)

    # ─── clasificador ────────────────────────────────────────────────────────

    def _classify_loop(self) -> None:
        assembler = capture.ClipAssembler(self.block_queue)
        log.info("Clasificador listo: ventanas de %.0f s, modo %s",
                 C.CAPTURE_SECONDS, C.CONFIG.snapshot()["detection_mode"].upper())
        while not self.stop_event.is_set():
            clip = assembler.next_clip(timeout=1.0)
            if clip is None:
                continue
            try:
                self._process_clip(clip)
            except Exception as exc:
                # Un clip malo no puede tumbar el hilo que decide alertas.
                log.error("error procesando clip (seguimos): %s", exc)

    def _process_clip(self, clip) -> None:
        captured_dt = datetime.now(timezone.utc)
        cfg = C.CONFIG.snapshot()
        rms, peak_db = capture.rms_and_peak(clip)
        self.last_rms, self.last_peak_db = rms, peak_db
        health.record_rms(rms)

        ml_result = {}
        if cfg["detection_mode"] in ("psd", "auto"):
            ml_result = detector.classify_samples(C.SAMPLE_RATE, clip, cfg)
        d = detector.decide(rms, ml_result, cfg)

        tag = (f"  psd={ml_result.get('label','?')}({ml_result.get('proba',0):.2f})"
               if ml_result else "")
        flag = " *** ALERTA ***" if d["alert"] else ""
        log.info("[%s] RMS=%.4f  %.1f dB%s%s",
                 _level_bar(rms, cfg["alert_threshold"]), rms, peak_db, tag, flag)

        if d["alert"]:
            now = time.time()
            notified = (now - self._last_alert) >= cfg["cooldown_s"]
            if notified:
                self._last_alert = now
            else:
                n = health.count_suppressed()
                log.info("  detección en cooldown — registrada como suprimida (%d en la sesión)", n)
            self._enqueue_detection(clip if notified else None, captured_dt,
                                    rms, peak_db, d, ml_result, suppressed=not notified)

        self._maybe_archive(clip, captured_dt)

    def _enqueue_detection(self, clip, captured_dt, rms, peak_db, decision,
                           ml_result, suppressed: bool) -> None:
        """El cooldown limita NOTIFICACIONES; el registro es sagrado (R-4.2,
        D-008). Suprimida = evento sin audio y sin WhatsApp."""
        event_id = str(uuid.uuid4())
        event_rel, clip_rel = storage.event_rel_paths(captured_dt, event_id)
        job = {
            "event_id":     event_id,
            "captured_iso": captured_dt.isoformat(),
            "event_rel":    event_rel,
            "clip_rel":     clip_rel,
            "clip":         clip,          # None en suprimidas
            "suppressed":   suppressed,
            "rms":          rms,
            "peak_db":      peak_db,
            "decision":     decision,
            "ml_result":    ml_result,
        }
        try:
            self.transport_queue.put_nowait(job)
        except queue.Full:
            # Política explícita: el evento va al spool YA (sin audio), el
            # clip se descarta contado. Nunca se pierde el registro.
            log.error("cola de transporte llena — evento %s directo al spool, clip descartado",
                      event_id[:8])
            if C.STORAGE_ENABLED:
                storage.spool_event(self._build_job_event(job, clip_uploaded=False),
                                    event_rel)
            if clip is not None:
                health.count_clips_dropped()

    def _maybe_archive(self, clip, captured_dt) -> None:
        """1 clip por minuto a la cola de archivo (análisis posterior), acotada
        (F-22) y con descartes contados. Escritura local pequeña, no red."""
        now = time.time()
        if (now - self._last_archive) < C.ARCHIVE_INTERVAL:
            return
        try:
            C.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            name = f"clip_{captured_dt.strftime('%Y-%m-%dT%H-%M-%S')}.wav"
            (C.ARCHIVE_DIR / name).write_bytes(storage.wav_bytes(clip))
            self._last_archive = now
            queue_files = sorted(C.ARCHIVE_DIR.glob("clip_*.wav"))
            if len(queue_files) > C.ARCHIVE_MAX_FILES:
                dropped = len(queue_files) - C.ARCHIVE_MAX_FILES
                for old in queue_files[:dropped]:
                    old.unlink(missing_ok=True)
                health.count_clips_dropped(dropped)
                log.warning("cola de archivo llena — %d clips viejos borrados sin subir", dropped)
        except OSError as exc:
            log.warning("no se pudo archivar clip: %s", exc)

    # ─── transporte ──────────────────────────────────────────────────────────

    def _build_job_event(self, job: dict, clip_uploaded: bool) -> dict:
        d = job["decision"]
        meta = {"decided_by": d["decided_by"],
                **({k: job["ml_result"].get(k) for k in ("pred", "proba", "label")}
                   if job["ml_result"] else {})}
        return storage.build_event(
            job["event_id"], job["captured_iso"], d["event_type"], d["detector"],
            d["score"], job["suppressed"], job["rms"], job["peak_db"],
            job["clip_rel"], clip_uploaded, meta)

    def _transport_loop(self) -> None:
        log.info("Transporte listo (cola máx %d)", C.TRANSPORT_QUEUE_MAX)
        while not self.stop_event.is_set():
            try:
                job = self.transport_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self._process_job(job)
            except Exception as exc:
                log.error("error en transporte (evento %s): %s", job.get("event_id", "?")[:8], exc)
                if C.STORAGE_ENABLED:
                    storage.spool_event(self._build_job_event(job, clip_uploaded=False),
                                        job["event_rel"])

    def _process_job(self, job: dict) -> None:
        # Cuenta CADA detección (también suprimidas) para el clúster de voz.
        notify.maybe_trigger_cluster_call()

        clip_uploaded = False
        # 1) Clip PRIMERO (F-13/R-4.5): notificar antes de subir produce links
        #    muertos permanentes.
        if job["clip"] is not None and C.STORAGE_ENABLED:
            log.info("  Subiendo clip...")
            clip_uploaded = storage.upload_bytes(job["clip_rel"],
                                                 storage.wav_bytes(job["clip"]), "audio/wav")
            if clip_uploaded:
                log.info("  → %s", job["clip_rel"])

        # 2) Registrar SIEMPRE (R-4.1): un blob por evento; si falla, al spool.
        if C.STORAGE_ENABLED:
            storage.write_event(self._build_job_event(job, clip_uploaded), job["event_rel"])

        # 3) Notificar DESPUÉS del upload, solo detecciones no suprimidas.
        if not job["suppressed"]:
            d = job["decision"]
            notify.send_whatsapp(job["rms"], job["peak_db"],
                                 job["clip_rel"] if clip_uploaded else None,
                                 ml_result=job["ml_result"] or None, label=d["label"])
            self.alert_count += 1
            if self.iot_client:
                try:
                    notify.send_iot_message(self.iot_client, job["rms"], job["peak_db"],
                                            msg_type="alert",
                                            audio_url=job["clip_rel"] if clip_uploaded else None,
                                            threshold=C.CONFIG.snapshot()["alert_threshold"])
                    log.info("  → Alerta enviada a IoT Hub")
                except Exception as exc:
                    log.warning("  Error enviando alerta IoT: %s", exc)
