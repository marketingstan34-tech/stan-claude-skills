"""Lexical baseline over passages, aggregated to distinct decisions.

Every returned passage is the exact stored paragraph with its offsets, so a
displayed quote can always be re-verified against the immutable version text.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from datetime import date

import psycopg

from legal_ai.retrieval.text import Term, build_tsquery, parse_query

MAX_LIMIT = 100


@dataclass
class PassageHit:
    passage_id: str
    paragraph_no: int
    section: str
    start: int
    end: int
    text: str
    is_admission: bool
    rank: float


@dataclass
class DecisionHit:
    decision_id: str
    version_id: str
    act_type: str | None
    act_number: str | None
    act_date: object
    case_type: str | None
    case_number: str | None
    case_year: int | None
    chamber: str | None
    proceeding_article: str | None
    admission_grounds: list
    canonical_url: str
    score: float
    terms_matched: int = 0
    passages: list[PassageHit] = field(default_factory=list)


@dataclass
class SearchResult:
    query: str
    terms: list[Term]
    mode: str  # tsquery mode used for candidate generation
    decisions: list[DecisionHit]
    corpus_size: int
    note: str | None = None


def _build_sql(n_terms: int) -> str:
    """Rank by how many distinct query terms a decision covers, then by ts_rank_cd.

    Only term indexes are interpolated; term values are bound parameters.
    """
    match_cols = ", ".join(
        f"(p.tsv @@ to_tsquery('simple', %(t{i})s))::int AS m{i}" for i in range(n_terms)
    )
    passage_cov = " + ".join(f"m{i}" for i in range(n_terms))
    decision_cov = " + ".join(f"max(m{i})" for i in range(n_terms))
    return f"""
WITH q AS (SELECT to_tsquery('simple', %(tsq)s) AS q),
hits AS (
    SELECT p.id, p.decision_version_id, p.paragraph_no, p.section, p.start_offset, p.end_offset,
           p.exact_text, p.is_admission, ts_rank_cd(p.tsv, q.q) AS rank, {match_cols}
    FROM passages p
    JOIN decisions d ON d.current_version_id = p.decision_version_id
    CROSS JOIN q
    WHERE p.tsv @@ q.q
      AND (%(only_290)s = false OR d.proceeding_article = '290')
      AND (%(articles)s::text[] IS NULL OR d.proceeding_article = ANY(%(articles)s::text[]))
      AND (%(until)s::date IS NULL OR d.act_date IS NULL OR d.act_date <= %(until)s::date)
),
per_decision AS (
    SELECT decision_version_id,
           {decision_cov} AS coverage,
           max(rank) + 0.1 * ln(1 + count(*)) AS score
    FROM hits GROUP BY decision_version_id
    ORDER BY coverage DESC, score DESC, decision_version_id
    LIMIT %(limit)s
)
SELECT d.id AS decision_id, pd.decision_version_id AS version_id, pd.score, pd.coverage,
       d.act_type, d.act_number, d.act_date, d.case_type, d.case_number, d.case_year, d.chamber,
       d.proceeding_article, d.admission_grounds, d.canonical_url,
       h.id AS passage_id, h.paragraph_no, h.section, h.start_offset, h.end_offset,
       h.exact_text, h.is_admission, h.rank, ({passage_cov}) AS passage_coverage
FROM per_decision pd
JOIN decisions d ON d.current_version_id = pd.decision_version_id
JOIN hits h ON h.decision_version_id = pd.decision_version_id
ORDER BY pd.coverage DESC, pd.score DESC, pd.decision_version_id, passage_coverage DESC, h.rank DESC, h.paragraph_no
"""


def _run(conn: psycopg.Connection, terms: list[Term], mode: str, only_290: bool, limit: int,
         passages_per_decision: int, articles: list[str] | None = None,
         until: date | None = None) -> list[DecisionHit]:
    params = {"tsq": build_tsquery(terms, mode), "only_290": only_290, "limit": limit,
              "articles": list(articles) if articles else None, "until": until}
    params.update({f"t{i}": t.to_tsquery() for i, t in enumerate(terms)})
    with conn.cursor() as cur:
        cur.execute(_build_sql(len(terms)), params)
        rows = cur.fetchall()
    by_id: dict[str, DecisionHit] = {}
    for r in rows:
        key = str(r["version_id"])
        hit = by_id.get(key)
        if hit is None:
            hit = DecisionHit(
                decision_id=str(r["decision_id"]), version_id=key, act_type=r["act_type"],
                act_number=r["act_number"], act_date=r["act_date"], case_type=r["case_type"],
                case_number=r["case_number"],
                case_year=r["case_year"], chamber=r["chamber"],
                proceeding_article=r["proceeding_article"],
                admission_grounds=r["admission_grounds"] or [], canonical_url=r["canonical_url"],
                score=float(r["score"]), terms_matched=r["coverage"],
            )
            by_id[key] = hit
        if len(hit.passages) < passages_per_decision:
            hit.passages.append(PassageHit(
                passage_id=str(r["passage_id"]), paragraph_no=r["paragraph_no"],
                section=r["section"], start=r["start_offset"], end=r["end_offset"],
                text=r["exact_text"], is_admission=r["is_admission"], rank=float(r["rank"]),
            ))
    return list(by_id.values())


def corpus_size(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM decisions WHERE current_version_id IS NOT NULL")
        return cur.fetchone()["n"]


def search(conn: psycopg.Connection, query: str, *, only_290: bool = False, limit: int = 30,
           passages_per_decision: int = 3, articles: list[str] | None = None,
           until: date | None = None) -> SearchResult:
    """Candidates match at least one term; decisions covering more distinct terms (across
    all their paragraphs) rank first. `articles` and `until` filter before the limit, so
    excluded decisions never crowd out valid ones."""
    limit = max(1, min(limit, MAX_LIMIT))
    terms = parse_query(query)
    size = corpus_size(conn)
    if not terms:
        return SearchResult(query, terms, "any", [], size, note="Няма думи за търсене след филтриране.")
    decisions = _run(conn, terms, "any", only_290, limit, passages_per_decision, articles, until)
    note = None
    if decisions and decisions[0].terms_matched < len(terms):
        note = "Нито един акт не съдържа всички думи. Показани са актовете с най-много съвпадения."
    return SearchResult(query, terms, "any", decisions, size, note=note)
