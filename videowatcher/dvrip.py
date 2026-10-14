"""Client for the Xiongmai "DVRIP" (Sofia) protocol spoken by iCSee/XMEye cameras on TCP 34567.

Every packet is a 20-byte little-endian header followed by a payload:

    0   u8   head flag (0xFF)
    1   u8   version (0)
    2   2B   reserved
    4   u32  session id
    8   u32  sequence number
    12  2B   total/current packet (unused here)
    14  u16  message id
    16  u32  payload length

Command payloads are JSON terminated by b"\\n\\0". The message ids and JSON bodies for
file search (1440 OPFileQuery) and download (1424 Claim / 1420 DownloadStart with
PlayMode "ByName") match what VMS Pro's XNetSDK sends; see EVIDENCE.md.
"""

from __future__ import annotations

import hashlib
import json
import socket
import struct
import threading
from dataclasses import dataclass
from typing import Callable

HEADER = struct.Struct("<BB2xII2xHI")

LOGIN = 1000
LOGOUT = 1002
KEEPALIVE = 1006
SYSINFO = 1020
FILE_QUERY = 1440
PLAYBACK = 1420
PLAYBACK_CLAIM = 1424
DOWNLOAD_DATA = 1426

RET_OK = 100
RET_NO_FILES = 109

RET_MESSAGES = {
    101: "unknown error",
    102: "not supported",
    103: "illegal request",
    104: "user already logged in",
    105: "user not logged in",
    106: "wrong username or password",
    107: "no permission",
    108: "timeout",
    109: "no files found",
    203: "wrong password",
    205: "user does not exist",
    206: "account locked",
    207: "account blacklisted",
}

_BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


class DvripError(Exception):
    def __init__(self, message: str, ret: int | None = None):
        super().__init__(message)
        self.ret = ret


class AuthError(DvripError):
    pass


def sofia_hash(password: str) -> str:
    """Password digest used by EncryptType "MD5": 8 base-62 chars from MD5 byte pairs."""
    digest = hashlib.md5(password.encode("utf-8")).digest()
    return "".join(_BASE62[(digest[i] + digest[i + 1]) % 62] for i in range(0, 16, 2))


def describe_ret(ret: int) -> str:
    return f"Ret={ret} ({RET_MESSAGES.get(ret, 'unknown code')})"


@dataclass(frozen=True)
class RecordFile:
    channel: int
    file_name: str
    begin: str  # "YYYY-MM-DD HH:MM:SS", camera local time
    end: str
    size_bytes: int  # device reports FileLength in KiB

    @classmethod
    def from_json(cls, channel: int, item: dict) -> "RecordFile":
        raw_len = item.get("FileLength", "0x0")
        kib = int(raw_len, 16) if isinstance(raw_len, str) else int(raw_len)
        return cls(channel, item["FileName"], item["BeginTime"], item["EndTime"], kib * 1024)


ProgressCallback = Callable[[int], None]


