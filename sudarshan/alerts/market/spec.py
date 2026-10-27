"""Market alert definitions (M10.2) and their checks, in the words the owner reads.

``check(fields, now)`` takes what the Alerts page sends (the bridge adds the instrument's key, tick
size and expiry from the master contract) and returns the fields as market.db stores them, with
the defaults filled in. Anything it cannot use raises ``SpecError``: the field, and one sentence
saying what to change. ``Alert.of(row)`` turns a stored row into what the engine runs, with a
fixed level converted to paise once (grain.py).

Defaults: a price level fires once; an alert ends 60 days after the day it was set (it works
through that day), and an F&O alert when its contract expires.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sudarshan.alerts.market import sessions
from sudarshan.alerts.market.grain import INDEX_TICK, PAISA, Grain, edge_down, edge_up, to_decimal
from sudarshan.autologin.schedule import IST

#: How a condition fires: on a move across its level, on every value that meets it, or on each
#: new break of a day high or low (a level check whose every hit is a new event).
CROSS, LEVEL, BREAK = "cross", "level", "break"
#: Which way, matching the engine's sides (-1 below, 0 at, 1 above).
UP, DOWN, EITHER = 1, -1, 0
ONCE, PER_CANDLE, AT_CLOSE, EVERY_TIME = "once", "per_candle", "at_close", "every_time"
FREQUENCIES = (ONCE, PER_CANDLE, AT_CLOSE, EVERY_TIME)
#: Candle choices in minutes; 0 is the day (the whole session).
DAY = 0
CANDLES = (1, 5, 15, 30, 60, DAY)
SOURCES = ("ltp", "bid", "ask")
#: OpenAlgo's exchange for each segment market alerts cover.
EXCHANGES = {
    "NSE": "NSE_EQ",
    "BSE": "BSE_EQ",
    "NFO": "NSE_FO",
    "BFO": "BSE_FO",
    "NSE_INDEX": "NSE_INDEX",
    "BSE_INDEX": "BSE_INDEX",
}
PRICE_OPS = {
    "crosses_above": (CROSS, UP),
    "crosses_below": (CROSS, DOWN),
    "crosses": (CROSS, EITHER),
    "at_or_above": (LEVEL, UP),
    "at_or_below": (LEVEL, DOWN),
}
OP_WORDS = {
    "crosses_above": "Crosses above",
    "crosses_below": "Crosses below",
    "crosses": "Crosses",
    "at_or_above": "Is at or above",
    "at_or_below": "Is at or below",
}
DEFAULT_DAYS = 60
#: "Every time" on a check that holds for many trades in a row needs at least this wait.
MIN_LEVEL_WAIT_S = 60
MAX_WAIT_S = 24 * 3600
NOTE_MAX = 120


class SpecError(ValueError):
    """A definition the engine cannot run; ``message`` is for the owner, ``field`` for the form."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


@dataclass(frozen=True, slots=True)
class Compiled:
    """What a kind's parameters mean to the engine."""

    mode: str
    direction: int
    source: str = "ltp"
    #: Fixed edges in paise (a price level); None when a condition derives them from the feed.
    up: int | None = None
    down: int | None = None
    #: The feed mode the alert needs: "ltpc" (last price and previous close) or "full".
    feed: str = "ltpc"
    #: A kind's own numbers (conditions.py): a % move's factors, a day break's start offset.
    extra: tuple = ()


@dataclass(frozen=True, slots=True)
class Kind:
    check: Callable[[Any, str, Grain], dict[str, Any]]
    compile: Callable[[Mapping[str, Any], Grain, str], Compiled]
    #: Words for this kind's check in a sentence ("Every time on '% move' needs ...").
    words: Callable[[Mapping[str, Any]], str]
    frequency: str
    candle: int | None = None
    wait_s: int = 0
    #: Why "At candle close" makes no sense for the kind, when it does not.
    no_close: str | None = None


@dataclass(frozen=True, slots=True)
class Alert:
    """One alert as the engine runs it. ``params`` is the stored JSON; treat it as read-only."""

    id: int
    version: int
    status: str
    kind: str
    key: str
    segment: str
    symbol: str
    exchange: str
    grain: Grain
    expiry: date | None
    params: Mapping[str, Any]
    frequency: str
    candle_min: int | None
    cooldown_ms: int
    ends_at: int | None
    note: str
    cond: Compiled
    #: Epoch ms this contract's alerts stop at: its segment's close on the expiry day.
    expires_at: int | None

    @classmethod
    def of(cls, row: Mapping[str, Any]) -> Alert:
        """A stored row (``check``'s fields plus id, version and status) ready for the engine."""
        segment = sessions.segment_of(row["instrument_key"])
        grain = _grain(segment, row.get("tick_size"))
        expiry = _date(row.get("expiry"))
        index = sessions.index_derivative(row["symbol"])
        return cls(
            id=int(row["id"]),
            version=int(row["version"]),
            status=row["status"],
            kind=row["kind"],
            key=row["instrument_key"],
            segment=segment,
            symbol=row["symbol"],
            exchange=row["exchange"],
            grain=grain,
            expiry=expiry,
            params=row["params"],
            frequency=row["frequency"],
            candle_min=row.get("candle_min"),
            cooldown_ms=int(row.get("cooldown_s") or 0) * 1000,
            ends_at=row.get("ends_at"),
            note=row.get("note") or "",
            cond=KINDS[row["kind"]].compile(row["params"], grain, segment),
            expires_at=sessions.expiry_close(segment, expiry, index=index) if expiry else None,
        )


