"""Court websites on the shared justice.bg template and the ecase.justice.bg act files.

Only courts whose "Съдебни актове" page id was verified with a real request are listed.
"""

from __future__ import annotations

from dataclasses import dataclass

ECASE_HOST = "ecase.justice.bg"


@dataclass(frozen=True)
class CourtSite:
    key: str
    name: str
    host: str
    acts_page: str  # page id of "Съдебни актове", differs per court


# Verified 02.10.2026 (docs/source-discovery.md §2.1).
COURTS: dict[str, CourtSite] = {
    "as-plovdiv": CourtSite("as-plovdiv", "Апелативен съд Пловдив", "plovdiv-as.justice.bg", "2465"),
    "os-plovdiv": CourtSite("os-plovdiv", "Окръжен съд Пловдив", "plovdiv-os.justice.bg", "3935"),
    # from the court's sitemap, 02.10.2026 (used for first-instance lookups)
    "rs-plovdiv": CourtSite("rs-plovdiv", "Районен съд Пловдив", "plovdiv-rs.justice.bg", "9885"),
}

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
