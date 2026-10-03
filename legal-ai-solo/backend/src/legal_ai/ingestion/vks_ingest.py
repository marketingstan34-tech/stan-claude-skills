"""Load saved VKS pages from a raw folder into the corpus tables.

Expected layout (written by `legal-ai crawl-vks` or a manual/Firecrawl fetch):
    <raw>/manifest.json            optional; acquisition notes and retrieval times
    <raw>/lists/<name>.html        result lists (+ optional <name>.meta.json)
    <raw>/acts/<SOURCE_ID>.html    act pages    (+ optional <SOURCE_ID>.meta.json)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import psycopg
from psycopg.types.json import Jsonb

from legal_ai.citations.verify import text_hash
from legal_ai.retrieval.text import normalize_for_search
from legal_ai.sources.vks import LIST_TRUNCATION_LIMIT, SOURCE
from legal_ai.sources.vks.parser import PARSER_VERSION, ListRow, parse_act, parse_list
from legal_ai.sources.vks.urls import act_url

COURT = "Върховен касационен съд"


@dataclass
class IngestStats:
    lists: int = 0
    truncated_lists: list[str] = field(default_factory=list)
    acts_seen: int = 0
    acts_ingested: int = 0
    new_versions: int = 0
    skipped: list[str] = field(default_factory=list)
    warnings: dict[str, list[str]] = field(default_factory=dict)


def _read_meta(html_path: Path) -> dict:
    meta_path = html_path.with_suffix(".meta.json")
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _upsert_artifact(cur, url: str, data: bytes, storage_key: str, acquisition: str,
                     retrieved_at: str | None) -> str:
    sha = hashlib.sha256(data).hexdigest()
    cur.execute(
        """
        INSERT INTO source_artifacts (source, url, retrieved_at, acquisition, sha256, storage_key)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (source, sha256) DO UPDATE SET url = source_artifacts.url
        RETURNING id
        """,
        (SOURCE, url, retrieved_at, acquisition, sha, storage_key),
    )
    return cur.fetchone()["id"]


def ingest_raw_dir(conn: psycopg.Connection, raw_dir: Path, storage_root: Path,
                   acquisition: str, scope: str) -> IngestStats:
    stats = IngestStats()
    manifest = {}
    if (raw_dir / "manifest.json").exists():
        manifest = json.loads((raw_dir / "manifest.json").read_text(encoding="utf-8"))
    acts_meta = {}
    for a in manifest.get("acts", []) if isinstance(manifest.get("acts"), list) else []:
        acts_meta[a.get("id")] = a

    rows_by_id: dict[str, ListRow] = {}
    case_type_by_id: dict[str, str] = {}

    with conn.cursor() as cur:
        for list_path in sorted((raw_dir / "lists").glob("*.html")):
            data = list_path.read_bytes()
            meta = _read_meta(list_path)
            url = meta.get("url") or meta.get("sourceURL") or f"file:{list_path.name}"
            rows = parse_list(data.decode("utf-8", errors="replace"))
            truncated = len(rows) >= LIST_TRUNCATION_LIMIT
            artifact_id = _upsert_artifact(
                cur, url, data, str(list_path.relative_to(storage_root)), acquisition, None
            )
            cur.execute(
                """
                INSERT INTO source_list_runs (source, url, description, row_count, truncated, artifact_id)
                VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
                """,
                (SOURCE, url, list_path.stem, len(rows), truncated, artifact_id),
            )
            stats.lists += 1
            if truncated:
                stats.truncated_lists.append(list_path.stem)
            qs = parse_qs(urlparse(url).query)
            case_type = qs.get("AktVidDelo", [None])[0]
            if case_type in (None, "empty") and qs.get("AktOtdelenie", [""])[0].endswith("тър."):
                case_type = "търг."  # commercial decisions are listed per chamber
            elif case_type == "empty":
                case_type = None
            for r in rows:
                rows_by_id.setdefault(r.source_id, r)
                if case_type:
                    case_type_by_id.setdefault(r.source_id, case_type)

        for act_path in sorted((raw_dir / "acts").glob("*.html")):
            source_id = act_path.stem
            stats.acts_seen += 1
            meta = _read_meta(act_path)
            status = meta.get("statusCode")
            if status not in (None, 200):
                stats.skipped.append(f"{source_id}: HTTP {status}")
                continue
            data = act_path.read_bytes()
            parsed = parse_act(data.decode("utf-8", errors="replace"))
            if not parsed.canonical_text:
                stats.skipped.append(f"{source_id}: {','.join(parsed.warnings)}")
                continue
            if parsed.warnings:
                stats.warnings[source_id] = parsed.warnings

            url = act_url(source_id)
            retrieved_at = acts_meta.get(source_id, {}).get("retrieved_at")
            artifact_id = _upsert_artifact(
                cur, url, data, str(act_path.relative_to(storage_root)), acquisition, retrieved_at
            )
            row = rows_by_id.get(source_id)
            cur.execute(
                """
                INSERT INTO decisions (source, source_record_id, court, chamber, act_type, act_number,
                    act_date, case_type, case_number, case_year, proceeding_article,
                    admission_grounds, canonical_url, admission_result)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source, source_record_id) DO UPDATE SET
                    chamber = EXCLUDED.chamber,
                    act_type = COALESCE(EXCLUDED.act_type, decisions.act_type),
                    act_number = COALESCE(EXCLUDED.act_number, decisions.act_number),
                    act_date = COALESCE(EXCLUDED.act_date, decisions.act_date),
                    case_type = COALESCE(EXCLUDED.case_type, decisions.case_type),
                    case_number = COALESCE(EXCLUDED.case_number, decisions.case_number),
                    case_year = COALESCE(EXCLUDED.case_year, decisions.case_year),
                    proceeding_article = EXCLUDED.proceeding_article,
                    admission_grounds = EXCLUDED.admission_grounds,
                    admission_result = EXCLUDED.admission_result
                RETURNING id
                """,
                (
                    SOURCE, source_id, COURT, parsed.chamber,
                    row.act_type if row else (parsed.heading or "").lower() or None,
                    row.act_number if row else None,
                    row.act_date if row else None,
                    case_type_by_id.get(source_id),
                    row.case_number if row else None,
                    row.case_year if row else None,
                    parsed.proceeding_article,
                    Jsonb(parsed.admission_grounds),
                    url,
                    parsed.admission_result,
                ),
            )
            decision_id = cur.fetchone()["id"]

            thash = text_hash(parsed.canonical_text)
            cur.execute(
                """
                SELECT id FROM decision_versions
                WHERE decision_id = %s AND text_hash = %s AND parser_version = %s
                """,
                (decision_id, thash, PARSER_VERSION),
            )
            existing = cur.fetchone()
            if existing:
                version_id = existing["id"]
            else:
                cur.execute(
                    """
                    INSERT INTO decision_versions (decision_id, artifact_id, canonical_text, text_hash,
                        parser_version, warnings)
                    VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
                    """,
                    (decision_id, artifact_id, parsed.canonical_text, thash, PARSER_VERSION,
                     Jsonb(parsed.warnings)),
                )
                version_id = cur.fetchone()["id"]
                stats.new_versions += 1
                admission = set(parsed.admission_paragraph_nos)
                cur.executemany(
                    """
                    INSERT INTO passages (decision_version_id, paragraph_no, section, start_offset,
                        end_offset, exact_text, is_admission, search_text)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (version_id, p.no, p.section, p.start, p.end, p.text, p.no in admission,
                         normalize_for_search(p.text))
                        for p in parsed.paragraphs
                    ],
                )
            cur.execute("UPDATE decisions SET current_version_id = %s WHERE id = %s",
                        (version_id, decision_id))
            stats.acts_ingested += 1

        cur.execute(
            """
            INSERT INTO corpus_snapshots (scope, manifest) VALUES (%s, %s) RETURNING id
            """,
            (scope, Jsonb({
                "raw_dir": str(raw_dir),
                "acquisition": acquisition,
                "source_query": manifest.get("query"),
                "lists": stats.lists,
                "truncated_lists": stats.truncated_lists,
                "acts_ingested": stats.acts_ingested,
                "skipped": stats.skipped,
                "parser_version": PARSER_VERSION,
                "completeness": "not verified against an independent count",
            })),
        )
        snapshot_id = cur.fetchone()["id"]
        cur.execute(
            """
            INSERT INTO corpus_members (corpus_snapshot_id, decision_version_id)
            SELECT %s, current_version_id FROM decisions
            WHERE source = %s AND current_version_id IS NOT NULL
            """,
            (snapshot_id, SOURCE),
        )
    conn.commit()
    return stats
