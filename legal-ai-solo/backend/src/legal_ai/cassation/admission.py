"""Chance of admission: how the VKS ruled (чл. 288 ГПК) on questions similar to the lawyer's.

Rule-based and free: the question's words are searched in the VKS admission rulings in the own
database (lexical search, same as everywhere); the rulings that cover most of the words are
counted by outcome, and the refusals are scanned for the usual reasons. Similarity is by words,
not by meaning, so the result is a hint for the lawyer, never a forecast.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from legal_ai.retrieval.lexical import search

# usual reasons for refusing admission (ТР 1/2009 на ОСГТК and the practice after it)
REASONS = [
    ("въпросът не е обуславящ за изхода на делото",
     r"не\s+е\s+обуславящ|не\s+(?:е\s+)?обуславя\s+изхода|няма\s+обуславящо\s+значение|без\s+значение\s+за\s+изхода"),
    ("въпросът е общ / абстрактен или неясно формулиран",
     r"общ\s+и\s+абстрактен|абстрактн\w+\s+характер|неясно\s+формулиран|некоректно\s+формулиран|бланкетн"),
    ("въпросът е фактически, не правен", r"фактическ\w+\s+(?:въпрос|характер)|касае\s+фактическ|правилността\s+на\s+фактическите"),
    ("не е доказано противоречие с практиката на ВКС",
     r"не\s+(?:е\s+)?(?:налице|установява|доказ\w+)\s+противореч|не\s+е\s+в\s+противоречие|съответства\s+на\s+(?:задължителната\s+)?(?:съдебна\s+)?практика"),
    ("не е обосновано значение за точното прилагане на закона (т. 3)",
     r"точното\s+прилагане\s+на\s+закона[^.]{0,120}(?:не\s+е|не\s+са|липсва|не\s+се)|не\s+е\s+обосновано\s+значение"),
    ("доводите са за неправилност, не за допускане (чл. 281, т. 3)",
     r"касационни\s+основания\s+по\s+чл\.\s*281|неправилност\s+на\s+въззивното\s+решение[^.]{0,60}не\s+(?:е|са)\s+основани"),
    ("не е налице вероятна нищожност, недопустимост или очевидна неправилност",
     r"очевидна\s+неправилност[^.]{0,80}(?:не\s+е|не\s+се|липсва)|не\s+(?:е\s+)?налице\s+(?:вероятна\s+)?(?:нищожност|недопустимост|очевидна)"),
]
_REASONS = [(label, re.compile(pattern, re.IGNORECASE)) for label, pattern in REASONS]


@dataclass
class Example:
    label: str
    outcome: str
    url: str
    snippet: str


@dataclass
class QuestionChance:
    question_id: str
    question: str
    found: int = 0
    admitted: int = 0
    refused: int = 0
    partly: int = 0
    reasons: list[tuple[str, int]] = field(default_factory=list)
    examples: list[Example] = field(default_factory=list)

    @property
    def share(self) -> int | None:
        decided = self.admitted + self.refused + self.partly
        return round(100 * (self.admitted + self.partly) / decided) if decided else None


def rulings_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM decisions WHERE admission_result IS NOT NULL "
                    "AND current_version_id IS NOT NULL")
        return cur.fetchone()["n"]


def chance(conn, question_id: str, question: str, limit: int = 30, keep: int = 15) -> QuestionChance:
    out = QuestionChance(question_id, question)
    result = search(conn, question, articles=["288"], limit=limit, passages_per_decision=2)
    if not result.decisions:
        return out
    need = max(2, (len(result.terms) + 1) // 2)       # at least half of the question's words
    hits = [d for d in result.decisions if d.terms_matched >= need][:keep]
    if not hits:
        return out
    ids = [h.decision_id for h in hits]
    with conn.cursor() as cur:
        cur.execute("""SELECT d.id::text AS id, d.admission_result, v.canonical_text FROM decisions d
                       JOIN decision_versions v ON v.id = d.current_version_id
                       WHERE d.id::text = ANY(%s)""", (ids,))
        rows = {r["id"]: r for r in cur.fetchall()}
    counts: dict[str, int] = {}
    for h in hits:
        row = rows.get(h.decision_id)
        if not row or not row["admission_result"]:
            continue
        outcome = row["admission_result"]
        out.found += 1
        if outcome == "допуска":
            out.admitted += 1
        elif outcome == "не допуска":
            out.refused += 1
            for label, rx in _REASONS:
                if rx.search(row["canonical_text"]):
                    counts[label] = counts.get(label, 0) + 1
        else:
            out.partly += 1
        if len(out.examples) < 4:
            date = h.act_date.strftime("%d.%m.%Y") if hasattr(h.act_date, "strftime") else ""
            label = f"Определение №{h.act_number or '?'}/{date} по дело №{h.case_number or '?'}/{h.case_year or '?'}"
            snippet = h.passages[0].text[:400] if getattr(h, "passages", None) else ""
            out.examples.append(Example(label, outcome, h.canonical_url, snippet))
    out.reasons = sorted(counts.items(), key=lambda x: -x[1])[:4]
    return out


def chances(conn, run: dict, chosen: list[str] | None = None) -> list[QuestionChance]:
    qs = run.get("analysis", {}).get("questions", [])
    return [chance(conn, q["id"], q["text"]) for q in qs if not chosen or q["id"] in chosen]
