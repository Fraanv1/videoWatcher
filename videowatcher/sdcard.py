"""Convert recordings copied from (or read directly off) a camera's microSD card.

Originals are only read, never modified. Every source file is listed with its SHA-256 in
`hashes_sha256.csv` so copies can later be shown to be identical to the card.
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from .store import safe_name
from .xmdemux import convert_to_mp4, find_ffmpeg


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def convert_folder(src: Path, out: Path, log=print) -> int:
    """Convert every .h264 under `src` into MP4 under `out`. Returns the number of failures."""
    files = sorted(p for p in src.rglob("*") if p.is_file() and p.suffix.lower() in (".h264", ".h265"))
    if not files:
        log(f"No .h264 recordings found under {src}")
        return 0
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        log("ffmpeg not found: only hashes will be computed (set VIDEOWATCHER_FFMPEG)")
    out.mkdir(parents=True, exist_ok=True)
    failures = 0
    with open(out / "hashes_sha256.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["source_file", "bytes", "sha256", "mp4", "result"])
        for i, path in enumerate(files, 1):
            rel = path.relative_to(src)
            digest = sha256_of(path)
            mp4 = out / Path(*[safe_name(part) for part in rel.with_suffix(".mp4").parts])
            result = "hash only"
            if ffmpeg and path.stat().st_size:
                mp4.parent.mkdir(parents=True, exist_ok=True)
                try:
                    info = convert_to_mp4(path, mp4, ffmpeg)
                    result = f"ok ({info.codec}, {info.fps or '?'} fps)"
                except (ValueError, RuntimeError, OSError) as exc:
                    result = f"error: {exc}"
                    failures += 1
            elif not path.stat().st_size:
                result = "empty file"
            writer.writerow([str(rel), path.stat().st_size, digest, str(mp4.relative_to(out)) if mp4.exists() else "", result])
            log(f"[{i}/{len(files)}] {rel} -> {result}")
    log(f"Done. Hash list: {out / 'hashes_sha256.csv'}")
    return failures
