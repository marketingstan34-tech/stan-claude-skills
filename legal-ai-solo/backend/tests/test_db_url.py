from legal_ai.db import describe_target, to_psycopg_url, with_required_ssl


def test_remote_hosts_require_ssl():
    url = with_required_ssl("postgresql+psycopg://u:p@aws-0-eu-central-1.pooler.supabase.com:5432/postgres")
    assert url.endswith("?sslmode=require")
    weak = with_required_ssl("postgresql://u:p@db.example.com/postgres?sslmode=prefer")
    assert "sslmode=require" in weak
    strict = with_required_ssl("postgresql://u:p@db.example.com/postgres?sslmode=verify-full")
    assert "sslmode=verify-full" in strict


def test_local_and_compose_hosts_unchanged():
    for host in ("127.0.0.1", "localhost", "postgres"):
        url = f"postgresql+psycopg://u:p@{host}:5432/legal_ai"
        assert with_required_ssl(url) == url


def test_psycopg_url_and_description_hide_nothing_extra():
    assert to_psycopg_url("postgresql+psycopg://u:p@127.0.0.1:5432/x").startswith("postgresql://")
    assert describe_target("postgresql+psycopg://user:secret@h.example.com:6543/postgres") == \
        "h.example.com:6543/postgres"
