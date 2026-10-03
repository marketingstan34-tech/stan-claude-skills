"""Draft of the statement of grounds for cassation (чл. 284, ал. 3, т. 1 ГПК) from an AI report.

Built with fixed rules from the saved report, with no further AI call, so nothing new can be
invented: questions, holdings and VKS decisions come from the report, and only quotes verified
verbatim in the source text are quoted. Everything the lawyer must fill in or check is in [square
brackets]. The layout follows the partner lawyer's own filings (heading, "УВАЖАЕМИ ВЪРХОВНИ СЪДИИ,",
"На първо място, ...", numbered questions, "Прилагам:", signature).
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from xml.sax.saxutils import escape

ORDINALS = ["първо", "второ", "трето", "четвърто", "пето", "шесто", "седмо", "осмо", "девето", "десето"]
DISCLAIMER = ("[ЧЕРНОВА, генерирана автоматично от справката. Проверете всеки въпрос, извод и цитирано "
              "решение преди подаване. Текстът в квадратни скоби се попълва или премахва.]")


@dataclass
class Block:
    kind: str      # center | heading | p | quote | item | note
    text: str


def _court_short(label: str) -> tuple[str, str, str]:
    """(court, case reference, decision date) from 'Апелативен съд Пловдив, Въззивно търговско дело № 899/2021, Решение от 14.03.2022'."""
    m = re.match(r"^(?P<court>[^,]+), (?P<kind>[^,]*?дело) № (?P<case>\d+/\d{4}), Решение от (?P<date>[\d.]+)", label)
    if not m:
        return "[въззивния съд]", "[в. дело № …/… г.]", "[дата]"
    kind = m["kind"].replace("Въззивно гражданско дело", "в. гр. дело").replace("Въззивно търговско дело", "в. т. дело")
    return m["court"], f"{kind} № {m['case']} г.", m["date"]


_PREFIX = re.compile(r"^(?:въззивният\s+съд|съдът|ВКС|върховният\s+касационен\s+съд)\s+(?:е\s+)?(?:приел|приема|посочва|сочи),?\s+че\s+",
                     re.IGNORECASE)


def _clause(sentence: str) -> str:
    """'Въззивният съд е приел, че X.' -> 'x' so it reads after '... е приел, че '."""
    s = _PREFIX.sub("", sentence.strip()).rstrip(" .")
    s = re.sub(r"^според\s+(?:въззивния\s+съд|съда)\s*,?\s*", "", s, flags=re.IGNORECASE)
    if len(s) > 1 and s[0].isupper() and s[1].islower():
        s = s[0].lower() + s[1:]
    return s


def build_draft(run: dict) -> list[Block]:
    a = run["analysis"]
    court, case_ref, act_date = _court_short(run["appellate"]["label"])
    act = f"Решение № [номер]/{act_date} г., постановено по {case_ref} по описа на {court}"
    holdings = {h["id"]: h for h in a.get("holdings", [])}
    hq = run.get("holding_quotes", {})
    contra: dict[str, list[dict]] = {}
    for x in run.get("assessments", []):
        if x.get("relevant") and x.get("stance") == "противоречи":
            contra.setdefault(x["question_id"], []).append(x)
    grounds = {"т.1"} | {q["ground"] for q in a["questions"] if not contra.get(q["id"])}
    grounds_text = " и ".join(f"чл. 280, ал. 1, {g.replace('т.', 'т. ')}" for g in sorted(grounds)) + " от ГПК"

    out = [Block("note", DISCLAIMER),
           Block("heading", "ДО ВЪРХОВНИЯ КАСАЦИОНЕН СЪД"),
           Block("heading", f"ЧРЕЗ {court.upper()}"),
           Block("heading", case_ref.upper().replace(" Г.", " г.")),
           Block("center", "ИЗЛОЖЕНИЕ НА КАСАЦИОННИ ОСНОВАНИЯ"),
           Block("center", f"По {grounds_text}"),
           Block("p", "От [име на доверителя, ЕГН/ЕИК, адрес] – чрез адв. [име], съдебен адрес: [адрес]."),
           Block("p", f"За допускане на касационно обжалване на {act}."),
           Block("heading", "УВАЖАЕМИ ВЪРХОВНИ СЪДИИ,"),
           Block("p", f"Моля да допуснете касационно обжалване на {act}."),
           Block("p", "[Кратко описание на делото – проверете:] " + a.get("case_summary", ""))]

    attached: list[str] = []
    seen: set[str] = set()
    number = 0
    for i, q in enumerate(a["questions"]):
        place = ORDINALS[i] if i < len(ORDINALS) else f"{i + 1}-о"
        found = contra.get(q["id"], [])
        lead = f"На {place} място, "
        for hid in q.get("holding_ids", []):
            h = holdings.get(hid)
            if not h:
                continue
            out.append(Block("p", f"{lead or 'Освен това '}въззивният съд е приел, че {_clause(h['summary'])}."))
            lead = ""
            quote = hq.get(hid) or {}
            if quote.get("status") == "text_verified" and quote.get("text"):
                out.append(Block("quote", f"„{quote['text']}“"))
        if lead:   # no holding attached to the question
            out.append(Block("p", f"{lead}[опишете извода на въззивния съд по този въпрос]."))
        for x in found:
            tr = x["label"].startswith("Тълкувателно")
            where = x["label"] if tr else f"{x['label']} на ВКС" + (f", {x['chamber']}" if x.get("chamber") else "")
            kind = "задължителна практика" if tr else "постановено по реда на чл. 290 ГПК"
            out.append(Block("p", f"Обратно, в {where} ({kind}) е прието, че {_clause(x['vks_rule'])}."))
            qt = x.get("quote") or {}
            if qt.get("status") == "text_verified" and qt.get("text"):
                out.append(Block("quote", f"„{qt['text']}“"))
            else:
                out.append(Block("note", "[Цитатът от това решение не е проверен дословно – добавете цитат от самото решение.]"))
            if x["label"] not in seen:
                seen.add(x["label"])
                attached.append(x["label"])
        if not found:
            if q["ground"] == "т.3":
                out.append(Block("note", "[Основание по чл. 280, ал. 1, т. 3 ГПК: обосновете значението на въпроса за "
                                         "точното прилагане на закона и за развитието на правото.]"))
            else:
                out.append(Block("note", "[Не е намерена противоречаща практика на ВКС по този въпрос – добавете "
                                         "практика или обосновете основание по т. 3, или премахнете въпроса.]"))
        number += 1
        kind = "материалноправен" if q["kind"] == "материалноправен" else "процесуалноправен"
        why = q.get("why_decisive", "").strip().rstrip(".")
        out.append(Block("p", f"Оттук възниква {kind} въпрос, обусловил изхода на делото"
                              + (f" [защо – проверете: {why}]" if why else "") + ":"))
        out.append(Block("item", f"{number}. „{q['text'].rstrip()}“"))

    out.append(Block("p", f"С оглед изложеното, моля да приемете, че са налице основанията по {grounds_text}, "
                          f"и да допуснете касационно обжалване на {act}."))
    if attached:
        out.append(Block("heading", "Прилагам:"))
        out += [Block("item", f"{k}. {label};") for k, label in enumerate(attached, 1)]
    out += [Block("p", "С уважение: ......................................"),
            Block("p", "(адв. [име])")]
    return out


def to_text(blocks: list[Block]) -> str:
    return "\n\n".join(b.text for b in blocks)


def _para(text: str, *, bold=False, center=False, italic=False, indent=False, highlight=False) -> str:
    ppr = ""
    if center or indent:
        ppr = "<w:pPr>" + ('<w:jc w:val="center"/>' if center else "") + \
              ('<w:ind w:left="567" w:right="567"/>' if indent else "") + "</w:pPr>"
    rpr = "<w:rPr>" + ("<w:b/>" if bold else "") + ("<w:i/>" if italic else "") + \
          ('<w:highlight w:val="yellow"/>' if highlight else "") + "</w:rPr>"
    return f'<w:p>{ppr}<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def to_docx(blocks: list[Block]) -> bytes:
    """A minimal Word document (Times New Roman 12), no extra dependency."""
    body = []
    for b in blocks:
        if b.kind == "heading":
            body.append(_para(b.text, bold=True))
        elif b.kind == "center":
            body.append(_para(b.text, bold=True, center=True))
        elif b.kind == "quote":
            body.append(_para(b.text, italic=True, indent=True))
        elif b.kind == "note":
            body.append(_para(b.text, highlight=True))
        else:
            body.append(_para(b.text))
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {ns}><w:body>'
                + "".join(body) + '<w:sectPr><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1418"/>'
                '</w:sectPr></w:body></w:document>')
    styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {ns}><w:docDefaults><w:rPrDefault>'
              '<w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman"/>'
              '<w:sz w:val="24"/><w:lang w:val="bg-BG"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr>'
              '<w:spacing w:after="120" w:line="300" w:lineRule="auto"/><w:jc w:val="both"/></w:pPr>'
              '</w:pPrDefault></w:docDefaults></w:styles>')
    types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
             '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
             '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                '</Relationships>')
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("word/styles.xml", styles)
    return buf.getvalue()
