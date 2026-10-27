"""How old a price is, and what to say about it (M7.39).

Every price a holding is valued at carries the day it belongs to, and this says whether it is still
good (``fresh``) or ``old`` (types.ts ``Freshness``), with a sentence where a number alone would
mislead. The rule depends on what the price is:

* **A fund's NAV** is old once it is more than ``NAV_OLD_BUSINESS_DAYS`` (3) business days behind the
  latest trading day: AMFI publishes in the evening and a holiday or two is normal.
* **A value kept by hand** (and a loan's balance) is old once it is more than the tab's number of
  days old (``MANUAL_OLD_DAYS``, 7 to 365). Its age is always said.
* **A listed price** has no sentence while it is a tick (the screen's feed line speaks), but says
  so when the service took it from a quote or a closing record, and is old when it belongs to a
  session before the latest one.

``today`` is the IST day now and ``last_session_day`` the latest trading day whose session has
begun (the Friday, before the market opens on a Monday); the service works both out, because this
module knows neither the clock nor the exchange's calendar. The phone runs the same rules on the
vectors in tests/vectors/freshness.json.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .days import business_days_between, clock_of_instant, days_between
from .kinds import (
    FRESHNESS_STATES,
    MANUAL_OLD_DAYS,
    MANUAL_OLD_DAYS_MAX,
    MANUAL_OLD_DAYS_MIN,
    NAV_OLD_BUSINESS_DAYS,
)
from .words import DOT, MONTHS

FRESH, OLD = FRESHNESS_STATES
NO_PRICE = "No price yet"


@dataclass(frozen=True, slots=True)
class Freshness:
    state: str
    #: The sentence for the screen; None for a listed tick.
    words: str | None

    def as_dict(self) -> dict:
        return {"state": self.state, "words": self.words}


def when(day: int, near: int) -> str:
    """12 Sep, and 12 Sep 2025 when it is not in the year of ``near``."""
    text = f"{day % 100} {MONTHS[day // 100 % 100 - 1]}"
    return text if day // 10000 == near // 10000 else f"{text} {day // 10000}"


def nav(nav_day: int, last_session_day: int, holidays: Iterable[int] = ()) -> Freshness:
    """A fund's NAV dated ``nav_day``."""
    words = f"NAV of {when(nav_day, last_session_day)}"
    if business_days_between(nav_day, last_session_day, holidays) > NAV_OLD_BUSINESS_DAYS:
        return Freshness(OLD, f"{words}; AMFI has not published a newer one.")
    return Freshness(FRESH, words)


def manual(
    price_day: int, today: int, old_days: int = MANUAL_OLD_DAYS, noun: str = "Value"
) -> Freshness:
    """A value kept by hand, dated ``price_day``; ``noun`` is "Balance" for a loan."""
    age = max(0, days_between(price_day, today))
    limit = min(max(old_days, MANUAL_OLD_DAYS_MIN), MANUAL_OLD_DAYS_MAX)
    how_old = "today" if age == 0 else f"{age} day{'' if age == 1 else 's'} old"
    words = f"{noun} from {when(price_day, today)}{DOT}{how_old}"
    return Freshness(OLD if age > limit else FRESH, words)


def _listed(price: Mapping, today: int, last_session_day: int) -> Freshness:
    """A listed price: a tick, a quote or a closing record."""
    day, at = price["day"], price["at"]
    if day < last_session_day:
        return Freshness(OLD, f"Price of {when(day, today)}; no newer price yet.")
    if price["basis"] == "close":
        return Freshness(FRESH, f"Close of {when(day, today)}")
    if price["source"] == "quote":
        what = "quote"
    elif price["late"]:
        what = "closing record, taken late"
    elif day < today:
        what = "closing record"
    else:
        return Freshness(FRESH, None)
    parts = []
    if at is not None:
        parts.append(clock_of_instant(at))
    if day != today:
        parts.append(when(day, today))
    stamp = ", ".join(parts)
    return Freshness(FRESH, f"Last price {stamp} ({what})" if stamp else f"Last price ({what})")


def of_price(
    price: Mapping,
    *,
    today: int,
    last_session_day: int,
    old_days: int = MANUAL_OLD_DAYS,
    holidays: Iterable[int] = (),
    noun: str = "Value",
) -> Freshness:
    """How fresh a ``PriceInfo`` is, by what kind of price it is (its ``basis``)."""
    if price["price_u"] is None or price["day"] is None:
        return Freshness(OLD, NO_PRICE)
    if price["basis"] == "nav":
        return nav(price["day"], last_session_day, holidays)
    if price["basis"] == "manual":
        return manual(price["day"], today, old_days, noun)
    return _listed(price, today, last_session_day)
