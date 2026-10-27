"""What the owner reads on Telegram: one short line per order under the brand header.

Plain trader language. The broker's own words appear only after "Broker says:"; there are no
status codes, protocol words or emojis. OpenAlgo's Telegram path sends with parse_mode Markdown
and does not escape, so the Markdown characters * _ ` [ ] are replaced in every text that comes
from the broker. Money and quantities use Indian digit grouping (1,23,456.50).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from sudarshan.autologin.messages import BRAND
from sudarshan.autologin.schedule import IST

MONTHS = "JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC"
# OpenAlgo symbols: [Base][DDMMMYY][Strike][CE|PE] and [Base][DDMMMYY]FUT.
_OPTION = re.compile(
    rf"^(?P<base>[A-Z0-9&-]+?)(?P<day>\d{{2}})(?P<mon>{MONTHS})(?P<yy>\d{{2}})"
    r"(?P<strike>\d+(?:\.\d+)?)(?P<side>CE|PE)$"
)
_FUTURE = re.compile(
    rf"^(?P<base>[A-Z0-9&-]+?)(?P<day>\d{{2}})(?P<mon>{MONTHS})(?P<yy>\d{{2}})FUT$"
)
#: Upstox's carry-forward product "D" reaches the order stream as CNC; on these it is NRML.
DERIVATIVE_EXCHANGES = {"NFO", "BFO", "MCX", "CDS", "BCD", "NCDEX", "NCO"}
#: Currency contracts trade in steps of ₹0.0025: four decimals there, two everywhere else.
CURRENCY_EXCHANGES = {"CDS", "BCD"}
STOP_TYPES = {"SL": "SL", "SL-M": "SL-M", "SLM": "SL-M", "SL M": "SL-M"}
_MARKDOWN = str.maketrans({"*": " ", "_": " ", "`": "'", "[": "(", "]": ")"})
REASON_MAX = 220
#: After a line learned from the order book rather than from the stream (resync.py).
FOUND_LATER = " (found after a pause in order alerts)"
#: A merged message spanning more than this shows a range of times rather than one.
SAME_MOMENT = timedelta(seconds=60)

LABELS = {
    "filled": "Filled",
    "part": "Part filled",
    "part_cancelled": "Part filled, rest cancelled",
    "cancelled": "Cancelled",
    "rejected": "Rejected",
    "triggered": "Triggered",
}


def clean(text, limit: int = 60) -> str:
    """Broker text made safe to show: printable, no Markdown characters, one line, bounded."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(text or ""))
    text = " ".join(text.translate(_MARKDOWN).split())
    if len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text


def _group(whole: str) -> str:
    """'123456' -> '1,23,456' (the last three digits, then pairs)."""
    if len(whole) <= 3:
        return whole
    head, tail = whole[:-3], whole[-3:]
    pairs = []
    while len(head) > 2:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    return ",".join([head, *pairs, tail])


def quantity(value: int) -> str:
    return ("-" if value < 0 else "") + _group(str(abs(int(value))))


def money(value: float, places: int = 2) -> str:
    """₹ with Indian grouping and ``places`` decimals (four for currency derivatives)."""
    whole, frac = f"{abs(value):.{places}f}".split(".")
    return f"{'-' if value < 0 else ''}₹{_group(whole)}.{frac}"


def instrument(symbol: str, today: date) -> str:
    """'NIFTY21OCT2525900CE' -> 'NIFTY 21 OCT 25900 CE'; other symbols are shown as they are.

    The year is shown only when the expiry is not in the current year.
    """
    text = clean(symbol, 40).upper()
    for pattern in (_OPTION, _FUTURE):
        match = pattern.match(text)
        if match:
            year = 2000 + int(match["yy"])
            when = f"{int(match['day'])} {match['mon']}" + (
                f" {year}" if year != today.year else ""
            )
            if pattern is _FUTURE:
                return f"{match['base']} {when} FUT"
            return f"{match['base']} {when} {match['strike']} {match['side']}"
    return text


