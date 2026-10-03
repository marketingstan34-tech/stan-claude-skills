"""Judges of a VKS act: the panel from the heading ("в състав: ПРЕДСЕДАТЕЛ: … ЧЛЕНОВЕ: …") and the
reporting judge ("докладваното от съдия Петрова" → the panel member with that surname).

Names are public in the acts. Only what the text says is used; a reporter whose surname matches no
panel member is kept as the surname alone.
"""

from __future__ import annotations

import re

_NAME_WORD = r"[А-ЯЁЇІ][А-ЯЁЇІ\-]+"
_NAME = re.compile(rf"^\s*(?:ПРЕДСЕДАТЕЛ|ЧЛЕНОВЕ|ЧЛЕН)?\s*:?\s*({_NAME_WORD}(?:\s+{_NAME_WORD}){{1,2}})\s*$")
_ROLE = re.compile(r"^\s*(ПРЕДСЕДАТЕЛ|ЧЛЕНОВЕ|ЧЛЕН)\s*:\s*(.*)$")
_REPORTER = re.compile(
    r"(?:докладван\w*\s+от\s+(?:съдия(?:та)?|председателя|члена|зам\.\s*председателя)"
    r"|съдия[\s-]*докладчик(?:ът)?)\s+([А-Я][а-я]+(?:[\s-]+[А-Я][а-я]+){0,2})")


def _title(name: str) -> str:
    return " ".join("-".join(p.capitalize() for p in w.split("-")) for w in name.split())


def parse_judges(text: str) -> tuple[list[str], str | None]:
    """(panel names in the heading's order, reporting judge or None)."""
    lines = (text or "")[:2500].split("\n")
    panel: list[str] = []
    in_panel = False
    for line in lines:
        role = _ROLE.match(line)
        if role:
            in_panel = True
            rest = role.group(2).strip()
            if not rest:
                continue
            line = rest
        elif not in_panel:
            continue
        m = _NAME.match(line)
        if m:
            name = _title(m.group(1))
            if name not in panel:
                panel.append(name)
        elif panel:
            break       # the first line after the names (e.g. "при участието на секретаря…")
    reporter = None
    m = _REPORTER.search(text or "")
    if m:
        last = m.group(1).split()[-1].lower()
        full = [p for p in panel if p.split()[-1].lower() == last]
        reporter = full[0] if len(full) == 1 else _title(m.group(1))
    return panel[:5], reporter


# ---------- research by judge (reads the own database) ----------

def list_judges(conn, query: str = "", limit: int = 200) -> list[dict]:
    """Reporting judges with how many acts they reported and how they ruled on admission (чл. 288)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT reporter AS name, count(*) AS n,
                   count(*) FILTER (WHERE proceeding_article = '290') AS n290,
                   count(*) FILTER (WHERE admission_result IS NOT NULL) AS n288,
                   count(*) FILTER (WHERE admission_result = 'допуска') AS admitted,
                   count(*) FILTER (WHERE admission_result = 'не допуска') AS refused,
                   count(*) FILTER (WHERE admission_result = 'частично') AS partly,
                   mode() WITHIN GROUP (ORDER BY chamber) AS chamber, max(act_date) AS last
            FROM decisions
            WHERE reporter IS NOT NULL AND current_version_id IS NOT NULL
              AND (%(q)s = '' OR reporter ILIKE %(like)s)
            GROUP BY reporter
            ORDER BY count(*) DESC LIMIT %(limit)s""",
                    {"q": query.strip(), "like": f"%{query.strip()}%", "limit": limit})
        rows = cur.fetchall()
    for r in rows:
        decided = r["admitted"] + r["refused"] + r["partly"]
        r["share"] = round(100 * (r["admitted"] + r["partly"]) / decided) if decided else None
    return rows


def judge_detail(conn, name: str, words: str = "", limit: int = 40) -> dict:
    """One judge: totals as reporter and as panel member, admission by year, and the acts
    (optionally only those whose text contains the words)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT count(*) AS n, count(*) FILTER (WHERE admission_result = 'допуска') AS admitted,
                   count(*) FILTER (WHERE admission_result = 'не допуска') AS refused,
                   count(*) FILTER (WHERE admission_result = 'частично') AS partly,
                   count(*) FILTER (WHERE proceeding_article = '290') AS n290,
                   mode() WITHIN GROUP (ORDER BY chamber) AS chamber
            FROM decisions WHERE reporter = %s AND current_version_id IS NOT NULL""", (name,))
        totals = cur.fetchone()
        cur.execute("""SELECT count(*) AS n FROM decisions
                       WHERE panel ? %s AND current_version_id IS NOT NULL""", (name,))
        totals["panel"] = cur.fetchone()["n"]
        cur.execute("""
            SELECT extract(year FROM act_date)::int AS year,
                   count(*) FILTER (WHERE admission_result = 'допуска' OR admission_result = 'частично') AS admitted,
                   count(*) FILTER (WHERE admission_result = 'не допуска') AS refused
            FROM decisions WHERE reporter = %s AND admission_result IS NOT NULL AND act_date IS NOT NULL
            GROUP BY 1 ORDER BY 1 DESC""", (name,))
        years = cur.fetchall()
        terms = [w for w in re.findall(r"[А-Яа-яA-Za-z0-9]{3,}", words.lower())][:6]
        cur.execute("""
            SELECT d.act_type, d.act_number, d.act_date, d.case_type, d.case_number, d.case_year, d.chamber,
                   d.proceeding_article, d.admission_result, d.canonical_url
            FROM decisions d
            WHERE d.reporter = %(name)s AND d.current_version_id IS NOT NULL
              AND (cardinality(%(terms)s::text[]) = 0 OR EXISTS (
                    SELECT 1 FROM passages p WHERE p.decision_version_id = d.current_version_id
                    AND p.tsv @@ to_tsquery('simple', array_to_string(
                        ARRAY(SELECT t || ':*' FROM unnest(%(terms)s::text[]) t), ' & '))))
            ORDER BY d.act_date DESC NULLS LAST LIMIT %(limit)s""",
                    {"name": name, "terms": terms, "limit": limit})
        acts = cur.fetchall()
    decided = totals["admitted"] + totals["refused"] + totals["partly"]
    totals["share"] = round(100 * (totals["admitted"] + totals["partly"]) / decided) if decided else None
    return {"name": name, "totals": totals, "years": years, "acts": acts, "terms": terms}
