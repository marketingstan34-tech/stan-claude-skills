"""URLs of the VKS act search, as observed in the live form on search.html."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

BASE = "https://www.vks.bg"

ACT_TYPES = {"решение": "15", "определение": "17", "разпореждане": "40"}
CASE_TYPES = {"гр.", "нак.", "търг."}
CHAMBERS = [
    "1-во гр.", "1-во нак.", "1-во тър.", "2-ро гр.", "2-ро нак.", "2-ро тър.",
    "3-то гр.", "3-то нак.", "4-то гр.", "4-А гр.", "4-Б гр.", "5-то гр.", "Петчленен състав",
]
COMMERCIAL_CHAMBERS = ["1-во тър.", "2-ро тър."]


@dataclass(frozen=True)
class ListQuery:
    year_from: int
    month_from: int
    year_to: int
    month_to: int
    day_from: int | None = None
    day_to: int | None = None
    act_type: str = "15"
    case_type: str = "гр."
    chamber: str | None = None
    words: str = ""
    annotation_phrases: str = ""

    def describe(self) -> str:
        days = f" дни {self.day_from or ''}–{self.day_to or ''}" if self.day_from or self.day_to else ""
        return (
            f"{self.year_from}-{self.month_from:02d}..{self.year_to}-{self.month_to:02d}{days}"
            f" вид={self.act_type} дело={self.case_type} отделение={self.chamber or 'всички'}"
            f" думи={self.words!r}"
        )


def list_url(q: ListQuery) -> str:
    params = {
        "AktNo": "",
        "AktNoOtGodina": str(q.year_from),
        "AktNoOtMesec": f"{q.month_from:02d}",
        "AktNoOtDen": str(q.day_from) if q.day_from else "",
        "AktNoDoGodina": str(q.year_to),
        "AktNoDoMesec": f"{q.month_to:02d}",
        "AktNoDoDen": str(q.day_to) if q.day_to else "",
        "AktVid": q.act_type,
        "AktVidDelo": q.case_type,
        "AktOtdelenie": q.chamber or "empty",
        "AktFraziVAnotacia": q.annotation_phrases,
        "AktDumiVSadarjanie": q.words,
    }
    return f"{BASE}/spisak-aktove.jsp?{urlencode(params)}"


def act_url(source_id: str) -> str:
    return f"{BASE}/pregled-akt.jsp?type=ot-spisak&id={source_id}"
