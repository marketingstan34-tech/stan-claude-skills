"""The lawyer's own edited version of a draft (изложение or жалба), kept next to the report.

The automatic version is never overwritten: the edit is a separate file, can be reset, and the
pages and the Word download use it when it exists.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from legal_ai.cassation.draft import Block

DOCS = {"draft": "Изложение по чл. 284, ал. 3, т. 1 ГПК", "appeal": "Касационна жалба"}
KINDS = {"heading", "center", "p", "quote", "item", "note"}
MAX_BLOCKS = 800
MAX_TEXT = 20_000


def _path(report_dir: Path, doc: str) -> Path:
    if doc not in DOCS:
        raise ValueError(doc)
    return report_dir / f"edit-{doc}.json"


def load_edit(report_dir: Path, doc: str) -> dict | None:
    try:
        data = json.loads(_path(report_dir, doc).read_text(encoding="utf-8"))
        blocks = [Block(b["kind"], b["text"]) for b in data["blocks"]]
        return {"blocks": blocks, "saved_at": data.get("saved_at", "")}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def clean_blocks(raw) -> list[Block]:
    if not isinstance(raw, list) or len(raw) > MAX_BLOCKS:
        raise ValueError("Невалиден текст.")
    out = []
    for b in raw:
        if not isinstance(b, dict):
            raise ValueError("Невалиден текст.")
        kind = b.get("kind") if b.get("kind") in KINDS else "p"
        text = str(b.get("text", "")).replace("\r", "").strip()[:MAX_TEXT]
        if text:
            out.append(Block(kind, text))
    if not out:
        raise ValueError("Документът е празен.")
    return out


def save_edit(report_dir: Path, doc: str, blocks: list[Block]) -> str:
    saved_at = datetime.now(timezone.utc).isoformat()
    path = _path(report_dir, doc)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"saved_at": saved_at, "blocks": [{"kind": b.kind, "text": b.text} for b in blocks]},
                              ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return saved_at


def reset_edit(report_dir: Path, doc: str) -> None:
    try:
        _path(report_dir, doc).unlink()
    except FileNotFoundError:
        pass
