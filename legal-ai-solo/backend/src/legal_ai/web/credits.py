"""Estimate of the OpenAI credit left, for the bar in the menu.

OpenAI does not give the prepaid balance to an ordinary API key, so the lawyer types the
balance shown on the OpenAI billing page once (after each top-up) and the program subtracts
the estimated cost of every report and appeal made after that moment. Failed runs and any
other use of the same key are not counted, so the figure is approximate ("≈").
Kept in credits.json in private storage.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from legal_ai.ai.pricing import TYPICAL_REPORT_USD, cost_usd

BILLING_URL = "https://platform.openai.com/settings/organization/billing/overview"
LOW_USD = 2.0


def _parse_time(value: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def load(storage: Path) -> dict | None:
    try:
        data = json.loads((storage / "credits.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("balance"), (int, float)) or not _parse_time(data.get("at")):
        return None
    return data


def save(storage: Path, balance: float, now: datetime | None = None) -> None:
    storage.mkdir(parents=True, exist_ok=True)
    tmp = storage / "credits.json.tmp"
    tmp.write_text(json.dumps({"balance": round(balance, 2),
                               "at": (now or datetime.now(timezone.utc)).isoformat()}), encoding="utf-8")
    os.replace(tmp, storage / "credits.json")


def parse_balance(text: str) -> float | None:
    t = (text or "").replace("$", "").replace(" ", "").replace(" ", "").replace(",", ".").strip()
    try:
        value = float(t)
    except ValueError:
        return None
    return value if 0 <= value < 1_000_000 else None


def spent_since(runs_dir: Path, since: datetime) -> float:
    """Estimated USD of the AI reports and appeals created after `since`."""
    total = 0.0
    stamp = since.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")
    if not runs_dir.is_dir():
        return 0.0
    for d in runs_dir.iterdir():
        if not d.is_dir() or not re.fullmatch(r"\d{14}(\d{2})?", d.name):
            continue
        if d.name[:14] >= stamp:      # the folder name is the report's creation time (UTC)
            try:
                run = json.loads((d / "run.json").read_text(encoding="utf-8"))
                created = _parse_time(run.get("created_at"))
                if created and created >= since:
                    total += cost_usd((run.get("usage") or {}).get("by_model") or {}) or 0.0
            except (OSError, ValueError, AttributeError):
                pass
        appeal_file = d / "appeal.json"
        try:
            if appeal_file.stat().st_mtime < since.timestamp():
                continue
            appeal = json.loads(appeal_file.read_text(encoding="utf-8"))
            created = _parse_time(appeal.get("created_at"))
            usage = appeal.get("usage") or {}
            if created and created >= since:
                total += cost_usd({appeal.get("model", ""): [usage.get("input_tokens", 0),
                                                             usage.get("output_tokens", 0)]}) or 0.0
        except (OSError, ValueError, AttributeError):
            pass
    return total


def summary(storage: Path, runs_dir: Path) -> dict:
    """What the bar shows. `set` is False until the lawyer enters the balance once."""
    data = load(storage)
    if data is None:
        return {"set": False, "url": BILLING_URL}
    at = _parse_time(data["at"])
    balance = float(data["balance"])
    spent = spent_since(runs_dir, at)
    left = max(balance - spent, 0.0)
    return {"set": True, "url": BILLING_URL, "balance": balance, "spent": spent, "left": left,
            "pct": round(100 * left / balance) if balance > 0 else 0,
            "reports_left": int(left // TYPICAL_REPORT_USD[1]), "low": left < LOW_USD,
            "at": at.astimezone(timezone.utc).strftime("%d.%m.%Y")}