def check(fields: Mapping[str, Any], now: int) -> dict[str, Any]:
    """The definition as stored, defaults filled in; ``now`` (epoch ms) dates the defaults."""
    key = fields.get("instrument_key")
    segment = sessions.segment_of(key) if isinstance(key, str) else ""
    exchange = fields.get("exchange")
    if segment not in sessions.SEGMENTS or EXCHANGES.get(exchange) != segment:
        raise SpecError(
            "instrument",
            "Market alerts work on NSE, BSE, NFO and BFO instruments and their indices for now.",
        )
    symbol = fields.get("symbol")
    if not isinstance(symbol, str) or not 0 < len(symbol.strip()) <= 60:
        raise SpecError("instrument", "Pick the instrument again from the search.")
    grain = _grain(segment, fields.get("tick_size"))
    expiry = _date(fields.get("expiry"))
    index = sessions.index_derivative(str(symbol))
    closes = sessions.expiry_close(segment, expiry, index=index) if expiry else None
    if closes is not None and closes <= now:
        raise SpecError(
            "instrument",
            f"This contract expired on {expiry.day} {expiry:%b}. Pick one that is still trading.",
        )
    kind = KINDS.get(fields.get("kind", "price"))
    if kind is None:
        raise SpecError("kind", "This kind of alert is not available yet.")
    params = kind.check(fields.get("params"), segment, grain)
    _reachable(kind, params, to_decimal(fields.get("price")), segment)
    frequency = fields.get("frequency") or kind.frequency
    if frequency not in FREQUENCIES:
        raise SpecError(
            "frequency", "Pick how often: once, once per candle, at candle close or every time."
        )
    if frequency == AT_CLOSE and kind.no_close:
        raise SpecError("frequency", kind.no_close)
    if frequency == AT_CLOSE and params.get("source", "ltp") != "ltp":
        raise SpecError(
            "frequency",
            "'At candle close' judges the last trade price. For the bid or ask, pick another "
            "frequency.",
        )
    candle = _candle(fields, frequency, kind)
    wait = _wait(fields.get("cooldown_s", kind.wait_s))
    if frequency == EVERY_TIME and kind.compile(params, grain, segment).mode in (LEVEL, BREAK):
        if wait < MIN_LEVEL_WAIT_S:  # each uptick of a trending day is a new high
            raise SpecError(
                "cooldown_s",
                f"Every time on '{kind.words(params)}' needs at least 1 minute between alerts.",
            )
    return {
        "instrument_key": key,
        "symbol": symbol.strip(),
        "exchange": exchange,
        "tick_size": str(grain.step),
        "expiry": expiry.isoformat() if expiry else None,
        "kind": fields.get("kind", "price"),
        "params": params,
        "frequency": frequency,
        "candle_min": candle,
        "cooldown_s": wait,
        "ends_at": _ends(fields, now, segment, expiry),
        "note": _note(fields.get("note")),
        "channels": _channels(fields.get("channels")),
    }


#: An edit to these changes what fires, so the alert's state starts again from the next price.
CONDITION_FIELDS = ("instrument_key", "kind", "params", "frequency", "candle_min")


