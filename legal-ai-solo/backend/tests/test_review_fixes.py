"""Regression tests for the findings of the independent code review (03.10.2026). SYNTHETIC data,
no network and no AI calls."""

import threading
from datetime import date
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from legal_ai import tracing
from legal_ai.ai import AIConfig, AIError, OpenAIProvider
from legal_ai.sources.courts import CourtSite
from legal_ai.sources.courts.acts import ActRow
from legal_ai.web import auth
from legal_ai.web.jobs import JobRunner

OK = {"id": "r", "status": "completed", "usage": {}, "output": [
    {"type": "message", "content": [{"type": "output_text", "text": "{}"}]}]}


def _call(ai):
    return ai.structured(model="m", system="s", user="u", schema_name="x", schema={"type": "object"})


# 1. first instance: the court named in the appellate decision decides, and the act date confirms

EXTRA = {
    "os-smolyan": CourtSite("os-smolyan", "Окръжен съд Смолян", "smolyan-os.justice.bg", "1", "окръжен", "Смолян", "Пловдив"),
    "os-pazardzhik": CourtSite("os-pazardzhik", "Окръжен съд Пазарджик", "pazardzhik-os.justice.bg", "2", "окръжен",
                               "Пазарджик", "Пловдив"),
    "os-stara-zagora": CourtSite("os-stara-zagora", "Окръжен съд Стара Загора", "starazagora-os.justice.bg", "3",
                                 "окръжен", "Стара Загора", "Пловдив"),
    "os-varna": CourtSite("os-varna", "Окръжен съд Варна", "varna-os.justice.bg", "4", "окръжен", "Варна", "Варна"),
}


@pytest.fixture()
def courts(monkeypatch):
    from legal_ai.sources import courts as mod
    monkeypatch.setattr(mod, "COURTS", {**mod.COURTS, **EXTRA})
    monkeypatch.setattr(tracing, "COURTS", mod.COURTS)
    return mod.COURTS


@pytest.mark.parametrize("text,key,exact", [
    ("Окръжен съд Пловдив", "os-plovdiv", True), ("Пловдивски окръжен съд, ТО", "os-plovdiv", True),
    ("Окръжен съд Смолян", "os-smolyan", True), ("ОС – Пазарджик", "os-pazardzhik", True),
    ("Старозагорски окръжен съд", "os-stara-zagora", True), ("Окръжен съд – Варна", "os-varna", True),
    ("СмОС", "os-smolyan", False), ("О.С.-П.", "os-plovdiv", False), ("", "os-plovdiv", False),
    ("Районен съд Пловдив", None, False), ("ПдРС, ІV гр.с.", None, False)])
def test_resolve_lower_court(courts, text, key, exact):
    c, ex = tracing.resolve_lower_court(text, courts["as-plovdiv"])
    assert (c.key if c else None, ex) == (key, exact)


class _Fake:
    def __init__(self, body):
        self.body, self.urls = body, []

    def get(self, url):
        self.urls.append(url)
        return SimpleNamespace(body=self.body.encode())


def _trace(monkeypatch, lower, act_date=date(2021, 7, 6)):
    monkeypatch.setattr(tracing, "parse_acts", lambda page: [
        ActRow("Търговско дело", 1110, 2019, "", "Решение", act_date, "https://ecase.justice.bg/x")])
    courts, vks = _Fake("<html></html>"), _Fake("<div id='TablicaRezultati'></div>")
    path = tracing.trace(courts, vks, "as-plovdiv", 899, 2021, date(2022, 3, 14), lower)
    return path[0], courts.urls


def test_other_district_court_is_looked_up_there(monkeypatch, courts):
    first, urls = _trace(monkeypatch, {"case": "1110/2019", "court": "Окръжен съд Смолян", "date": "06.07.2021"})
    assert urls[0].startswith("https://smolyan-os.justice.bg/") and first.court == "Окръжен съд Смолян"
    assert len(first.acts) == 1 and not first.note


def test_unknown_court_is_not_looked_up(monkeypatch):
    first, urls = _trace(monkeypatch, {"case": "1110/2019", "court": "Районен съд Пловдив", "date": "06.07.2021"})
    assert urls == [] and first.acts == [] and first.note


def test_abbreviated_court_is_linked_only_with_matching_date(monkeypatch):
    first, urls = _trace(monkeypatch, {"case": "1110/2019", "court": "О.С.-П", "date": "06.07.2021"})
    assert len(urls) == 1 and len(first.acts) == 1 and "съкратено" in first.note
    first, _ = _trace(monkeypatch, {"case": "1110/2019", "court": "О.С.-П", "date": "06.07.2021"},
                      act_date=date(2020, 1, 1))
    assert first.acts == [] and "не е свързано" in first.note
    first, _ = _trace(monkeypatch, {"case": "1110/2019", "court": "Окръжен съд Пловдив", "date": ""})
    assert first.acts == [] and "не е свързано" in first.note


