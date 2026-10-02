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
    assert any(lbl.startswith("Тълкувателно решение № 1/2013") for lbl in labels)
    assert any(lbl.startswith("Решение №901/") for lbl in labels)
    assert not any("pregled-akt" in u for u in vks_urls)  # local acts are not downloaded again
    assert any(s.get("local") for s in r.searches)
