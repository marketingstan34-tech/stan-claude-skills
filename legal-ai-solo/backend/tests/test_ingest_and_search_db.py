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
