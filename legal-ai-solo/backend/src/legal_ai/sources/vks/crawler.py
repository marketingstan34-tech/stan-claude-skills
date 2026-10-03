"""Polite direct fetcher for VKS lists and act pages.

- one request at a time, at least SOURCE_MIN_INTERVAL_SECONDS between requests;
- only https://www.vks.bg is allowed (SSRF guard), redirects are not followed;
- bounded retries for timeouts/429/5xx, honouring Retry-After; no retry on 4xx;
- month-by-month listing; a list with 249 rows is split by chamber, and any
  part that still has 249 rows is recorded as truncated (never silently).
"""

from __future__ import annotations

import calendar
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

from legal_ai.sources.vks import HOST, LIST_TRUNCATION_LIMIT
from legal_ai.sources.vks.parser import parse_list
from legal_ai.sources.vks.urls import CHAMBERS, ListQuery, act_url, list_url

from legal_ai.http import FetchError, Fetched, PoliteClient  # noqa: F401  (re-exported)


class VksClient(PoliteClient):
    """Polite client restricted to www.vks.bg."""

    def __init__(self, min_interval: float = 2.0, user_agent: str = "legal-ai-solo/0.1",
                 transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__([HOST], min_interval, user_agent, transport=transport,
                         sleep=sleep, clock=clock)


@dataclass
class CrawlReport:
    lists: list[dict] = field(default_factory=list)
    acts: list[dict] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)


def _slug(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Zа-яА-Я]+", "-", text).strip("-")


_KEEP_IDS = ("Content", "TablicaRezultati", "kriterii")


def slim_html(body: bytes) -> bytes:
    """Keep only the act text / result table of a VKS page (pages embed ~600 KB of fonts).

    The kept elements are serialized unchanged by lxml, so parse_act/parse_list read the
    slim file exactly as the full page. Falls back to the full body if nothing is found.
    """
    import lxml.etree
    import lxml.html

    try:
        doc = lxml.html.fromstring(body.decode("utf-8", errors="replace"))
    except (ValueError, lxml.etree.ParserError):
        return body
    parts = [lxml.html.tostring(el, encoding="unicode")
             for el in doc.xpath("//*[@id]") if el.get("id") in _KEEP_IDS]
    if not parts:
        return body
    html = ("<!doctype html><html><head><meta charset=\"utf-8\"></head><body>"
            + "\n".join(parts) + "</body></html>")
    return html.encode("utf-8")


