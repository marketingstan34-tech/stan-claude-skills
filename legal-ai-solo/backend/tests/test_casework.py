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


# the appeal draft (one AI call; a fake AI here)

class FakeAI:
    def __init__(self, answer):
        self.answer, self.prompts = answer, []
        self.config = type("C", (), {"analysis_model": "fake-model"})()
        self.usage = type("U", (), {"calls": 1, "input_tokens": 10, "output_tokens": 5})()

    def structured(self, **kw):
        self.prompts.append(kw["user"])
        return self.answer


APPELLATE = "РЕШЕНИЕ № 125 гр. Пловдив. Съдът приема, че банката правилно е отнесла плащането към главницата."
ANSWER = {"grounds": [
    {"kind": "материален закон", "holding_ids": ["H1"], "title": "Неправилно отнасяне на плащането",
     "quote": "банката правилно е отнесла плащането към главницата", "complaint": "Порок.", "argument": "Довод.",
     "vks_labels": ["Решение №1/01.01.2015 по дело №1/2014", "Решение №999/01.01.2020 по дело №9/2019"]},
    {"kind": "процесуални правила", "holding_ids": [], "title": "Необсъдени доказателства",
     "quote": "текст, който го няма в решението", "complaint": "Порок 2.", "argument": "Довод 2.", "vks_labels": []}],
    "petitum_scope": "в частта", "petitum_part": "е отхвърлен искът", "petitum_request": "да уважите иска"}


def test_appeal_checks_quotes_and_vks_labels():
    from legal_ai.cassation.appeal import build_appeal, generate
    ai = FakeAI(ANSWER)
    appeal = generate(ai, RUN, APPELLATE, ["Q2"])
    g1, g2 = appeal["grounds"]
    assert g1["quote_status"] == "text_verified" and g2["quote_status"] == "not_found"
    assert g1["vks_labels"] == ["Решение №1/01.01.2015 по дело №1/2014"]           # only from the report
    assert g1["dropped_labels"] == ["Решение №999/01.01.2020 по дело №9/2019"]
    assert "Въпрос две?" in ai.prompts[0] and "Въпрос едно?" not in ai.prompts[0]
    text = to_text(build_appeal(RUN, appeal, {"client": "Х ЕООД", "opponent": "Банка АД", "lawyer": "А. Б."}, "125", 1))
    assert "КАСАЦИОННА ЖАЛБА" in text and "срещу Банка АД" in text and "Решение № 125/14.03.2022" in text
    assert "в частта, с която е отхвърлен искът" in text and "чл. 281, т. 3 ГПК" in text
    assert "„банката правилно е отнесла плащането към главницата“" in text
    assert "не е намерен дословно" in text and "Решение №999" not in text
    assert "Копия на цитираните решения на ВКС (1 бр.)" in text


