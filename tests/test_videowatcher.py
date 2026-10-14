import io
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tests.fake_camera import FakeCamera, xm_stream
from videowatcher import dvrip
from videowatcher.app import create_app
from videowatcher.auth import LoginThrottle, SessionSigner, UserStore, hash_password, verify_password
from videowatcher.jobs import JobManager
from videowatcher.store import CameraStore
from videowatcher.xmdemux import extract_video


class SofiaHashTest(unittest.TestCase):
    def test_known_empty_password_hash(self):
        # Well-known digest of the empty password on Xiongmai devices.
        self.assertEqual(dvrip.sofia_hash(""), "tlJwpbo6")


class DvripClientTest(unittest.TestCase):
    def setUp(self):
        self.cam = FakeCamera()

    def tearDown(self):
        self.cam.close()

    def client(self):
        return dvrip.DvripClient("127.0.0.1", self.cam.port, timeout=5)

    def test_login_and_wrong_password(self):
        with self.client() as c:
            c.login("admin", "secret")
            self.assertEqual(c.session, 0x1F)
            self.assertEqual(c.system_info()["DeviceModel"], "FAKE-IPC")
        with self.client() as c, self.assertRaises(dvrip.AuthError) as ctx:
            c.login("admin", "nope")
        self.assertEqual(ctx.exception.ret, 203)

    def test_file_query_pages_past_64(self):
        with self.client() as c:
            c.login("admin", "secret")
            files = c.find_files(0, "2026-10-14 00:00:00", "2026-10-14 23:59:59")
        self.assertEqual(len(files), 70)
        self.assertEqual(files[0].size_bytes, 16 * 1024)
        queries = [b for m, b in self.cam.requests if m == dvrip.FILE_QUERY]
        self.assertEqual(len(queries), 2)
        self.assertEqual(queries[0]["OPFileQuery"]["DriverTypeMask"], "0x0000FFFF")

    def test_no_files(self):
        with self.client() as c:
            c.login("admin", "secret")
            self.assertEqual(c.find_files(0, "2026-10-15 00:00:00", "2026-10-15 23:59:59"), [])

    def test_download_streams_exact_bytes(self):
        progress = []
        with self.client() as c:
            c.login("admin", "secret")
            rec = c.find_files(0, "2026-10-14 00:00:00", "2026-10-14 00:04:59")[0]
            sink = io.BytesIO()
            n = c.download(rec, sink, on_progress=progress.append)
        self.assertEqual(sink.getvalue(), self.cam.payload)
        self.assertEqual(n, len(self.cam.payload))
        self.assertEqual(progress[-1], n)
        actions = [b["OPPlayBack"]["Action"] for m, b in self.cam.requests if m in (dvrip.PLAYBACK, dvrip.PLAYBACK_CLAIM)]
        self.assertEqual(actions, ["Claim", "DownloadStart", "DownloadStop"])
        start = next(b for m, b in self.cam.requests if m == dvrip.PLAYBACK)["OPPlayBack"]
        self.assertEqual(start["Parameter"]["PlayMode"], "ByName")
        self.assertEqual(start["Parameter"]["FileName"], rec.file_name)

    def test_download_cancel(self):
        cancel = threading.Event()
        cancel.set()
        with self.client() as c:
            c.login("admin", "secret")
            rec = c.find_files(0, "2026-10-14 00:00:00", "2026-10-14 00:04:59")[0]
            with self.assertRaises(dvrip.DvripError):
                c.download(rec, io.BytesIO(), cancel=cancel)


