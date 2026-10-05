"""Accuracy check on real cases with a known outcome (no cases from the lawyer needed).

Cases are VKS admission rulings (чл. 288 ГПК) in the own database, half admitted and half refused.
The appealed appellate decision is read from the ruling ("решение № … по в.гр.д. № …/… по описа на
… съд"), downloaded from the court's site and analysed like any report; the practice cutoff is the
usual one (appellate decision + 60 days), so the ruling itself cannot be seen. Then, by rules:
- question match: how many words of the question the VKS admitted (or discussed) are in the best
  of our questions; then one AI call per case judges by meaning (judge_questions), since VKS often
  restates a question in other words;
- practice match: VKS decisions cited in the ruling that our report also found (number and date).
"""

from __future__ import annotations

import re
from datetime import date

from legal_ai.citations.refs import VKS_REF as _VKS_REF
from legal_ai.citations.refs import cited_refs

_APPEALED = re.compile(
    r"решение\s*№\s*(?P<no>\d{1,6})\s*(?:/|от)\s*(?P<date>\d{1,2}\.\d{1,2}\.\d{4})\s*г?\.?,?\s*"
    r"(?:постановено\s+)?по\s+(?P<kind>[а-я.\s]{1,20}?д(?:ело)?\.?)\s*№\s*(?P<case>\d{1,6})\s*(?:/|по описа за)\s*(?P<year>\d{4})"
    r"\s*г?\.?,?\s*(?P<court>(?:по\s+описа\s+на|на)\s+[^,.;]{3,60}?съд(?:\s*[–-]?\s*[А-Я][а-я]+)?)",
    re.IGNORECASE)
# How the rulings state the questions (seen in real rulings, 09.2026): a quoted question ending in
# "?", a numbered list after "следните въпроси:", or "по въпроса за …" without a question mark.
_SENT_Q = re.compile(r"(?:^|(?<=[.:;„“\"]))\s*([^.:;„“\"?]{25,900}\?)")
_LIST_HEAD = re.compile(r"(?:следни[яте]*\s+(?:правни\s+)?въпрос[а-я]*|въпрос[а-я]*\s*(?:е|са)?\s*(?:със\s+следното\s+съдържание"
                        r"|формулиран[а-я]*|поставен[а-я]*))[^:.?]{0,220}:", re.IGNORECASE)
_ABBR = re.compile(r"\b(чл|ал|т|г|бр|д|гр|в\.гр|т\.д|търг|пр|предл|изр|ч|тълк|вр|напр|вкл|ГПК|ЗЗД|ЗС|ЗН|ЕС)\.(?=\s*[\dа-яA-Za-z§])")
_LIST_ITEM = re.compile(r"(?:^|\s)(?:\d{1,2}[.)]|[а-е]\))\s+")
_ABOUT = re.compile(r"по\s+(?:правни[яте]*\s+|материалноправни[яте]*\s+|процесуалноправни[яте]*\s+)?въпрос[а-я]*"
                    r"[^.?]{0,40}?\b(?:за|относно|дали)\s+([^.?]{25,400})", re.IGNORECASE)


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
    """The legal questions as the ruling states them (the cassator's and the ones VKS admits)."""
    text = " ".join((text or "").split())
    # the dot of "чл. 52", "ал. 1", "т. 3", "г." does not end a sentence
    text = _ABBR.sub(lambda m: m.group(1) + "\u2024", text)
    found: list[str] = []
    for m in _SENT_Q.finditer(text):
        found.append(m.group(1))
    for m in _LIST_HEAD.finditer(text):
        block = text[m.end(): m.end() + 2500]
        block = re.split(r"(?:Върховният касационен съд|Настоящият състав|ВКС,?\s+[IV]+|Ответник|Становище)", block)[0]
        items = [i.strip(" ;,–-") for i in _LIST_ITEM.split(block)]
        found += [i for i in items if 25 <= len(i) <= 600]
    for m in _ABOUT.finditer(text):
        found.append(m.group(1))
    out, seen = [], []
    for q in found:
        q = q.strip(" „“\"").replace("\u2024", ".")
        st = _stems(q) - _COMMON
        if len(st) < 3 or any(len(st & s) >= 0.8 * len(st) for s in seen):
            continue
        seen.append(st)
        out.append(q)
    return out[:8]


def _label_refs(label: str) -> set[str]:
    """The number/date of a VKS act in our report's label (it names VKS itself, so no tail check)."""
    out = set()
    for no, d in _VKS_REF.findall((label or "").replace("№", "№ ")):
        dd, mm, yy = d.split(".")
        out.add(f"{int(no)}/{int(dd):02d}.{int(mm):02d}.{yy}")
    return out


