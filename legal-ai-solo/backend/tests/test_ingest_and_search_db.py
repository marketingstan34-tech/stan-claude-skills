"""Integration test against a real PostgreSQL. Set TEST_DATABASE_URL to an empty, migrated
database that may be wiped (never the working database)."""

import os
import shutil
from pathlib import Path

import pytest

from legal_ai.citations.verify import TextStatus, verify_quote
from legal_ai.db import connect
from legal_ai.ingestion.vks_ingest import ingest_raw_dir
from legal_ai.retrieval.lexical import search

TEST_DB = os.environ.get("TEST_DATABASE_URL")
RAW = Path(__file__).parent / "fixtures" / "synthetic_raw"

pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL не е зададен")


@pytest.fixture()
def conn():
    with connect(TEST_DB) as c:
        with c.cursor() as cur:
            cur.execute("""TRUNCATE corpus_members, corpus_snapshots, passages, source_list_runs,
                           decision_versions, decisions, source_artifacts CASCADE""")
        c.commit()
        yield c


@pytest.fixture()
def raw(tmp_path):
    dest = tmp_path / "raw"
    shutil.copytree(RAW, dest)
    return dest


def test_ingest_search_and_verify(conn, raw):
    stats = ingest_raw_dir(conn, raw, raw.parent, "manual", "synthetic")
    assert stats.acts_ingested == 1 and stats.new_versions == 1
    assert any("content_div_missing" in s for s in stats.skipped)

    result = search(conn, "възлагане на неподеляем имот чл. 349")
    assert len(result.decisions) == 1 and result.note is None
    assert result.decisions[0].terms_matched == len(result.terms)
    d = result.decisions[0]
    assert (d.act_number, d.case_number, d.case_type, d.proceeding_article) == ("901", "4001", "гр.", "290")
    assert d.admission_grounds == ["чл. 280, ал. 1, т. 1"]

    with conn.cursor() as cur:
        cur.execute("SELECT canonical_text, text_hash FROM decision_versions WHERE id = %s",
                    (d.version_id,))
        version = cur.fetchone()
    for p in d.passages:
        assert verify_quote(version["canonical_text"], p.start, p.end, p.text,
                            expected_hash=version["text_hash"]) is TextStatus.TEXT_VERIFIED


def test_reingest_is_idempotent(conn, raw):
    ingest_raw_dir(conn, raw, raw.parent, "manual", "synthetic")
    stats = ingest_raw_dir(conn, raw, raw.parent, "manual", "synthetic")
    assert stats.new_versions == 0
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM decision_versions")
        assert cur.fetchone()["n"] == 1


def test_only_290_filter_and_partial_coverage(conn, raw):
    ingest_raw_dir(conn, raw, raw.parent, "manual", "synthetic")
    assert len(search(conn, "възлагане", only_290=True).decisions) == 1
    result = search(conn, "възлагане несъществуващадума")
    assert result.decisions[0].terms_matched == 1 and result.note
    assert search(conn, "несъществуващадума").decisions == []


