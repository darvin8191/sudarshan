"""What a follow row says, for a trader (M8.10). Pure: the caller passes the facts in."""

from __future__ import annotations

from sudarshan.watchlists import numfmt
from sudarshan.watchlists.dynamic import Resolution

#: A price older than this is shown with its time; a quiet stock is not called stale (rule 5).
OLD_PRICE_MS = 10_000


def _rule(offset: int) -> str:
    if offset == 0:
        return "ATM"
    return f"ATM{'+' if offset > 0 else '-'}{abs(offset)}"


def chip(kind: str, offset: int | None) -> str:
    """The chip on a row: ``ATM``, ``ATM+2`` or ``Current-month FUT``."""
    if kind == "month_future":
        return "Current-month FUT"
    return _rule(int(offset or 0))


def waiting(underlying: str) -> str:
    return f"Waiting for {underlying}'s price to pick the strike"


def closed(underlying: str, price: float, at_ms: int) -> str:
    return (
        f"Market closed · strike from {underlying}'s last price "
        f"{numfmt.num(price)} ({numfmt.hms(at_ms)})"
    )


def moved(old_strike: float, right: str, at_ms: int, price: float, underlying: str) -> str:
    """The line under a row for a minute after its strike changes."""
    return (
        f"Moved from {numfmt.num(old_strike)} {right} at {numfmt.hm(at_ms)} "
        f"({underlying} {numfmt.num(price)})"
    )


def rolled(expiry: str, at_ms: int) -> str:
    """On expiry day, from the stop of the regime (15:40 until 6 Dec 2026, 15:30 for an index
    contract from 7 Dec 2026)."""
    return f"Rolled to {numfmt.day(expiry)} at {numfmt.hm(at_ms)}"


def missing(res: Resolution, underlying: str, row: dict, rule_word: str = "") -> str:
    """Why a row has no contract, in a sentence with the next step."""
    exp = numfmt.day(res.expiry) if res.expiry else ""
    if res.why == "no_expiry":
        return f"{underlying} has no {rule_word or 'matching'} expiry in today's contract list."
    if res.why == "past_end":
        n = res.steps or 0
        way = "above" if n > 0 else "below"
        return f"No strike {abs(n)} {way} the ATM in the {exp} expiry."
    if res.why == "no_side":
        sides = " and ".join(res.missing)
        return (
            f"{underlying} {numfmt.num(res.strike or 0)} {sides} is not in today's contract list."
        )
    return f"No strike for {underlying} in the {exp} expiry in today's contract list."


def describe(res: Resolution, underlying: str, row: dict, now_ms: int) -> str:
    """The one line a resolved row carries."""
    p = res.price
    if res.state == "waiting":
        return waiting(underlying)
    if res.state == "missing":
        return missing(res, underlying, row)
    if res.state == "closed" and p.value is not None and p.at_ms is not None:
        return closed(underlying, p.value, p.at_ms)
    exp = numfmt.day(res.expiry or "")
    if row["kind"] == "month_future":
        return f"Now: {underlying} future, {exp}"
    right = row.get("opt_right") or "CE"
    shown = "CE and PE" if row["kind"] == "strike_pair" else right
    out = f"Now: {underlying} {numfmt.num(res.strike or 0)} {shown}, {exp}"
    if p.value is not None:
        out += f" ({underlying} at {numfmt.num(p.value)}"
        if p.at_ms is not None and now_ms - p.at_ms > OLD_PRICE_MS:
            out += f", price from {numfmt.hms(p.at_ms)}"
        out += ")"
    return out
