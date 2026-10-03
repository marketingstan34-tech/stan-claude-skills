"""The lawyer's own data for one report: service date, claim value, parties, chosen questions.

Kept as case.json next to the report in private storage (never in Git). Everything here is
entered or confirmed by the lawyer; suggestions read from the decision are marked as such.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

FIELDS = {   # name -> max length (text fields)
    "client": 200, "client_id": 40, "client_address": 300, "opponent": 200,
    "lawyer": 120, "lawyer_address": 300,
}
_AMOUNT = re.compile(r"(\d{1,3}(?:[  .]\d{3})+|\d+)(?:,(\d{1,2}))?\s*(лв\.?|лева|€|евро|EUR)", re.IGNORECASE)
_ACT_NO = re.compile(r"Р\s*Е\s*Ш\s*Е\s*Н\s*И\s*Е\s*(?:№|N)\s*(\d{1,7})")


STATUSES = ["нов", "в работа", "подадена жалба", "приключен"]


def set_status(run_dir: Path, status: str) -> bool:
    """Change only the status, keeping the rest of the case data."""
    if status not in STATUSES:
        return False
    data = load_case(run_dir)
    data["status"] = status
    save_case(run_dir, data)
    return True


def load_case(run_dir: Path) -> dict:
    try:
        data = json.loads((run_dir / "case.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_case(run_dir: Path, data: dict) -> None:
    tmp = run_dir / "case.json.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, run_dir / "case.json")


def parse_form(form: dict, question_ids: list[str]) -> tuple[dict, str]:
    """(clean data, error message) from the submitted form."""
    out: dict = {}
    served = (form.get("served") or "").strip()
    if served:
        try:
            out["served"] = date.fromisoformat(served).isoformat()
        except ValueError:
            return {}, "Датата на връчване не е валидна."
    amount = (form.get("amount") or "").replace(" ", "").replace(" ", "").replace(",", ".").strip()
    if amount:
        try:
            out["amount"] = round(float(amount), 2)
        except ValueError:
            return {}, "Цената на иска трябва да е число, напр. 25000 или 12 500,50."
        if not 0 <= out["amount"] < 1e12:
            return {}, "Цената на иска е извън допустимите стойности."
    out["currency"] = "EUR" if form.get("currency") == "EUR" else "BGN"
    out["kind"] = form.get("kind") if form.get("kind") in ("граждански", "търговски") else ""
    out["property"] = form.get("property") == "1"
    for name, limit in FIELDS.items():
        out[name] = (form.get(name) or "").strip()[:limit]
    chosen = form.get("questions")
    if isinstance(chosen, list):
        out["questions"] = [q for q in chosen if q in question_ids]
    return out, ""


def rank_questions(run: dict) -> list[str]:
    """Question ids, strongest first: contradicting VKS practice found, then the AI's own order."""
    contra: dict[str, int] = {}
    for a in run.get("assessments", []):
        if a.get("relevant") and a.get("stance") == "противоречи":
            contra[a["question_id"]] = contra.get(a["question_id"], 0) + 1
    qs = run.get("analysis", {}).get("questions", [])
    order = {q["id"]: i for i, q in enumerate(qs)}
    return sorted((q["id"] for q in qs),
                  key=lambda qid: (-min(contra.get(qid, 0), 3), order[qid]))


def default_questions(run: dict) -> list[str]:
    """Pre-selected: up to 3 questions with contradicting practice, else the first 2."""
    contra = {a["question_id"] for a in run.get("assessments", [])
              if a.get("relevant") and a.get("stance") == "противоречи"}
    ranked = rank_questions(run)
    with_practice = [q for q in ranked if q in contra][:3]
    return with_practice or ranked[:2]


def suggest_amount(text: str) -> tuple[float, str] | None:
    """The largest sum written in the decision (a hint for the claim value, to be checked)."""
    best: tuple[float, str] | None = None
    for m in _AMOUNT.finditer(text or ""):
        whole = re.sub(r"[  .]", "", m.group(1))
        value = float(whole + "." + (m.group(2) or "0"))
        cur = "EUR" if m.group(3).lower() in ("€", "евро", "eur") else "BGN"
        if best is None or value > best[0]:
            best = (value, cur)
    return best


_REG_NO = re.compile(r"Рег\.?\s*(?:№|N)\s*(\d{1,7})\s*/\s*\d{1,2}\.\d{1,2}\.\d{4}")


def decision_number(text: str) -> str:
    """"РЕШЕНИЕ № 125" or, in the court sites' files, "Рег.№ 163 / 03.08.2026" above the heading."""
    head = (text or "")[:400]
    m = _ACT_NO.search(head) or _REG_NO.search(head)
    return m.group(1) if m else ""


def case_kind(label_or_text: str) -> str:
    t = (label_or_text or "").lower()
    return "търговски" if ("търговско" in t or "т.д." in t.replace(" ", "")) else "граждански" if t else ""


def steps(rtype: str, rid: str, case: dict, has_appeal: bool, edited: set[str]) -> list[dict]:
    """What is done and what is left on a case, in working order; each step links to where it is done.

    `rtype`: "run" (AI report) or "trace" (no-AI report); `edited`: {"draft", "appeal"} edited by the lawyer.
    """
    base = f"/{'runs' if rtype == 'run' else 'traces'}/{rid}"
    ai = rtype == "run"
    out = [
        {"label": "Справка с AI (въпроси и практика)", "done": ai, "href": "/analyze"},
        {"label": "Дата на връчване (срок)", "done": bool(case.get("served")), "href": f"{base}#case-data"},
        {"label": "Праг по чл. 280, ал. 3", "done": case.get("amount") is not None or bool(case.get("property")),
         "href": f"{base}#case-data"},
    ]
    if ai:
        out += [
            {"label": "Отметнати въпроси", "done": bool(case.get("questions")), "href": f"{base}#case-data"},
            {"label": "Страни и адвокат", "done": bool(case.get("client") and case.get("lawyer")),
             "href": f"{base}#case-data"},
            {"label": "Изложение прегледано", "done": "draft" in edited, "href": f"{base}/draft/edit"},
            {"label": "Касационна жалба написана", "done": has_appeal, "href": f"{base}/appeal"},
            {"label": "Жалба прегледана", "done": "appeal" in edited,
             "href": f"{base}/appeal/edit" if has_appeal else f"{base}/appeal"},
        ]
    out.append({"label": "Подадена", "done": case.get("status") in ("подадена жалба", "приключен"), "href": base})
    return out