class DvripClient:
    """One TCP session with a camera. Not thread-safe; use one client per operation."""

    def __init__(self, host: str, port: int = 34567, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.session = 0
        self.seq = 0
        self.sock: socket.socket | None = None

    # -- transport ---------------------------------------------------------------

    def connect(self) -> None:
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def __enter__(self) -> "DvripClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self.session:
                self.logout()
        except (OSError, DvripError):
            pass
        self.close()

    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise DvripError("connection closed by camera")
            buf += chunk
        return bytes(buf)

    def _send(self, msg_id: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8") + b"\n\0"
        self.sock.sendall(HEADER.pack(0xFF, 0, self.session, self.seq, msg_id, len(payload)) + payload)
        self.seq += 1

    def _recv_packet(self) -> tuple[int, bytes]:
        head, _ver, session, _seq, msg_id, length = HEADER.unpack(self._recv_exact(HEADER.size))
        if head != 0xFF:
            raise DvripError(f"bad packet header 0x{head:02x}")
        if session:
            self.session = session
        return msg_id, self._recv_exact(length) if length else b""

    @staticmethod
    def _decode(payload: bytes) -> dict:
        text = payload.rstrip(b"\0\n ").decode("utf-8", errors="replace")
        return json.loads(text) if text else {}

    def _request(self, msg_id: int, body: dict) -> dict:
        self._send(msg_id, body)
        while True:
            reply_id, payload = self._recv_packet()
            if reply_id == msg_id + 1:
                return self._decode(payload)
            # Ignore unsolicited packets (alarms, keepalive replies) between request and reply.

    def _session_hex(self) -> str:
        return f"0x{self.session:08X}"

    # -- commands ----------------------------------------------------------------

    def login(self, user: str, password: str, login_type: str = "DVRIP-Mobile") -> dict:
        reply = self._request(LOGIN, {
            "EncryptType": "MD5",
            "LoginType": login_type,
            "UserName": user,
            "PassWord": sofia_hash(password),
        })
        ret = reply.get("Ret")
        if ret != RET_OK:
            raise AuthError(f"camera login failed: {describe_ret(ret)}", ret)
        self.session = int(reply["SessionID"], 16)
        return reply

    def logout(self) -> None:
        self._send(LOGOUT, {"Name": "", "SessionID": self._session_hex()})
        self.session = 0

    def system_info(self) -> dict:
        reply = self._request(SYSINFO, {"Name": "SystemInfo", "SessionID": self._session_hex()})
        return reply.get("SystemInfo", {})

    def find_files(self, channel: int, begin: str, end: str, file_type: str = "h264") -> list[RecordFile]:
        """List recordings between two camera-local timestamps ("YYYY-MM-DD HH:MM:SS")."""
        found: dict[str, RecordFile] = {}
        cursor = begin
        while True:
            reply = self._request(FILE_QUERY, {
                "Name": "OPFileQuery",
                "SessionID": self._session_hex(),
                "OPFileQuery": {
                    "BeginTime": cursor,
                    "Channel": channel,
                    "DriverTypeMask": "0x0000FFFF",
                    "EndTime": end,
                    "Event": "*",
                    "StreamType": "0x0",
                    "Type": file_type,
                },
            })
            ret = reply.get("Ret")
            if ret == RET_NO_FILES:
                break
            if ret != RET_OK:
                raise DvripError(f"file search failed: {describe_ret(ret)}", ret)
            page = reply.get("OPFileQuery") or []
            new = [RecordFile.from_json(channel, item) for item in page]
            fresh = [f for f in new if f.file_name not in found]
            for f in fresh:
                found[f.file_name] = f
            # The camera returns at most 64 entries per page; continue from the last end time.
            if len(page) < 64 or not fresh:
                break
            cursor = max(f.end for f in new)
        return sorted(found.values(), key=lambda f: f.begin)

    def _playback(self, action: str, rec: RecordFile) -> dict:
        return {
            "Name": "OPPlayBack",
            "SessionID": self._session_hex(),
            "OPPlayBack": {
                "Action": action,
                "Parameter": {
                    "Channel": rec.channel,
                    "FileName": rec.file_name,
                    "FileOffset": 0,
                    "OffWithHead": 0,
                    "PlayMode": "ByName",
                    "StreamType": 0,
                    "Value": 0,
                    "TransMode": "TCP",
                },
                "StartTime": rec.begin,
                "EndTime": rec.end,
            },
        }

    def download(
        self,
        rec: RecordFile,
        sink,
        on_progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
        idle_timeout: float = 20.0,
    ) -> int:
        """Stream one recording into the writable binary `sink`. Returns bytes written.

        The camera sends DOWNLOAD_DATA packets and ends with an empty one. Some firmware
        just stops sending, so a long idle gap after data has arrived also counts as the end.
        """
        claim = self._request(PLAYBACK_CLAIM, self._playback("Claim", rec))
        if claim.get("Ret") != RET_OK:
            raise DvripError(f"download claim refused: {describe_ret(claim.get('Ret'))}", claim.get("Ret"))
        self._send(PLAYBACK, self._playback("DownloadStart", rec))

        written = 0
        self.sock.settimeout(idle_timeout)
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    raise DvripError("download cancelled")
                try:
                    msg_id, payload = self._recv_packet()
                except socket.timeout:
                    if written:
                        break
                    raise DvripError("camera sent no data")
                if msg_id == DOWNLOAD_DATA:
                    if not payload:
                        break
                    sink.write(payload)
                    written += len(payload)
                    if on_progress:
                        on_progress(written)
                elif msg_id == PLAYBACK + 1:
                    ret = self._decode(payload).get("Ret")
                    if ret != RET_OK:
                        raise DvripError(f"download refused: {describe_ret(ret)}", ret)
        finally:
            self.sock.settimeout(self.timeout)
            try:
                self._send(PLAYBACK, self._playback("DownloadStop", rec))
            except OSError:
                pass
        return written
