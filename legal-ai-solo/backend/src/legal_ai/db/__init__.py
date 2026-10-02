"""Database access (psycopg 3). Schema lives in migrations/."""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row


def to_psycopg_url(database_url: str) -> str:
    """Accept SQLAlchemy-style URLs (postgresql+psycopg://) as well."""
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def connect(database_url: str) -> psycopg.Connection:
    return psycopg.connect(to_psycopg_url(database_url), row_factory=dict_row)
