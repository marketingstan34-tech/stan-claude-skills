"""Approximate cost of AI calls, from the token counts the provider (OpenAI or Anthropic) reports.

Prices in USD per 1M tokens (input, output), as published for these models in October 2026
(OpenAI pricing page; also listed by OpenRouter). Output includes reasoning tokens. The real
amount is in OpenAI → Usage; this is only an estimate for the lawyer.
"""

from __future__ import annotations

PRICES_USD_PER_M = {"gpt-5.5": (5.00, 30.00), "gpt-5.4-mini": (0.75, 4.50),
                    # Anthropic list prices (11.2026); Haiku 5.5 for prompts up to 100K tokens;
                    # Opus 5 / Sonnet 5.5 can answer when a refusal falls back to them
                    "claude-opus-5-5": (4.00, 20.00), "claude-haiku-5-5": (0.10, 0.50),
                    "claude-opus-5": (5.00, 25.00), "claude-sonnet-5-5": (2.00, 10.00)}
PRICES_AS_OF = "10.2026"
# measured on a real report (02.10.2026): 46 calls, 166 143 / 25 317 tokens -> 0.71 USD; benchmark
# 05.10.2026: 0.86 USD per report with 3 acts per question. With 6 per question (05.10.2026): estimate
TYPICAL_REPORT_USD = (1.0, 1.4)
# Claude (Opus 5.5 + Haiku 5.5 filter): estimate from the per-token prices, not yet measured
TYPICAL_REPORT_USD_CLAUDE = (0.7, 1.2)
# appeal-2 (03.10.2026): a long appeal with the case documents and style samples; estimate, not measured
TYPICAL_APPEAL_USD = (0.8, 2.0)
# „Съдия от ВКС": decision + statement + appeal in, a short review out; estimate
TYPICAL_JUDGE_USD = (0.2, 0.5)


def price(model: str) -> tuple[float, float] | None:
    for prefix in sorted(PRICES_USD_PER_M, key=len, reverse=True):
        if model.startswith(prefix):
            return PRICES_USD_PER_M[prefix]
    return None


def cost_usd(by_model: dict) -> float | None:
    """Sum over {model: [input_tokens, output_tokens]}; None if a model has no known price."""
    total = 0.0
    for model, (tin, tout) in (by_model or {}).items():
        pr = price(model)
        if pr is None:
            return None
        total += tin / 1e6 * pr[0] + tout / 1e6 * pr[1]
    return total


def report_usd() -> tuple[float, float]:
    """Typical cost of one report for the provider in AI_PROVIDER."""
    from legal_ai.ai import provider_name
    try:
        return TYPICAL_REPORT_USD_CLAUDE if provider_name() == "anthropic" else TYPICAL_REPORT_USD
    except Exception:  # noqa: BLE001 - a bad AI_PROVIDER is reported when a report starts
        return TYPICAL_REPORT_USD


def vendor() -> dict[str, str]:
    """Name and billing page of the provider in AI_PROVIDER, for the page texts."""
    from legal_ai.ai import provider_name
    try:
        if provider_name() == "anthropic":
            return {"name": "Anthropic", "billing": "https://platform.claude.com/settings/billing"}
    except Exception:  # noqa: BLE001
        pass
    return {"name": "OpenAI", "billing": "https://platform.openai.com/settings/organization/billing/overview"}
