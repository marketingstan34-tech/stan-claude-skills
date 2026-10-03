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

# The court below each level; Sofia: the city court (СГС) and the district court (СОС) are both
# below the Sofia appellate court.
_BELOW = {"апелативен": ("окръжен", "градски"), "окръжен": ("районен",), "градски": ("районен",)}
_LEVEL_WORDS = re.compile(r"ОКРЪЖЕН|ОКРЪЖНИЯ|РАЙОНЕН|РАЙОННИЯ|ГРАДСКИ|ГРАДСКИЯ|СЪД|СЪДА|ГР\.?|ПО|ОПИСА|НА|"
                          r"(?<![А-Я])[ОР]\.?\s?С\.?(?![А-Я])|(?<=[А-Яа-я])[ОР]С(?![А-Я])")


def _norm(text: str) -> str:
    return " ".join((text or "").upper().replace("–", " ").replace("-", " ").replace("Ё", "Е").split())


def _city_forms(city: str) -> list[str]:
    """How a town appears in a court reference: "Стара Загора", "Старозагорски", "Пазарджишки"."""
    c = _norm(city)
    words = c.split()
    stem = c[:-2] if len(c) >= 7 else c[:-1] if len(c) >= 5 else c
    forms = {c, stem}
    if len(words) == 2:
        first = words[0][:-1] + "О" if words[0][-1] in "АО" else words[0]
        last = words[1][:-2] if len(words[1]) >= 6 else words[1][:-1]
        forms.add(first + last)
    return sorted(forms, key=len, reverse=True)


def _level_named(t: str) -> set[str]:
    out = set()
    if "ОКРЪЖ" in t or re.search(r"(?<![А-Я])[А-Я]{0,2}О\.?\s?С\.?(?![А-Я])|(?<=[А-Я][а-я])ОС\b", t):
        out.add("окръжен")
    if "РАЙОН" in t or re.search(r"(?<![А-Я])[А-Я]{0,2}Р\.?\s?С\.?(?![А-Я])", t):
        out.add("районен")
    if "ГРАДСКИ" in t or re.search(r"(?<![А-Я])СГС(?![А-Я])", t):
        out.add("градски")
    return out


def _subsequence(abbr: str, city: str) -> bool:
    """"ПД" fits "ПЛОВДИВ" (first letter equal, the rest in order)."""
    city = city.replace(" ", "")
    if not abbr or not city.startswith(abbr[0]):
        return False
    i = 1
    for ch in city[1:]:
        if i < len(abbr) and ch == abbr[i]:
            i += 1
    return i == len(abbr)


def resolve_lower_court(text: str, appellate: CourtSite) -> tuple[CourtSite | None, bool]:
    """The first-instance court named in the appellate decision: (court, written in full).

    A town written in full (or as its adjective) decides. An abbreviation ("О.С.-П.", "ПдОС") is
    resolved among the lower courts of the region; if several fit, the court in the appellate
    court's own town is taken only if it is one of them. Such a match is a candidate only: the
    trace links it only when the act date matches. (None, False) when nothing fits or another
    level is named.
    """
    levels = _BELOW.get(appellate.level, ())
    if not levels:
        return None, False
    raw = (text or "").replace("–", "-")
    t = _norm(raw)
    named = _level_named(raw.upper())
    if named and not named & set(levels):
        return None, False
    pool = [c for c in COURTS.values() if c.level in levels and (not named or c.level in named)]
    # 1) the town written in full or as an adjective, anywhere in the country
    best: tuple[int, CourtSite] | None = None
    for c in pool:
        for form in _city_forms(c.city):
            if re.search(r"(?<![А-Я])" + re.escape(form), t) and (best is None or len(form) > best[0]):
                best = (len(form), c)
                break
    if best:
        return best[1], True
    # 2) an abbreviation, within the appellate region
    region = [c for c in pool if c.region and c.region == (appellate.region or appellate.city)] or pool
    letters = _LEVEL_WORDS.sub(" ", raw.upper().replace(".", " ").replace("-", " "))
    abbrs = [w for w in letters.split() if w.isalpha() and len(w) <= 3]
    if not abbrs and t:
        return None, False
    fits = [c for c in region if any(_subsequence(a, _norm(c.city)) for a in abbrs)] if abbrs else region
    same_town = [c for c in fits if c.city == appellate.city]
    if len(fits) == 1:
        return fits[0], False
    if same_town:
        return same_town[0], False
    return None, False


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
    ref = _CASE_REF.search((lower or {}).get("case", "") or "")
    named = (lower or {}).get("court", "") or ""
    lc, exact = resolve_lower_court(named, court)
    if lc and ref:
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
                inst.note = f"Не е намерено в {lc.name}" + ("." if exact else " (възможно е друг съд от района).")
            elif not any(_same_date(want, a["date"]) for a in acts):
                # the same number and year can belong to another case: nothing is linked
                inst.note = (f"В {lc.name} има дело {ref[1]}/{ref[2]}, но датата на акта не съвпада с посочената "
                             "във въззивното решение (или липсва) – не е свързано. Проверете ръчно.")
            else:
                inst.acts = acts
                if not exact:
                    inst.note = (f"Съдът е посочен съкратено („{named or '—'}“); делото е намерено в {lc.name} "
                                 "със същия номер, година и дата на акта.")
        except FetchError as exc:
            inst.note = f"Справката не успя: {exc}"
        path.append(inst)
    elif lower:
        path.append(Instance("първа", lower.get("court", "?"), lower.get("case", "?"),
                             note="Съдът не е разпознат или не е в списъка за автоматична проверка."))

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
