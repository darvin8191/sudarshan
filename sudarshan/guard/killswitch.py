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
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from utils import real_threading
from utils.logging import get_logger

from . import flag, words
from . import gate as risk_gate
from . import store as risk_store
from .store import IST, KillSwitchState, utcnow

logger = get_logger(__name__)

RESUME_HOLD_S = 60.0  # amendment R3
DRAIN_S = 10.0
POLL_S = 0.05
SECOND_SWEEP_AFTER_S = 2.0

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


def phrase_matches(given: object, expected: str) -> bool:
    """The confirm phrase, case and surrounding space aside (amendment R3)."""
    return bool(expected) and str(given or "").strip().casefold() == expected.strip().casefold()


def _clock(at: datetime | None) -> str:
    return "" if at is None else at.astimezone(IST).strftime("%H:%M")


# -- turning it on ---------------------------------------------------------------------------------


def activate(owner: Owner, *, exit_all: bool, phrase: object = None, phrase_needed: bool = True):
    """Turn the kill switch on, then clear up; returns an ``Answer`` and the report in its state."""
    if flag.stage() == flag.OFF:
        return Answer(503, words.KS_NOT_HERE)
    settings = _settings()
    if phrase_needed:
        if settings is None:
            return Answer(503, words.KS_SETTINGS_UNREADABLE)
        if not phrase_matches(phrase, settings.confirm_phrase):
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
            return Answer(200, words.ks_already_on(_clock(current.since)), current)
        final = _clear_up(owner, state, in_flight, settings)
        return Answer(200, words.ks_report_lead(final.report), final)
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


def _clear_up(
    owner: Owner, state: KillSwitchState, in_flight: list[int], settings
) -> KillSwitchState:
    steps = _Steps()
    steps.run("saved", lambda: _save(state))
    steps.run("told", lambda: _tell(owner, words.ks_turned_on(_clock(state.since), owner.surface)))
    drained = steps.run("drain", lambda: _drain(in_flight))
    if flag.stage() == flag.ON:
        if owner.auth_token and owner.broker:
            before = _protective_stops(owner)
            cancelled = steps.run("live_orders", lambda: _cancel_live_orders(owner, before))
            steps.run("live_gtts", lambda: _cancel_live_gtts(owner))
            if state.exit_all:
                steps.run("live_exit_all", lambda: _exit_all_live(owner))
            if not drained["ok"] or not cancelled["ok"]:
                steps.run("live_orders_again", lambda: _sweep_again(owner))
        else:
            steps.note("live", words.KS_LIVE_NOT_REACHED)
    if owner.api_key:
        steps.run("sandbox_orders", lambda: _cancel_sandbox_orders(owner))
        steps.run("sandbox_gtts", lambda: _cancel_sandbox_gtts(owner))
        if state.exit_all:
            steps.run("sandbox_exit_all", lambda: _exit_all_sandbox(owner))
    else:
        steps.note("sandbox", words.KS_SANDBOX_NO_KEY)
    if settings is None or settings.revoke_mcp:
        steps.run("mcp", _revoke_mcp)
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
        )
    except Exception:
        logger.exception("The kill switch's report could not be saved")
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
    """Wait for the sends that were on their way when it went on; a cooperative wait."""
    deadline = time.monotonic() + DRAIN_S
    waiting = risk_gate.GATE.unanswered(tokens)
    while waiting and time.monotonic() < deadline:
        time.sleep(POLL_S)
        waiting = risk_gate.GATE.unanswered(tokens)
    return (
        not waiting,
        words.ks_drained(len(tokens), len(waiting)),
        {"sent": len(tokens), "waiting": len(waiting)},
    )


def _protective_stops(owner: Owner) -> dict:
    """Before the cancel: the stop orders working against a held position (amendment R1)."""
    try:
        return preview_live(owner, gtts=False)  # the GTT book is read once, to cancel them
    except Exception:
        logger.exception("The kill switch could not count the stop orders before cancelling")
        return {}


