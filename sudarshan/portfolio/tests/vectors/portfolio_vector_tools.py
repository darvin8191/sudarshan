"""Helpers the vector generators share (make_pa*.py); like them, imports nothing from sudarshan.

Exact rounding done two ways, the owner's words written again from scratch (so a sentence in a
vector is not a copy of the code's), and the shape of a ledger case.
"""

import json
import re
from decimal import ROUND_HALF_UP, Decimal, getcontext
from fractions import Fraction
from pathlib import Path

getcontext().prec = 80
RUPEE = chr(0x20B9)
DOT = " " + chr(0xB7) + " "
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
U = 10**6  # one unit
#: What the cases call the accounts and the holding in their sentences (ids are text in JSON).
NAMES = {"accounts": {"1": "Zerodha", "2": "Account B"}, "assets": {"1": "RELIANCE"}, "units": {}}


# ---------------------------------------------------------------- exact rounding, two ways
def half_away_fraction(x: Fraction) -> int:
    s = -1 if x < 0 else 1
    return s * int((abs(x) + Fraction(1, 2)) // 1)


def half_away_decimal(n: int, d: int) -> int:
    q = (Decimal(n) / Decimal(d)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(q)


def rd(n: int, d: int) -> int:
    a, b = half_away_fraction(Fraction(n, d)), half_away_decimal(n, d)
    assert a == b, (n, d, a, b)
    return a


# ---------------------------------------------------------------- independent words
def group_in(n: int) -> str:
    s = str(abs(n))
    if len(s) > 3:
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", s[:-3])
        s = head + "," + s[-3:]
    return ("-" if n < 0 else "") + s


def inr(paise: int) -> str:
    r, p = divmod(abs(paise), 100)
    return ("-" if paise < 0 else "") + RUPEE + group_in(r) + "." + f"{p:02d}"


def price_text(price_u: int) -> str:
    r, p = divmod(price_u, 10000)
    frac = f"{p:04d}".rstrip("0")
    frac = frac + "0" * (2 - len(frac)) if len(frac) < 2 else frac
    return RUPEE + group_in(r) + "." + frac


def qty_text(qty_u: int) -> str:
    r, p = divmod(qty_u, 10**6)
    frac = f"{p:06d}".rstrip("0")
    return group_in(r) + ("." + frac if frac else "")


def day_text(day: int) -> str:
    return f"{day % 100} {MONTHS[day // 100 % 100 - 1]} {day // 10000}"


def pct_text(scaled: int) -> str:
    """A percentage counted in ten-thousandths of a percent as text: 80000 -> "8.0000"."""
    whole, part = divmod(abs(scaled), 10_000)
    return ("-" if scaled < 0 else "") + f"{whole}.{part:04d}"


def pct4(numerator: int, denominator: int):
    """``numerator / denominator`` as a percentage with 4 decimals (half away from zero), or None."""
    if denominator == 0:
        return None
    return pct_text(rd(numerator * 100 * 10_000, denominator))


# ---------------------------------------------------------------- the calendar, worked out again
def days_from_civil(year: int, month: int, day: int) -> int:
    """Days since 1 Jan 1970 (Howard Hinnant's civil-days algorithm)."""
    year -= month <= 2
    era = year // 400
    yoe = year - era * 400
    doy = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def civil_days(day: int) -> int:
    return days_from_civil(day // 10000, day // 100 % 100, day % 100)


def weekday(day: int) -> int:
    """0 for Monday to 6 for Sunday (1 Jan 1970 was a Thursday)."""
    return (civil_days(day) + 3) % 7


def epoch_ms(day: int, hour: int, minute: int, second: int, millis: int = 0) -> int:
    """The instant of an IST wall-clock time on ``day``, in epoch milliseconds."""
    seconds = civil_days(day) * 86400 + hour * 3600 + minute * 60 + second
    return (seconds - 19800) * 1000 + millis


def business_days(start: int, end: int, holidays=()) -> int:
    """Weekdays after ``start`` up to and including ``end``, counted one day at a time."""
    count = 0
    for step in range(1, civil_days(end) - civil_days(start) + 1):
        day = _next_days(start, step)
        if weekday(day) < 5 and day not in holidays:
            count += 1
    return count


def _next_days(day: int, step: int) -> int:
    """The day ``step`` days after ``day`` as YYYYMMDD."""
    z = civil_days(day) + step + 719468
    era = z // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + (3 if mp < 10 else -9)
    y = yoe + era * 400 + (m <= 2)
    return y * 10000 + m * 100 + d


# ---------------------------------------------------------------- the shape of a ledger case
def e(i, kind, day, account, asset, qty_u=None, amount=None, **more):
    """An entry row in the contract's names; ``seq`` is the id, so entry order is id order."""
    row = {"id": i, "seq": i, "kind": kind, "trade_date": day, "account_id": account}
    if asset is not None:
        row["asset_id"] = asset
    if qty_u is not None:
        row["qty_u"] = qty_u
    if amount is not None:
        row["amount_paise"] = amount
    row.update(more)
    return row


def mv(entry, qty_u, cost, gain, paise, used=(), account=None):
    """A move of a replay; its account is the entry's unless a transfer says which side."""
    return {
        "entry": entry,
        "account": account,
        "qty_u": qty_u,
        "cost_paise": cost,
        "gain_paise": gain,
        "paise": paise,
        "used": [list(u) for u in used],
    }


def pos(qty_u, cost, lots, realised=0, income=0, fees=0, over_year=None):
    """A position; ``over_year`` (units held for more than a year) only when the case asks."""
    out = {
        "qty_u": qty_u,
        "cost_paise": cost,
        "realised_paise": realised,
        "income_paise": income,
        "fees_paise": fees,
        "lots": [list(lot) for lot in lots],
    }
    if over_year is not None:
        out["over_year_qty_u"] = over_year
    return out


def totals(realised=0, income=0, fees=0):
    return {"realised_paise": realised, "income_paise": income, "fees_paise": fees}


def refused(entry, reason, message, **facts):
    """The refusal a case expects: the entry named, why, the sentence and the numbers behind it."""
    return {"refused": {"entry": entry, "reason": reason, "message": message, "facts": facts}}


def add_case(cases, cid, title, why, entries, expect, **more):
    """Append a case: ``names``, ``decimals``, ``as_of`` and ``over_year_on`` go in as given;
    ``words`` (entry id to sentence) goes inside the expectation."""
    words = more.pop("words", None)
    if "refused" not in expect:
        by_id = {row["id"]: row for row in entries}
        for move in expect["moves"]:
            if move["account"] is None:
                move["account"] = by_id[move["entry"]]["account_id"]
        if words:
            expect = {**expect, "words": {str(k): v for k, v in words.items()}}
    case = {"id": cid, "title": title, "why": why, "entries": entries, "expect": expect}
    case.update({key: value for key, value in more.items() if value})
    cases.append(case)


def write_all(built: dict, here: Path) -> None:
    """Write each ``{file name: data}`` as indented JSON next to the generator."""
    for name, data in built.items():
        text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
        (here / name).write_text(text, encoding="utf-8")
