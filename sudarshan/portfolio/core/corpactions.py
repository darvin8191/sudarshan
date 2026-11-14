"""Upstox's corporate actions as integers, and the two ways a ratio can be read (M7.36, M7.27).

Upstox lists a company's dividends, bonus issues, splits and rights issues by ISIN
(``GET /v2/fundamentals/{isin}/corporate-actions``): ``name``, ``expiry_date`` ("ex-date or
effective date", written like ``14 Aug 2025``), ``amount`` (a dividend's rupees a share), ``ratio``
(like ``1:1``, or null) and ``event_details`` (name and value pairs). ``parse_answer`` turns one
answer into events; a row it cannot read is left out and counted with its reason, never guessed.

**The ratio is kept as written and read two ways.** Upstox does not say which side of ``1:5`` is
new and which is held. ``ratio_num`` and ``ratio_den`` of an event are the two numbers in the
order Upstox wrote them. ``readings`` gives the reading SUDARSHAN proposes and the other one, both
in the terms the ledger and the tax core use:

    BONUS  num:den   ``num`` new shares for every ``den`` held
    SPLIT  num:den   every ``den`` shares become ``num``

Proposed: a bonus ``a:b`` is ``a`` new for ``b`` held (how Indian companies announce one); a split
``a:b`` is every ``a`` become ``b`` (one share into five is written 1:5). The owner sees the
reading as a sentence on his own quantity and can turn it round; nothing changes a lot before he
has accepted one of the two (design p7 6.3, owner decision 10).

Pure: no clock, no file, no network; every input is an argument.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from .days import days_between, is_day
from .kinds import CA_KINDS, PRICE_SCALE

MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
#: ``14 Aug 2025``, ``14 August 2025``, ``14-Aug-2025``; then ``2025-08-14``; then ``14-08-2025``.
DAY_WORDS = re.compile(r"(\d{1,2})[\s\-/]+([A-Za-z]{3,9})\.?,?[\s\-/]+(\d{4})")
DAY_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
DAY_DMY = re.compile(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})")
FULL_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
               "september", "october", "november", "december")  # fmt: skip
RATIO = re.compile(r"\s*(\d{1,6})\s*:\s*(\d{1,6})\s*")
#: Kinds whose ratio changes how many shares are held; only these ever reach a lot.
LOT_KINDS = ("bonus", "split")
#: Why a row was left out, counted per read.
SKIPS = ("kind", "date", "row")


def parse_day(text: Any) -> int | None:
    """Upstox's date as YYYYMMDD; None for anything that is not a real day."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    day = None
    if found := DAY_WORDS.fullmatch(text):
        month = _month(found.group(2).lower())
        day = None if month is None else _day(int(found.group(3)), month, int(found.group(1)))
    elif found := DAY_ISO.match(text):
        day = _day(int(found.group(1)), int(found.group(2)), int(found.group(3)))
    elif found := DAY_DMY.fullmatch(text):
        day = _day(int(found.group(3)), int(found.group(2)), int(found.group(1)))
    return day if day is not None and is_day(day) else None


def _month(word: str) -> int | None:
    for number, (short, full) in enumerate(zip(MONTHS, FULL_MONTHS, strict=True), start=1):
        if word in (short, full) or (short == "sep" and word == "sept"):
            return number
    return None


def _day(year: int, month: int, number: int) -> int | None:
    return year * 10000 + month * 100 + number if 1 <= month <= 12 and 1 <= number <= 31 else None


def parse_kind(name: Any) -> str | None:
    """One of ``CA_KINDS`` when the name carries that word ("Bonus", "Stock Split", "Rights
    Issue"); None for a kind SUDARSHAN does not know."""
    if not isinstance(name, str):
        return None
    words = re.findall(r"[a-z]+", name.lower())
    found = [kind for kind in CA_KINDS if kind in words or kind + "s" in words]
    return found[0] if len(found) == 1 else None


def parse_ratio(text: Any) -> tuple[int, int] | None:
    """``"1:5"`` as ``(1, 5)``, the order Upstox wrote; None when it is not two positive whole
    numbers."""
    found = RATIO.fullmatch(text) if isinstance(text, str) else None
    if found is None:
        return None
    first, second = int(found.group(1)), int(found.group(2))
    return (first, second) if first > 0 and second > 0 else None


def parse_amount(value: Any) -> int | None:
    """Rupees a share as ``amount_u`` (x 10^4, half away from zero); None for nothing, zero, a
    negative or anything that is not a number. A float is read through its text, never as binary."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number <= 0:
        return None
    return int((number * PRICE_SCALE).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def parse_details(items: Any) -> list[dict[str, str]]:
    """The name and value pairs as text, in Upstox's order; anything else is left out."""
    out = []
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, Mapping):
            continue
        name, value = item.get("name"), item.get("value")
        if isinstance(name, str) and name.strip() and value is not None:
            out.append({"name": name.strip(), "value": str(value).strip()})
    return out


