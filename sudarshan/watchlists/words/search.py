"""What the search sheet says, for a trader (M8.18). Pure."""

from __future__ import annotations

from sudarshan.watchlists import numfmt
from sudarshan.watchlists.search import Understood, expiry_iso

_MONTH_FULL = ("January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December")  # fmt: skip


def title(row) -> str:
    """The line over a result: ``NIFTY 25,900 CE``, ``NIFTY FUT, 24 Nov 2026`` or a symbol."""
    iso = expiry_iso(row.expiry)
    if row.instrumenttype in ("CE", "PE"):
        return f"{row.underlying} {numfmt.num(row.strike or 0)} {row.instrumenttype}"
    if row.instrumenttype == "FUT" and iso:
        return f"{row.underlying} FUT, {numfmt.day(iso)}"
    return row.symbol


def detail(row) -> str:
    """The line under it: ``NFO · 24 Nov 2026 · lot 65``."""
    iso = expiry_iso(row.expiry)
    parts = [row.exchange]
    if iso:
        parts.append(numfmt.day(iso))
    elif row.name:
        parts.append(row.name)
    if iso and row.lotsize:
        parts.append(f"lot {numfmt.group(int(row.lotsize))}")
    return " · ".join(parts)


def _expiry_part(u: Understood) -> str | None:
    spec, first = u.expiry, (numfmt.short_day(u.expiries[0]) if u.expiries else "")
    if spec is None:
        if not u.options:
            return None
        return (
            "every expiry" if u.strike is not None and u.atm is None else f"nearest expiry {first}"
        )
    kind = spec[0]
    if kind == "month_name":
        return f"{_MONTH_FULL[spec[1] - 1]} expiries"
    word = {"nearest": "nearest expiry", "next": "next expiry", "month": "monthly expiry",
            "next_month": "next monthly expiry", "date": "expiry", "day": "expiry"}[kind]  # fmt: skip
    return f"{word} {first}".strip()


def understood_line(u: Understood) -> str:
    """The query as understood, in one line: ``NIFTY · next expiry 1 Dec · strike 26,000 · PE``."""
    if u.isin:
        return f"ISIN {u.isin}"
    parts = [u.underlying] if u.underlying else []
    if u.future:
        parts.append("futures")
    elif u.expiry is not None or u.options:
        part = _expiry_part(u)
        if part:
            parts.append(part)
    if u.atm is not None:
        parts.append("ATM" if u.atm == 0 else f"ATM{'+' if u.atm > 0 else '-'}{abs(u.atm)}")
    if u.strike is not None:
        parts.append(f"strike {numfmt.num(u.strike)}")
    if u.side:
        parts.append(u.side)
    if u.needs_price:
        parts.append("the price is not known yet")
    return " · ".join(parts)


def no_match(query: str, suggestions: tuple[str, ...]) -> str:
    """``No exact match for RELAINCE. Did you mean RELIANCE?``"""
    head = f"No exact match for {query.strip().upper()}."
    if not suggestions:
        return head
    return head + " Did you mean " + " or ".join(suggestions) + "?"
