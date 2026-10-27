"""Every Active alert by instrument key (M10.3): one feed tick in, decisions out. Pure.

The runtime hands each tick from the feed to ``on_tick`` with the time and what it knows about the
market (clock.Market), and calls ``on_clock`` about once a second for what time alone decides: a
candle's close, an end date, an expiry. Both return what happened. The Book keeps every alert's
state in memory; ``changed`` hands over the states to save in the 1 s flush (a fire's state also
rides in its outcome, for the transaction that records the fire). Nothing here reads a file, the
clock or the network.

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

from sudarshan.alerts.market.clock import OK, Market, candle, gate
from sudarshan.alerts.market.engine import Obs, Outcome, State, carries, new_session, step, timer
from sudarshan.alerts.market.sessions import day_key
from sudarshan.alerts.market.spec import AT_CLOSE, CROSS, PER_CANDLE, Alert
from sudarshan.autologin.schedule import IST

#: A tick with nothing new for any alert on its key: no new trade, no quote one watches.
NOTHING_NEW = "nothing_new"
#: A value that is not a price (0, negative, not finite): dropped and counted.
BAD_VALUE = "bad_value"
#: The frequencies judged by candle.
CANDLED = frozenset({PER_CANDLE, AT_CLOSE})


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
        self._changed: dict[int, State] = {}

    # ---- the alerts ------------------------------------------------------------------------
    def put(self, alert: Alert, state: State | None = None) -> None:
        """Judge ``alert`` from now on if it is Active, with ``state`` (default: the one kept)."""
        if state is not None:
            self._states[alert.id] = state
        self._states.setdefault(alert.id, State())
        self._retire(alert.id)
        if alert.status == "active":
            self._active[alert.id] = alert
            self._groups.setdefault(alert.key, {})[alert.id] = alert
            self._index(alert.key)

    def drop(self, alert_id: int) -> State | None:
        """Forget a deleted alert; returns its last state."""
        self._retire(alert_id)
        self._changed.pop(alert_id, None)
        return self._states.pop(alert_id, None)

    def state(self, alert_id: int) -> State | None:
        return self._states.get(alert_id)

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

    def changed(self) -> dict[int, State]:
        """The states changed since the last call, to save; the Book forgets the list."""
        changed, self._changed = self._changed, {}
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
            value = alert.grain.ticks(raw)
            if value is None:
                bad = True
                continue
            judged = True
            if alert.frequency in CANDLED:
                k, start, end = candle(market.sessions[segment], alert.candle_min, at)
                obs = Obs(value, at, today, cond.up, cond.down, k, start, end)
            else:
                obs = Obs(value, at, today, cond.up, cond.down)
            old = states[alert.id]
            out = step(alert, old, obs, now, market.previous, market.pauses)
            if out.state is not old:
                states[alert.id] = self._changed[alert.id] = out.state
            if out.fire is not None or out.status is not None or out.note is not None:
                outcomes.append(out)
                if out.status is not None:  # it stopped or paused itself: judged no more
                    self._retire(alert.id)
        if judged:
            return TickResult(OK, tuple(outcomes))
        check = BAD_VALUE if bad else next((c for c in (trade, quote) if c), NOTHING_NEW)
        return TickResult(check, tuple(outcomes))

    def on_clock(self, now: int, market: Market) -> list[Outcome]:
        """Candles due at their close, ends and expiries; call it about once a second."""
        outcomes: list[Outcome] = []
        for alert in list(self._active.values()):
            out = timer(alert, self._states[alert.id], now, market.pauses)
            if out is not None:
                self._apply(out, outcomes)
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
        if alert.cond.mode != CROSS or alert.cond.up is None:
            return False
        source = alert.cond.source
        value = alert.grain.ticks(frame.get(source))
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
        side = -1 if value < up else (1 if value > down else 0)
        self._states[alert_id] = state._replace(side=side, ref=value, seen_at=at)
        self._changed[alert_id] = self._states[alert_id]
        return True

    # ---- inside ----------------------------------------------------------------------------
    def _apply(self, out: Outcome, outcomes: list[Outcome]) -> None:
        if out.state is not self._states.get(out.alert_id):
            self._states[out.alert_id] = out.state
            self._changed[out.alert_id] = out.state
        if out.fire is not None or out.status is not None or out.note is not None:
            outcomes.append(out)
        if out.status is not None:  # it stopped or paused itself: judged no more
            self._retire(out.alert_id)

    def _retire(self, alert_id: int) -> None:
        """Judge the alert no more (its state stays)."""
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
