"""Background download queue. Files for one camera download one at a time (VMS Pro does the
same), different cameras run in parallel."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .backends import backend_for
from .dvrip import RecordFile
from .store import DOWNLOAD_DIR, Camera, safe_name
from .xmdemux import convert_to_mp4, find_ffmpeg


@dataclass
class Job:
    camera: Camera
    record: RecordFile
    want_mp4: bool
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    state: str = "queued"  # queued | downloading | converting | done | error | cancelled
    received: int = 0
    error: str = ""
    raw_path: Path | None = None
    mp4_path: Path | None = None
    created: float = field(default_factory=time.time)
    cancel: threading.Event = field(default_factory=threading.Event)

    def public(self) -> dict:
        expected = self.record.size_bytes
        return {
            "id": self.id,
            "camera_id": self.camera.id,
            "camera": self.camera.name,
            "file_name": self.record.file_name,
            "begin": self.record.begin,
            "end": self.record.end,
            "state": self.state,
            "received": self.received,
            "expected": expected,
            "progress": min(100, round(100 * self.received / expected)) if expected else None,
            "error": self.error,
            "has_raw": bool(self.raw_path and self.raw_path.exists()),
            "has_mp4": bool(self.mp4_path and self.mp4_path.exists()),
        }


class JobManager:
    def __init__(self, download_dir: Path = DOWNLOAD_DIR):
        self.download_dir = download_dir
        self.jobs: dict[str, Job] = {}
        self._camera_locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    def submit(self, camera: Camera, record: RecordFile, want_mp4: bool) -> Job:
        job = Job(camera, record, want_mp4)
        with self._lock:
            self.jobs[job.id] = job
            cam_lock = self._camera_locks.setdefault(camera.id, threading.Lock())
        threading.Thread(target=self._run, args=(job, cam_lock), daemon=True).start()
        return job

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job or job.state in ("done", "error", "cancelled"):
            return False
        job.cancel.set()
        return True

    def _target(self, job: Job) -> Path:
        rec = job.record
        day = rec.begin[:10]
        stem = f"ch{rec.channel}_{rec.begin.replace(':', '').replace(' ', '_')}_{rec.end[11:].replace(':', '')}"
        return self.download_dir / safe_name(job.camera.name) / day / f"{safe_name(stem)}.h264"

    def _run(self, job: Job, cam_lock: threading.Lock) -> None:
        with cam_lock:
            if job.cancel.is_set():
                job.state = "cancelled"
                return
            target = self._target(job)
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_suffix(".part")
            job.state = "downloading"
            try:
                with open(partial, "wb") as sink:
                    backend_for(job.camera).download(
                        job.camera, job.record, sink,
                        on_progress=lambda n: setattr(job, "received", n),
                        cancel=job.cancel,
                    )
                partial.replace(target)
                job.raw_path = target
                if job.want_mp4:
                    ffmpeg = find_ffmpeg()
                    if not ffmpeg:
                        job.error = "saved .h264 only: ffmpeg not found (set VIDEOWATCHER_FFMPEG)"
                    else:
                        job.state = "converting"
                        mp4 = target.with_suffix(".mp4")
                        try:
                            convert_to_mp4(target, mp4, ffmpeg)
                            job.mp4_path = mp4
                        except (ValueError, RuntimeError, OSError) as exc:
                            job.error = f"saved .h264 only: MP4 conversion failed: {exc}"
                job.state = "done"
            except Exception as exc:  # surfaced to the UI via job.error
                partial.unlink(missing_ok=True)
                job.state = "cancelled" if job.cancel.is_set() else "error"
                job.error = str(exc) or exc.__class__.__name__
