"""Draft of the cassation appeal itself (касационна жалба, чл. 281 and чл. 284 ГПК).

One AI call writes the complaints and arguments; everything else is assembled with fixed rules.
Each challenged passage is checked verbatim against the decision (shown only if found), and only
VKS decisions that the report itself marked "противоречи" may be cited. Party data, fees and
anything missing stay in [square brackets] for the lawyer.
"""

from __future__ import annotations

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


def contra_list(run: dict, chosen: list[str] | None) -> dict[str, str]:
    """label -> the rule of each "противоречи" decision for the chosen questions."""
    out: dict[str, str] = {}
    for x in run.get("assessments", []):
        if x.get("relevant") and x.get("stance") == "противоречи" and (not chosen or x["question_id"] in chosen):
            out.setdefault(x["label"], x.get("vks_rule", ""))
    return out


def appeal_prompt(run: dict, text: str, chosen: list[str] | None,
                  context_docs: list[tuple[str, str]] | None = None) -> str:
    a = run["analysis"]
    holdings = "\n".join(f"{h['id']}. {h['summary']}" for h in a.get("holdings", []))
    questions = "\n".join(f"{q['id']}. {q['text']}" for q in a["questions"] if not chosen or q["id"] in chosen)
    vks = "\n".join(f"- {label}: {rule}" for label, rule in contra_list(run, chosen).items()) or "(няма)"
    notes = (run.get("notes") or "").strip()
    return (f"{P.APPEAL_INSTRUCTIONS}\n\n=== ИЗВОДИ НА ВЪЗЗИВНИЯ СЪД ===\n{holdings}\n\n"
            f"=== ИЗБРАНИ ПРАВНИ ВЪПРОСИ ===\n{questions}\n\n=== РЕШЕНИЯ НА ВКС „ПРОТИВОРЕЧИ“ ===\n{vks}\n\n"
            f"=== ВЪЗЗИВНО РЕШЕНИЕ ===\n{text}"
            + (f"\n\n{P.NOTES_HEADER}\n{notes}" if notes else "")
            + (P.CONTEXT_NOTE + P.context_block(context_docs) if context_docs else ""))


def generate(ai, run: dict, text: str, chosen: list[str] | None,
             context_docs: list[tuple[str, str]] | None = None) -> dict:
    """The AI part, checked: verbatim quotes and VKS labels from the report only."""
    from legal_ai.cassation.pipeline import check_quote
    raw = ai.structured(model=ai.config.analysis_model, system=P.SYSTEM_BASE,
                        user=appeal_prompt(run, text, chosen, context_docs), schema_name="cassation_appeal",
                        schema=P.APPEAL_SCHEMA)
    allowed = contra_list(run, chosen)
    grounds = []
    for g in raw["grounds"]:
        q = check_quote(text, g["quote"]) if g["quote"].strip() else None
        grounds.append({**g, "quote_status": q.status if q else "none", "quote": q.text if q else "",
                        "vks_labels": [label for label in g["vks_labels"] if label in allowed],
                        "dropped_labels": [label for label in g["vks_labels"] if label not in allowed]})
    return {"grounds": grounds, "petitum_scope": raw["petitum_scope"], "petitum_part": raw["petitum_part"],
            "petitum_request": raw["petitum_request"], "version": P.APPEAL_VERSION,
            "model": ai.config.analysis_model, "created_at": datetime.now(timezone.utc).isoformat(),
            "usage": {"calls": ai.usage.calls, "input_tokens": ai.usage.input_tokens,
                      "output_tokens": ai.usage.output_tokens}}


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
           Block("heading", "УВАЖАЕМИ ВЪРХОВНИ СЪДИИ,"),
           Block("p", f"В законния срок обжалвам {act}, {scope}. Считам решението за неправилно поради "
                      + ", ".join(kinds) + " – касационни основания по чл. 281, т. 3 ГПК"
                      + (", както и за " + " и ".join(k for k in kinds if k in ("недопустимост", "нищожност"))
                         if any(k in ("недопустимост", "нищожност") for k in kinds) else "") + ".")]
    for i, g in enumerate(appeal["grounds"]):
        num = ROMAN[i] if i < len(ROMAN) else str(i + 1)
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
    out.append(Block("p", "Съображенията за допускане на касационното обжалване са изложени в приложеното изложение "
                          "по чл. 284, ал. 3, т. 1 ГПК."))
    out.append(Block("p", f"Моля да допуснете касационно обжалване, да отмените въззивното решение {scope} и "
                          f"{appeal['petitum_request'].strip().rstrip('.')}."))
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
