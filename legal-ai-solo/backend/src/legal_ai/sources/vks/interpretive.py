"""Interpretative decisions (тълкувателни решения) of the VKS general assemblies, as PDFs.

URL pattern verified 02.10.2026 (search results and direct requests):
    https://www.vks.bg/talkuvatelni-dela-<college>/vks-<college>-tdelo-<year>-<n>-reshenie.pdf
Enumeration probes n = 1, 2, ... per year and college and stops after `gap` misses in a row,
so a renamed or missing file is reported as missing, never guessed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from legal_ai.http import FetchError, PoliteClient
from legal_ai.sources.vks import HOST

COLLEGES = {
    "osgtk": "ОСГТК (Общо събрание на Гражданска и Търговска колегия)",
    "osgk": "ОСГК (Общо събрание на Гражданска колегия)",
    "ostk": "ОСТК (Общо събрание на Търговска колегия)",
}

_MONTHS = {"януари": 1, "февруари": 2, "март": 3, "април": 4, "май": 5, "юни": 6, "юли": 7,
           "август": 8, "септември": 9, "октомври": 10, "ноември": 11, "декември": 12}
_DATE = re.compile(r"(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})", re.IGNORECASE)


def tr_url(college: str, year: int, n: int) -> str:
    if college not in COLLEGES:
        raise ValueError(college)
    return f"https://{HOST}/talkuvatelni-dela-{college}/vks-{college}-tdelo-{year}-{n}-reshenie.pdf"


def find_date(text: str) -> date | None:
    m = _DATE.search(text[:1500])
    if not m:
        return None
    try:
        return date(int(m[3]), _MONTHS[m[2].lower()], int(m[1]))
    except ValueError:
        return None


@dataclass
class TrFile:
    college: str
    year: int
    number: int
    url: str
    path: Path


def download_all(client: PoliteClient, out_dir: Path, years: range, gap: int = 2,
                 max_n: int = 15) -> tuple[list[TrFile], list[str]]:
    """Returns downloaded (or already present) files and a log of misses/errors."""
    files: list[TrFile] = []
    log: list[str] = []
    for college in COLLEGES:
        for year in years:
            misses = 0
            for n in range(1, max_n + 1):
                path = out_dir / college / f"{year}-{n}.pdf"
                url = tr_url(college, year, n)
                if path.exists() and path.stat().st_size > 0:
                    files.append(TrFile(college, year, n, url, path))
                    misses = 0
                    continue
                try:
                    f = client.get(url)
                except FetchError as exc:
                    misses += 1
                    if "HTTP 404" not in str(exc):
                        log.append(f"{url}: {exc}")
                    if misses >= gap:
                        break
                    continue
                if f.body[:5] != b"%PDF-":
                    log.append(f"{url}: не е PDF ({f.content_type})")
                    misses += 1
                    if misses >= gap:
                        break
                    continue
                misses = 0
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f.body)
                path.with_suffix(".meta.json").write_text(json.dumps(
                    {"url": url, "retrieved_at": f.retrieved_at}, ensure_ascii=False), encoding="utf-8")
                files.append(TrFile(college, year, n, url, path))
    return files, log
