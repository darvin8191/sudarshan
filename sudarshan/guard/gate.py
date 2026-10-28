"""One gate in front of every function that sends or changes an order (M5.13).

Four Upstox functions send or change an order (broker/upstox/api/order_api.place_order_api and
modify_order, gtt_api.place_gtt_order and modify_gtt_order), and four sandbox ones do the same.
Every surface reaches the broker only through them, so each carries one decorator from here. While
``SUDARSHAN_RISK_GUARD`` does not cover its pipe (flag.py), the decorator calls the function and
returns its own answer, after one environment lookup and nothing else.

When it covers the pipe, an order passes in three steps:

1. Under the lock: is anything refusing new risk now (the kill switch on, or its state unknown)?
   If not, the order is recorded as a send and goes, with no read.
2. Outside the lock: read what tells an exit from a new order (the broker's own position book;
   for a modify, the order as the broker has it). Database and network work never happens under
   the lock.
3. Under the lock again: the refusing state is checked afresh, the order judged by rules.py
   against the book and the exits this process already sent on that position that the book does
   not show yet (claims), and either recorded or refused. The check and the claim are one hold,
   so two exits judged at once cannot both take the same quantity.

An exit always goes. A refusal is 403 with a sentence (words.py), never 409, which the phone reads
as an earlier exit still being placed (frontend/src/sudarshan/books/sendOutcome.ts), and no
function is called. Each send stays recorded until its answer says nothing was sent, until
``SHOWS_WITHIN_S`` after it was answered or the book shows it, or ``SEND_HELD_AT_MOST_S`` at the
latest. The records are the in-flight registry the kill switch waits on when it is turned on.

If the gate itself breaks, an order passes only when nothing is refusing new risk. While the kill
switch is on, or its state could not be read, it is refused (fail closed). Under the kill switch
or on a locked day, a positions read that fails is answered from the book read in the last
``DAY_BOOK_REUSE_S``; with none, an order that cannot be judged is refused, with a sentence
pointing at the Upstox app for an urgent exit. In every other state an unreadable book lets the
order go, noted, because it may be an exit.

The daily loss limit (M5.14, daily.py) refuses new risk the same way once the day's loss has
reached it: the first order to see the figure at the limit writes the day's lock, and the lock
holds every new order in that pipe until India's date changes, whatever the figure does after.
While a limit is set, each order's step 2 also yields the day's figure, from the same read of the
book. A book that cannot be read leaves the order unjudged, so under the daily limit alone it
goes, and that is noted (an exit must never be refused for a read that failed); a two-leg GTT,
new risk whatever the book says, is refused instead.

The price band (M20.7, band.py) holds a priced order that adds risk to the market: step 2 reads
Upstox's quote for it (kept a second) only when the book shows the order adding risk, and step 3
refuses a price too far on the aggressive side. Exits and MARKET orders are never held to it; no
quote lets the order go, noted, with the exchange's own price check still behind it.

The order size limit (M20.7, size.py) holds the part of an order that adds risk to the owner's
cap, in lots on a derivatives exchange (the lot size read in step 2 from the master contract)
and in shares in cash. A batch (batch.py) is held to the band and the size limit whole before
any part of it is sent (check_together).

The burst brake (M5.14) counts the new orders that add risk in a rolling window, per pipe, and
refuses the next one once the owner's number has gone in it. Exits and changes to an order are
never counted or refused. A batch counts once: its check leaves one credit for every order it
will send that adds risk (each slice of a split), kept under that order's instrument, side and
quantity, so only those orders use them, for ``BATCH_PARTS_WITHIN_S``. The counts live in memory, under
the same lock, and are lost on a restart (design section 11).

The lock is a real one from utils.real_threading, held for dict work only: correct under the
eventlet worker (no I/O, no logging and no wait inside it, so the hub never stalls on it), under
gthread's real threads, and on the development server.
"""

from __future__ import annotations

import functools
import itertools
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from sudarshan.books.exit_guard import EXIT_SHOWS_WITHIN_S
from utils import real_threading
from utils.broker_backpressure import BusyResponse
from utils.logging import get_logger
from utils.thread_safe_cache import LockedTTLCache

from . import band, daily, flag, readers, rules, size, words
from . import store as risk_store
from .store import IST, KillSwitchState

logger = get_logger(__name__)

LIVE, SANDBOX = "live", "sandbox"

#: An answered send counts against its position this long: the time Upstox's book may take to
#: show an exit (the same as sudarshan/books/exit_guard.py).
SHOWS_WITHIN_S = EXIT_SHOWS_WITHIN_S
#: A send counts this long at most, answered or not: the phone's no-answer hold
#: (frontend/src/sudarshan/books/sent.ts).
SEND_HELD_AT_MOST_S = 150.0
#: How soon the kill switch's state is read again after a read failed.
STATE_RETRY_S = 2.0
#: Refusals of one kind are written to the audit at most this often; the rest are counted in
#: memory and written with the next one. A SQLite commit is disk work eventlet cannot make
#: cooperative, so a write per refusal would stall the worker under a runaway script.
NOTE_EVERY_S = 2.0
#: A count kept in memory this long without a refusal is written by the next note or flush.
NOTE_KEPT_S = 60.0
#: The gate's own time is kept for this many decisions, for its p50 and p95 on the status route
#: (sessions S1 and G3 record them).
TIMED_KEPT = 500
#: When Upstox's positions cannot be read just now, the book read this recently serves: for the
#: day's figure (design section 5), and, under the kill switch or on a locked day only, to judge
#: an order (the coordinator's decision d). With none, such an order is refused.
DAY_BOOK_REUSE_S = 15.0
#: A segment's session window, read from the market calendar, is kept this long.
SESSION_KEPT_S = 600.0
#: The daily limit could not be checked this recently: the banner says so.
UNCHECKED_SHOWN_S = 60.0
#: A batch counts once in the burst brake: the orders it sends within this long, each on the
#: instrument, side and quantity the batch named, are not counted again.
BATCH_PARTS_WITHIN_S = 30.0

# Machine codes, for the audit and SUDARSHAN's own endpoints, never for a sentence.
KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
RISK_STATE_UNREADABLE = "RISK_STATE_UNREADABLE"
GUARD_UNAVAILABLE = "GUARD_UNAVAILABLE"
GUARD_ERROR = "GUARD_ERROR"
DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
PRICE_BAND = "PRICE_BAND"
SIZE_LIMIT = "SIZE_LIMIT"
BURST_BRAKE = "BURST_BRAKE"

PLACE, MODIFY, GTT_PLACE, GTT_MODIFY = "place", "modify", "gtt_place", "gtt_modify"
_UNREAD = object()
_NO_QUOTE = object()  # not read: the band did not need the market for this order
BANDED_TYPES = ("LIMIT", "SL", "SL-M")
REFUSED, ANSWERED = "refused", "answered"


