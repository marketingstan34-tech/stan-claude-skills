"""Court websites on the shared justice.bg template and the ecase.justice.bg act files.

Only courts whose "Съдебни актове" page id was verified with a real request are listed (all 146
civil/commercial courts in the country, 03.10.2026).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ECASE_HOST = "ecase.justice.bg"


@dataclass(frozen=True)
class CourtSite:
    key: str
    name: str
    host: str
    acts_page: str  # page id of "Съдебни актове", differs per court
    level: str = ""   # апелативен | окръжен | градски | районен
    city: str = ""
    region: str = ""  # town of the appellate court above it


def _load() -> dict[str, CourtSite]:
    """All civil/commercial courts, verified 03.10.2026 (courts.json; docs/source-discovery.md §2.1.1).
    Plovdiv AS/OS/RS were first verified 02.10.2026 (§2.1) and have the same page ids."""
    data = json.loads((Path(__file__).with_name("courts.json")).read_text(encoding="utf-8"))
    return {c["key"]: CourtSite(c["key"], c["name"], c["host"], c["acts_page"], c["level"], c["city"], c["region"])
            for c in data["courts"]}


COURTS: dict[str, CourtSite] = _load()

CASE_TYPES = ("Гражданско", "Търговско")
ACT_KINDS = {"решение": "5001", "определение": "5002"}

ALLOWED_HOSTS = tuple(c.host for c in COURTS.values()) + (ECASE_HOST,)


def find_court(name: str) -> CourtSite | None:
    """The listed court with this name (e.g. "Окръжен съд Смолян"), ignoring case and spacing."""
    want = " ".join(name.lower().replace("–", " ").replace("-", " ").split())
    for c in COURTS.values():
        if " ".join(c.name.lower().replace("-", " ").split()) == want:
            return c
    return None
