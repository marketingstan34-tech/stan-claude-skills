"""Deadline and threshold checks for a cassation appeal (deterministic, no AI).

- Deadline: one month from service of the appellate decision (чл. 283 ГПК), counted by чл. 60 ГПК:
  it ends on the same date of the next month, or on the last day of that month if there is no such
  date (ал. 3); if that day is not a working day, on the next working day (ал. 6). Non-working days
  are Saturdays, Sundays and the official holidays of чл. 154 КТ, including the day after a holiday
  that falls on a weekend. Days moved by a decision of the Council of Ministers are not known here.
- Threshold (чл. 280, ал. 3, т. 1 ГПК): civil cases up to 5000 лв. and commercial cases up to
  20 000 лв. are not appealable, except claims for ownership and other real rights.

Both are shown as a check for the lawyer, never as a decision.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

BGN_PER_EUR = 1.95583
THRESHOLDS_BGN = {"граждански": 5000, "търговски": 20000}
_FIXED = [(1, 1), (3, 3), (5, 1), (5, 6), (5, 24), (9, 6), (9, 22)]
_CHRISTMAS = [(12, 24), (12, 25), (12, 26)]


def orthodox_easter(year: int) -> date:
    """Orthodox Easter Sunday (Julian computus, shown in the Gregorian calendar; 1900-2099)."""
    a, b, c = year % 4, year % 7, year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    month, day = divmod(d + e + 114, 31)
    return date(year, month, day + 1) + timedelta(days=13)


def holidays(year: int) -> set[date]:
    """Official holidays (чл. 154 КТ), with the weekend carry-over to the next working day."""
    easter = orthodox_easter(year)
    days = {easter + timedelta(days=k) for k in (-2, -1, 0, 1)}
    fixed = [date(year, m, d) for m, d in _FIXED + _CHRISTMAS]
    days |= set(fixed)
    for h in fixed:   # a holiday on a Saturday or Sunday moves to the next working day
        if h.weekday() >= 5:
            nxt = h + timedelta(days=1)
            while nxt.weekday() >= 5 or nxt in days:
                nxt += timedelta(days=1)
            days.add(nxt)
    return days


def is_working_day(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


@dataclass
class Deadline:
    served: date
    nominal: date     # one month after service (чл. 60, ал. 3)
    last_day: date    # moved to a working day if needed (чл. 60, ал. 6)
    moved: bool

    def days_left(self, today: date | None = None) -> int:
        return (self.last_day - (today or date.today())).days


def appeal_deadline(served: date) -> Deadline:
    nominal = add_months(served, 1)
    last = nominal
    while not is_working_day(last):
        last += timedelta(days=1)
    return Deadline(served, nominal, last, last != nominal)


@dataclass
class Threshold:
    ok: bool | None   # True: above the threshold or excepted; False: below; None: not enough data
    text: str


def threshold_check(amount: float | None, currency: str, kind: str, property_claim: bool) -> Threshold:
    if property_claim:
        return Threshold(True, "Иск за собственост или други вещни права: прагът по чл. 280, ал. 3, т. 1 не се прилага.")
    if amount is None or kind not in THRESHOLDS_BGN:
        return Threshold(None, "Въведете цената на иска и вида на делото.")
    bgn = amount * BGN_PER_EUR if currency == "EUR" else amount
    limit = THRESHOLDS_BGN[kind]
    shown = f"{amount:,.2f} {'€' if currency == 'EUR' else 'лв.'}".replace(",", " ")
    if currency == "EUR":
        shown += f" (= {bgn:,.2f} лв.)".replace(",", " ")
    if bgn <= limit:
        return Threshold(False, f"Цена на иска {shown} – до {limit:,} лв. за {kind} дела: решението по правило не подлежи "
                                "на касационно обжалване (чл. 280, ал. 3, т. 1 ГПК).".replace(",", " "))
    return Threshold(True, f"Цена на иска {shown} – над {limit:,} лв. за {kind} дела.".replace(",", " "))
