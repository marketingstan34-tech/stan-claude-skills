"""Filings started without a report: искова молба, отговор на искова молба, въззивна жалба,
отговор на въззивна жалба and частна жалба.

The lawyer uploads the case documents and writes what the client wants. Two AI calls: a light one
lists the legal issues with search words, the VKS practice for them is taken from the own
database (art. 290 and interpretative decisions, with the passages that match), and the main
model writes the filing. As with the cassation appeal, every quotation („…“, 40+ characters) is
checked verbatim against the documents and the practice passages, and VKS decisions may be cited
only from the list given. Kept in storage/pleadings/<id>/ (private storage, never in Git).
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path

from legal_ai.cassation import prompts as P
from legal_ai.cassation.appeal import mark_quotes
from legal_ai.cassation.draft import Block, _party

VERSION = "pleading-1"
_ID = re.compile(r"^\d{14}$")
MAX_DOCS = 60
DOC_EACH = 60_000
DOC_TOTAL = 160_000
PRACTICE_ACTS = 12
PASSAGE_CHARS = 1500

FIELDS = {   # name -> max length
    "title": 160, "court": 160, "upper_court": 160, "case_ref": 80, "client": 200, "client_id": 40,
    "client_address": 300, "opponent": 300, "lawyer": 120, "lawyer_address": 300, "notes": 8000,
}

KINDS = {
    "iskova": {
        "title": "Искова молба (чл. 127–128 ГПК)", "short": "Искова молба", "main": None,
        "hint": "Качете договорите, кореспонденцията и другите доказателства. В бележките опишете фактите и "
                "какво иска клиентът (сума, вещ, установяване).",
        "task": """Напиши искова молба по чл. 127 и чл. 128 ГПК в полза на клиента на адвоката (ищец). Изложи
обстоятелствата, на които се основава искът, подробно и хронологично, с всички факти от документите
(дати, номера, суми, площи, страни); правното основание (закон, член, алинея) и защо фактите го
изпълват; при няколко иска – всеки отделно, и при нужда евентуален иск. В petitum – точно какво да
постанови съдът (осъдителен, установителен или конститутивен диспозитив, с пълната индивидуализация:
сума с лихвите от коя дата до изплащането, вещ, имот). В claim_value – цената на иска по чл. 69 ГПК
(или [да се допълни: цена на иска]). В evidence – писмените доказателства от документите и
доказателствените искания (свидетели – за кои факти, експертизи – задачите, чл. 190 и чл. 192 ГПК).""",
    },
    "otgovor_iskova": {
        "title": "Отговор на искова молба (чл. 131 ГПК)", "short": "Отговор на искова молба",
        "main": "искова молба",
        "hint": "Качете исковата молба на насрещната страна с приложенията ѝ и доказателствата на клиента. "
                "В бележките опишете позицията на клиента.",
        "task": """Напиши отговор по чл. 131 ГПК на исковата молба на насрещната страна (тя е ОСНОВНИЯТ ДОКУМЕНТ) в
