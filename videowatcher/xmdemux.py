"""Convert Xiongmai ".h264" recordings into MP4.

The files a camera stores (and sends on download) are not a plain H.264 stream: each frame
is wrapped in a small private header that begins with 00 00 01 Fx.

    FC/FE  I-frame  16 bytes: media, fps, width/8, height/8, timestamp u32, length u32
    FD     P-frame   8 bytes: length u32
    FA     audio     8 bytes: codec, rate, length u16
    F9     info      8 bytes: type, n, length u16

`extract_video` strips those headers, keeps the Annex-B video payload and detects H.264
versus H.265 from the first parameter-set NAL, so ffmpeg can remux it without re-encoding.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

I_FRAMES = (0xFC, 0xFE)
P_FRAME = 0xFD
AUDIO = 0xFA
INFO = 0xF9
KNOWN = {*I_FRAMES, P_FRAME, AUDIO, INFO}


@dataclass
class VideoInfo:
    codec: str | None = None  # "h264" | "hevc"
    fps: int | None = None
    frames: int = 0
    skipped_bytes: int = 0


def _detect_codec(payload: bytes) -> str | None:
    i = payload.find(b"\x00\x00\x01")
    if i < 0 or i + 3 >= len(payload):
        return None
    nal = payload[i + 3]
    if (nal >> 1) & 0x3F in (32, 33, 34):  # HEVC VPS/SPS/PPS
        return "hevc"
    if nal & 0x1F in (7, 8, 5):  # H.264 SPS/PPS/IDR
        return "h264"
    return None


def extract_video(src: Path, dst: Path) -> VideoInfo:
    data = src.read_bytes()
    info = VideoInfo()
    pos = 0
    with open(dst, "wb") as out:
        while pos + 8 <= len(data):
            if data[pos:pos + 3] != b"\x00\x00\x01" or data[pos + 3] not in KNOWN:
                # Lost sync (truncated frame or unknown block): scan to the next frame header.
                nxt = _next_header(data, pos + 1)
                info.skipped_bytes += nxt - pos
                pos = nxt
                continue
            kind = data[pos + 3]
            if kind in I_FRAMES:
                if pos + 16 > len(data):
                    break
                _media, fps, _w, _h, _ts, length = struct.unpack_from("<BBBBII", data, pos + 4)
                start = pos + 16
                if fps and not info.fps:
                    info.fps = fps
                if info.codec is None:
                    info.codec = _detect_codec(data[start:start + 64])
            elif kind == P_FRAME:
                (length,) = struct.unpack_from("<I", data, pos + 4)
                start = pos + 8
            else:
                (length,) = struct.unpack_from("<H", data, pos + 6)
                start = pos + 8
            end = start + length
            if end > len(data):
                break
            if kind in I_FRAMES or kind == P_FRAME:
                out.write(data[start:end])
                info.frames += 1
            pos = end
    return info


def _next_header(data: bytes, start: int) -> int:
    pos = start
    while True:
        i = data.find(b"\x00\x00\x01", pos)
        if i < 0 or i + 3 >= len(data):
            return len(data)
        if data[i + 3] in KNOWN:
            return i
        pos = i + 1


def find_ffmpeg() -> str | None:
    configured = os.environ.get("VIDEOWATCHER_FFMPEG")
    if configured and Path(configured).is_file():
        return configured
    return shutil.which("ffmpeg")


def convert_to_mp4(src: Path, dst: Path, ffmpeg: str) -> VideoInfo:
    raw = dst.with_suffix(".es")
    try:
        info = extract_video(src, raw)
        if not info.frames or not info.codec:
            raise ValueError("no video frames found in recording")
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-fflags", "+genpts", "-r", str(info.fps or 25),
            "-f", info.codec, "-i", str(raw),
            "-c", "copy", "-movflags", "+faststart", str(dst),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {result.stderr.strip()[:500]}")
        return info
    finally:
        raw.unlink(missing_ok=True)
