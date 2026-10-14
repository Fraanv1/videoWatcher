"""In-process fake Xiongmai camera speaking enough DVRIP for tests: login, file query with
64-entry pages, and download-by-name streaming XM-framed video."""

from __future__ import annotations

import json
import socketserver
import struct
import threading

from videowatcher.dvrip import (
    DOWNLOAD_DATA, FILE_QUERY, HEADER, LOGIN, LOGOUT, PLAYBACK, PLAYBACK_CLAIM, SYSINFO, sofia_hash,
)

HEVC_VPS = b"\x00\x00\x00\x01\x40\x01\x0c\x01\xff\xff"


def xm_stream(frames: int = 30, fps: int = 15) -> bytes:
    out = bytearray()
    for i in range(frames):
        if i % 10 == 0:
            body = HEVC_VPS + b"\x00\x00\x00\x01\x26\x01" + bytes([i]) * 200
            out += b"\x00\x00\x01\xfc" + struct.pack("<BBBBII", 2, fps, 240, 135, 0, len(body)) + body
        else:
            body = b"\x00\x00\x00\x01\x02\x01" + bytes([i]) * 80
            out += b"\x00\x00\x01\xfd" + struct.pack("<I", len(body)) + body
        audio = b"\xd5" * 160
        out += b"\x00\x00\x01\xfa" + struct.pack("<BBH", 0x0E, 2, len(audio)) + audio
    return bytes(out)


def make_files(day: str, count: int) -> list[dict]:
    files = []
    for i in range(count):
        h, m = divmod(i * 5, 60)
        h2, m2 = divmod(i * 5 + 5, 60)
        begin, end = f"{h:02d}.{m:02d}.00", f"{h2 % 24:02d}.{m2:02d}.00"
        files.append({
            "BeginTime": f"{day} {begin.replace('.', ':')}",
            "EndTime": f"{day} {end.replace('.', ':')}",
            "DiskNo": 0, "SerialNo": 0, "FileLength": "0x00000010",
            "FileName": f"/idea0/{day}/001/{begin}-{end}[R][@{i:04x}][0].h264",
        })
    return files


class FakeCamera:
    def __init__(self, user="admin", password="secret", files=None, payload=None, chunk=4096):
        self.user, self.password = user, password
        self.files = files if files is not None else make_files("2026-10-14", 70)
        self.payload = payload if payload is not None else xm_stream()
        self.chunk = chunk
        self.requests: list[tuple[int, dict]] = []
        cam = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                cam._serve(self.request)

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    @staticmethod
    def _recv(sock, n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError
            buf += chunk
        return buf

    @staticmethod
    def _send(sock, session, msg_id, payload: bytes):
        sock.sendall(HEADER.pack(0xFF, 0, session, 0, msg_id, len(payload)) + payload)

    def _reply(self, sock, session, msg_id, body):
        self._send(sock, session, msg_id, json.dumps(body).encode() + b"\n\0")

    def _serve(self, sock):
        session = 0x1F
        try:
            while True:
                _h, _v, _s, _seq, msg_id, length = HEADER.unpack(self._recv(sock, HEADER.size))
                body = json.loads(self._recv(sock, length).rstrip(b"\0\n").decode()) if length else {}
                self.requests.append((msg_id, body))
                if msg_id == LOGIN:
                    ok = body.get("UserName") == self.user and body.get("PassWord") == sofia_hash(self.password)
                    self._reply(sock, session, LOGIN + 1, {"Ret": 100 if ok else 203, "SessionID": f"0x{session:08X}", "AliveInterval": 20})
                elif msg_id == SYSINFO:
                    self._reply(sock, session, SYSINFO + 1, {"Ret": 100, "SystemInfo": {"DeviceModel": "FAKE-IPC", "SerialNo": "fake123"}})
                elif msg_id == FILE_QUERY:
                    q = body["OPFileQuery"]
                    match = [f for f in self.files if q["BeginTime"] <= f["BeginTime"] <= q["EndTime"]]
                    if not match:
                        self._reply(sock, session, FILE_QUERY + 1, {"Ret": 109})
                    else:
                        self._reply(sock, session, FILE_QUERY + 1, {"Ret": 100, "OPFileQuery": match[:64]})
                elif msg_id == PLAYBACK_CLAIM:
                    self._reply(sock, session, PLAYBACK_CLAIM + 1, {"Ret": 100})
                elif msg_id == PLAYBACK:
                    action = body["OPPlayBack"]["Action"]
                    if action == "DownloadStart":
                        self._reply(sock, session, PLAYBACK + 1, {"Ret": 100})
                        for i in range(0, len(self.payload), self.chunk):
                            self._send(sock, session, DOWNLOAD_DATA, self.payload[i:i + self.chunk])
                        self._send(sock, session, DOWNLOAD_DATA, b"")
                elif msg_id == LOGOUT:
                    return
        except (ConnectionError, OSError):
            return
