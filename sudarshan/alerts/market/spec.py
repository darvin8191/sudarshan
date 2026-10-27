"""Market alert definitions (M10.2) and their checks, in the words the owner reads.

``check(fields, now)`` takes what the Alerts page sends (the bridge adds the instrument's key, tick
size and expiry from the master contract) and returns the fields as market.db stores them, with
the defaults filled in. Anything it cannot use raises ``SpecError``: the field, and one sentence
saying what to change. ``Alert.of(row)`` turns a stored row into what the engine runs, with a
fixed level converted to whole ticks once (grain.py).

Defaults: a price level fires once; an alert ends 60 days after the day it was set (it works
through that day), and an F&O alert when its contract expires.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sudarshan.alerts.market import sessions
from sudarshan.alerts.market.grain import INDEX_TICK, Grain, to_decimal
from sudarshan.autologin.schedule import IST

#: How a condition fires: on a move across its level, or on every value that meets it.
CROSS, LEVEL = "cross", "level"
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
    #: Fixed edges in ticks (a price level); None when a condition derives them from the feed.
    up: int | None = None
    down: int | None = None
    #: The feed mode the alert needs: "ltpc" (last price and previous close) or "full".
    feed: str = "ltpc"


@dataclass(frozen=True, slots=True)
class Kind:
    check: Callable[[Any, str, Grain], dict[str, Any]]
    compile: Callable[[Mapping[str, Any], Grain], Compiled]
    #: Words for this kind's check in a sentence ("Every time on '% move' needs ...").
    words: Callable[[Mapping[str, Any]], str]
    frequency: str
    candle: int | None = None
    wait_s: int = 0


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
        hours = sessions.hours(segment)
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
            cond=KINDS[row["kind"]].compile(row["params"], grain),
            expires_at=sessions.epoch_ms(expiry, hours[1]) if expiry and hours else None,
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
    kind = KINDS.get(fields.get("kind", "price"))
    if kind is None:
        raise SpecError("kind", "This kind of alert is not available yet.")
    params = kind.check(fields.get("params"), segment, grain)
    frequency = fields.get("frequency") or kind.frequency
    if frequency not in FREQUENCIES:
        raise SpecError(
            "frequency", "Pick how often: once, once per candle, at candle close or every time."
        )
    candle = _candle(fields, frequency, kind)
    wait = _wait(fields.get("cooldown_s", kind.wait_s))
    if frequency == EVERY_TIME and kind.compile(params, grain).mode == LEVEL:
        if wait < MIN_LEVEL_WAIT_S:
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
    if level < grain.step:  # nothing trades below one tick: "at or below" could never fire
        raise SpecError("level", f"Enter a price level of at least one tick, {grain.price(1)}.")
    source = params.get("source", "ltp")
    if source not in SOURCES:
        raise SpecError("source", "Pick the price to watch: last price, bid or ask.")
    if source != "ltp" and segment in sessions.INDEX_SEGMENTS:
        raise SpecError("source", "An index has no bid or ask. Watch its last price instead.")
    return {"op": op, "level": str(level), "source": source}


def _compile_price(params: Mapping[str, Any], grain: Grain) -> Compiled:
    mode, direction = PRICE_OPS[params["op"]]
    level = to_decimal(params["level"])
    source = params.get("source", "ltp")
    feed = "ltpc" if source == "ltp" else "full"  # the bid and ask come in full mode only
    return Compiled(mode, direction, source, grain.up(level), grain.down(level), feed)


KINDS: dict[str, Kind] = {
    "price": Kind(_check_price, _compile_price, lambda p: OP_WORDS[p["op"]], ONCE),
}


def _grain(segment: str, tick_size) -> Grain:
    grain = Grain.of(tick_size)
    if grain is None and segment in sessions.INDEX_SEGMENTS:
        grain = Grain.of(INDEX_TICK)
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
