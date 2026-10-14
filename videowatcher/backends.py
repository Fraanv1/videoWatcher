"""Transport backends. A camera's `transport` field picks one.

"lan" talks DVRIP directly to the camera's IP. "p2p" (reaching cameras by serial number
through the Xiongmai cloud, as VMS Pro does) is reserved: it needs either the vendor's
XNetSDK.dll or a reimplementation of its P2P layer, neither of which is done yet.
"""

from __future__ import annotations

import threading
from typing import BinaryIO, Protocol

from .dvrip import DvripClient, ProgressCallback, RecordFile
from .store import Camera


class BackendUnavailable(Exception):
    pass


class RecorderBackend(Protocol):
    def test(self, cam: Camera) -> dict: ...

    def search(self, cam: Camera, channel: int, begin: str, end: str) -> list[RecordFile]: ...

    def download(
        self,
        cam: Camera,
        rec: RecordFile,
        sink: BinaryIO,
        on_progress: ProgressCallback,
        cancel: threading.Event,
    ) -> int: ...


class LanBackend:
    def _client(self, cam: Camera) -> DvripClient:
        if not cam.host:
            raise BackendUnavailable(f"camera '{cam.name}' has no IP address configured")
        return DvripClient(cam.host, cam.port)

    def test(self, cam: Camera) -> dict:
        with self._client(cam) as c:
            c.login(cam.username, cam.password)
            return c.system_info()

    def search(self, cam: Camera, channel: int, begin: str, end: str) -> list[RecordFile]:
        with self._client(cam) as c:
            c.login(cam.username, cam.password)
            return c.find_files(channel, begin, end)

    def download(self, cam, rec, sink, on_progress, cancel) -> int:
        with self._client(cam) as c:
            c.login(cam.username, cam.password)
            return c.download(rec, sink, on_progress=on_progress, cancel=cancel)


class P2PBackend:
    def _unavailable(self, *_args, **_kwargs):
        raise BackendUnavailable(
            "P2P (cloud serial number) access is not implemented yet; "
            "set the camera's transport to 'lan' and give it a reachable IP"
        )

    test = search = download = _unavailable


BACKENDS: dict[str, RecorderBackend] = {"lan": LanBackend(), "p2p": P2PBackend()}


def backend_for(cam: Camera) -> RecorderBackend:
    try:
        return BACKENDS[cam.transport]
    except KeyError:
        raise BackendUnavailable(f"unknown transport '{cam.transport}'") from None
