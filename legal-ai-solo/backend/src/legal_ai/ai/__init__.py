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
from typing import Any

import httpx

OPENAI_URL = "https://api.openai.com/v1/responses"


class AIError(Exception):
    pass


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
    )


class OpenAIProvider:
    """Responses API with strict json_schema output.

    The key comes from OPENAI_API_KEY; when it is absent the request is sent without an
    Authorization header (in the cloud dev environment a proxy adds the credential).
    """

    def __init__(self, config: AIConfig, transport: httpx.BaseTransport | None = None,
                 sleep=time.sleep, poll_seconds: float = 3.0, max_wait: float = 900.0) -> None:
        self.config = config
        self._sleep = sleep
        self._poll = poll_seconds
        self._max_wait = max_wait
        self._lock = threading.Lock()  # usage and the call cap are shared by worker threads
        self._reserved = 0
        self.usage = Usage()
        headers = {"Content-Type": "application/json"}
        key = os.environ.get("OPENAI_API_KEY", "")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        self._http = httpx.Client(timeout=httpx.Timeout(60.0), headers=headers, transport=transport)

    def close(self) -> None:
        self._http.close()

    def structured(self, *, model: str, system: str, user: str, schema_name: str,
                   schema: dict[str, Any], effort: str | None = None) -> dict[str, Any]:
        with self._lock:
            if self.usage.calls + self._reserved >= self.config.max_calls:
                raise AIError(f"Достигнат лимит от {self.config.max_calls} AI заявки за един анализ.")
            self._reserved += 1
        try:
            return self._structured(model, system, user, schema_name, schema, effort)
        finally:
            with self._lock:
                self._reserved -= 1

    def _structured(self, model, system, user, schema_name, schema, effort) -> dict[str, Any]:
        payload = {
            "model": model,
            "input": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "reasoning": {"effort": effort or self.config.reasoning_effort},
            "text": {"format": {"type": "json_schema", "name": schema_name, "strict": True,
                                "schema": schema}},
        }
        payload["background"] = True  # long reasoning must not hold one HTTP connection open
        last = ""
        for _ in range(2):
            data = self._run_background(payload)
            usage = data.get("usage") or {}
            with self._lock:
                self.usage.add(model, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)))
            if data.get("status") != "completed":
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

    def _request(self, method: str, url: str, **kw) -> dict[str, Any]:
        last = ""
        for attempt in range(4):
            try:
                resp = self._http.request(method, url, **kw)
            except httpx.TransportError as exc:
                last = type(exc).__name__
            else:
                if resp.status_code == 200:
                    return resp.json()
                last = f"HTTP {resp.status_code}: {resp.text[:300]}"
                if resp.status_code not in (429, 500, 502, 503, 504):
                    break
            self._sleep(2 ** attempt * 2)
        raise AIError(f"OpenAI: {last}")

    def _run_background(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._request("POST", OPENAI_URL, json=payload)
        waited = 0.0
        while data.get("status") in ("queued", "in_progress"):
            if waited >= self._max_wait:
                raise AIError(f"AI заявката не приключи за {self._max_wait:.0f} сек.")
            self._sleep(self._poll)
            waited += self._poll
            data = self._request("GET", f"{OPENAI_URL}/{data['id']}")
        return data
