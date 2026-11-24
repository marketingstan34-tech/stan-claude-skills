"""Labels shown and cited for a report: the appellate decision and interpretative decisions.

- A report made from an uploaded or pasted document was titled with the file name. The heading of
  the decision names the court, the case and the date, so the standard label is read from it
  (also for reports saved before this existed).
- Interpretative decisions were labelled with the year of the interpretative case
  ("Тълкувателно решение № 4/2014 на ОСГК"); they are cited with the decision date, read from the
  own database.
"""

from __future__ import annotations

import re
from pathlib import Path

from legal_ai.cassation.local import tr_label

GENERIC = ("Качен документ", "Поставен текст")
_OLD_TR = re.compile(r"^Тълкувателно решение № (\d{1,3})/(\d{4}) на (\S+)$")
_KINDS = [(re.compile(r"^в\.?\s*гр", re.I), "Въззивно гражданско дело"),
          (re.compile(r"^в\.?\s*т", re.I), "Въззивно търговско дело"),
          (re.compile(r"^ч\.?\s*гр", re.I), "Частно гражданско дело"),
          (re.compile(r"^ч\.?\s*т", re.I), "Частно търговско дело")]


def standard_label(text: str) -> str | None:
    """"Апелативен съд Пловдив, Въззивно гражданско дело № 553/2025, Решение от 03.08.2026"."""
    from legal_ai.cassation.noai import extract_case_header
    from legal_ai.upload import first_date
    head = extract_case_header(text or "")
    if not head:
        return None
    kind = head["kind"]
    for rx, full in _KINDS:
        if rx.match(kind):
            kind = full
            break
    if not kind.lower().endswith("дело"):
        kind = f"{kind} дело"
    when = first_date(text)
    return (f"{head['court']}, {kind[0].upper() + kind[1:]} № {head['number']}/{head['year']}, "
            f"Решение от {when.strftime('%d.%m.%Y') if when else '?'}")


def appellate_label(label: str, report_dir: Path) -> str:
    """The standard label for a report made from a document; other labels are kept."""
    if not (label or "").startswith(GENERIC):
        return label
    try:
        with open(report_dir / "appellate.txt", encoding="utf-8") as f:
            text = f.read(4000)
    except OSError:
        return label
    return standard_label(text) or label


def tr_relabel(labels: set[str], conn) -> dict[str, str]:
    """old label -> label with the decision date, for old-style interpretative decision labels."""
    out: dict[str, str] = {}
    if conn is None:
        return out
    with conn.cursor() as cur:
        for old in labels:
            m = _OLD_TR.match(old or "")
            if not m:
                continue
            cur.execute("""SELECT act_number, act_date, case_year, chamber FROM decisions
                           WHERE source = 'vks-tr' AND act_number = %s AND case_year = %s AND chamber = %s""",
                        (m[1], int(m[2]), m[3]))
            rows = cur.fetchall()
            if len(rows) == 1:
                r = rows[0]
                out[old] = tr_label(r["act_number"], r["act_date"], r["case_year"], r["chamber"])
    return out


def refresh_run(run: dict, report_dir: Path, conn=None, appeal: dict | None = None) -> None:
    """Apply both fixes to a loaded report (and its appeal draft) in memory."""
    run["appellate"]["label"] = appellate_label(run["appellate"]["label"], report_dir)
    labels = {a["label"] for a in run.get("assessments", [])}
    if appeal:
        labels |= {lab for g in appeal.get("grounds", []) for lab in g.get("vks_labels", [])}
    mapping = tr_relabel(labels, conn)
    if not mapping:
        return
    for a in run.get("assessments", []):
        a["label"] = mapping.get(a["label"], a["label"])
    if appeal:
        for g in appeal.get("grounds", []):
            g["vks_labels"] = [mapping.get(lab, lab) for lab in g.get("vks_labels", [])]
