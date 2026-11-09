"""The kill switch: new risk refused everywhere at once, then cleared up (M5.13, section 3).

Turning it on (``activate``):

1. Flip it under the gate's lock (gate.Gate.turn_on): from that moment no order adding risk is
   let go, in Live or the sandbox, and the sends still waiting for their answer are known. A
   second activation answers that it is already on (or still being turned on).
2. Save it (before any broker call: after a restart it is still on) and tell the owner.
3. Wait up to ``DRAIN_S`` for those sends to be answered, so an entry that was already on its
   way is cancelled below rather than left working.
4. Live (stage ``on`` only): cancel every open order and every GTT, the protective ones too
   (amendment R1), and, when asked, Exit all through close_position_with_auth and its one claim
   (sudarshan/books/exit_guard.py), the same path every Exit all takes. A second cancel sweep
   follows when a send was still unanswered or a cancel was not confirmed.
5. The sandbox: the same, through the sandbox's own services.
6. Revoke remote MCP access when it is enabled, and turn the agent's own switch on, noting
   whether this activation did (amendment R7).
7. Save the report and tell the owner what each step did.

Each step runs whatever became of the ones before it, and the switch stays on whatever fails.
Analyze mode, the order mode and Python strategies are never touched: exits and cancels keep
going where they were going, and a strategy's own stops keep working.

Resuming (``resume``) needs the confirm phrase, at least ``RESUME_HOLD_S`` since it went on, no
activation still running, and no Exit all being placed. It is never possible with an API key.
Upstox's own kill switch is never set (it needs flat positions and locks a segment for 12 hours).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from utils import real_threading
from utils.logging import get_logger

from . import flag, readers, rules, words
from . import gate as risk_gate
from . import store as risk_store
from .store import IST, KillSwitchState, utcnow

logger = get_logger(__name__)

RESUME_HOLD_S = 60.0  # amendment R3
DRAIN_S = 10.0
POLL_S = 0.05
SECOND_SWEEP_AFTER_S = 2.0
#: Exit all leaves out a position whose closing order still shows working (M6.9, order_working),
#: and Upstox's book shows a cancelled order "cancel pending", which reads as working, for a
#: moment. So the kill switch's Exit all first waits, at most this long, for the orders it
#: cancelled to show as ended; then it goes regardless, and Exit all says which it left out.
CANCELS_SHOW_WITHIN_S = 5.0
CANCELS_POLL_S = 0.5
#: Exit all that sent nothing because Upstox did not send the orders in time is tried once more,
#: this long after (the stops are gone by then, so a position left open has nothing watching it).
EXIT_ALL_AGAIN_AFTER_S = 2.0

_running_lock = real_threading.Lock()
_running: set[str] = set()  # activations whose steps are running in this process


@dataclass(frozen=True)
class Owner:
    """Whose account the switch acts on, and who asked from where."""

    user: str  # the OpenAlgo user
    surface: str  # app, telegram or api
    actor: str  # who asked, as the audit names them
    auth_token: str | None = None  # the broker session; None while logged out
    broker: str | None = None
    api_key: str | None = None  # the sandbox's services take the API key


@dataclass(frozen=True)
class Answer:
    status: int  # as the routes answer it: 200, 400, 409, 423 or 503
    message: str
    state: KillSwitchState | None = None
    activated: bool = False  # this call turned it on (not "already on"), so its steps ran


def phrase_matches(given: object, expected: str) -> bool:
    """The confirm phrase, case and surrounding space aside (amendment R3)."""
    return (
        bool(expected.strip())
        and str(given or "").strip().casefold() == expected.strip().casefold()
    )


def _clock(at: datetime | None) -> str:
    return "" if at is None else at.astimezone(IST).strftime("%H:%M")


# -- turning it on ---------------------------------------------------------------------------------


def activate(owner: Owner, *, exit_all: bool, phrase: object = None, phrase_needed: bool = True):
    """Turn the kill switch on, then clear up; returns an ``Answer`` and the report in its state."""
    if flag.stage() == flag.OFF:
        return Answer(503, words.KS_NOT_HERE)
    settings = _settings()
    if phrase_needed:
        # Unreadable settings: the default phrase applies, so it can always be turned on.
        expected = (
            settings.confirm_phrase
            if settings is not None
            else risk_store.RiskSettings().confirm_phrase
        )
        if not phrase_matches(phrase, expected):
            return Answer(400, words.PHRASE_WRONG)
    recover()
    activation_id = "ks-" + uuid.uuid4().hex[:12]
    state = KillSwitchState(
        on=True,
        since=utcnow(),
        surface=owner.surface,
        actor=owner.actor,
        activation_id=activation_id,
        exit_all=exit_all,
        running=True,
    )
    with _running_lock:
        _running.add(activation_id)
    try:
        outcome, current, in_flight = risk_gate.GATE.turn_on(state)
        if outcome == "running":
            return Answer(409, words.KS_BEING_TURNED_ON, current)
        if outcome == "already":
            return Answer(
                200,
                words.ks_already_on(_clock(current.since), exit_all_asked=exit_all),
                current,
            )
        final = _clear_up(owner, state, in_flight, settings)
        return Answer(200, words.ks_report_lead(final.report), final, activated=True)
    finally:
        with _running_lock:
            _running.discard(activation_id)


class _Steps:
    """Each step's outcome, in order; a step that raises is recorded and the next one runs."""

    def __init__(self) -> None:
        self.done: list[dict] = []

    def run(self, name: str, step: Callable[[], tuple[bool, str, dict]]) -> dict:
        try:
            ok, said, detail = step()
        except Exception:
            logger.exception(f"Kill switch step {name} broke")
            ok, said, detail = False, words.ks_step_broke(name), {}
        entry = {"step": name, "ok": bool(ok), "words": said}
        if detail:
            entry["detail"] = detail
        self.done.append(entry)
        return entry

    def note(self, name: str, said: str) -> None:
        self.done.append({"step": name, "ok": False, "words": said})


