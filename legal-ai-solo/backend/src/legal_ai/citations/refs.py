"""VKS acts cited in a text, as "number/dd.mm.yyyy" (e.g. "решение № 228 от 01.10.2014 г. по гр. д.
№ 1060/2014 г. на I г.о."). Acts of other courts (the appealed decision, the first instance) are left out:
a VKS act is cited with its chamber or the court's name right after the case number."""

from __future__ import annotations

import re
from datetime import date

VKS_REF = re.compile(r"(?:решение|определение)\s*№\s*(\d{1,6})\s*(?:/|от)\s*(\d{1,2}\.\d{1,2}\.\d{4})", re.IGNORECASE)
_VKS_TAIL = re.compile(r"ВКС|Върховния\s+касационен|[IV]+\s*-?\s*р?[аио]?\s*[гт]\.\s*о\.|\b[ГТ]К\b|ОСГ[ТК]?К")
_OTHER_COURT = re.compile(r"(?:Окръжен|Апелативен|Районен|Софийски\s+градски|административен)\s+съд|\b[ОАР]С\b|СГС")


def cited_refs(text: str, cutoff: date | None = None) -> set[str]:
    """VKS acts cited in the text; with `cutoff`, only those dated up to it."""
    out = set()
    for m in VKS_REF.finditer(text or ""):
        tail = text[m.end(): m.end() + 160]
        cut = _OTHER_COURT.search(tail)
        vks = _VKS_TAIL.search(tail)
        if not vks or (cut and cut.start() < vks.start()):
            continue
        dd, mm, yy = m.group(2).split(".")
        try:
            d = date(int(yy), int(mm), int(dd))
        except ValueError:
            continue
        if cutoff and d > cutoff:
            continue
        out.add(f"{int(m.group(1))}/{int(dd):02d}.{int(mm):02d}.{yy}")
    return out


def ref_date(ref: str) -> date:
    no, d = ref.split("/")
    dd, mm, yy = d.split(".")
    return date(int(yy), int(mm), int(dd))
