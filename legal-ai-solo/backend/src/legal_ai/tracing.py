"""The path of a case: first instance -> appeal -> VKS (lawyer's requirement, spec §14.1).

Every link is looked up on an official site and shown with its source. A link that could
not be confirmed is reported as such, never inferred.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from urllib.parse import urlencode

from lxml import html as lxml_html

from legal_ai.http import FetchError, PoliteClient
from legal_ai.sources.courts import COURTS, CourtSite
from legal_ai.sources.courts.acts import acts_url, parse_acts

VKS = "https://www.vks.bg"

# Lower court of each supported appellate court (same city). It is used only when the appellate
# decision names that court (or an abbreviation that fits it) and the act date matches.
LOWER_COURT = {"as-plovdiv": "os-plovdiv", "os-plovdiv": "rs-plovdiv"}
_LEVEL = {"os-plovdiv": "окръжен", "rs-plovdiv": "районен"}
_CITY = {"os-plovdiv": "ПЛОВДИВ", "rs-plovdiv": "ПЛОВДИВ"}
# Stems of other court towns: the district courts and the district/regional courts in the
# Plovdiv appellate region (a reference naming any of them is another court).
_OTHER_TOWNS = ("БЛАГОЕВГР", "БУРГАС", "ВАРН", "ВЕЛИКО ТЪРН", "ВЕЛИКОТЪРН", "ВИДИН", "ВРАЦ", "ГАБРОВ", "ДОБРИЧ",
                "КЪРДЖАЛ", "КЮСТЕНДИЛ", "ЛОВЕЧ", "МОНТАН", "ПАЗАРДЖ", "ПЕРНИ", "ПЛЕВЕН", "ПЛЕВЕНС", "РАЗГРАД",
                "РУСЕ", "РУСЕНС", "СИЛИСТР", "СЛИВЕН", "СМОЛЯН", "СОФИ", "СТАРА ЗАГОР", "СТАРОЗАГОР", "ТЪРГОВИЩ",
                "ХАСКОВ", "ШУМЕН", "ЯМБОЛ", "АСЕНОВГР", "КАРЛОВ", "ПЪРВОМА", "ПЕЩЕР", "ВЕЛИНГРАД", "ПАНАГЮРИЩ",
                "ДЕВИН", "ЧЕПЕЛАР", "МАДАН", "ЗЛАТОГРАД", "КАЗАНЛЪК", "ЧИРПАН", "РАДНЕВ", "ГЪЛЪБОВ",
                "ДИМИТРОВГР", "ХАРМАНЛ", "СВИЛЕНГР", "ИВАЙЛОВГР", "ТОПОЛОВГР", "МОМЧИЛГР", "КРУМОВГР",
                "АРДИНО", "ДЖЕБЕЛ")


def lower_court_match(text: str, lower_key: str) -> bool | None:
    """Does the court named in the appellate decision fit `lower_key`?

    True: town and level written and both fit. False: another town or level is named, so the
    case must not be looked up there. None: abbreviated or missing (e.g. "О.С.-П."): the lookup
    is a candidate only and is confirmed by the act date.
    """
    t = " ".join((text or "").upper().replace("–", "-").split())
    if not t:
        return None
    level = _LEVEL[lower_key]
    is_os = "ОКРЪЖ" in t or re.search(r"(?<![А-Я])(?:[А-Я]{0,2})О\.?\s?С\.?(?![А-Я])", t) is not None
    is_rs = "РАЙОН" in t or re.search(r"(?<![А-Я])(?:[А-Я]{0,2})Р\.?\s?С\.?(?![А-Я])", t) is not None
    if (level == "окръжен" and is_rs and not is_os) or (level == "районен" and is_os and not is_rs):
        return False
    if any(town in t for town in _OTHER_TOWNS):
        return False
    town_ok = _CITY[lower_key] in t or re.search(r"(?<![А-Я])ПД\.?\s?[ОР]\.?\s?С", t) is not None \
        or re.search(r"(?<![А-Я])[ОР]\.?\s?С\.?\s?-?\s?ПД(?![А-Я])", t) is not None
    level_ok = is_os if level == "окръжен" else is_rs
    return True if town_ok and level_ok else None


def _same_date(want: str, got: str) -> bool:
    want = want.replace("г.", "").replace("г", "").strip()
    return bool(want and got) and (want.startswith(got) or got in want)


@dataclass
class Instance:
    level: str                 # "първа" | "въззивна" | "ВКС"
    court: str
    case: str
    acts: list[dict] = field(default_factory=list)   # {type, number, date, result, url}
    result: str = ""
    source_url: str = ""
    note: str = ""


def _cells(row) -> list[str]:
    return [" ".join(c.text_content().split()) for c in row.xpath("./div")]


def vks_case_search_url(prev_number: int, prev_court: str) -> str:
    return f"{VKS}/spisak-dela.jsp?" + urlencode({
        "DeloNo": "", "DeloNoGodina": "", "DeloVhNo": "", "DeloVhNoGodina": "",
        "DeloOtdelenie": "empty", "DeloVhNoInstancia": str(prev_number), "DeloInstancia": prev_court})


def parse_vks_case_list(page: str) -> list[dict]:
    """Rows of #TablicaRezultati on spisak-dela.jsp (structure verified 02.10.2026)."""
    doc = lxml_html.fromstring(page)
    out = []
    for row in doc.xpath('//*[@id="TablicaRezultati"]/div')[1:]:
        c = _cells(row)
        href = row.xpath(".//a/@href")
        if len(c) < 6 or not href or not href[0].startswith("pregled-delo.jsp?id="):
            continue
        out.append({"url": f"{VKS}/{href[0]}", "kind": c[1], "case": c[2], "chamber": c[3],
                    "prev_case": c[4], "prev_court": c[5]})
    return out


