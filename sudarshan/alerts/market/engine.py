"""The market alert engine (M10.2, M10.3): one alert's decision on one observation, pure.

No I/O of any kind, like services/risk/: no database, no feed, no clock, no logging. Every input
arrives as an argument and every decision leaves as a return value, so the service, the replay
check and the golden vectors (sudarshan/alerts/tests/market_vectors.json) run the same code.

An observation is one value in whole ticks at one moment: a new trade at its own time, or a quote
at the time it arrived, with the edges of the alert's level (grain.py). ``step`` judges it against
what the alert remembered from the previous observation and returns the new state with what
happened: a fire, an automatic stop, or nothing.

Sides. A crossing keeps the side its last value stood on: below (-1), at (0) or above (1) the
level. "Crosses above" fires on a move from below to at-or-above, "crosses below" on a move from
above to at-or-below, "crosses" on either; a move away from "at" does not fire, and the first value
only seeds the side. A level check ("is at or above") fires on every value that meets it, the first
included, as its frequency allows.

Frequency: once (then Stopped); once per candle (judged by the observation's candle); at candle
close (the candle's last value, judged once a later trade or the timer shows the candle has ended;
a crossing compares it with the previous close); every time. After any fire nothing fires until
the wait between alerts is over, and a crossing inside the wait is used up. The 51st fire in a
session pauses the alert instead; its end date or its contract's expiry stops it.

Gaps (a fixed price level's crossing only). The side survives a session: the first value of the
next one fires on a crossing as an opening ("opened above 100.00, previous 99.90"); a side from
any older session only seeds. Inside a session, a change first seen after a pause in checking
(clock.py) fires marked with that pause, and a candle the pause touched is not judged at its close.
"""

from __future__ import annotations

from typing import NamedTuple

from sudarshan.alerts.market.clock import Pause, paused_between, paused_during
from sudarshan.alerts.market.spec import (
    AT_CLOSE,
    CROSS,
    DOWN,
    EITHER,
    ONCE,
    PER_CANDLE,
    UP,
    Alert,
)

#: An alert that would fire more often than this in one session is paused instead.
FIRE_LIMIT = 50
#: With no later trade, a timer judges a candle's close this long after the candle ends.
CLOSE_CHECK_MS = 2000
#: A note on the alert for the page: its candle could not be judged (a pause touched it).
NOT_CHECKED = "not_checked"


class Forming(NamedTuple):
    """At candle close: the candle still forming, and the last value seen in it."""

    candle: int
    start: int
    end: int
    value: int
    at: int
    up: int
    down: int


class State(NamedTuple):
    """What one alert carries between observations. Immutable: every step returns a new one.

    The first nine fields are market.db's ``alert_state`` row. ``forming`` and ``derived`` live
    in memory only: after a restart the feed rebuilds them.
    """

    #: The session (YYYYMMDD) of the last observation; 0 before the first.
    session: int = 0
    #: Where the last value stood: -1 below, 0 at, 1 above (a level check: the way it was met).
    side: int | None = None
    #: The last value in ticks (a day high or low break keeps that high or low here).
    ref: int | None = None
    seen_at: int | None = None
    last_fired_at: int | None = None
    last_fired_candle: int | None = None
    fires: int = 0
    fires_session: int = 0
    #: ``fires_session`` when this session's allowance started: 0, or the count at a resume.
    quota_base: int = 0
    forming: Forming | None = None
    #: A condition's working values for the session (conditions.py).
    derived: tuple | None = None


class Obs(NamedTuple):
    """One value to judge, with the edges of the alert's level at that moment."""

    value: int
    #: Epoch ms on the observation's clock: the trade's time, or the quote's arrival.
    at: int
    session: int
    up: int
    down: int
    #: The candle holding ``at``, with its start and end (once per candle, at candle close).
    candle: int = 0
    start: int = 0
    end: int = 0


class Fire(NamedTuple):
    alert_id: int
    #: The once-only key of this fire in the alert log (market.db ``alert_event.claim``).
    claim: str
    session: int
    candle: int
    #: When the engine decided, and the time of the value that fired.
    at: int
    trade_time: int
    value: int
    #: The edge the value met, in ticks, and the way: UP or DOWN.
    level: int
    direction: int
    #: The fire's number in its session, from 1.
    n: int
    #: An opening line's previous value: the last one of the session before.
    previous: int | None = None
    #: The pause (start, end) this was seen after, when it may have happened inside it.
    found_later: tuple[int, int] | None = None
    opening: bool = False


