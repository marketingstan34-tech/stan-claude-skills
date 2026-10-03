"""Text from a document the lawyer uploads (the appellate decision as PDF, Word, HTML or text).

The file is kept only in private storage. Nothing is guessed: if the text cannot be read, the
user gets a plain message saying which format to use instead. A scanned PDF (no text layer) is
read with OCR (Tesseract, Bulgarian) when the server has it; the result is marked as OCR.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import date
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

from legal_ai.sources.courts.document import DocumentError, _normalize, extract_text

MAX_BYTES = 20 * 1024 * 1024
ALLOWED = {".pdf", ".docx", ".html", ".htm", ".txt"}
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_DATE = re.compile(r"(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{4})")


OCR_MAX_PAGES = 60
OCR_NOTE = "сканиран документ – текстът е разчетен автоматично (OCR); проверете имената, числата и цитатите"
# "№" is often read as one of these before a number
_OCR_NO = re.compile(r"(?<![А-Яа-яA-Za-z])(?:Хе|Хо|Ne|No|Мо|Ме|Nе|Nо)\s*(?=\d)")


class UploadError(Exception):
    pass


_PRIVATE = re.compile(r"^\W{0,3}(?:ДОГОВОР\s+ЗА\s+ПРАВНА\s+ЗАЩИТА|ПЪЛНОМОЩНО)")


def private_page(page: str) -> bool:
    """A page that starts with a power of attorney or a fee agreement: not sent to the AI."""
    lines = [line.strip() for line in page.splitlines() if line.strip()]
    return bool(_PRIVATE.match(" ".join(lines[:2])))


def ocr_available() -> bool:
    return bool(shutil.which("tesseract") and shutil.which("pdftoppm"))


def ocr_pdf(body: bytes) -> str:
    """Text of a scanned PDF, page by page (pdftoppm at 200 dpi, then Tesseract bul+eng)."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.pdf"
        src.write_bytes(body)
        subprocess.run(["pdftoppm", "-r", "200", "-gray", "-l", str(OCR_MAX_PAGES), "-png", str(src),
                        str(Path(tmp) / "p")], check=True, capture_output=True, timeout=300)
        pages = []
        for img in sorted(Path(tmp).glob("p-*.png")):
            out = subprocess.run(["tesseract", str(img), "stdout", "-l", "bul+eng", "--psm", "6"],
                                 check=True, capture_output=True, timeout=120)
            pages.append(out.stdout.decode("utf-8", "replace"))
    return "\f".join(pages)   # form feed between pages, so attachments can be dropped page by page


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


def read_upload(filename: str, body: bytes, ocr: bool = True) -> tuple[str, str, list[str]]:
    """(text, format, warnings) for an uploaded document.

    `ocr=False` (the quick check when the form is sent) accepts a scanned PDF without reading it;
    the background job then reads it with OCR."""
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
    if len(text) < 300 and ext == ".pdf" and ocr_available():
        if not ocr:
            return "", "pdf-scan", [OCR_NOTE]
        try:
            pages = _OCR_NO.sub("№ ", ocr_pdf(body)).split("\f")
            kept = [pg for pg in pages if not private_page(pg)]
            text = _normalize("\n".join(kept))
        except (OSError, subprocess.SubprocessError) as exc:
            raise UploadError("Сканираният PDF не може да се разчете. Качете Word файл или PDF с текст.") from exc
        fmt = "pdf-ocr"
        warnings = [OCR_NOTE] + ([f"махнати {len(pages) - len(kept)} стр. (пълномощно / договор за правна защита)"]
                                 if len(kept) < len(pages) else [])
    if len(text) < 300:
        raise UploadError("В документа почти няма текст (може да е сканиран). Качете PDF с текст или Word файл.")
    if not re.search(r"[А-Яа-я]{3}", text):
        warnings.append("няма кирилица в текста")
    return text, fmt, warnings


def read_stored(path: Path, filename: str) -> tuple[str, str, list[str]]:
    """read_upload for a file already in private storage; OCR text is kept next to it (.ocr.txt)
    so a scanned document is read only once."""
    cache = path.with_name(path.name + ".ocr.txt")
    if cache.is_file():
        return cache.read_text(encoding="utf-8"), "pdf-ocr", [OCR_NOTE]
    text, fmt, warnings = read_upload(filename, path.read_bytes())
    if fmt == "pdf-ocr":
        cache.write_text(text, encoding="utf-8")
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
