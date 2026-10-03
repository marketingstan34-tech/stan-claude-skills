"""'Съдебни актове' search on a court site: URL, result rows and the act file link.

Party names in the result table are NOT anonymised on these sites, so they are not parsed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlencode, urlparse, parse_qs

from lxml import html as lxml_html

from legal_ai.sources.courts import ACT_KINDS, CASE_TYPES, ECASE_HOST, CourtSite


def acts_url(court: CourtSite, case_number: int, case_year: int, case_type: str = "",
             act_kind: str = "решение") -> str:
    if case_type and case_type not in CASE_TYPES:
        raise ValueError(f"Неподдържан вид дело: {case_type}")
    params = {
        "from": "", "to": "",
        "actkindcode": ACT_KINDS.get(act_kind, ""),
        "casenumber": str(case_number), "caseyear": str(case_year),
        "casetype": case_type,
    }
    return f"https://{court.host}/bg/{court.acts_page}?{urlencode(params)}"


@dataclass(frozen=True)
class ActRow:
    case_kind: str          # e.g. "Въззивно търговско дело"
    case_number: int
    case_year: int
    judge: str
    act_type: str           # "Решение", "Определение", ...
    act_date: date | None
    file_url: str | None    # https://ecase.justice.bg/act/getactpublicfile?guid=...

    @property
    def guid(self) -> str | None:
        if not self.file_url:
            return None
        return parse_qs(urlparse(self.file_url).query).get("guid", [None])[0]


_NUM_YEAR = re.compile(r"^\s*(\d+)\s*/\s*(\d{4})\s*$")
_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _date(s: str) -> date | None:
    m = re.match(r"^\s*(\d{2})\.(\d{2})\.(\d{4})\s*$", s)
    return date(int(m[3]), int(m[2]), int(m[1])) if m else None


def parse_acts(page_html: str) -> list[ActRow]:
    """Rows of table.results-table. Columns (verified): №, вид дело, номер/година,
    ищец, ответник, съдия, вид акт, дата, файл, мотиви."""
    doc = lxml_html.fromstring(page_html)
    rows: list[ActRow] = []
    for tr in doc.xpath('//table[contains(@class,"results-table")]//tr[contains(@class,"table-data")]'):
        tds = tr.xpath("./td")
        if len(tds) < 9:
            continue
        cell = [" ".join(td.text_content().split()) for td in tds]
        m = _NUM_YEAR.match(cell[2])
        if not m:
            continue
        link = None
        for href in tds[8].xpath(".//a/@href"):
            p = urlparse(href)
            guid = parse_qs(p.query).get("guid", [""])[0]
            if p.scheme == "https" and p.hostname == ECASE_HOST and _GUID.match(guid):
                link = f"https://{ECASE_HOST}/act/getactpublicfile?guid={guid}"
                break
        rows.append(ActRow(cell[1], int(m[1]), int(m[2]), cell[5], cell[6], _date(cell[7]), link))
    return rows