@dataclass
class _Cancels:
    """What the switch's passes over Upstox's Live orders have done so far, carried from the first
    look to the sweep and on to Exit all: a later step's words never say nothing was cancelled
    after an earlier one cancelled, or may have (release review of fix/guard-on, HIGH-2)."""

    ids: list[str] = field(default_factory=list)  # orders Upstox confirmed cancelled
    stops: int = 0  # stop-loss orders among them, counted where positions could be read
    unsure: int = 0  # cancels sent whose answer did not confirm: they may have gone
    blind: bool = False  # Upstox's cancel of every open order was sent without the book
    gtts: int = 0  # GTTs cancelled
    unseen: bool = False  # an order book read failed, so ``seen`` may miss working orders

    @property
    def touched(self) -> bool:
        """Whether anything was, or may have been, cancelled."""
        return bool(self.ids or self.unsure or self.blind or self.gtts)

    @property
    def said(self) -> tuple[int, int, int, int]:
        """What the words take: cancelled, stop-loss orders among them, not confirmed, GTTs."""
        return len(self.ids), self.stops, self.unsure + int(self.blind), self.gtts


def _clear_up(
    owner: Owner, state: KillSwitchState, in_flight: list[int], settings
) -> KillSwitchState:
    steps = _Steps()
    steps.run("saved", lambda: _save(state))
    steps.run("told", lambda: _tell(owner, words.ks_turned_on(_clock(state.since), owner.surface)))
    drained = steps.run("drain", lambda: _drain(in_flight))
    if flag.stage() == flag.ON:
        if owner.auth_token and owner.broker:
            seen: dict[str, dict] = {}  # every order row read, by order id
            cancels = _Cancels()
            if state.exit_all:
                cancels.unseen = not _note_rows(owner, seen)  # the orders before the cancel
            cancelled = steps.run(
                "live_orders",
                lambda: _cancel_live_orders(owner, seen, exit_all=state.exit_all, cancels=cancels),
            )
            again = None
            if not drained["ok"] or not cancelled["ok"]:
                # Before Exit all, never after it: a look after it would cancel the exit it sent.
                again = steps.run(
                    "live_orders_again",
                    lambda: _sweep_again(owner, seen, state.exit_all, cancels),
                )
            latest = again if again is not None else cancelled
            no_exit = state.exit_all and bool((latest.get("detail") or {}).get("no_cancel"))
            if no_exit and not cancels.touched:
                # Nothing was cancelled, so a GTT stop keeps protecting its position too (X5): a
                # single-leg GTT cancelled here would leave the position with no stop and, with
                # the book unreadable, no exit.
                steps.note("live_gtts", words.KS_GTTS_KEPT_UNREAD)
            else:
                gtts = steps.run("live_gtts", lambda: _cancel_live_gtts(owner))
                cancels.gtts += int((gtts.get("detail") or {}).get("cancelled") or 0)
            if no_exit:
                # The order book could not be read, so no more was cancelled (X5: a cancel
                # without it takes the stop losses, and an exit cannot be sized without it).
                # Closing orders may still be working: a market exit beside one could close the
                # position twice, so none is sent. The words say what went before, if anything.
                steps.note("live_exit_all", words.ks_no_exit_orders_unread(*cancels.said))
            elif state.exit_all:
                steps.run(
                    "live_exit_all", lambda: _exit_all_live(owner, list(cancels.ids), seen, cancels)
                )
        else:
            steps.note("live", words.KS_LIVE_NOT_REACHED)
    if owner.api_key:
        steps.run("sandbox_orders", lambda: _cancel_sandbox_orders(owner))
        steps.run("sandbox_gtts", lambda: _cancel_sandbox_gtts(owner))
        if state.exit_all:
            steps.run("sandbox_exit_all", lambda: _exit_all_sandbox(owner))
    else:
        steps.note("sandbox", words.KS_SANDBOX_NO_KEY)
    agent: dict = {}
    if flag.stage() == flag.ON:
        # Both reach tools that trade Live, so the sandbox stage leaves them alone, as its
        # words say (release review A, L2).
        if (settings is None or settings.revoke_mcp) and _mcp_enabled():
            steps.run("mcp", _revoke_mcp)  # with MCP off there is nothing to say about it
        agent = steps.run("agent", _engage_agent)
    report = {
        "activation_id": state.activation_id,
        "steps": steps.done,
        "finished": utcnow().isoformat(),
    }
    final = replace(
        state,
        running=False,
        report_json=json.dumps(report),
        agent_engaged_by_us=bool(agent.get("detail", {}).get("engaged_by_us")),
    )
    risk_gate.GATE.set_state(final)
    try:
        _save(final)
        risk_store.STORE.audit(
            "ks_on",
            words.ks_report_lead(report),
            surface=owner.surface,
            actor=owner.actor,
            activation_id=state.activation_id,
            detail=report,
            at=state.since,  # the moment it went on, the one the card and the banner show
        )
    except Exception:
        logger.exception("The kill switch's report could not be saved")
    if owner.surface != "telegram":  # a Telegram activation's reply is this report
        _tell(owner, words.ks_report_message(report))
    return final