@dataclass(frozen=True)
class Order:
    """What one call would do, read from its arguments (and, for a modify, from the broker)."""

    pipe: str
    action: str  # PLACE, MODIFY, GTT_PLACE or GTT_MODIFY
    exchange: str
    symbol: str
    product: str  # OpenAlgo's: MIS, NRML or CNC
    side: int | None
    quantity: int | None
    owner: str  # Live: the broker session's token; sandbox: the user
    ref: str | None = None  # the order or GTT a modify changes
    trigger_type: str = ""  # a GTT's: SINGLE or OCO
    terms: dict = field(default_factory=dict)  # a modify's new terms, as given
    price_type: str = ""  # a place's: MARKET, LIMIT, SL or SL-M (a modify's are in its terms)
    price: float = 0.0
    trigger: float = 0.0

    @property
    def key(self) -> tuple[str, str, str, str]:
        """The position it acts on, as its pipe keeps positions apart."""
        return (
            self.pipe,
            self.exchange,
            self.symbol,
            rules.position_product(self.pipe, self.product),
        )

    @property
    def priced(self) -> rules.OrderTerms | None:
        """The price terms the band is checked on: a place's own, a modify's new ones (known
        once the broker's copy is read). A GTT's are not: it is priced for when it triggers."""
        if self.action == MODIFY:
            return self.terms.get("new")
        if self.action == PLACE:
            return rules.OrderTerms(self.quantity or 0, self.price_type, self.price, self.trigger)
        return None

    @property
    def cnc_sell(self) -> bool:
        return rules.is_cnc_sell(self.exchange, self.product, self.side)

    @property
    def name(self) -> tuple[str, str, str]:
        return (self.exchange, self.symbol, self.product)


@dataclass
class Send:
    """An order this process let go, until it no longer counts against its position."""

    order: Order
    quantity: int
    reducing: bool | None  # None: let go while nothing was refusing, so never judged
    expect: int | None  # an exit: |p| the book shows once it and earlier exits have filled
    position_then: int | None
    admitted: float  # monotonic
    sent_at: float  # epoch, for the sentence
    ref: str | None = None  # the broker's order id, once known
    answered: float | None = None


@dataclass
class _Note:
    """One kind of refusal, as last written to the audit, and how many came since."""

    kind: str
    words: str
    pipe: str
    surface: str
    detail: dict
    written: float  # monotonic
    pending: int = 0


@dataclass(frozen=True)
class Decision:
    refused: bool = False
    words: str = ""
    code: str = ""
    token: int | None = None
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class _Guards:
    """The owner's guards as they stand for one order, read before deciding."""

    day: str = ""  # India's date, YYYY-MM-DD
    settings: risk_store.RiskSettings | None = None  # None: they could not be read
    lock: risk_store.DayLock | None = None  # written today, so it holds whatever the limit is now

    @property
    def limit(self) -> float | None:
        return None if self.settings is None else self.settings.daily_loss_limit

    @property
    def daily(self) -> bool:
        """The daily loss limit is set, or has locked the day."""
        return self.lock is not None or self.limit is not None

    def price_band(self, order: Order) -> band.Band | None:
        """The band ``order``'s price is held to: None when the band is off, the order is not
        priced, or its instrument takes no band."""
        settings, terms = self.settings, order.priced
        if settings is None or not settings.band_on or terms is None:
            return None
        if terms.price_type not in BANDED_TYPES:
            return None
        return band.band_for(band.segment_of(order.symbol, order.exchange), settings)

    def sized(self, order: Order) -> bool:
        """Whether the order size limit holds ``order``."""
        settings = self.settings
        if settings is None or not settings.size_on:
            return False
        return size.unit_of(order.exchange) is not None

    @property
    def braking(self) -> bool:
        """The burst brake is on."""
        return self.settings is not None and self.settings.burst_on

    def braked(self, order: Order) -> bool:
        """Whether the burst brake counts ``order``: a new order or GTT, never a change."""
        return self.braking and order.action in (PLACE, GTT_PLACE)

    def judges(self, order: Order) -> bool:
        """Whether a guard needs ``order`` judged, beyond the kill switch."""
        return (
            self.daily
            or self.price_band(order) is not None
            or self.sized(order)
            or self.braked(order)
        )

    def active(self, order: Order) -> bool:
        """Whether to read anything for ``order``: a modify's instrument and new terms are known
        only once the broker's copy of the order is read."""
        if self.judges(order):
            return True
        settings = self.settings
        if settings is None or order.action not in (MODIFY, GTT_MODIFY):
            return False
        return settings.size_on or (settings.band_on and order.action == MODIFY)


@dataclass
class _Burst:
    """One pipe's admissions that added risk, newest last, and the batches' credits."""

    admitted: deque = field(default_factory=deque)  # monotonic times
    # (exchange, symbol, side, quantity) -> [(until, count)]: orders a batch counted already
    credits: dict = field(default_factory=dict)
    told: float | None = None  # monotonic: when the owner was last told it engaged


def _credit_key(order: Order, quantity: int | None = None) -> tuple:
    """What a batch's credit is kept under: the instrument, side and quantity of one order it
    sends (fields that read the same as the batch names them and as the order is sent)."""
    return (
        order.exchange,
        order.symbol,
        order.side,
        order.quantity if quantity is None else quantity,
    )


@dataclass(frozen=True)
class _Reads:
    """What the owner's limits read for an order adding risk: the market and the lot size."""

    quote: Any = None  # band.Quote; _NO_QUOTE when the band did not need it; None: unreadable
    lot_size: float | None = None


@dataclass(frozen=True)
class _Refusing:
    """What refuses new risk right now."""

    state: KillSwitchState | None  # None: its state could not be read


@dataclass(frozen=True)
class _Facts:
    position: int = 0
    working: rules.WorkingOrder | None = None
    unreadable: bool = False
    pass_through: bool = False  # nothing to judge: the broker answers it (a final order)
    stale: bool = False  # judged on a book kept from before: only while new risk is refused