def test_analysis_uses_local_corpus_and_interpretative_decisions(conn, raw):
    """SYNTHETIC: live VKS search finds nothing; the local corpus supplies a decision and a TR."""
    import json
    from datetime import date

    import httpx

    from legal_ai.ai import AIConfig, OpenAIProvider
    from legal_ai.cassation.pipeline import SourceDoc, run_analysis
    from legal_ai.http import PoliteClient

    ingest_raw_dir(conn, raw, raw.parent, "manual", "synthetic")
    with conn.cursor() as cur:  # one synthetic interpretative decision
        cur.execute("""INSERT INTO source_artifacts (source, url, acquisition, sha256, storage_key)
                       VALUES ('vks-tr', 'https://www.vks.bg/tr.pdf', 'manual', repeat('1', 64), 'x')
                       RETURNING id""")
        art = cur.fetchone()["id"]
        cur.execute("""INSERT INTO decisions (source, source_record_id, court, chamber, act_type, act_number,
                       act_date, case_type, case_number, case_year, proceeding_article, canonical_url)
                       VALUES ('vks-tr', 'osgtk-2013-1', 'ВКС', 'ОСГТК', 'Тълкувателно решение', '1',
                       '2013-12-09', 'тълк.', '1', 2013, 'ТР', 'https://www.vks.bg/tr.pdf') RETURNING id""")
        dec = cur.fetchone()["id"]
        text = "СИНТЕТИЧНО ТР.\nВъзлагането на неподеляем имот се допуска при условия."
        cur.execute("""INSERT INTO decision_versions (decision_id, artifact_id, canonical_text, text_hash,
                       parser_version) VALUES (%s, %s, %s, repeat('2', 64), 'tr-1') RETURNING id""",
                    (dec, art, text))
        ver = cur.fetchone()["id"]
        cur.execute("""INSERT INTO passages (decision_version_id, paragraph_no, section, start_offset,
                       end_offset, exact_text, search_text) VALUES
                       (%s, 0, 'reasoning', 0, 14, 'СИНТЕТИЧНО ТР.', 'синтетично тр'),
                       (%s, 1, 'reasoning', 15, %s, %s, 'възлагането на неподеляем имот се допуска при условия')""",
                    (ver, ver, len(text), text[15:]))
        cur.execute("UPDATE decisions SET current_version_id = %s WHERE id = %s", (ver, dec))
    conn.commit()

    answers = [{"case_summary": "С.", "lower_instance": {"act": "", "date": "", "case": "", "court": ""},
                "holdings": [], "questions": [{"id": "В1", "text": "Въпрос?", "kind": "материалноправен",
                                               "holding_ids": [], "ground": "т.1", "why_decisive": "-"}],
                "search": [{"question_id": "В1", "word_sets": [["неподеляем", "имот"]]}]}]
    seen_prompts = []

    def ai_handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=pending.pop())
        body = json.loads(request.content)
        seen_prompts.append(body["input"][1]["content"])
        out = answers[0] if len(seen_prompts) == 1 else {
            "relevant": True, "stance": "противоречи", "vks_rule": "Правило.", "quote": "", "explanation": "-"}
        pending.append({"id": "r", "status": "completed", "usage": {},
                        "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(out)}]}]})
        return httpx.Response(200, json={"id": "r", "status": "queued"})
    pending = []

    vks_urls = []

    def vks_handler(request):
        vks_urls.append(str(request.url))
        return httpx.Response(200, text="<html><body>няма резултати</body></html>")

    ai = OpenAIProvider(AIConfig("m", "m", max_calls=10), transport=httpx.MockTransport(ai_handler),
                        sleep=lambda s: None)
    vks = PoliteClient(["www.vks.bg"], transport=httpx.MockTransport(vks_handler), sleep=lambda s: None)
    r = run_analysis(ai, vks, SourceDoc("С", "file:///s", "текст", "txt", "now"), date(2030, 1, 1), conn=conn)

    labels = sorted(a.label for a in r.assessments)
    # cited with the decision date and the interpretative case: "... № 1/<дата> г. по тълк. д. № 1/2013 г. на ..."
    assert any(lbl.startswith("Тълкувателно решение") and "тълк. д. № 1/2013 г." in lbl for lbl in labels), labels
    assert any(lbl.startswith("Решение №901/") for lbl in labels)
    assert not any("pregled-akt" in u for u in vks_urls)  # local acts are not downloaded again
    assert any(s.get("local") for s in r.searches)


def _fake_ai_and_vks(search_plan, list_html=""):
    """SYNTHETIC OpenAI (thread-safe, answers by prompt) and VKS site (records URLs)."""
    import json
    import threading

    import httpx

    from legal_ai.ai import AIConfig, OpenAIProvider
    from legal_ai.http import PoliteClient

    analysis = {"case_summary": "С.", "lower_instance": {"act": "", "date": "", "case": "", "court": ""},
                "holdings": [], "search": search_plan,
                "questions": [{"id": item["question_id"], "text": f"Въпрос {item['question_id']}?",
                               "kind": "материалноправен", "holding_ids": [], "ground": "т.1",
                               "why_decisive": "-"} for item in search_plan]}
    lock = threading.Lock()
    done: dict[str, dict] = {}

    def ai_handler(request):
        if request.method == "GET":
            with lock:
                return httpx.Response(200, json=done[request.url.path.rsplit("/", 1)[1]])
        body = json.loads(request.content)
        out = analysis if "=== ВЪЗЗИВНО РЕШЕНИЕ ===" in body["input"][1]["content"] else {
            "relevant": True, "stance": "противоречи", "vks_rule": "Правило.", "quote": "", "explanation": "-"}
        with lock:
            rid = f"r{len(done)}"
            done[rid] = {"id": rid, "status": "completed", "usage": {}, "output": [
                {"type": "message", "content": [{"type": "output_text", "text": json.dumps(out)}]}]}
        return httpx.Response(200, json={"id": rid, "status": "queued"})

    vks_urls: list[str] = []

    def vks_handler(request):
        vks_urls.append(str(request.url))
        if "spisak-aktove.jsp" in str(request.url):
            return httpx.Response(200, text=list_html)
        return httpx.Response(404, text="актът не трябва да се сваля")

    ai = OpenAIProvider(AIConfig("m", "m", max_calls=20), transport=httpx.MockTransport(ai_handler),
                        sleep=lambda s: None)
    vks = PoliteClient(["www.vks.bg"], transport=httpx.MockTransport(vks_handler), sleep=lambda s: None)
    return ai, vks, vks_urls


def test_local_first_skips_the_live_search_when_the_corpus_has_enough(conn, raw, monkeypatch):
    """SYNTHETIC: В1 has a local art. 290 decision (threshold lowered to 1), В2 has none."""
    from datetime import date
    from urllib.parse import parse_qs, urlparse

    from legal_ai.cassation import pipeline
    from legal_ai.cassation.pipeline import SourceDoc, render_markdown, run_analysis

    ingest_raw_dir(conn, raw, raw.parent, "manual", "synthetic")
    monkeypatch.setattr(pipeline, "LOCAL_FIRST_MIN", 1)
    plan = [{"question_id": "В1", "word_sets": [["неподеляем", "имот"]]},
            {"question_id": "В2", "word_sets": [["несъществуваща", "дума"]]}]
    doc = SourceDoc("С", "file:///s", "текст", "txt", "now")

    def words(urls):
        return [parse_qs(urlparse(u).query)["AktDumiVSadarjanie"][0] for u in urls if "spisak-aktove" in u]

    ai, vks, urls = _fake_ai_and_vks(plan)
    r = run_analysis(ai, vks, doc, date(2030, 1, 1), conn=conn, local_first=True)
    assert words(urls) == ["несъществуваща дума"]          # В1 is not searched on the site
    rec = [s for s in r.searches if s.get("live_skipped")]
    assert len(rec) == 1 and rec[0]["question_id"] == "В1" and rec[0]["local_decisions"] == 1
    assert "собствената база даде 1 решения по чл. 290 (праг 1)" in rec[0]["skipped"]
    assert [a.label[:13] for a in r.assessments] == ["Решение №901/"]
    md = render_markdown(r)
    assert "не е търсено в сайта на ВКС" in md and "сайтът на ВКС не е търсен" in md

    ai, vks, urls = _fake_ai_and_vks(plan)                  # the option off: both are searched
    r = run_analysis(ai, vks, doc, date(2030, 1, 1), conn=conn, local_first=False)
    assert words(urls) == ["неподеляем имот", "несъществуваща дума"]
    assert not any(s.get("live_skipped") for s in r.searches)


def test_act_found_live_but_already_in_the_database_is_not_downloaded(conn, raw):
    """SYNTHETIC: the live list returns the ingested act for words the local search misses."""
    from datetime import date

    from legal_ai.cassation.pipeline import SourceDoc, run_analysis

    ingest_raw_dir(conn, raw, raw.parent, "manual", "synthetic")
    with conn.cursor() as cur:
        cur.execute("SELECT source_record_id FROM decisions WHERE source = 'vks' AND act_number = '901'")
        key = cur.fetchone()["source_record_id"]
    html = (f"<a href='pregled-akt.jsp?type=ot-spisak&id={key}'>Решение №901/15.01.2025 "
            "по дело №4001/2024</a>")
    plan = [{"question_id": "В1", "word_sets": [["несъществуваща", "дума"]]}]
    ai, vks, urls = _fake_ai_and_vks(plan, html)
    r = run_analysis(ai, vks, SourceDoc("С", "file:///s", "текст", "txt", "now"), date(2030, 1, 1),
                     conn=conn, local_first=True)
    assert not any("pregled-akt" in u for u in urls)
    assert [a.source_id for a in r.assessments] == [key]
    assert not r.skipped