def _save(state: KillSwitchState) -> tuple[bool, str, dict]:
    risk_store.STORE.save_state(
        on=state.on,
        since=state.since,
        surface=state.surface,
        actor=state.actor,
        activation_id=state.activation_id,
        exit_all=state.exit_all,
        running=state.running,
        report_json=state.report_json,
        agent_engaged_by_us=state.agent_engaged_by_us,
    )
    _announce(state)
    return True, words.KS_SAVED, {}


def _drain(tokens: list[int]) -> tuple[bool, str, dict]:
    """Wait for the sends that were on their way when it went on; a cooperative wait.

    A send let through before the switch went on that is still waiting for its turn at Upstox
    is stopped there (gate.Gate.send_refusal), so it ends the wait at once and is counted."""
    deadline = time.monotonic() + DRAIN_S
    waiting = risk_gate.GATE.unanswered(tokens)
    while waiting and time.monotonic() < deadline:
        time.sleep(POLL_S)
        waiting = risk_gate.GATE.unanswered(tokens)
    stopped = len(risk_gate.GATE.withdrawn(tokens))
    return (
        not waiting,
        words.ks_drained(len(tokens), len(waiting), stopped),
        {"sent": len(tokens), "waiting": len(waiting), "stopped": stopped},
    )


def _note_rows(owner: Owner, seen: dict[str, dict]) -> bool:
    """Remember the order book's rows (one read, cut off after ``readers.READ_LIMIT_S``): the
    cancel below leaves some orders out of the book, and Exit all needs to know which positions
    the orders cancelled were closing. Returns whether the book was read."""
    try:
        for row in readers.live_order_rows(owner.auth_token):
            seen[str(row.get("order_id"))] = row
    except readers.Unreadable:
        logger.warning("The kill switch could not read the order book before cancelling")
        return False
    return True


def _stops_cancelled(owner: Owner, ids: list[str], seen: dict[str, dict]) -> dict:
    """The stop orders among the orders just cancelled that were working against a held
    position (amendment R1), counted from the order book read after the cancel."""
    try:
        rows = readers.live_rows(owner.auth_token)
    except readers.Unreadable:
        logger.exception("The kill switch could not count the stop orders it cancelled")
        return {}
    held = _held_by_key(rows)
    working = [
        (
            (str(row.get("instrument_token")), str(row.get("product") or "").upper()),
            rules.side_of(row.get("transaction_type")),
            str(row.get("order_type") or "").upper(),
        )
        for row in (seen.get(orderid) for orderid in ids)
        if row
    ]
    stops, protected = _stops(working, held)
    return {"stops": stops, "protected": protected}


def _held_by_key(rows: list) -> dict:
    held: dict = {}
    for row in rows:
        if isinstance(row, dict) and int(float(row.get("quantity") or 0)):
            key = (str(row.get("instrument_token")), str(row.get("product") or "").upper())
            held[key] = held.get(key, 0) + int(float(row.get("quantity") or 0))
    return held


def _cancel_the_rest(owner: Owner, ids: list[str], seen: dict[str, dict]):
    """After Cancel all: Upstox's Cancel all takes only the orders it shows as open or trigger
    pending, so every other order not final (an after-market one, one being validated) is
    cancelled by its id. Returns ``(cancelled, failed, not_final)`` order ids, or ``None``
    when the order book could not be read to look."""
    from broker.upstox.mapping.order_data import normalize_order_status
    from services.cancel_order_service import cancel_order_with_auth

    try:
        rows = readers.live_order_rows(owner.auth_token)
    except readers.Unreadable:
        logger.exception("The kill switch could not read the order book after Cancel all")
        return None
    for row in rows:
        seen[str(row.get("order_id"))] = row
    not_final = {
        str(row.get("order_id"))
        for row in rows
        if normalize_order_status(row.get("status")) not in readers.FINAL_STATUSES
    }
    cancelled, failed = [], []
    for row in rows:
        orderid = str(row.get("order_id") or "")
        if not orderid or orderid in ids or orderid not in not_final:
            continue
        done, _body, _code = cancel_order_with_auth(
            orderid, owner.auth_token, owner.broker, {}, force_live=True
        )
        (cancelled if done else failed).append(orderid)
    return cancelled, failed, not_final


