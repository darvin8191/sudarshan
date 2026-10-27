"""Conditions that take their level from the feed (M10.2): % move, day high or low, VWAP. Pure.

Each turns one feed frame into what the engine judges: the value in paise and the edges of the
level at that moment (a fixed price level needs nothing from here; spec compiles its edges once).

% move. The base is the previous close (``cp``, as the books use it) or today's open
(``ohlc.open``). The level for "up X%" is base x (1 + X/100) rounded up to the paisa, for "down
X%" base x (1 - X/100) rounded down, worked out in Decimal once a session and again if the base
changes (Upstox can correct ``cp``). It is a level check, so a gap beyond it fires at the first
trade.

Day high or low. A value breaks the high when it is above the day's high as the alert last saw
it (equal is not a break), at or after the start time. The value judged is the higher of the trade
and the feed's own day high, so a break between two ticks, or inside a pause, is still seen: the
high the alert remembers is kept in its state (market.db), and the first value above it after a
pause fires, marked. On a special day the start time moves with the session's open: 09:30 is 15
minutes after it.

VWAP. Upstox's average traded price (``atp``) is the session's VWAP. The side is that of the
trade against it, and starts again each session. The VWAP is not on the paisa grid (100.226), so
its edges are taken as a fixed level's are: rounded up for "above" and down for "below". A trade
below the VWAP is never "at" it, and the VWAP drifting while the price stands still moves no
side. An index has no traded volume, so no VWAP: spec refuses one.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import NamedTuple

from sudarshan.alerts.market.clock import Market
from sudarshan.alerts.market.engine import State
from sudarshan.alerts.market.grain import edge_down, edge_up, paise, to_decimal
from sudarshan.alerts.market.spec import UP, Alert

WAITING_CLOSE = "waiting_close"
WAITING_OPEN = "waiting_open"
WAITING_VWAP = "waiting_vwap"
#: An edge no value reaches: the level is not live yet (before the start time, or unseeded).
NEVER = 1 << 62


class Edges(NamedTuple):
    value: int
    up: int
    down: int
    #: The condition's working values for the session, kept in the alert's state.
    derived: tuple | None = None
    #: For the words: a % move's base, or the day's high or low before this value.
    base: int | None = None
    #: The last trade, when ``value`` is not it (a day high taken from the feed's own high).
    price: int | None = None


def edges(alert: Alert, state: State, frame: Mapping, value: int, at: int, market: Market):
    """The Edges for this frame, or a waiting code when the feed has not sent what it needs."""
    kind = alert.kind
    if kind == "move":
        return _move(alert, state, frame, value, market.today)
    if kind == "day_break":
        return _break(alert, state, frame, value, at, market)
    atp = frame.get("atp")  # vwap
    if paise(atp) is None:  # not sent yet, 0, or not a number
        return WAITING_VWAP
    vwap = to_decimal(atp)
    return Edges(value, edge_up(vwap), edge_down(vwap))


def _move(alert: Alert, state: State, frame: Mapping, value: int, today: int):
    if alert.params["base"] == "close":
        raw, waiting = frame.get("cp"), WAITING_CLOSE
    else:
        ohlc = frame.get("ohlc")
        raw, waiting = (ohlc.get("open") if isinstance(ohlc, Mapping) else None), WAITING_OPEN
    derived = state.derived
    if derived is not None and derived[0] == today and derived[1] == raw:
        return Edges(value, derived[3], derived[4], derived, derived[2])
    base = paise(raw)
    if base is None:
        return waiting
    exact = to_decimal(raw)
    rise, fall = alert.cond.extra
    up, down = edge_up(exact * rise), edge_down(exact * fall)
    return Edges(value, up, down, (today, raw, base, up, down), base)


def _break(alert: Alert, state: State, frame: Mapping, value: int, at: int, market: Market):
    ohlc = frame.get("ohlc")
    high = alert.cond.direction == UP
    day = paise(ohlc.get("high" if high else "low")) if isinstance(ohlc, Mapping) else None
    remembered = state.ref if state.session == market.today else None
    session = market.sessions[alert.segment]
    live = remembered is not None and at >= session.start + alert.cond.extra[0] * 60_000
    seen = [v for v in (value, day, remembered) if v is not None]
    if high:
        return Edges(max(seen), remembered + 1 if live else NEVER, -NEVER, None, remembered, value)
    return Edges(min(seen), NEVER, remembered - 1 if live else -NEVER, None, remembered, value)
