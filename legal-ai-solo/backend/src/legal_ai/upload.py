"""Text from a document the lawyer uploads (the appellate decision as PDF, Word, HTML or text).

The file is kept only in private storage. Nothing is guessed: if the text cannot be read, the
user gets a plain message saying which format to use instead.
"""

from __future__ import annotations

import re
import zipfile
from datetime import date
from io import BytesIO
from xml.etree import ElementTree

from legal_ai.sources.courts.document import DocumentError, _normalize, extract_text

MAX_BYTES = 20 * 1024 * 1024
ALLOWED = {".pdf", ".docx", ".html", ".htm", ".txt"}
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_DATE = re.compile(r"(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{4})")


class UploadError(Exception):
    pass


def extension(filename: str) -> str:
    m = re.search(r"\.[A-Za-z0-9]{1,5}$", filename or "")
    return m.group(0).lower() if m else ""


def _docx_text(body: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(body)) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > 50 * 1024 * 1024:
                raise UploadError("Word файлът е твърде голям.")
            xml = z.read(info)
    except (zipfile.BadZipFile, KeyError) as exc:
        raise UploadError("Файлът не е валиден Word документ (.docx).") from exc
    root = ElementTree.fromstring(xml)
    lines = []
    for p in root.iter(_W + "p"):
        parts = []
        for node in p.iter():
            if node.tag == _W + "t" and node.text:
                parts.append(node.text)
            elif node.tag == _W + "tab":
                parts.append("\t")
            elif node.tag in (_W + "br", _W + "cr"):
                parts.append("\n")
        line = "".join(parts).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _txt(body: bytes) -> str:
    for enc in ("utf-8-sig", "windows-1251"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    raise UploadError("Текстовият файл не е в UTF-8 или Windows-1251.")


def read_upload(filename: str, body: bytes) -> tuple[str, str, list[str]]:
    """(text, format, warnings) for an uploaded document."""
    ext = extension(filename)
    if ext in (".doc", ".rtf", ".odt"):
        raise UploadError("Този формат не се чете. Запазете документа като .docx или PDF и го качете отново.")
    if ext not in ALLOWED:
        raise UploadError("Позволени са PDF, Word (.docx), HTML и текст (.txt).")
    if not body:
        raise UploadError("Файлът е празен.")
    if len(body) > MAX_BYTES:
        raise UploadError("Файлът е над 20 MB.")
    warnings: list[str] = []
    try:
        if ext == ".docx":
            text, fmt = _normalize(_docx_text(body)), "docx"
        elif ext == ".txt":
            text, fmt = _normalize(_txt(body)), "txt"
        else:
            t = extract_text(body, "text/html" if ext in (".html", ".htm") else "")
            text, fmt, warnings = t.text, t.fmt, t.warnings
    except UploadError:
        raise
    except DocumentError as exc:
        raise UploadError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - broken, encrypted or truncated files from users
        raise UploadError("Файлът не може да се прочете (повреден или защитен с парола). "
                          "Запазете го наново като PDF или .docx.") from exc
    if len(text) < 300:
        raise UploadError("В документа почти няма текст (може да е сканиран). Качете PDF с текст или Word файл.")
    if not re.search(r"[А-Яа-я]{3}", text):
        warnings.append("няма кирилица в текста")
    return text, fmt, warnings


def first_date(text: str, within: int = 800) -> date | None:
    """The first date near the top (the decision date in Bulgarian court decisions), if any."""
    for m in _DATE.finditer(text[:within]):
        try:
            d = date(int(m[3]), int(m[2]), int(m[1]))
        except ValueError:
            continue
        if 1990 <= d.year <= date.today().year:
            return d
    return None