полза на клиента на адвоката (ответник). Включи, по ред: становище по допустимостта на иска (липса на
правен интерес, недопустимост, неподсъдност – само ако личат); становище по основателността – по
всяко обстоятелство от исковата молба: признава ли се или се оспорва и защо; всички възражения,
които личат от документите (давност, плащане, прихващане, нищожност или унищожаемост, неизпълнен
договор, изтекъл срок и др.), с фактите им; оспорване на представени документи, ако има основание.
Внимание: възраженията и доказателствата, непосочени в отговора, по правило се преклудират
(чл. 133 ГПК) – затова изброй всичко, което личи, и отбележи с [да се провери: …] онова, което зависи от
факти извън документите. В petitum – какво да постанови съдът (напр. да отхвърли иска изцяло). В
evidence – доказателствата и доказателствените искания на ответника.""",
    },
    "vazzivna": {
        "title": "Въззивна жалба (чл. 258–260 ГПК)", "short": "Въззивна жалба", "main": "решение",
        "hint": "Качете обжалваното първоинстанционно решение, исковата молба, отговора, протоколите и "
                "доказателствата. В бележките – кое не е наред според клиента.",
        "task": """Напиши въззивна жалба по чл. 258–260 ГПК срещу първоинстанционното решение (ОСНОВНИЯТ
ДОКУМЕНТ) в полза на клиента на адвоката. Оплакванията са за неправилност: нарушение на материалния
закон, съществени нарушения на съдопроизводствените правила и необоснованост – подробно по всеки
извод на съда, с дословни цитати от решението и с доказателствата по делото. Ако съдът е отказал да
допусне или не е обсъдил доказателства, посочи доказателствените искания по чл. 260, т. 5 и чл. 266
ГПК в evidence. В petitum – какво да постанови въззивният съд след отмяната.""",
    },
    "otgovor_vazzivna": {
        "title": "Отговор на въззивна жалба (чл. 263 ГПК)", "short": "Отговор на въззивна жалба",
        "main": "въззивна жалба",
        "hint": "Качете въззивната жалба на насрещната страна и първоинстанционното решение (защитаваме го), "
                "както и другите документи по делото.",
        "task": """Напиши отговор по чл. 263, ал. 1 ГПК на въззивната жалба на насрещната страна (тя е ОСНОВНИЯТ
ДОКУМЕНТ) в полза на клиента на адвоката, който защитава първоинстанционното решение. Обори всяко
оплакване на жалбоподателя поотделно с изводите на първоинстанционния съд (дословни цитати от
решението), с доказателствата и закона; посочи кои оплаквания са общи и не подлежат на проверка
(чл. 269 ГПК); ако жалбата иска нови доказателства – защо не са допустими по чл. 266 ГПК. В petitum –
какво да постанови въззивният съд (да остави в сила решението).""",
    },
    "chastna": {
        "title": "Частна жалба (чл. 274–278 ГПК)", "short": "Частна жалба", "main": "определение",
        "hint": "Качете обжалваното определение и документите, по които е постановено.",
        "task": """Напиши частна жалба по чл. 274–278 ГПК срещу определението (ОСНОВНИЯТ ДОКУМЕНТ) в полза на
клиента на адвоката: защо определението е неправилно (процесуалният закон и фактите по делото) и какво
следва да постанови горният съд (petitum).""",
    },
}

_RULES = """
ТОЧНОСТ (задължително):
- Факти, имена, номера, дати, площи и суми – само от документите и бележките. Нищо измислено. Ако нещо
  липсва, напиши [да се допълни: …].
- Дословни цитати „…“ – само текст, който стои дословно в документите или в откъсите от практиката.
- Решения на ВКС и ТР – само от „ПРАКТИКА НА ВКС“ по-долу (етикетът дословно във vks_labels) или
  цитираните в документите, със същите номер, дата и дело. Не измисляй решения. Цитирай практика само
  когато наистина подкрепя довода.
- Текст на закон в кавички – само ако е дословно в документите; иначе предай съдържанието без кавички
  и добави [проверете текста на чл. …].
- От ОБРАЗЕЦА НА СТИЛА и АРХИВА не взимай факти, имена и номера – само стила и правните доводи.

СТИЛ: обстойно и подробно, както пише опитен адвокат – от първо лице от името на доверителя, свързан
текст в абзаци, убедително, без повтаряне на едни и същи формули. Ако има ОБРАЗЕЦ НА СТИЛА – следвай го.

ПОЛЕТА:
1. intro: встъпителен абзац след обръщението.
2. sections: 2–8 части (title – кратко заглавие без номер; paragraphs – абзаците; vks_labels – ползваните
   решения от списъка).
3. petitum: какво да постанови съдът, като продължение на „Моля …“ (започва с „да“, напр. „да осъдите
   ответника да заплати…“), с пълната индивидуализация.
