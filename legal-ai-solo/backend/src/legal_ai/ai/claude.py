"""Claude (Anthropic) provider: the same `structured` call as OpenAIProvider, through the
official SDK, with JSON-schema structured outputs.

Chosen with AI_PROVIDER=anthropic; the key comes from ANTHROPIC_API_KEY (set only in the
host's variables). Responses are streamed, so long analyses do not hit HTTP timeouts.
"""

from __future__ import annotations

import json
import threading
from typing import Any

import anthropic

from legal_ai.ai import AIConfig, AIError, AIQuotaError, Usage

MAX_TOKENS = 64000
# Claude effort levels; OpenAI's "minimal" / "none" have no equivalent
_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
# models that may decline a request for safety reasons and accept a server-side fallback
# to another model for that request (Haiku has none)
_FALLBACK_PREFIXES = ("claude-opus-", "claude-sonnet-", "claude-fable-")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
_QUOTA_ERRORS = (anthropic.PaymentRequiredError, anthropic.EnforcedSpendLimitReachedError,
                 anthropic.ContractSpendLimitReachedError, anthropic.OrganizationOnHoldError)
QUOTA_MESSAGE = ("Кредитът в Anthropic е изчерпан или е достигнат лимит за харчене. Добавете кредит в "
                 "platform.claude.com → Billing и пуснете анализа отново.")


def effort_level(value: str | None, default: str) -> str:
    v = (value or default).strip().lower()
    return v if v in _EFFORTS else "low"


class ClaudeProvider:
    """Messages API with `output_config.format` (json_schema). The call cap counts model
    requests: a slot is reserved under a lock before each request, as in OpenAIProvider."""

    def __init__(self, config: AIConfig, client: Any = None) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._started = 0
        self.uncertain = 0
        self.usage = Usage()
        # retries cover 429 / 5xx / lost connections before the answer starts
        self._client = client or anthropic.Anthropic(max_retries=2)

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close:
            close()

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
        level = effort_level(effort, self.config.reasoning_effort)
        kwargs: dict[str, Any] = {
            "model": model, "max_tokens": MAX_TOKENS, "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"effort": level, "format": {"type": "json_schema", "schema": schema}},
        }
        if model.startswith(_FALLBACK_PREFIXES):
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        last = ""
        for _ in range(2):
            self._reserve_call()
            try:
                with self._client.beta.messages.stream(**kwargs) as stream:
                    msg = stream.get_final_message()
            except _QUOTA_ERRORS as exc:
                raise AIQuotaError(QUOTA_MESSAGE) from exc
            except anthropic.BadRequestError as exc:
                if "credit balance" in str(exc).lower():
                    raise AIQuotaError(QUOTA_MESSAGE) from exc
                raise AIError(f"Claude: {exc}") from exc
            except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
                with self._lock:
                    self.uncertain += 1
                raise AIError(f"Claude: {type(exc).__name__}. Не е ясно дали заявката е приета "
                              "(възможно е да е таксувана); не е изпратена повторно.") from exc
            except anthropic.APIStatusError as exc:
                raise AIError(f"Claude: HTTP {exc.status_code}: {str(exc)[:300]}") from exc
            u = msg.usage
            inp = (u.input_tokens or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0) \
                + (getattr(u, "cache_read_input_tokens", 0) or 0)
            with self._lock:
                self.usage.add(msg.model or model, inp, u.output_tokens or 0)
            if msg.stop_reason == "refusal":
                raise AIError("Claude отказа да отговори на тази заявка (предпазен филтър).")
            if msg.stop_reason == "max_tokens":
                last = "отговорът е прекъснат (max_tokens)"
                continue
            text = "".join(b.text for b in msg.content if b.type == "text")
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                last = f"невалиден JSON: {exc}"
        raise AIError(f"AI отговорът не е валиден след 2 опита ({last}).")
