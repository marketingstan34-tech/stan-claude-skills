"""Act file bytes -> plain text.

ecase.justice.bg serves either a text-layer PDF or Word-exported HTML whose charset is
declared only in a <meta> tag (windows-1251 in the files checked). Decoding by that tag
is mandatory: a UTF-8 decode destroys the Cyrillic irreversibly.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

from lxml import html as lxml_html

_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([A-Za-z0-9_\-]+)""", re.I)
_BLOCK_TAGS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table"}


class DocumentError(Exception):
    pass


@dataclass
class ActText:
    text: str
    fmt: str            # "pdf" | "html"
    encoding: str | None
    pages: int | None
    warnings: list[str]


def sniff_format(body: bytes, content_type: str = "") -> str:
    if body[:5] == b"%PDF-":
        return "pdf"
    head = body[:4096].lower()
    if b"<html" in head or b"<!doctype html" in head or "html" in content_type.lower():
        return "html"
    raise DocumentError("Непознат формат на файла (нито PDF, нито HTML).")


def _header_charset(content_type: str) -> str | None:
    m = re.search(r"charset=([A-Za-z0-9_\-]+)", content_type or "")
    return m[1].lower() if m else None


def _meta_charset(body: bytes) -> str | None:
    m = _META_CHARSET.search(body[:20000])
    return m[1].decode("ascii").lower() if m else None


def html_charset(body: bytes, content_type: str = "") -> str:
    return _header_charset(content_type) or _meta_charset(body) or "utf-8"


def _decode_html(body: bytes, content_type: str) -> tuple[str, str, list[str]]:
    """Strict decode with the declared charsets first, then utf-8 and windows-1251."""
    warnings: list[str] = []
    header, meta = _header_charset(content_type), _meta_charset(body)
    tried: list[str] = []
    for enc in (header, meta, "utf-8", "windows-1251"):
        if not enc or enc in tried:
            continue
        tried.append(enc)
        try:
            text = body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
        if header and enc != header:
            warnings.append(f"кодировката в HTTP header ({header}) не пасва; използвана е {enc}")
        return text, enc, warnings
    raise DocumentError(f"Не може да се декодира (опитани: {', '.join(tried)})")


def _normalize(text: str) -> str:
    text = text.replace("\r", "").replace("\xa0", " ")
    lines = [" ".join(line.split()) for line in text.split("\n")]
    out, blank = [], False
    for line in lines:
        if not line:
            if not blank and out:
                out.append("")
            blank = True
        else:
            out.append(line)
            blank = False
    return "\n".join(out).strip()


def _html_to_text(markup: str) -> str:
    """One output line per block (paragraph, heading, list item, table cell paragraph)."""
    doc = lxml_html.fromstring(markup)
    for bad in doc.xpath("//script|//style|//head|//xml"):
        bad.getparent().remove(bad)
    blocks = doc.xpath("//body//*[self::p or self::h1 or self::h2 or self::h3 or self::h4"
                       " or self::h5 or self::h6 or self::li]")
    if not blocks:
        return doc.text_content()
    lines = []
    for b in blocks:
        if b.xpath("ancestor::p|ancestor::li"):
            continue  # nested block: its text is already in the ancestor
        t = " ".join(b.text_content().split())
        if t:
            lines.append(t)
    return "\n".join(lines)


_INDENT = re.compile(r"^ {6,}\S")


def _reflow_layout(page_text: str) -> list[str]:
    """Join wrapped lines of layout-mode text into paragraphs.

    A paragraph starts after a blank line or at an indented line (>= 6 spaces); other
    lines continue the current paragraph. Runs of spaces from justification collapse.
    """
    paras: list[str] = []
    current: list[str] = []
    for line in page_text.split("\n"):
        if not line.strip():
            if current:
                paras.append(" ".join(current))
                current = []
            continue
        if _INDENT.match(line) and current:
            paras.append(" ".join(current))
            current = []
        current.append(" ".join(line.split()))
    if current:
        paras.append(" ".join(current))
    return paras


def _pdf_to_text(body: bytes) -> tuple[str, int]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(body))
    paras: list[str] = []
    for page in reader.pages:
        paras.extend(_reflow_layout(page.extract_text(extraction_mode="layout") or ""))
    return "\n".join(paras), len(reader.pages)


def extract_text(body: bytes, content_type: str = "") -> ActText:
    fmt = sniff_format(body, content_type)
    warnings: list[str] = []
    if fmt == "pdf":
        raw, pages = _pdf_to_text(body)
        text = _normalize(raw)
        if len(text) < 200 * max(pages, 1) * 0.2:
            warnings.append("малко текст в PDF — възможно сканиран документ без текстов слой")
        enc = None
    else:
        markup, enc, warnings = _decode_html(body, content_type)
        text = _normalize(_html_to_text(markup))
        pages = None
    if "�" in text:
        warnings.append("текстът съдържа заместващи знаци (�) — възможна грешна кодировка")
    if not re.search(r"[А-Яа-я]{3}", text):
        warnings.append("няма кирилица в текста")
    return ActText(text, fmt, enc, pages, warnings)
