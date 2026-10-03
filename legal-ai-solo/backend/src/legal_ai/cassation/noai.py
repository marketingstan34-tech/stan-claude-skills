"""Case work without AI: what can be read from the appellate text with fixed rules.

- the appealed first-instance act ("въззивна жалба ... против решение № ... по гр.д. № ...");
- VKS practice and interpretative decisions cited by the appellate court, matched to the corpus.

Everything here is deterministic. A reference that the rules cannot read is left empty,
never guessed; the page then says so.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

_WS = re.compile(r"\s+")
_DATE = r"(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{2,4})"
_CASE = r"(?:гр|т|ч\.?\s*гр|ч\.?\s*т|в\.?\s*гр|в\.?\s*т)\.?\s*д\.?\s*(?:№|N)\s*(\d{1,6})\s*/\s*(\d{2,4})"

# "въззивна жалба ... против/срещу решение № 1659/10.05.2018 г., постановено по гр.д. № 13727/2014 г.
# по описа на ПдРС, ІV гр.с., ..." (forms seen in Plovdiv appellate decisions, 02.10.2026)
_APPEALED = re.compile(
    r"(?:против|срещу)\s+(?:първоинстанционно\s+)?решение\s*(?:№|N)\s*(\d{1,7})"
    r"(?:\s*/\s*|\s+от\s+)" + _DATE + r"\s*(?:г\.?|год\.?)?"
    r"[^.;]{0,80}?\b" + _CASE + r"\s*(?:г\.?|год\.?)?\s*,?\s*(?:по\s+описа\s+на|на)?\s*([^,;()]{2,60})?",
    re.IGNORECASE,
)

_ACT = re.compile(r"(решение|определение)\s*(?:№|N)\s*(\d{1,7})(?:\s*/\s*|\s+от\s+)" + _DATE,
                  re.IGNORECASE)
_CASE_RE = re.compile(_CASE, re.IGNORECASE)
_TR = re.compile(r"(?:\bТР|тълкувателно\s+решение)\s*(?:№|N)\s*(\d{1,3})(?:\s*/\s*(\d{4}))?",
                 re.IGNORECASE)
_YEAR = re.compile(r"\b(\d{4})\s*г")
_VKS = re.compile(r"\bВКС\b|Върховния(?:т)?\s+касационен\s+съд")
_UNIT_START = re.compile(r"решение|определение|\bТР\b|тълкувателно|\bпо\s+(?:гр|т)\.?\s*д", re.IGNORECASE)


def _year(y: str) -> int:
    n = int(y)
    if n >= 100:
        return n
    return 1900 + n if n >= 90 else 2000 + n


def _date(d: str, m: str, y: str) -> str:
    return f"{int(d):02d}.{int(m):02d}.{_year(y)}"


def _flat(text: str) -> str:
    return _WS.sub(" ", text)


def extract_appealed(text: str) -> dict | None:
    """The first-instance act named in the appeal paragraph, or None if it cannot be read.

    Returns the same keys as the AI analysis (act, date, case, court) so tracing works
    the same way with or without AI.
    """
    flat = _flat(text)
    start = flat.lower().find("въззивна жалба")
    if start < 0:
        start = 0
    m = _APPEALED.search(flat, start, start + 1500) or _APPEALED.search(flat)
    if m is None:
        return None
    act_no, d, mo, y, case_no, case_y, court = m.groups()
    date = _date(d, mo, y)
    return {"act": f"Решение № {act_no}/{date}", "date": date,
            "case": f"{int(case_no)}/{_year(case_y)}", "court": (court or "").strip(" .")}


@dataclass
class Citation:
    kind: str                     # "решение" | "определение" | "ТР" | "дело"
    text: str                     # the citation as written (whitespace normalised)
    act_number: str = ""
    act_date: str = ""            # dd.mm.yyyy
    case_number: str = ""
    case_year: int | None = None
    tr_year: int | None = None
    decision_id: str | None = None
    label: str = ""


def extract_vks_citations(text: str) -> list[Citation]:
    """VKS acts cited in the text: each mention of "ВКС" with the act or case reference
    written next to it (in the same clause, up to ~200 characters before)."""
    flat = _flat(text)
    out: list[Citation] = []
    seen: set[tuple] = set()
    for v in _VKS.finditer(flat):
        before = flat[max(0, v.start() - 200):v.start()]
        starts = list(_UNIT_START.finditer(before))
        if not starts:
            continue
        # the clause starts at the last act keyword before "ВКС", or earlier if that
        # keyword is only the case reference of the same act
        first = starts[-1]
        if first.group(0).lower().startswith("по") and len(starts) > 1:
            first = starts[-2]
        unit_start = v.start() - len(before) + first.start()
        tail = flat[v.end():v.end() + 90]
        cut = re.search(r"[);]|\.\s+[А-ЯA-Z]", tail)
        unit = flat[unit_start:v.end() + (cut.start() if cut else len(tail))].strip()

        tr = _TR.search(unit)
        act = _ACT.search(unit)
        case = _CASE_RE.search(unit)
        if tr:
            year = tr.group(2) or (case.group(2) if case else None) or \
                (_YEAR.search(unit).group(1) if _YEAR.search(unit) else None)
            c = Citation("ТР", unit, act_number=tr.group(1), tr_year=_year(year) if year else None)
        elif act:
            c = Citation(act.group(1).lower(), unit, act_number=act.group(2),
                         act_date=_date(act.group(3), act.group(4), act.group(5)))
        elif case:
            c = Citation("дело", unit)
        else:
            continue
        if case and c.kind != "ТР":
            c.case_number, c.case_year = case.group(1), _year(case.group(2))
        key = (c.kind, c.act_number, c.act_date, c.case_number, c.case_year, c.tr_year)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def match_citations(conn, cites: list[Citation]) -> list[Citation]:
    """Fill decision_id/label for citations found in the corpus (exact number and date or year)."""
    with conn.cursor() as cur:
        for c in cites:
            row = None
            if c.kind == "ТР" and c.tr_year:
                cur.execute("""SELECT id, act_number, case_year, chamber FROM decisions
                               WHERE source = 'vks-tr' AND act_number = %s AND case_year = %s
                               LIMIT 1""", (c.act_number, c.tr_year))
                row = cur.fetchone()
                if row:
                    c.label = f"Тълкувателно решение № {row['act_number']}/{row['case_year']} на {row['chamber']}"
            elif c.act_number and c.act_date:
                d, m, y = c.act_date.split(".")
                cur.execute("""SELECT id, act_number, act_date, case_number, case_year FROM decisions
                               WHERE source = 'vks' AND act_number = %s AND act_date = %s
                               LIMIT 1""", (c.act_number, f"{y}-{m}-{d}"))
                row = cur.fetchone()
            elif c.case_number and c.case_year:
                cur.execute("""SELECT id, act_number, act_date, case_number, case_year FROM decisions
                               WHERE source = 'vks' AND case_number = %s AND case_year = %s
                               ORDER BY act_date DESC LIMIT 1""", (c.case_number, c.case_year))
                row = cur.fetchone()
            if row:
                c.decision_id = str(row["id"])
                if not c.label:
                    when = row["act_date"].strftime("%d.%m.%Y") if row.get("act_date") else "?"
                    c.label = (f"Решение №{row['act_number']}/{when} по дело "
                               f"№{row['case_number']}/{row['case_year']}")
    return cites


def as_dicts(cites: list[Citation]) -> list[dict]:
    return [asdict(c) for c in cites]