def _save(path: Path, fetched: Fetched, slim: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(slim_html(fetched.body) if slim else fetched.body)
    path.with_suffix(".meta.json").write_text(json.dumps({
        "url": fetched.url, "statusCode": fetched.status, "retrieved_at": fetched.retrieved_at,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def months_between(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    (y, m), out = start, []
    while (y, m) <= end:
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def quarters_between(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int, int]]:
    """(year, first_month, last_month) of each calendar quarter clipped to [start, end]."""
    out: dict[tuple[int, int], list[int]] = {}
    for y, m in months_between(start, end):
        out.setdefault((y, (m - 1) // 3), []).append(m)
    return [(y, ms[0], ms[-1]) for (y, _), ms in out.items()]


def crawl(client: VksClient, out_dir: Path, start: tuple[int, int], end: tuple[int, int],
          words: str = "", act_type: str = "15", case_type: str = "гр.",
          chambers: list[str] | None = None) -> CrawlReport:
    """`chambers`: list each month and quarter per chamber instead of all chambers at once
    (commercial decisions before 2023 are not returned for AktVidDelo=търг., only per chamber;
    docs/source-discovery.md 1.9)."""
    report = CrawlReport()
    ids: dict[str, str] = {}

    def fetch_list(q: ListQuery, name: str) -> int:
        url = list_url(q)
        fetched = client.get(url)
        _save(out_dir / "lists" / f"{name}.html", fetched)
        rows = parse_list(fetched.body.decode("utf-8", errors="replace"))
        for r in rows:
            ids.setdefault(r.source_id, r.link_text)
        report.lists.append({"name": name, "url": url, "description": q.describe(),
                             "row_count": len(rows),
                             "truncated": len(rows) >= LIST_TRUNCATION_LIMIT,
                             "retrieved_at": fetched.retrieved_at})
        return len(rows)

    parts = [(c, f"__{_slug(c)}") for c in chambers] if chambers else [(None, "")]
    for y, m in months_between(start, end):
        for only, suffix in parts:
            base = ListQuery(y, m, y, m, act_type=act_type, case_type=case_type, words=words,
                             chamber=only)
            name = f"{y}-{m:02d}{suffix}"
            if fetch_list(base, name) < LIST_TRUNCATION_LIMIT:
                continue
            if only:
                report.truncated.append(name)
                continue
            for chamber in CHAMBERS:
                q = ListQuery(y, m, y, m, act_type=act_type, case_type=case_type, words=words,
                              chamber=chamber)
                part = f"{name}__{_slug(chamber)}"
                if fetch_list(q, part) < LIST_TRUNCATION_LIMIT:
                    continue
                # still full (seen for determinations): halves of the month, then ten-day parts
                last = calendar.monthrange(y, m)[1]
                for d1, d2 in ((1, 10), (11, 20), (21, last)):
                    dq = ListQuery(y, m, y, m, day_from=d1, day_to=d2, act_type=act_type,
                                   case_type=case_type, words=words, chamber=chamber)
                    if fetch_list(dq, f"{part}__d{d1:02d}-{d2:02d}") >= LIST_TRUNCATION_LIMIT:
                        report.truncated.append(f"{part}__d{d1:02d}-{d2:02d}")

    # Monthly lists were observed to omit decisions that a quarter list returns
    # (docs/source-discovery.md 1.2.1 p.5), so also list by quarter and merge by id.
    for y, q_start, q_end in quarters_between(start, end):
        if q_start == q_end:
            continue
        for only, suffix in parts:
            q = ListQuery(y, q_start, y, q_end, act_type=act_type, case_type=case_type, words=words,
                          chamber=only)
            name = f"{y}-Q{(q_start - 1) // 3 + 1}-{q_start:02d}-{q_end:02d}{suffix}"
            if fetch_list(q, name) < LIST_TRUNCATION_LIMIT:
                continue
            if only:
                report.truncated.append(name)
                continue
            for chamber in CHAMBERS:  # a truncated quarter list is split like a month list
                part_q = ListQuery(y, q_start, y, q_end, act_type=act_type, case_type=case_type,
                                   words=words, chamber=chamber)
                part = f"{name}__{_slug(chamber)}"
                # the quarter lists only add what the month lists miss; when a chamber's quarter is
                # still full, the month lists (split down to ten days) already cover it
                if fetch_list(part_q, part) >= LIST_TRUNCATION_LIMIT and words != "288":
                    report.truncated.append(part)

    for source_id, link_text in ids.items():
        path = out_dir / "acts" / f"{source_id}.html"
        entry = {"id": source_id, "link_text": link_text, "url": act_url(source_id),
                 "file": str(path.relative_to(out_dir))}
        if path.exists() and path.stat().st_size > 0:
            entry["ok"] = True
            entry["note"] = "already present"
        else:
            try:
                fetched = client.get(act_url(source_id))
                _save(path, fetched)
                entry.update(ok=True, retrieved_at=fetched.retrieved_at, status_code=200)
            except FetchError as exc:
                entry.update(ok=False, error=str(exc))
        report.acts.append(entry)

    (out_dir / "manifest.json").write_text(json.dumps({
        "created_at": datetime.now(timezone.utc).isoformat(),
        "query": {"source": "vks", "acquisition": "direct", "act_type": act_type,
                  "case_type": case_type, "chambers": chambers or [], "words": words,
                  "from": f"{start[0]}-{start[1]:02d}", "to": f"{end[0]}-{end[1]:02d}"},
        "lists": report.lists,
        "truncated": report.truncated,
        "acts": report.acts,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