class Reason(NamedTuple):
    """Why an alert stopped, paused or was not judged, and the moment it applies to.

    Codes: fired_once, ended, expired, fire_limit; and NOT_CHECKED with the candle's start.
    """

    code: str
    at: int


class Outcome(NamedTuple):
    alert_id: int
    state: State
    fire: Fire | None = None
    #: "stopped" or "paused" when the alert stops itself, with the reason.
    status: str | None = None
    reason: Reason | None = None
    #: Something the page shows on the alert, such as a candle it could not judge.
    note: Reason | None = None


def step(
    alert: Alert,
    state: State,
    obs: Obs,
    now: int,
    previous: int = 0,
    pauses: tuple[Pause, ...] = (),
) -> Outcome:
    """Judge one observation for one alert, at ``now`` (epoch ms).

    ``previous`` is the trading day before today (YYYYMMDD) and ``pauses`` today's pauses in
    checking: clock.Market carries both.
    """
    if alert.status != "active":
        return Outcome(alert.id, state)
    expires, ends = alert.expires_at, alert.ends_at
    if (expires is not None and obs.at >= expires) or (ends is not None and obs.at >= ends):
        return _stop(alert, state, now, stop_reason(alert, obs.at), pauses)
    if alert.frequency == AT_CLOSE:
        return _at_close(alert, state, obs, now, previous, pauses)
    opening = False
    if obs.session != state.session:
        opening = carries(alert, state, previous)
        state = new_session(state, obs.session, opening)
    return judge(alert, state, obs, now, pauses, opening)


def timer(alert: Alert, state: State, now: int, pauses: tuple[Pause, ...] = ()) -> Outcome | None:
    """What the time alone decides: a candle to judge at its close, or an end. None: nothing."""
    if alert.status != "active":
        return None
    stop = stop_reason(alert, now)
    if stop is not None:
        return _stop(alert, state, now, stop, pauses)
    if state.forming is not None and now >= state.forming.end + CLOSE_CHECK_MS:
        return close(alert, state, now, pauses)
    return None


def stop_reason(alert: Alert, at: int) -> Reason | None:
    """The contract expired, or the end date passed, at ``at``."""
    if alert.expires_at is not None and at >= alert.expires_at:
        return Reason("expired", alert.expires_at)
    if alert.ends_at is not None and at >= alert.ends_at:
        return Reason("ended", alert.ends_at)
    return None


def carries(alert: Alert, state: State, previous: int) -> bool:
    """A fixed level's crossing keeps its side into the next session, and only that one."""
    return (
        alert.kind == "price"
        and alert.cond.mode == CROSS
        and state.side is not None
        and previous != 0
        and state.session == previous
    )


def new_session(state: State, session: int, keep_side: bool = False) -> State:
    """A session's first observation: the session's counts start at 0, and the side seeds
    again unless it carries (``carries``)."""
    side = state.side if keep_side else None
    return State(session, side, state.ref, state.seen_at, state.last_fired_at, None, state.fires)


def resume(state: State) -> State:
    """Resumed by the owner: the next value only seeds, and a new allowance of fires starts."""
    s = state
    return State(s.session, None, None, None, s.last_fired_at, s.last_fired_candle, s.fires,
                 s.fires_session, s.fires_session)  # fmt: skip


def judge(
    alert: Alert,
    state: State,
    obs: Obs,
    now: int,
    pauses: tuple[Pause, ...] = (),
    opening: bool = False,
) -> Outcome:
    """The condition on one value: the side it stands on, and whether that is an event."""
    value, up, down = obs.value, obs.up, obs.down
    direction = alert.cond.direction
    before = state.side
    if alert.cond.mode == CROSS:
        side = -1 if value < up else (1 if value > down else 0)
        event = 0
        if before is not None:
            if before < 0 <= side:
                event = UP
            elif before > 0 >= side:
                event = DOWN
            if direction != EITHER and event != direction:
                event = 0
        changed = True
    else:
        if value >= up and direction != DOWN:
            side = UP
        elif value <= down and direction != UP:
            side = DOWN
        else:
            side = 0
        event = side
        changed = before != side
    s = state
    state = State(s.session, side, value, obs.at, s.last_fired_at, s.last_fired_candle, s.fires,
                  s.fires_session, s.quota_base, s.forming, s.derived)  # fmt: skip
    if not event:
        return Outcome(alert.id, state)
    if opening:
        return _fire(alert, state, obs, now, event, s.ref, None, True)
    found = paused_between(pauses, s.seen_at, obs.at) if pauses and changed else None
    return _fire(alert, state, obs, now, event, None, found, False)


