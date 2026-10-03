"""Web routes for the cassation analysis, with a SYNTHETIC saved run (no network, no AI)."""

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused@127.0.0.1/unused")
    monkeypatch.setenv("PRIVATE_STORAGE_PATH", str(tmp_path))
    run_dir = tmp_path / "runs" / "20260102030405"
    run_dir.mkdir(parents=True)
    run = {
        "appellate": {"label": "Синтетичен съд, дело 1/2020", "url": "file:///x", "fmt": "txt"},
        "analysis": {"case_summary": "Синтетичен казус.",
                     "lower_instance": {"act": "", "date": "", "case": "", "court": ""},
                     "holdings": [{"id": "H1", "summary": "Извод.", "quote": "цитат"}],
                     "questions": [{"id": "В1", "text": "Синтетичен въпрос?", "kind": "материалноправен",
                                    "holding_ids": ["H1"], "ground": "т.1", "why_decisive": "Защото."}],
                     "search": []},
        "holding_quotes": {"H1": {"text": "цитат", "status": "text_verified", "start": 0, "end": 5}},
        "assessments": [{"question_id": "В1", "source_id": "A" * 32, "label": "Решение №1/01.01.2015 по дело №1/2014",
                         "url": "https://www.vks.bg/x", "chamber": "II т.о.", "proceeding_article": "290",
                         "relevant": True, "stance": "противоречи", "vks_rule": "Правило.",
                         "quote": {"text": "лош цитат", "status": "not_found", "start": None, "end": None},
                         "explanation": "Обяснение.", "matched_word_sets": []}],
        "searches": [], "skipped": [], "usage": {"calls": 2, "input_tokens": 1, "output_tokens": 1},
        "models": {"analysis": "m", "light": "m"}, "prompt_version": "cass-1",
        "created_at": "2026-01-02T03:04:05+00:00", "cutoff": "2022-01-01",
    }
    (run_dir / "run.json").write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
    from legal_ai.web.app import create_app
    return TestClient(create_app(), base_url="http://127.0.0.1")


def test_form_lists_saved_runs(client):
    r = client.get("/analyze")
    assert r.status_code == 200
    assert "Синтетичен съд, дело 1/2020" in r.text and "Апелативен съд Пловдив" in r.text


def test_report_shows_stance_and_flags_unverified_quotes(client):
    r = client.get("/runs/20260102030405")
    assert r.status_code == 200
    assert "Противоречи на въззивния съд" in r.text
    assert "✅ цитатът е проверен" in r.text and "⚠️ цитатът не е намерен" in r.text


def test_report_rejects_non_numeric_ids(client):
    assert client.get("/runs/..%2F..%2Fetc").status_code == 404


def test_start_requires_same_origin(client):
    r = client.post("/analyze", data={"court": "as-plovdiv", "case": "1", "year": "2020"},
                    headers={"origin": "http://evil.example"}, follow_redirects=False)
    assert r.status_code == 403
    r = client.post("/analyze", data={"court": "nope", "case": "1", "year": "2020"},
                    headers={"origin": "http://127.0.0.1"}, follow_redirects=False)
    assert r.status_code == 400


def test_noai_trace_page_and_listing(client, tmp_path):
    d = tmp_path / "traces" / "20260102030406"
    d.mkdir(parents=True)
    trace = {
        "created_at": "2026-01-02T03:04:06+00:00", "params": {"court": "as-plovdiv"},
        "appellate": {"label": "Синтетичен съд, дело 2/2020", "url": "file:///y", "text_chars": 12},
        "lower_instance": {"act": "Решение № 5/01.02.2019", "date": "01.02.2019", "case": "7/2018", "court": "ОС"},
        "path": [{"level": "първа", "court": "ОС", "case": "7/2018", "acts": [], "result": "",
                  "source_url": "", "note": ""}],
        "citations": [{"kind": "ТР", "text": "ТР № 1/2013 г. на ВКС", "act_number": "1", "act_date": "",
                       "case_number": "", "case_year": None, "tr_year": 2013, "decision_id": None, "label": ""}],
    }
    (d / "trace.json").write_text(json.dumps(trace, ensure_ascii=False), encoding="utf-8")
    (d / "appellate.txt").write_text("Синтетичен текст", encoding="utf-8")
    r = client.get("/traces/20260102030406")
    assert r.status_code == 200
    assert "Решение № 5/01.02.2019" in r.text and "не е в базата" in r.text and "Синтетичен текст" in r.text
    assert "Синтетичен съд, дело 2/2020" in client.get("/analyze").text
    assert client.get("/traces/..%2Fx").status_code == 404


def test_start_rejects_unknown_mode(client):
    r = client.post("/analyze", data={"court": "as-plovdiv", "case": "1", "year": "2020", "mode": "x"},
                    headers={"origin": "http://127.0.0.1"}, follow_redirects=False)
    assert r.status_code == 400
