"""Every Active alert by instrument key (M10.3): one feed tick in, decisions out. Pure.

The runtime hands each tick from the feed to ``on_tick`` with the time and what it knows about the
market (clock.Market), and calls ``on_clock`` about once a second for what time alone decides: a
candle's close, an end date, an expiry. Both return what happened. The Book keeps every alert's
state in memory; ``changed`` hands over the states to save in the 1 s flush, each with the
alert's version it was judged at, so the store drops one an edit has overtaken (a fire's state
also rides in its outcome, for the transaction that records the fire). Nothing here reads a
file, the clock or the network.

A tick is a feed frame as the bus carries it (sudarshan/feed/protocol.py): the key's full current
state as a dict. The Book reads only the fields the alerts on that key need, and checks each where
it uses it, so a frame is never converted whole.

Which values an alert judges: one on the last price judges a new trade only (its time, price or
quantity changed), at the trade's own time; one on the bid or ask judges every tick that carries
it, at the tick's arrival (``ts_recv``). A tick is judged only when the gate lets it through.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import NamedTuple

from sudarshan.alerts.market import conditions
from sudarshan.alerts.market.clock import OK, Market, candle, gate
from sudarshan.alerts.market.engine import (
    Obs,
    Outcome,
    Reason,
    State,
    carries,
    new_session,
    step,
    timer,
)
from sudarshan.alerts.market.grain import paise
from sudarshan.alerts.market.sessions import day_key
from sudarshan.alerts.market.spec import AT_CLOSE, BREAK, CROSS, PER_CANDLE, Alert
from sudarshan.autologin.schedule import IST

#: A tick with nothing new for any alert on its key: no new trade, no quote one watches.
NOTHING_NEW = "nothing_new"
#: A value that is not a price (0, negative, not finite): dropped and counted.
BAD_VALUE = "bad_value"
#: The frequencies judged by candle.
CANDLED = frozenset({PER_CANDLE, AT_CLOSE})
#: A note: a % move's base changed inside the session, so its level was worked out again.
BASE_CHANGED = "base_changed"


def _ms(value) -> int | None:
    """An epoch-ms field of a frame; None unless it is a finite number."""
    if value.__class__ is int:
        return value
    return int(value) if value.__class__ is float and value - value == 0 else None


class TickResult(NamedTuple):
    #: clock.OK when an alert judged the tick; otherwise why none did (a gate code, BAD_VALUE
    #: or NOTHING_NEW), for the runtime's counters.
    check: str
    #: Only the outcomes that tell something: a fire, a stop, a note.
    outcomes: tuple[Outcome, ...] = ()


class Book:
    def __init__(self) -> None:
        self._active: dict[int, Alert] = {}
        self._states: dict[int, State] = {}
        #: Instrument key -> its Active alerts, by id; ``_keys`` holds the same as tuples.
        self._groups: dict[str, dict[int, Alert]] = {}
        self._keys: dict[str, tuple[Alert, ...]] = {}
        self._trades: dict[str, tuple] = {}
        #: Alert id -> (the version it was judged at, its state), to save in the next flush.
        self._changed: dict[int, tuple[int, State]] = {}
        #: Alert id -> what it waits for from the feed (conditions.WAITING_*), while it waits.
        self._waiting: dict[int, str] = {}
        #: Stopped alerts: their state is kept only until ``changed`` has handed it over (a
        #: Paused one keeps its state, which ``engine.resume`` needs).
        self._stopped: set[int] = set()

    # ---- the alerts ------------------------------------------------------------------------
    def put(self, alert: Alert, state: State | None = None) -> None:
        """Judge ``alert`` from now on if it is Active, with ``state`` (default: the one kept).

        The alert's state, given or kept, is queued for the next flush under this version, in
        place of anything queued before: an edit's reset or a resume reaches market.db even
        when no tick follows, and a state judged under the old version never overwrites it."""
        if state is not None:
            self._states[alert.id] = state
        kept = self._states.setdefault(alert.id, State())
        if alert.status != "stopped":
            self._changed[alert.id] = (alert.version, kept)
        self._retire(alert.id, alert.status)
        if alert.status == "active":
            self._active[alert.id] = alert
            self._groups.setdefault(alert.key, {})[alert.id] = alert
            self._index(alert.key)

    def drop(self, alert_id: int) -> State | None:
        """Forget a deleted alert; returns its last state."""
        self._retire(alert_id)
        self._changed.pop(alert_id, None)
        self._stopped.discard(alert_id)
        return self._states.pop(alert_id, None)

    def state(self, alert_id: int) -> State | None:
        return self._states.get(alert_id)

    def waiting(self, alert_id: int) -> str | None:
        """What the alert waits for from the feed ("Waiting for the previous close"), if any."""
        return self._waiting.get(alert_id)

    def alerts(self) -> list[Alert]:
        """The Active alerts, by id."""
        return sorted(self._active.values(), key=lambda alert: alert.id)

    def modes(self) -> dict[str, str]:
        """Instrument key -> the feed mode its Active alerts need: the alert service's claim."""
        modes: dict[str, str] = {}
        for alert in self._active.values():
            if modes.get(alert.key) != "full":
                modes[alert.key] = alert.cond.feed
        return modes

    def changed(self) -> dict[int, tuple[int, State]]:
        """The states changed since the last call, to save: alert id -> (version, state). The
        Book forgets the list, and the states of the alerts that stopped (the store has them from
        here on)."""
        changed, self._changed = self._changed, {}
        for alert_id in self._stopped:
            self._states.pop(alert_id, None)
        self._stopped.clear()
        return changed

    # ---- what happens ----------------------------------------------------------------------
    def on_tick(self, frame: Mapping, now: int, market: Market) -> TickResult:
        """One frame from the feed, judged at ``now`` (epoch ms)."""
        key = frame.get("key")
        alerts = self._keys.get(key)
        if not alerts:
            return TickResult(NOTHING_NEW)
        recv = _ms(frame.get("ts_recv"))
        recv = now if recv is None else recv
        segment = alerts[0].segment
        ltp, ltt = frame.get("ltp"), _ms(frame.get("ltt"))
        trade = quote = None
        if ltp is not None and ltt is not None:
            identity = (ltt, ltp, frame.get("ltq"))
            if identity != self._trades.get(key):
                trade = gate(market, segment, ltt, recv, now, trade=True)
                if trade == OK:
                    self._trades[key] = identity
        outcomes: list[Outcome] = []
        judged = bad = False
        states, today = self._states, market.today
        for alert in alerts:
            cond = alert.cond
            source = cond.source
            if source == "ltp":
                if trade != OK:
                    continue
                raw, at = ltp, ltt
            else:
                raw = frame.get(source)  # "bid" or "ask"
                if raw is None:
                    continue
                if quote is None:
                    quote = gate(market, segment, recv, recv, now, trade=False)
                if quote != OK:
                    continue
                at = recv
            value = paise(raw)
            if value is None:
                bad = True
                continue
            judged = True
            old = start_state = states[alert.id]
            up, down, got = cond.up, cond.down, None
            if up is None:  # a level taken from the feed (conditions.py)
                got = conditions.edges(alert, old, frame, value, at, market)
                if got.__class__ is str:
                    self._wait(alert.id, got, at, outcomes)
                    continue
                self._waiting.pop(alert.id, None)
                value, up, down = got.value, got.up, got.down
                if got.derived is not old.derived:
                    start_state = old._replace(derived=got.derived)
            if alert.frequency in CANDLED:
                k, start, end = candle(market.sessions[segment], alert.candle_min, at)
                obs = Obs(value, at, today, up, down, k, start, end)
            else:
                obs = Obs(value, at, today, up, down)
            out = step(alert, start_state, obs, now, market.previous, market.pauses)
            if got is not None:
                out = _context(alert, old, out, got)
            if out.state is not old:
                states[alert.id] = out.state
                self._changed[alert.id] = (alert.version, out.state)
            if out.fire is not None or out.status is not None or out.note is not None:
                outcomes.append(out)
                if out.status is not None:  # it stopped or paused itself: judged no more
                    self._retire(alert.id, out.status)
        if judged:
            return TickResult(OK, tuple(outcomes))
        check = BAD_VALUE if bad else next((c for c in (trade, quote) if c), NOTHING_NEW)
        return TickResult(check, tuple(outcomes))

    def on_clock(self, now: int, market: Market) -> list[Outcome]:
        """Candles due at their close, ends and expiries; call it about once a second."""
        outcomes: list[Outcome] = []
        for alert in list(self._active.values()):
            state = self._states[alert.id]
            out = timer(alert, state, now, market.pauses)
            if out is not None:
                derived = state.derived
                if out.fire is not None and alert.kind == "move" and derived is not None:
                    out = out._replace(fire=out.fire._replace(base=derived[2]))
                self._apply(alert, out, outcomes)
        return outcomes

    def seed(self, alert_id: int, frame: Mapping, market: Market) -> bool:
        """Set a crossing's side from a snapshot, without firing (a new or resumed alert).

        A value from today's session seeds today's side. Yesterday's last value seeds a fixed
        level, so a gap at the next open fires as "opened above". An alert that already has a
        side keeps it: after a restart the saved side is what tells a crossing in the pause.
        """
        alert, state = self._active.get(alert_id), self._states.get(alert_id)
        if alert is None or state.side is not None or alert.frequency == AT_CLOSE:
            return False
        if alert.cond.mode not in (CROSS, BREAK):
            return False
        source = alert.cond.source
        value = paise(frame.get(source))
        at = _ms(frame.get("ltt" if source == "ltp" else "ts_recv"))
        if value is None or at is None:
            return False
        day = day_key(datetime.fromtimestamp(at / 1000, IST).date())
        session = market.sessions.get(alert.segment)
        today = day == market.today and session is not None and session.start <= at < session.end
        before = day == market.previous and carries(alert, State(day, 0), market.previous)
        if not (today or before) or day < state.session:
            return False
        if day != state.session:
            state = new_session(state, day)
        up, down = alert.cond.up, alert.cond.down
        if up is None:  # VWAP, or a day high or low: their level comes from today's frame
            got = conditions.edges(alert, state, frame, value, at, market)
            if got.__class__ is str:
                return False
            value, up, down = got.value, got.up, got.down
        side = 0 if alert.cond.mode == BREAK else (-1 if value < up else (1 if value > down else 0))
        self._states[alert_id] = state._replace(side=side, ref=value, seen_at=at)
        self._changed[alert_id] = (alert.version, self._states[alert_id])
        return True

    # ---- inside ----------------------------------------------------------------------------
    def _wait(self, alert_id: int, code: str, at: int, outcomes: list[Outcome]) -> None:
        """The feed has not sent what the alert needs; the page says so once it starts."""
        if self._waiting.get(alert_id) != code:
            self._waiting[alert_id] = code
            outcomes.append(Outcome(alert_id, self._states[alert_id], note=Reason(code, at)))

    def _apply(self, alert: Alert, out: Outcome, outcomes: list[Outcome]) -> None:
        if out.state is not self._states.get(out.alert_id):
            self._states[out.alert_id] = out.state
            self._changed[out.alert_id] = (alert.version, out.state)
        if out.fire is not None or out.status is not None or out.note is not None:
            outcomes.append(out)
        if out.status is not None:  # it stopped or paused itself: judged no more
            self._retire(out.alert_id, out.status)

    def _retire(self, alert_id: int, status: str | None = None) -> None:
        """Judge the alert no more; its state stays (a stopped one's until the next flush)."""
        self._waiting.pop(alert_id, None)
        if status == "stopped":
            self._stopped.add(alert_id)
        elif status is not None:
            self._stopped.discard(alert_id)
        alert = self._active.pop(alert_id, None)
        if alert is not None:
            self._groups[alert.key].pop(alert_id, None)
            self._index(alert.key)

    def _index(self, key: str) -> None:
        group = self._groups.get(key)
        if group:
            self._keys[key] = tuple(group[i] for i in sorted(group))
        else:
            self._groups.pop(key, None)
            self._trades.pop(key, None)
            self._keys.pop(key, None)


def _context(alert: Alert, old: State, out: Outcome, got: conditions.Edges) -> Outcome:
    """A condition's words on its fire (a % move's base, the high broken), and its notes."""
    if out.fire is not None and (got.base is not None or got.price is not None):
        out = out._replace(fire=out.fire._replace(base=got.base, price=got.price))
    derived = old.derived
    if (
        alert.kind == "move"
        and derived is not None
        and got.derived is not derived
        and derived[0] == got.derived[0]
        and out.note is None
    ):
        out = out._replace(note=Reason(BASE_CHANGED, out.state.seen_at or 0))
    return out