def _stems(text: str) -> set[str]:
    return {w[:6].lower() for w in re.findall(r"[А-Яа-я]{5,}", text or "")}


_COMMON = _stems("следва въпроса въпросът правен процесуален материален съдът решението въззивния "
                 "касационно обжалване допускане когато какви какво дали")


def question_pairs(ours: list[str], theirs: list[str]) -> list[dict]:
    """For every question read from the ruling: our closest question (index from 1) and the share of
    its words found there (0..1)."""
    out = []
    for t in theirs:
        want = _stems(t) - _COMMON
        if len(want) < 3:
            continue
        best, idx = 0.0, None
        for k, q in enumerate(ours, 1):
            share = len(want & _stems(q)) / len(want)
            if share > best:
                best, idx = share, k
        out.append({"vks": t, "ours": idx, "share": round(best, 2)})
    return out


def question_match(ours: list[str], theirs: list[str]) -> float | None:
    """Best share of a VKS question's words found in one of our questions (0..1); None when the
    ruling's questions could not be read (then the case is left out of the count)."""
    pairs = question_pairs(ours, theirs)
    return max(p["share"] for p in pairs) if pairs else None


# why a cited act is not in our report, from the run alone (the database check is added by the job)
WHY = {"irrelevant": "намерено и оценено, но AI го прецени като неотносимо",
       "skipped": "намерено, но отпадна",
       "not_picked": "намерено от търсенето, но не влезе сред оценените (лимит на брой)",
       "not_found": "търсенето не го намери",
       "not_in_db": "няма го в базата (не е търсено в сайта)"}


def missed(run: dict, refs: set[str]) -> list[dict]:
    """For each cited act our report did not mark as relevant: where it was lost."""
    assessed: dict[str, bool] = {}
    for a in run.get("assessments", []):
        for r in _label_refs(a.get("label", "")):
            assessed[r] = assessed.get(r, False) or bool(a.get("relevant"))
    skipped = {}
    for line in run.get("skipped", []):
        for r in _label_refs(line.split(":", 1)[0]):
            skipped[r] = line.split(":", 1)[1].strip() if ":" in line else ""
    found = set()
    for label in run.get("candidates", []):
        found |= _label_refs(label)
    out = []
    for r in sorted(refs):
        if assessed.get(r):
            continue
        if r in assessed:
            out.append({"ref": r, "why": "irrelevant"})
        elif r in skipped:
            out.append({"ref": r, "why": "skipped", "detail": skipped[r]})
        elif r in found:
            out.append({"ref": r, "why": "not_picked"})
        else:
            out.append({"ref": r, "why": "not_found"})
    return out


def in_database(conn, refs: list[str]) -> dict[str, str | None]:
    """ref -> proceeding article of the VKS act in our database ("290", "288", "" for other), or None
    when the act is not there."""
    out: dict[str, str | None] = {}
    if not refs:
        return out
    with conn.cursor() as cur:
        for r in refs:
            no, d = r.split("/")
            dd, mm, yy = d.split(".")
            cur.execute("""SELECT coalesce(proceeding_article, '') AS art FROM decisions
                           WHERE source IN ('vks', 'vks-tr') AND act_number = %s AND act_date = %s LIMIT 1""",
                        (no, date(int(yy), int(mm), int(dd))))
            row = cur.fetchone()
            out[r] = row["art"] if row else None
    return out


def score(run: dict, ruling_text: str, cutoff: date | None = None) -> dict:
    ours = [q["text"] for q in run.get("analysis", {}).get("questions", [])]
    theirs = ruling_questions(ruling_text)
    cited = cited_refs(ruling_text, cutoff)
    found = set()
    for a in run.get("assessments", []):
        if a.get("relevant"):
            found |= _label_refs(a.get("label", ""))
    return {"vks_questions": theirs, "our_questions": ours, "question_match": question_match(ours, theirs),
            "pairs": question_pairs(ours, theirs),
            "cited": sorted(cited), "practice_found": sorted(cited & found),
            "missed": missed(run, cited - found),
            "cited_later": len(cited_refs(ruling_text) - cited),
            "contra": sum(1 for a in run.get("assessments", []) if a.get("relevant") and a.get("stance") == "противоречи")}


