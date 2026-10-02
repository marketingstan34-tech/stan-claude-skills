"""Pure parsers for VKS HTML. No network, no database.

Result list:  links `pregled-akt.jsp?type=ot-spisak&id=<32 hex>` with text
              like `Решение №129/06.03.2025 по дело №1061/2024`.
Act page:     full text inside `<div id="Content" class="AktSadarjanie">`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

import lxml.html

PARSER_VERSION = "vks-4"

_LIST_LINK = re.compile(
    r'pregled-akt\.jsp\?type=ot-spisak&(?:amp;)?id=([0-9A-F]{32})"[^>]*>([^<]*)<'
)
_LINK_TEXT = re.compile(
    r"^\s*(?P<type>[А-Яа-я]+)\s*№\s*(?P<no>\d+)\s*/\s*(?P<date>\d{2}\.\d{2}\.\d{4})"
    r"\s+по\s+дело\s*№\s*(?P<case_no>\d+)\s*/\s*(?P<case_year>\d{4})\s*$"
)


@dataclass(frozen=True)
class ListRow:
    source_id: str
    link_text: str
    act_type: str | None
    act_number: str | None
    act_date: date | None
    case_number: str | None
    case_year: int | None


def _parse_bg_date(s: str) -> date | None:
    try:
        d, m, y = s.split(".")
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def parse_list(html: str) -> list[ListRow]:
    """Rows in page order, de-duplicated by source id."""
    rows: list[ListRow] = []
    seen: set[str] = set()
    for source_id, text in _LIST_LINK.findall(html):
        if source_id in seen:
            continue
        seen.add(source_id)
        text = " ".join(text.split())
        m = _LINK_TEXT.match(text)
        if m:
            rows.append(ListRow(
                source_id=source_id,
                link_text=text,
                act_type=m["type"].lower(),
                act_number=m["no"],
                act_date=_parse_bg_date(m["date"]),
                case_number=m["case_no"],
                case_year=int(m["case_year"]),
            ))
        else:
            rows.append(ListRow(source_id, text, None, None, None, None, None))
    return rows


# --- act page ---------------------------------------------------------------

_BLOCK_TAGS = {"div", "p", "table", "tr", "li", "ul", "ol", "h1", "h2", "h3", "h4", "center"}

_CHAMBER = re.compile(
    r"(Първо|Второ|Трето|Четвърто|Пето)\s+(гражданско|търговско|наказателно)\s+отделение",
    re.IGNORECASE,
)
_ORDINALS = {
    "първо": "Първо", "второ": "Второ", "трето": "Трето", "четвърто": "Четвърто", "пето": "Пето",
    "i": "Първо", "ii": "Второ", "iii": "Трето", "iv": "Четвърто", "v": "Пето",
}
# "Гражданска колегия, Първо отделение", "първо отделение на Гражданска колегия", "I-во отделение"
_ORDINAL_CHAMBER = re.compile(
    r"\b(първо|второ|трето|четвърто|пето|iv|v|i{1,3})(?:\s*-\s*(?:во|ро|то))?\s+отделение",
    re.IGNORECASE,
)
_COLLEGE = re.compile(r"(граждан|търгов|наказат)\w*\s+колегия", re.IGNORECASE)
_COLLEGE_ADJ = {"граждан": "гражданско", "търгов": "търговско", "наказат": "наказателно"}
_PROCEEDING = re.compile(
    r"Производството\s+е\s+по\s+(?:реда\s+на\s+)?чл\.\s*(\d+[а-я]?)", re.IGNORECASE
)
_ADMISSION_HINT = re.compile(
    r"допус(?:нато|ка|нал|кане)[^.]{0,40}касационно\s+обжалване"
    r"|касационно(?:то)?\s+обжалване[^.]{0,60}?\s(?:е\s+)?допуснато",
    re.IGNORECASE,
)
_ART_280 = re.compile(r"чл\.\s*280\b", re.IGNORECASE)
# "ал. 1, т. 1", "ал.2, предл.2", "ал. 2, пр. 3" following a "чл. 280" mention
_ART_280_PART = re.compile(
    r"ал\.\s*(\d)(?:\s*,?\s*(?:т\.\s*(\d)|пр(?:едл)?\.\s*(\d|първо|второ|трето|последно)))?",
    re.IGNORECASE,
)
# Art. 280(2) GPK has three alternatives ("предложения"); "последно" is the third.
_PROPOSAL_WORDS = {"първо": "1", "второ": "2", "трето": "3", "последно": "3"}
# Grounds of art. 280(2) GPK stated in words, in the order of the provision.
_ART_280_2_WORDS = (
    (re.compile(r"вероятна\s+нищожност", re.IGNORECASE), "чл. 280, ал. 2, предл. 1"),
    (re.compile(r"вероятна\s+(?:нищожност\s+или\s+)?недопустимост", re.IGNORECASE),
     "чл. 280, ал. 2, предл. 2"),
    (re.compile(r"очевидн\w*\s+неправилност|очевидно\s+неправилн", re.IGNORECASE),
     "чл. 280, ал. 2, предл. 3"),
)
ADMISSION_WINDOW = 2  # grounds are often stated in the paragraph(s) after the admission sentence


def extract_280_grounds(text: str) -> list[str]:
    grounds: list[str] = []

    def add(label: str) -> None:
        if label not in grounds:
            grounds.append(label)

    for m in _ART_280.finditer(text):
        tail = text[m.end(): m.end() + 120]
        stop = re.search(r"ГПК|чл\.\s*(?!280)\d", tail)
        for g in _ART_280_PART.finditer(tail[: stop.start()] if stop else tail):
            label = f"чл. 280, ал. {g.group(1)}"
            if g.group(2):
                label += f", т. {g.group(2)}"
            elif g.group(3):
                label += f", предл. {_PROPOSAL_WORDS.get(g.group(3).lower(), g.group(3))}"
            add(label)
    for pattern, label in _ART_280_2_WORDS:
        if pattern.search(text):
            add(label)
    # A bare "ал. 2" adds nothing when a specific alternative of the same paragraph is known.
    specific = {g.rsplit(",", 1)[0] for g in grounds if g.count(",") == 2}
    return [g for g in grounds if g not in specific]


_SPACED_HEADING = re.compile(r"^(?:[А-Я]\s){3,}[А-Я]$")
_DISPOSITIVE = re.compile(r"^(?:Р\s*Е\s*Ш\s*И|О\s*П\s*Р\s*Е\s*Д\s*Е\s*Л\s*И|Р\s*А\s*З\s*П\s*О\s*Р\s*Е\s*Д\s*И)\s*:?\s*$")


@dataclass(frozen=True)
class Paragraph:
    no: int
    start: int  # Unicode code point offset in canonical text, inclusive
    end: int    # exclusive
    text: str
    section: str  # "reasoning" | "dispositive"


@dataclass
class ParsedAct:
    canonical_text: str
    paragraphs: list[Paragraph]
    heading: str | None
    chamber: str | None
    proceeding_article: str | None
    admission_paragraph_nos: list[int] = field(default_factory=list)
    admission_grounds: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _emit_text(node, out: list[str]) -> None:
    if node.text:
        out.append(node.text)
    for child in node:
        tag = child.tag if isinstance(child.tag, str) else ""
        if tag == "br":
            out.append("\n")
        elif tag in ("script", "style"):
            pass
        elif tag in _BLOCK_TAGS:
            out.append("\n")
            _emit_text(child, out)
            out.append("\n")
        else:
            _emit_text(child, out)
        if child.tail:
            out.append(child.tail)


def normalize_line(line: str) -> str:
    line = unicodedata.normalize("NFC", line).replace("\xa0", " ")
    return " ".join(line.split())


def html_to_lines(content_html_element) -> list[str]:
    parts: list[str] = []
    _emit_text(content_html_element, parts)
    raw = "".join(parts)
    return [ln for ln in (normalize_line(x) for x in raw.split("\n")) if ln]


def _detect_chamber(head: str) -> str | None:
    m = _CHAMBER.search(head)
    if m:
        return f"{m.group(1).capitalize()} {m.group(2).lower()} отделение"
    ordinal, college = _ORDINAL_CHAMBER.search(head), _COLLEGE.search(head)
    if ordinal and college:
        name = _ORDINALS[ordinal.group(1).lower()]
        return f"{name} {_COLLEGE_ADJ[college.group(1).lower()]} отделение"
    return None


def parse_act(html: str) -> ParsedAct:
    doc = lxml.html.fromstring(html)
    nodes = doc.xpath('//*[@id="Content"]')
    if not nodes:
        return ParsedAct("", [], None, None, None, warnings=["content_div_missing"])

    lines = html_to_lines(nodes[0])
    paragraphs: list[Paragraph] = []
    pieces: list[str] = []
    pos = 0
    section = "reasoning"
    for i, line in enumerate(lines):
        if _DISPOSITIVE.match(line):
            section = "dispositive"
        if pieces:
            pieces.append("\n")
            pos += 1
        paragraphs.append(Paragraph(i, pos, pos + len(line), line, section))
        pieces.append(line)
        pos += len(line)
    text = "".join(pieces)

    warnings: list[str] = []
    if not text:
        warnings.append("empty_text")

    heading = None
    for p in paragraphs[:5]:
        if _SPACED_HEADING.match(p.text):
            heading = p.text.replace(" ", "")
            break
        if p.text.upper() in {"РЕШЕНИЕ", "ОПРЕДЕЛЕНИЕ", "РАЗПОРЕЖДАНЕ"}:
            heading = p.text.upper()
            break

    head = "\n".join(p.text for p in paragraphs[:8])
    chamber = _detect_chamber(head)
    if chamber is None:
        warnings.append("chamber_not_found")

    m = _PROCEEDING.search(text)
    proceeding = m.group(1) if m else None

    admission_nos: list[int] = []
    grounds: list[str] = []
    reasoning = [p for p in paragraphs if p.section == "reasoning"]
    for i, p in enumerate(reasoning):
        if _ADMISSION_HINT.search(p.text):
            admission_nos.append(p.no)
            window = " ".join(x.text for x in reasoning[i: i + 1 + ADMISSION_WINDOW])
            for label in extract_280_grounds(window):
                if label not in grounds:
                    grounds.append(label)

    return ParsedAct(
        canonical_text=text,
        paragraphs=paragraphs,
        heading=heading,
        chamber=chamber,
        proceeding_article=proceeding,
        admission_paragraph_nos=admission_nos,
        admission_grounds=grounds,
        warnings=warnings,
    )
