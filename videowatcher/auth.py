"""App login for the videoWatcher UI: scrypt password hashes, HMAC-signed session cookies,
and a per-client throttle on failed logins."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from pathlib import Path

from .store import DATA_DIR, _atomic_write, _read

SESSION_COOKIE = "vw_session"
SESSION_TTL = 12 * 3600
SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}

MAX_FAILURES = 5
LOCKOUT_SECONDS = 300


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, **SCRYPT)
    return f"scrypt${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    expected = hashlib.scrypt(password.encode("utf-8"), salt=_unb64(salt), **SCRYPT)
    return hmac.compare_digest(expected, _unb64(digest))


class UserStore:
    def __init__(self, path: Path | None = None):
        self.path = path or DATA_DIR / "users.json"
        self._lock = threading.Lock()
        # Compared against when the username is unknown, so both paths cost one scrypt.
        self._dummy = hash_password(secrets.token_hex(8))

    def _all(self) -> dict[str, str]:
        return _read(self.path, {})

    def exists(self) -> bool:
        return bool(self._all())

    def set_password(self, username: str, password: str) -> None:
        if len(password) < 8:
            raise ValueError("password must be at least 8 characters")
        with self._lock:
            users = self._all()
            users[username] = hash_password(password)
            _atomic_write(self.path, users)

    def check(self, username: str, password: str) -> bool:
        stored = self._all().get(username)
        ok = verify_password(password, stored or self._dummy)
        return ok and stored is not None


class SessionSigner:
    def __init__(self, key_path: Path | None = None):
        path = key_path or DATA_DIR / "secret.key"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(secrets.token_bytes(32))
        self.key = path.read_bytes()

    def _sig(self, body: str) -> str:
        return _b64(hmac.new(self.key, body.encode(), hashlib.sha256).digest())

    def issue(self, username: str, ttl: int = SESSION_TTL) -> str:
        body = f"{_b64(username.encode())}.{int(time.time()) + ttl}"
        return f"{body}.{self._sig(body)}"

    def verify(self, token: str | None) -> str | None:
        if not token or token.count(".") != 2:
            return None
        user_b64, expiry, sig = token.split(".")
        body = f"{user_b64}.{expiry}"
        if not hmac.compare_digest(sig, self._sig(body)):
            return None
        if not expiry.isdigit() or int(expiry) < time.time():
            return None
        return _unb64(user_b64).decode()


class LoginThrottle:
    def __init__(self):
        self._failures: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def blocked_for(self, client: str) -> int:
        with self._lock:
            count, since = self._failures.get(client, (0, 0.0))
            remaining = since + LOCKOUT_SECONDS - time.time()
            return int(remaining) + 1 if count >= MAX_FAILURES and remaining > 0 else 0

    def record(self, client: str, success: bool) -> None:
        with self._lock:
            if success:
                self._failures.pop(client, None)
                return
            count, since = self._failures.get(client, (0, time.time()))
            if time.time() - since > LOCKOUT_SECONDS:
                count, since = 0, time.time()
            self._failures[client] = (count + 1, since)
