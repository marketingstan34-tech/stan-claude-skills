"""Accuracy check on real cases with a known outcome (no cases from the lawyer needed).

Cases are VKS admission rulings (чл. 288 ГПК) in the own database, half admitted and half refused.
The appealed appellate decision is read from the ruling ("решение № … по в.гр.д. № …/… по описа на
… съд"), downloaded from the court's site and analysed like any report; the practice cutoff is the
usual one (appellate decision + 60 days), so the ruling itself cannot be seen. Then, by rules:
- question match: how many words of the question the VKS admitted (or discussed) are in the best
  of our questions;
- practice match: VKS decisions cited in the ruling that our report also found (number and date).
"""

from __future__ import annotations

import re

_APPEALED = re.compile(
    r"решение\s*№\s*(?P<no>\d{1,6})\s*(?:/|от)\s*(?P<date>\d{1,2}\.\d{1,2}\.\d{4})\s*г?\.?,?\s*"
    r"(?:постановено\s+)?по\s+(?P<kind>[а-я.\s]{1,20}?д(?:ело)?\.?)\s*№\s*(?P<case>\d{1,6})\s*(?:/|по описа за)\s*(?P<year>\d{4})"
    r"\s*г?\.?,?\s*(?P<court>(?:по\s+описа\s+на|на)\s+[^,.;]{3,60}?съд(?:\s*[–-]?\s*[А-Я][а-я]+)?)",
    re.IGNORECASE)
_QUESTION = re.compile(r"(?:по\s+(?:поставения|първия|втория|третия|четвъртия|петия|въпроса|въпросите)[^:]{0,80}[:/–-]|"
                       r"въпрос[ът]*\s*[:„\"])\s*(?P<q>[^?]{20,400}\?)", re.IGNORECASE)
_VKS_REF = re.compile(r"(?:решение|определение)\s*№\s*(\d{1,6})\s*(?:/|от)\s*(\d{1,2}\.\d{1,2}\.\d{4})", re.IGNORECASE)


def appealed_decision(text: str) -> dict | None:
    """The appellate decision a VKS ruling is about: court key, case number and year."""
    from legal_ai.tracing import find_named_court
    for m in _APPEALED.finditer(text or ""):
        court = find_named_court(m.group("court"))
        if court is None or court.level == "ВКС":
            continue
        return {"court": court.key, "court_name": court.name, "case": int(m.group("case")),
                "year": int(m.group("year")), "number": m.group("no"), "date": m.group("date")}
    return None


def ruling_questions(text: str) -> list[str]:
    return [" ".join(m.group("q").split()) for m in _QUESTION.finditer(text or "")][:6]


def cited_refs(text: str) -> set[str]:
    out = set()
    for no, d in _VKS_REF.findall(text or ""):
        dd, mm, yy = d.split(".")
        out.add(f"{int(no)}/{int(dd):02d}.{int(mm):02d}.{yy}")
    return out


def _stems(text: str) -> set[str]:
    return {w[:6].lower() for w in re.findall(r"[А-Яа-я]{5,}", text or "")}


_COMMON = _stems("следва въпроса въпросът правен процесуален материален съдът решението въззивния "
                 "касационно обжалване допускане когато какви какво дали")


def question_match(ours: list[str], theirs: list[str]) -> float:
    """Best share of the VKS question's words found in one of our questions (0..1)."""
    best = 0.0
    for t in theirs:
        want = _stems(t) - _COMMON
        if len(want) < 3:
            continue
        for q in ours:
            best = max(best, len(want & _stems(q)) / len(want))
    return round(best, 2)


def score(run: dict, ruling_text: str) -> dict:
    ours = [q["text"] for q in run.get("analysis", {}).get("questions", [])]
    theirs = ruling_questions(ruling_text)
    cited = cited_refs(ruling_text)
    found = set()
    for a in run.get("assessments", []):
        if a.get("relevant"):
            found |= cited_refs(a.get("label", "").replace("№", "№ "))
    return {"vks_questions": theirs, "our_questions": ours, "question_match": question_match(ours, theirs),
            "cited": sorted(cited), "practice_found": sorted(cited & found),
            "contra": sum(1 for a in run.get("assessments", []) if a.get("relevant") and a.get("stance") == "противоречи")}


def pick_cases(conn, n: int = 10) -> list[dict]:
    """Recent admission rulings with a readable appealed decision on a listed court, half admitted."""
    picked: dict[str, list[dict]] = {"допуска": [], "не допуска": []}
    with conn.cursor() as cur:
        cur.execute("""SELECT d.id::text AS id, d.admission_result, d.act_number, d.act_date, d.canonical_url,
                              v.canonical_text
                       FROM decisions d JOIN decision_versions v ON v.id = d.current_version_id
                       WHERE d.admission_result IN ('допуска', 'не допуска')
                       ORDER BY d.act_date DESC NULLS LAST LIMIT 600""")
        for r in cur:
            bucket = picked[r["admission_result"]]
            if len(bucket) >= (n + 1) // 2:
                continue
            ref = appealed_decision(r["canonical_text"])
            if ref is None:
                continue
            bucket.append({"ruling_id": r["id"], "outcome": r["admission_result"],
                           "ruling": f"Определение №{r['act_number'] or '?'}/"
                                     f"{r['act_date'].strftime('%d.%m.%Y') if r['act_date'] else '?'}",
                           "ruling_url": r["canonical_url"], "ruling_text": r["canonical_text"], **ref})
    return (picked["допуска"] + picked["не допуска"])[:n]


def summary(cases: list[dict]) -> dict:
    done = [c for c in cases if c.get("score")]
    if not done:
        return {"done": 0}
    adm = [c for c in done if c["outcome"] == "допуска"]
    hit = [c for c in adm if c["score"]["question_match"] >= 0.5]
    with_cited = [c for c in done if c["score"]["cited"]]
    practice = [c for c in with_cited if c["score"]["practice_found"]]
    return {"done": len(done), "admitted": len(adm), "question_hits": len(hit),
            "with_cited": len(with_cited), "practice_hits": len(practice),
            "failed": sum(1 for c in cases if c.get("error"))}
