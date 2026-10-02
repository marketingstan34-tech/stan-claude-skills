"""Polite HTTP client shared by all court sources.

- one request at a time, at least `min_interval` seconds between requests (never below 2 s);
- only https and only hosts on the allowlist (SSRF guard); redirects are not followed;
- bounded retries for timeouts/429/5xx, honouring Retry-After; no retry on other 4xx;
- response size cap.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatch
from typing import Callable, Iterable
from urllib.parse import urlparse

import httpx

MAX_ATTEMPTS = 3


class FetchError(Exception):
    pass


@dataclass
class Fetched:
    url: str
    status: int
    body: bytes
    retrieved_at: str
    content_type: str = ""


def host_allowed(host: str | None, patterns: Iterable[str]) -> bool:
    """Exact host or a '*.example.bg' pattern (which does not match the bare domain)."""
    if not host:
        return False
    host = host.lower()
    for p in patterns:
        p = p.lower()
        if p.startswith("*."):
            if host.endswith(p[1:]) and fnmatch(host, p):
                return True
        elif host == p:
            return True
    return False


class PoliteClient:
    def __init__(self, allowed_hosts: Iterable[str], min_interval: float = 2.0,
                 user_agent: str = "legal-ai-solo/0.1", max_bytes: int = 5 * 1024 * 1024,
                 transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._allowed = tuple(allowed_hosts)
        self._min_interval = max(2.0, min_interval)
        self._max_bytes = max_bytes
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None
        self._http = httpx.Client(
            timeout=httpx.Timeout(60.0),
            headers={"User-Agent": user_agent},
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _wait_turn(self) -> None:
        if self._last is not None:
            remaining = self._min_interval - (self._clock() - self._last)
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()

    def get(self, url: str) -> Fetched:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not host_allowed(parsed.hostname, self._allowed):
            raise FetchError(f"URL извън разрешения домейн: {url}")
        last_error = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._wait_turn()
            try:
                with self._http.stream("GET", url) as resp:
                    if resp.status_code == 429 or resp.status_code >= 500:
                        last_error = f"HTTP {resp.status_code}"
                        retry_after = resp.headers.get("Retry-After", "")
                        delay = float(retry_after) if retry_after.isdigit() else 2 ** attempt
                        self._sleep(min(delay, 60) + random.uniform(0, 1))
                        continue
                    if resp.status_code != 200:
                        raise FetchError(f"HTTP {resp.status_code} за {url}")
                    chunks, size = [], 0
                    for chunk in resp.iter_bytes():
                        size += len(chunk)
                        if size > self._max_bytes:
                            raise FetchError(f"Отговорът е над {self._max_bytes} байта: {url}")
                        chunks.append(chunk)
                    return Fetched(url, 200, b"".join(chunks),
                                   datetime.now(timezone.utc).isoformat(),
                                   resp.headers.get("Content-Type", ""))
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = type(exc).__name__
                self._sleep(2 ** attempt + random.uniform(0, 1))
        raise FetchError(f"Неуспешно след {MAX_ATTEMPTS} опита ({last_error}): {url}")
