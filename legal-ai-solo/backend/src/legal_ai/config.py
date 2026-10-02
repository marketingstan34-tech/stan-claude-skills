"""Settings from environment variables. No secrets have defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    private_storage_path: Path
    source_min_interval_seconds: float
    source_user_agent: str


def load_settings() -> Settings:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url or "REPLACE_ME" in database_url:
        raise RuntimeError(
            "DATABASE_URL не е зададен. Копирайте .env.example в .env и попълнете стойностите."
        )
    return Settings(
        database_url=database_url,
        private_storage_path=Path(os.environ.get("PRIVATE_STORAGE_PATH", "data")),
        source_min_interval_seconds=max(
            2.0, float(os.environ.get("SOURCE_MIN_INTERVAL_SECONDS", "2"))
        ),
        source_user_agent=os.environ.get(
            "SOURCE_USER_AGENT", "legal-ai-solo/0.1 (private research tool)"
        ),
    )