def pick_cases(conn, n: int = 10) -> list[dict]:
    """Recent admission rulings with a readable appealed decision on a listed court, half admitted."""
    picked: dict[str, list[dict]] = {"допуска": [], "не допуска": []}
    with conn.cursor() as cur:
        cur.execute("""SELECT d.id::text AS id, d.admission_result, d.act_number, d.act_date, d.canonical_url,
                              v.canonical_text
                       FROM decisions d JOIN decision_versions v ON v.id = d.current_version_id
                       WHERE d.admission_result IN ('допуска', 'не допуска')
                       ORDER BY d.act_date DESC NULLS LAST LIMIT 4000""")
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


# AI judgement of each question read from the ruling against ours, by meaning
VERDICTS = ("същият", "частично", "няма", "не е въпрос")
JUDGE_SYSTEM = ("Ти си български юрист и сравняваш правни въпроси по чл. 280, ал. 1 ГПК. Преценяваш по смисъла, "
                "не по думите.")
JUDGE_INSTRUCTIONS = """За всеки ВЪПРОС НА ВКС (извлечен автоматично от определение на ВКС, затова може да е откъс,
който не е правен въпрос) посочи най-близкия НАШ ВЪПРОС и оценка:
- "същият": нашият въпрос поставя същия правен проблем, дори с други думи или малко по-широко/по-тясно,
  така че отговорът на ВКС би отговорил и на него;
- "частично": засяга същата материя или норма, но основният правен проблем е друг;
- "няма": нито един наш въпрос не засяга този проблем (тогава ours = 0);
- "не е въпрос": текстът не е правен въпрос (напр. откъс от мотиви или изложение на фактите) (ours = 0).
why: едно кратко изречение защо."""
JUDGE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["items"],
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["vks", "ours", "verdict", "why"],
        "properties": {"vks": {"type": "integer"}, "ours": {"type": "integer"},
                       "verdict": {"type": "string", "enum": list(VERDICTS)}, "why": {"type": "string"}}}}}}


def judge_questions(ai, model: str, effort: str, sc: dict) -> None:
    """One AI call: adds {"ours", "verdict", "why"} as "judge" to each pair of the score, and the best
    verdict of the case as sc["judged"] (None when no VKS question is a real question)."""
    pairs, ours = sc.get("pairs") or [], sc.get("our_questions") or []
    if not pairs or not ours:
        return
    user = (JUDGE_INSTRUCTIONS + "\n\n=== ВЪПРОСИ НА ВКС ===\n"
            + "\n".join(f"{i}. {p['vks']}" for i, p in enumerate(pairs, 1))
            + "\n\n=== НАШИ ВЪПРОСИ ===\n" + "\n".join(f"{k}. {q}" for k, q in enumerate(ours, 1)))
    out = ai.structured(model=model, system=JUDGE_SYSTEM, user=user, schema_name="question_match",
                        schema=JUDGE_SCHEMA, effort=effort)
    for it in out.get("items", []):
        i = it.get("vks", 0)
        if 1 <= i <= len(pairs) and it.get("verdict") in VERDICTS:
            k = it.get("ours", 0)
            pairs[i - 1]["judge"] = {"ours": k if 1 <= k <= len(ours) else None,
                                     "verdict": it["verdict"], "why": str(it.get("why", ""))[:300]}
    real = [p["judge"]["verdict"] for p in pairs if p.get("judge") and p["judge"]["verdict"] != "не е въпрос"]
    sc["judged"] = next((v for v in VERDICTS if v in real), None)


# a question counts as found from this share of shared words: in the real runs (05.10.2026) the same
# question in other words scored 0.44–0.48, a different question under 0.25
HIT = 0.4


def _verdict(sc: dict) -> str | None:
    """By meaning (AI) when judged, else by shared words."""
    if sc.get("judged") is not None or any(p.get("judge") for p in sc.get("pairs") or []):
        return sc.get("judged")
    if sc.get("question_match") is None:
        return None
    return "същият" if sc["question_match"] >= HIT else "няма"


def summary(cases: list[dict]) -> dict:
    done = [c for c in cases if c.get("score")]
    if not done:
        return {"done": 0}
    adm = [c for c in done if c["outcome"] == "допуска" and _verdict(c["score"]) is not None]
    hit = [c for c in adm if _verdict(c["score"]) == "същият"]
    with_cited = [c for c in done if c["score"]["cited"]]
    practice = [c for c in with_cited if c["score"]["practice_found"]]
    return {"done": len(done), "admitted": len(adm), "question_hits": len(hit),
            "with_cited": len(with_cited), "practice_hits": len(practice),
            "failed": sum(1 for c in cases if c.get("error")),
            "by_meaning": any(c["score"].get("judged") is not None for c in done)}
