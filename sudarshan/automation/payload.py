"""Reading one alert message (M12.1). Pure: text in, an ``Alert`` or a ``BadMessage`` out.

The three message forms the Automation page gives the owner all read here:

- a strategy's order-fill alert, every value filled in by TradingView as text;
- an indicator's ``alert()``, whose script writes the text, with ``bar`` and ``fired`` as numbers
  (milliseconds);
- an ``alertcondition()`` or a price alert with a fixed ``position``.

TradingView sends valid JSON as ``application/json`` and anything else as ``text/plain``; both are
read as JSON text. No form carries a secret. What is refused, and why, is ``REASONS``; the
sentence for each is in words.py.
"""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from typing import Any

from .model import FLAT, LONG, POSITIONS, SHORT, Alert

MAX_VALUE = 64
#: Read and used.
USED = frozenset(
    {
        "v", "id", "action", "contracts", "position", "size", "before", "before_size",
        "signed_size", "ticker", "bar", "fired",
    }
)  # fmt: skip
#: Allowed and not used: what the page's message carries for the owner's own reading.
IGNORED = frozenset({"price", "interval", "comment"})
REASONS = (
    "not_json", "not_object", "version", "unknown_key", "too_long", "placeholder", "position",
    "before", "fired", "bar", "number", "mismatch",
)  # fmt: skip
_PLACEHOLDER = re.compile(r"\{\{[^{}]{0,60}\}\}|\{\{")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z")
_DIGITS = re.compile(r"\d{10,16}")
_TICKER = re.compile(r"([A-Z]{2,12})(_DLY|_DL)?:([A-Z0-9&_!.-]{1,40})")
_FIRST, _LAST = 1_577_836_800, 4_102_444_800  # 2020 to 2100, in seconds


