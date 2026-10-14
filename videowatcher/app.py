"""FastAPI app: login, camera management, recording search and downloads."""

from __future__ import annotations

import re
import socket
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import SESSION_COOKIE, SESSION_TTL, LoginThrottle, SessionSigner, UserStore
from .backends import BackendUnavailable, backend_for
from .dvrip import DvripError, RecordFile
from .jobs import JobManager
from .store import Camera, CameraStore

STATIC = Path(__file__).parent / "static"
CSRF_HEADER = "x-videowatcher"
TIME_RE = re.compile(r"^\d{2}:\d{2}(:\d{2})?$")


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class CameraBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    host: str = Field(default="", max_length=253)
    port: int = Field(default=34567, ge=1, le=65535)
    username: str = Field(default="admin", max_length=64)
    password: str | None = Field(default=None, max_length=128)  # None/"" keeps the stored one
    channels: int = Field(default=1, ge=1, le=64)
    transport: str = Field(default="lan", pattern="^(lan|p2p)$")
    serial: str = Field(default="", max_length=64)


class DownloadBody(BaseModel):
    camera_id: str
    channel: int = Field(ge=0, le=63)
    file_name: str = Field(min_length=1, max_length=255)
    begin: str
    end: str
    size_bytes: int = Field(default=0, ge=0)
    mp4: bool = True


def create_app(
    cameras: CameraStore | None = None,
    users: UserStore | None = None,
    signer: SessionSigner | None = None,
    jobs: JobManager | None = None,
    secure_cookie: bool = False,
) -> FastAPI:
    cameras = cameras or CameraStore()
    users = users or UserStore()
    signer = signer or SessionSigner()
    jobs = jobs or JobManager()
    throttle = LoginThrottle()

    app = FastAPI(title="videoWatcher", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):
        # Browsers cannot attach a custom header cross-site without a CORS preflight we never allow.
        if request.method in ("POST", "PUT", "DELETE") and request.url.path.startswith("/api/"):
            if request.headers.get(CSRF_HEADER) != "1":
                return JSONResponse({"detail": "missing request header"}, status_code=403)
        return await call_next(request)

    def current_user(request: Request) -> str:
        user = signer.verify(request.cookies.get(SESSION_COOKIE))
        if not user:
            raise HTTPException(401, "login required")
        return user

    def camera_or_404(cam_id: str) -> Camera:
        cam = cameras.get(cam_id)
        if not cam:
            raise HTTPException(404, "camera not found")
        return cam

    def camera_call(fn, *args):
        try:
            return fn(*args)
        except BackendUnavailable as exc:
            raise HTTPException(400, str(exc))
        except DvripError as exc:
            raise HTTPException(502, str(exc))
        except (socket.timeout, TimeoutError):
            raise HTTPException(504, "camera did not answer in time")
        except OSError as exc:
            raise HTTPException(502, f"cannot reach camera: {exc.strerror or exc}")

    # -- session -----------------------------------------------------------------

    @app.post("/api/login")
    def login(body: LoginBody, request: Request, response: Response):
        client = request.client.host if request.client else "?"
        wait = throttle.blocked_for(client)
        if wait:
            raise HTTPException(429, f"too many failed attempts, retry in {wait}s")
        ok = users.check(body.username, body.password)
        throttle.record(client, ok)
        if not ok:
            raise HTTPException(401, "wrong username or password")
        response.set_cookie(
            SESSION_COOKIE, signer.issue(body.username), max_age=SESSION_TTL,
            httponly=True, samesite="strict", secure=secure_cookie,
        )
        return {"username": body.username}

    @app.post("/api/logout")
    def logout(response: Response):
        response.delete_cookie(SESSION_COOKIE)
        return {"ok": True}

    @app.get("/api/me")
    def me(user: str = Depends(current_user)):
        return {"username": user}

    # -- cameras -----------------------------------------------------------------

    @app.get("/api/cameras")
    def list_cameras(_: str = Depends(current_user)):
        return [c.public() for c in sorted(cameras.all(), key=lambda c: c.name.lower())]

    @app.post("/api/cameras")
    def add_camera(body: CameraBody, _: str = Depends(current_user)):
        data = body.model_dump()
        data["password"] = data["password"] or ""
        return cameras.save(Camera(**data)).public()

    @app.put("/api/cameras/{cam_id}")
    def update_camera(cam_id: str, body: CameraBody, _: str = Depends(current_user)):
        cam = camera_or_404(cam_id)
        data = body.model_dump()
        if not data["password"]:
            data["password"] = cam.password
        return cameras.save(Camera(id=cam.id, **data)).public()

    @app.delete("/api/cameras/{cam_id}")
    def delete_camera(cam_id: str, _: str = Depends(current_user)):
        if not cameras.delete(cam_id):
            raise HTTPException(404, "camera not found")
        return {"ok": True}

    @app.post("/api/cameras/{cam_id}/test")
    def test_camera(cam_id: str, _: str = Depends(current_user)):
        cam = camera_or_404(cam_id)
        info = camera_call(backend_for(cam).test, cam)
        keys = ("DeviceModel", "SerialNo", "SoftWareVersion", "VideoInChannel", "HardWare")
        return {k: info.get(k) for k in keys if k in info}

    # -- recordings & downloads --------------------------------------------------

    @app.get("/api/cameras/{cam_id}/recordings")
    def recordings(
        cam_id: str,
        day: date = Query(alias="date"),
        channel: int = Query(0, ge=0, le=63),
        start: str = Query("00:00:00", alias="from"),
        end: str = Query("23:59:59", alias="to"),
        _: str = Depends(current_user),
    ):
        if not (TIME_RE.match(start) and TIME_RE.match(end)):
            raise HTTPException(422, "times must be HH:MM or HH:MM:SS")
        pad = lambda t, s: t if len(t) == 8 else f"{t}:{s}"
        cam = camera_or_404(cam_id)
        files = camera_call(
            backend_for(cam).search, cam, channel,
            f"{day} {pad(start, '00')}", f"{day} {pad(end, '59')}",
        )
        return [
            {"channel": f.channel, "file_name": f.file_name, "begin": f.begin,
             "end": f.end, "size_bytes": f.size_bytes}
            for f in files
        ]

    @app.post("/api/downloads")
    def start_download(body: DownloadBody, _: str = Depends(current_user)):
        cam = camera_or_404(body.camera_id)
        rec = RecordFile(body.channel, body.file_name, body.begin, body.end, body.size_bytes)
        return jobs.submit(cam, rec, body.mp4).public()

    @app.get("/api/downloads")
    def list_downloads(_: str = Depends(current_user)):
        return [j.public() for j in sorted(jobs.jobs.values(), key=lambda j: j.created, reverse=True)]

    @app.delete("/api/downloads/{job_id}")
    def cancel_download(job_id: str, _: str = Depends(current_user)):
        if not jobs.cancel(job_id):
            raise HTTPException(404, "no running download with that id")
        return {"ok": True}

    @app.get("/api/downloads/{job_id}/file")
    def download_file(job_id: str, kind: str = Query("mp4", pattern="^(mp4|raw)$"),
                      _: str = Depends(current_user)):
        job = jobs.jobs.get(job_id)
        path = job and (job.mp4_path if kind == "mp4" else job.raw_path)
        if not path or not path.exists():
            raise HTTPException(404, "file not available")
        return FileResponse(path, filename=path.name)

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="ui")
    return app