def tags(update) -> str:
    """'(SL, MIS, NFO)': the order type only for stop orders, then product and exchange."""
    exchange = clean(update.exchange, 12).upper()
    product = clean(update.product, 12).upper()
    if product == "CNC" and exchange in DERIVATIVE_EXCHANGES:
        product = "NRML"
    stop = STOP_TYPES.get(" ".join(update.pricetype.upper().replace("_", " ").split()))
    return ", ".join(part for part in (stop, product, exchange) if part)


def line(kind: str, update, today: date, *, found_later: bool = False) -> str:
    """One order's line, e.g. 'Filled: BUY 65 NIFTY 21 OCT 25900 CE at ₹123.45 (MIS, NFO)'.

    ``found_later``: learned from the order book after a pause in the stream, not as it happened;
    the line says so.
    """
    action = clean(update.action, 8).upper() or "ORDER"
    name = instrument(update.symbol, today)
    # Quantities in units, as the broker reports them; a missing one is left out, never "0".
    total = f" {quantity(update.quantity)}" if update.quantity else ""
    done = update.filled or update.quantity
    filled = f" {quantity(done)}" if done else ""
    of_total = f" of {quantity(update.quantity)}" if update.quantity else ""
    places = 4 if clean(update.exchange, 12).upper() in CURRENCY_EXCHANGES else 2
    at = f" at {money(update.average_price, places)}" if update.average_price > 0 else ""
    meta = tags(update)
    meta = f" ({meta})" if meta else ""
    if kind == "filled":
        body = f"{action}{filled} {name}{at}{meta}"
    elif kind in ("part", "part_cancelled"):
        body = f"{action}{filled}{of_total} {name}{at}{meta}"
    elif kind == "cancelled":
        body = f"{action}{total} {name}, none filled{meta}"
    elif kind == "rejected":
        reason = clean(update.reason, REASON_MAX)
        if reason:
            said = f"Broker says: {reason}"
        elif found_later:  # the order book row came without Upstox's words
            said = "The reason is in the Upstox app's order book."
        else:
            said = "The broker gave no reason."
        # The rest of a part-filled order can be rejected; the filled part is a position.
        part = f"; {quantity(update.filled)} were filled{at}" if update.filled else ""
        body = f"{action}{total} {name}{meta}{part}. {said}"
    elif kind == "triggered":
        price = update.trigger_price
        trigger = f" at trigger {money(price, places)}" if price > 0 else ""
        body = f"{action}{total} {name}{trigger}, not filled yet{meta}"
    else:
        raise ValueError(f"unknown alert kind {kind!r}")
    label = LABELS[kind]
    if update.sandbox:
        label = f"Sandbox {label[0].lower()}{label[1:]}"
    return f"{label}: {body}{FOUND_LATER if found_later else ''}"


#: What the order book said about a pause, and what the owner can do when it could not say.
BOOK_AFTER_PAUSE = {
    "checked": "the order book was checked",
    "unreadable": "the order book could not be read; open Orders in the app to check",
    "sandbox": (
        "OpenAlgo is in Analyze mode, so the Upstox order book could not be checked; "
        "open it in the Upstox app"
    ),
}


def gap_line(start: datetime, end: datetime, book: str) -> str:
    """The pause in the stream, and what the order book said about it (BOOK_AFTER_PAUSE)."""
    start, end = start.astimezone(IST), end.astimezone(IST)
    return f"Order alerts paused {start:%H:%M:%S} to {end:%H:%M:%S} IST; {BOOK_AFTER_PAUSE[book]}."


def compose(lines: list[str], first: datetime, last: datetime, dropped: int = 0) -> str:
    """The Telegram message: brand header, the lines, then the IST time (or range)."""
    first, last = first.astimezone(IST), last.astimezone(IST)
    when = f"{first:%H:%M:%S}"
    if last - first >= SAME_MOMENT:
        when = f"{first:%H:%M:%S} to {last:%H:%M:%S}"
    parts = [BRAND, *lines]
    if dropped:
        parts.append(
            f"{quantity(dropped)} more order updates could not be listed here. "
            "The order book has them."
        )
    parts.append(f"{when} IST")
    return "\n".join(parts)