class BadMessage(ValueError):
    """The alert message is not one SUDARSHAN gave. ``reason`` is one of ``REASONS``; ``what``
    names the key or the placeholder, already cut to something safe to show."""

    def __init__(self, reason: str, what: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.what = re.sub(r"[^A-Za-z0-9_.{} ]", "", str(what))[:40]


def _text(value: Any, key: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, dict | list):
        raise BadMessage("number" if key in _NUMBERS else "unknown_key", key)
    text = str(value).strip() if not isinstance(value, float) else repr(value)
    if len(text) > MAX_VALUE:
        raise BadMessage("too_long", key)
    return text or None


def _number(value: Any, key: str) -> float | None:
    text = _text(value, key)
    if text is None:
        return None
    try:
        number = float(text)
    except ValueError:
        raise BadMessage("number", key) from None
    if not math.isfinite(number):
        raise BadMessage("number", key)
    return number


def _seconds(value: Any, key: str) -> int | None:
    """A moment as epoch seconds: ISO text to the second in UTC (``{{timenow}}``), or a number of
    milliseconds (Pine's ``timenow``)."""
    text = _text(value, key)
    if text is None:
        return None
    if _ISO.fullmatch(text):
        try:
            at = datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
        except ValueError:
            raise BadMessage(key) from None
        seconds = int(at.timestamp())
    elif _DIGITS.fullmatch(text.split(".")[0]) and re.fullmatch(r"\d+(\.\d+)?", text):
        seconds = int(float(text)) // 1000
    else:
        raise BadMessage(key)
    if not _FIRST <= seconds <= _LAST:
        raise BadMessage(key)
    return seconds


def _word(value: Any, key: str) -> str | None:
    text = _text(value, key)
    if text is None:
        return None
    text = text.lower()
    if text not in POSITIONS:
        raise BadMessage(key)
    return text


_NUMBERS = frozenset({"contracts", "size", "before_size", "signed_size"})


def parse(body: str | bytes) -> Alert:
    """The alert in ``body``, or ``BadMessage``."""
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8")
        except UnicodeDecodeError:
            raise BadMessage("not_json") from None
    left = _PLACEHOLDER.search(body)
    if left:  # what TradingView sends when a strategy placeholder is used in another alert
        raise BadMessage("placeholder", left.group(0))
    try:
        data = json.loads(body)
    except ValueError:
        raise BadMessage("not_json") from None
    if not isinstance(data, dict):
        raise BadMessage("not_object")
    for key in data:
        if key not in USED and key not in IGNORED:
            raise BadMessage("unknown_key", key)
    for key in IGNORED & set(data):
        if len(str(data[key])) > MAX_VALUE:
            raise BadMessage("too_long", key)
    if _text(data.get("v"), "v") != "1":
        raise BadMessage("version")
    position = _word(data.get("position"), "position")
    if position is None:
        raise BadMessage("position")
    fired = _seconds(data.get("fired"), "fired")
    if fired is None:
        raise BadMessage("fired")
    size = _number(data.get("size"), "size")
    before = _word(data.get("before"), "before")
    before_size = _number(data.get("before_size"), "before_size")
    signed = _number(data.get("signed_size"), "signed_size")
    contracts = _number(data.get("contracts"), "contracts")
    for number in (size, before_size, contracts):
        if number is not None and number < 0:
            raise BadMessage("number")
    if size is not None and (size == 0) != (position == FLAT):
        raise BadMessage("mismatch", "size")  # "long" with size 0, or "flat" with a size
    if before is not None and before_size is not None and (before_size == 0) != (before == FLAT):
        raise BadMessage("mismatch", "before_size")
    if signed is not None:
        # The signed size is only a cross-check of the direction word and the absolute size.
        sign = {LONG: 1, SHORT: -1, FLAT: 0}[position]
        if (signed > 0) - (signed < 0) != sign:
            raise BadMessage("mismatch", "signed_size")
        if size is not None and not math.isclose(abs(signed), size, rel_tol=1e-9, abs_tol=1e-9):
            raise BadMessage("mismatch", "signed_size")
    action = _text(data.get("action"), "action")
    if action is not None:
        action = action.lower()
        if action not in ("buy", "sell"):
            raise BadMessage("unknown_key", "action")
    exchange = ticker = None
    delayed = False
    name = _text(data.get("ticker"), "ticker")
    if name is not None:
        found = _TICKER.fullmatch(name.upper())
        if not found:
            raise BadMessage("unknown_key", "ticker")
        exchange, delayed, ticker = found.group(1), bool(found.group(2)), found.group(3)
    return Alert(
        position=position,
        fired_s=fired,
        size=size,
        before=before,
        before_size=before_size,
        order_id=_text(data.get("id"), "id"),
        action=action,
        contracts=contracts,
        exchange=exchange,
        ticker=ticker,
        delayed=delayed,
        interval=_text(data.get("interval"), "interval"),
        bar_s=_seconds(data.get("bar"), "bar"),
    )


#: What is never kept: a link's token, a word that names a secret, or any unbroken run of 32 or
#: more letters and digits (an API key, a hash, a token of another service).
_SECRET = re.compile(
    r"sdt_[A-Za-z0-9_-]{8,}|(?i:apikey|api_key|token|password|secret)|[A-Za-z0-9_+/=-]{32,}"
)


def redact(body: str | bytes, keep: int = 600) -> str:
    """The message as it is stored: only the keys this module knows, each value cut, so a secret
    pasted into an alert by mistake (an old message with an API key) is never kept."""
    if isinstance(body, bytes):
        body = body.decode("utf-8", "replace")
    try:
        data = json.loads(body)
    except ValueError:
        return "(not readable)"
    if not isinstance(data, dict):
        return "(not readable)"
    kept = {}
    for key, value in data.items():
        if key in USED or key in IGNORED:
            text = str(value)
            kept[key] = "(removed)" if _SECRET.search(text) else text[:MAX_VALUE]
    dropped = len(data) - len(kept)
    if dropped:
        kept["_other_keys"] = dropped
    return json.dumps(kept, separators=(",", ":"), ensure_ascii=True)[:keep]
