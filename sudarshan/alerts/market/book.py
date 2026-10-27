"""Every Active alert by instrument key (M10.3): one feed tick in, decisions out. Pure.

The runtime hands each tick from the feed to ``on_tick`` with the time and what it knows about the
market (clock.Market), and calls ``on_clock`` about once a second for what time alone decides: a
candle's close, an end date, an expiry (a Paused alert's too: it stops at its end). Both return what happened. The Book keeps every alert's
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

A stock that closes by auction (``Market.auction``, a CAS key) is judged on its own session,
which ends before the auction; after it its quotes are never judged. The auction's print
(clock.auction) is judged once a day, as itself, by price-level and % move alerts, and their fire
says it was the closing auction. It is the official close, so it belongs to the day's last candle
on the cash session's grid (the 15:15-15:30 candle, the day): an alert at candle close judges
that candle when the print arrives, or, with no print, when the print's time is over
(cross-design X6c). A day high or low and a VWAP take continuous trades only.

The print is a trade (X6i, the owner's probe of 27 Oct 2026): a frame of a CAS key counts as it
only when its cumulative volume (``vtt``) rose against the key's last frame. From 15:20 Upstox
sends such a key one frame a minute with its ltt on the minute, the last continuous price and the
auction's crossed bid and ask; their volume does not move, so they are never a trade, a print or
judged (NOT_A_TRADE). The volume comes in full mode only, so the runtime claims a CAS key in it
(``modes(full=...)``); without a volume no frame is taken for the print, and an alert at candle
close judges its last candle when the print's time is over.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import NamedTuple

from sudarshan.alerts.market import conditions
from sudarshan.alerts.market.clock import (
    AUCTION_PRINT,
    OK,
    Market,
    auction,
    candle,
    gate,
    within,
)
from sudarshan.alerts.market.engine import (
    CLOSE_CHECK_MS,
    Obs,
    Outcome,
    Reason,
    State,
    carries,
    close,
    new_session,
    step,
    timer,
)
from sudarshan.alerts.market.grain import paise
from sudarshan.alerts.market.sessions import AUCTION, day_key
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
#: The kinds a closing auction's print is judged by (not at candle close).
AUCTION_KINDS = frozenset({"price", "move"})
#: A CAS key's frame in its auction's time whose volume did not rise: the auction's book, never
#: a trade (X6i).
NOT_A_TRADE = "not_a_trade"


def _ms(value) -> int | None:
    """An epoch-ms field of a frame; None unless it is a finite number."""
    if value.__class__ is int:
        return value
    return int(value) if value.__class__ is float and value - value == 0 else None


def _volume(value) -> int | None:
    """A frame's cumulative volume (vtt); None unless it is a finite number of at least 0."""
    count = _ms(value)
    return count if count is not None and count >= 0 else None


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
        #: Paused alerts with an end date or a contract's expiry: ``on_clock`` stops them at it.
        self._paused: dict[int, Alert] = {}
        #: CAS key -> the day (YYYYMMDD) its closing auction's print was judged: once a day.
        self._printed: dict[str, int] = {}
        #: Instrument key -> the cumulative volume of its last frame, when the feed sends one.
        self._volumes: dict[str, int] = {}

    # ---- the alerts ------------------------------------------------------------------------
    def put(self, alert: Alert, state: State | None = None) -> None:
        """Judge ``alert`` from now on if it is Active, with ``state`` (default: the one kept);
        a Paused one is only watched for its end date and its contract's expiry.

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
        elif alert.status == "paused" and (alert.ends_at, alert.expires_at) != (None, None):
            self._paused[alert.id] = alert

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

    def printed(self, key: str, today: int) -> bool:
        """Whether today's closing auction print of ``key`` has come (the page's words)."""
        return self._printed.get(key) == today

    def alerts(self) -> list[Alert]:
        """The Active alerts, by id."""
        return sorted(self._active.values(), key=lambda alert: alert.id)

    def modes(self, full: frozenset[str] = frozenset()) -> dict[str, str]:
        """Instrument key -> the feed mode its Active alerts need: the alert service's claim.
        A key in ``full`` (a CAS key: its print is known by its volume, X6i) is claimed in full
        mode whatever its alerts need."""
        modes: dict[str, str] = {}
        for alert in self._active.values():
            if modes.get(alert.key) != "full":
                modes[alert.key] = "full" if alert.key in full else alert.cond.feed
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
        cls = segment + AUCTION if key in market.auction else segment  # its session's key
        ltp, ltt = frame.get("ltp"), _ms(frame.get("ltt"))
        volume, last = _volume(frame.get("vtt")), self._volumes.get(key)
        if volume is not None:
            self._volumes[key] = volume
        trade = quote = None
        if ltp is not None and ltt is not None:
            identity = (ltt, ltp, frame.get("ltq"))
            if identity != self._trades.get(key):
                trade = gate(market, cls, ltt, recv, now, trade=True)
                if trade != OK and cls != segment:  # the closing auction's print?
                    trade = auction(market, segment, ltt, recv, now)
                    rose = volume is not None and last is not None and volume > last
                    if trade == AUCTION_PRINT and not rose:
                        trade = NOT_A_TRADE  # its volume did not move: the auction's book
                if trade == OK or trade == AUCTION_PRINT:
                    self._trades[key] = identity
                if trade == AUCTION_PRINT:
                    if self._printed.get(key) == market.today:
                        trade = None  # the day's print is judged once
                    else:
                        self._printed[key] = market.today
        outcomes: list[Outcome] = []
        judged = bad = False
        states, today = self._states, market.today
        pauses = market.pauses and _session_pauses(market, cls)
        for alert in alerts:
            cond = alert.cond
            source = cond.source
            printed = False
            if source == "ltp":
                if trade != OK and (trade != AUCTION_PRINT or alert.kind not in AUCTION_KINDS):
                    continue
                raw, at, printed = ltp, ltt, trade == AUCTION_PRINT
            else:
                raw = frame.get(source)  # "bid" or "ask"
                if raw is None:
                    continue
                if quote is None:
                    quote = gate(market, cls, recv, recv, now, trade=False)
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
                grid = market.sessions[segment]  # the cash grid, a CAS key's too
                k, start, end = candle(grid, alert.candle_min, grid.end - 1 if printed else at)
                due = None
                if cls != segment and end == grid.end and alert.kind in AUCTION_KINDS:
                    due = market.sessions[cls].auction_until + CLOSE_CHECK_MS  # waits for it
                obs = Obs(value, at, today, up, down, k, start, end, due)
            else:
                obs = Obs(value, at, today, up, down)
            out = step(alert, start_state, obs, now, market.previous, pauses)
            if got is not None:
                out = _context(alert, old, out, got)
            done = None
            if printed and alert.frequency == AT_CLOSE:
                done = _close_at_print(alert, out, obs.candle, now, pauses)
            elif printed and out.fire is not None:
                out = out._replace(fire=out.fire._replace(auction=True))
            final = (out if done is None else done).state
            if final is not old:
                states[alert.id] = final
                self._changed[alert.id] = (alert.version, final)
            for told in (out,) if done is None else (out, done):
                if told.fire is not None or told.status is not None or told.note is not None:
                    outcomes.append(told)
                    if told.status is not None:  # it stopped or paused itself: judged no more
                        self._retire(alert.id, told.status)
        if judged:
            return TickResult(OK, tuple(outcomes))
        check = BAD_VALUE if bad else next((c for c in (trade, quote) if c), NOTHING_NEW)
        return TickResult(check, tuple(outcomes))

    def on_clock(self, now: int, market: Market) -> list[Outcome]:
        """Candles due at their close, ends and expiries (a Paused alert's too); call it about
        once a second."""
        outcomes: list[Outcome] = []
        clipped: dict[str, tuple] = {}
        for alert in [*self._active.values(), *self._paused.values()]:
            state = self._states[alert.id]
            pauses = market.pauses
            if pauses:
                pauses = clipped.get(alert.segment)
                if pauses is None:
                    pauses = clipped[alert.segment] = _session_pauses(market, alert.segment)
            out = timer(alert, state, now, market.today, pauses)
            if out is not None:
                self._apply(alert, _with_base(alert, state, out), outcomes)
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
        cls = alert.segment + AUCTION if alert.key in market.auction else alert.segment
        session = market.sessions.get(cls)
        today = (
            day == market.today
            and session is not None
            and (
                session.start <= at < session.end
                or (source == "ltp" and session.end <= at <= (session.auction_until or 0))
            )
        )  # a CAS key's print (the official close) seeds too
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
        self._paused.pop(alert_id, None)
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
            self._printed.pop(key, None)
            self._volumes.pop(key, None)


def _close_at_print(
    alert: Alert, out: Outcome, last: int, now: int, pauses: tuple
) -> Outcome | None:
    """At candle close: the closing auction's price is the day's last candle's close, so that
    candle is judged as the print arrives (cross-design X6c), its fire marked as the auction's."""
    f = out.state.forming
    if out.status is not None or f is None or f.judged or f.candle != last:
        return None
    done = _with_base(alert, out.state, close(alert, out.state, now, pauses))
    if done.fire is not None:
        done = done._replace(fire=done.fire._replace(auction=True))
    return done


def _with_base(alert: Alert, state: State, out: Outcome) -> Outcome:
    """A % move judged at a candle's close says its base, as one judged on a trade does."""
    derived = state.derived
    if out.fire is not None and alert.kind == "move" and derived is not None:
        out = out._replace(fire=out.fire._replace(base=derived[2]))
    return out


def _session_pauses(market: Market, segment: str) -> tuple:
    """Today's pauses as they fall inside the segment's session (clock.within)."""
    session = market.sessions.get(segment)
    return within(market.pauses, session.start, session.end) if session is not None else ()


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
