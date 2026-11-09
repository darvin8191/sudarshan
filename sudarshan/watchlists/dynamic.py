"""The rules of the rows that follow the money (M8.10, W14): expiry, ATM, its band and the roll.

Pure: every input is an argument, there is no clock, no I/O and no logging. Times are epoch
milliseconds; an expiry is an ISO date (``2026-11-24``); a strike is a number in rupees. The
bridge looks the session closes up in OpenAlgo's market calendar and passes them in as
``closes`` (date to epoch ms); a date with no entry closes at 15:30 IST. Arithmetic on strikes is
exact (fractions), so a price on a band edge is never moved by a float.
"""

from __future__ import annotations

from datetime import UTC, datetime
from fractions import Fraction
from typing import Any, NamedTuple

BAND_PAD = Fraction(1, 10)
#: 15:30 IST is 10:00 UTC.
_DEFAULT_CLOSE_UTC = (10, 0)


class Price(NamedTuple):
    """The price an underlying is judged on, and where it came from."""

    value: float | None
    at_ms: int | None
    source: str | None  # tick, snapshot, feed, last
    state: str  # ok, closed, waiting


class Resolution(NamedTuple):
    """What a follow row points at now (the contract itself is looked up by the bridge)."""

    state: str  # ok, waiting, closed, missing
    expiry: str | None
    strike: float | None
    band: tuple[float | None, float | None] | None
    valid_until: int | None
    price: Price
    why: str | None  # no_expiry, past_end, no_strike, no_side, no_price
    missing: tuple[str, ...] = ()
    steps: int | None = None


def close_ms(expiry: str, closes: dict[str, int] | None = None) -> int:
    """The session close of an expiry day: the calendar's, else 15:30 IST."""
    if closes and expiry in closes:
        return closes[expiry]
    y, m, d = (int(p) for p in expiry.split("-"))
    base = datetime(y, m, d, *_DEFAULT_CLOSE_UTC, tzinfo=UTC)
    return int(base.timestamp() * 1000)


def _month_ends(expiries: list[str]) -> set[str]:
    last: dict[str, str] = {}
    for e in expiries:
        last[e[:7]] = e
    return set(last.values())


def pick_expiries(
    expiries: list[str], now_ms: int, closes: dict[str, int] | None = None
) -> dict[str, str | None]:
    """``nearest``, ``next``, ``month`` and ``next_month`` among the listed expiries.

    An expiry is open while its session close is after ``now_ms`` (so the row rolls at the close
    of expiry day, 15:30:00.000 sharp); ``month`` is the first open expiry that is the last of its
    calendar month in the list, ``next_month`` the next such one.
    """
    allx = sorted(set(expiries))
    ends = _month_ends(allx)
    live = [e for e in allx if close_ms(e, closes) > now_ms]
    month = next((e for e in live if e in ends), None)
    after = [e for e in live if month is not None and e > month and e in ends]
    return {
        "nearest": live[0] if live else None,
        "next": live[1] if len(live) > 1 else None,
        "month": month,
        "next_month": after[0] if after else None,
    }


def atm_strike(price: float, strikes: list[float]) -> float | None:
    """The strike nearest the price, a tie going to the lower strike (OpenAlgo's rule)."""
    if not strikes:
        return None
    p = Fraction(str(price))
    return min(sorted(strikes), key=lambda s: abs(Fraction(str(s)) - p))


def band(strikes: list[float], strike: float) -> tuple[float | None, float | None]:
    """The open interval a held strike keeps while the price stays inside it.

    From the midpoint below minus 10% of the gap below, to the midpoint above plus 10% of the gap
    above; no bound past the end strike (``None``).
    """
    s = sorted(strikes)
    i = s.index(strike)
    cur = Fraction(str(strike))
    low = high = None
    if i > 0:
        gap = cur - Fraction(str(s[i - 1]))
        low = float(cur - gap / 2 - gap * BAND_PAD)
    if i < len(s) - 1:
        gap = Fraction(str(s[i + 1])) - cur
        high = float(cur + gap / 2 + gap * BAND_PAD)
    return low, high


