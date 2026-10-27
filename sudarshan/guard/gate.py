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
switch is on, or its state could not be read, it is refused (fail closed); a read of the book
that failed refuses too, with a sentence pointing at the Upstox app for an urgent exit.

The lock is a real one from utils.real_threading, held for dict work only: correct under the
eventlet worker (no I/O, no logging and no wait inside it, so the hub never stalls on it), under
gthread's real threads, and on the development server.
"""

from __future__ import annotations

import functools
import itertools
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from sudarshan.books.exit_guard import EXIT_SHOWS_WITHIN_S
from utils import real_threading
from utils.broker_backpressure import BusyResponse
from utils.logging import get_logger

from . import flag, readers, rules, words
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
#: While the kill switch is on, a position book read this recently that shows a position flat,
#: with nothing sent on it, is enough to refuse an order on it without reading again.
BOOK_REUSE_S = 5.0
#: How soon the kill switch's state is read again after a read failed.
STATE_RETRY_S = 2.0
#: Refusals of one kind are written to the audit at most this often; the rest are counted in
#: memory and written with the next one. A SQLite commit is disk work eventlet cannot make
#: cooperative, so a write per refusal would stall the worker under a runaway script.
NOTE_EVERY_S = 2.0
#: A count kept in memory this long without a refusal is written by the next note or flush.
NOTE_KEPT_S = 60.0

# Machine codes, for the audit and SUDARSHAN's own endpoints, never for a sentence.
KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
RISK_STATE_UNREADABLE = "RISK_STATE_UNREADABLE"
GUARD_UNAVAILABLE = "GUARD_UNAVAILABLE"
GUARD_ERROR = "GUARD_ERROR"

PLACE, MODIFY, GTT_PLACE, GTT_MODIFY = "place", "modify", "gtt_place", "gtt_modify"
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
class _Refusing:
    """What refuses new risk right now."""

    state: KillSwitchState | None  # None: its state could not be read


@dataclass(frozen=True)
class _Facts:
    position: int = 0
    working: rules.WorkingOrder | None = None
    unreadable: bool = False
    pass_through: bool = False  # nothing to judge: the broker answers it (a final order)


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
        order = None
        try:
            order = kind.describe(*args, **kwargs)
            decision = self._admit(order)
        except Exception:
            logger.exception("The risk check broke on an order")
            decision = self._broke(kind.pipe, order)
        if decision.refused or decision.code:
            self._note(kind.pipe, order, decision)
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
        with self._lock:
            now = time.monotonic()
            self._forget(now)
            refusing = self._refusing()
            if refusing is None:
                return Decision(token=self._register(order, None, now))
        known, facts = self._facts(order, refusing)  # reads: outside the lock
        with self._lock:
            now = time.monotonic()
            refusing = self._refusing()  # it may have changed while the book was read
            if refusing is None or facts.pass_through:
                return Decision(token=self._register(known, None, now))
            if facts.unreadable:
                return self._unreadable(known, refusing)
            verdict, sent_at = self._judge(known, facts, now)
            if verdict.reducing:
                return Decision(token=self._register(known, verdict, now))
            return self._refuse(known, refusing, verdict, sent_at)

    def _refusing(self) -> _Refusing | None:
        """What refuses new risk now; call under the lock."""
        state = self._state
        if state is None or state.on:
            return _Refusing(state)
        return None

    # -- reading what an order does, outside the lock ---------------------------------------------

    def _facts(self, order: Order, refusing: _Refusing) -> tuple[Order, _Facts]:
        try:
            if order.action == MODIFY:
                return self._modify_facts(order, refusing)
            if order.action == GTT_MODIFY:
                order = self._gtt_modify_order(order)
            if order.cnc_sell or (order.action != PLACE and order.trigger_type == "OCO"):
                return order, _Facts()  # judged without the book
            return order, _Facts(position=self._position(order, refusing))
        except (readers.Unreadable, ValueError) as exc:
            logger.warning(f"The risk check could not read what an order does: {exc}")
            return order, _Facts(unreadable=True)

    def _position(self, order: Order, refusing: _Refusing) -> int:
        """p for the order's position, from the broker's own book."""
        if order.pipe == SANDBOX:
            return self._read.sandbox_position(
                order.owner, order.symbol, order.exchange, order.product
            )
        instrument = self._read.instrument_token(order.symbol, order.exchange)
        if instrument is None:
            return 0  # no instrument: nothing can be held in it, and the broker sends nothing
        product = rules.position_product(LIVE, order.product)
        rows = self._read.live_cached_rows(order.owner)
        if rows is None and refusing.state is not None and refusing.state.on:
            recent = self._recent_rows(order.owner)
            if recent is not None and rules.upstox_position(recent, instrument, product) == 0:
                if not self._has_sends(order.key):
                    return 0
        if rows is None:
            rows = self._read.live_rows(order.owner)
        with self._lock:
            self._rows = (order.owner, time.monotonic(), rows)
        return rules.upstox_position(rows, instrument, product)

    def _recent_rows(self, owner: str) -> list | None:
        with self._lock:
            kept = self._rows
        if kept is None or kept[0] != owner or time.monotonic() - kept[1] > BOOK_REUSE_S:
            return None
        return kept[2]

    def _has_sends(self, key: tuple) -> bool:
        with self._lock:
            return any(send.order.key == key for send in self._sends.values())

    def _modify_facts(self, order: Order, refusing: _Refusing) -> tuple[Order, _Facts]:
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
        if order.pipe == LIVE:
            rows = self._read.live_cached_rows(order.owner)
            if rows is None:
                rows = self._read.live_rows(order.owner)
            position = rules.upstox_position(rows, working.instrument, working.product)
        else:
            position = self._read.sandbox_position(
                order.owner, known.symbol, known.exchange, known.product
            )
        return known, _Facts(position=position, working=working.order)

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

    def _unreadable(self, order: Order, refusing: _Refusing) -> Decision:
        since = None if refusing.state is None else _hhmm(refusing.state.since)
        if order.action in (MODIFY, GTT_MODIFY):
            return Decision(True, words.order_not_found(order.pipe), GUARD_UNAVAILABLE)
        return Decision(True, words.book_unreadable(order.pipe, since), GUARD_UNAVAILABLE)

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
    )


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
