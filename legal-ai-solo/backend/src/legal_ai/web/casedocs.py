"""Documents the lawyer adds for the appeal draft, kept only in private storage.

- Case documents for one report (pleadings, protocols, the earlier appeal, expert reports...):
  runs/<id>/added.json, files in uploads/. Read (OCR for scans) when the appeal is written.
- Style samples: the lawyer's own filings in any case, used only for the writing style:
  style/index.json, files in style/.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from legal_ai.upload import ALLOWED, MAX_BYTES, extension, first_date, read_stored

MAX_CASE_DOCS = 60
MAX_STYLE = 3
ZIP_MAX_TOTAL = 200 * 1024 * 1024

# (kind, pattern at the top of the document, priority: lower goes first when the prompt is full)
KINDS = [
    ("пълномощно", r"ПЪЛНОМОЩНО", 9),
    ("договор за правна защита", r"ДОГОВОР\s+ЗА\s+ПРАВНА\s+ЗАЩИТА", 9),
    ("отговор на касационна жалба", r"ОТГОВОР[^\n]{0,40}\n?[^\n]{0,40}(?i:касационна\s+жалба)|ЧЛ\.\s*287", 2),
    ("отговор на въззивна жалба", r"ОТГОВОР[^\n]{0,40}\n?[^\n]{0,40}(?i:въззивна\s+жалба)|ЧЛ\.\s*263", 2),
    ("отговор на искова молба", r"ОТГОВОР[^\n]{0,40}\n?[^\n]{0,40}(?i:искова\s+молба)|ЧЛ\.\s*131", 2),
    ("изложение по чл. 284", r"ИЗЛОЖЕНИЕ", 2),
    ("касационна жалба", r"КАСАЦИОННА\s+ЖАЛБА", 2),
    ("въззивна жалба", r"ВЪЗЗИВНА\s+ЖАЛБА", 1),
    ("искова молба", r"ИСКОВА\s+МОЛБА", 1),
    ("писмена защита", r"ПИСМЕН[А-Я]*\s+(?:ЗАЩИТА|БЕЛЕЖКИ)", 2),
    ("протокол от заседание", r"П\s*Р\s*О\s*Т\s*О\s*К\s*О\s*Л", 2),
    ("експертиза", r"ЗАКЛЮЧЕНИЕ|ЕКСПЕРТИЗА", 3),
    ("решение", r"Р\s*Е\s*Ш\s*Е\s*Н\s*И\s*Е", 0),
    ("определение", r"О\s*П\s*Р\s*Е\s*Д\s*Е\s*Л\s*Е\s*Н\s*И\s*Е", 3),
    ("нотариален акт", r"НОТАРИАЛЕН\s+АКТ", 4),
    ("скица", r"СКИЦА", 5),
    ("удостоверение", r"УДОСТОВЕРЕНИЕ", 5),
    ("молба", r"МОЛБА", 4),
]
EXCLUDED_KINDS = {"пълномощно", "договор за правна защита"}


def classify(text: str) -> tuple[str, int]:
    """(kind, priority) from the heading: the kind word that appears first in the top of the document."""
    head = (text or "")[:1500]
    best: tuple[int, str, int] | None = None
    for kind, pattern, prio in KINDS:
        m = re.search(pattern, head)           # headings are in capitals; plain words in the body are not
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), kind, prio)
    if best is None:
        return "друг документ", 6
    kind, prio = best[1], best[2]
    if kind == "решение":
        top = head.upper()
        if "ВЪРХОВЕН КАСАЦИОНЕН" in top:
            return "решение на ВКС", 1
        if "ВЪЗЗИВ" in top or "АПЕЛАТИВЕН" in top:
            return "въззивно решение", 0
        return "първоинстанционно решение", 0
    return kind, prio


def expand(filename: str, body: bytes) -> tuple[list[tuple[str, bytes]], list[str]]:
    """The files in an upload: a ZIP is opened (only supported formats, size limits), anything else as is.
    Returns (files, skipped names)."""
    if extension(filename) != ".zip":
        return [(filename, body)], []
    import zipfile
    from io import BytesIO
    files, skipped, total = [], [], 0
    try:
        z = zipfile.ZipFile(BytesIO(body))
    except zipfile.BadZipFile as exc:
        raise ValueError("ZIP файлът не може да се отвори.") from exc
    for info in z.infolist():
        name = os.path.basename(info.filename)
        if info.is_dir() or not name or name.startswith(".") or "__MACOSX" in info.filename:
            continue
        if extension(name) not in ALLOWED or info.file_size > MAX_BYTES:
            skipped.append(name)
            continue
        total += info.file_size
        if total > ZIP_MAX_TOTAL or len(files) >= MAX_CASE_DOCS:
            skipped.append(name)
            continue
        files.append((name, z.read(info)))
    return files, skipped


class FileList:
    def __init__(self, index: Path, files_dir: Path, limit: int) -> None:
        self.index, self.files_dir, self.limit = index, files_dir, limit

    def items(self) -> list[dict]:
        try:
            data = json.loads(self.index.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [{"date": "", **x} for x in data if isinstance(x, dict) and x.get("file") and x.get("filename")] \
            if isinstance(data, list) else []

    def _save(self, items: list[dict]) -> None:
        self.index.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.index.with_name(self.index.name + ".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.index)

    def add(self, filename: str, body: bytes, text: str = "") -> dict:
        items = self.items()
        if len(items) >= self.limit:
            raise ValueError(f"Най-много {self.limit} документа. Махнете някой, преди да добавите нов.")
        self.files_dir.mkdir(parents=True, exist_ok=True)
        name = f"{uuid4().hex}{extension(filename)}"
        (self.files_dir / name).write_bytes(body)
        item = {"id": uuid4().hex[:12], "file": name, "filename": os.path.basename(filename)[:120],
                "added": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "date": ""}
        if text:     # a document with a text layer is classified at once; a scan when it is read (OCR)
            kind, prio = classify(text)
            d = first_date(text, 1500)
            item.update(kind=kind, prio=prio, date=d.isoformat() if d else "", included=kind not in EXCLUDED_KINDS)
        self._save(items + [item])
        return item

    def remove(self, item_id: str) -> bool:
        items = self.items()
        keep = [x for x in items if x.get("id") != item_id]
        if len(keep) == len(items):
            return False
        for x in items:
            if x.get("id") == item_id:
                for f in (self.files_dir / x["file"], self.files_dir / (x["file"] + ".ocr.txt")):
                    try:
                        f.unlink()
                    except OSError:
                        pass
        self._save(keep)
        return True

    def set_included(self, item_id: str, included: bool) -> None:
        items = self.items()
        for x in items:
            if x.get("id") == item_id:
                x["included"] = included
        self._save(items)

    def read_all(self) -> list[dict]:
        """Reads every document (OCR for scans, cached), stores its kind and date, returns them
        in working order: by priority of the kind, then by date."""
        items, out = self.items(), []
        for x in items:
            if not re.fullmatch(r"[0-9a-f]{32}\.[a-z]{2,5}", x["file"]):
                continue
            try:
                text = read_stored(self.files_dir / x["file"], x["filename"])[0]
            except Exception:   # noqa: BLE001 - one unreadable document must not stop the appeal
                x["kind"] = "не се чете"
                continue
            kind, prio = classify(text)
            d = first_date(text, 1500)
            x.update(kind=kind, date=d.isoformat() if d else "", prio=prio)
            x.setdefault("included", kind not in EXCLUDED_KINDS)
            if x["included"]:
                out.append({**x, "text": text})
        self._save(items)
        return sorted(out, key=lambda x: (x["prio"], x["date"] or "9999"))

    def texts(self) -> list[tuple[str, str]]:
        """(label, text) of the included documents, in working order (see read_all)."""
        out = []
        for x in self.read_all():
            date = ".".join(reversed(x["date"].split("-"))) if x["date"] else "без дата"
            out.append((f"{x['kind']} – {x['filename']} ({date})", x["text"]))
        return out

    def chronology(self) -> str:
        """One line per included document, by date, for the top of the prompt."""
        rows = sorted((x for x in self.items() if x.get("included", True) and x.get("kind")),
                      key=lambda x: x.get("date") or "9999")
        return "\n".join(f"- {('.'.join(reversed(x['date'].split('-'))) if x.get('date') else 'без дата')}: "
                         f"{x['kind']} ({x['filename']})" for x in rows)


def case_docs(storage: Path, run_id: str) -> FileList:
    return FileList(storage / "runs" / run_id / "added.json", storage / "uploads", MAX_CASE_DOCS)


def style_samples(storage: Path) -> FileList:
    return FileList(storage / "style" / "index.json", storage / "style", MAX_STYLE)