def in_band(price: float, edges: tuple[float | None, float | None]) -> bool:
    """True when the price is strictly inside the band's open ends."""
    p = Fraction(str(price))
    low, high = edges
    return (low is None or p > Fraction(str(low))) and (high is None or p < Fraction(str(high)))


def choose_atm(price: float, strikes: list[float], held: float | None = None) -> float | None:
    """The held strike while the price stays in its band, else the ATM found afresh."""
    if held is not None and held in strikes and in_band(price, band(strikes, held)):
        return held
    return atm_strike(price, strikes)


def offset_strike(strikes: list[float], atm: float, offset: int) -> float | None:
    """The strike ``offset`` steps from the ATM in the sorted list, ``None`` past either end."""
    s = sorted(strikes)
    j = s.index(atm) + offset
    return s[j] if 0 <= j < len(s) else None


def choose_price(
    *,
    tick: tuple[float, int] | None = None,
    connected: bool = False,
    snapshot: tuple[float, int] | None = None,
    feed: tuple[float, int] | None = None,
    last: tuple[float, int] | None = None,
    closed: bool = False,
) -> Price:
    """The price to judge an underlying on, in the order of the design (rule 5).

    The page's own tick while its socket is connected, however old (a quiet stock is not stale);
    else the page's snapshot; else the feed's. With the market closed the same price is kept and
    said to be the last one; with none at all the row waits.
    """
    chain = (
        ("tick", tick if connected else None),
        ("snapshot", snapshot),
        ("feed", feed),
    )
    for source, got in chain:
        if got is not None:
            return Price(got[0], got[1], source, "closed" if closed else "ok")
    if closed and last is not None:
        return Price(last[0], last[1], "last", "closed")
    return Price(None, None, None, "waiting")


def resolve(
    row: dict[str, Any],
    expiries: list[str],
    strikes_by_expiry: dict[str, list[float]],
    now_ms: int,
    price: Price,
    held: float | None = None,
    closes: dict[str, int] | None = None,
    sides: dict[tuple[str, float], tuple[str, ...]] | None = None,
) -> Resolution:
    """Work out the expiry and strike a follow row points at.

    ``row`` carries ``kind``, ``expiry_rule``, and for options ``strike_rule`` (``atm`` or
    ``fixed``), ``offset``, ``strike`` (rupees, fixed) and ``opt_right``. ``sides`` optionally
    names the rights listed per (expiry, strike) so a missing side is told, never guessed.
    """
    expiry = pick_expiries(expiries, now_ms, closes)[row["expiry_rule"]]
    if expiry is None:
        return Resolution("missing", None, None, None, None, price, "no_expiry")
    until = close_ms(expiry, closes)
    if row["kind"] == "month_future":
        state = "closed" if price.state == "closed" else "ok"
        return Resolution(state, expiry, None, None, until, price, None)
    strikes = sorted(strikes_by_expiry.get(expiry, []))
    if row.get("strike_rule") == "fixed":
        strike = row.get("strike")
        if strike not in strikes:
            return Resolution("missing", expiry, None, None, until, price, "no_strike")
        steps = None
        edges = None
    else:
        if price.value is None:
            return Resolution("waiting", expiry, None, None, until, price, "no_price")
        centre = choose_atm(price.value, strikes, held)
        if centre is None:
            return Resolution("missing", expiry, None, None, until, price, "no_strike")
        steps = int(row.get("offset") or 0)
        strike = offset_strike(strikes, centre, steps)
        if strike is None:
            return Resolution("missing", expiry, None, None, until, price, "past_end",
                              steps=steps)  # fmt: skip
        edges = band(strikes, centre)
    need = ("CE", "PE") if row["kind"] == "strike_pair" else (row.get("opt_right") or "CE",)
    if sides is not None:
        have = sides.get((expiry, strike), ())
        gone = tuple(r for r in need if r not in have)
        if gone:
            return Resolution(
                "missing", expiry, strike, edges, until, price, "no_side", gone, steps
            )
    state = "closed" if price.state == "closed" else "ok"
    return Resolution(state, expiry, strike, edges, until, price, None, (), steps)
