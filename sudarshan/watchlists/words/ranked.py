"""What a ranked list says, for a trader (M8.9). Pure: the caller passes the facts in.

Prices tick on their own and say "Real-time"; a ranking is a read, so it says "Ranked by Upstox"
and its age, never "Live".
"""

from __future__ import annotations

from sudarshan.watchlists import numfmt

STALE_AFTER_MS = 10 * 60 * 1000
#: Traded value arrives in rupees and is shown in crore. To be checked against a real answer (W-1).
CRORE = 10_000_000

SIGNIN_TODAY = (
    "Sign in to Upstox to read rankings. Ranked lists use your daily Upstox sign-in, which has "
    "not been done today."
)
OTHER_BROKER = "Ranked lists come from Upstox. Sign in to Upstox to use them."
WAIT = "Upstox asked us to wait. Pull down again in a minute."
NO_ALERT = (
    "A ranked list changes with every read. Set the alert on a list or a built-in list instead."
)
KIND = {"futures": "Futures", "options": "Options"}
ASSET = {"INDEX": "Index", "STOCK": "Stock", "COMMODITY": "Commodity"}
CATEGORY = {
    "TOP_TRADED": "Top traded", "MOST_ACTIVE": "Most active", "OI_GAINERS": "OI gainers",
    "OI_LOSERS": "OI losers", "PRICE_GAINERS": "Price gainers", "PRICE_LOSERS": "Price losers",
    "PREMIUM": "Premium", "DISCOUNT": "Discount", "IV_GAINERS": "IV gainers",
    "IV_LOSERS": "IV losers", "UNDER_5000": "Under ₹5,000 to buy",
    "UNDER_10000": "Under ₹10,000 to buy",
}  # fmt: skip


def label(source: str, asset: str, category: str) -> str:
    """``Index futures · OI gainers``."""
    return f"{ASSET[asset]} {source} · {CATEGORY[category]}"


def figure(category: str, metric: dict, price: dict) -> str | None:
    """The ranking's own figure for the second line of a row, as read."""
    cur, pct, chg = metric.get("current"), metric.get("change_pct"), metric.get("change_abs")
    if category == "TOP_TRADED" and cur is not None:
        return f"Traded ₹{numfmt.num(cur / CRORE, 1)} cr"
    if category == "MOST_ACTIVE" and cur is not None:
        return f"Volume {numfmt.group(int(round(cur)))}"
    if category in ("OI_GAINERS", "OI_LOSERS"):
        return f"OI {numfmt.signed(pct, 1, '%')}" if pct is not None else None
    if category in ("PRICE_GAINERS", "PRICE_LOSERS"):
        p = price.get("change_pct")
        return f"Price {numfmt.signed(p, 1, '%')}" if p is not None else None
    if category in ("IV_GAINERS", "IV_LOSERS") and cur is not None:
        tail = f", {numfmt.signed(chg, 1)}" if chg is not None else ""
        return f"IV {numfmt.fixed(cur, 1)}%{tail}"
    if category in ("PREMIUM", "DISCOUNT") and cur is not None:
        return f"{CATEGORY[category]} {numfmt.fixed(abs(cur), 2)}"
    if category in ("UNDER_5000", "UNDER_10000") and cur is not None:
        return f"Costs ₹{numfmt.group(int(round(cur)))} to buy"
    return None


def oi_figure(oi_now: float, previous: float | None) -> str | None:
    """The OI figure recomputed from a tick against Smartlist's ``previous``."""
    if not previous:
        return None
    return f"OI {numfmt.signed((oi_now - previous) / previous * 100, 1, '%')}"


def age(made_ms: int, now_ms: int) -> str:
    """``just now``, ``6 min ago``, ``2 h ago``, ``2 d ago``."""
    s = max(0, (now_ms - made_ms) // 1000)
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{s // 60} min ago"
    if s < 86400:
        return f"{s // 3600} h ago"
    return f"{s // 86400} d ago"


def is_stale(made_ms: int, now_ms: int) -> bool:
    """True after 10 minutes: the line takes the stale style (an icon and the age)."""
    return now_ms - made_ms > STALE_AFTER_MS


def age_line(made_ms: int, now_ms: int) -> str:
    line = f"Ranked by Upstox · {age(made_ms, now_ms)}"
    return line + " · Pull down to read it again" if is_stale(made_ms, now_ms) else line


def times_line(made_ms: int, read_ms: int) -> str:
    """A tap on the line: ``Ranking made 10:42:05, read 10:43:10``."""
    return f"Ranking made {numfmt.hms(made_ms)}, read {numfmt.hms(read_ms)}"


def expired_line(made_ms: int, now_ms: int) -> str:
    """The sign-in lapsed while the list was open; the old rows stay, labelled with their age."""
    return (
        f"Ranked by Upstox · {age(made_ms, now_ms)} · Your Upstox sign-in has expired. "
        "Sign in to read it again."
    )


def closed_line(made_ms: int, now_ms: int) -> str:
    """``Market closed · ranking from 15:29 yesterday`` (Upstox's own time)."""
    days = (numfmt.ist_date(now_ms) - numfmt.ist_date(made_ms)).days
    when = numfmt.hm(made_ms)
    if days == 1:
        when += " yesterday"
    elif days > 1:
        when += " on " + numfmt.short_day(numfmt.ist_date(made_ms).isoformat())
    return f"Market closed · ranking from {when}"


def unavailable(made_ms: int | None) -> str:
    """Upstox refused or did not answer; the last ranking, if any, stays."""
    if made_ms is None:
        return "Upstox's ranking could not be read just now. Pull down to try again."
    return f"Upstox's ranking could not be read just now. Showing the ranking from {numfmt.hm(made_ms)}."


def left_out(n: int) -> str | None:
    """``2 contracts from Upstox are not in today's contract list and are left out.``"""
    if not n:
        return None
    one = n == 1
    return (
        f"{n} contract{'' if one else 's'} from Upstox {'is' if one else 'are'} not in today's "
        f"contract list and {'is' if one else 'are'} left out."
    )


def updated_line(read_ms: int, new_names: list[str]) -> str | None:
    """``Ranking updated 10:43 · 3 new: NIFTY 25,950 CE, ...`` (at most three names)."""
    if not new_names:
        return None
    shown = ", ".join(new_names[:3]) + (
        f" and {len(new_names) - 3} more" if len(new_names) > 3 else ""
    )
    return f"Ranking updated {numfmt.hm(read_ms)} · {len(new_names)} new: {shown}"


def underlying_chip(count: int, total: int, underlying: str) -> str:
    """``12 of the top 50 are NIFTY contracts``."""
    return f"{count} of the top {total} are {underlying} contracts"


def refresh_wait(seconds: int) -> str:
    """The Refresh ranking button inside its minute: ``Read 20 s ago``."""
    return f"Read {seconds} s ago"
