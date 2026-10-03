"""Other filings written with the same engine as the cassation appeal (cassation/appeal.py):
answer to a cassation appeal (чл. 287 ГПК), appeal against the first-instance decision
(чл. 258–260 ГПК) and private appeal against a ruling (чл. 274–278 ГПК).

Same style, accuracy rules, quote checks and case documents as the appeal; only the task, the
attacked act and the fixed header and request differ.
"""

from __future__ import annotations

from legal_ai.cassation import prompts as P
from legal_ai.cassation.appeal import UNVERIFIED, _request, contra_quotes  # noqa: F401 - re-exported for pages
from legal_ai.cassation.draft import Block, _court_short, _party

_SHARED = P.APPEAL_INSTRUCTIONS.split("\n\nПОДРОБНОСТ.", 1)[1]
_NOTE = ("\n\nЗАБЕЛЕЖКА: указанията по-долу са написани за касационна жалба; приложи ги към обжалвания акт и към "
         "вида на документа по-горе (напр. вместо „въззивният съд“ – съдът, постановил обжалвания акт).")

FILINGS = {
    "otgovor": {
        "title": "Отговор на касационна жалба (чл. 287 ГПК)",
        "short": "Отговор по чл. 287",
        "stance": "подкрепя",
        "decision_title": "ВЪЗЗИВНО РЕШЕНИЕ (защитаваме го)",
        "needs": None,
        "task": """Напиши отговор по чл. 287, ал. 1 ГПК на касационната жалба на насрещната страна (тя е в документите
по делото – касационна жалба и изложение по чл. 284, ал. 3, т. 1) в полза на страната, която адвокатът
представлява – ответник по касационната жалба, който защитава въззивното решение.
Направи две неща, по ред: (1) защо касационно обжалване НЕ следва да се допуска – за всеки въпрос на
касатора: не е обуславящ, общ/абстрактен, фактически, няма противоречие с посочената практика, т. 3 не е
обоснована, няма вероятна нищожност/недопустимост/очевидна неправилност (ТР № 1/2010 на ОСГТК); (2) защо
въззивното решение е правилно – обори всяко оплакване на касатора с фактите, доказателствата и закона.
Решенията на ВКС в списъка подкрепят въззивния съд – ползвай ги. Ако касационната жалба липсва в
документите, напиши това в intro и работи по изводите на въззивния съд.""",
    },
    "vazzivna": {
        "title": "Въззивна жалба (чл. 258–260 ГПК)",
        "short": "Въззивна жалба",
        "stance": "противоречи",
        "decision_title": "ПЪРВОИНСТАНЦИОННО РЕШЕНИЕ (обжалваният акт)",
        "needs": "първоинстанционно решение",
        "task": """Напиши въззивна жалба по чл. 258–260 ГПК срещу ПЪРВОИНСТАНЦИОННОТО решение по-долу, в полза на
страната, която адвокатът представлява. Оплакванията са за неправилност: нарушение на материалния закон,
съществени нарушения на съдопроизводствените правила и необоснованост (чл. 260, т. 3 ГПК) – подробно по
всеки извод на първоинстанционния съд. Ако от документите личат доказателства, които съдът е отказал да
допусне или не е обсъдил, добави в closing доказателствените искания по чл. 260, т. 5 и чл. 266 ГПК
(или [да се допълни: доказателствени искания]). Обръщението е „УВАЖАЕМИ ВЪЗЗИВНИ СЪДИИ“ – пиши intro
след него.""",
    },
    "chastna": {
        "title": "Частна жалба (чл. 274–278 ГПК)",
        "short": "Частна жалба",
        "stance": "противоречи",
        "decision_title": "ОБЖАЛВАНОТО ОПРЕДЕЛЕНИЕ",
        "needs": "определение",
        "task": """Напиши частна жалба по чл. 274–278 ГПК срещу определението по-долу, в полза на страната, която
адвокатът представлява: защо определението е неправилно (процесуалният закон и фактите по делото), какво
следва да постанови горният съд. Ако определението е на въззивен съд и се обжалва пред ВКС (чл. 274,
ал. 3 ГПК), добави и основанията за допускане по чл. 280, ал. 1 и 2. Обръщението е „УВАЖАЕМИ СЪДИИ“.""",
    },
}


def instructions(kind: str) -> str:
    f = FILINGS[kind]
    return f["task"] + _NOTE + "\n\nПОДРОБНОСТ." + _SHARED


