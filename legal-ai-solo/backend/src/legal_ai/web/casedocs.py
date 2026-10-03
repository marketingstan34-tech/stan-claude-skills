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

from legal_ai.upload import extension, read_stored

MAX_CASE_DOCS = 8
MAX_STYLE = 3


class FileList:
    def __init__(self, index: Path, files_dir: Path, limit: int) -> None:
        self.index, self.files_dir, self.limit = index, files_dir, limit

    def items(self) -> list[dict]:
        try:
            data = json.loads(self.index.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [x for x in data if isinstance(x, dict) and x.get("file") and x.get("filename")] \
            if isinstance(data, list) else []

    def _save(self, items: list[dict]) -> None:
        self.index.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.index.with_name(self.index.name + ".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.index)

    def add(self, filename: str, body: bytes) -> dict:
        items = self.items()
        if len(items) >= self.limit:
            raise ValueError(f"Най-много {self.limit} документа. Махнете някой, преди да добавите нов.")
        self.files_dir.mkdir(parents=True, exist_ok=True)
        name = f"{uuid4().hex}{extension(filename)}"
        (self.files_dir / name).write_bytes(body)
        item = {"id": uuid4().hex[:12], "file": name, "filename": os.path.basename(filename)[:120],
                "added": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
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

    def texts(self) -> list[tuple[str, str]]:
        """(file name, text) of every readable document; an unreadable one is left out."""
        out = []
        for x in self.items():
            if not re.fullmatch(r"[0-9a-f]{32}\.[a-z]{2,5}", x["file"]):
                continue
            try:
                out.append((x["filename"], read_stored(self.files_dir / x["file"], x["filename"])[0]))
            except Exception:   # noqa: BLE001 - one unreadable document must not stop the appeal
                continue
        return out


def case_docs(storage: Path, run_id: str) -> FileList:
    return FileList(storage / "runs" / run_id / "added.json", storage / "uploads", MAX_CASE_DOCS)


def style_samples(storage: Path) -> FileList:
    return FileList(storage / "style" / "index.json", storage / "style", MAX_STYLE)