# 2. a POST that may have reached OpenAI is never repeated; every POST counts against the cap

def test_lost_post_answer_is_not_repeated():
    posts = []

    def handler(request):
        posts.append(request.method)
        raise httpx.ReadTimeout("lost", request=request)

    ai = OpenAIProvider(AIConfig("m", "m", max_calls=1), transport=httpx.MockTransport(handler), sleep=lambda s: None)
    with pytest.raises(AIError, match="не е изпратена повторно"):
        _call(ai)
    assert posts == ["POST"] and ai.calls_started == 1 and ai.uncertain == 1


def test_safe_post_retries_count_against_the_cap():
    answers = [httpx.Response(429, text="rate limit"), httpx.Response(200, json=OK)]

    def handler(request):
        return answers.pop(0)

    ai = OpenAIProvider(AIConfig("m", "m", max_calls=1), transport=httpx.MockTransport(handler), sleep=lambda s: None)
    with pytest.raises(AIError, match="лимит от 1"):
        _call(ai)   # the retry after 429 would be a second paid request: refused by the cap

    answers[:] = [httpx.Response(429, text="rate limit"), httpx.Response(200, json=OK)]
    ai = OpenAIProvider(AIConfig("m", "m", max_calls=2), transport=httpx.MockTransport(handler), sleep=lambda s: None)
    assert _call(ai) == {} and ai.calls_started == 2


def test_server_error_on_post_is_not_repeated():
    posts = []

    def handler(request):
        posts.append(1)
        return httpx.Response(502, text="bad gateway")

    ai = OpenAIProvider(AIConfig("m", "m", max_calls=5), transport=httpx.MockTransport(handler), sleep=lambda s: None)
    with pytest.raises(AIError):
        _call(ai)
    assert len(posts) == 1 and ai.uncertain == 1


# 3. one analysis at a time, checked and accepted under one lock

