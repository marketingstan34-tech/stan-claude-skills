"""The VKS decisions cited in the draft ("Прилагам"), as one ZIP of Word files.

Each text comes from the own database when the decision is there, otherwise from www.vks.bg
(one request every 2 s, as everywhere). A decision that cannot be fetched is listed in
ЛИПСВАЩИ.txt with its link, never left out silently.
"""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from typing import Callable

from legal_ai.cassation.draft import Block, to_docx

MAX_FILES = 20


def _file_name(i: int, label: str) -> str:
    safe = re.sub(r"[^0-9A-Za-zА-Яа-я№._ -]+", "_", label).strip(" ._")[:90]
    return f"{i:02d} {safe}.docx"


def _db_text(conn, source_id: str) -> str | None:
    if conn is None or not source_id:
        return None
    source, record = ("vks-tr", source_id.split(":", 1)[1]) if source_id.startswith("vks-tr:") else ("vks", source_id)
    with conn.cursor() as cur:
        cur.execute("""SELECT v.canonical_text FROM decisions d
                       JOIN decision_versions v ON v.id = d.current_version_id
                       WHERE d.source = %s AND d.source_record_id = %s LIMIT 1""", (source, record))
        row = cur.fetchone()
    return row["canonical_text"] if row else None


def build_zip(items: list[tuple[str, str, str]], conn=None,
              fetch_text: Callable[[str], str | None] | None = None) -> tuple[bytes, int, list[str]]:
    """(zip bytes, files written, missing labels). `items`: (label, source_id, url)."""
    buf = BytesIO()
    missing: list[str] = []
    written = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, (label, sid, url) in enumerate(items[:MAX_FILES], 1):
            text = _db_text(conn, sid)
            if text is None and fetch_text and re.fullmatch(r"[0-9A-F]{32}", sid or ""):
                try:
                    text = fetch_text(sid)
                except Exception:   # noqa: BLE001 - one failed download must not lose the others
                    text = None
            if not text:
                missing.append(f"{label}\n  {url}")
                continue
            blocks = [Block("heading", label), Block("note", f"Източник: {url}")]
            blocks += [Block("p", para) for para in text.split("\n") if para.strip()]
            z.writestr(_file_name(i, label), to_docx(blocks))
            written += 1
        if len(items) > MAX_FILES:
            missing += [f"{label}\n  {url}" for label, _, url in items[MAX_FILES:]]
        if missing:
            z.writestr("ЛИПСВАЩИ.txt", "Тези решения не бяха свалени – отворете ги от връзката:\n\n"
                       + "\n\n".join(missing) + "\n")
    return buf.getvalue(), written, missing
