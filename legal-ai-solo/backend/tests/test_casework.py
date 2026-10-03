"""Deadline, threshold, the lawyer's case data, the draft with it and the attachments ZIP. SYNTHETIC data."""

import io
import json
import zipfile
from datetime import date

import pytest
from fastapi.testclient import TestClient

from legal_ai.cassation import casefile
from legal_ai.cassation.attachments import build_zip
from legal_ai.cassation.deadline import appeal_deadline, holidays, orthodox_easter, threshold_check
from legal_ai.cassation.draft import attached_labels, build_draft, to_text


@pytest.mark.parametrize("year,easter", [(2024, date(2024, 5, 5)), (2025, date(2025, 4, 20)), (2026, date(2026, 4, 12))])
def test_orthodox_easter(year, easter):
    assert orthodox_easter(year) == easter


def test_deadline_rules():
    assert date(2026, 12, 28) in holidays(2026)            # 26.12.2026 is a Saturday
    d = appeal_deadline(date(2026, 1, 31))                  # no 31 February: last day of the month
    assert d.nominal == date(2026, 2, 28) and d.last_day == date(2026, 3, 2) and d.moved
    d = appeal_deadline(date(2026, 3, 10))                  # 10.04.2026 is Good Friday
    assert d.last_day == date(2026, 4, 14)
    d = appeal_deadline(date(2026, 10, 3))
    assert d.last_day == date(2026, 11, 3) and not d.moved


def test_threshold():
    assert threshold_check(5000, "BGN", "граждански", False).ok is False
    assert threshold_check(5000.01, "BGN", "граждански", False).ok is True
    assert threshold_check(15000, "BGN", "търговски", False).ok is False
    assert threshold_check(11000, "EUR", "търговски", False).ok is True     # 21 514 лв.
    assert threshold_check(100, "BGN", "граждански", True).ok is True
    assert threshold_check(None, "BGN", "граждански", False).ok is None


RUN = {
    "appellate": {"label": "Апелативен съд Пловдив, Въззивно търговско дело № 899/2021, Решение от 14.03.2022"},
    "analysis": {"case_summary": "Казус.", "lower_instance": {"act": "", "date": "", "case": "", "court": ""},
                 "search": [], "holdings": [{"id": "H1", "summary": "Извод.", "quote": ""}],
                 "questions": [{"id": "Q1", "text": "Въпрос едно?", "kind": "материалноправен", "holding_ids": ["H1"], "ground": "т.1"},
                               {"id": "Q2", "text": "Въпрос две?", "kind": "процесуалноправен", "holding_ids": [], "ground": "т.1"},
                               {"id": "Q3", "text": "Въпрос три?", "kind": "материалноправен", "holding_ids": [], "ground": "т.3"}]},
    "holding_quotes": {},
    "assessments": [{"question_id": "Q2", "source_id": "A" * 32, "label": "Решение №1/01.01.2015 по дело №1/2014",
                     "url": "https://www.vks.bg/x", "chamber": "I т.о.", "relevant": True, "stance": "противоречи",
                     "vks_rule": "Правило.", "quote": {}}],
}


def test_question_ranking_and_defaults():
    assert casefile.rank_questions(RUN)[0] == "Q2"
    assert casefile.default_questions(RUN) == ["Q2"]


def test_form_parsing():
    data, err = casefile.parse_form({"served": "2026-10-01", "amount": "12 500,50", "currency": "EUR", "kind": "търговски",
                                     "property": "1", "client": "  Х ЕООД ", "questions": ["Q1", "Q9"]}, ["Q1", "Q2"])
    assert not err and data["amount"] == 12500.5 and data["currency"] == "EUR" and data["property"]
    assert data["client"] == "Х ЕООД" and data["questions"] == ["Q1"]
    assert casefile.parse_form({"served": "31.02.2026"}, [])[1]
    assert casefile.parse_form({"amount": "много"}, [])[1]