def _cancel_live_orders(owner: Owner, before: dict) -> tuple[bool, str, dict]:
    from services.cancel_all_order_service import cancel_all_orders_with_auth

    ok, body, status = cancel_all_orders_with_auth(
        {}, owner.auth_token, owner.broker, {}, force_live=True
    )
    cancelled = list(body.get("canceled_orders") or []) if isinstance(body, dict) else []
    failed = list(body.get("failed_cancellations") or []) if isinstance(body, dict) else []
    detail = {"cancelled": len(cancelled), "failed": len(failed), "status": status}
    if ok and not failed:
        return True, words.ks_orders_cancelled(len(cancelled), before), detail
    if isinstance(body, dict) and failed:
        from blueprints.orders import _cancel_all_summary

        return False, _cancel_all_summary(body)["message"], detail
    return False, str((body or {}).get("message") or words.KS_ORDERS_NOT_CONFIRMED), detail


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


def _exit_all_live(owner: Owner) -> tuple[bool, str, dict]:
    """Exit all through the one claim every Exit all takes (round nine's path, never a second)."""
    from services.close_position_service import close_position_with_auth

    ok, body, status = close_position_with_auth(
        {}, owner.auth_token, owner.broker, {}, force_live=True
    )
    message = str((body or {}).get("message") or "")
    if status == 409:
        return False, words.KS_EXIT_ALL_BUSY, {"status": status}
    return bool(ok), words.ks_exit_all(message), {"status": status}


def _sweep_again(owner: Owner) -> tuple[bool, str, dict]:
    """A send still on its way, or a cancel not confirmed: look again after a moment."""
    time.sleep(SECOND_SWEEP_AFTER_S)
    ok, said, detail = _cancel_live_orders(owner, {})
    return ok, words.ks_swept_again(said), detail


def _cancel_sandbox_orders(owner: Owner) -> tuple[bool, str, dict]:
    from services.sandbox_service import sandbox_cancel_all_orders

    ok, body, status = sandbox_cancel_all_orders({}, owner.api_key, {})
    count = int(
        (body or {}).get("canceled_count") or len((body or {}).get("canceled_orders") or [])
    )
    return bool(ok), words.ks_sandbox_orders(count, ok), {"cancelled": count, "status": status}


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
    return (
        bool(ok),
        words.ks_sandbox_exit_all(str((body or {}).get("message") or "")),
        {"status": status},
    )


def _revoke_mcp() -> tuple[bool, str, dict]:
    if os.getenv("MCP_HTTP_ENABLED", "False").lower() != "true":
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

    from . import rules

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
    gtts = len(body.get("data") or []) if ok and isinstance(body, dict) else None
    return {
        "orders": len(working),
        "gtts": gtts,
        "stops": stops,
        "protected": protected,
        "positions": len(held),
    }


def preview_live(owner: Owner, *, gtts: bool = True) -> dict:
    """Counted fresh from Upstox: open orders, GTTs, and stop orders protecting held positions."""
    from broker.upstox.api import order_api
    from broker.upstox.mapping.order_data import normalize_order_status

    from . import readers, rules

    rows = readers.live_rows(owner.auth_token)
    held = {}
    for row in rows:
        if isinstance(row, dict) and int(float(row.get("quantity") or 0)):
            key = (str(row.get("instrument_token")), str(row.get("product") or "").upper())
            held[key] = held.get(key, 0) + int(float(row.get("quantity") or 0))
    book = order_api.get_order_book(owner.auth_token)
    if not isinstance(book, dict) or book.get("status") != "success":
        raise readers.Unreadable("orders")
    working = [
        (
            (str(order.get("instrument_token")), str(order.get("product") or "").upper()),
            rules.side_of(order.get("transaction_type")),
            str(order.get("order_type") or "").upper(),
        )
        for order in book.get("data") or []
        if isinstance(order, dict)
        and normalize_order_status(order.get("status")) in ("open", "trigger pending")
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

        ok, body, _code = get_gtt_orderbook_with_auth(owner.auth_token, owner.broker, None)
        counted["gtts"] = len(body.get("data") or []) if ok else None
    return counted


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
    try:
        _save(off)  # saved first: if this fails, the switch stays on
    except Exception:
        logger.exception("The kill switch could not be saved as off")
        return Answer(503, words.KS_NOT_SAVED_OFF, state)
    if not risk_gate.GATE.turn_off(state.activation_id, off):
        return Answer(409, words.KS_CHANGED_MEANWHILE, risk_gate.GATE.state())
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
    _tell(owner, words.KS_RESUMED)
    _announce(off)
    return Answer(200, words.KS_RESUMED, off)


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