def parse_row(isin: str, row: Any) -> tuple[dict[str, Any] | None, str | None]:
    """``(event, None)``, or ``(None, why)`` with why one of ``SKIPS``."""
    if not isinstance(row, Mapping):
        return None, "row"
    kind = parse_kind(row.get("name"))
    if kind is None:
        return None, "kind"
    ex_day = parse_day(row.get("expiry_date"))
    if ex_day is None:
        return None, "date"
    ratio = parse_ratio(row.get("ratio"))
    event = {
        "isin": isin,
        "kind": kind,
        "ex_day": ex_day,
        "amount_u": parse_amount(row.get("amount")),
        "ratio_num": ratio[0] if ratio else None,
        "ratio_den": ratio[1] if ratio else None,
        "details": parse_details(row.get("event_details")),
    }
    return event, None


def parse_answer(isin: str, payload: Any) -> dict[str, Any]:
    """``{"events": [...], "skipped": {why: count}}`` of one answer; an event listed twice is kept
    once. Raises ValueError when the answer is not Upstox's shape at all (no ``data`` list), so
    that a broken answer is never read as "no events" and never marks the old ones gone."""
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, list):
        raise ValueError("no data list")
    events, seen, skipped = [], set(), dict.fromkeys(SKIPS, 0)
    for row in data:
        event, why = parse_row(isin, row)
        if event is None:
            skipped[why] += 1
        elif identity(event) not in seen:
            seen.add(identity(event))
            events.append(event)
    return {"events": events, "skipped": skipped}


def identity(event: Mapping[str, Any]) -> str:
    """What makes an event the same event on the next read: the ISIN, the kind, the ex-date, the
    amount, the ratio as written and a dividend's type (an interim and a special one can share a
    day and an amount). The reader hashes this text; the other details may be reworded by Upstox
    without the event, and the owner's decision on it, becoming a new one."""
    sort = ""
    for item in event.get("details") or ():
        if "type" in item["name"].lower():
            sort = item["value"].lower()
            break
    ratio = "" if event.get("ratio_num") is None else f"{event['ratio_num']}:{event['ratio_den']}"
    amount = "" if event.get("amount_u") is None else str(event["amount_u"])
    return "|".join((event["isin"], event["kind"], str(event["ex_day"]), amount, ratio, sort))


def readings(kind: str, first: int | None, second: int | None) -> list[tuple[int, int]]:
    """The ways a bonus's or a split's ratio can be read as ``(num, den)``, the proposed one
    first; one reading when both numbers are the same, none when there is no ratio or the kind
    changes no lot."""
    if kind not in LOT_KINDS or not first or not second:
        return []
    proposed = (first, second) if kind == "bonus" else (second, first)
    return [proposed] if first == second else [proposed, (proposed[1], proposed[0])]


def swap(kind: str, first: int, second: int, num: int, den: int) -> tuple[int, int]:
    """The other way round: the reading that is not ``(num, den)``. ValueError when ``(num,
    den)`` is not a reading of this ratio at all."""
    ways = readings(kind, first, second)
    if (num, den) not in ways:
        raise ValueError("not a reading of this ratio")
    return ways[-1] if ways[0] == (num, den) else ways[0]


# ---- the nightly rotation (design p7 6.2) ----------------------------------------------------
#: An ISIN is read again once a week; one with an event this near (days ahead) is read nightly.
ROTATION_DAYS = 7
NEAR_DAYS = 14


def tonight(
    isins: list[str],
    read_days: Mapping[str, int | None],
    near: set[str],
    today: int,
    limit: int,
) -> list[str]:
    """The ISINs to read tonight, at most ``limit``: first those never read, then those with an
    event inside ``NEAR_DAYS``, then the ones last read ``ROTATION_DAYS`` or more ago, oldest
    first. ``read_days`` gives the day of each ISIN's last read (None or absent: never). A week's
    rotation of N instruments is about N / 7 a night."""
    known = sorted(set(isins))
    never = [isin for isin in known if read_days.get(isin) is None]
    read = [isin for isin in known if read_days.get(isin) is not None]
    soon = [isin for isin in read if isin in near and read_days[isin] < today]
    stale = sorted(
        (isin for isin in read if isin not in near
         and days_between(read_days[isin], today) >= ROTATION_DAYS),
        key=lambda isin: (read_days[isin], isin),
    )  # fmt: skip
    return (never + soon + stale)[: max(0, limit)]