def _cancel_live_orders(
    owner: Owner,
    seen: dict[str, dict],
    exit_all: bool = False,
    cancels: _Cancels | None = None,
) -> tuple[bool, str, dict]:
    """Cancel every open Live order: Upstox's Cancel all, then the rest by id from the order
    book. ``exit_all``: Exit all follows, sized from that same order book, so when the book
    cannot be read nothing is cancelled blind (release review v0.4.1, X5): Upstox's cancel of
    every open order would take the stop losses and Exit all would then send nothing, leaving
    each position held with no stop and no exit. ``cancels`` carries what this and earlier passes
    did, so the words of a later pass never deny an earlier cancel."""
    from services.cancel_all_order_service import cancel_all_orders_with_auth

    cancels = cancels if cancels is not None else _Cancels()
    before_this_pass = cancels.touched
    with readers.limited():  # the order book read inside is cut off, never the 120 s client's
        ok, body, status = cancel_all_orders_with_auth(
            {}, owner.auth_token, owner.broker, {}, force_live=True
        )
    body = body if isinstance(body, dict) else {}
    ids = [str(orderid) for orderid in body.get("canceled_orders") or []]
    refused = [str(orderid) for orderid in body.get("failed_cancellations") or []]
    unsure = [str(orderid) for orderid in body.get("not_confirmed_cancellations") or []]
    rest = _cancel_the_rest(owner, ids, seen)
    still: list[str] = []
    if rest is not None:
        ids += rest[0]
        # A cancel Upstox refused counts only while the order still shows as working.
        still = [i for i in refused if i not in ids and i in rest[2]]
        still += [i for i in rest[1] if i not in still]
    else:
        still = [i for i in refused if i not in ids]
    detail = {"cancelled": len(ids), "failed": len(still), "status": status}
    before: dict = {}
    cancels.ids += [i for i in ids if i not in cancels.ids]
    cancels.unsure += len([i for i in unsure if i not in ids])
    if rest is None:
        cancels.unseen = True
    if ids:
        detail["cancelled_ids"] = ids
        # The stop-loss orders among them, for the words after Exit all: counted on every pass
        # that cancelled, not only one that could check the book after.
        before = _stops_cancelled(owner, ids, seen)
        detail["stops_cancelled"] = int(before.get("stops") or 0)
        cancels.stops += detail["stops_cancelled"]
    if rest is None:
        detail["checked"] = False
        if not ids and exit_all and not refused and not before_this_pass:
            # Exit all cannot be sized without the order book, so a cancel without it would
            # leave every position with its stop loss gone and no exit sent: the stops stay,
            # and the sweep a moment later looks for the book again. Nothing at all has been
            # sent to Upstox, by this pass or an earlier one, so the words may say so.
            detail["no_cancel"] = True
            detail["kept_for_exit_all"] = True
            return False, words.KS_ORDERS_KEPT_UNREAD, detail
        if not ids and exit_all and not refused:
            # The same, after a pass that cancelled, or may have: no more is cancelled, and the
            # words name what went before, for a position may be open with its stop gone.
            detail["no_cancel"] = True
            detail["after_cancels"] = True
            return False, words.ks_orders_unread_after_cancels(*cancels.said), detail
        if not ids and not exit_all:
            # The order book cannot be read, so no order could be cancelled one by one: Upstox's
            # own cancel of every open order needs no book. Without it an order closing a
            # position may still be working, and Exit all would close it twice.
            gone = _cancel_without_book(owner)
            cancels.blind = True
            cancels.ids += [i for i in gone["ids"] if i not in cancels.ids]
            if gone["not_confirmed"]:
                cancels.unsure += 1
            detail.update({k: gone[k] for k in ("done", "total") if gone[k] is not None})
            if gone["ids"]:
                # The orders Upstox says it cancelled: Exit all waits for them to show ended,
                # as it does after the sweep.
                detail["cancelled_ids"] = gone["ids"]
            if gone["cancelled"]:
                detail["without_book"] = True
                if gone.get("nothing_open"):
                    detail["nothing_open"] = True
                    return True, words.KS_NOTHING_TO_CANCEL_UNREAD, detail
                return True, words.KS_ORDERS_CANCELLED_UNREAD, detail
            detail["no_cancel"] = True
            return (
                False,
                words.ks_orders_not_cancelled_unread(
                    gone["done"], gone["total"], gone["not_confirmed"]
                ),
                detail,
            )
    if still:
        if all(i in unsure for i in still):
            # Anything that may have reached the broker reads "not confirmed", never refused.
            return False, words.ks_cancels_not_confirmed(len(ids), len(still)), detail
        return False, words.ks_orders_left(len(ids), len(still)), detail
    if rest is None:
        if ok and not refused:
            return False, words.ks_orders_unchecked(len(ids)), detail
        return False, str(body.get("message") or words.KS_ORDERS_NOT_CONFIRMED), detail
    return True, words.ks_orders_cancelled(len(ids), before), detail


def _cancel_without_book(owner: Owner) -> dict:
    """Upstox's cancel of every open order, with no read of the order book: what it answered
    (``order_api.cancel_every_open_order_detail``). A cancel that broke here may have gone."""
    try:
        from broker.upstox.api.order_api import cancel_every_open_order_detail

        return cancel_every_open_order_detail(owner.auth_token)
    except Exception:
        logger.exception("The kill switch could not cancel every open order without the book")
        return {
            "cancelled": False,
            "not_confirmed": True,
            "ids": [],
            "done": None,
            "total": None,
            "nothing_open": False,
        }


def _cancel_live_gtts(owner: Owner) -> tuple[bool, str, dict]:
    from services.cancel_gtt_order_service import cancel_gtt_order_with_auth
    from services.gtt_orderbook_service import get_gtt_orderbook_with_auth

    ok, body, _status = get_gtt_orderbook_with_auth(owner.auth_token, owner.broker, None)
    if not ok:
        return False, words.KS_GTTS_NOT_READ, {}
    triggers = [g.get("trigger_id") for g in body.get("data") or [] if g.get("trigger_id")]
    failed = []
    for trigger_id in triggers:
        done, _answer, _code = cancel_gtt_order_with_auth(
            str(trigger_id), owner.auth_token, owner.broker, {}, force_live=True
        )
        if not done:
            failed.append(str(trigger_id))
    cancelled = len(triggers) - len(failed)
    return (
        not failed,
        words.ks_gtts_cancelled(cancelled, len(failed)),
        {"cancelled": cancelled, "failed": failed},
    )


def _cancels_shown(owner: Owner, cancelled: list[str], seen: dict[str, dict]) -> set[str]:
    """Wait, at most ``CANCELS_SHOW_WITHIN_S``, until no order the switch cancelled shows as
    working in Upstox's order book; a cooperative wait, and only for those orders. Each read is
    cut off after ``readers.READ_LIMIT_S``. Returns the cancelled orders seen as ended."""
    if not cancelled:
        return set()
    from broker.upstox.mapping.order_data import normalize_order_status

    waiting = {str(orderid) for orderid in cancelled}
    ended: set[str] = set()
    deadline = time.monotonic() + CANCELS_SHOW_WITHIN_S
    while True:
        try:
            rows = readers.live_order_rows(owner.auth_token)
        except readers.Unreadable:  # a read refused or failed: looked at again until time is up
            logger.exception("The kill switch could not read the order book after cancelling")
            rows = None
        if rows is not None:
            for row in rows:
                seen[str(row.get("order_id"))] = row
            working = {
                str(row.get("order_id"))
                for row in rows
                if normalize_order_status(row.get("status")) not in readers.FINAL_STATUSES
            }
            ended = waiting - working
            if not waiting & working:
                return ended
        if time.monotonic() >= deadline:
            return ended
        time.sleep(CANCELS_POLL_S)


