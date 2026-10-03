"""Approximate cost of AI calls, from the token counts OpenAI reports.

Prices in USD per 1M tokens (input, output), as published for these models in October 2026
(OpenAI pricing page; also listed by OpenRouter). Output includes reasoning tokens. The real
amount is in OpenAI → Usage; this is only an estimate for the lawyer.
"""

from __future__ import annotations

PRICES_USD_PER_M = {"gpt-5.5": (5.00, 30.00), "gpt-5.4-mini": (0.75, 4.50)}
PRICES_AS_OF = "10.2026"
# measured on a real report (02.10.2026): 46 calls, 166 143 / 25 317 tokens -> 0.71 USD
TYPICAL_REPORT_USD = (0.7, 1.0)
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