def parse_vks_case(page: str) -> dict:
    doc = lxml_html.fromstring(page)

    def table(tid: str) -> list[list[str]]:
        return [_cells(r) for r in doc.xpath(f'//*[@id="{tid}"]/div')]

    data = {r[0]: r[1] for r in table("DeloDanni") if len(r) >= 2}
    acts = []
    for row in doc.xpath('//*[@id="DeloAktove"]/div')[1:]:
        c = _cells(row)
        href = row.xpath(".//a/@href")
        if len(c) >= 3:
            num, _, d = c[1].partition("/")
            acts.append({"type": c[0], "number": num.strip(), "date": d.strip(), "result": c[2],
                         "url": f"{VKS}/{href[0]}" if href else ""})
    outcome = {r[0]: r[1] for r in table("DeloIzhod") if len(r) >= 2}
    return {"data": data, "acts": acts, "outcome": outcome}


_CASE_REF = re.compile(r"(\d{1,6})\s*/\s*(\d{4})")


def trace(courts: PoliteClient, vks: PoliteClient, court_key: str, number: int, year: int,
          appellate_date: date | None, lower: dict | None) -> list[Instance]:
    court = COURTS[court_key]
    path: list[Instance] = []

    # first instance, from the reference in the appellate decision, confirmed on the court site
    lower_key = LOWER_COURT.get(court_key)
    ref = _CASE_REF.search((lower or {}).get("case", "") or "")
    named = (lower or {}).get("court", "") or ""
    match = lower_court_match(named, lower_key) if lower_key else False
    if lower_key and ref and match is not False:
        lc: CourtSite = COURTS[lower_key]
        inst = Instance("първа", lc.name, f"{ref[1]}/{ref[2]}")
        try:
            url = acts_url(lc, int(ref[1]), int(ref[2]), "", "решение")
            rows = [r for r in parse_acts(courts.get(url).body.decode("utf-8", errors="replace"))
                    if r.case_number == int(ref[1]) and r.case_year == int(ref[2])]
            inst.source_url = url
            want = (lower or {}).get("date", "") or ""
            acts = [{"type": r.act_type, "number": "", "date": r.act_date.strftime("%d.%m.%Y") if r.act_date else "",
                     "result": "", "url": r.file_url or ""} for r in rows]
            if not rows:
                inst.note = f"Не е намерено в {lc.name} (възможно е друг съд от района)."
            elif not any(_same_date(want, a["date"]) for a in acts):
                # the same number and year can belong to another case: nothing is linked
                inst.note = (f"В {lc.name} има дело {ref[1]}/{ref[2]}, но датата на акта не съвпада с посочената "
                             "във въззивното решение (или липсва) – не е свързано. Проверете ръчно.")
            else:
                inst.acts = acts
                if match is None:
                    inst.note = (f"Съдът е посочен съкратено („{named or '—'}“); делото е намерено в {lc.name} "
                                 "със същия номер, година и дата на акта.")
        except FetchError as exc:
            inst.note = f"Справката не успя: {exc}"
        path.append(inst)
    elif lower:
        path.append(Instance("първа", lower.get("court", "?"), lower.get("case", "?"),
                             note="Съдът не се поддържа още за автоматична проверка."))

    path.append(Instance("въззивна", court.name, f"{number}/{year}",
                         acts=[{"type": "Решение", "number": "",
                                "date": appellate_date.strftime("%d.%m.%Y") if appellate_date else "",
                                "result": "", "url": ""}],
                         source_url=acts_url(court, number, year)))

    # VKS, by the appellate case number
    try:
        url = vks_case_search_url(number, court.name)
        rows = [r for r in parse_vks_case_list(vks.get(url).body.decode("utf-8", errors="replace"))
                if r["prev_case"] == f"{number}/{year}" and r["prev_court"] == court.name]
        if not rows:
            path.append(Instance("ВКС", "Върховен касационен съд", "-", source_url=url,
                                 note="Няма дело във ВКС по това въззивно дело (към днешна дата)."))
        for r in rows:
            case = parse_vks_case(vks.get(r["url"]).body.decode("utf-8", errors="replace"))
            path.append(Instance("ВКС", "Върховен касационен съд",
                                 f"{r['kind']} {r['case'] or '(без номер)'}, {r['chamber']}".strip(),
                                 acts=case["acts"], result=case["data"].get("Резултат от делото", ""),
                                 source_url=r["url"]))
    except FetchError as exc:
        path.append(Instance("ВКС", "Върховен касационен съд", "?", note=f"Справката не успя: {exc}"))
    return path


def as_dicts(path: list[Instance]) -> list[dict]:
    return [asdict(i) for i in path]
