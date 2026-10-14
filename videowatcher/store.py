"""JSON-file persistence for cameras and app users, kept under the data directory."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Portable build (PyInstaller): keep data next to videoWatcher.exe, not in the temp bundle.
APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("VIDEOWATCHER_DATA", APP_DIR / "data"))
DOWNLOAD_DIR = Path(os.environ.get("VIDEOWATCHER_DOWNLOADS", APP_DIR / "downloads"))


def _atomic_write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


@dataclass
class Camera:
    name: str
    host: str = ""
    port: int = 34567
    username: str = "admin"
    password: str = ""
    channels: int = 1
    transport: str = "lan"  # "lan" | "p2p"
    serial: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def public(self) -> dict:
        data = asdict(self)
        data.pop("password")
        data["has_password"] = bool(self.password)
        return data


class CameraStore:
    def __init__(self, path: Path | None = None):
        self.path = path or DATA_DIR / "cameras.json"
        self._lock = threading.Lock()

    def all(self) -> list[Camera]:
        return [Camera(**c) for c in _read(self.path, [])]

    def get(self, cam_id: str) -> Camera | None:
        return next((c for c in self.all() if c.id == cam_id), None)

    def save(self, cam: Camera) -> Camera:
        with self._lock:
            cams = [c for c in self.all() if c.id != cam.id] + [cam]
            _atomic_write(self.path, [asdict(c) for c in cams])
        return cam

    def delete(self, cam_id: str) -> bool:
        with self._lock:
            cams = self.all()
            kept = [c for c in cams if c.id != cam_id]
            _atomic_write(self.path, [asdict(c) for c in kept])
        return len(kept) != len(cams)


def safe_name(text: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", text).strip("._") or "file"