def _exit_all_live(
    owner: Owner,
    cancelled: list[str],
    seen: dict[str, dict],
    cancels: _Cancels | None = None,
) -> tuple[bool, str, dict]:
    """Exit all through the one claim every Exit all takes (round nine's path, never a second),
    once the orders just cancelled show as ended, so it leaves no position out for them.

    ``cancels``: what the cancel steps before it did. An Exit all that sent nothing because
    Upstox did not send the orders in time is tried once more after ``EXIT_ALL_AGAIN_AFTER_S``;
    a position it still leaves open with nothing watching it has its stop gone, or may have, and
    the step says so and points to the Upstox app (release review v0.4.1, X5)."""
    cancels = cancels if cancels is not None else _Cancels(ids=list(cancelled))
    ended = _cancels_shown(owner, cancelled, seen)
    shown = not cancelled or ended == {str(orderid) for orderid in cancelled}
    ok, body, status = _close_all(owner)
    tried_again = False
    if _nothing_sent_for_want_of_the_orders(body):
        time.sleep(EXIT_ALL_AGAIN_AFTER_S)
        ok, body, status = _close_all(owner)
        tried_again = True
    detail: dict[str, Any] = {"status": status, "cancels_shown": shown}
    if tried_again:
        detail["tried_again"] = True
    each = body.get("positions")
    unwatched: list[dict] = []
    if isinstance(each, list):
        # What became of each position (sent, not_confirmed, not_sent with its reason): the
        # phone settles its holds on it as after its own Exit all.
        detail["positions"] = each
        outcomes = [str(item.get("outcome")) for item in each if isinstance(item, dict)]
        detail["counts"] = {name: outcomes.count(name) for name in sorted(set(outcomes))}
        unwatched = _left_with_nothing_watching(each)
    if status == 409 and not isinstance(each, list):
        # Another Exit all holds the book; one that left positions out has its own words.
        return False, words.KS_EXIT_ALL_BUSY, detail
    said = words.ks_exit_all(str(body.get("message") or ""), tried_again)
    known, maybe = _stops_gone(unwatched, cancels, seen)
    if known or maybe:
        said += " " + words.ks_left_without_stops(known, maybe)
        detail["left_without_stops"] = known + maybe
    return bool(ok), said, detail


def _close_all(owner: Owner) -> tuple[bool, dict, int]:
    from services.close_position_service import close_position_with_auth

    # close_all_positions hands its one order book read to the gate for its own sends.
    ok, body, status = close_position_with_auth(
        {}, owner.auth_token, owner.broker, {}, force_live=True
    )
    return ok, body if isinstance(body, dict) else {}, status


def _nothing_sent_for_want_of_the_orders(body: dict) -> bool:
    """Exit all sent no exit, and every position it left out was left out because Upstox did not
    send the order list in time (the sentence sudarshan.books.exit_guard writes for that)."""
    from sudarshan.books import exit_guard

    each = body.get("positions")
    if not isinstance(each, list) or not each:
        return False
    for item in each:
        if not isinstance(item, dict) or item.get("outcome") != "not_sent":
            return False
        position = (item.get("exchange"), item.get("symbol"), item.get("product"))
        if item.get("reason") != exit_guard.exit_not_sized(position, False):
            return False
    return True


def _left_with_nothing_watching(each: list) -> list[dict]:
    """The positions Exit all left with nothing sent and no closing order known to be working:
    not sized (the order list did not answer, or does not add up), not reached in its time, or
    not listed by Upstox's positions and not sent for (exit_guard.left_unlisted: its size could
    not be worked out, or it may be a conversion). A position left out for an order working on
    its closing side, a hold or a claim has that order; these have nothing, and their stops may
    be the ones just cancelled."""
    from broker.upstox.api import order_api
    from sudarshan.books import exit_guard

    late_words = getattr(order_api, "_late_words", None)
    left = []
    for item in each:
        if not isinstance(item, dict) or item.get("outcome") != "not_sent":
            continue
        position = (item.get("exchange"), item.get("symbol"), item.get("product"))
        reasons = {
            exit_guard.exit_not_sized(position, False),
            exit_guard.exit_not_sized(position, True),
        }
        if late_words is not None:
            reasons.add(late_words([exit_guard.trader_name(position)]))
        if item.get("reason") in reasons or exit_guard.left_unlisted(position, item.get("reason")):
            left.append(item)
    return left


_STOP_TYPES = ("SL", "SL-M", "SL-L", "STOPLOSS", "STOPLOSS_MARKET", "STOPLOSS_LIMIT")


