"""One shared password for the hosted app (owner and partner lawyer). Not user accounts.

- APP_PASSWORD set: every page except /login, /health and /static needs a signed session cookie.
- APP_PASSWORD not set: only local use (Host 127.0.0.1 / localhost, and not hosted) is served; anything
  else gets a page saying the password is missing (fail closed). On Railway (RAILWAY_ENVIRONMENT) or with
  APP_REQUIRE_LOGIN=1 the app is "hosted" and never runs open, even if a request claims Host: localhost.
The password itself is never stored in the cookie or written to logs.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time

COOKIE = "las_session"
MAX_AGE = 30 * 24 * 3600
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
_failures: dict[str, list[float]] = {}
_lock = threading.Lock()


def password() -> str:
    return os.environ.get("APP_PASSWORD", "")


def hosted() -> bool:
    """On a hosting platform the app must never run without a password, whatever the Host header says."""
    return bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("APP_REQUIRE_LOGIN"))


def _key_file_secret() -> str:
    """A random key kept in private storage, so sessions survive restarts without SECRET_KEY."""
    path = os.path.join(os.environ.get("PRIVATE_STORAGE_PATH", "data"), ".session_key")
    try:
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(key)
    except OSError:
        pass   # read-only storage: the key lives for this process only
    return key


def _secret() -> bytes:
    if not _secret.key:
        _secret.key = os.environ.get("SECRET_KEY") or _key_file_secret()
    return hashlib.sha256(("las:" + _secret.key + ":" + password()).encode()).digest()


_secret.key = ""


def make_cookie(now: float | None = None) -> str:
    ts = str(int(now if now is not None else time.time()))
    sig = hmac.new(_secret(), ts.encode(), hashlib.sha256).hexdigest()
    return f"{ts}.{sig}"


def cookie_ok(value: str | None, now: float | None = None) -> bool:
    if not value or "." not in value:
        return False
    ts, sig = value.split(".", 1)
    if not ts.isdigit():
        return False
    good = hmac.new(_secret(), ts.encode(), hashlib.sha256).hexdigest()
    age = (now if now is not None else time.time()) - int(ts)
    return hmac.compare_digest(sig, good) and 0 <= age <= MAX_AGE


def check_password(given: str) -> bool:
    expected = password()
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


GLOBAL = "*"


def client_address(forwarded_for: str, peer: str) -> str:
    """The address the hosting proxy saw. Railway appends it as the LAST X-Forwarded-For entry;
    earlier entries come from the client and cannot be trusted."""
    parts = [p.strip() for p in (forwarded_for or "").split(",") if p.strip()]
    return parts[-1] if parts else (peer or "?")


def too_many_failures(client: str, now: float | None = None) -> bool:
    """At most 5 wrong passwords per 10 minutes per client, and 30 in total (the password is shared)."""
    now = now if now is not None else time.time()
    with _lock:
        for key in list(_failures):
            _failures[key] = [t for t in _failures[key] if now - t < 600]
            if not _failures[key]:
                del _failures[key]
        return len(_failures.get(client, [])) >= 5 or len(_failures.get(GLOBAL, [])) >= 30


def record_failure(client: str, now: float | None = None) -> None:
    t = now if now is not None else time.time()
    with _lock:
        _failures.setdefault(client, []).append(t)
        _failures.setdefault(GLOBAL, []).append(t)