def changes_condition(old: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
    """True when an edit changes the condition, not only its delivery, note, wait or end date.

    The design names the level, the check, the kind and the instrument. How often and the candle
    count too: a Once alert that fired and is edited to Every time must be able to fire again.
    """
    return any(old.get(name) != new.get(name) for name in CONDITION_FIELDS)


def status_after_edit(status: str, condition_changed: bool) -> str:
    """A changed condition makes a Stopped alert Active again; a Paused one stays Paused."""
    return "active" if condition_changed and status == "stopped" else status


def _check_price(params, segment: str, grain: Grain) -> dict[str, Any]:
    params = params if isinstance(params, Mapping) else {}
    op = params.get("op")
    if op not in PRICE_OPS:
        raise SpecError(
            "op",
            "Pick the check: crosses above, crosses below, crosses, is at or above, or is at or "
            "below.",
        )
    level = to_decimal(params.get("level"))
    if level is None or level <= 0:
        raise SpecError("level", "Enter the price level, a number above 0.")
    if level < PAISA:  # nothing trades below a paisa: "at or below" could never fire
        raise SpecError("level", "Enter a price level of at least 0.01.")
    source = params.get("source", "ltp")
    if source not in SOURCES:
        raise SpecError("source", "Pick the price to watch: last price, bid or ask.")
    if source != "ltp" and segment in sessions.INDEX_SEGMENTS:
        raise SpecError("source", "An index has no bid or ask. Watch its last price instead.")
    return {"op": op, "level": str(level), "source": source}


def _compile_price(params: Mapping[str, Any], grain: Grain, segment: str) -> Compiled:
    mode, direction = PRICE_OPS[params["op"]]
    level = to_decimal(params["level"])
    source = params.get("source", "ltp")
    feed = "ltpc" if source == "ltp" else "full"  # the bid and ask come in full mode only
    return Compiled(mode, direction, source, edge_up(level), edge_down(level), feed)


MOVE_DIRECTIONS = {"up": UP, "down": DOWN, "either": EITHER}
VWAP_DIRECTIONS = {"above": UP, "below": DOWN, "either": EITHER}
MAX_RISE = 1000


def _check_move(params, segment: str, grain: Grain) -> dict[str, Any]:
    params = params if isinstance(params, Mapping) else {}
    base = params.get("base", "close")
    if base not in ("close", "open"):
        raise SpecError("base", "Pick the base: the previous close or today's open.")
    direction = params.get("direction")
    if direction not in MOVE_DIRECTIONS:
        raise SpecError("direction", "Pick the way: up, down or either way.")
    percent = to_decimal(params.get("percent"))
    if percent is None or percent <= 0:
        raise SpecError("percent", "Enter the % move, a number above 0.")
    if direction != "up" and percent >= 100:
        raise SpecError("percent", "A fall can be at most 99.99%.")
    if percent > MAX_RISE:
        raise SpecError("percent", "A rise can be at most 1,000%.")
    return {"base": base, "direction": direction, "percent": str(percent)}


def _compile_move(params: Mapping[str, Any], grain: Grain, segment: str) -> Compiled:
    share = to_decimal(params["percent"]) / 100
    feed = "ltpc" if params["base"] == "close" else "full"  # the open comes with the day's OHLC
    direction = MOVE_DIRECTIONS[params["direction"]]
    return Compiled(LEVEL, direction, feed=feed, extra=(1 + share, 1 - share))


def _check_break(params, segment: str, grain: Grain) -> dict[str, Any]:
    params = params if isinstance(params, Mapping) else {}
    side = params.get("side")
    if side not in ("high", "low"):
        raise SpecError("side", "Pick the break: the day's high or the day's low.")
    after = params.get("after", "09:30")
    start, end = sessions.SEGMENTS[segment]
    minute = _minute(after)
    if minute is None or not start <= minute < end:
        raise SpecError("after", f"Pick a start time between {_hhmm(start)} and {_hhmm(end)}.")
    return {"side": side, "after": _hhmm(minute)}


def _compile_break(params: Mapping[str, Any], grain: Grain, segment: str) -> Compiled:
    direction = UP if params["side"] == "high" else DOWN
    offset = _minute(params["after"]) - sessions.SEGMENTS[segment][0]
    return Compiled(BREAK, direction, feed="full", extra=(offset,))


def _check_vwap(params, segment: str, grain: Grain) -> dict[str, Any]:
    params = params if isinstance(params, Mapping) else {}
    if segment in sessions.INDEX_SEGMENTS:
        raise SpecError("kind", "An index has no traded volume, so it has no VWAP. Use its future.")
    direction = params.get("direction")
    if direction not in VWAP_DIRECTIONS:
        raise SpecError("direction", "Pick the way: crosses above, crosses below or either way.")
    return {"direction": direction}


def _compile_vwap(params: Mapping[str, Any], grain: Grain, segment: str) -> Compiled:
    return Compiled(CROSS, VWAP_DIRECTIONS[params["direction"]], feed="full")


KINDS: dict[str, Kind] = {
    "price": Kind(_check_price, _compile_price, lambda p: OP_WORDS[p["op"]], ONCE),
    "move": Kind(_check_move, _compile_move, lambda p: "% move", PER_CANDLE, DAY),
    "day_break": Kind(
        _check_break,
        _compile_break,
        lambda p: f"Breaks the day's {p['side']}",
        PER_CANDLE,
        DAY,
        no_close="A day high or low alert checks every trade, so 'At candle close' is not "
        "available for it.",
    ),
    "vwap": Kind(_check_vwap, _compile_vwap, lambda p: "Crosses VWAP", EVERY_TIME, wait_s=900),
}


def _reachable(kind: Kind, params: Mapping[str, Any], price, segment: str) -> None:
    """A fall that would take ``price`` (the last price the form shows, when it sends one)
    below 0.01 can never happen: down 99.9% on a ₹2 stock."""
    if kind is not KINDS["move"] or params["direction"] != "down" or not price or price <= 0:
        return
    if edge_down(price * (1 - to_decimal(params["percent"]) / 100)) < 1:
        sign = "" if segment in sessions.INDEX_SEGMENTS else "₹"
        raise SpecError(
            "percent",
            f"A fall of {params['percent']}% from {sign}{price:.2f} would take the price below "
            f"{sign}0.01, so it can never happen. Pick a smaller %.",
        )


def _minute(text) -> int | None:
    """'09:30' -> 570; None unless it is HH:MM."""
    if not isinstance(text, str) or len(text.strip()) != 5 or text.strip()[2] != ":":
        return None
    hours, minutes = text.strip().split(":")
    if not (hours.isdigit() and minutes.isdigit()) or int(minutes) > 59:
        return None
    return int(hours) * 60 + int(minutes)


def _hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _grain(segment: str, tick_size) -> Grain:
    grain = Grain.of(tick_size)
    if grain is None and segment in sessions.INDEX_SEGMENTS:
        grain = Grain.of(INDEX_TICK)
    if grain is not None and not grain.whole_paise:  # currency derivatives (0.0025): see grain.py
        raise SpecError(
            "instrument",
            "This instrument's prices move in steps smaller than a paisa, which market alerts do "
            "not cover yet.",
        )
    if grain is None:
        raise SpecError(
            "instrument",
            "This instrument has no tick size in the master contract, so its levels cannot be "
            "checked. Download the master contract again, then set the alert.",
        )
    return grain


def _date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise SpecError("instrument", "Pick the instrument again from the search.") from None


def _candle(fields: Mapping[str, Any], frequency: str, kind: Kind) -> int | None:
    if frequency not in (PER_CANDLE, AT_CLOSE):
        return None
    value = fields.get("candle_min", fields.get("candle", kind.candle))
    value = DAY if value == "day" else value
    if isinstance(value, bool) or value not in CANDLES:
        raise SpecError("candle", "Pick a candle of 1, 5, 15, 30 or 60 minutes, or Day.")
    return int(value)


def _wait(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SpecError("cooldown_s", "Pick the wait between alerts in whole seconds.")
    if not 0 <= value <= MAX_WAIT_S:  # NaN fails this too
        raise SpecError("cooldown_s", "Pick a wait between alerts from 0 to 24 hours.")
    if value != int(value):
        raise SpecError("cooldown_s", "Pick the wait between alerts in whole seconds.")
    return int(value)


def _ends(fields: Mapping[str, Any], now: int, segment: str, expiry: date | None) -> int | None:
    """Epoch ms the alert stops at, or None: no end of its own (a contract still expires)."""
    if "ends" not in fields:
        if segment in sessions.FO_SEGMENTS and expiry is not None:
            return None
        today = datetime.fromtimestamp(now / 1000, IST).date()
        return sessions.epoch_ms(today + timedelta(days=DEFAULT_DAYS + 1), 0)
    value = fields["ends"]
    if value is None:
        return None
    ends = None
    if isinstance(value, str):
        text = value.strip()
        try:
            if len(text) == 10:  # a date: the alert works through that day
                ends = sessions.epoch_ms(date.fromisoformat(text) + timedelta(days=1), 0)
            else:
                moment = datetime.fromisoformat(text)
                moment = moment if moment.tzinfo else moment.replace(tzinfo=IST)
                ends = int(moment.timestamp() * 1000)
        except ValueError:
            ends = None
    if ends is None:
        raise SpecError("ends", "Pick an end date, or no end.")
    if ends <= now:
        raise SpecError("ends", "That end date has already passed. Pick a later one, or no end.")
    return ends


def _note(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SpecError("note", "A note is text.")
    value = " ".join(value.split())
    if len(value) > NOTE_MAX:
        raise SpecError("note", f"A note can be at most {NOTE_MAX} characters.")
    return value


def _channels(value) -> dict[str, bool] | None:
    """None means the owner's notification settings decide; otherwise this alert's own choice."""
    if value is None:
        return None
    if not isinstance(value, Mapping) or not all(
        isinstance(value.get(name), bool) for name in ("telegram", "app")
    ):
        raise SpecError("channels", "Pick where to send it: Telegram, the app, or both.")
    return {"telegram": value["telegram"], "app": value["app"]}
