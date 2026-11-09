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
function is called. Each send stays recorded until its answer says nothing was sent, or until
the book shows it (or its order ended); past ``SEND_HELD_AT_MOST_S`` from its answer it stays
only until the next order book read answers for it, and no longer than its trading day. An answer
that names no order is forgotten ``SHOWS_WITHIN_S`` after it, for the book has no order to be
asked about. The records are the in-flight registry the kill switch waits on when it is turned on.

A claim ends with its order. A send let go while nothing was judging it counts against its
position only while it waits for its answer. A cancel that went through (Upstox's or the
sandbox's ``cancel_order``, Cancel all included) drops the claims on that order at once
(``cancelled``). And before an order is refused only because of claims, the broker's order book
is read once: a claim whose order shows cancelled, rejected or still resting is dropped, and the
order judged again; a claim whose order shows complete, or is not in the book yet, stays until
the positions book shows the exit or ``SEND_HELD_AT_MOST_S`` have passed; a claim whose market
order shows working stays however long it has, counting from that read (release review v0.4.1,
L1), so a plain exit never goes beside a first that is still working.

If the gate itself breaks, an order passes only when nothing is refusing new risk. While the kill
switch is on, or its state could not be read, it is refused (fail closed). Under the kill switch
or on a locked day, a positions read that fails is answered from the book last read, corrected by
the order book's fills (``KEPT_BOOK_WITHIN_S``, no shorter clock); with none, an order that cannot be judged is refused, with a sentence
pointing at the Upstox app for an urgent exit. In every other state an unreadable book lets the
order go, noted, because it may be an exit.

A reversing order (one that would close a position and open it the other way) under the kill
switch or the day's lock is cut to its exit: the exit to 0 is sent and only the part beyond it is
refused, with a sentence that says so in the answer (the coordinator's decision a). Anywhere
else a reversing order is judged whole, as before.

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

import contextlib
import contextvars
import functools
import itertools
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from broker.upstox.api import rate_limiter
from sudarshan.books import fills
from sudarshan.books.exit_guard import EXIT_ALL_SENDS_FOR_S, EXIT_SHOWS_WITHIN_S, trader_name
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
#: (frontend/src/sudarshan/books/sent.ts). A market exit whose answer named its order counts
#: past it while the order book shows that order working (release review v0.4.1, L1): after this
#: long from the answer, or from the last read that showed it working, its claim stands only until
#: the next read of the book answers for it. Every other send (an entry, a resting limit or stop)
#: ends here, as it always did.
SEND_HELD_AT_MOST_S = 150.0
#: A market exit's claim never asked about again ends here at the latest: longer than any Indian
#: session, so a working order is never dropped by a clock; an MCX order answered late in the
#: evening keeps it into the next morning, which costs one order book read if anything meets it.
CLAIM_KEPT_AT_MOST_S = 16 * 3600.0
#: How soon the kill switch's state is read again after a read failed.
STATE_RETRY_S = 2.0
#: An order that finds the first state read in flight waits this long for it.
STATE_WAIT_S = 5.0
#: The audit is written to at most once in this long, overall (``NOTE_ROWS_PER_WINDOW`` rows);
#: the rest are counted in memory and written with the next window. A SQLite commit is disk work eventlet cannot make
#: cooperative, so a write per refusal would stall the worker under a runaway script.
NOTE_EVERY_S = 2.0
#: A count kept in memory this long without a refusal is written by the next note or flush.
NOTE_KEPT_S = 60.0
#: However many kinds of refusal there are, no more than this many audit rows are written in one
#: ``NOTE_EVERY_S`` window (the rest are counted and written in the next).
NOTE_ROWS_PER_WINDOW = 3
#: Kinds of refusal kept counting at once; past it they fall into one row per reason.
NOTE_MAX_KEYS = 200
_NEVER = -1e9
#: The gate's own time is kept for this many decisions, for its p50 and p95 on the status route
#: (sessions S1 and G3 record them).
TIMED_KEPT = 500
#: A positions book kept from an earlier read judges an order, when the positions cannot be read
#: now, only if Upstox's order book can be read to correct it (``Gate._corrected``), and no longer
#: than this after it was read.
KEPT_BOOK_WITHIN_S = SEND_HELD_AT_MOST_S
#: The day's loss figure, when the positions cannot be read now, comes from a kept book no older
#: than this: the order book's fills correct a row's quantities, not its money (realised,
#: unrealised, the day's values), so the figure has only its age (release review v0.4.1, L3).
DAY_BOOK_WITHIN_S = 15.0
#: Order book rows a caller read for the exits it is sending (``order_book_read``) correct the
#: gate's positions, instead of a read of its own, while they are at most this old: Exit all reads
#: the book once and then sends for ``EXIT_ALL_SENDS_FOR_S``, with time for the read before and
#: the last answer after (release review v0.4.1, X3).
HANDED_ORDERS_WITHIN_S = EXIT_ALL_SENDS_FOR_S + 15.0
#: Outside any ``handing_order_rows`` scope: nothing can be handed, and nothing is read.
_NO_SCOPE = object()
#: ``(owner, read at, rows)`` the caller of the current scope handed, None for a scope with
#: nothing handed yet, or ``_NO_SCOPE``. A context variable, so it belongs to one greenlet or
#: thread; the scope (``handing_order_rows``, around the broker call that reads and sends) resets
#: it when the call ends, so nothing outlives that call on any thread: a request's, a Flow
#: pool's or APScheduler's (review of fix/guard-on, MEDIUM-1).
_HANDED_ORDERS: contextvars.ContextVar[object] = contextvars.ContextVar(
    "sudarshan_guard_handed_orders", default=_NO_SCOPE
)
#: A segment's session window, read from the market calendar, is kept this long.
SESSION_KEPT_S = 600.0
#: How far ahead the daily card looks for the next session (a long holiday weekend fits).
SESSION_LOOKAHEAD_DAYS = 10
#: The daily limit could not be checked this recently: the banner says so. Its first failure
#: after a quiet spell is announced to every open screen (``sudarshan_risk_day``).
UNCHECKED_SHOWN_S = 60.0
#: A risk refusal of an order that did not come from the app's own screens is told on Telegram
#: at most once this often for one pipe, symbol and reason; those not told are counted into the
#: next (the coordinator's decision b).
TOLD_EVERY_S = 60.0
#: How many pipe, symbol and reason the telling remembers before forgetting the old ones.
TOLD_KEPT = 256
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
_ENDED, _WORKING = "ended", "working"


def _market_exit(send: Send) -> bool:
    """A send judged as reducing its position, by a market order: the one kind of claim that
    outlasts ``SEND_HELD_AT_MOST_S`` (a market exit can rest price-protected at Upstox)."""
    return bool(send.reducing) and not _rests(send.order)


def _rests(order: Order) -> bool:
    """A limit or stop order, which can rest at the broker without being on its way to a fill
    (a market order that is not filled yet is in flight, or beyond price protection). A modify
    is judged by its new terms; terms unknown count as a market order (the claim stays)."""
    terms = order.priced
    price_type = terms.price_type if terms is not None else order.price_type
    return price_type in BANDED_TYPES


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
    closed: int = 0  # how much of this entry later exits have been taken to close (the brake)
    working_at: float | None = None  # monotonic: the last order book read showing it working


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
    clip: int | None = None  # send only this much (the exit of a reversal), refuse the rest


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
class _CheckClaims:
    """The order would be refused only for claims on these broker orders: read their status."""

    refs: frozenset


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
        self._withdrawn: dict[int, float] = {}  # token -> monotonic: let through, then not sent
        self._state: KillSwitchState | None = None
        self._state_retry_at = 0.0
        self._loading = None  # the first state read in flight: an Event set when it ends
        self._rows: tuple[str, float, list] | None = None  # (owner, when, rows): Live only
        self._store = store
        self._read = read or readers
        self._notes: dict[str, _Note] = {}  # audit rows by key, with counts not yet written
        self._note_window = _NEVER  # monotonic: when the audit was last written to
        self._writing = False  # one audit writer at a time
        self._timed: deque[float] = deque(maxlen=TIMED_KEPT)  # seconds per decision
        # Today's lock per pipe: (pipe, day) -> the lock, or None when read and not there. Only
        # this process writes locks (one worker), so a None read once stays true until it does.
        self._day_locks: dict[tuple[str, str], risk_store.DayLock | None] = {}
        self._sessions = LockedTTLCache(maxsize=32, ttl=SESSION_KEPT_S)
        self._unchecked: dict[str, float] = {}  # pipe -> monotonic: the daily limit unchecked
        # pipe -> whether that was today's figure, not the book: new orders paused, exits go
        self._paused: dict[str, bool] = {}
        self._bursts: dict[str, _Burst] = {}  # pipe -> its admissions (the burst brake)
        self._tellings: dict[tuple, tuple[float, int]] = {}  # (pipe, symbol, code) -> (at, untold)
        self._problem: tuple[float, str, str] | None = None  # (epoch, kind, words): the last one

    def _the_store(self) -> risk_store.RiskStore:
        return self._store or risk_store.STORE

    # -- the kill switch's state -------------------------------------------------------------

    def _load_state(self) -> None:
        """Read the kill switch's state at first use; while it cannot be read it stays unknown.

        One reader at a time: an order arriving while the first read is in flight waits for it
        (at most ``STATE_WAIT_S``, polling so the eventlet hub is never held) instead of being
        refused as unreadable when the store reads fine (gate review L3)."""
        if self._state is not None:
            return
        now = time.monotonic()
        with self._lock:
            if self._state is not None:
                return
            reading = self._loading
            if reading is None:
                if now < self._state_retry_at:
                    return
                self._state_retry_at = now + STATE_RETRY_S
                reading = self._loading = real_threading.Event()
                mine = True
            else:
                mine = False
        if not mine:
            real_threading.wait_for(reading, STATE_WAIT_S)
            return
        try:
            state = self._the_store().state()
            with self._lock:
                if self._state is None:
                    self._state = state
        except Exception:
            logger.exception("The risk guard could not read the kill switch's state")
        finally:
            with self._lock:
                self._loading = None
            reading.set()

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

    def undo_turn_off(self, off: KillSwitchState, state: KillSwitchState) -> None:
        """``state`` was turned off as ``off`` and could not be saved as off: it is on again,
        unless something changed it since."""
        with self._lock:
            if self._state is off:
                self._state = state

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

    def withdrawn(self, tokens: list[int]) -> list[int]:
        """Which of ``tokens`` were let through before the switch went on and then not sent."""
        with self._lock:
            return [t for t in tokens if t in self._withdrawn]

    def send_refusal(self, token: int) -> str | None:
        """Asked while a send waits for its turn at the broker's pacer, and when the turn
        comes: a sentence when the kill switch went on after the order was let through and the
        order was not judged to reduce its position (the send is then dropped and not sent),
        else None."""
        with self._lock:
            refusing = self._refusing()
            if refusing is None:
                return None
            send = self._sends.get(token)
            if send is not None and send.reducing is True:
                return None
        if send is not None and send.reducing is None and self._is_exit_by_last_read(token, send):
            return None  # decision c: a lone exit always goes
        with self._lock:
            refusing = self._refusing()
            if refusing is None:
                return None
            send = self._sends.get(token)
            if send is not None and send.reducing is True:
                return None
            since = "" if refusing.state is None else _hhmm(refusing.state.since)
            self._sends.pop(token, None)
            self._withdrawn[token] = time.monotonic()
            order = None if send is None else send.order
        said = words.kill_switch_on_at_send(since)
        self._note(
            LIVE if order is None else order.pipe,
            order,
            Decision(True, said, KILL_SWITCH_ACTIVE, detail={"reason": "waiting_to_be_sent"}),
        )
        return said

    def _is_exit_by_last_read(self, token: int, send: Send) -> bool:
        """A send let through without being judged (every limit was off), now waiting while the
        switch is on: judged against the last positions read, from the book kept by the last
        check or Upstox's own kept copy and corrected by the order book's fills, never a new
        positions read while it waits. An exit goes; with no
        book to judge it by, or an order that adds risk, it is withdrawn. A yes is recorded on
        the send, so it counts as the exit it is."""
        order = send.order
        if order.pipe != LIVE or order.action != PLACE or order.cnc_sell:
            return False
        try:
            rows = self._kept_rows(order.owner, KEPT_BOOK_WITHIN_S)
            if rows is None:
                rows = self._read.live_cached_rows(order.owner)
            # M6.9: a book of any age judges only as the order book's fills correct it (the
            # row's own day figures say what it already shows), never by a clock.
            rows = None if rows is None else self._corrected(order.owner, rows, strict=True)
            instrument = self._read.instrument_token(order.symbol, order.exchange)
        except readers.Unreadable:
            return False
        if rows is None or instrument is None:
            return False
        position = rules.upstox_position(
            rows, instrument, rules.position_product(LIVE, order.product)
        )
        with self._lock:
            if self._sends.get(token) is not send:
                return False
            # only the sends already judged count: another waiting unjudged send is judged in
            # its own turn, and the first judged to be an exit holds the position
            others = [o for o in self._counted(order, position) if o.reducing is not None]
            verdict = self._verdict(
                order, _Facts(position=position), sum(o.quantity for o in others)
            )
            if not verdict.reducing:
                return False
            send.reducing = True
            send.position_then = verdict.position
            if verdict.reason == rules.EXIT:
                send.expect = rules.exit_expectation(
                    verdict.position, verdict.claimed, send.quantity
                )
            return True

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
            return kind.refusal(decision.words, decision.detail.get("told"))
        if decision.clip is not None:
            args, kwargs = kind.clip(args, kwargs, decision.clip)
        check = None
        if decision.token is not None and kind.pipe == LIVE:
            token = decision.token
            check = rate_limiter.SEND_CHECK.set(lambda: self.send_refusal(token))
        try:
            result = fn(*args, **kwargs)
        except BaseException:
            self.settle(decision.token, ANSWERED, None)  # it may have reached the broker
            raise
        finally:
            if check is not None:
                rate_limiter.SEND_CHECK.reset(check)
        try:
            answer, ref = kind.outcome(result)
        except Exception:
            answer, ref = ANSWERED, None
        self.settle(decision.token, answer, ref)
        if decision.clip is not None:
            result = _noted(result, decision.words, decision.clip)
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
        elif (decision.refused or decision.clip is not None) and order is not None:
            decision = self._told(kind, order, decision)
        if decision.detail.get("unchecked_now"):
            _announce_unchecked(kind.pipe)
        return decision

    def _told(self, kind: _Kind, order: Order, decision: Decision) -> Decision:
        """The coordinator's decision b: a risk refusal of an order that did not come from the
        app's own screens is told on Telegram, merged per symbol and reason per minute. A new
        order's refusal is told through its answer (``nothing_sent``, which the services pass to
        their failure event and Telegram hears); a change, a GTT or a reversal cut to its exit
        is told here. The sentence names any exit it left unsent (words.py)."""
        if _from_app():
            return decision
        by_answer = order.action == PLACE and decision.clip is None
        if by_answer and kind.pipe == SANDBOX:
            return decision  # Analyze mode's own alert already tells every order it refuses
        untold = self.told_now(kind.pipe, order.symbol, decision.code)
        if untold is None:
            return decision  # told in the last minute: counted into the next
        if not by_answer:
            _tell_owner(words.refusal_told(decision.words, untold, kind.pipe, cut=decision.clip))
            return decision
        said = decision.words + words.refused_again(untold)
        return replace(decision, words=said, detail={**decision.detail, "told": untold})

    def told_now(self, pipe: str, symbol: str, code: str) -> int | None:
        """Whether a refusal from outside the app's screens is told now: None when one for the
        same pipe, symbol and reason was told in the last ``TOLD_EVERY_S`` (it is counted),
        else how many like it went untold since the last telling (0 at first)."""
        now = time.monotonic()
        with self._lock:
            kept = self._tellings
            if len(kept) > TOLD_KEPT:
                for key in [k for k, v in kept.items() if now - v[0] >= TOLD_EVERY_S]:
                    del kept[key]
            key = (pipe, symbol, code)
            told = kept.get(key)
            if code == DAILY_LOSS_LIMIT:  # the day's lock was told for every symbol at once
                whole = kept.get((pipe, "", code))
                if whole is not None and now - whole[0] < TOLD_EVERY_S:
                    kept[key] = (whole[0], (told[1] if told else 0) + 1)
                    return None
            if told is not None and now - told[0] < TOLD_EVERY_S:
                kept[key] = (told[0], told[1] + 1)
                return None
            kept[key] = (now, 0)
            return 0 if told is None else told[1]

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
        checked = False  # the order book read for claims, at most once per order
        while True:
            with self._lock:
                now = time.monotonic()
                refusing = self._refusing()  # it may have changed while the book was read
                if lock is None and guards.daily:
                    lock = self._day_locks.get((known.pipe, guards.day))  # another order's
                decided = self._decide(
                    known, facts, guards, refusing, lock, day_unread, reads, now, checked
                )
            if not isinstance(decided, _CheckClaims):
                return decided
            checked = True
            ended = self._ended_orders(known, decided.refs)  # a read: outside the lock
            if ended is not None:
                with self._lock:
                    now = time.monotonic()
                    working = ended.get(_WORKING, set())
                    self._drop_claims(known.pipe, ended.get(_ENDED, set()))
                    self._drop_claims(known.pipe, working, resting_only=True)
                    self._seen_working(known.pipe, working, now)
                    # Complete, or not in the book: past SEND_HELD_AT_MOST_S the positions book
                    # has had its time to show the exit, so the claim stands for nothing more.
                    self._drop_stale_claims(
                        known.pipe, decided.refs - working - ended.get(_ENDED, set()), now
                    )

    def _decide(
        self,
        known: Order,
        facts: _Facts,
        guards: _Guards,
        refusing: _Refusing | None,
        lock: risk_store.DayLock | None,
        day_unread: bool,
        reads: _Reads,
        now: float,
        checked: bool,
    ) -> Decision | _CheckClaims:
        """Step 3: judge and decide, under the lock. ``_CheckClaims`` when the order would be
        refused only because of claims the order book has not been read for yet."""
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
        if not checked:
            refs = self._claims_in_the_way(known, facts, verdict)
            if refs:
                return _CheckClaims(refs)
        if (refusing is not None or lock is not None) and self._clippable(known, verdict):
            return self._clip(known, refusing, lock, verdict, now)
        if refusing is not None:
            return self._refuse(known, refusing, verdict, sent_at)
        if lock is not None:
            return self._refuse_daily(known, lock, verdict, sent_at)
        if day_unread:
            detail = {"reason": verdict.reason, "position": verdict.position}
            return Decision(
                True, words.daily_unreadable(known.pipe), GUARD_UNAVAILABLE, detail=detail
            )
        return self._held_to_settings(known, guards, verdict, reads, now, sent_at)

    @staticmethod
    def _clippable(order: Order, verdict: rules.Verdict) -> bool:
        """A new order that would close its position and open it the other way: its exit can
        be sent alone (a change to an order or a GTT is judged whole)."""
        return order.action == PLACE and verdict.reason == rules.CROSSES and verdict.room > 0

    def _clip(
        self,
        order: Order,
        refusing: _Refusing | None,
        lock: risk_store.DayLock | None,
        verdict: rules.Verdict,
        now: float,
    ) -> Decision:
        """The coordinator's decision a: under the kill switch or the day's lock a reversal
        sends its exit to 0 and only the part beyond is refused. The exit is recorded as one;
        call under the lock."""
        room = verdict.room
        exit_part = replace(order, quantity=room)
        exited = rules.Verdict(True, rules.EXIT, verdict.position, verdict.claimed, room)
        token = self._register(exit_part, exited, now)
        beyond = (order.quantity or 0) - room
        if refusing is not None:
            since = None if refusing.state is None else _hhmm(refusing.state.since)
            said = words.reversal_cut(order.name, order.side, room, beyond, since=since)
            code = KILL_SWITCH_ACTIVE if refusing.state is not None else RISK_STATE_UNREADABLE
        else:
            said = words.reversal_cut(
                order.name, order.side, room, beyond, locked=(lock.figure, lock.loss_limit)
            )
            code = DAILY_LOSS_LIMIT
        detail = {"reason": verdict.reason, "position": verdict.position, "sent": room,
                  "refused": beyond}  # fmt: skip
        return Decision(words=said, code=code, token=token, detail=detail, clip=room)

    def _claims_in_the_way(self, order: Order, facts: _Facts, verdict: rules.Verdict) -> frozenset:
        """The broker orders whose claims alone stop this order being an exit: none when it adds
        risk whatever was sent before it. Call under the lock (after ``_judge``)."""
        if verdict.claimed <= 0 or not self._verdict(order, facts, 0).reducing:
            return frozenset()
        return frozenset(send.ref for send in self._counted(order, facts.position) if send.ref)

    def _ended_orders(self, order: Order, refs: frozenset) -> dict[str, set[str]] | None:
        """Of ``refs``, the orders the broker shows cancelled or rejected (key ``_ENDED``: their
        claims stand for nothing) and those it shows working (key ``_WORKING``: the claim of a
        resting limit or stop stands for nothing, but a market order not filled yet is still on
        its way to the book, so its claim stays until the book shows it final). A read that
        fails answers None, and ends none."""
        try:
            if order.pipe == LIVE:
                statuses = self._read.live_order_statuses(order.owner)
            else:
                statuses = self._read.sandbox_order_statuses(order.owner, sorted(refs))
        except readers.Unreadable as exc:
            logger.warning(f"The risk check could not read the order book for claims: {exc}")
            return None
        found: dict[str, set[str]] = {}
        for ref in refs:
            status = statuses.get(ref)
            if status is None or status == "complete":
                continue
            kind = _WORKING if status in ("open", "trigger pending") else _ENDED
            found.setdefault(kind, set()).add(ref)
        return found

    def _drop_claims(self, pipe: str, refs: set[str], resting_only: bool = False) -> None:
        """What was sent on these broker orders stops counting; call under the lock.
        ``resting_only``: only the sends of a limit or stop order (a market order that the book
        shows working has not reached the positions book yet, so its claim stays)."""
        for token, send in list(self._sends.items()):
            if send.order.pipe == pipe and send.ref in refs:
                if resting_only and not _rests(send.order):
                    continue
                del self._sends[token]

    def _seen_working(self, pipe: str, refs: set[str], now: float) -> None:
        """The order book showed these orders working: their claims count from now again
        (``_forget``); call under the lock."""
        for send in self._sends.values():
            if send.order.pipe == pipe and send.ref in refs:
                send.working_at = now

    def _drop_stale_claims(self, pipe: str, refs: set[str], now: float) -> None:
        """Of the sends on these orders, drop the ones past ``SEND_HELD_AT_MOST_S`` from their
        answer or the last read that showed them working: the book was just read and did not
        show the order working, so the claim has nothing left to wait for. Call under the lock."""
        for token, send in list(self._sends.items()):
            if send.order.pipe != pipe or send.ref not in refs or send.answered is None:
                continue
            if now - max(send.answered, send.working_at or _NEVER) > SEND_HELD_AT_MOST_S:
                del self._sends[token]

    def release(self, pipe: str, ref: str) -> None:
        """An order the broker cancelled: its claims end (``cancelled``)."""
        with self._lock:
            self._drop_claims(pipe, {ref})

    def _refusing(self) -> _Refusing | None:
        """What refuses new risk now; call under the lock."""
        state = self._state
        if state is None or state.on:
            return _Refusing(state)
        return None

    # -- reading what an order does, outside the lock ---------------------------------------------

    def _facts(self, order: Order, fallback: bool = False) -> tuple[Order, _Facts]:
        """What the order does, read from the broker's books. ``fallback``: new risk is refused
        now, so a positions book kept from an earlier read, corrected by the order book, may judge it."""
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

    def _live_rows(
        self, owner: str, fallback: bool = False, kept_within: float = KEPT_BOOK_WITHIN_S
    ) -> tuple[list, bool]:
        """Upstox's positions for ``owner``, kept with the time the read began. When they cannot
        be read and ``fallback`` allows it, the book read in the last ``kept_within`` seconds
        and corrected by the order book, with True; else Unreadable. Rows are corrected by the
        fills the order book shows (``_corrected``), so a lagging positions book judges right."""
        started = time.monotonic()
        try:
            rows = self._read.live_cached_rows(owner)
            if rows is None:
                rows = self._read.live_rows(owner)
        except readers.Unreadable:
            kept = self._kept_rows(owner, kept_within) if fallback else None
            if kept is None:
                raise
            # A book kept from before judges only as the order book's fills correct it, however
            # old it is: the row's own day figures say what it already shows.
            corrected = self._corrected(owner, kept, strict=True)
            if corrected is None:
                raise
            return corrected, True
        with self._lock:
            self._rows = (owner, started, rows)  # as Upstox shows them: corrected on every use
        return self._corrected(owner, rows) or rows, False

    def _corrected(self, owner: str, rows: list, strict: bool = False) -> list | None:
        """``rows`` with each quantity corrected by what Upstox's order book says was filled
        since the row was read (sudarshan.books.fills), read after the rows: the rows the caller
        handed for the exits it is sending (``order_book_read``), else a read of the gate's own.
        A row it cannot correct stays as read, unless ``strict``, which answers None for any such
        row; so does a book that cannot be read. A position the day's fills show in a product the
        rows do not list yet (bought a moment ago) is added as a row of its own, so its exit is
        judged an exit, never an opening order (review of fix/guard-on, MEDIUM-2); but not in an
        instrument the rows list in another product, which cannot be told from a conversion in
        Upstox's app whose new row is not shown yet: Exit all names that and sends nothing, and
        the gate reads the product as the rows do (re-review of v0.5.1, LOW-C)."""
        orders = handed_order_rows(owner)
        if orders is None:
            try:
                orders = self._read.live_order_rows(owner)
            except Exception:
                return None
        # Each row is corrected together with the other products of its instrument, so a
        # position converted in Upstox's app is judged right (sudarshan.books.fills).
        held_rows = fills.held_each(rows, orders)
        out = []
        for row, held in zip(rows, held_rows, strict=True):
            if held is None:
                if strict and isinstance(row, dict):
                    return None
                out.append(row)
            else:
                out.append({**row, "quantity": held})
        listed = {
            str(row.get("instrument_token") or "").strip()
            for row in rows
            if isinstance(row, dict) and str(row.get("instrument_token") or "").strip()
        }
        for exchange, tradingsymbol, token, product, net in fills.unlisted_nets(rows, orders):
            if token in listed:
                continue  # in another product's row: it may be a conversion (LOW-C)
            out.append(
                {
                    "instrument_token": token,
                    "product": product,
                    "quantity": net,
                    "exchange": exchange,
                    "tradingsymbol": tradingsymbol,
                    "unlisted": True,  # from the fills alone: no money figures to read
                }
            )
        return out

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
            # the broker sends the request's kind, not the stored one: OCO if either says OCO
            trigger_type="OCO"
            if "OCO" in (gtt.trigger_type.upper(), order.trigger_type.upper())
            else gtt.trigger_type,
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
                now_shown = self._mark_unchecked(order.pipe, time.monotonic(), paused=True)
            if now_shown:
                _announce_unchecked(order.pipe)
            return None, True
        if not daily.reached(day.limit, figure):
            return None, False
        return self._lock_day(order.pipe, day, figure), False

    def _window(self, exchange: str, day: date) -> dict | None:
        """The segment's session window on ``day`` (market_calendar_db), kept a while; None on
        a day it is closed. Raises when the calendar cannot be read."""
        key = (exchange, day.isoformat())
        window = self._sessions.get(key, _UNREAD)
        if window is _UNREAD:
            from database.market_calendar_db import get_effective_session_window

            window = get_effective_session_window(day, exchange)
            if window is not None:  # "closed" is asked again: a read that failed says the same
                self._sessions[key] = window
        return window

    def _in_session(self, exchange: str, at: datetime) -> bool:
        """Whether the segment is in session now, by the exchange's own hours (R11)."""
        try:
            window = self._window(exchange, at.astimezone(IST).date())
        except Exception:
            logger.exception("The market calendar could not be read for the daily limit")
            # Looked at from India's morning on: never in the hours after midnight, when
            # Upstox's book can still show the day before.
            return at.astimezone(IST).hour >= 9
        return daily.in_session(window, at)

    def session(self, exchange: str, at: datetime) -> dict[str, Any]:
        """Whether the figure is looked at for ``exchange`` at ``at``, and when its session next
        opens (ISO, India's time), for the daily card. ``opens_at`` is None while in session or
        when the calendar cannot say."""
        if self._in_session(exchange, at):
            return {"in_session": True, "opens_at": None}
        start_ms = None
        try:
            today = at.astimezone(IST).date()
            for ahead in range(SESSION_LOOKAHEAD_DAYS):
                window = self._window(exchange, today + timedelta(days=ahead))
                if window is not None and int(window["start_ms"]) > at.timestamp() * 1000:
                    start_ms = int(window["start_ms"])
                    break
        except Exception:
            logger.exception("The market calendar could not be read for the daily card")
        opens = None if start_ms is None else datetime.fromtimestamp(start_ms / 1000, IST)
        return {"in_session": False, "opens_at": None if opens is None else opens.isoformat()}

    def _day_figure(self, order: Order) -> daily.DayFigure:
        if order.pipe == SANDBOX:
            return self._read.sandbox_day(order.owner)
        return daily.live_figure(self._rows_for_day(order.owner))

    def _rows_for_day(self, owner: str) -> list:
        """The book the order was just judged on; else a read; else the one kept in the last
        ``DAY_BOOK_WITHIN_S`` as the order book corrects it (its money is that old, so no
        longer: the kept book that judges quantities for ``KEPT_BOOK_WITHIN_S`` serves no figure)."""
        kept = self._kept_rows(owner, 1.0)
        if kept is not None:
            return kept
        return self._live_rows(owner, fallback=True, kept_within=DAY_BOOK_WITHIN_S)[0]

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
                # its own message covers the refusals of its first minute: marked with the lock,
                # so no refusal that sees the lock is told as well
                self._tellings[(pipe, "", DAILY_LOSS_LIMIT)] = (time.monotonic(), 0)
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
        return self.day_unchecked_left(pipe) is not None

    def day_unchecked_left(self, pipe: str) -> float | None:
        """How many more seconds the banner says the daily limit is not being checked; None
        when it is being checked. Seconds, not a time of day, so a phone whose clock is a
        little off still shows it for as long."""
        with self._lock:
            at = self._unchecked.get(pipe)
        if at is None:
            return None
        left = at + UNCHECKED_SHOWN_S - time.monotonic()
        return round(left, 1) if left >= 0 else None

    def day_unchecked_paused(self, pipe: str) -> bool:
        """Whether the daily limit is not being checked because today's figure could not be read
        while the book could (new orders are paused, exits go), rather than because the book
        could not be read (orders go, since any may be an exit). Read with
        ``day_unchecked_left``, which says whether it is being said at all."""
        with self._lock:
            return self._paused.get(pipe, False)

    def _mark_unchecked(self, pipe: str, now: float, paused: bool = False) -> bool:
        """The daily limit could not be checked in ``pipe`` just now: ``paused`` when it was
        today's figure that could not be read (new orders refused), else the book (orders go).
        While a book that could not be read is being said, that stays the line: orders go then,
        and an order refused for its figure says so in its own words. True when the banner was
        not already saying this (the screens are told). Call under the lock."""
        at = self._unchecked.get(pipe)
        shown = at is not None and now - at <= UNCHECKED_SHOWN_S
        was_paused = self._paused.get(pipe, False)
        if paused and shown and not was_paused:
            paused = False
        self._unchecked[pipe] = now
        self._paused[pipe] = paused
        return not shown or was_paused != paused

    # -- deciding, under the lock -------------------------------------------------------------------

    def _judge(self, order: Order, facts: _Facts, now: float) -> tuple[rules.Verdict, float | None]:
        claimed, sent_at = self._claimed(order, facts.position, now)
        return self._verdict(order, facts, claimed), sent_at

    def _verdict(self, order: Order, facts: _Facts, claimed: int) -> rules.Verdict:
        """What the order does to its position, with ``claimed`` already on its way."""
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
        return verdict

    def _claimed(self, order: Order, position: int, now: float) -> tuple[int, float | None]:
        """c: what was sent on the order's position and side that the book does not show yet,
        and when the latest of it was sent."""
        counted = self._counted(order, position)
        total = sum(send.quantity for send in counted)
        latest = max((send.sent_at for send in counted), default=None)
        return total, latest

    def _counted(self, order: Order, position: int) -> list[Send]:
        """The sends that count as claims against ``order``: on its position and side, not yet
        shown by the book. Call under the lock.

        An exit the book now shows is dropped here. A send let go while nothing judged it counts
        only until it is answered. Of the sends naming one broker order (an order and its
        modifies), only the newest counts, and a modify leaves out its own order.
        """
        newest: dict[str, int] = {}
        counted: list[tuple[int, Send]] = []
        for token, send in list(self._sends.items()):
            if send.order.key != order.key or send.order.action not in (PLACE, MODIFY):
                continue
            if send.reducing is None and send.answered is not None:
                continue  # never judged: a claim only while it is on its way
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
        return [
            send
            for token, send in counted
            if not (send.ref and newest.get(send.ref) != token) and send.order.side == order.side
        ]

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
        for token, when in list(self._withdrawn.items()):
            if now - when > SEND_HELD_AT_MOST_S:
                del self._withdrawn[token]
        for token, send in list(self._sends.items()):
            answered = send.answered
            if answered is None:
                gone = now - send.admitted > SEND_HELD_AT_MOST_S
            elif send.ref and _market_exit(send):
                # Upstox named its order, a market exit: the claim stands until the book shows
                # the exit (``_counted``) or the order ended (``_ended_orders``). No clock ends it
                # while the order may still be working (resting price-protected, L1): past
                # ``SEND_HELD_AT_MOST_S`` it stands until the next order book read answers for
                # it (``_drop_stale_claims``), and never past ``CLAIM_KEPT_AT_MOST_S``.
                gone = now - answered > CLAIM_KEPT_AT_MOST_S
            elif send.ref:
                # An entry, or a resting limit or stop: the book has had its time to show it, and
                # a resting order's claim stands for nothing once the book shows it resting.
                gone = now - answered > SEND_HELD_AT_MOST_S
            else:
                gone = now - answered > SHOWS_WITHIN_S  # no order to ask the book about
            if gone:
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
        self,
        order: Order,
        lock: risk_store.DayLock,
        verdict: rules.Verdict,
        sent_at: float | None = None,
    ) -> Decision:
        detail = {
            "reason": verdict.reason,
            "position": verdict.position,
            "room": verdict.room,
            "figure": lock.figure,
            "limit": lock.loss_limit,
        }
        text = words.daily_locked(
            lock.figure,
            lock.loss_limit,
            verdict,
            position=order.name,
            side=order.side,
            claim_sent_at=sent_at,
        )
        return Decision(True, text, DAILY_LOSS_LIMIT, detail=detail)

    def _unjudged(self, order: Order, guards: _Guards, now: float) -> Decision:
        """With nothing refusing new risk, an order whose book could not be read goes, noted: it
        may be an exit, and an exit is never refused for a read that failed."""
        detail = {}
        if guards.daily:
            if self._mark_unchecked(order.pipe, now):
                detail["unchecked_now"] = True  # admit() tells the screens, outside the lock
            said = words.daily_not_checked(order.pipe)
        else:
            said = words.checks_not_run(order.pipe)
        token = self._register(order, None, now)
        return Decision(token=token, words=said, code=GUARD_UNAVAILABLE, detail=detail)

    def _held_to_settings(
        self,
        order: Order,
        guards: _Guards,
        verdict: rules.Verdict,
        reads: _Reads,
        now: float,
        sent_at: float | None = None,
    ) -> Decision:
        """An order adding risk, with nothing refusing new risk: the owner's own limits, each
        only when he has turned it on. Call under the lock."""
        refusal, unchecked = self._over_limits(order, guards, verdict, reads, sent_at)
        if refusal is not None:
            return refusal
        closes = guards.braked(order) and self._closes_unseen_entry(order, verdict)
        if guards.braked(order) and not closes:
            refusal = self._brake(order.pipe, guards.settings, now, key=_credit_key(order))
            if refusal is not None:
                return refusal
        token = self._register(order, verdict, now)
        if closes:
            self._sends[token].closed = self._sends[token].quantity  # it closes; nothing closes it
        if unchecked:
            return Decision(token=token, words=unchecked, code=GUARD_UNAVAILABLE)
        return Decision(token=token)

    def _closes_unseen_entry(self, order: Order, verdict: rules.Verdict) -> bool:
        """Whether ``order`` closes an entry this process let go whose fill the book does not show
        yet. The book still says flat, so it reads as a new order; the burst brake counts the
        entry only (gate review L5). Call under the lock."""
        for send in self._sends.values():
            if (
                send.order.key == order.key
                and send.reducing is False
                and send.order.side != order.side
                and send.order.action in (PLACE, MODIFY)
                and send.position_then == verdict.position
                and send.quantity - send.closed >= (order.quantity or 0) > 0
            ):
                send.closed += order.quantity or 0
                return True
        return False

    def _over_limits(
        self,
        order: Order,
        guards: _Guards,
        verdict: rules.Verdict,
        reads: _Reads,
        sent_at: float | None = None,
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
                said = words.band_refused(breach, verdict, position=order.name)
                return Decision(True, said, PRICE_BAND, detail=detail), None
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
                said = words.size_refused(
                    excess, verdict, position=order.name, side=order.side, claim_sent_at=sent_at
                )
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
        """A batch (batch.py) judged whole before any part is sent: all or nothing in every state
        (the coordinator's decision c). It is refused when any part adding risk would be refused
        alone: under the kill switch, on a locked day (the day's figure read for it when a limit
        is set), over the band or the size limit; then by the burst brake as a whole (index
        None). Returns the first such part's index and the refusal a single order would get,
        its detail naming the batch's exits (``exits``: their indexes), which are not sent
        either; or None. A batch of exits only always goes.

        Nothing is recorded but the brake's one admission for the batch and its credits: one
        for each order a part adding risk will send (``slices``: the quantities each part goes
        as, its own quantity unless it is split), under that order's instrument, side and
        quantity, so those orders are not counted again and no other order uses them. The
        kill switch, the day's lock and the limits are judged before the brake, so a batch
        refused anyway takes no admission. Each part is decided again as it is sent. The parts
        are judged in order, each against the book and the exits earlier parts would send, so
        two parts selling the same long take its room once. A part whose book cannot be read is
        left to that decision while nothing refuses new risk; under the kill switch or on a
        locked day the batch is refused (decision d).
        """
        if not orders:
            return None
        self._load_state()
        guards = self._guards(orders[0])
        settings = guards.settings
        with self._lock:
            refusing = self._refusing()
        lock = guards.lock
        limits = settings is not None and (
            settings.band_on or settings.size_on or settings.burst_on
        )
        if refusing is None and lock is None and not guards.daily and not limits:
            return None
        every = refusing is not None or lock is not None or guards.daily  # read every part
        fallback = refusing is not None or lock is not None
        taken: dict[tuple, int] = {}  # (position, side): what earlier parts would exit
        judged: list[tuple] = []  # (index, order, facts, verdict, sent_at): parts adding risk
        exits: list[int] = []
        unreadable = None  # the first part whose book could not be read
        for index, order in enumerate(orders):
            if not every and not guards.braking:
                if guards.price_band(order) is None and not guards.sized(order):
                    continue
            known, facts = self._facts(order, fallback)  # reads: outside the lock
            if facts.pass_through:
                continue
            if facts.unreadable:
                unreadable = unreadable or (index, known)
                continue
            mine = (known.key, known.side)
            with self._lock:
                claimed, sent_at = self._claimed(known, facts.position, time.monotonic())
            verdict = rules.classify(
                known.side,
                known.quantity,
                facts.position,
                claimed + taken.get(mine, 0),
                cnc_sell=known.cnc_sell,
            )
            if verdict.reducing:
                taken[mine] = taken.get(mine, 0) + (known.quantity or 0)
                exits.append(index)
                continue
            judged.append((index, known, facts, verdict, sent_at))
        found = self._batch_refusal(orders, guards, refusing, lock, judged, unreadable, slices)
        if found is None:
            return None
        index, refusal = found
        return index, replace(refusal, detail={**refusal.detail, "exits": exits})

    def _batch_refusal(
        self,
        orders: list[Order],
        guards: _Guards,
        refusing: _Refusing | None,
        lock: risk_store.DayLock | None,
        judged: list[tuple],
        unreadable: tuple[int, Order] | None,
        slices: list[list[int]] | None,
    ) -> tuple[int | None, Decision] | None:
        """``check_together``'s decision, in the gate's order: what refuses new risk, the day,
        the owner's limits, then the burst brake. Each refusal goes to the risk history."""
        if unreadable is not None and (refusing is not None or lock is not None):
            index, known = unreadable
            return self._noted_batch(index, known, self._unreadable(known, refusing, lock))
        if not judged:
            return None  # exits only: an exit always goes
        index, known, facts, verdict, sent_at = judged[0]
        if refusing is not None:
            refusal = self._refuse(known, refusing, verdict, sent_at)
            return self._noted_batch(index, known, refusal)
        if lock is None and guards.daily:
            lock, day_unread = self._day_facts(known, facts, guards)  # may lock the day now
            if lock is None and day_unread:
                detail = {"reason": verdict.reason, "position": verdict.position}
                refusal = Decision(
                    True, words.daily_unreadable(known.pipe), GUARD_UNAVAILABLE, detail=detail
                )
                return self._noted_batch(index, known, refusal)
        if lock is not None:
            refusal = self._refuse_daily(known, lock, verdict, sent_at)
            return self._noted_batch(index, known, refusal)
        credits: dict[tuple, int] = {}  # the orders the parts adding risk will send
        for index, known, facts, verdict, sent_at in judged:
            sent_as = slices[index] if slices is not None and index < len(slices) else None
            for quantity in sent_as or [known.quantity]:
                held = _credit_key(known, quantity)
                credits[held] = credits.get(held, 0) + 1
            reads = self._limit_reads(known, facts, guards, adding=True)
            refusal, _unchecked = self._over_limits(known, guards, verdict, reads, sent_at)
            if refusal is not None:
                return self._noted_batch(index, known, refusal)
        if guards.braking and credits:
            with self._lock:
                refusal = self._brake(
                    orders[0].pipe, guards.settings, time.monotonic(), credits=list(credits.items())
                )
            if refusal is not None:
                if refusal.detail.get("tell"):
                    _tell_owner(refusal.detail["tell"])
                return self._noted_batch(None, orders[0], refusal)  # the batch as a whole
        return None

    def _noted_batch(
        self, index: int | None, order: Order, refusal: Decision
    ) -> tuple[int | None, Decision]:
        self._note(order.pipe, order, refusal)  # the risk history shows it
        return index, refusal

    def _unreadable(
        self, order: Order, refusing: _Refusing | None, lock: risk_store.DayLock | None = None
    ) -> Decision:
        """New risk is refused now and the order cannot be judged: no book, not even one kept
        from the last read corrected by the order book, so it is refused with the way to an urgent exit."""
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
            refused = decision.refused or decision.clip is not None  # a cut: the rest refused
            kind = "order_refused" if refused else "guard_error"
            if decision.code == GUARD_UNAVAILABLE:
                kind = "guard_unavailable"
            key = f"{decision.code}|{surface}|{symbol}"
            said = decision.words or "The risk check broke on an order; it was let go."
            detail = {"code": decision.code, "symbol": symbol, **decision.detail}
            now = time.monotonic()
            with self._lock:
                if kind != "order_refused":
                    self._problem = (time.time(), kind, said)
                note = self._notes.get(key)
                if note is None and len(self._notes) >= NOTE_MAX_KEYS:
                    key = f"{decision.code}|{pipe}|many"  # a runaway over many symbols: one row
                    note = self._notes.get(key)
                    detail = {"code": decision.code, "symbol": "", **decision.detail}
                if note is None:
                    self._notes[key] = _Note(kind, said, pipe, surface, detail, _NEVER, 1)
                else:
                    note.pending += 1
                    note.words, note.detail = said, detail
                batch = self._take_due(now)
            for item in batch:
                self._write_note(*item)
            if batch:
                with self._lock:
                    self._writing = False
        except Exception:
            with self._lock:
                self._writing = False
            logger.exception("A risk refusal could not be written to the audit")

    def _take_due(self, now: float) -> list[tuple[str, _Note]]:
        """The counts to write now: at most ``NOTE_ROWS_PER_WINDOW`` rows, and only once in a
        ``NOTE_EVERY_S`` window overall, by one writer at a time, oldest first. A commit is disk
        work the eventlet hub cannot interleave, so however many symbols a runaway script tries,
        the hub is held for those few commits per window and no more (gate review M8). The rest
        stay counted in memory for the next window or ``flush_notes``. Call under the lock."""
        for key, note in list(self._notes.items()):
            if not note.pending and now - note.written > NOTE_KEPT_S:
                del self._notes[key]  # written long ago and quiet since
        if self._writing or now - self._note_window < NOTE_EVERY_S:
            return []
        due = sorted((n.written, key) for key, n in self._notes.items() if n.pending)
        batch = []
        for _written, key in due[:NOTE_ROWS_PER_WINDOW]:
            note = self._notes[key]
            batch.append((key, replace(note)))
            note.pending, note.written = 0, now
        if batch:
            self._note_window, self._writing = now, True
        return batch

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


def order_book_read(owner: str, rows: list) -> None:
    """A caller sizing exits read Upstox's order book for ``owner`` (the Upstox module's
    ``_read_order_rows``: a smart order, Exit all): the gate corrects its positions with these
    rows for the exits that follow in the same ``handing_order_rows`` scope, instead of reading
    the book once more for each of them, until ``HANDED_ORDERS_WITHIN_S`` have passed or the
    scope ends. Outside a scope nothing is handed."""
    if _HANDED_ORDERS.get() is _NO_SCOPE:
        return
    _HANDED_ORDERS.set((str(owner), time.monotonic(), list(rows)))


def handed_order_rows(owner: str) -> list | None:
    """The order book rows the current scope's caller handed for ``owner``, while they are at
    most ``HANDED_ORDERS_WITHIN_S`` old; else None, and the gate reads the book itself."""
    handed = _HANDED_ORDERS.get()
    if handed is _NO_SCOPE or handed is None:
        return None
    read_for, read_at, rows = handed
    if read_for != str(owner) or time.monotonic() - read_at > HANDED_ORDERS_WITHIN_S:
        return None
    return rows


@contextlib.contextmanager
def handed_orders_kept_apart():
    """Run a block whose reads are handed to the gate for that block alone: nothing handed before
    it is used in it, and nothing it hands outlives it, whatever thread runs it. The token
    resets the variable in ``finally``, so no path depends on a request's teardown."""
    token = _HANDED_ORDERS.set(None)
    try:
        yield
    finally:
        _HANDED_ORDERS.reset(token)


def handing_order_rows(fn: Callable) -> Callable:
    """Decorate a broker function that reads the order book to size the exits it sends (the
    Upstox module's place_smartorder_api and close_all_positions): its read is handed to the
    gate for its own sends alone (``handed_orders_kept_apart``)."""

    @functools.wraps(fn)
    def scoped(*args, **kwargs):
        with handed_orders_kept_apart():
            return fn(*args, **kwargs)

    return scoped


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


def gtt_refused(symbol: str, exchange: str) -> None:
    """A sandbox GTT whose order the gate refused when it triggered is rejected for good
    (sandbox/gtt_manager.py): the owner is told once; never raises."""
    try:
        name = trader_name((str(exchange or "").upper(), str(symbol or ""), ""))
        _tell_owner(words.sandbox_gtt_refused(name))
    except Exception:
        logger.exception("A refused sandbox GTT could not be told on Telegram")


def cancelled(orderid: object, pipe: str = LIVE) -> None:
    """A cancel the broker confirmed (broker/upstox/api/order_api.cancel_order, the sandbox's
    OrderManager.cancel_order): what this process sent on that order stops counting against its
    position. One environment lookup while the guard does not cover the pipe; never raises."""
    if not flag.covers(pipe):
        return
    try:
        GATE.release(pipe, str(orderid))
    except Exception:
        logger.exception("A cancelled order's claims could not be let go")


def _announce_unchecked(pipe: str) -> None:
    """Every open screen hears the daily limit is not being checked (its banner reads /state
    again, which says until when); never fails an order."""
    try:
        from extensions import emit_from_any_thread

        emit_from_any_thread("sudarshan_risk_day", {"pipe": pipe, "unchecked": True})
    except Exception:
        logger.exception("An unchecked daily limit could not be told to the open screens")


def _from_app() -> bool:
    """Whether the order came from the app's own screens: a request carrying the owner's logged
    in session, as the phone's ticket, Positions and Orders send theirs (even to /api/v1).
    Webhooks, scripts, Flow, the strategy module, MCP and Telegram carry none."""
    try:
        from flask import has_request_context, session

        return has_request_context() and bool(session.get("user"))
    except Exception:
        return False


def _hhmm(at: datetime | None) -> str:
    return "" if at is None else at.astimezone(IST).strftime("%H:%M")


def _surface() -> str:
    """Where the order came from: the route's pattern, or background work. Never the path
    itself: a webhook's token is in its path (/strategy/webhook/<token>, /flow/webhook/<token>),
    and the audit is shown on the Risk page and kept 90 days."""
    try:
        from flask import has_request_context, request

        if has_request_context():
            rule = request.url_rule
            return rule.rule if rule is not None else "request"
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
    refusal: Callable[[str, int | None], Any]  # the sentence, and whether it is told (untold)
    outcome: Callable[[Any], tuple[str, str | None]]
    at: int = 0  # where a place's order data is in the call's arguments

    def clip(self, args: tuple, kwargs: dict, quantity: int) -> tuple[tuple, dict]:
        """The call's arguments with the order's quantity cut (a reversal's exit): its data
        copied, never changed in place."""
        data = args[self.at]
        given = data.get("quantity")
        cut = {**data, "quantity": quantity if isinstance(given, int) else str(quantity)}
        return (*args[: self.at], cut, *args[self.at + 1 :]), kwargs


def _noted(result, said: str, quantity: int):
    """A reversal cut to its exit: the answer's body carries the sentence (``risk_note``) and
    what went (``sent_quantity``), for the services to pass on; the answer is the broker's."""
    try:
        if isinstance(result, tuple) and len(result) == 3:
            first, body, last = result
            if isinstance(body, dict):
                return first, {**body, "risk_note": said, "sent_quantity": quantity}, last
    except Exception:
        logger.exception("A reversal's note could not be added to its answer")
    return result


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


def _refuse_with_response(text: str, told: int | None = None):
    body = {"status": "error", "message": text}
    if told is not None:  # Telegram hears it (subscribers/telegram_subscriber.on_order_failed)
        body["nothing_sent"] = True
    return RefusedResponse(), body, None


def _refuse_with_body(text: str, told: int | None = None):
    return {"status": "error", "message": text}, 403


def withdrawn_response(text: str):
    """A place or a GTT let through and then not sent (the kill switch went on while it waited
    for its turn): the answer a refusal gives, ``(response, body, None)``."""
    return RefusedResponse(), {"status": "error", "message": text, "nothing_sent": True}, None


def withdrawn_body(text: str):
    """The same for a change to an order or a GTT: ``(body, status)``."""
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


def _refuse_in_the_sandbox(text: str, told: int | None = None):
    # risk_refused: a GTT leg the gate refuses is rejected for good (sandbox/gtt_manager.py);
    # nothing_sent: Analyze mode's order alert titles it as not sent
    body = {"status": "error", "message": text, "mode": "analyze", "risk_refused": True}
    return False, {**body, "nothing_sent": True}, 403


def _answer_in_the_sandbox(result) -> tuple[str, str | None]:
    """A sandbox answer ``(ok, body, status)``: a 4xx placed nothing; anything else may have."""
    ok, body, status = result
    if not ok and isinstance(status, int) and status < 500:
        return REFUSED, None
    ref = (body.get("orderid") or body.get("trigger_id")) if isinstance(body, dict) else None
    return ANSWERED, str(ref) if ref else None


#: sandbox/order_manager.OrderManager.place_order, inside its position lock
sandbox_place = _guarded(
    _Kind(SANDBOX, _sandbox_place, _refuse_in_the_sandbox, _answer_in_the_sandbox, at=1)
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
