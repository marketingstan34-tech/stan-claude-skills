"""Data for the dashboard-style pages (layout from the owner's UI kit, spec §14.2)."""

from __future__ import annotations

import calendar
import json
import re
from datetime import date, datetime
from pathlib import Path

MONTHS = ["Януари", "Февруари", "Март", "Април", "Май", "Юни", "Юли", "Август", "Септември",
          "Октомври", "Ноември", "Декември"]
CARD_COLORS = ["peach", "blue", "pink"]
_LABEL = re.compile(r"^(?P<court>.+?), (?P<kind>[^,]*?)дело № (?P<case>\d+/\d{4}), Решение от (?P<date>[\d.?]+)")


def _short(label: str) -> dict:
    m = _LABEL.match(label)
    if not m:
        return {"court": label, "case": "", "kind": "", "date": ""}
    kind = m["kind"].strip().replace("Въззивно ", "в.").replace("гражданско", "гр.").replace("търговско", "т.")
    return {"court": m["court"], "case": m["case"], "kind": kind, "date": m["date"]}


def _when(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y")
    except ValueError:
        return iso[:10]


def _path_summary(path: list[dict]) -> dict:
    confirmed = [i for i in path if i.get("acts") and not i.get("note")]
    vks = next((i for i in path if i.get("level") == "ВКС"), None)
    if vks is None:
        vks_text = "ВКС: не е проверено"
    elif vks.get("result"):
        vks_text = "ВКС: " + " ".join(vks["result"].split()[:2])
    elif vks.get("note"):
        vks_text = "ВКС: няма дело"
    else:
        vks_text = "ВКС: висящо"
    levels = [{"short": {"първа": "I", "въззивна": "II", "ВКС": "ВКС"}.get(i.get("level"), "?"),
               "ok": bool(i.get("acts")) and not i.get("note")} for i in path]
    pct = round(100 * len(confirmed) / len(path)) if path else 0
    return {"percent": pct, "vks_text": vks_text, "levels": levels}


def list_reports(runs_dir: Path, traces_dir: Path) -> list[dict]:
    """All saved reports, newest first: no-AI traces and AI analyses."""
    out = []
    for kind, base, name in (("trace", traces_dir, "trace.json"), ("run", runs_dir, "run.json")):
        if not base.exists():
            continue
        for d in base.iterdir():
            f = d / name
            if not (d.is_dir() and d.name.isdigit() and f.exists()):
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                label = data["appellate"]["label"]
            except (OSError, ValueError, KeyError):
                continue   # a broken report must not break the pages that list reports
            item = {"id": d.name, "kind": kind, "url": f"/{'traces' if kind == 'trace' else 'runs'}/{d.name}",
                    "label": label, "created_at": data["created_at"], "created": _when(data["created_at"]),
                    **_short(label), **_path_summary(data.get("path") or [])}
            if kind == "trace":
                cites = data.get("citations", [])
                item["mode"] = "Без AI"
                item["detail"] = f"{len(cites)} цитирани акта на ВКС, {sum(1 for c in cites if c['decision_id'])} в базата"
            else:
                contra = sum(1 for a in data.get("assessments", []) if a["relevant"] and a["stance"] == "противоречи")
                item["mode"] = "С AI"
                item["detail"] = f"{len(data['analysis']['questions'])} въпроса, {contra} решения „противоречи“"
            out.append(item)
    out.sort(key=lambda r: r["created_at"], reverse=True)
    return out


def month_param(value: str | None, default: date) -> tuple[int, int]:
    if value and re.fullmatch(r"\d{4}-\d{2}", value):
        y, m = (int(x) for x in value.split("-"))
        if 2000 <= y <= 2100 and 1 <= m <= 12:
            return y, m
    return default.year, default.month


def empty_month(year: int, month: int) -> dict:
    return _month(year, month, {}, set())


def corpus_month(conn, year: int, month: int) -> dict:
    """Decisions in the corpus per day of a month, as calendar cells (kit colour classes)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT extract(day FROM act_date)::int AS day, source, count(*) AS n FROM decisions
            WHERE act_date >= %s AND act_date < %s AND current_version_id IS NOT NULL
            GROUP BY 1, 2""", (date(year, month, 1),
                               date(year + (month == 12), month % 12 + 1, 1)))
        rows = cur.fetchall()
    vks: dict[int, int] = {}
    tr: set[int] = set()
    for r in rows:
        if r["source"] == "vks-tr":
            tr.add(r["day"])
        else:
            vks[r["day"]] = vks.get(r["day"], 0) + r["n"]
    return _month(year, month, vks, tr)


def _month(year: int, month: int, vks: dict[int, int], tr: set[int]) -> dict:
    days = []
    for d in range(1, calendar.monthrange(year, month)[1] + 1):
        n = vks.get(d, 0)
        kind = ("yellow" if d in tr else "light" if n == 0 else "slate" if n < 5 else
                "medium-gray" if n < 12 else "charcoal")
        days.append({"day": d, "n": n, "tr": d in tr, "type": kind})
    prev = (year - 1, 12) if month == 1 else (year, month - 1)
    nxt = (year + 1, 1) if month == 12 else (year, month + 1)
    return {"year": year, "month": month, "name": f"{MONTHS[month - 1]} {year}", "days": days,
            "total": sum(vks.values()), "prev": f"{prev[0]}-{prev[1]:02d}", "next": f"{nxt[0]}-{nxt[1]:02d}"}


def corpus_events(storage: Path, limit: int = 5) -> list[dict]:
    """Latest quarters added to the corpus (data/raw/vks-corpus/progress.jsonl), newest first."""
    log = storage / "raw" / "vks-corpus" / "progress.jsonl"
    if not log.exists():
        return []
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
    out = []
    for e in reversed(lines):
        if not e.get("acts"):
            continue
        y, months = e["quarter"].split("-", 1)
        out.append({"title": f"ВКС · {'граждански' if e['case_type'] == 'гр.' else 'търговски'} дела",
                    "text": f"{y}, месеци {months.replace('..', '–')}: {e['ingested']} решения добавени",
                    "initials": "ГК" if e["case_type"] == "гр." else "ТК"})
        if len(out) >= limit:
            break
    return out