4. claim_value: цената на иска (само за искова молба; иначе празен низ).
5. evidence: доказателствата и доказателствените искания, по едно на елемент (празен списък, ако няма).
6. closing: заключителен абзац.
"""

PLEADING_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["intro", "sections", "petitum", "claim_value", "evidence", "closing"],
    "properties": {
        "intro": {"type": "string"},
        "sections": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["title", "paragraphs", "vks_labels"],
            "properties": {"title": {"type": "string"},
                           "paragraphs": {"type": "array", "items": {"type": "string"}},
                           "vks_labels": {"type": "array", "items": {"type": "string"}}}}},
        "petitum": {"type": "string"},
        "claim_value": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "closing": {"type": "string"},
    },
}

ISSUES_INSTRUCTIONS = """По документите и бележките по-долу посочи 2–5 правни въпроса, от които зависи спорът
(правната квалификация на иска, основанията, възраженията, процесуалните въпроси). За всеки дай 2–3
набора от 2–3 думи за търсене в решенията на ВКС: характерни за проблема думи във формата, в която се
срещат в мотивите на ВКС (напр. "неоснователно", "обогатяване", "задатък"); член – само цифрите
("59"). Без общи думи (съд, решение, иск, ГПК, ЗЗД, договор)."""

ISSUES_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["issues"],
    "properties": {"issues": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["text", "word_sets"],
        "properties": {"text": {"type": "string"},
                       "word_sets": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}}}}},
}


# ---------- storage ----------

def valid_id(pid: str) -> bool:
    return bool(_ID.match(pid or ""))


def root(storage: Path) -> Path:
    return storage / "pleadings"


def load(storage: Path, pid: str) -> dict | None:
    if not valid_id(pid):
        return None
    try:
        data = json.loads((root(storage) / pid / "pleading.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("kind") in KINDS else None


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def clean_form(form: dict) -> dict:
    return {k: (form.get(k) or "").strip()[:n] for k, n in FIELDS.items()}


def create(storage: Path, kind: str, form: dict) -> str:
    if kind not in KINDS:
        raise ValueError("Непознат вид документ.")
    base = datetime.now(timezone.utc)
    pid = base.strftime("%Y%m%d%H%M%S")
    while (root(storage) / pid).exists():   # two in the same second
        base = base.replace(second=(base.second + 1) % 60)
        pid = base.strftime("%Y%m%d%H%M%S")
    data = {"id": pid, "kind": kind, "created_at": datetime.now(timezone.utc).isoformat(), **clean_form(form)}
    _write(root(storage) / pid / "pleading.json", data)
    return pid


def update(storage: Path, pid: str, form: dict) -> None:
    data = load(storage, pid)
    if data is None:
        raise ValueError("Няма такъв документ.")
    data.update(clean_form(form))
    _write(root(storage) / pid / "pleading.json", data)


def all_items(storage: Path) -> list[dict]:
    out = []
    base = root(storage)
    if base.exists():
        for d in sorted(base.iterdir(), reverse=True):
            item = load(storage, d.name)
            if item:
                item["has_draft"] = (d / "draft.json").exists()
                out.append(item)
    return out


def docs(storage: Path, pid: str):
    from legal_ai.web.casedocs import FileList
    return FileList(root(storage) / pid / "docs.json", storage / "uploads", MAX_DOCS)


def load_draft(storage: Path, pid: str) -> dict | None:
    try:
        return json.loads((root(storage) / pid / "draft.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_draft(storage: Path, pid: str, draft: dict) -> None:
    _write(root(storage) / pid / "draft.json", draft)


# ---------- practice from the own database ----------

def _stems(words: list[str]) -> list[str]:
    return [w.lower()[:6] for w in words if len(w) >= 4]


def best_passages(paragraphs: list[str], words: list[str], limit: int = 2) -> list[str]:
    """The paragraphs of a decision that contain most of the search words (by their first 6 letters)."""
    stems = _stems(words)
    scored = []
    for i, p in enumerate(paragraphs):
        low = p.lower()
        hits = sum(1 for s in stems if s in low)
        if hits and len(p) > 120:
            scored.append((hits, -i, p))
    scored.sort(reverse=True)
    return [p[:PASSAGE_CHARS] for _, _, p in scored[:limit]]


def find_practice(conn, issues: list[dict], cutoff: date) -> list[dict]:
    """[{label, url, passages}] from the own database for the issues (art. 290 and interpretative)."""
    from legal_ai.cassation.local import key_words, load_act, local_broad, local_candidates
    if conn is None:
        return []
    score: dict[str, int] = {}
    words_for: dict[str, list[str]] = {}
    for issue in issues:
        sets = [ws for ws in issue.get("word_sets", []) if ws]
        for did, e in local_candidates(conn, sets, cutoff, per_set=4).items():
            score[did] = score.get(did, 0) + 2 * len(e["hits"])
            words_for.setdefault(did, []).extend(w for ws in e["hits"] for w in ws)
        broad = key_words(issue.get("text", ""))
        for did in local_broad(conn, broad, cutoff, per_set=3):
            score[did] = score.get(did, 0) + 1
            words_for.setdefault(did, []).extend(broad)
    out = []
    for did in sorted(score, key=lambda d: -score[d])[:PRACTICE_ACTS]:
        act = load_act(conn, did)
        if act is None:
            continue
        passages = best_passages([p.text for p in act.paragraphs], words_for.get(did, []))
        if passages:
            out.append({"label": act.label, "url": act.url, "passages": passages})
    return out


# ---------- the AI part ----------

def _doc_block(docs_list: list[tuple[str, str]], header: str) -> str:
    return P.context_block(docs_list, DOC_EACH, DOC_TOTAL, header) if docs_list else ""


def prompt(kind: str, item: dict, main: tuple[str, str] | None, other_docs: list[tuple[str, str]],
           practice: list[dict], style: list[tuple[str, str]], archive: list[tuple[str, str]]) -> str:
    spec = KINDS[kind]
    parts = [spec["task"], _RULES]
    if item.get("notes"):
        parts.append(f"=== БЕЛЕЖКИ НА АДВОКАТА (позицията и целта на клиента; не цитирай от тях) ===\n{item['notes']}")
    if main:
        parts.append(f"=== ОСНОВНИЯТ ДОКУМЕНТ: {main[0]} ===\n{main[1][:DOC_EACH]}")
    prac = "\n\n".join(f"- {p['label']}\n" + "\n".join(f"  Откъс: „{t}“" for t in p["passages"]) for p in practice)
    parts.append(f"=== ПРАКТИКА НА ВКС (от базата; цитирай само ако подкрепя довода) ===\n{prac or '(няма намерена)'}")
    text = "\n\n".join(parts)
    text += _doc_block(other_docs, P.CONTEXT_HEADER.replace("не е въззивното решение", "документ по делото"))
    text += P.context_block(style, P.STYLE_EACH, P.STYLE_TOTAL, P.STYLE_HEADER) if style else ""
    text += P.context_block(archive, P.STYLE_EACH, P.STYLE_TOTAL, P.ARCHIVE_HEADER) if archive else ""
    return text


def split_docs(kind: str, docs_read: list[dict]) -> tuple[tuple[str, str] | None, list[tuple[str, str]]]:
    """(main document, the other documents) from the case folder, by the kind of the filing."""
    want = KINDS[kind]["main"]
    named = [(f"{x['kind']} – {x['filename']}", x["text"], x) for x in docs_read]
    main = None
    if want:
        cands = [n for n in named if n[2]["kind"] == want]
        if cands:   # the latest of that kind
            main = sorted(cands, key=lambda n: n[2].get("date") or "")[-1]
    others = [(n, t) for n, t, x in named if main is None or x is not main[2]]
    return ((main[0], main[1]) if main else None), others


def generate(ai, kind: str, item: dict, docs_read: list[dict], conn, style=None, archive=None,
             today: date | None = None, progress=lambda msg: None) -> dict:
    spec = KINDS[kind]
    main, others = split_docs(kind, docs_read)
    if spec["main"] and main is None:
        raise ValueError(f"Няма {spec['main']} в документите – качете я/го, за да се пише {spec['short'].lower()}.")
    cfg = ai.config
    topic = (item.get("notes", "") + "\n\n" + (main[1] if main else "\n".join(t for _, t in others)))[:40_000]
    progress("AI определя правните въпроси за търсене на практика…")
    issues = ai.structured(model=cfg.light_model, system=P.SYSTEM_BASE,
                           user=f"{ISSUES_INSTRUCTIONS}\n\n{topic}", schema_name="pleading_issues",
                           schema=ISSUES_SCHEMA, effort=cfg.assess_effort).get("issues", [])
    progress("Търсене на практика на ВКС в базата…")
    practice = find_practice(conn, issues, today or date.today())
    progress(f"AI пише: {spec['title']} (обикновено 3–8 минути)…")
    raw = ai.structured(model=cfg.analysis_model, system=P.SYSTEM_BASE,
                        user=prompt(kind, item, main, others, practice, style or [], archive or []),
                        schema_name="pleading", schema=PLEADING_SCHEMA)
    allowed = {p["label"] for p in practice}
    sources = [t for _, t in others] + ([main[1]] if main else []) + [t for p in practice for t in p["passages"]]
    unverified = 0

    def checked(par: str) -> str:
        nonlocal unverified
        out, n = mark_quotes(par.strip(), sources)
        unverified += n
        return out
    sections = [{"title": s["title"].strip(), "paragraphs": [checked(p) for p in s["paragraphs"] if p.strip()],
                 "vks_labels": [x for x in s["vks_labels"] if x in allowed],
                 "dropped_labels": [x for x in s["vks_labels"] if x not in allowed]} for s in raw["sections"]]
    return {"kind": kind, "intro": checked(raw["intro"]), "sections": sections,
            "petitum": raw["petitum"].strip(), "claim_value": raw["claim_value"].strip(),
            "evidence": [e.strip() for e in raw["evidence"] if e.strip()], "closing": checked(raw["closing"]),
            "issues": [i.get("text", "") for i in issues], "practice": practice,
            "main_doc": main[0] if main else "", "doc_names": [n for n, _ in others],
            "unverified_quotes": unverified, "version": VERSION, "model": cfg.analysis_model,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "usage": {"calls": ai.usage.calls, "input_tokens": ai.usage.input_tokens,
                      "output_tokens": ai.usage.output_tokens, "by_model": ai.usage.by_model}}


# ---------- the document ----------

def _please(text: str) -> str:
    t = (text or "").strip().rstrip(".")
    t = re.sub(r"^(?:моля\s+)", "", t, flags=re.IGNORECASE)
    return t or "[посочете какво да постанови съдът]"


def build(item: dict, draft: dict) -> list[Block]:
    kind = item["kind"]
    lawyer = item.get("lawyer") or "[име]"
    court = item.get("court") or "[СЪД]"
    upper = item.get("upper_court") or "[ГОРЕН СЪД]"
    case_ref = item.get("case_ref") or "…/…"
    opponent = item.get("opponent") or "[насрещна страна, ЕГН/ЕИК, адрес]"
    party = f"От {_party(item)} – чрез адв. {lawyer}, съдебен адрес: {item.get('lawyer_address') or '[адрес]'},"
    out = [Block("note", f"[ЧЕРНОВА – {KINDS[kind]['title']}, написана с AI. Проверете всеки довод, цитат и "
                         "решение преди подаване. Текстът в квадратни скоби се попълва или премахва.]")]
    if kind == "iskova":
        out += [Block("heading", f"ДО {court.upper()}"), Block("center", "ИСКОВА МОЛБА"),
                Block("p", party), Block("p", f"срещу {opponent}"),
                Block("p", f"Цена на иска: {draft.get('claim_value') or '[да се допълни]'}"),
                Block("heading", "УВАЖАЕМИ ГОСПОДИН/ГОСПОЖО ПРЕДСЕДАТЕЛ,")]
    elif kind == "otgovor_iskova":
        out += [Block("heading", f"ДО {court.upper()}"), Block("heading", f"ПО ДЕЛО № {case_ref} г."),
                Block("center", "ОТГОВОР"), Block("center", "по чл. 131 ГПК"),
                Block("p", party.replace(" – чрез", " – ответник, чрез", 1)),
                Block("p", f"по исковата молба на {opponent}"),
                Block("heading", "УВАЖАЕМИ ГОСПОДИН/ГОСПОЖО СЪДИЯ,")]
    elif kind in ("vazzivna", "otgovor_vazzivna"):
        out += [Block("heading", f"ДО {upper.upper()}"), Block("heading", f"ЧРЕЗ {court.upper()}"),
                Block("heading", f"ПО ДЕЛО № {case_ref} г.")]
        if kind == "vazzivna":
            out += [Block("center", "ВЪЗЗИВНА ЖАЛБА"), Block("p", party), Block("p", f"срещу {opponent},"),
                    Block("p", f"против Решение № [номер]/[дата] г., постановено по дело № {case_ref} г. по описа "
                               f"на {court}.")]
        else:
            out += [Block("center", "ОТГОВОР"), Block("center", "по чл. 263, ал. 1 ГПК"), Block("p", party),
                    Block("p", f"по въззивната жалба на {opponent} срещу Решение № [номер]/[дата] г., постановено "
                               f"по дело № {case_ref} г. по описа на {court}.")]
        out.append(Block("heading", "УВАЖАЕМИ ВЪЗЗИВНИ СЪДИИ,"))
    else:
        out += [Block("heading", f"ДО {upper.upper()}"), Block("heading", f"ЧРЕЗ {court.upper()}"),
                Block("heading", f"ПО ДЕЛО № {case_ref} г."), Block("center", "ЧАСТНА ЖАЛБА"), Block("p", party),
                Block("p", f"против Определение № [номер]/[дата] г., постановено по дело № {case_ref} г. "
                           f"по описа на {court}."),
                Block("heading", "УВАЖАЕМИ СЪДИИ,")]
    if draft.get("intro"):
        out.append(Block("p", draft["intro"]))
    roman = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]
    for i, s in enumerate(draft.get("sections", [])):
        num = roman[i] if i < len(roman) else str(i + 1)
        out.append(Block("heading", f"{num}. {s['title'].rstrip('.')}"))
        out += [Block("p", par) for par in s.get("paragraphs", [])]
    if draft.get("closing"):
        out.append(Block("p", draft["closing"]))
    petitum = _please(draft.get("petitum", ""))
    if kind == "vazzivna":
        out.append(Block("p", f"Моля да отмените обжалваното решение и {petitum}."))
    elif kind == "chastna":
        out.append(Block("p", f"Моля да отмените обжалваното определение и {petitum}."))
    else:
        out.append(Block("p", f"Моля {petitum}."))
    if draft.get("evidence"):
        out.append(Block("heading", "Доказателства и доказателствени искания:"))
        out += [Block("item", f"{k}. {e}") for k, e in enumerate(draft["evidence"], 1)]
    if kind == "otgovor_iskova":
        out.append(Block("note", "[Възраженията и доказателствата, непосочени в отговора, по правило се преклудират "
                                 "(чл. 133 ГПК) – проверете дали всичко е включено.]"))
    if kind != "chastna":
        out.append(Block("p", "Моля да ми присъдите направените разноски, вкл. адвокатско възнаграждение. "
                              "[Списък по чл. 80 ГПК]"))
    attachments = {
        "iskova": ["Документ за платена държавна такса [сума];", "Пълномощно;", "Писмените доказателства по описа;",
                   "Преписи от исковата молба и приложенията за ответника."],
        "otgovor_iskova": ["Пълномощно;", "Писмените доказателства по описа;",
                           "Преписи от отговора и приложенията за ищеца."],
        "vazzivna": ["Документ за платена държавна такса [сума];", "Пълномощно;",
                     "Препис от жалбата за насрещната страна."],
        "otgovor_vazzivna": ["Пълномощно;", "Препис от отговора за насрещната страна."],
        "chastna": ["Документ за платена държавна такса [сума];", "Препис от жалбата за насрещната страна."],
    }[kind]
    out.append(Block("heading", "Прилагам:"))
    out += [Block("item", f"{k}. {t}") for k, t in enumerate(attachments, 1)]
    out += [Block("p", "С уважение: ......................................"), Block("p", f"(адв. {lawyer})")]
    return out
