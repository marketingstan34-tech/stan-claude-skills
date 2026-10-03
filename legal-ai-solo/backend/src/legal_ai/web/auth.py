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


def _secret() -> bytes:
    # SECRET_KEY signs cookies; without it a per-process key is used (sessions end on restart).
    key = os.environ.get("SECRET_KEY") or _secret.fallback
    return hashlib.sha256(("las:" + key + ":" + password()).encode()).digest()


_secret.fallback = secrets.token_hex(32)


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


def too_many_failures(client: str, now: float | None = None) -> bool:
    """At most 5 wrong passwords per 10 minutes per client address."""
    now = now if now is not None else time.time()
    with _lock:
        recent = [t for t in _failures.get(client, []) if now - t < 600]
        _failures[client] = recent
        return len(recent) >= 5


def record_failure(client: str, now: float | None = None) -> None:
    with _lock:
        _failures.setdefault(client, []).append(now if now is not None else time.time())