def test_runner_accepts_one_job_under_concurrency(tmp_path, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(JobRunner, "_run", lambda self, job: gate.wait(5))
    runner = JobRunner(tmp_path / "runs", tmp_path / "traces")
    barrier = threading.Barrier(8)
    got = []

    def go():
        barrier.wait()
        got.append(runner.start({"mode": "noai"}))

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    gate.set()
    assert sum(j is not None for j in got) == 1 and len(runner.jobs) == 1


def test_result_directories_never_collide(tmp_path):
    from legal_ai.cassation.pipeline import unique_dir
    a = unique_dir(tmp_path, "2026-10-03T10:00:00+00:00")
    b = unique_dir(tmp_path, "2026-10-03T10:00:00+00:00")
    assert a != b and a.name.isdigit() and b.name.isdigit()


# 5. login limit: the client cannot pick its own address

def test_client_address_uses_the_proxy_entry():
    assert auth.client_address("1.1.1.1, 9.9.9.9", "10.0.0.1") == "9.9.9.9"
    assert auth.client_address("", "10.0.0.1") == "10.0.0.1"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused@127.0.0.1/unused")
    monkeypatch.setenv("PRIVATE_STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("ALLOWED_HOSTS", "legal.example.app")
    auth._failures.clear()
    from legal_ai.web.app import create_app
    return TestClient(create_app(), base_url="https://legal.example.app")


def test_rotating_forwarded_for_does_not_bypass_the_limit(client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "синтетична-парола-3")
    origin = {"origin": "https://legal.example.app"}
    for i in range(6):
        client.post("/login", data={"password": "x"}, headers={**origin, "x-forwarded-for": f"10.0.0.{i}"})
    r = client.post("/login", data={"password": "синтетична-парола-3"},
                    headers={**origin, "x-forwarded-for": "10.0.0.99"}, follow_redirects=False)
    assert r.status_code == 401 and "Твърде много" in r.text


def test_on_railway_only_the_proxy_entry_counts(client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "синтетична-парола-3")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    origin = {"origin": "https://legal.example.app"}
    for i in range(6):
        client.post("/login", data={"password": "x"},
                    headers={**origin, "x-forwarded-for": f"10.0.0.{i}, 203.0.113.7"})
    r = client.post("/login", data={"password": "синтетична-парола-3"},
                    headers={**origin, "x-forwarded-for": "10.0.0.99, 203.0.113.7"}, follow_redirects=False)
    assert r.status_code == 401 and "Твърде много" in r.text


def test_global_failure_cap():
    auth._failures.clear()
    for i in range(30):
        auth.record_failure(f"c{i}", now=1000.0)
    assert auth.too_many_failures("new-client", now=1001.0)
    assert not auth.too_many_failures("new-client", now=1700.0)


def test_session_key_survives_restart(tmp_path, monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("PRIVATE_STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("APP_PASSWORD", "синтетична-парола-4")
    monkeypatch.setattr(auth._secret, "key", "")
    cookie = auth.make_cookie()
    monkeypatch.setattr(auth._secret, "key", "")   # a new process reads the same key file
    assert auth.cookie_ok(cookie) and (tmp_path / ".session_key").exists()


# 6. "practice up to" a month: validated and up to the month's last day

def test_until_month_is_validated(client, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("APP_REQUIRE_LOGIN", raising=False)
    c = TestClient(client.app, base_url="http://127.0.0.1")
    r = c.post("/analyze", data={"court": "as-plovdiv", "case": "1", "year": "2020", "until": "2022-13"},
               headers={"origin": "http://127.0.0.1"}, follow_redirects=False)
    assert r.status_code == 400 and "ГГГГ-ММ" in r.text


# free text in the form

def _local(client, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("APP_REQUIRE_LOGIN", raising=False)
    return TestClient(client.app, base_url="http://127.0.0.1")


def test_pasted_text_starts_an_analysis(client, monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(JobRunner, "_run", lambda self, job: started.append(job.params))
    c = _local(client, monkeypatch)
    text = "Въззивният съд приема, че искът е неоснователен. " * 10
    r = c.post("/analyze", data={"text": text}, headers={"origin": "http://127.0.0.1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/jobs/")
    p = started[0]
    assert p["pasted"] and p["filename"].endswith(".txt")
    assert (tmp_path / p["file"]).read_text(encoding="utf-8") == text.strip()


def test_pasted_text_too_short_or_with_a_file(client, monkeypatch):
    c = _local(client, monkeypatch)
    h = {"origin": "http://127.0.0.1"}
    r = c.post("/analyze", data={"text": "кратко"}, headers=h)
    assert r.status_code == 400 and "поне 300 знака" in r.text
    r = c.post("/analyze", data={"text": "дълъг текст " * 40}, headers=h,
               files={"document": ("a.txt", ("текст " * 100).encode(), "text/plain")})
    assert r.status_code == 400 and "не и двете" in r.text


def test_form_has_the_text_field(client, monkeypatch):
    monkeypatch.setattr("legal_ai.web.app.connect", lambda url: (_ for _ in ()).throw(
        __import__("psycopg").OperationalError("no db")))
    r = _local(client, monkeypatch).get("/analyze")
    assert r.status_code == 200 and 'name="text"' in r.text


# the court and the case are read from the document itself

HEAD = ("РЕШЕНИЕ № 125 гр. Пловдив, 14.03.2022 г. В ИМЕТО НА НАРОДА АПЕЛАТИВЕН СЪД – ПЛОВДИВ, 2-РИ ТЪРГОВСКИ "
        "СЪСТАВ, в публично заседание ... Въззивно търговско дело № 20215001000899 по описа за 2021 година ")


def test_case_header_from_document():
    from legal_ai.cassation.noai import extract_case_header
    h = extract_case_header(HEAD)
    assert (h["court"], h["number"], h["year"], h["kind"]) == ("Апелативен съд Пловдив", 899, 2021,
                                                               "Въззивно търговско дело")
    h = extract_case_header("РЕШЕНИЕ № 5 ОКРЪЖЕН СЪД – СМОЛЯН, гражданска колегия, въззивно гражданско дело № 123/2022 г.")
    assert (h["court"], h["number"], h["year"]) == ("Окръжен съд Смолян", 123, 2022)
    h = extract_case_header("СОФИЙСКИ ГРАДСКИ СЪД, ГО, II-В състав, в.гр.д. № 4567 по описа за 2022 г.")
    assert (h["court"], h["number"], h["year"]) == ("Софийски градски съд", 4567, 2022)
    assert extract_case_header("Някакъв текст без заглавие на съдебно решение.") is None


def test_identify_fills_the_case_only_for_listed_courts():
    p = {"file": "uploads/x.txt"}
    JobRunner._identify(p, HEAD)
    assert (p["court"], p["case"], p["year"]) == ("as-plovdiv", 899, 2021) and p["identified"]["supported"]
    p = {"file": "uploads/x.txt"}
    JobRunner._identify(p, "ОКРЪЖЕН СЪД – НЕСЪЩЕСТВУВАЩ ГРАД, въззивно гражданско дело № 1/2022 г.")
    assert "court" not in p and p["identified"]["supported"] is False
    p = {"file": "uploads/x.txt", "court": "as-plovdiv", "case": 5, "year": 2020}
    JobRunner._identify(p, HEAD)
    assert p["case"] == 5 and "identified" not in p
