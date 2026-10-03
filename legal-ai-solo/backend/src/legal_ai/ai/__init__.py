"""AI provider: structured (JSON-schema) calls with token accounting.

The model only sees text we give it; it has no tools, network or files. Its output is
validated against the schema and every quote it returns is checked against source text
by the caller before anything is shown.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx

OPENAI_URL = "https://api.openai.com/v1/responses"


class AIError(Exception):
    pass


class AIQuotaError(AIError):
    """No credit / quota left: stop the whole run instead of failing call after call."""


_QUOTA_CODES = {"credit_balance_exhausted", "insufficient_quota", "billing_hard_limit_reached"}

# Background mode: the first status checks come quickly (short answers finish in seconds),
# then every `poll_seconds`. Only the waiting changes; the answer and the max wait do not.
POLL_BACKOFF = (0.5, 1.0, 1.5, 2.0)
DEFAULT_WORKERS = 4
MAX_WORKERS = 12


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    by_model: dict[str, list[int]] = field(default_factory=dict)

    def add(self, model: str, inp: int, out: int) -> None:
        self.calls += 1
        self.input_tokens += inp
        self.output_tokens += out
        m = self.by_model.setdefault(model, [0, 0])
        m[0] += inp
        m[1] += out


@dataclass(frozen=True)
class AIConfig:
    analysis_model: str
    light_model: str
    max_calls: int = 60          # hard cap per analysis run
    reasoning_effort: str = "medium"
    assess_model: str = ""       # stage 1: relevance filter; defaults to light_model
    assess_effort: str = "low"
    stance_model: str = ""       # stage 2: stance + quote for relevant acts; defaults to analysis_model
    stance_effort: str = "low"
    background: bool = True      # background mode + polling (survives proxy 502s on long calls)
    poll_backoff: bool = True    # 0.5, 1, 1.5, 2 s, then every poll_seconds; False = fixed interval
    workers: int = DEFAULT_WORKERS  # parallel assessments in one analysis run (1..MAX_WORKERS)


def _flag(var: str, default: bool) -> bool:
    v = os.environ.get(var, "").strip().lower()
    if not v:
        return default
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    raise AIError(f"{var} трябва да е 1 или 0, а е „{v}“.")


def runtime_env() -> dict[str, Any]:
    """AI_BACKGROUND, AI_POLL_BACKOFF and AI_WORKERS (clamped to 1..MAX_WORKERS)."""
    raw = os.environ.get("AI_WORKERS", "").strip()
    try:
        workers = int(raw) if raw else DEFAULT_WORKERS
    except ValueError:
        raise AIError(f"AI_WORKERS трябва да е цяло число от 1 до {MAX_WORKERS}, а е „{raw}“.") from None
    return {"background": _flag("AI_BACKGROUND", True),
            "poll_backoff": _flag("AI_POLL_BACKOFF", True),
            "workers": min(max(workers, 1), MAX_WORKERS)}


def poll_delays(steady: float, backoff: bool = True) -> Iterator[float]:
    """Endless waits between status checks of a background response."""
    if backoff:
        yield from POLL_BACKOFF
    while True:
        yield steady


def load_ai_config() -> AIConfig:
    def model(var: str) -> str:
        v = os.environ.get(var, "")
        if not v or v.startswith("SET_"):
            raise AIError(f"{var} не е зададен (валиден model ID на OpenAI).")
        return v
    return AIConfig(
        analysis_model=model("AI_ANALYSIS_MODEL"),
        light_model=model("AI_QUERY_MODEL"),
        max_calls=int(os.environ.get("AI_MAX_CALLS_PER_RUN", "60")),
        reasoning_effort=os.environ.get("AI_REASONING_EFFORT", "medium"),
        assess_model=os.environ.get("AI_ASSESS_MODEL", "") or model("AI_QUERY_MODEL"),
        assess_effort=os.environ.get("AI_ASSESS_EFFORT", "low"),
        stance_model=os.environ.get("AI_STANCE_MODEL", "") or model("AI_ANALYSIS_MODEL"),
        stance_effort=os.environ.get("AI_STANCE_EFFORT", "low"),
        **runtime_env(),
    )


class OpenAIProvider:
    """Responses API with strict json_schema output.

    The key comes from OPENAI_API_KEY; when it is absent the request is sent without an
    Authorization header (in the cloud dev environment a proxy adds the credential).

    The call cap counts model requests (every attempt inside `structured`). A slot is
    reserved under a lock before the request is sent, so parallel workers never exceed
    `max_calls` and never refuse each other while slots are left.
    """

    def __init__(self, config: AIConfig, transport: httpx.BaseTransport | None = None,
                 sleep=time.sleep, poll_seconds: float = 3.0, max_wait: float = 900.0) -> None:
        self.config = config
        self._sleep = sleep
        self._poll = poll_seconds
        self._max_wait = max_wait
        self._lock = threading.Lock()  # usage and the call cap are shared by worker threads
        self._started = 0  # model requests sent or about to be sent; the cap counts these
        self.uncertain = 0  # POSTs whose outcome is unknown (may have been created and billed)
        self.usage = Usage()
        headers = {"Content-Type": "application/json"}
        key = os.environ.get("OPENAI_API_KEY", "")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        self._http = httpx.Client(timeout=httpx.Timeout(60.0), headers=headers, transport=transport)

    def close(self) -> None:
        self._http.close()

    @property
    def calls_started(self) -> int:
        with self._lock:
            return self._started

    def _reserve_call(self) -> None:
        with self._lock:
            if self._started >= self.config.max_calls:
                raise AIError(f"Достигнат лимит от {self.config.max_calls} AI заявки за един анализ.")
            self._started += 1

    def structured(self, *, model: str, system: str, user: str, schema_name: str,
                   schema: dict[str, Any], effort: str | None = None) -> dict[str, Any]:
        payload = {
            "model": model,
            "input": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "reasoning": {"effort": effort or self.config.reasoning_effort},
            "text": {"format": {"type": "json_schema", "name": schema_name, "strict": True,
                                "schema": schema}},
        }
        if self.config.background:
            payload["background"] = True  # long reasoning must not hold one HTTP connection open
        last = ""
        for _ in range(2):
            data = self._run(payload)
            usage = data.get("usage") or {}
            with self._lock:
                self.usage.add(model, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)))
            if data.get("status") != "completed":
                err = data.get("error") or {}
                if isinstance(err, dict) and err.get("code") in _QUOTA_CODES:
                    raise AIQuotaError("Кредитът в OpenAI е изчерпан. Добавете кредит в "
                                       "platform.openai.com → Billing и пуснете анализа отново.")
                last = f"status={data.get('status')} {data.get('incomplete_details') or data.get('error')}"
                continue
            text = "".join(
                c.get("text", "")
                for o in data.get("output", []) if o.get("type") == "message"
                for c in o.get("content", []) or [] if c.get("type") == "output_text"
            )
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                last = f"невалиден JSON: {exc}"
        raise AIError(f"AI отговорът не е валиден след 2 опита ({last}).")

    def _uncertain(self, why: str) -> AIError:
        with self._lock:
            self.uncertain += 1
        return AIError(f"OpenAI: {why}. Не е ясно дали заявката е приета (възможно е да е таксувана); "
                       "не е изпратена повторно.")

    def _request(self, method: str, url: str, *, create: bool = False, **kw) -> dict[str, Any]:
        """One API call. Status checks (GET) are retried freely. A request that creates a model
        response (POST, `create`) counts against the cap on every attempt and is sent again only
        when it surely did not reach OpenAI (no connection, 429 or 503); after a lost answer
        it is not repeated, so one question is never billed twice."""
        last = ""
        for attempt in range(4):
            if create:
                self._reserve_call()
            try:
                resp = self._http.request(method, url, **kw)
            except httpx.ReadTimeout as exc:
                if "timeout" in kw:  # a direct (non-background) call that used up the max wait
                    if create:
                        with self._lock:
                            self.uncertain += 1
                    raise AIError(f"AI заявката не приключи за {self._max_wait:.0f} сек.") from exc
                if create:
                    raise self._uncertain(type(exc).__name__) from exc
                last = type(exc).__name__
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                last = type(exc).__name__   # never sent
            except httpx.TransportError as exc:
                if create:
                    raise self._uncertain(type(exc).__name__) from exc
                last = type(exc).__name__
            else:
                if resp.status_code == 200:
                    return resp.json()
                last = f"HTTP {resp.status_code}: {resp.text[:300]}"
                if any(code in resp.text for code in _QUOTA_CODES):
                    raise AIQuotaError("Кредитът в OpenAI е изчерпан. Добавете кредит в "
                                       "platform.openai.com → Billing и пуснете анализа отново.")
                if create and resp.status_code in (500, 502, 504):
                    raise self._uncertain(f"HTTP {resp.status_code}")
                if resp.status_code not in (429, 500, 502, 503, 504):
                    break
            self._sleep(2 ** attempt * 2)
        raise AIError(f"OpenAI: {last}")

    def _run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("background"):
            data = self._request("POST", OPENAI_URL, create=True, json=payload)
        else:  # direct request: the answer comes in this response, so wait up to max_wait
            data = self._request("POST", OPENAI_URL, create=True, json=payload,
                                 timeout=httpx.Timeout(self._max_wait, connect=30.0))
        waited = 0.0
        delays = poll_delays(self._poll, self.config.poll_backoff)
        while data.get("status") in ("queued", "in_progress"):
            if waited >= self._max_wait:
                raise AIError(f"AI заявката не приключи за {self._max_wait:.0f} сек.")
            delay = next(delays)
            self._sleep(delay)
            waited += delay
            data = self._request("GET", f"{OPENAI_URL}/{data['id']}")
        return data