def test_appeal_routes_and_status(client, monkeypatch):
    import legal_ai.web.jobs as jobs
    c, d = client
    h = {"origin": "http://127.0.0.1"}
    assert "Напиши жалбата с AI" in c.get("/runs/20260102030405/appeal").text
    started = []
    monkeypatch.setattr(jobs.JobRunner, "_run", lambda self, job: started.append(job.params))
    r = c.post("/runs/20260102030405/appeal", headers=h, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/jobs/") and started[0]["mode"] == "appeal"
    from legal_ai.cassation.appeal import generate
    (d / "appeal.json").write_text(json.dumps(generate(FakeAI(ANSWER), RUN, APPELLATE, ["Q2"]), ensure_ascii=False),
                                   encoding="utf-8")
    page = c.get("/runs/20260102030405/appeal").text
    assert "КАСАЦИОННА ЖАЛБА" in page and "Неправилно отнасяне на плащането" in page
    assert c.get("/runs/20260102030405/appeal.docx").status_code == 200
    # status
    assert c.post("/runs/20260102030405/status", data={"status": "в работа"}, headers=h,
                  follow_redirects=False).status_code == 303
    assert json.loads((d / "case.json").read_text(encoding="utf-8"))["status"] == "в работа"
    reports = c.get("/reports?status=" + "в работа").text
    assert "в работа · 1" in reports
    assert "в.т. 899/2021" in reports or "899/2021" in reports
    assert "899/2021" not in c.get("/reports?status=" + "приключен").text.split("all-reports")[1]


def test_other_documents_go_with_the_decision(client, monkeypatch):
    import legal_ai.web.jobs as jobs
    c, d = client
    started = []

    def fake_run(self, job):
        started.append(job.params)
        job.status = "done"

    monkeypatch.setattr(jobs.JobRunner, "_run", fake_run)
    doc = ("Въззивният съд приема, че искът е неоснователен. " * 10).encode()
    first = ("Първоинстанционният съд уважава иска изцяло. " * 10).encode()
    r = c.post("/analyze", data={"text": "Бележки.", "mode": "ai"},
               files=[("document", ("reshenie.txt", doc, "text/plain")),
                      ("extra", ("parva.txt", first, "text/plain")), ("extra", ("zhalba.txt", first, "text/plain"))],
               headers={"origin": "http://127.0.0.1"}, follow_redirects=False)
    assert r.status_code == 303
    p = started[0]
    assert [e["filename"] for e in p["extras"]] == ["parva.txt", "zhalba.txt"] and p["notes"] == "Бележки."
    runner = jobs.JobRunner(d.parent, d.parent.parent / "traces")
    assert runner._extras(p)[0][1].startswith("Първоинстанционният съд")
    # documents alone, without the decision, are refused
    r = c.post("/analyze", data={"mode": "ai"}, files=[("extra", ("parva.txt", first, "text/plain"))],
               headers={"origin": "http://127.0.0.1"})
    assert r.status_code == 400


def test_context_documents_in_the_prompts():
    from legal_ai.cassation import prompts as P
    from legal_ai.cassation.appeal import appeal_prompt
    block = P.context_block([("parva.txt", "А" * 50_000), ("b.txt", "Б" * 80_000)])
    assert block.count("ДРУГ ДОКУМЕНТ ПО ДЕЛОТО") == 2 and "съкратено" in block
    assert len(block) < P.CONTEXT_MAX_TOTAL + 1000
    prompt = appeal_prompt(RUN, "Решение.", None, [("parva.txt", "Първа инстанция.")])
    assert "ДРУГ ДОКУМЕНТ ПО ДЕЛОТО: parva.txt" in prompt and "Първа инстанция." in prompt


def test_cost_estimate():
    from legal_ai.ai.pricing import cost_usd
    measured = {"gpt-5.5-2026-04-23": [52217, 9652], "gpt-5.4-mini-2026-03-17": [113926, 15665]}
    assert round(cost_usd(measured), 2) == 0.71
    assert cost_usd({"unknown-model": [1, 1]}) is None


def test_form_shows_the_cost(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr("legal_ai.web.app.connect", lambda url: (_ for _ in ()).throw(
        __import__("psycopg").OperationalError("no db")))
    page = c.get("/analyze").text
    assert "С AI приблизително: справка 0.70–1.00 $" in page and "10 ¢" not in page


def test_empty_number_and_year_from_the_browser(client, monkeypatch):
    """Browsers send empty fields as "": that must not fail when a document is given."""
    import legal_ai.web.jobs as jobs
    c, _ = client
    started = []

    def fake_run(self, job):
        started.append(job.params)
        job.status = "done"

    monkeypatch.setattr(jobs.JobRunner, "_run", fake_run)
    doc = ("Въззивният съд приема, че искът е неоснователен. " * 10).encode()
    h = {"origin": "http://127.0.0.1"}
    r = c.post("/analyze", data={"court": "", "case": "", "year": "", "case_type": "", "until": "", "mode": "ai",
                                 "text": "Бележки."}, files={"document": ("r.txt", doc, "text/plain")},
               headers=h, follow_redirects=False)
    assert r.status_code == 303 and started[0]["case"] is None and started[0]["year"] is None
    r = c.post("/analyze", data={"court": "as-plovdiv", "case": "abc", "year": "2021"}, headers=h)
    assert r.status_code == 400 and "число" in r.text
    r = c.post("/analyze", data={"court": "as-plovdiv", "case": "899", "year": "2021", "mode": "noai"},
               headers=h, follow_redirects=False)
    assert r.status_code == 303 and started[-1]["case"] == 899 and started[-1]["year"] == 2021


# fixes after the first real case (03.10.2026)

def test_vks_case_of_another_kind_or_earlier_is_not_linked():
    from legal_ai.tracing import vks_case_mismatch
    acts = [{"date": "01.06.2026"}, {"date": "05.08.2026"}]
    assert "друг вид" in vks_case_mismatch("тър.", acts, "Въззивно гражданско дело", date(2026, 8, 3))
    assert "преди въззивното решение" in vks_case_mismatch("гр.", acts, "Въззивно гражданско дело", date(2026, 8, 3))
    assert vks_case_mismatch("гр.", [{"date": "05.09.2026"}], "Въззивно гражданско дело", date(2026, 8, 3)) == ""
    assert vks_case_mismatch("тър.", [], "", None) == ""


def test_standard_label_from_the_decision_heading():
    from legal_ai.cassation.labels import appellate_label, standard_label
    text = ("Рег.№ 163 / 03.08.2026\nРЕШЕНИЕ\nгр. Пловдив\nВ ИМЕТО НА НАРОДА\nАПЕЛАТИВЕН СЪД – ПЛОВДИВ, "
            "2-РИ ГРАЖДАНСКИ СЪСТАВ ... Въззивно гражданско дело № 20255000500553 по описа за 2025 година")
    assert standard_label(text) == "Апелативен съд Пловдив, Въззивно гражданско дело № 553/2025, Решение от 03.08.2026"
    assert casefile.decision_number(text) == "163"
    assert appellate_label("Апелативен съд X, ...", None) == "Апелативен съд X, ..."   # not a document: kept


def test_draft_does_not_repeat_holdings():
    run = json.loads(json.dumps(RUN))
    run["analysis"]["questions"][1]["holding_ids"] = ["H1"]
    text = to_text(build_draft(run, {"questions": ["Q1", "Q2"]}))
    assert text.count("е приел, че извод") == 1 and "изложени по-горе (на първо място)" in text


def test_appeal_request_is_not_doubled():
    from legal_ai.cassation.appeal import _request
    assert _request("Да отмени въззивното решение и вместо него да постанови решение, с което да уважи иска.") == \
        "вместо него да постановите решение, с което да уважите иска"


def test_edit_draft_and_appeal_and_documents_page(client):
    c, d = client
    h = {"origin": "http://127.0.0.1"}
    assert "contenteditable" in c.get("/runs/20260102030405/draft/edit").text
    assert c.get("/runs/20260102030405/appeal/edit").status_code == 404        # no appeal yet
    blocks = [{"kind": "heading", "text": "ДО ВКС"}, {"kind": "p", "text": "Моят текст."}, {"kind": "bad", "text": "x"},
              {"kind": "p", "text": "   "}]
    r = c.post("/runs/20260102030405/draft/edit", json={"blocks": blocks}, headers=h)
    assert r.status_code == 200 and r.json()["ok"]
    page = c.get("/runs/20260102030405/draft").text
    assert "Моят текст." in page and "редактирана версия" in page
    docx = c.get("/runs/20260102030405/draft.docx").content
    import zipfile as _z
    assert "Моят текст." in _z.ZipFile(io.BytesIO(docx)).read("word/document.xml").decode()
    assert c.post("/runs/20260102030405/draft/edit", json={"blocks": []}, headers=h).status_code == 400
    assert c.post("/runs/20260102030405/draft/edit", json={"blocks": blocks},
                  headers={"origin": "http://evil.example"}).status_code == 403
    docs = c.get("/documents").text
    assert "редактирано" in docs and "/runs/20260102030405/draft/edit" in docs and "Напиши с AI" in docs
    r = c.post("/runs/20260102030405/draft/reset", headers=h, follow_redirects=False)
    assert r.status_code == 303 and "Моят текст." not in c.get("/runs/20260102030405/draft").text
    assert c.get("/runs/20260102030405/other/edit").status_code == 404


def test_report_type_is_not_overwritten_by_the_case_kind(client):
    """The case kind ("в.т.") and the report type (with or without AI) are different keys."""
    c, _ = client
    page = c.get("/reports").text
    assert 'С AI</span><span class="meetings-count-circle">1</span>' in page
    assert "в.т. 899/2021" in page
