"""Load interpretative-decision PDFs into the corpus as source 'vks-tr'."""

from __future__ import annotations

import hashlib
import json

import psycopg
from psycopg.types.json import Jsonb

from legal_ai.citations.verify import text_hash
from legal_ai.retrieval.text import normalize_for_search
from legal_ai.sources.courts.document import extract_text
from legal_ai.sources.vks.interpretive import COLLEGES, TrFile, find_date

SOURCE = "vks-tr"
PARSER_VERSION = "tr-1"
COURT = "Върховен касационен съд"


def ingest_tr(conn: psycopg.Connection, f: TrFile, storage_key: str) -> tuple[bool, list[str]]:
    data = f.path.read_bytes()
    t = extract_text(data, "application/pdf")
    if not t.text:
        return False, ["празен текст"]
    lines = [ln for ln in t.text.split("\n") if ln.strip()]
    canonical = "\n".join(lines)
    meta_path = f.path.with_suffix(".meta.json")
    retrieved_at = json.loads(meta_path.read_text(encoding="utf-8")).get("retrieved_at") \
        if meta_path.exists() else None
    with conn.cursor() as cur:
        sha = hashlib.sha256(data).hexdigest()
        cur.execute("""
            INSERT INTO source_artifacts (source, url, retrieved_at, acquisition, mime, sha256, storage_key)
            VALUES (%s, %s, %s, 'direct', 'application/pdf', %s, %s)
            ON CONFLICT (source, sha256) DO UPDATE SET url = source_artifacts.url RETURNING id""",
                    (SOURCE, f.url, retrieved_at, sha, storage_key))
        artifact_id = cur.fetchone()["id"]
        cur.execute("""
            INSERT INTO decisions (source, source_record_id, court, chamber, act_type, act_number,
                act_date, case_type, case_number, case_year, proceeding_article, canonical_url)
            VALUES (%s, %s, %s, %s, 'Тълкувателно решение', %s, %s, 'тълк.', %s, %s, 'ТР', %s)
            ON CONFLICT (source, source_record_id) DO UPDATE SET act_date = EXCLUDED.act_date
            RETURNING id""",
                    (SOURCE, f"{f.college}-{f.year}-{f.number}", COURT, COLLEGES[f.college].split(" ")[0],
                     str(f.number), find_date(canonical), str(f.number), f.year, f.url))
        decision_id = cur.fetchone()["id"]
        thash = text_hash(canonical)
        cur.execute("SELECT id FROM decision_versions WHERE decision_id=%s AND text_hash=%s "
                    "AND parser_version=%s", (decision_id, thash, PARSER_VERSION))
        row = cur.fetchone()
        if row:
            version_id = row["id"]
        else:
            cur.execute("""
                INSERT INTO decision_versions (decision_id, artifact_id, canonical_text, text_hash,
                    parser_version, warnings) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                        (decision_id, artifact_id, canonical, thash, PARSER_VERSION, Jsonb(t.warnings)))
            version_id = cur.fetchone()["id"]
            pos, rows = 0, []
            for i, ln in enumerate(lines):
                rows.append((version_id, i, "reasoning", pos, pos + len(ln), ln, False,
                             normalize_for_search(ln)))
                pos += len(ln) + 1
            cur.executemany("""
                INSERT INTO passages (decision_version_id, paragraph_no, section, start_offset,
                    end_offset, exact_text, is_admission, search_text)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""", rows)
        cur.execute("UPDATE decisions SET current_version_id=%s WHERE id=%s", (version_id, decision_id))
    return True, t.warnings
