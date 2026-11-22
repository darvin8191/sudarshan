"""Event operands (M9.13, gap audit GA8.3): trading days to a share's next ex-date and to a
contract's expiry, counted on the exchange's calendar.

Pure, like the rest of the engine: the run's sessions arrive in ``Context.trading_days``, the
instrument's dates in ``Subject`` (``expiry``, ``ex_dates``); nothing here opens a file, reads a
clock or asks a service. ``ahead`` builds the sessions from a calendar's own predicate.

* **A count of sessions.** The sessions after the run's own up to the one the event falls in: 0 on
  that session, 1 when it is the next. A holiday or a weekend is no session.
* **A day the exchange is shut.** An ex-date on it takes effect at the first session after it,
  when the share first trades without the entitlement (a Saturday's ex-date is Monday's). An
  expiry on it is the session before it, as the exchange moves an expiry off a holiday.
* **Not known is not a number.** A share whose corporate actions SUDARSHAN does not read, an
  instrument with no expiry, a contract that expires past the days counted (``REACH_DAYS``), or a
  run without the exchange's days gives a reason (``NO_EVENTS``): "Not checked", never a miss.
* **None listed is a number.** A share whose actions were read and list none still to come within
  the days counted counts ``NONE_LISTED`` sessions: never within a screen's few days, always past
  them, shown as "none listed".
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Sequence
from dataclasses import fields, is_dataclass
from datetime import date, timedelta
from typing import Any

from .types import NO_EVENTS, Event, Why

#: Sessions counted for "none listed", and how far ``ahead`` reaches (calendar days).
NONE_LISTED = 9999
REACH_DAYS = 400
#: An OpenAlgo future or option symbol: ``NIFTY24NOV26FUT``, ``SBIN24NOV26800CE``.
_CONTRACT = re.compile(
    r"^(?P<under>[A-Z0-9&-]+?)(?P<dd>\d{2})(?P<mon>[A-Z]{3})(?P<yy>\d{2})(?P<rest>.*)(FUT|CE|PE)$"
)
_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def ahead(day: date, is_trading_day: Callable[[date], bool], reach: int = REACH_DAYS) -> tuple:
    """The sessions from ``day`` on (``day`` itself when it trades) for ``reach`` calendar days,
    as YYYYMMDD, by the calendar's own word on each day."""
    out = []
    for step in range(reach):
        when = day + timedelta(days=step)
        if is_trading_day(when):
            out.append(when.year * 10000 + when.month * 100 + when.day)
    return tuple(out)


def expiry_of_symbol(symbol: object) -> int | None:
    """A future's or an option's expiry from its OpenAlgo symbol, as YYYYMMDD; None for anything
    else (a share, an index, a symbol that is not one)."""
    found = _CONTRACT.match(symbol) if isinstance(symbol, str) else None
    if found is None or found["mon"] not in _MONTHS:
        return None
    year, month, day = 2000 + int(found["yy"]), _MONTHS.index(found["mon"]) + 1, int(found["dd"])
    try:
        date(year, month, day)  # a real day, or none
    except ValueError:
        return None
    return year * 10000 + month * 100 + day


def contract_expiry(symbol: object, segment: object) -> int | None:
    """The expiry of a universe's member, the service's and the replay's one rule: a future's or
    an option's (a derivatives segment, ``NSE_FO``, ``BSE_FO``) from its symbol; None for a
    share or an index, whatever its symbol looks like."""
    if not isinstance(segment, str) or not segment.endswith("_FO"):
        return None
    return expiry_of_symbol(symbol)


def ex_days(ex_dates: Sequence[tuple[str, int]] | None, kinds: Sequence[str],
            days: Sequence[int], previous: int = 0) -> int | Why:  # fmt: skip
    """Sessions to the next ex-date of one of ``kinds``: the first session on or after it. An
    ex-date before the run's session counts as this session only while no session lies between
    (``previous``, the session before it, is known and earlier); otherwise it is past."""
    if not days:
        return Why(NO_EVENTS, ("calendar",))
    if ex_dates is None:
        return Why(NO_EVENTS, ("ex_dates",))
    found = NONE_LISTED
    for kind, day in ex_dates:
        if kind not in kinds:
            continue
        if day < days[0]:
            at = 0 if previous and previous < day else None
        else:
            at = bisect_left(days, day)
            at = at if at < len(days) else None
        if at is not None:
            found = min(found, at)
    return found


def expiry_days(expiry: int | None, days: Sequence[int]) -> int | Why:
    """Sessions to a contract's expiry: the last session on or before its day. A contract whose
    expiry is before the run's session is not one any more; one past the days counted is not
    counted (the holiday list does not reach that far)."""
    if not days:
        return Why(NO_EVENTS, ("calendar",))
    if expiry is None or expiry < days[0]:
        return Why(NO_EVENTS, ("expiry",))
    if expiry > days[-1]:
        return Why(NO_EVENTS, ("far",))
    return bisect_right(days, expiry) - 1


def reads_ex_dates(plan: Any) -> bool:
    """Whether a compiled plan's lines or its rank count days to an ex-date: what the replay check
    cannot judge, a recording holding prices and not the portfolio's ex-dates."""
    return _ex_in((plan.clauses, plan.rank))


def _ex_in(value: Any) -> bool:
    if isinstance(value, Event):
        return value.what == "ex_days"
    if isinstance(value, tuple | list):
        return any(_ex_in(item) for item in value)
    if is_dataclass(value) and not isinstance(value, type):
        return any(_ex_in(getattr(value, f.name)) for f in fields(value))
    return False


__all__ = ["NONE_LISTED", "REACH_DAYS", "ahead", "contract_expiry", "ex_days", "expiry_days",
           "expiry_of_symbol", "reads_ex_dates"]  # fmt: skip