class Gate:
    """The in-flight registry, the claims and the kill switch's state, under one real lock."""

    def __init__(self, store: risk_store.RiskStore | None = None, read: Any = None):
        self._lock = real_threading.Lock()
        self._sends: dict[int, Send] = {}
        self._tokens = itertools.count(1)
        self._state: KillSwitchState | None = None
        self._state_retry_at = 0.0
        self._rows: tuple[str, float, list] | None = None  # (owner, when, rows): Live only
        self._store = store
        self._read = read or readers
        self._notes: dict[str, _Note] = {}  # audit rows by key, with counts not yet written
        self._timed: deque[float] = deque(maxlen=TIMED_KEPT)  # seconds per decision
        # Today's lock per pipe: (pipe, day) -> the lock, or None when read and not there. Only
        # this process writes locks (one worker), so a None read once stays true until it does.
        self._day_locks: dict[tuple[str, str], risk_store.DayLock | None] = {}
        self._sessions = LockedTTLCache(maxsize=32, ttl=SESSION_KEPT_S)
        self._unchecked: dict[str, float] = {}  # pipe -> monotonic: the daily limit unchecked
        self._bursts: dict[str, _Burst] = {}  # pipe -> its admissions (the burst brake)
        self._problem: tuple[float, str, str] | None = None  # (epoch, kind, words): the last one

    def _the_store(self) -> risk_store.RiskStore:
        return self._store or risk_store.STORE

    # -- the kill switch's state -------------------------------------------------------------

    def _load_state(self) -> None:
        """Read the kill switch's state at first use; while it cannot be read it stays unknown."""
        if self._state is not None:
            return
        now = time.monotonic()
        with self._lock:
            if self._state is not None or now < self._state_retry_at:
                return
            self._state_retry_at = now + STATE_RETRY_S  # one reader at a time
        try:
            state = self._the_store().state()
        except Exception:
            logger.exception("The risk guard could not read the kill switch's state")
            return
        with self._lock:
            if self._state is None:
                self._state = state

    def state(self) -> KillSwitchState | None:
        """The kill switch as this process holds it; None while it could not be read."""
        self._load_state()
        with self._lock:
            return self._state

    def set_state(self, state: KillSwitchState) -> None:
        """Hold ``state`` (the kill switch's activation and resume save it first)."""
        with self._lock:
            self._state = state

    def turn_on(self, state: KillSwitchState) -> tuple[str, KillSwitchState, list[int]]:
        """Turn the switch on under the lock, unless it is on already.

        Every admission checks the switch under this lock, so no order adding risk is let go
        after this returns, and the tokens returned are every send still waiting for its answer.

        Returns:
            ``("on", state, tokens)``; or ``("already", current, [])`` when it was on, or
            ``("running", current, [])`` while an activation is still running its steps.
        """
        with self._lock:
            current = self._state
            if current is not None and current.on:
                return ("running" if current.running else "already"), current, []
            self._state = state
            return "on", state, [t for t, s in self._sends.items() if s.answered is None]

    def turn_off(self, activation_id: str | None, state: KillSwitchState) -> bool:
        """Turn the switch off, only if it is still the activation the owner resumed."""
        with self._lock:
            current = self._state
            if current is None or not current.on or current.running:
                return False
            if current.activation_id != activation_id:
                return False
            self._state = state
            return True

    def settle_restart(self, activation_id: str | None, state: KillSwitchState) -> bool:
        """An activation saved as running by a process that is gone: hold it as settled."""
        with self._lock:
            current = self._state
            if current is None or not current.running or current.activation_id != activation_id:
                return False
            self._state = state
            return True

    def unanswered(self, tokens: list[int]) -> list[int]:
        """Which of ``tokens`` are still waiting for their answer."""
        with self._lock:
            return [t for t in tokens if t in self._sends and self._sends[t].answered is None]

    def sends(self) -> int:
        """How many sends count now (diagnostics and tests)."""
        with self._lock:
            self._forget(time.monotonic())
            return len(self._sends)

    # -- one order -------------------------------------------------------------------------------

    def run(self, kind: _Kind, fn: Callable, args: tuple, kwargs: dict):
        """Admit or refuse one call to ``fn``; a refusal never calls it."""
        decision = self.admit(kind, args, kwargs)
        if decision.refused:
            return kind.refusal(decision.words)
        try:
            result = fn(*args, **kwargs)
        except BaseException:
            self.settle(decision.token, ANSWERED, None)  # it may have reached the broker
            raise
        try:
            answer, ref = kind.outcome(result)
        except Exception:
            answer, ref = ANSWERED, None
        self.settle(decision.token, answer, ref)
        return result

    def admit(self, kind: _Kind, args: tuple, kwargs: dict) -> Decision:
        """Decide on one call. Never raises: a broken check refuses only while new risk is."""
        started = time.monotonic()
        order = None
        try:
            order = kind.describe(*args, **kwargs)
            decision = self._admit(order)
        except Exception:
            logger.exception("The risk check broke on an order")
            decision = self._broke(kind.pipe, order)
        took = time.monotonic() - started
        with self._lock:
            self._timed.append(took)
        if decision.refused or decision.code:
            self._note(kind.pipe, order, decision)
        if decision.code == BURST_BRAKE and decision.detail.get("tell"):
            _tell_owner(decision.detail["tell"])
        return decision

    def settle(self, token: int | None, answer: str, ref: str | None) -> None:
        """Record a call's answer: a refusal drops its send; anything else starts its clock."""
        if token is None:
            return
        with self._lock:
            send = self._sends.get(token)
            if send is None:
                return
            if answer == REFUSED:
                del self._sends[token]
                return
            send.answered = time.monotonic()
            if ref and not send.ref:
                send.ref = str(ref)

    def _admit(self, order: Order) -> Decision:
        self._load_state()
        guards = self._guards(order)  # the settings and today's lock: reads, outside the lock
        with self._lock:
            now = time.monotonic()
            self._forget(now)
            refusing = self._refusing()
            if refusing is None and not guards.active(order):
                return Decision(token=self._register(order, None, now))
        fallback = refusing is not None or guards.lock is not None
        known, facts = self._facts(order, fallback)  # reads: outside the lock
        lock, day_unread, reads = None, False, _Reads(_NO_QUOTE)
        if refusing is None and not facts.pass_through and not facts.unreadable:
            lock, day_unread = self._day_facts(known, facts, guards)  # the figure, and its lock
            reads = self._limit_reads(known, facts, guards)  # the market and the lot size
        with self._lock:
            now = time.monotonic()
            refusing = self._refusing()  # it may have changed while the book was read
            if lock is None and guards.daily:
                lock = self._day_locks.get((known.pipe, guards.day))  # another order's, meanwhile
            if facts.pass_through:
                return Decision(token=self._register(known, None, now))
            if facts.unreadable:
                if refusing is not None or lock is not None:
                    return self._unreadable(known, refusing, lock)
                return self._unjudged(known, guards, now)
            if facts.stale and refusing is None and lock is None:
                return self._unjudged(known, guards, now)  # a kept book judges refusals only
            if refusing is None and lock is None and not day_unread and not guards.judges(known):
                return Decision(token=self._register(known, None, now))
            verdict, sent_at = self._judge(known, facts, now)
            if verdict.reducing:
                return Decision(token=self._register(known, verdict, now))
            if refusing is not None:
                return self._refuse(known, refusing, verdict, sent_at)
            if lock is not None:
                return self._refuse_daily(known, lock, verdict)
            if day_unread:
                detail = {"reason": verdict.reason, "position": verdict.position}
                return Decision(
                    True, words.daily_unreadable(known.pipe), GUARD_UNAVAILABLE, detail=detail
                )
            return self._held_to_settings(known, guards, verdict, reads, now)

    def _refusing(self) -> _Refusing | None:
        """What refuses new risk now; call under the lock."""
        state = self._state
        if state is None or state.on:
            return _Refusing(state)
        return None

    # -- reading what an order does, outside the lock ---------------------------------------------

    def _facts(self, order: Order, fallback: bool = False) -> tuple[Order, _Facts]:
        """What the order does, read from the broker's books. ``fallback``: new risk is refused
        now, so a positions book kept from the last ``DAY_BOOK_REUSE_S`` may judge it."""
        try:
            if order.action == MODIFY:
                return self._modify_facts(order, fallback)
            if order.action == GTT_MODIFY:
                order = self._gtt_modify_order(order)
            if order.cnc_sell or (order.action != PLACE and order.trigger_type == "OCO"):
                return order, _Facts()  # judged without the book
            position, stale = self._position(order, fallback)
            return order, _Facts(position=position, stale=stale)
        except (readers.Unreadable, ValueError) as exc:
            logger.warning(f"The risk check could not read what an order does: {exc}")
            return order, _Facts(unreadable=True)

    def _position(self, order: Order, fallback: bool = False) -> tuple[int, bool]:
        """p for the order's position, from the broker's own book, and whether that book was
        kept from before (``_live_rows``). Every order is judged on a read of its own: a
        position opened since an earlier read (a fill, a GTT, a trade in Upstox's app) is
        never mistaken for flat."""
        if order.pipe == SANDBOX:
            position = self._read.sandbox_position(
                order.owner, order.symbol, order.exchange, order.product
            )
            return position, False
        instrument = self._read.instrument_token(order.symbol, order.exchange)
        if instrument is None:
            return 0, False  # no instrument: nothing can be held in it, and nothing is sent
        product = rules.position_product(LIVE, order.product)
        rows, stale = self._live_rows(order.owner, fallback)
        return rules.upstox_position(rows, instrument, product), stale

    def _live_rows(self, owner: str, fallback: bool = False) -> tuple[list, bool]:
        """Upstox's positions for ``owner``, kept with the time the read began. When they cannot
        be read and ``fallback`` allows it, the book read in the last ``DAY_BOOK_REUSE_S``,
        with True; else Unreadable."""
        started = time.monotonic()
        try:
            rows = self._read.live_cached_rows(owner)
            if rows is None:
                rows = self._read.live_rows(owner)
        except readers.Unreadable:
            kept = self._kept_rows(owner, DAY_BOOK_REUSE_S) if fallback else None
            if kept is None:
                raise
            return kept, True
        with self._lock:
            self._rows = (owner, started, rows)
        return rows, False

    def _kept_rows(self, owner: str, within: float) -> list | None:
        """The book last read for ``owner``, if that read began at most ``within`` ago."""
        with self._lock:
            kept = self._rows
        if kept is None or kept[0] != owner or time.monotonic() - kept[1] > within:
            return None
        return kept[2]

    def _modify_facts(self, order: Order, fallback: bool = False) -> tuple[Order, _Facts]:
        """The order a modify changes, as the broker has it, and the position it acts on."""
        if order.pipe == LIVE:
            working = self._read.live_working_order(order.owner, order.ref)
        else:
            working = self._read.sandbox_working_order(order.owner, order.ref)
        if working is None:
            raise readers.Unreadable("the order to change is not in the book")
        if working.final:
            return order, _Facts(pass_through=True)  # done: the broker refuses the change
        new = _merged_terms(working.order.terms, order.terms)
        known = replace(
            order,
            exchange=working.exchange,
            symbol=working.symbol,
            product=_openalgo_product(order.pipe, working.product, order.product, working.exchange),
            side=working.order.side,
            quantity=max(0, new.quantity - working.order.filled),
            terms={"new": new},
        )
        if known.cnc_sell:
            return known, _Facts(working=working.order)
        stale = False
        if order.pipe == LIVE:
            rows, stale = self._live_rows(order.owner, fallback)
            position = rules.upstox_position(rows, working.instrument, working.product)
        else:
            position = self._read.sandbox_position(
                order.owner, known.symbol, known.exchange, known.product
            )
        return known, _Facts(position=position, working=working.order, stale=stale)

    def _gtt_modify_order(self, order: Order) -> Order:
        """A GTT modify judged as the GTT will be: its side, product and kind are the broker's."""
        if order.pipe == LIVE:
            gtt = self._read.live_gtt(order.owner, order.ref)
        else:
            gtt = self._read.sandbox_gtt(order.owner, order.ref)
        if gtt is None:
            raise readers.Unreadable("the GTT to change is not in the book")
        return replace(
            order,
            exchange=gtt.exchange.upper() or order.exchange,
            symbol=gtt.symbol or order.symbol,
            product=gtt.product.upper() or order.product,
            side=gtt.side,
            trigger_type=gtt.trigger_type,
        )

    # -- the daily loss limit, outside the lock --------------------------------------------------

    def _guards(self, order: Order) -> _Guards:
        """The owner's settings and today's lock for the order's pipe (cached reads). Settings
        that cannot be read leave every guard but the kill switch and a written lock unchecked."""
        try:
            settings = self._the_store().settings()
        except Exception:
            logger.exception("The risk guard could not read its settings")
            settings = None
        day = daily.day_of(datetime.now(UTC)).isoformat()
        return _Guards(day=day, settings=settings, lock=self._day_lock(order.pipe, day))

    def _day_lock(self, pipe: str, day: str) -> risk_store.DayLock | None:
        """Today's lock for ``pipe``: read once a day from the risk database, then held here."""
        key = (pipe, day)
        with self._lock:
            if key in self._day_locks:
                return self._day_locks[key]
        try:
            lock = self._the_store().day_lock(pipe, day)
        except Exception:
            logger.exception("The risk guard could not read today's lock")
            return None  # read again next time
        with self._lock:
            for other in [k for k in self._day_locks if k[1] != day]:
                del self._day_locks[other]  # an earlier day's
            return self._day_locks.setdefault(key, lock)

    def _day_facts(
        self, order: Order, facts: _Facts, day: _Guards
    ) -> tuple[risk_store.DayLock | None, bool]:
        """Today's lock: the one written, or one written now because the figure has reached the
        limit in the order's segment's session. ``(lock, True)`` when the figure is unreadable."""
        if day.lock is not None or day.limit is None or facts.pass_through or facts.unreadable:
            return day.lock, False
        at = datetime.now(UTC)
        if not self._in_session(order.exchange, at):
            return None, False
        try:
            figure = self._day_figure(order)
        except readers.Unreadable as exc:
            logger.warning(f"The daily loss limit could not be checked: {exc}")
            with self._lock:
                self._unchecked[order.pipe] = time.monotonic()
            return None, True
        if not daily.reached(day.limit, figure):
            return None, False
        return self._lock_day(order.pipe, day, figure), False

    def _in_session(self, exchange: str, at: datetime) -> bool:
        """Whether the segment is in session now, by the exchange's own hours (R11)."""
        today = at.astimezone(IST).date()
        key = (exchange, today.isoformat())
        window = self._sessions.get(key, _UNREAD)
        if window is _UNREAD:
            try:
                from database.market_calendar_db import get_effective_session_window

                window = get_effective_session_window(today, exchange)
            except Exception:
                logger.exception("The market calendar could not be read for the daily limit")
                # Looked at from India's morning on: never in the hours after midnight, when
                # Upstox's book can still show the day before.
                return at.astimezone(IST).hour >= 9
            if window is not None:  # "closed" is asked again: a read that failed says the same
                self._sessions[key] = window
        return daily.in_session(window, at)

    def _day_figure(self, order: Order) -> daily.DayFigure:
        if order.pipe == SANDBOX:
            return self._read.sandbox_day(order.owner)
        return daily.live_figure(self._rows_for_day(order.owner))

    def _rows_for_day(self, owner: str) -> list:
        """The book the order was just judged on; else a read, else one up to 15 s old."""
        kept = self._kept_rows(owner, 1.0)
        if kept is not None:
            return kept
        return self._live_rows(owner, fallback=True)[0]

    def _lock_day(self, pipe: str, day: _Guards, figure: daily.DayFigure) -> risk_store.DayLock:
        """Write today's lock; the first order here to see the breach says so, once."""
        try:
            lock = self._the_store().lock_day(pipe, day.day, figure.total, float(day.limit))
        except Exception:
            logger.exception("Today's lock could not be saved; it holds in this process")
            lock = None
        if lock is None:
            lock = risk_store.DayLock(
                pipe, day.day, datetime.now(UTC), figure.total, float(day.limit)
            )
        with self._lock:
            kept = self._day_locks.get((pipe, day.day))
            first = kept is None
            if first:
                self._day_locks[(pipe, day.day)] = lock
            else:
                lock = kept
        if first:
            self._announce_day(lock, figure)
        return lock

    def _announce_day(self, lock: risk_store.DayLock, figure: daily.DayFigure) -> None:
        """The day locked: the audit, Telegram and every open screen (never fails an order)."""
        said = words.daily_lock_told(lock.figure, lock.loss_limit, lock.pipe)
        try:
            self._the_store().audit(
                "daily_lock",
                said,
                pipe=lock.pipe,
                surface=_surface(),
                detail={
                    "figure": lock.figure,
                    "limit": lock.loss_limit,
                    "approximate": figure.approximate,
                },
            )
        except Exception:
            logger.exception("Today's lock could not be written to the audit")
        try:
            from database.user_db import find_user_by_username

            from .killswitch import Owner, _tell

            admin = find_user_by_username()
            if admin is not None:
                _tell(Owner(admin.username, "app", "risk guard"), said)
        except Exception:
            logger.exception("Today's lock could not be told on Telegram")
        try:
            from extensions import emit_from_any_thread

            emit_from_any_thread("sudarshan_risk_day", {"pipe": lock.pipe, "locked": True})
        except Exception:
            logger.exception("Today's lock could not be told to the open screens")

    def _limit_reads(
        self, order: Order, facts: _Facts, guards: _Guards, *, adding: bool | None = None
    ) -> _Reads:
        """What the owner's limits need read for this order, read only when it adds risk (an
        exit pays no read): the market for the band, unless it is a stop-limit, whose limit is
        held to its own trigger; the lot size for a cap in lots. ``adding`` None: judged here
        from the book."""
        terms = order.priced
        banded = guards.price_band(order) is not None and terms is not None
        banded = banded and terms.price_type != "SL"
        in_lots = guards.sized(order) and size.unit_of(order.exchange) == size.LOTS
        if not banded and not in_lots:
            return _Reads(_NO_QUOTE)
        if adding is None:
            with self._lock:
                verdict, _sent_at = self._judge(order, facts, time.monotonic())
            adding = not verdict.reducing
        if not adding:
            return _Reads(_NO_QUOTE)
        quote = _NO_QUOTE
        if banded:
            try:
                quote = self._read.quote(order.pipe, order.owner, order.symbol, order.exchange)
            except readers.Unreadable as exc:
                logger.warning(f"The price band could not read the market: {exc}")
                quote = None
        lot_size = None
        if in_lots:
            try:
                lot_size = self._read.lot_size(order.symbol, order.exchange)
            except readers.Unreadable as exc:
                logger.warning(f"The size limit could not read the lot size: {exc}")
        return _Reads(quote, lot_size)

    def day_lock(self, pipe: str) -> risk_store.DayLock | None:
        """Today's lock for ``pipe``, if the day has locked (the status routes)."""
        return self._day_lock(pipe, daily.day_of(datetime.now(UTC)).isoformat())

    def day_unchecked(self, pipe: str) -> bool:
        """Whether the daily limit could not be checked in the last minute (the banner)."""
        with self._lock:
            at = self._unchecked.get(pipe)
        return at is not None and time.monotonic() - at <= UNCHECKED_SHOWN_S

    # -- deciding, under the lock -------------------------------------------------------------------

    def _judge(self, order: Order, facts: _Facts, now: float) -> tuple[rules.Verdict, float | None]:
        claimed, sent_at = self._claimed(order, facts.position, now)
        if order.action == MODIFY:
            verdict = rules.classify_modify(
                facts.working,
                order.terms["new"],
                facts.position,
                claimed,
                cnc_sell=order.cnc_sell,
            )
        elif order.action in (GTT_PLACE, GTT_MODIFY):
            verdict = rules.classify_gtt(
                order.trigger_type,
                order.side,
                order.quantity,
                facts.position,
                claimed,
                cnc_sell=order.cnc_sell,
            )
        else:
            verdict = rules.classify(
                order.side, order.quantity, facts.position, claimed, cnc_sell=order.cnc_sell
            )
        return verdict, sent_at

    def _claimed(self, order: Order, position: int, now: float) -> tuple[int, float | None]:
        """c: what was sent on the order's position and side that the book does not show yet.

        An exit the book now shows is dropped here. Of the sends naming one broker order (an
        order and its modifies), only the newest counts, and a modify leaves out its own order.
        """
        newest: dict[str, int] = {}
        counted: list[tuple[int, Send]] = []
        for token, send in list(self._sends.items()):
            if send.order.key != order.key or send.order.action not in (PLACE, MODIFY):
                continue
            if send.expect is not None and rules.exit_shown(
                position, send.position_then, send.expect
            ):
                del self._sends[token]
                continue
            if send.ref:
                if send.ref == order.ref and order.action == MODIFY:
                    continue
                if newest.get(send.ref, 0) > token:
                    continue
                newest[send.ref] = token
            counted.append((token, send))
        total, latest = 0, None
        for token, send in counted:
            if send.ref and newest.get(send.ref) != token:
                continue
            if send.order.side == order.side:
                total += send.quantity
                latest = send.sent_at if latest is None else max(latest, send.sent_at)
        return total, latest

    def _register(self, order: Order, verdict: rules.Verdict | None, now: float) -> int:
        """Record a send let go; call under the lock."""
        token = next(self._tokens)
        quantity = order.quantity or 0
        expect = None
        if verdict is not None and verdict.reason == rules.EXIT:
            expect = rules.exit_expectation(verdict.position, verdict.claimed, quantity)
        self._sends[token] = Send(
            order=order,
            quantity=quantity,
            reducing=None if verdict is None else verdict.reducing,
            expect=expect,
            position_then=None if verdict is None else verdict.position,
            admitted=now,
            sent_at=time.time(),
            ref=order.ref if order.action in (MODIFY, GTT_MODIFY) else None,
        )
        return token

    def _forget(self, now: float) -> None:
        """Drop the sends that no longer count; call under the lock."""
        for token, send in list(self._sends.items()):
            answered = send.answered
            if now - send.admitted > SEND_HELD_AT_MOST_S or (
                answered is not None and now - answered > SHOWS_WITHIN_S
            ):
                del self._sends[token]

    def _refuse(
        self, order: Order, refusing: _Refusing, verdict: rules.Verdict, sent_at: float | None
    ) -> Decision:
        detail = {"reason": verdict.reason, "position": verdict.position, "room": verdict.room}
        if refusing.state is None:
            return Decision(True, words.STATE_UNREADABLE, RISK_STATE_UNREADABLE, detail=detail)
        text = words.kill_switch_on(
            _hhmm(refusing.state.since),
            verdict,
            position=order.name,
            side=order.side,
            claim_sent_at=sent_at,
        )
        return Decision(True, text, KILL_SWITCH_ACTIVE, detail=detail)

    def _refuse_daily(
        self, order: Order, lock: risk_store.DayLock, verdict: rules.Verdict
    ) -> Decision:
        detail = {
            "reason": verdict.reason,
            "position": verdict.position,
            "room": verdict.room,
            "figure": lock.figure,
            "limit": lock.loss_limit,
        }
        text = words.daily_locked(
            lock.figure, lock.loss_limit, verdict, position=order.name, side=order.side
        )
        return Decision(True, text, DAILY_LOSS_LIMIT, detail=detail)

    def _unjudged(self, order: Order, guards: _Guards, now: float) -> Decision:
        """With nothing refusing new risk, an order whose book could not be read goes, noted: it
        may be an exit, and an exit is never refused for a read that failed."""
        if guards.daily:
            self._unchecked[order.pipe] = now
            said = words.daily_not_checked(order.pipe)
        else:
            said = words.checks_not_run(order.pipe)
        token = self._register(order, None, now)
        return Decision(token=token, words=said, code=GUARD_UNAVAILABLE)

    def _held_to_settings(
        self, order: Order, guards: _Guards, verdict: rules.Verdict, reads: _Reads, now: float
    ) -> Decision:
        """An order adding risk, with nothing refusing new risk: the owner's own limits, each
        only when he has turned it on. Call under the lock."""
        refusal, unchecked = self._over_limits(order, guards, verdict, reads)
        if refusal is not None:
            return refusal
        if guards.braked(order):
            refusal = self._brake(order.pipe, guards.settings, now, key=_credit_key(order))
            if refusal is not None:
                return refusal
        token = self._register(order, verdict, now)
        if unchecked:
            return Decision(token=token, words=unchecked, code=GUARD_UNAVAILABLE)
        return Decision(token=token)

    def _over_limits(
        self, order: Order, guards: _Guards, verdict: rules.Verdict, reads: _Reads
    ) -> tuple[Decision | None, str | None]:
        """The owner's band and size limit on an order adding risk: its refusal, or None and,
        when a limit could not be checked, the sentence that says so. No I/O and nothing
        recorded."""
        unchecked = None
        held = guards.price_band(order)
        terms = order.priced
        if held is not None and terms is not None:
            quote = None if reads.quote is _NO_QUOTE else reads.quote  # _NO_QUOTE: judged as
            # adding risk only now, or read while the kill switch was on
            breach = band.check(
                order.side,
                terms.price_type,
                terms.price,
                terms.trigger,
                quote,
                held,
                both_sides=guards.settings.band_both_sides,
            )
            if breach is not None:
                detail = {
                    "reason": verdict.reason,
                    "price": breach.price,
                    "reference": breach.reference,
                    "away_pct": breach.away_pct,
                    "band_pct": breach.band_pct,
                }
                return Decision(True, words.band_refused(breach), PRICE_BAND, detail=detail), None
            if terms.price_type != "SL" and (
                quote is None or band.reference(order.side, quote) <= 0
            ):  # no market to hold it to: it goes, noted (the exchange's own check stands)
                unchecked = words.BAND_NOT_CHECKED
        if guards.sized(order):
            adding = size.adding(verdict, order.quantity)
            excess = size.check(order.exchange, adding, reads.lot_size, guards.settings)
            if excess is not None:
                detail = {"reason": verdict.reason, "adding": adding, "unit": excess.unit,
                          "amount": excess.amount, "cap": excess.cap}  # fmt: skip
                said = words.size_refused(excess, verdict, position=order.name, side=order.side)
                return Decision(True, said, SIZE_LIMIT, detail=detail), None
            if adding and size.unit_of(order.exchange) == size.LOTS and not reads.lot_size:
                unchecked = unchecked or words.SIZE_NOT_CHECKED
        return None, unchecked

    def _brake(
        self,
        pipe: str,
        settings,
        now: float,
        *,
        key: tuple | None = None,
        credits: list[tuple[tuple, int]] = (),
    ) -> Decision | None:
        """The burst brake for one admission adding risk: a refusal when the owner's number of
        them has gone in the window, else the admission counted. An order (``key``) a batch has
        already counted uses one of its credits instead; a batch, counted once, leaves its
        ``credits``: per instrument and side, the orders it will send. Call under the lock."""
        burst = self._bursts.setdefault(pipe, _Burst())
        window = float(settings.burst_window_s)
        while burst.admitted and now - burst.admitted[0] >= window:
            burst.admitted.popleft()
        for held in list(burst.credits):
            kept = [(until, count) for until, count in burst.credits[held] if until > now]
            kept = [(until, count) for until, count in kept if count > 0]
            if kept:
                burst.credits[held] = kept
            else:
                del burst.credits[held]
        if key is not None and key in burst.credits:
            until, count = burst.credits[key][0]
            burst.credits[key][0] = (until, count - 1)  # an order of a batch already counted
            return None
        if len(burst.admitted) >= int(settings.burst_n):
            again = time.time() + window - (now - burst.admitted[0])
            said = words.burst_refused(int(settings.burst_n), window, again)
            detail = {"count": len(burst.admitted), "window_s": window}
            if burst.told is None or now - burst.told >= window:
                burst.told = now
                detail["tell"] = words.burst_told(int(settings.burst_n), window, again, pipe)
            return Decision(True, said, BURST_BRAKE, detail=detail)
        burst.admitted.append(now)
        for held, count in credits:
            burst.credits.setdefault(held, []).append((now + BATCH_PARTS_WITHIN_S, count))
        return None

    # -- a batch, held to the owner's limits whole -------------------------------------------------

    def check_together(
        self, orders: list[Order], slices: list[list[int]] | None = None
    ) -> tuple[int | None, Decision] | None:
        """The parts of a batch (batch.py) held to the owner's band and size limit before any is
        sent: the first part over one, with the refusal a single order would get; or, with no
        part over, the burst brake's refusal of the batch as a whole (index None); or None.

        Nothing is recorded but the brake's one admission for the batch and its credits: one
        for each order a part adding risk will send (``slices``: the quantities each part goes
        as, its own quantity unless it is split), under that order's instrument, side and
        quantity, so those orders are not counted again and no other order uses them. Each part is decided again as it is sent, kill switch and
        daily limit included. A part whose book cannot be read is left to that decision. The
        parts are judged in order, each against the book and the exits earlier parts would
        send, so two parts selling the same long take its room once.
        """
        if not orders:
            return None
        self._load_state()
        guards = self._guards(orders[0])
        settings = guards.settings
        if settings is None or not (settings.band_on or settings.size_on or settings.burst_on):
            return None
        taken: dict[tuple, int] = {}  # (position, side): what earlier parts would exit
        credits: dict[tuple, int] = {}  # the orders the parts adding risk will send
        for index, order in enumerate(orders):
            if guards.price_band(order) is None and not guards.sized(order):
                if not guards.braking:
                    continue
            known, facts = self._facts(order)  # reads: outside the lock
            if facts.unreadable or facts.pass_through:
                continue
            mine = (known.key, known.side)
            with self._lock:
                claimed, _sent_at = self._claimed(known, facts.position, time.monotonic())
            verdict = rules.classify(
                known.side,
                known.quantity,
                facts.position,
                claimed + taken.get(mine, 0),
                cnc_sell=known.cnc_sell,
            )
            if verdict.reducing:
                taken[mine] = taken.get(mine, 0) + (known.quantity or 0)
                continue
            sent_as = slices[index] if slices is not None and index < len(slices) else None
            for quantity in sent_as or [known.quantity]:
                held = _credit_key(known, quantity)
                credits[held] = credits.get(held, 0) + 1
            reads = self._limit_reads(known, facts, guards, adding=True)
            refusal, _unchecked = self._over_limits(known, guards, verdict, reads)
            if refusal is not None:
                self._note(known.pipe, known, refusal)  # the risk history shows it
                return index, refusal
        if guards.braking and credits:
            with self._lock:
                refusal = self._brake(
                    orders[0].pipe, settings, time.monotonic(), credits=list(credits.items())
                )
            if refusal is not None:
                if refusal.detail.get("tell"):
                    _tell_owner(refusal.detail["tell"])
                self._note(orders[0].pipe, orders[0], refusal)
                return None, refusal  # the batch as a whole, not one part
        return None

    def _unreadable(
        self, order: Order, refusing: _Refusing | None, lock: risk_store.DayLock | None = None
    ) -> Decision:
        """New risk is refused now and the order cannot be judged: no book, not even one kept
        from the last ``DAY_BOOK_REUSE_S``, so it is refused with the way to an urgent exit."""
        since, locked = None, None
        if refusing is not None:
            since = None if refusing.state is None else _hhmm(refusing.state.since)
        elif lock is not None:
            locked = (lock.figure, lock.loss_limit)
        if order.action in (MODIFY, GTT_MODIFY):
            said = words.order_not_found(order.pipe, since, locked=locked)
        else:
            said = words.book_unreadable(order.pipe, since, locked=locked)
        return Decision(True, said, GUARD_UNAVAILABLE, detail={"reason": "unreadable"})

    def _broke(self, pipe: str, order: Order | None) -> Decision:
        """The check failed: refuse only while new risk is refused, else let it go."""
        with self._lock:
            state = self._state
            if state is None or state.on:
                return Decision(True, words.check_broke(pipe), GUARD_ERROR)
            token = None if order is None else self._register(order, None, time.monotonic())
        return Decision(token=token, code=GUARD_ERROR)

    # -- the audit, outside the lock ------------------------------------------------------------

    def _note(self, pipe: str, order: Order | None, decision: Decision) -> None:
        """Audit a refusal or a check that broke: written now when its kind was not written in
        the last ``NOTE_EVERY_S``, otherwise counted and written with the next. Never fails an
        order."""
        try:
            surface = _surface()
            symbol = "" if order is None else order.symbol
            kind = "order_refused" if decision.refused else "guard_error"
            if decision.code == GUARD_UNAVAILABLE:
                kind = "guard_unavailable"
            key = f"{decision.code}|{surface}|{symbol}"
            said = decision.words or "The risk check broke on an order; it was let go."
            detail = {"code": decision.code, "symbol": symbol, **decision.detail}
            now = time.monotonic()
            with self._lock:
                if kind != "order_refused":
                    self._problem = (time.time(), kind, said)
                stale = self._take_stale(now)
                note = self._notes.get(key)
                if note is not None and now - note.written < NOTE_EVERY_S:
                    note.pending += 1
                    note.words, note.detail = said, detail
                    write = None
                else:
                    count = 1 + (note.pending if note is not None else 0)
                    self._notes[key] = _Note(kind, said, pipe, surface, detail, now)
                    write = (key, _Note(kind, said, pipe, surface, detail, now, count))
            for item in [*stale, *([write] if write else [])]:
                self._write_note(*item)
        except Exception:
            logger.exception("A risk refusal could not be written to the audit")

    def _take_stale(self, now: float) -> list[tuple[str, _Note]]:
        """Counts kept past ``NOTE_KEPT_S``, taken out to be written; call under the lock."""
        stale = []
        for key, note in list(self._notes.items()):
            if now - note.written > NOTE_KEPT_S:
                del self._notes[key]
                if note.pending:
                    stale.append((key, note))
        return stale

    def _write_note(self, key: str, note: _Note) -> None:
        count = note.pending
        self._the_store().audit(
            note.kind,
            note.words,
            pipe=note.pipe,
            surface=note.surface,
            detail=note.detail,
            merge_key=key,
            count=count,
        )

    def timing(self) -> dict[str, Any]:
        """The gate's own time per order over its last ``TIMED_KEPT`` decisions, in ms: what
        checking adds to an order (the audit write of a refusal is not in it)."""
        with self._lock:
            taken = list(self._timed)
        taken.sort()  # outside the lock
        if not taken:
            return {"orders": 0, "p50_ms": None, "p95_ms": None, "max_ms": None}

        def nearest_rank(percent: int) -> float:
            rank = -(-percent * len(taken) // 100)  # ceil(percent * n / 100), in integers
            return round(taken[max(rank, 1) - 1] * 1000, 2)

        return {
            "orders": len(taken),
            "p50_ms": nearest_rank(50),
            "p95_ms": nearest_rank(95),
            "max_ms": round(taken[-1] * 1000, 2),
        }

    def last_problem(self) -> dict[str, str] | None:
        """When the check last broke or could not read a book, and what the order was told."""
        with self._lock:
            problem = self._problem
        if problem is None:
            return None
        at, kind, said = problem
        return {"at": datetime.fromtimestamp(at, IST).isoformat(), "kind": kind, "words": said}

    def flush_notes(self) -> None:
        """Write every count still kept in memory (before the audit is read)."""
        with self._lock:
            pending = []
            for key, note in self._notes.items():
                if note.pending:
                    pending.append((key, replace(note)))
                    note.pending = 0
        for key, note in pending:
            try:
                self._write_note(key, note)
            except Exception:
                logger.exception("A risk refusal count could not be written to the audit")


def _tell_owner(text: str) -> None:
    """Queue a Telegram message to the owner (the admin account); never fails an order."""
    try:
        from database.user_db import find_user_by_username

        from .killswitch import Owner, _tell

        admin = find_user_by_username()
        if admin is not None:
            _tell(Owner(admin.username, "app", "risk guard"), text)
    except Exception:
        logger.exception("A risk message could not be told on Telegram")


def _hhmm(at: datetime | None) -> str:
    return "" if at is None else at.astimezone(IST).strftime("%H:%M")


def _surface() -> str:
    """Where the order came from: the request's path, or background work."""
    try:
        from flask import has_request_context, request

        if has_request_context():
            return request.path
    except Exception:  # noqa: S110 - only a label for the audit
        pass
    return "background"


def _merged_terms(old: rules.OrderTerms, given: dict) -> rules.OrderTerms:
    """A modify's terms: what it gives, and the order's own for what it leaves out."""
    quantity = rules.quantity_of(given.get("quantity"))
    price_type = str(given.get("price_type") or "").strip().upper()
    return rules.OrderTerms(
        quantity=old.quantity if quantity is None else quantity,
        price_type=price_type if price_type in rules.PRICE_TYPES else old.price_type,
        price=rules.price_of(given["price"]) if "price" in given else old.price,
        trigger=rules.price_of(given["trigger"]) if "trigger" in given else old.trigger,
    )


def _openalgo_product(pipe: str, kept_as: str, asked: str, exchange: str) -> str:
    """The OpenAlgo product of a position kept as ``kept_as``, so its key matches the orders'.

    The request's own product when it agrees with the broker's record; otherwise Upstox's own
    reading of its code (broker/upstox/mapping/transform_data.reverse_map_product_type).
    """
    if pipe != LIVE:
        return kept_as
    asked = str(asked or "").strip().upper()
    if asked in rules.UPSTOX_PRODUCTS and rules.position_product(LIVE, asked) == kept_as:
        return asked
    if kept_as == "I":
        return "MIS"
    return "CNC" if exchange in rules.EQUITY_CASH else "NRML"


# -- the decorators ------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Kind:
    pipe: str
    describe: Callable[..., Order]
    refusal: Callable[[str], Any]
    outcome: Callable[[Any], tuple[str, str | None]]


class RefusedResponse:
    """The broker's HTTP response, for an order the risk gate refused: nothing was sent."""

    status = 403
    status_code = 403
    text = ""

    def json(self) -> dict:
        return {}


def _word(value: object) -> str:
    return str(value or "").strip().upper()


def _upstox_place(data: dict, auth: str, *args, **kwargs) -> Order:
    return Order(
        pipe=LIVE,
        action=PLACE,
        exchange=_word(data.get("exchange")),
        symbol=_word(data.get("symbol")),
        product=_word(data.get("product")),
        side=rules.side_of(data.get("action")),
        quantity=rules.quantity_of(data.get("quantity")),
        owner=auth,
        **_price_terms(data),
    )


def _price_terms(data: dict) -> dict[str, Any]:
    """An order's price terms: OpenAlgo's requests name the type ``pricetype``, the sandbox's
    order manager ``price_type`` (services/sandbox_service.py renames it)."""
    return {
        "price_type": _word(data.get("pricetype") or data.get("price_type")),
        "price": rules.price_of(data.get("price")),
        "trigger": rules.price_of(data.get("trigger_price")),
    }


def _upstox_modify(data: dict, auth: str, *args, **kwargs) -> Order:
    terms = {"quantity": data.get("quantity"), "price_type": data.get("pricetype")}
    if "price" in data:
        terms["price"] = data.get("price")
    if "trigger_price" in data:
        terms["trigger"] = data.get("trigger_price")
    return Order(
        pipe=LIVE,
        action=MODIFY,
        exchange=_word(data.get("exchange")),
        symbol=_word(data.get("symbol")),
        product=_word(data.get("product")),
        side=rules.side_of(data.get("action")),
        quantity=rules.quantity_of(data.get("quantity")),
        owner=auth,
        ref=str(data.get("orderid") or ""),
        terms=terms,
    )


def _upstox_gtt(action: str):
    def describe(data: dict, auth: str, *args, **kwargs) -> Order:
        return Order(
            pipe=LIVE,
            action=action,
            exchange=_word(data.get("exchange")),
            symbol=_word(data.get("symbol")),
            product=_word(data.get("product")),
            side=rules.side_of(data.get("action")),
            quantity=rules.quantity_of(data.get("quantity")),
            owner=auth,
            ref=str(data.get("trigger_id") or "") if action == GTT_MODIFY else None,
            trigger_type=_word(data.get("trigger_type")),
        )

    return describe


def _refuse_with_response(text: str):
    return RefusedResponse(), {"status": "error", "message": text}, None


def _refuse_with_body(text: str):
    return {"status": "error", "message": text}, 403


def _answer_of_place(result) -> tuple[str, str | None]:
    """A place_order_api or place_gtt_order answer: refused (nothing sent) or answered."""
    res, _body, ref = result
    status = getattr(res, "status", None)
    if res is None or isinstance(res, BusyResponse):
        return REFUSED, None
    if isinstance(status, int) and 400 <= status < 500:
        return REFUSED, None
    return ANSWERED, str(ref) if ref else None


def _answer_of_change(result) -> tuple[str, str | None]:
    """A modify answer ``(body, status)``: only a refusal before the send (429) is certain."""
    _body, status = result
    return (REFUSED if status == 429 else ANSWERED), None


def _guarded(kind: _Kind) -> Callable[[Callable], Callable]:
    def wrap(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def guarded(*args, **kwargs):
            if not flag.covers(kind.pipe):
                return fn(*args, **kwargs)
            return GATE.run(kind, fn, args, kwargs)

        return guarded

    return wrap


#: broker/upstox/api/order_api.place_order_api(data, auth)
upstox_place = _guarded(_Kind(LIVE, _upstox_place, _refuse_with_response, _answer_of_place))
#: broker/upstox/api/order_api.modify_order(data, auth)
upstox_modify = _guarded(_Kind(LIVE, _upstox_modify, _refuse_with_body, _answer_of_change))
#: broker/upstox/api/gtt_api.place_gtt_order(data, auth)
upstox_gtt_place = _guarded(
    _Kind(LIVE, _upstox_gtt(GTT_PLACE), _refuse_with_response, _answer_of_place)
)
#: broker/upstox/api/gtt_api.modify_gtt_order(data, auth)
upstox_gtt_modify = _guarded(
    _Kind(LIVE, _upstox_gtt(GTT_MODIFY), _refuse_with_body, _answer_of_change)
)


def _symbol(value: object) -> str:
    """A sandbox symbol as the sandbox keeps it (its position rows use the order's own)."""
    return str(value or "").strip()


def _sandbox_place(manager, order_data: dict, *args, **kwargs) -> Order:
    return Order(
        pipe=SANDBOX,
        action=PLACE,
        exchange=_word(order_data.get("exchange")),
        symbol=_symbol(order_data.get("symbol")),
        product=_word(order_data.get("product")),
        side=rules.side_of(order_data.get("action")),
        quantity=rules.quantity_of(order_data.get("quantity")),
        owner=str(manager.user_id),
        **_price_terms(order_data),
    )


def _sandbox_modify(manager, orderid, new_data: dict, *args, **kwargs) -> Order:
    """OrderManager.modify_order: the quantity when given, a price or trigger only when set."""
    terms = {}
    if "quantity" in new_data:
        terms["quantity"] = new_data["quantity"]
    if new_data.get("price"):
        terms["price"] = new_data["price"]
    if new_data.get("trigger_price"):
        terms["trigger"] = new_data["trigger_price"]
    return Order(
        pipe=SANDBOX,
        action=MODIFY,
        exchange="",
        symbol="",
        product="",
        side=None,
        quantity=None,
        owner=str(manager.user_id),
        ref=str(orderid),
        terms=terms,
    )


def _sandbox_gtt(action: str):
    def describe(manager, *args, **kwargs) -> Order:
        if action == GTT_PLACE:  # GTTManager.place_gtt(gtt_data, last_price)
            data, ref = args[0], None
        else:  # GTTManager.modify_gtt(trigger_id, gtt_data)
            ref, data = str(args[0]), args[1]
        return Order(
            pipe=SANDBOX,
            action=action,
            exchange=_word(data.get("exchange")),
            symbol=_symbol(data.get("symbol")),
            product=_word(data.get("product")),
            side=rules.side_of(data.get("action")),
            quantity=rules.quantity_of(data.get("quantity")),
            owner=str(manager.user_id),
            ref=ref,
            trigger_type=_word(data.get("trigger_type")),
        )

    return describe


def _refuse_in_the_sandbox(text: str):
    return False, {"status": "error", "message": text, "mode": "analyze"}, 403


def _answer_in_the_sandbox(result) -> tuple[str, str | None]:
    """A sandbox answer ``(ok, body, status)``: a 4xx placed nothing; anything else may have."""
    ok, body, status = result
    if not ok and isinstance(status, int) and status < 500:
        return REFUSED, None
    ref = (body.get("orderid") or body.get("trigger_id")) if isinstance(body, dict) else None
    return ANSWERED, str(ref) if ref else None


#: sandbox/order_manager.OrderManager.place_order, inside its position lock
sandbox_place = _guarded(
    _Kind(SANDBOX, _sandbox_place, _refuse_in_the_sandbox, _answer_in_the_sandbox)
)
#: sandbox/order_manager.OrderManager.modify_order
sandbox_modify = _guarded(
    _Kind(SANDBOX, _sandbox_modify, _refuse_in_the_sandbox, _answer_in_the_sandbox)
)
#: sandbox/gtt_manager.GTTManager.place_gtt
sandbox_gtt_place = _guarded(
    _Kind(SANDBOX, _sandbox_gtt(GTT_PLACE), _refuse_in_the_sandbox, _answer_in_the_sandbox)
)
#: sandbox/gtt_manager.GTTManager.modify_gtt
sandbox_gtt_modify = _guarded(
    _Kind(SANDBOX, _sandbox_gtt(GTT_MODIFY), _refuse_in_the_sandbox, _answer_in_the_sandbox)
)

GATE = Gate()