def _stops_gone(unwatched: list[dict], cancels: _Cancels, seen: dict[str, dict]) -> tuple[int, int]:
    """Of the positions left with nothing watching them: how many had a stop-loss order the
    switch cancelled (a cancelled order of the stop kind on that instrument and product in the
    rows read), and how many may have had one (a cancel not confirmed, a cancel without the
    book, a GTT cancelled, or rows the switch never saw). ``(known, maybe)``."""
    if not unwatched:
        return 0, 0
    gone: set[tuple[str, str]] = set()
    other: set[tuple[str, str]] = set()
    for orderid in cancels.ids:
        row = seen.get(str(orderid))
        if not row:
            continue
        key = (str(row.get("instrument_token") or ""), str(row.get("product") or "").upper())
        kind = str(row.get("order_type") or "").upper().replace("_", "-")
        (gone if kind in _STOP_TYPES else other).add(key)
    unsure = bool(cancels.unsure or cancels.blind or cancels.gtts or cancels.unseen)
    known = maybe = 0
    for item in unwatched:
        try:
            token = readers.instrument_token(item.get("symbol"), item.get("exchange"))
        except readers.Unreadable:
            token = None
        key = (str(token or ""), rules.position_product(risk_gate.LIVE, item.get("product")))
        if token and key in gone:
            known += 1
        elif unsure or (token and key in other):
            maybe += 1
    return known, maybe


def _sweep_again(
    owner: Owner, seen: dict[str, dict], exit_all: bool = False, cancels: _Cancels | None = None
) -> tuple[bool, str, dict]:
    """A send still on its way, or a cancel not confirmed: look again after a moment."""
    time.sleep(SECOND_SWEEP_AFTER_S)
    ok, said, detail = _cancel_live_orders(owner, seen, exit_all=exit_all, cancels=cancels)
    return ok, words.ks_swept_again(said), detail


def _cancel_sandbox_orders(owner: Owner) -> tuple[bool, str, dict]:
    """Cancel every open sandbox order. The report names the orders it cancelled, as the Live
    step does: the phone says "Cancelled by the kill switch." for those orders only."""
    from services.sandbox_service import sandbox_cancel_all_orders

    ok, body, status = sandbox_cancel_all_orders({}, owner.api_key, {})
    ids = [str(orderid) for orderid in (body or {}).get("canceled_orders") or [] if orderid]
    count = int((body or {}).get("canceled_count") or len(ids))
    detail: dict[str, Any] = {"cancelled": count, "status": status}
    if ids:
        detail["cancelled_ids"] = ids
    return bool(ok), words.ks_sandbox_orders(count, ok), detail


def _cancel_sandbox_gtts(owner: Owner) -> tuple[bool, str, dict]:
    from services.sandbox_service import sandbox_cancel_gtt_order, sandbox_gtt_orderbook

    ok, body, _status = sandbox_gtt_orderbook(owner.api_key)
    if not ok:
        return False, words.KS_SANDBOX_GTTS_NOT_READ, {}
    triggers = [g.get("trigger_id") for g in body.get("data") or [] if g.get("trigger_id")]
    failed = [t for t in triggers if not sandbox_cancel_gtt_order(str(t), owner.api_key)[0]]
    cancelled = len(triggers) - len(failed)
    return not failed, words.ks_sandbox_gtts(cancelled, len(failed)), {"cancelled": cancelled}


def _exit_all_sandbox(owner: Owner) -> tuple[bool, str, dict]:
    from services.sandbox_service import sandbox_close_position

    ok, body, status = sandbox_close_position({}, owner.api_key, {})
    body = body or {}
    return (
        bool(ok),
        words.ks_sandbox_exit_all(
            str(body.get("message") or ""),
            body.get("closed_positions"),
            body.get("failed_closures"),
        ),
        {"status": status},
    )


def _mcp_enabled() -> bool:
    return os.getenv("MCP_HTTP_ENABLED", "False").lower() in ("true", "1", "t")


def _revoke_mcp() -> tuple[bool, str, dict]:
    if not _mcp_enabled():
        return True, words.KS_MCP_OFF, {}
    from database.oauth_db import revoke_all_tokens

    count = revoke_all_tokens("sudarshan_kill_switch")
    return True, words.ks_mcp_revoked(count), {"revoked": count}


def _engage_agent() -> tuple[bool, str, dict]:
    from services.agent import settings as agent_settings

    if agent_settings.is_kill_switch_engaged():
        return True, words.KS_AGENT_ALREADY_STOPPED, {"engaged_by_us": False}
    agent_settings.set_kill_switch(True)
    return True, words.KS_AGENT_STOPPED, {"engaged_by_us": True}


# -- who it acts for -------------------------------------------------------------------------------


def owner_for(
    username: str,
    *,
    surface: str,
    actor: str,
    broker: str | None = None,
    api_key: str | None = None,
) -> Owner:
    """The owner's broker session and API key, read the way the app's own routes read them."""
    from database.auth_db import get_api_key_for_tradingview, get_auth_token, get_auth_token_broker

    key = api_key or get_api_key_for_tradingview(username)
    if not broker and key:
        _token, broker = get_auth_token_broker(key)
    return Owner(
        user=username,
        surface=surface,
        actor=actor,
        auth_token=get_auth_token(username),
        broker=broker,
        api_key=key,
    )


# -- before it goes on: what it will cancel (amendment R1) ----------------------------------------


def preview(owner: Owner) -> dict | None:
    """What turning it on would cancel: the Live account's when the guard covers Live, else the
    sandbox's. None when neither can be read."""
    if flag.stage() == flag.ON and owner.auth_token and owner.broker:
        return preview_live(owner)
    if owner.api_key:
        return preview_sandbox(owner)
    return None


def _stops(working: list[tuple[tuple, int | None, str]], held: dict) -> tuple[int, int]:
    """Stop orders working against a held position, and how many positions they protect."""
    stops, protected = 0, set()
    for key, side, price_type in working:
        position = held.get(key, 0)
        if price_type in ("SL", "SL-M") and position and side and (side > 0) != (position > 0):
            stops += 1
            protected.add(key)
    return stops, len(protected)


