"""Candidates from the local corpus (VKS decisions under art. 290 and interpretative decisions).

Deterministic: the same word sets give the same candidates, unlike the live site search
whose results depend on exact word forms. Acts found here are read from the database, so
they are not downloaded again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import psycopg

from legal_ai.retrieval.lexical import search


@dataclass
class LocalParagraph:
    no: int
    text: str


@dataclass
class LocalAct:
    """Duck-types the parts of ParsedAct used by the pipeline (excerpt, quotes, labels)."""
    key: str
    source: str
    label: str
    url: str
    canonical_text: str
    paragraphs: list[LocalParagraph]
    heading: str | None
    chamber: str | None
    proceeding_article: str | None
    act_date: date | None
    admission_paragraph_nos: list[int] = field(default_factory=list)


def _label(row: dict) -> str:
    if row["source"] == "vks-tr":
        return f"Тълкувателно решение № {row['act_number']}/{row['case_year']} на {row['chamber']}"
    d = row["act_date"].strftime("%d.%m.%Y") if row["act_date"] else "?"
    return f"Решение №{row['act_number']}/{d} по дело №{row['case_number']}/{row['case_year']}"


def load_act(conn: psycopg.Connection, decision_id: str) -> LocalAct | None:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT d.id, d.source, d.source_record_id, d.chamber, d.act_number, d.act_date,
                   d.case_number, d.case_year, d.proceeding_article, d.canonical_url,
                   v.id AS version_id, v.canonical_text
            FROM decisions d JOIN decision_versions v ON v.id = d.current_version_id
            WHERE d.id = %s""", (decision_id,))
        row = cur.fetchone()
        if row is None:
            return None
        cur.execute("""SELECT paragraph_no, exact_text, is_admission FROM passages
                       WHERE decision_version_id = %s ORDER BY paragraph_no""", (row["version_id"],))
        paras = cur.fetchall()
    key = row["source_record_id"] if row["source"] == "vks" else f"{row['source']}:{row['source_record_id']}"
    return LocalAct(
        key=key, source=row["source"], label=_label(row), url=row["canonical_url"],
        canonical_text=row["canonical_text"],
        paragraphs=[LocalParagraph(p["paragraph_no"], p["exact_text"]) for p in paras],
        heading=None, chamber=row["chamber"], proceeding_article=row["proceeding_article"],
        act_date=row["act_date"],
        admission_paragraph_nos=[p["paragraph_no"] for p in paras if p["is_admission"]])


def local_candidates(conn: psycopg.Connection, word_sets: list[list[str]], cutoff: date,
                     per_set: int = 8) -> dict[str, dict]:
    """decision_id -> {"hits": [word_set, ...], "source": ..., "proceeding_article": ...}

    A decision counts for a word set only if it covers every word of the set (prefix match
    after stripping endings, see retrieval.text). Only art. 290 decisions and interpretative
    decisions up to the cutoff are kept.
    """
    found: dict[str, dict] = {}
    for ws in word_sets:
        query = " ".join(ws)
        result = search(conn, query, only_290=False, limit=40, passages_per_decision=1,
                        articles=["290", "ТР"], until=cutoff)
        n_terms = len(result.terms)
        kept = 0
        for d in result.decisions:
            if kept >= per_set:
                break
            if n_terms == 0 or d.terms_matched < n_terms:
                continue
            if d.proceeding_article not in ("290", "ТР"):
                continue
            if d.act_date and d.act_date > cutoff:
                continue
            e = found.setdefault(d.decision_id, {"hits": [], "proceeding_article": d.proceeding_article})
            e["hits"].append(ws)
            kept += 1
    return found