def test_hints_from_the_decision():
    text = "РЕШЕНИЕ № 125 гр. Пловдив ... сумата от 184 000 лева и 47 839,99 лв. лихва; 1000 € разноски"
    assert casefile.decision_number(text) == "125"
    assert casefile.suggest_amount(text) == (184000.0, "BGN")
    assert casefile.case_kind(RUN["appellate"]["label"]) == "търговски"


def test_draft_uses_the_lawyers_data():
    case = {"client": "Х ЕООД", "client_id": "123", "client_address": "гр. Пловдив", "lawyer": "А. Б.",
            "lawyer_address": "гр. Пловдив, ул. 1", "questions": ["Q2"]}
    text = to_text(build_draft(RUN, case, "125"))
    assert "Решение № 125/14.03.2022" in text and "От Х ЕООД, ЕГН/ЕИК 123, гр. Пловдив – чрез адв. А. Б." in text
    assert "Въпрос две?" in text and "Въпрос едно?" not in text and "(адв. А. Б.)" in text
    assert attached_labels(RUN, case) == [("Решение №1/01.01.2015 по дело №1/2014", "A" * 32, "https://www.vks.bg/x")]
    assert attached_labels(RUN, {"questions": ["Q1"]}) == []


def test_attachments_zip_uses_fetch_and_lists_missing():
    items = [("Решение №1/01.01.2015 по дело №1/2014", "A" * 32, "https://www.vks.bg/x"),
             ("Решение №2", "B" * 32, "https://www.vks.bg/y")]
    body, written, missing = build_zip(items, None, lambda sid: "Текст на решението.\nВтори абзац." if sid == "A" * 32 else None)
    z = zipfile.ZipFile(io.BytesIO(body))
    names = z.namelist()
    assert written == 1 and len(missing) == 1 and "ЛИПСВАЩИ.txt" in names
    assert "Решение №2" in z.read("ЛИПСВАЩИ.txt").decode()
    assert any(n.endswith(".docx") for n in names)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused@127.0.0.1/unused")
    monkeypatch.setenv("PRIVATE_STORAGE_PATH", str(tmp_path))
    for v in ("APP_PASSWORD", "RAILWAY_ENVIRONMENT", "APP_REQUIRE_LOGIN"):
        monkeypatch.delenv(v, raising=False)
    d = tmp_path / "runs" / "20260102030405"
    d.mkdir(parents=True)
    run = {**RUN, "searches": [], "skipped": [], "usage": {"calls": 1, "input_tokens": 1, "output_tokens": 1},
           "models": {"analysis": "m"}, "prompt_version": "x", "created_at": "2026-01-02T03:04:05+00:00",
           "cutoff": "2022-01-01"}
    (d / "run.json").write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
    (d / "appellate.txt").write_text("РЕШЕНИЕ № 125 гр. Пловдив, сумата от 184 000 лева", encoding="utf-8")
    from legal_ai.web.app import create_app
    return TestClient(create_app(), base_url="http://127.0.0.1"), d


def test_case_form_saves_and_feeds_the_draft(client):
    c, d = client
    h = {"origin": "http://127.0.0.1"}
    r = c.post("/runs/20260102030405/case", data={"served": "2026-10-03", "amount": "184000", "kind": "търговски",
                                                  "client": "Х ЕООД", "lawyer": "А. Б.", "questions": ["Q2"]},
               headers=h, follow_redirects=False)
    assert r.status_code == 303 and "saved=1" in r.headers["location"]
    saved = json.loads((d / "case.json").read_text(encoding="utf-8"))
    assert saved["questions"] == ["Q2"] and saved["client"] == "Х ЕООД"
    page = c.get("/runs/20260102030405").text
    assert "Последен ден за жалбата: 03.11.2026" in page and "над 20 000 лв." in page
    draft = c.get("/runs/20260102030405/draft").text
    assert "Х ЕООД" in draft and "Решение № 125/14.03.2022" in draft
    r = c.post("/runs/20260102030405/case", data={"served": "2026-10-03"}, headers=h, follow_redirects=False)
    assert "error=" in r.headers["location"]          # no question kept
    assert c.post("/runs/20260102030405/case", data={}, headers={"origin": "http://evil.example"}).status_code == 403
