"""„Съдия от ВКС": a second AI reads the statement (чл. 284, ал. 3, т. 1) and the appeal as a VKS
judge deciding on admission (чл. 288 ГПК) would, by the standards of ТР 1/2009 на ОСГТК, and lists
the weak points before filing. One AI call; nothing is changed in the documents.
"""

from __future__ import annotations

from datetime import datetime, timezone

from legal_ai.cassation import prompts as P

JUDGE_VERSION = "judge-1"

JUDGE_INSTRUCTIONS = """Ти си съдия във Върховния касационен съд и решаваш дали да допуснеш касационно обжалване
(чл. 288 ГПК) по изложението по чл. 284, ал. 3, т. 1 ГПК и касационната жалба по-долу. Търси причина да
откажеш, както прави съдът, и кажи на адвоката честно какво ще те накара да откажеш и как да го поправи.

Прилагай стандартите на ТР № 1/19.02.2010 г. по тълк. д. № 1/2009 г. на ОСГТК и практиката след него:
- правният въпрос трябва да е формулиран ясно и конкретно, да е материалноправен или процесуалноправен
  (не фактически, не за правилността на решението) и да е ОБУСЛАВЯЩ – въззивният съд да се е произнесъл
  по него и изводът му да е определил изхода на делото;
- общ, абстрактен или хипотетичен въпрос, или въпрос, който предпоставя отговора, не се допуска; ВКС може
  да уточни въпроса, но не и да го формулира вместо касатора;
- за т. 1 на чл. 280, ал. 1 – посочена конкретна практика на ВКС (задължителна или по чл. 290), на която
  въззивното решение противоречи, и в какво точно е противоречието;
- за т. 2 – противоречие с актове на Конституционния съд или Съда на ЕС;
- за т. 3 – обосновано защо въпросът е от значение за точното прилагане на закона и развитието на правото
  (непълна, неясна или противоречива норма, или нужда от промяна на практиката) – не е достатъчно да
  се цитира текстът;
- чл. 280, ал. 2 – вероятна нищожност, недопустимост или очевидна неправилност – само ако личат от
  решението;
- оплакванията за неправилност (чл. 281, т. 3) не са основание за допускане.
Провери и праговете и изключенията на чл. 280, ал. 3 ГПК, ако личат от данните.

Бъди конкретен: посочвай въпроса (Q1, Q2…) и мястото в текста. Без измислени факти и решения. Ако
изложението е силно, кажи го. Пиши на български, кратко и ясно.

ПОЛЕТА:
- overall: "силно", "средно" или "слабо" – вероятност да бъде допуснато обжалването.
- summary: 2–4 изречения обща преценка.
- questions: за всеки правен въпрос: question_id, verdict ("вероятно се допуска", "под въпрос",
  "вероятно не се допуска"), reasons (защо – по стандартите горе), fix (как да се преформулира или
  допълни; конкретна по-добра формулировка, ако може).
- issues: други слабости (в изложението или жалбата): severity ("критично", "важно", "дребно"),
  where ("изложение" или "жалба"), problem, fix.
"""

JUDGE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["overall", "summary", "questions", "issues"],
    "properties": {
        "overall": {"type": "string", "enum": ["силно", "средно", "слабо"]},
        "summary": {"type": "string"},
        "questions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["question_id", "verdict", "reasons", "fix"],
            "properties": {
                "question_id": {"type": "string"},
                "verdict": {"type": "string", "enum": ["вероятно се допуска", "под въпрос", "вероятно не се допуска"]},
                "reasons": {"type": "string"}, "fix": {"type": "string"},
            }}},
        "issues": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["severity", "where", "problem", "fix"],
            "properties": {
                "severity": {"type": "string", "enum": ["критично", "важно", "дребно"]},
                "where": {"type": "string", "enum": ["изложение", "жалба"]},
                "problem": {"type": "string"}, "fix": {"type": "string"},
            }}},
    },
}


def judge_prompt(run: dict, decision: str, statement: str, appeal: str, admission_notes: str = "") -> str:
    qs = "\n".join(f"{q['id']}. {q['text']}" for q in run.get("analysis", {}).get("questions", []))
    return (f"{JUDGE_INSTRUCTIONS}\n\n=== ПРАВНИТЕ ВЪПРОСИ ОТ СПРАВКАТА ===\n{qs}\n\n"
            f"=== ИЗЛОЖЕНИЕ ПО ЧЛ. 284, АЛ. 3, Т. 1 ГПК ===\n{statement}\n\n"
            f"=== КАСАЦИОННА ЖАЛБА ===\n{appeal or '(още няма)'}\n\n"
            + (f"=== КАК ВКС Е РЕШАВАЛ СХОДНИ ВЪПРОСИ (по думи, от базата) ===\n{admission_notes}\n\n"
               if admission_notes else "")
            + f"=== ВЪЗЗИВНО РЕШЕНИЕ ===\n{decision}")


def review(ai, run: dict, decision: str, statement: str, appeal: str, admission_notes: str = "") -> dict:
    raw = ai.structured(model=ai.config.analysis_model, system=P.SYSTEM_BASE,
                        user=judge_prompt(run, decision, statement, appeal, admission_notes),
                        schema_name="vks_judge", schema=JUDGE_SCHEMA)
    return {**raw, "version": JUDGE_VERSION, "model": ai.config.analysis_model,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "with_appeal": bool(appeal.strip()),
            "usage": {"calls": ai.usage.calls, "input_tokens": ai.usage.input_tokens,
                      "output_tokens": ai.usage.output_tokens}}
