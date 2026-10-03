"""Shared-password login for the hosted app (SYNTHETIC, no database needed)."""

import pytest
from fastapi.testclient import TestClient

from legal_ai.web import auth


@pytest.fixture()
def make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused@127.0.0.1/unused")
    monkeypatch.setenv("PRIVATE_STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("ALLOWED_HOSTS", "legal.example.app")
    auth._failures.clear()

    def make(base="https://legal.example.app"):
        from legal_ai.web.app import create_app
        return TestClient(create_app(), base_url=base)
    return make


def test_hosted_without_password_is_closed(make_client, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    for base in ("https://legal.example.app", "http://localhost"):
        c = make_client(base)
        r = c.get("/analyze", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"
        assert "APP_PASSWORD" in c.get("/login").text


def test_local_use_without_password_still_works(make_client, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("APP_REQUIRE_LOGIN", raising=False)
    assert make_client("http://127.0.0.1").get("/reports").status_code == 200
    r = make_client().get("/reports", follow_redirects=False)
    assert r.status_code == 303


def test_login_flow_and_cookie(make_client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "синтетична-парола-1")
    c = make_client()
    r = c.get("/reports", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=/reports")
    origin = {"origin": "https://legal.example.app"}
    r = c.post("/login", data={"password": "грешна", "next": "/reports"}, headers=origin, follow_redirects=False)
    assert r.status_code == 401 and "Грешна парола" in r.text
    r = c.post("/login", data={"password": "синтетична-парола-1", "next": "//evil.example", "remember": "1"},
               headers=origin, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/start"
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "синтетична" not in cookie
    assert c.get("/reports").status_code == 200
    c.get("/logout")
    assert c.get("/reports", follow_redirects=False).status_code == 303


def test_login_rate_limit_and_origin(make_client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "синтетична-парола-2")
    c = make_client()
    origin = {"origin": "https://legal.example.app"}
    assert c.post("/login", data={"password": "x"}, headers={"origin": "https://evil.example"}).status_code == 403
    for _ in range(5):
        c.post("/login", data={"password": "x"}, headers=origin)
    r = c.post("/login", data={"password": "синтетична-парола-2"}, headers=origin, follow_redirects=False)
    assert r.status_code == 401 and "Твърде много" in r.text


def test_cookie_signature_and_expiry(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "p")
    monkeypatch.setenv("SECRET_KEY", "k")
    v = auth.make_cookie(now=1000)
    assert auth.cookie_ok(v, now=1000 + 60)
    assert not auth.cookie_ok(v, now=1000 + auth.MAX_AGE + 1)
    assert not auth.cookie_ok(v[:-1] + ("0" if v[-1] != "0" else "1"), now=1001)
    monkeypatch.setenv("APP_PASSWORD", "changed")          # changing the password ends all sessions
    assert not auth.cookie_ok(v, now=1001)


def test_railway_healthcheck_host_allowed(make_client, monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setenv("APP_PASSWORD", "синтетична-парола-3")
    c = make_client("http://healthcheck.railway.app")
    r = c.get("/reports", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