def first_instance(run: dict) -> dict:
    for inst in run.get("path") or []:
        if inst.get("level") == "първа":
            acts = [a for a in inst.get("acts", []) if a.get("type") == "Решение"]
            return {"court": inst.get("court") or "[първоинстанционния съд]",
                    "case": inst.get("case") or "…/…", "date": acts[0].get("date", "") if acts else ""}
    return {"court": "[първоинстанционния съд]", "case": "…/…", "date": ""}


def build_filing(kind: str, run: dict, filing: dict, case: dict | None = None, act_number: str = "") -> list[Block]:
    case = case or {}
    court, case_ref, act_date = _court_short(run["appellate"]["label"])
    lawyer = case.get("lawyer") or "[име]"
    party = f"От {_party(case)} – чрез адв. {lawyer}, съдебен адрес: {case.get('lawyer_address') or '[адрес]'},"
    opponent = case.get("opponent") or "[насрещна страна, ЕГН/ЕИК, адрес]"
    out = [Block("note", f"[ЧЕРНОВА – {FILINGS[kind]['title']}, написана с AI. Проверете всеки довод и цитат преди "
                         "подаване. Текстът в квадратни скоби се попълва или премахва.]")]
    if kind == "otgovor":
        out += [Block("heading", "ДО ВЪРХОВНИЯ КАСАЦИОНЕН СЪД"), Block("heading", f"ЧРЕЗ {court.upper()}"),
                Block("heading", case_ref.upper().replace(" Г.", " г.")),
                Block("center", "ОТГОВОР"), Block("center", "по чл. 287, ал. 1 ГПК"),
                Block("p", party),
                Block("p", f"по касационната жалба на {opponent} срещу Решение № {act_number or '[номер]'}/{act_date} г., "
                           f"постановено по {case_ref} по описа на {court}."),
                Block("heading", "УВАЖАЕМИ ВЪРХОВНИ СЪДИИ,")]
    elif kind == "vazzivna":
        fi = first_instance(run)
        out += [Block("heading", f"ДО {court.upper()}"), Block("heading", f"ЧРЕЗ {fi['court'].upper()}"),
                Block("heading", f"ДЕЛО № {fi['case']} г."), Block("center", "ВЪЗЗИВНА ЖАЛБА"),
                Block("p", party), Block("p", f"срещу {opponent},"),
                Block("p", f"против Решение № [номер]/{fi['date'] or '[дата]'} г., постановено по дело № {fi['case']} г. "
                           f"по описа на {fi['court']}."),
                Block("heading", "УВАЖАЕМИ ВЪЗЗИВНИ СЪДИИ,")]
    else:
        out += [Block("heading", "ДО [ГОРНИЯ СЪД]"), Block("heading", f"ЧРЕЗ {court.upper()}"),
                Block("heading", case_ref.upper().replace(" Г.", " г.")), Block("center", "ЧАСТНА ЖАЛБА"),
                Block("p", party), Block("p", "против Определение № [номер]/[дата] г. по горното дело."),
                Block("heading", "УВАЖАЕМИ СЪДИИ,")]
    if filing.get("intro"):
        out.append(Block("p", filing["intro"].strip()))
    roman = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII"]
    for i, g in enumerate(filing.get("grounds", [])):
        paras = g.get("paragraphs") or []
        if paras:
            out.append(Block("p", f"{roman[i] if i < len(roman) else i + 1}. {paras[0]}"))
            out += [Block("p", par) for par in paras[1:]]
    if filing.get("closing"):
        out.append(Block("p", filing["closing"].strip()))
    if kind == "otgovor":
        out.append(Block("p", "Моля да не допускате касационно обжалване на въззивното решение, а при допускане – да го "
                              "оставите в сила като правилно. Моля да ми присъдите направените разноски, вкл. адвокатско "
                              "възнаграждение. [Списък по чл. 80 ГПК]"))
        attachments = ["Пълномощно;", "Препис от отговора за насрещната страна."]
    elif kind == "vazzivna":
        out.append(Block("p", "Моля да отмените обжалваното решение и " + _request(filing.get("petitum_request", "")) + "."))
        out.append(Block("p", "Моля да ми присъдите направените разноски пред двете инстанции. [Списък по чл. 80 ГПК]"))
        attachments = ["Документ за платена държавна такса [сума];", "Пълномощно;", "Препис от жалбата за насрещната страна."]
    else:
        out.append(Block("p", "Моля да отмените обжалваното определение и " + _request(filing.get("petitum_request", "")) + "."))
        attachments = ["Документ за платена държавна такса [сума];", "Препис от жалбата за насрещната страна."]
    out.append(Block("heading", "Прилагам:"))
    out += [Block("item", f"{k}. {t}") for k, t in enumerate(attachments, 1)]
    out += [Block("p", "С уважение: ......................................"), Block("p", f"(адв. {lawyer})")]
    return out