class DemuxTest(unittest.TestCase):
    def test_extracts_hevc_without_xm_headers_or_audio(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            src, dst = tmp / "in.h264", tmp / "out.es"
            src.write_bytes(b"garbage" + xm_stream(frames=20, fps=12))
            info = extract_video(src, dst)
            out = dst.read_bytes()
            self.assertEqual((info.codec, info.fps, info.frames), ("hevc", 12, 20))
            self.assertEqual(info.skipped_bytes, len(b"garbage"))
            self.assertNotIn(b"\x00\x00\x01\xfc", out)
            self.assertNotIn(b"\x00\x00\x01\xfa", out)
            self.assertNotIn(b"\xd5" * 160, out)
            self.assertTrue(out.startswith(b"\x00\x00\x00\x01\x40\x01"))
        finally:
            shutil.rmtree(tmp)


class AuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_password_hashing(self):
        stored = hash_password("correct horse")
        self.assertTrue(verify_password("correct horse", stored))
        self.assertFalse(verify_password("wrong", stored))
        self.assertNotEqual(stored, hash_password("correct horse"))

    def test_user_store_rejects_short_and_unknown(self):
        users = UserStore(self.tmp / "users.json")
        with self.assertRaises(ValueError):
            users.set_password("a", "short")
        users.set_password("fran", "longenough")
        self.assertTrue(users.check("fran", "longenough"))
        self.assertFalse(users.check("ghost", "longenough"))

    def test_session_tamper_and_expiry(self):
        signer = SessionSigner(self.tmp / "k")
        token = signer.issue("fran")
        self.assertEqual(signer.verify(token), "fran")
        self.assertIsNone(signer.verify(token[:-2] + "xx"))
        self.assertIsNone(signer.verify(signer.issue("fran", ttl=-1)))
        self.assertIsNone(SessionSigner(self.tmp / "other").verify(token))

    def test_throttle(self):
        t = LoginThrottle()
        for _ in range(5):
            t.record("1.2.3.4", False)
        self.assertGreater(t.blocked_for("1.2.3.4"), 0)
        self.assertEqual(t.blocked_for("5.6.7.8"), 0)


class AppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cam = FakeCamera()
        users = UserStore(self.tmp / "users.json")
        users.set_password("fran", "longenough")
        self.jobs = JobManager(self.tmp / "downloads")
        app = create_app(CameraStore(self.tmp / "cameras.json"), users, SessionSigner(self.tmp / "k"), self.jobs)
        self.http = TestClient(app)
        self.h = {"X-Videowatcher": "1"}

    def tearDown(self):
        self.cam.close()
        shutil.rmtree(self.tmp)

    def login(self):
        r = self.http.post("/api/login", json={"username": "fran", "password": "longenough"}, headers=self.h)
        self.assertEqual(r.status_code, 200)

    def add_camera(self, **extra):
        body = {"name": "Garaje", "host": "127.0.0.1", "port": self.cam.port,
                "username": "admin", "password": "secret", **extra}
        r = self.http.post("/api/cameras", json=body, headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_requires_login_and_csrf_header(self):
        self.assertEqual(self.http.get("/api/cameras").status_code, 401)
        r = self.http.post("/api/login", json={"username": "fran", "password": "longenough"})
        self.assertEqual(r.status_code, 403)
        r = self.http.post("/api/login", json={"username": "fran", "password": "bad"}, headers=self.h)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.http.get("/").status_code, 200)

    def test_camera_password_never_returned(self):
        self.login()
        cam = self.add_camera()
        self.assertNotIn("password", cam)
        self.assertTrue(cam["has_password"])
        listed = self.http.get("/api/cameras").json()
        self.assertNotIn("secret", str(listed))
        # Updating without a password keeps the stored one.
        r = self.http.put(f"/api/cameras/{cam['id']}", json={"name": "Garaje 2", "host": "127.0.0.1",
                                                             "port": self.cam.port}, headers=self.h)
        self.assertTrue(r.json()["has_password"])
        self.assertEqual(self.http.post(f"/api/cameras/{cam['id']}/test", headers=self.h).json()["DeviceModel"], "FAKE-IPC")

    def test_search_download_and_fetch_file(self):
        self.login()
        cam = self.add_camera()
        recs = self.http.get(f"/api/cameras/{cam['id']}/recordings",
                             params={"date": "2026-10-14", "from": "00:00", "to": "00:09"}).json()
        self.assertEqual(len(recs), 2)
        job = self.http.post("/api/downloads", json={"camera_id": cam["id"], "mp4": False, **recs[0]},
                             headers=self.h).json()
        for _ in range(100):
            job = next(j for j in self.http.get("/api/downloads").json() if j["id"] == job["id"])
            if job["state"] in ("done", "error"):
                break
            time.sleep(0.05)
        self.assertEqual(job["state"], "done", job["error"])
        r = self.http.get(f"/api/downloads/{job['id']}/file", params={"kind": "raw"})
        self.assertEqual(r.content, self.cam.payload)
        self.assertEqual(self.http.get(f"/api/downloads/{job['id']}/file", params={"kind": "mp4"}).status_code, 404)

    def test_camera_errors_are_reported(self):
        self.login()
        cam = self.add_camera(password="wrong")
        r = self.http.get(f"/api/cameras/{cam['id']}/recordings", params={"date": "2026-10-14"})
        self.assertEqual(r.status_code, 502)
        self.assertIn("wrong password", r.json()["detail"])
        p2p = self.add_camera(name="Cloud", transport="p2p", serial="abc", host="")
        r = self.http.get(f"/api/cameras/{p2p['id']}/recordings", params={"date": "2026-10-14"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("not implemented", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