def preview_sandbox(owner: Owner) -> dict:
    """The same counts from the sandbox's own books."""
    from services.sandbox_service import (
        sandbox_get_orderbook,
        sandbox_get_positions,
        sandbox_gtt_orderbook,
    )

    ok, body, _code = sandbox_get_orderbook(owner.api_key, {})
    data = body.get("data") if ok and isinstance(body, dict) else None
    orders = data.get("orders") if isinstance(data, dict) else []
    ok, body, _code = sandbox_get_positions(owner.api_key, {})
    held = {}
    for row in (body.get("data") if ok and isinstance(body, dict) else None) or []:
        if isinstance(row, dict) and row.get("quantity"):
            key = (row.get("symbol"), row.get("exchange"), row.get("product"))
            held[key] = int(row["quantity"])
    working = [
        (
            (order.get("symbol"), order.get("exchange"), order.get("product")),
            rules.side_of(order.get("action")),
            str(order.get("pricetype") or "").upper(),
        )
        for order in orders or []
        if isinstance(order, dict)
        and str(order.get("order_status") or "").lower() in ("open", "trigger pending")
    ]
    stops, protected = _stops(working, held)
    ok, body, _code = sandbox_gtt_orderbook(owner.api_key)
    triggers = (
        [g for g in (body.get("data") or []) if isinstance(g, dict)]
        if ok and isinstance(body, dict)
        else None
    )
    counted = {
        "orders": len(working),
        "gtts": None if triggers is None else len(triggers),
        "stops": stops,
        "protected": protected,
        "positions": len(held),
    }
    closing = {}  # a single GTT on the closing side of a held position is a stop too
    for gtt in triggers or []:
        key = (gtt.get("symbol"), gtt.get("exchange"), gtt.get("product"))
        side = rules.side_of(gtt.get("action"))
        if str(gtt.get("trigger_type") or "").upper() == "SINGLE" and held.get(key) and side:
            if (side > 0) != (held[key] > 0):
                closing[key] = closing.get(key, 0) + 1
    if closing:
        counted["gtt_stops"] = sum(closing.values())
        counted["protected"] = len(set(_protected_keys(working, held)) | set(closing))
    return counted


def preview_live(owner: Owner, *, gtts: bool = True) -> dict:
    """Counted fresh from Upstox: open orders, GTTs, and stop orders protecting held positions
    (a single GTT on the closing side of a held position is a stop too)."""
    from broker.upstox.mapping.order_data import normalize_order_status

    rows = readers.live_rows(owner.auth_token)
    held = _held_by_key(rows)
    working = [
        (
            (str(order.get("instrument_token")), str(order.get("product") or "").upper()),
            rules.side_of(order.get("transaction_type")),
            str(order.get("order_type") or "").upper(),
        )
        for order in readers.live_order_rows(owner.auth_token)
        if normalize_order_status(order.get("status")) in ("open", "trigger pending")
    ]
    stops, protected = _stops(working, held)
    counted = {
        "orders": len(working),
        "stops": stops,
        "protected": protected,
        "positions": len(held),
    }
    if gtts:
        from services.gtt_orderbook_service import get_gtt_orderbook_with_auth

        with readers.limited():
            ok, body, _code = get_gtt_orderbook_with_auth(owner.auth_token, owner.broker, None)
        triggers = [g for g in (body.get("data") or []) if isinstance(g, dict)] if ok else None
        counted["gtts"] = None if triggers is None else len(triggers)
        if triggers:
            gtt_stops = _gtt_stops(rows, triggers)
            if gtt_stops[0]:
                counted["gtt_stops"] = len(gtt_stops[0])
                counted["protected"] = len(set(_protected_keys(working, held)) | gtt_stops[1])
    return counted


def _protected_keys(working: list, held: dict) -> list:
    """The positions a working stop order protects."""
    return [
        key
        for key, side, price_type in working
        if price_type in ("SL", "SL-M")
        and held.get(key, 0)
        and side
        and (side > 0) != (held[key] > 0)
    ]


def _gtt_stops(rows: list, triggers: list[dict]) -> tuple[list[dict], set]:
    """The single GTTs that close a held position, and the positions they protect (an OCO
    opens a position at Upstox, so it protects none)."""
    from broker.upstox.mapping.transform_data import reverse_map_product_type

    order_api = readers._upstox()
    by_name: dict[tuple, tuple[tuple, int]] = {}
    for row in rows:
        quantity = int(float(row.get("quantity") or 0)) if isinstance(row, dict) else 0
        if not quantity:
            continue
        exchange = row.get("exchange")
        symbol = order_api.get_symbol(row.get("instrument_token"), exchange)
        if symbol:
            name = (str(symbol), str(exchange), reverse_map_product_type(exchange, row["product"]))
            key = (str(row.get("instrument_token")), str(row.get("product") or "").upper())
            by_name[name] = (key, quantity)
    stops, protected = [], set()
    for gtt in triggers:
        name = (str(gtt.get("symbol")), str(gtt.get("exchange")), str(gtt.get("product")))
        side = rules.side_of(gtt.get("action"))
        if str(gtt.get("trigger_type") or "").upper() != "SINGLE" or name not in by_name:
            continue
        key, quantity = by_name[name]
        if side and (side > 0) != (quantity > 0):
            stops.append(gtt)
            protected.add(key)
    return stops, protected


# -- resuming --------------------------------------------------------------------------------------


