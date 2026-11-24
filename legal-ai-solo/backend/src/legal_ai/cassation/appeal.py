"""Draft of the cassation appeal itself (касационна жалба, чл. 281 and чл. 284 ГПК).

One AI call writes the opening, the complaints and the closing in the lawyer's style (from his own
filings, when given as style samples); the header, request, costs and attachments are fixed rules.
Every quotation („…“, 40+ characters) is checked verbatim against the decision, the other case
documents and the verified VKS quotes, and gets a note if not found; the VKS decisions in the list
field are only those the report marked "противоречи". Party data, fees and
anything missing stay in [square brackets] for the lawyer.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from legal_ai.cassation import prompts as P
from legal_ai.cassation.draft import Block, _court_short, _party

KIND_TEXT = {
    "материален закон": "нарушение на материалния закон",
    "процесуални правила": "съществено нарушение на съдопроизводствените правила",
    "необоснованост": "необоснованост",
    "недопустимост": "недопустимост",
    "нищожност": "нищожност",
}
ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII"]
DISCLAIMER = ("[ЧЕРНОВА НА КАСАЦИОННА ЖАЛБА, генерирана с AI от справката. Проверете всяко оплакване, довод и "
              "цитирано решение преди подаване. Текстът в квадратни скоби се попълва или премахва.]")


def contra_list(run: dict, chosen: list[str] | None, stance: str = "противоречи") -> dict[str, str]:
    """label -> the rule of each decision with this stance ("противоречи" by default) for the chosen questions."""
    out: dict[str, str] = {}
    for x in run.get("assessments", []):
        if x.get("relevant") and x.get("stance") == stance and (not chosen or x["question_id"] in chosen):
            out.setdefault(x["label"], x.get("vks_rule", ""))
    return out


def contra_quotes(run: dict, chosen: list[str] | None, stance: str = "противоречи") -> dict[str, str]:
    """label -> the verified quote of each decision with this stance (only quotes found verbatim)."""
    out: dict[str, str] = {}
    for x in run.get("assessments", []):
        q = x.get("quote") if isinstance(x.get("quote"), dict) else {}
        if (x.get("relevant") and x.get("stance") == stance and (not chosen or x["question_id"] in chosen)
                and q.get("status") == "text_verified" and q.get("text")):
            out.setdefault(x["label"], q["text"])
    return out


def appeal_prompt(run: dict, text: str, chosen: list[str] | None,
                  context_docs: list[tuple[str, str]] | None = None,
                  style_docs: list[tuple[str, str]] | None = None,
                  instructions: str = "", decision_title: str = "ВЪЗЗИВНО РЕШЕНИЕ", stance: str = "противоречи",
                  archive_docs: list[tuple[str, str]] | None = None) -> str:
    a = run["analysis"]
    holdings = "\n".join(f"{h['id']}. {h['summary']}" for h in a.get("holdings", []))
    questions = "\n".join(f"{q['id']}. {q['text']}" for q in a["questions"] if not chosen or q["id"] in chosen)
    quotes = contra_quotes(run, chosen, stance)
    vks = "\n".join(f"- {label}: {rule}" + (f"\n  Дословен цитат от решението: „{quotes[label]}“" if label in quotes else "")
                    for label, rule in contra_list(run, chosen, stance).items()) or "(няма)"
    notes = (run.get("notes") or "").strip()
    return (f"{instructions or P.APPEAL_INSTRUCTIONS}\n\n=== ИЗВОДИ НА ВЪЗЗИВНИЯ СЪД (от справката) ===\n{holdings}\n\n"
            f"=== ИЗБРАНИ ПРАВНИ ВЪПРОСИ ===\n{questions}\n\n=== РЕШЕНИЯ НА ВКС „{stance.upper()}“ ===\n{vks}\n\n"
            f"=== {decision_title} ===\n{text}"
            + (f"\n\n{P.NOTES_HEADER}\n{notes}" if notes else "")
            + (P.context_block(context_docs, P.APPEAL_CONTEXT_EACH, P.APPEAL_CONTEXT_TOTAL) if context_docs else "")
            + (P.context_block(style_docs, P.STYLE_EACH, P.STYLE_TOTAL, P.STYLE_HEADER) if style_docs else "")
            + (P.context_block(archive_docs, P.STYLE_EACH, P.STYLE_TOTAL, P.ARCHIVE_HEADER) if archive_docs else ""))


_INLINE_QUOTE = re.compile(r"„([^“”\n]{40,})[“”]")
UNVERIFIED = " [цитатът не е намерен дословно в документите – проверете]"


def mark_quotes(paragraph: str, sources: list[str]) -> tuple[str, int]:
    """Adds a note after every quotation („…“, 40+ characters) not found verbatim in any source text."""
    from legal_ai.cassation.pipeline import check_quote
    missing = 0

    def note(m: re.Match) -> str:
        nonlocal missing
        if any(check_quote(src, m.group(1)).status == "text_verified" for src in sources if src):
            return m.group(0)
        missing += 1
        return m.group(0) + UNVERIFIED
    return _INLINE_QUOTE.sub(note, paragraph), missing


def generate(ai, run: dict, text: str, chosen: list[str] | None,
             context_docs: list[tuple[str, str]] | None = None,
             style_docs: list[tuple[str, str]] | None = None,
             instructions: str = "", decision_title: str = "ВЪЗЗИВНО РЕШЕНИЕ", stance: str = "противоречи",
             archive_docs: list[tuple[str, str]] | None = None) -> dict:
    """The AI part, checked: quotations found verbatim in the documents, VKS labels from the report only."""
    raw = ai.structured(model=ai.config.analysis_model, system=P.SYSTEM_BASE,
                        user=appeal_prompt(run, text, chosen, context_docs, style_docs, instructions,
                                           decision_title, stance, archive_docs),
                        schema_name="cassation_appeal", schema=P.APPEAL_SCHEMA)
    allowed = contra_list(run, chosen, stance)
    sources = [text, *(t for _, t in context_docs or ()), *contra_quotes(run, chosen, stance).values()]
    unverified = 0

    def checked(par: str) -> str:
        nonlocal unverified
        out, n = mark_quotes(par.strip(), sources)
        unverified += n
        return out
    grounds = []
    for g in raw["grounds"]:
        grounds.append({"kind": g["kind"], "holding_ids": g["holding_ids"],
                        "paragraphs": [checked(p) for p in g["paragraphs"] if p.strip()],
                        "vks_labels": [label for label in g["vks_labels"] if label in allowed],
                        "dropped_labels": [label for label in g["vks_labels"] if label not in allowed]})
    return {"intro": checked(raw["intro"]), "grounds": grounds, "closing": checked(raw["closing"]),
            "petitum_scope": raw["petitum_scope"], "petitum_part": raw["petitum_part"],
            "petitum_request": raw["petitum_request"], "unverified_quotes": unverified,
            "context_names": [n for n, _ in context_docs or ()], "style_names": [n for n, _ in style_docs or ()],
            "archive_names": [n for n, _ in archive_docs or ()],
            "version": P.APPEAL_VERSION,
            "model": ai.config.analysis_model, "created_at": datetime.now(timezone.utc).isoformat(),
            "usage": {"calls": ai.usage.calls, "input_tokens": ai.usage.input_tokens,
                      "output_tokens": ai.usage.output_tokens}}


_ACT_REF = re.compile(r"(\d{1,6})\s*/\s*(\d{1,2}\.\d{1,2}\.\d{4})")


def _mentioned(label: str, body: str) -> bool:
    """Whether the decision `label` (e.g. "Решение №323/01.06.2026 по дело №371/2025") is cited in `body`."""
    m = _ACT_REF.search(label)
    if not m:
        return label in body
    flat = re.sub(r"\s+", "", body)
    return f"{m.group(1)}/{m.group(2)}" in flat


_ALREADY = re.compile(r"^\s*(?:и\s+)?да\s+отмени(?:те)?\s+(?:изцяло\s+|частично\s+)?(?:обжалваното\s+|въззивното\s+)?"
                      r"решение(?:\s+(?:изцяло|частично|в\s+обжалваната\s+част))?\s*(?:,\s*)?(?:и\s+)?", re.IGNORECASE)


def _request(text: str) -> str:
    """What VKS should decide after setting aside, without repeating "да отмени ... решението"."""
    t = _ALREADY.sub("", (text or "").strip()).strip().rstrip(".")
    t = re.sub(r"^вместо\s+него\s+", "вместо него ", t, flags=re.IGNORECASE)
    if t and t[0].isupper() and (len(t) < 2 or t[1].islower()):
        t = t[0].lower() + t[1:]
    t = re.sub(r"\bда\s+постанови\b", "да постановите", t)
    t = re.sub(r"\bда\s+върне\b", "да върнете", t)
    t = re.sub(r"\bда\s+уважи\b", "да уважите", t)
    t = re.sub(r"\bда\s+признае\b", "да признаете", t)
    return t or "[посочете какво да постанови ВКС]"


def build_appeal(run: dict, appeal: dict, case: dict | None = None, act_number: str = "",
                 n_attached: int = 0) -> list[Block]:
    case = case or {}
    court, case_ref, act_date = _court_short(run["appellate"]["label"])
    act = f"Решение № {act_number or '[номер]'}/{act_date} г., постановено по {case_ref} по описа на {court}"
    kinds = []
    for g in appeal["grounds"]:
        if KIND_TEXT[g["kind"]] not in kinds:
            kinds.append(KIND_TEXT[g["kind"]])
    scope = "изцяло" if appeal["petitum_scope"] == "изцяло" else f"в частта, с която {appeal['petitum_part'].strip() or '[посочете частта]'}"

    out = [Block("note", DISCLAIMER),
           Block("heading", "ДО ВЪРХОВНИЯ КАСАЦИОНЕН СЪД"),
           Block("heading", f"ЧРЕЗ {court.upper()}"),
           Block("heading", case_ref.upper().replace(" Г.", " г.")),
           Block("center", "КАСАЦИОННА ЖАЛБА"),
           Block("p", f"От {_party(case)} – чрез адв. {case.get('lawyer') or '[име]'}, съдебен адрес: "
                      f"{case.get('lawyer_address') or '[адрес]'},"),
           Block("p", f"срещу {case.get('opponent') or '[насрещна страна, ЕГН/ЕИК, адрес]'},"),
           Block("p", f"против {act}, {scope}."),
           Block("heading", "УВАЖАЕМИ ВЪРХОВНИ СЪДИИ,")]
    if appeal.get("intro"):
        out.append(Block("p", appeal["intro"].strip()))
    else:   # appeal-1: a fixed opening
        out.append(Block("p", f"В законния срок обжалвам {act}, {scope}. Считам решението за неправилно поради "
                              + ", ".join(kinds) + " – касационни основания по чл. 281, т. 3 ГПК"
                              + (", както и за " + " и ".join(k for k in kinds if k in ("недопустимост", "нищожност"))
                                 if any(k in ("недопустимост", "нищожност") for k in kinds) else "") + "."))
    quotes = contra_quotes(run, None)
    for i, g in enumerate(appeal["grounds"]):
        num = ROMAN[i] if i < len(ROMAN) else str(i + 1)
        if g.get("paragraphs"):     # appeal-2: the lawyer's style, connected paragraphs
            paras = g["paragraphs"]
            out.append(Block("p", f"{num}. {paras[0]}"))
            out += [Block("p", par) for par in paras[1:]]
            body = " ".join(paras)
            for label in g.get("vks_labels", []):
                if not _mentioned(label, body):
                    q = quotes.get(label)
                    out.append(Block("p", f"В този смисъл е и {label} на ВКС"
                                          + (f", в което е прието, че „{q}“." if q else ".")))
            continue
        out.append(Block("heading", f"{num}. {g['title'].strip().rstrip('.')} ({KIND_TEXT[g['kind']]})"))
        if g.get("quote") and g.get("quote_status") == "text_verified":
            out.append(Block("p", "Въззивният съд е приел:"))
            out.append(Block("quote", f"„{g['quote']}“"))
        elif g.get("quote_status") not in (None, "none"):
            out.append(Block("note", "[Цитатът от въззивното решение не е намерен дословно – добавете точния текст.]"))
        out.append(Block("p", g["complaint"].strip()))
        out.append(Block("p", g["argument"].strip()))
        if g.get("vks_labels"):
            out.append(Block("p", "В този смисъл е практиката на ВКС: " + "; ".join(g["vks_labels"]) + "."))
    if appeal.get("closing"):
        out.append(Block("p", appeal["closing"].strip()))
    out.append(Block("p", "Съображенията за допускане на касационното обжалване са изложени в приложеното изложение "
                          "по чл. 284, ал. 3, т. 1 ГПК."))
    out.append(Block("p", f"Моля да допуснете касационно обжалване, да отмените въззивното решение {scope} и "
                          f"{_request(appeal['petitum_request'])}."))
    out.append(Block("p", "Моля да ми присъдите направените разноски пред всички инстанции, вкл. адвокатско "
                          "възнаграждение. [Списък по чл. 80 ГПК]"))
    out.append(Block("heading", "Прилагам:"))
    attachments = ["Изложение по чл. 284, ал. 3, т. 1 ГПК;"]
    if n_attached:
        attachments.append(f"Копия на цитираните решения на ВКС ({n_attached} бр.);")
    attachments += ["Документ за платена държавна такса [сума];", "Пълномощно;",
                    "Препис от жалбата и приложенията за насрещната страна."]
    out += [Block("item", f"{k}. {text}") for k, text in enumerate(attachments, 1)]
    out += [Block("p", "С уважение: ......................................"),
            Block("p", f"(адв. {case.get('lawyer') or '[име]'})")]
    return out
