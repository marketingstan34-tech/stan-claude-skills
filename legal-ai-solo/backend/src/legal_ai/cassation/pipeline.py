"""Cassation analysis: appellate decision -> legal questions -> VKS practice -> stance.

Retrieval here is the live VKS act search (exact word forms, AND). It is a stand-in until
the full local corpus exists, and the report says so.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from legal_ai.ai import OpenAIProvider
from legal_ai.cassation import prompts as P
from legal_ai.citations.verify import TextStatus, locate_quote, verify_quote
from legal_ai.http import FetchError, PoliteClient
from legal_ai.sources.courts import COURTS
from legal_ai.sources.courts.acts import ActRow, acts_url, parse_acts
from legal_ai.sources.courts.document import extract_text
from legal_ai.sources.vks import LIST_TRUNCATION_LIMIT
from legal_ai.sources.vks.parser import ParsedAct, parse_act, parse_list
from legal_ai.sources.vks.urls import ListQuery, act_url, list_url

MAX_LIST_QUERIES = 30
PER_QUESTION = 4
MAX_ACTS = 24
EXCERPT_CHARS = 7000


@dataclass
class SourceDoc:
    label: str
    url: str
    text: str
    fmt: str
    retrieved_at: str
    act_date: date | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class Quote:
    text: str
    status: str            # text_verified | not_found
    start: int | None = None
    end: int | None = None


@dataclass
class Assessment:
    question_id: str
    source_id: str
    label: str
    url: str
    chamber: str | None
    proceeding_article: str | None
    relevant: bool
    stance: str
    vks_rule: str
    quote: Quote
    explanation: str
    matched_word_sets: list[list[str]]


@dataclass
class RunResult:
    appellate: SourceDoc
    analysis: dict
    holding_quotes: dict[str, Quote]
    assessments: list[Assessment]
    searches: list[dict]
    skipped: list[str]
    usage: dict
    models: dict
    prompt_version: str
    created_at: str
    cutoff: str


_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[„\"(]?[А-ЯA-Z0-9])")


def check_quote(text: str, quote: str) -> Quote:
    """Whole quote verbatim, or else every sentence verbatim and in order.

    In the second case the sentences are not contiguous in the source; they are shown
    joined by " […] " so the reader sees that something was left out between them.
    """
    loc = locate_quote(text, quote) if quote.strip() else None
    if loc and verify_quote(text, loc[0], loc[1], quote) == TextStatus.TEXT_VERIFIED:
        return Quote(quote, "text_verified", loc[0], loc[1])
    parts = [s for s in _SENTENCE.split(quote.strip()) if s.strip()]
    if len(parts) > 1:
        spans, pos = [], 0
        for part in parts:
            found = locate_quote(text[pos:], part)
            if not found:
                return Quote(quote, "not_found")
            spans.append((pos + found[0], pos + found[1]))
            pos += found[1]
        shown = " […] ".join(" ".join(text[a:b].split()) for a, b in spans)
        return Quote(shown, "text_verified", spans[0][0], spans[-1][1])
    return Quote(quote, "not_found")


# ---------- sources ----------

def fetch_appellate(client: PoliteClient, court_key: str, number: int, year: int,
                    case_type: str = "") -> tuple[ActRow, SourceDoc]:
    court = COURTS[court_key]
    page = client.get(acts_url(court, number, year, case_type, "решение"))
    rows = [r for r in parse_acts(page.body.decode("utf-8", errors="replace"))
            if r.act_type == "Решение" and r.file_url and r.case_number == number
            and r.case_year == year]
    if not rows:
        raise FetchError(f"Няма публикувано решение по дело {number}/{year} в {court.name}.")
    row = max(rows, key=lambda r: r.act_date or date.min)
    f = client.get(row.file_url)
    t = extract_text(f.body, f.content_type)
    label = f"{court.name}, {row.case_kind} № {number}/{year}, Решение от " \
            f"{row.act_date.strftime('%d.%m.%Y') if row.act_date else '?'}"
    return row, SourceDoc(label, row.file_url, t.text, t.fmt, f.retrieved_at, row.act_date, t.warnings)


def load_local(path: Path, label: str = "") -> SourceDoc:
    body = path.read_bytes()
    if path.suffix.lower() == ".txt":
        text, fmt, warnings = body.decode("utf-8"), "txt", []
    else:
        t = extract_text(body)
        text, fmt, warnings = t.text, t.fmt, t.warnings
    return SourceDoc(label or path.name, path.resolve().as_uri(), text, fmt,
                     datetime.now(timezone.utc).isoformat(), None, warnings)


# ---------- VKS retrieval ----------

def _words_ok(ws: list[str]) -> list[str]:
    out = []
    for w in ws:
        w = " ".join(w.split()).strip(" ,.;:„“\"'")
        if w and len(w) <= 40:
            out.append(w)
    return out[:4]


def search_vks(vks: PoliteClient, search_plan: list[dict], cutoff: date,
               searches_log: list[dict]) -> dict[str, dict]:
    """source_id -> {row, by_question: {qid: [word_set, ...]}}

    One query per word set over all case types (criminal acts are dropped later by
    chamber); the query budget is split evenly between the questions.
    """
    found: dict[str, dict] = {}
    per_question = max(2, MAX_LIST_QUERIES // max(1, len(search_plan)))
    for item in search_plan:
        qid = item["question_id"]
        used = 0
        for ws in item["word_sets"]:
            ws = _words_ok(ws)
            if not ws:
                continue
            if used >= per_question:
                searches_log.append({"question_id": qid, "words": ws, "skipped": "лимит на заявките"})
                continue
            q = ListQuery(2008, 1, cutoff.year, cutoff.month, act_type="15",
                          case_type="empty", words=" ".join(ws))
            url = list_url(q)
            used += 1
            try:
                rows = parse_list(vks.get(url).body.decode("utf-8", errors="replace"))
            except FetchError as exc:
                searches_log.append({"question_id": qid, "words": ws, "url": url, "error": str(exc)})
                continue
            searches_log.append({"question_id": qid, "words": ws, "case_type": "всички",
                                 "url": url, "rows": len(rows),
                                 "truncated": len(rows) >= LIST_TRUNCATION_LIMIT})
            for r in rows:
                if r.act_date and r.act_date > cutoff:
                    continue
                e = found.setdefault(r.source_id, {"row": r, "by_question": {}})
                e["by_question"].setdefault(qid, []).append(ws)
    return found


def pick_candidates(found: dict[str, dict], question_ids: list[str]) -> dict[str, list[str]]:
    """Per question: acts matched by most word sets (broad truncated lists count less)."""
    picked: dict[str, list[str]] = {}
    total: set[str] = set()
    for qid in question_ids:
        scored = [(len(e["by_question"][qid]), e["row"].act_date or date.min, sid)
                  for sid, e in found.items() if qid in e["by_question"]]
        scored.sort(reverse=True)
        chosen = []
        for _, _, sid in scored:
            if len(chosen) >= PER_QUESTION:
                break
            if sid in total or len(total) < MAX_ACTS:
                chosen.append(sid)
                total.add(sid)
        picked[qid] = chosen
    return picked


def excerpt(act: ParsedAct, words: list[str]) -> str:
    """Admission paragraphs + paragraphs richest in the search words, in document order."""
    lw = [w.lower() for w in words]
    scores = []
    for p in act.paragraphs:
        t = p.text.lower()
        scores.append((sum(1 for w in lw if w in t), p.no))
    keep = set(act.admission_paragraph_nos)
    budget = EXCERPT_CHARS - sum(len(act.paragraphs[i].text) for i in keep if i < len(act.paragraphs))
    for score, no in sorted(scores, key=lambda s: (-s[0], s[1])):
        if score == 0 or budget <= 0:
            break
        if no not in keep:
            keep.add(no)
            budget -= len(act.paragraphs[no].text)
    parts = [act.heading or ""] + [act.paragraphs[i].text for i in sorted(keep) if i < len(act.paragraphs)]
    return "\n\n".join(p for p in parts if p)[: EXCERPT_CHARS + 2000]


# ---------- main ----------

def run_analysis(ai: OpenAIProvider, vks: PoliteClient, appellate: SourceDoc,
                 cutoff: date) -> RunResult:
    cfg = ai.config
    analysis = ai.structured(
        model=cfg.analysis_model, system=P.SYSTEM_BASE,
        user=f"{P.ANALYSIS_INSTRUCTIONS}\n\n=== ВЪЗЗИВНО РЕШЕНИЕ ===\n{appellate.text}",
        schema_name="cassation_analysis", schema=P.ANALYSIS_SCHEMA)
    holding_quotes = {h["id"]: check_quote(appellate.text, h["quote"]) for h in analysis["holdings"]}

    searches: list[dict] = []
    found = search_vks(vks, analysis["search"], cutoff, searches)
    qids = [q["id"] for q in analysis["questions"]]
    picked = pick_candidates(found, qids)

    acts: dict[str, ParsedAct | None] = {}
    skipped: list[str] = []
    assessments: list[Assessment] = []
    holdings = {h["id"]: h for h in analysis["holdings"]}
    for q in analysis["questions"]:
        qwords = sorted({w for item in analysis["search"] if item["question_id"] == q["id"]
                         for ws in item["word_sets"] for w in _words_ok(ws)})
        for sid in picked.get(q["id"], []):
            if sid not in acts:
                try:
                    acts[sid] = parse_act(vks.get(act_url(sid)).body.decode("utf-8", errors="replace"))
                except FetchError as exc:
                    acts[sid] = None
                    skipped.append(f"{sid}: {exc}")
            act = acts[sid]
            if act is None:
                continue
            if act.chamber and "наказател" in act.chamber.lower():
                skipped.append(f"{found[sid]['row'].link_text}: наказателно дело")
                continue
            if act.proceeding_article != "290":
                skipped.append(f"{found[sid]['row'].link_text}: не е решение по чл. 290 ГПК")
                continue
            hold = "\n".join(f"- {holdings[h]['summary']}" for h in q["holding_ids"] if h in holdings)
            user = (f"{P.ASSESS_INSTRUCTIONS}\n\n(А) ВЪПРОС: {q['text']}\n"
                    f"Извод на въззивния съд:\n{hold or '-'}\n\n"
                    f"(Б) {found[sid]['row'].link_text} ({act.chamber or 'отделение не е разпознато'})\n"
                    f"{excerpt(act, qwords)}")
            a = ai.structured(model=cfg.analysis_model, system=P.SYSTEM_BASE, user=user,
                              schema_name="vks_assessment", schema=P.ASSESS_SCHEMA)
            assessments.append(Assessment(
                question_id=q["id"], source_id=sid, label=found[sid]["row"].link_text,
                url=act_url(sid), chamber=act.chamber, proceeding_article=act.proceeding_article,
                relevant=bool(a["relevant"]), stance=a["stance"], vks_rule=a["vks_rule"],
                quote=check_quote(act.canonical_text, a["quote"]) if a["quote"] else Quote("", "empty"),
                explanation=a["explanation"],
                matched_word_sets=found[sid]["by_question"].get(q["id"], [])))

    return RunResult(
        appellate=appellate, analysis=analysis, holding_quotes=holding_quotes,
        assessments=assessments, searches=searches, skipped=skipped,
        usage={"calls": ai.usage.calls, "input_tokens": ai.usage.input_tokens,
               "output_tokens": ai.usage.output_tokens, "by_model": ai.usage.by_model},
        models={"analysis": cfg.analysis_model, "light": cfg.light_model},
        prompt_version=P.PROMPT_VERSION, created_at=datetime.now(timezone.utc).isoformat(),
        cutoff=cutoff.isoformat())


# ---------- report ----------

_STANCE_ORDER = {"противоречи": 0, "неясно": 1, "подкрепя": 2, "неотносимо": 3}
_STANCE_TITLE = {"противоречи": "Противоречи на въззивния съд (полезно за касатора)",
                 "неясно": "Относимо, посоката не е ясна",
                 "подкрепя": "Подкрепя въззивния съд (внимание)"}


def _q(quote: Quote) -> str:
    if quote.status == "text_verified":
        return f"> „{quote.text}“  \n> ✅ цитатът е проверен дословно в текста"
    if quote.status == "empty":
        return ""
    return f"> „{quote.text}“  \n> ⚠️ цитатът НЕ е намерен дословно — не го използвайте без проверка"


def render_markdown(r: RunResult) -> str:
    a = r.analysis
    li = a["lower_instance"]
    lines = [
        f"# Касационен анализ — {r.appellate.label}",
        "",
        f"Източник: {r.appellate.url}  ",
        f"Анализът е направен {r.created_at[:16].replace('T', ' ')} UTC · модел {r.models['analysis']}"
        f" · промпт {r.prompt_version} · практика на ВКС до {r.cutoff}",
        "",
        "> Това е помощен анализ, не правно становище. Всеки цитат е проверен дословно спрямо "
        "източника (✅) или е маркиран (⚠️). Търсенето е в сайта на ВКС по точни думи и може да "
        "пропусне практика; тълкувателните решения още не са включени.",
        "",
        "## Казусът", "", a["case_summary"], "",
        "## Обжалван пред въззивния съд акт (както е посочен в текста)", "",
        f"{li['act'] or '?'} от {li['date'] or '?'} по {li['case'] or '?'}, {li['court'] or '?'}", "",
        "## Ключови изводи на въззивния съд", "",
    ]
    for h in a["holdings"]:
        lines += [f"**{h['id']}.** {h['summary']}", "", _q(r.holding_quotes[h["id"]]), ""]
    lines += ["## Правни въпроси и практика на ВКС", ""]
    by_q: dict[str, list[Assessment]] = {}
    for x in r.assessments:
        by_q.setdefault(x.question_id, []).append(x)
    for q in a["questions"]:
        lines += [f"### {q['id']}. {q['text']}", "",
                  f"*{q['kind']} · основание: чл. 280, ал. 1, {q['ground']} ГПК (предложение)* — "
                  f"{q['why_decisive']}", ""]
        found = [x for x in by_q.get(q["id"], []) if x.relevant and x.stance != "неотносимо"]
        if not found:
            lines += ["Не е намерена относима практика с това търсене.", ""]
        for stance in ("противоречи", "неясно", "подкрепя"):
            group = [x for x in found if x.stance == stance]
            if not group:
                continue
            lines += [f"**{_STANCE_TITLE[stance]}**", ""]
            for x in group:
                lines += [f"- [{x.label}]({x.url}) · {x.chamber or '?'}  ",
                          f"  {x.vks_rule}  ", f"  *{x.explanation}*", ""]
                qt = _q(x.quote)
                if qt:
                    lines += ["  " + qt.replace("\n", "\n  "), ""]
        sets = [s for s in r.searches if s.get("question_id") == q["id"] and "rows" in s]
        if sets:
            lines += ["<details><summary>Търсения</summary>", ""]
            lines += [f"- {' + '.join(s['words'])} ({s['case_type']}): {s['rows']} резултата"
                      + (" — отрязан списък" if s.get("truncated") else "") for s in sets]
            lines += ["", "</details>", ""]
    lines += ["## Технически данни", "",
              f"- AI заявки: {r.usage['calls']}, токени вход/изход: {r.usage['input_tokens']}"
              f"/{r.usage['output_tokens']}",
              f"- Пропуснати: {len(r.skipped)}"]
    lines += [f"  - {s}" for s in r.skipped[:20]]
    if r.appellate.warnings:
        lines += [f"- Предупреждения за текста: {', '.join(r.appellate.warnings)}"]
    return "\n".join(lines) + "\n"


def save_run(r: RunResult, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = re.sub(r"[^0-9]", "", r.created_at)[:14]
    d = out_dir / stamp
    d.mkdir(parents=True, exist_ok=True)
    (d / "report.md").write_text(render_markdown(r), encoding="utf-8")
    data = asdict(r)
    data["appellate"]["text_chars"] = len(r.appellate.text)
    data["appellate"].pop("text")
    (d / "run.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str),
                                encoding="utf-8")
    (d / "appellate.txt").write_text(r.appellate.text, encoding="utf-8")
    return d