def _fire(alert, state, obs, now, event, previous, found, opening) -> Outcome:
    s = state
    if s.last_fired_at is not None and obs.at - s.last_fired_at < alert.cooldown_ms:
        return Outcome(alert.id, state)  # inside the wait: this one is used up
    if alert.frequency == PER_CANDLE and s.last_fired_candle == obs.candle:
        return Outcome(alert.id, state)
    if s.fires_session - s.quota_base >= FIRE_LIMIT:
        return Outcome(alert.id, state, status="paused", reason=Reason("fire_limit", obs.at))
    n = s.fires_session + 1
    level = obs.up if event == UP else obs.down
    fire = Fire(alert.id, claim(alert, obs.session, obs.candle, n), obs.session, obs.candle, now,
                obs.at, obs.value, level, event, n, previous, found, opening)  # fmt: skip
    state = State(s.session, s.side, s.ref, s.seen_at, obs.at, obs.candle, s.fires + 1, n,
                  s.quota_base, s.forming, s.derived)  # fmt: skip
    if alert.frequency == ONCE:
        return Outcome(alert.id, state, fire, "stopped", Reason("fired_once", obs.at))
    return Outcome(alert.id, state, fire)


def claim(alert: Alert, session: int, candle: int, n: int) -> str:
    """The fire's once-only key. The alert's version is in it: an edited condition (a new
    version) may fire again where the old one already did."""
    head = f"{alert.id}:v{alert.version}"
    if alert.frequency == ONCE:
        return f"{head}:once"
    if alert.frequency == PER_CANDLE:
        return f"{head}:{session}:c{candle}"
    if alert.frequency == AT_CLOSE:
        return f"{head}:{session}:x{candle}"
    return f"{head}:{session}:{n}"


def close(alert: Alert, state: State, now: int, pauses: tuple[Pause, ...] = ()) -> Outcome:
    """Judge the forming candle at its close: its last value, timed at the candle's end.

    A candle that a pause touched is not judged: its close may not be its last trade, and the
    side stays where the last judged close left it."""
    f = state.forming
    state = state._replace(forming=None)
    if pauses and paused_during(pauses, f.start, f.end):
        return Outcome(alert.id, state, note=Reason(NOT_CHECKED, f.start))
    out = judge(alert, state, Obs(f.value, f.end, state.session, f.up, f.down, f.candle,
                f.start, f.end), now, pauses)  # fmt: skip
    if out.fire is not None:
        out = out._replace(fire=out.fire._replace(trade_time=f.at))
    return out


def _at_close(alert, state, obs, now, previous, pauses) -> Outcome:
    """A trade for an alert judged at candle close: it ends the forming candle or adds to it."""
    done = None
    forming = state.forming
    if forming is not None and obs.session == state.session and obs.candle < forming.candle:
        return Outcome(alert.id, state)  # older than the forming candle: never reopen a candle
    if forming is not None and (obs.session != state.session or obs.candle != forming.candle):
        done = close(alert, state, now, pauses)
        if done.status is not None:
            return done
        state = done.state
    if obs.session != state.session:
        state = new_session(state, obs.session, carries(alert, state, previous))
    s = state
    forming = Forming(obs.candle, obs.start, obs.end, obs.value, obs.at, obs.up, obs.down)
    state = State(s.session, s.side, s.ref, s.seen_at, s.last_fired_at, s.last_fired_candle,
                  s.fires, s.fires_session, s.quota_base, forming, s.derived)  # fmt: skip
    if done is None:
        return Outcome(alert.id, state)
    return Outcome(alert.id, state, done.fire, note=done.note)


def _stop(alert, state, now, stop, pauses) -> Outcome:
    """Stop at an end or an expiry; a forming candle that ended by then is judged first."""
    if state.forming is not None and state.forming.end <= stop.at:
        done = close(alert, state, now, pauses)
        if done.status is not None:
            return done
        return Outcome(alert.id, done.state, done.fire, "stopped", stop, done.note)
    return Outcome(alert.id, state, status="stopped", reason=stop)