def resume(owner: Owner, *, phrase: object) -> Answer:
    """Turn the kill switch off, for the owner only, with the phrase, after the hold."""
    settings = _settings()
    if settings is None:
        return Answer(503, words.KS_SETTINGS_UNREADABLE)
    if not phrase_matches(phrase, settings.confirm_phrase):
        return _refused(owner, Answer(400, words.PHRASE_WRONG, risk_gate.GATE.state()))
    recover()
    state = risk_gate.GATE.state()
    if state is None:
        return Answer(503, words.KS_SETTINGS_UNREADABLE)
    if not state.on:
        return Answer(200, words.KS_ALREADY_OFF, state)
    if state.running or _is_running(state.activation_id):
        return Answer(409, words.KS_BEING_TURNED_ON, state)
    held = RESUME_HOLD_S - (utcnow() - state.since).total_seconds() if state.since else 0
    if held > 0:
        return _refused(owner, Answer(423, words.ks_resume_wait(int(held) + 1), state))
    from sudarshan.books import exit_guard

    if exit_guard.exit_all_held("live"):
        return Answer(409, words.KS_EXIT_ALL_STILL_PLACED, state)
    off = KillSwitchState(report_json=state.report_json)
    if not risk_gate.GATE.turn_off(state.activation_id, off):  # confirmed first
        return Answer(409, words.KS_CHANGED_MEANWHILE, risk_gate.GATE.state())
    try:
        _save(off)  # saves and tells the screens, only once the gate has turned it off
    except Exception:
        logger.exception("The kill switch could not be saved as off")
        risk_gate.GATE.undo_turn_off(off, state)  # if this fails, the switch stays on
        return Answer(503, words.KS_NOT_SAVED_OFF, state)
    if state.agent_engaged_by_us:
        try:
            from services.agent import settings as agent_settings

            agent_settings.set_kill_switch(False)
        except Exception:
            logger.exception("The agent's switch could not be released")
    try:
        risk_store.STORE.audit(
            "ks_off",
            words.KS_RESUMED,
            surface=owner.surface,
            actor=owner.actor,
            activation_id=state.activation_id,
        )
    except Exception:
        logger.exception("The kill switch's resume could not be audited")
    said = _resumed_words()
    _tell(owner, said)
    return Answer(200, said, off)


def _resumed_words() -> str:
    """Off, and whether the day's loss limit still refuses new orders (design 3.8, step 8)."""
    for pipe in (risk_gate.LIVE, risk_gate.SANDBOX):
        if flag.covers(pipe) and risk_gate.GATE.day_lock(pipe) is not None:
            return words.KS_RESUMED_LOCKED
    return words.KS_RESUMED


def _refused(owner: Owner, answer: Answer) -> Answer:
    """A resume refused for its phrase or its hold goes to the audit (one try, never fails it)."""
    state = answer.state
    try:
        risk_store.STORE.audit(
            "resume_refused",
            words.ks_resume_refused(answer.message),
            surface=owner.surface,
            actor=owner.actor,
            activation_id=None if state is None else state.activation_id,
        )
    except Exception:
        logger.exception("A refused resume could not be audited")
    return answer


# -- shared ------------------------------------------------------------------------------------------


def _settings():
    try:
        return risk_store.STORE.settings()
    except Exception:
        logger.exception("The risk settings could not be read")
        return None


def _is_running(activation_id: str | None) -> bool:
    with _running_lock:
        return activation_id in _running


def recover() -> None:
    """An activation saved as running that no step in this process runs: the app restarted."""
    state = risk_gate.GATE.state()
    if state is None or not state.running or _is_running(state.activation_id):
        return
    report = state.report or {}
    report["restarted"] = words.KS_RESTARTED
    settled = replace(state, running=False, report_json=json.dumps(report))
    if risk_gate.GATE.settle_restart(state.activation_id, settled):
        try:
            _save(settled)
        except Exception:
            logger.exception("The kill switch's restart could not be saved")


def _tell(owner: Owner, text: str) -> tuple[bool, str, dict]:
    """Queue a Telegram message to the owner (never waits for Telegram)."""
    try:
        from database.telegram_db import get_telegram_user_by_username
        from services.telegram_alert_service import alert_executor, telegram_alert_service
        from sudarshan.books.telegram_words import markdown_safe

        if not telegram_alert_service.is_bot_active():
            return False, words.KS_TELEGRAM_OFF, {}
        user = get_telegram_user_by_username(owner.user)
        if not user:
            return False, words.KS_TELEGRAM_NOT_LINKED, {}
        alert_executor.submit(
            telegram_alert_service.send_alert_sync, user["telegram_id"], markdown_safe(text)
        )
        return True, words.KS_TELEGRAM_SENT, {}
    except Exception:
        logger.exception("The kill switch could not queue its Telegram message")
        return False, words.KS_TELEGRAM_FAILED, {}


def _announce(state: KillSwitchState) -> None:
    """Tell every open screen (Socket.IO ``sudarshan_risk``); from any thread."""
    try:
        from extensions import emit_from_any_thread

        emit_from_any_thread("sudarshan_risk", public_state(state))
    except Exception:
        logger.exception("The kill switch could not tell the open screens")


def public_state(state: KillSwitchState | None) -> dict[str, Any]:
    """The kill switch as screens and SUDARSHAN's endpoints show it; never the phrase."""
    if state is None:
        return {"known": False, "on": None}
    since = state.since
    resume_at = None
    if state.on and since is not None:
        resume_at = datetime.fromtimestamp(since.timestamp() + RESUME_HOLD_S, IST).isoformat()
    return {
        "known": True,
        "on": state.on,
        "since": since.isoformat() if since else None,
        "since_clock": _clock(since),
        "surface": state.surface,
        "actor": state.actor,
        "exit_all": state.exit_all,
        "running": state.running,
        "can_resume_at": resume_at,
        "report": state.report,
    }
