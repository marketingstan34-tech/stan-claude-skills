"""Database access (psycopg 3). Schema lives in migrations/."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg
from psycopg.rows import dict_row

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres", ""}


def with_required_ssl(database_url: str) -> str:
    """Remote databases (e.g. Supabase) must use TLS; local/Compose ones may not."""
    parts = urlsplit(database_url)
    if (parts.hostname or "") in _LOCAL_HOSTS:
        return database_url
    query = dict(parse_qsl(parts.query))
    if query.get("sslmode") in (None, "", "disable", "allow", "prefer"):
        query["sslmode"] = "require"
    return urlunsplit(parts._replace(query=urlencode(query)))


def to_sqlalchemy_url(database_url: str) -> str:
    """SQLAlchemy needs the psycopg 3 driver named (hosting platforms give postgresql://)."""
    url = with_required_ssl(database_url)
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def to_psycopg_url(database_url: str) -> str:
    """Accept SQLAlchemy-style URLs (postgresql+psycopg://) as well."""
    url = with_required_ssl(database_url).replace("postgresql+psycopg://", "postgresql://", 1)
    return "postgresql://" + url[len("postgres://"):] if url.startswith("postgres://") else url


def describe_target(database_url: str) -> str:
    """Host/port/db without credentials, for logs and messages."""
    parts = urlsplit(database_url)
    return f"{parts.hostname}:{parts.port or 5432}{parts.path}"


def connect(database_url: str) -> psycopg.Connection:
    return psycopg.connect(to_psycopg_url(database_url), row_factory=dict_row)
