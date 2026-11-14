"""One alert, step by step (M12.1, M12.2). ORDER PATH. Callable without a request context.

``handle`` is what the public route calls. Its order is the contract:

1. the flag: ``off`` answers exactly as a link nobody made, before anything else is looked at;
2. the token's shape, then the sender's address, each answered the same way, with no lookup;
3. the token, by its hash;
4. the size of the body, the limit per link;
5. the message (payload.py): a bad one is written to the log and answered as not readable;
6. a first look at the rules, only to learn whether a sibling alert is worth a short wait;
7. **the claim**: holding the strategy's lock, the strategy and its state are read again, the
   rules are asked again, and the alert's row is written. The unique index on (strategy, claim)
   is the check and the marker in one statement, and the row is written before anything is sent;
8. the contract, the size and where the order goes (symbols.py, rules.pipe);
9. the order, still holding the lock, and its answer on the row.

The caller is never read from, or depended on, after its body is in: TradingView hangs up after
three seconds and an order can take longer. What the public caller is told is deliberately
almost nothing: the holder of a link learns no name, no position and no verdict from it.
"""

from __future__ import annotations

import ipaddress
import time
from dataclasses import dataclass, field, replace
from typing import Any

from utils import real_threading, runtime
from utils.keyed_locks import KeyedLocks, LockBusy
from utils.logging import get_logger
from utils.thread_safe_cache import LockedTTLCache

from . import contract, flag, payload, rules, symbols, words
from . import model as m
from .desk import Desk, senders

logger = get_logger(__name__)

#: One lock per strategy: an alert's claim, its order and the owner's switch all take it.
LOCKS = KeyedLocks(name="automation")
#: Longest an alert waits for its strategy's lock under gthread (the smart order's own figure).
LOCK_WAIT_S = 30.0

#: What builds the desk for a call that brings none. A test stands a fake in its place.
DESK = Desk

UNKNOWN = (404, {"status": "error", "message": "Not found"})
TOO_LARGE = (413, {"status": "error", "message": "The alert message is too large."})
TOO_MANY = (429, {"status": "error", "message": "Too many alerts. Try again in a minute."})
NOT_READ = (400, {"status": "error", "message": "The alert message could not be read."})
HANDLED = (200, {"status": "success", "message": "handled"})
ALREADY = (200, {"status": "success", "message": "already handled"})

#: Alerts per link in the last minute. Bounded: a key is a strategy's id, never a token.
_per_link = LockedTTLCache(maxsize=256, ttl=60.0)
#: What was refused before any strategy was known, counted for one log line a minute.
_refused = LockedTTLCache(maxsize=8, ttl=60.0)
_count_lock = real_threading.Lock()
#: When an alert last arrived from this server's own address (the proxy is not passing the
#: sender's address on). The Automation page says so.
_local_sender_ms = 0


@dataclass(frozen=True)
class Step:
    """One smart order of a plan."""

    exchange: str
    symbol: str
    target: int  # +1, 0, -1
    quantity: int
    order: dict[str, Any]
    expires_today: bool = False


@dataclass(frozen=True)
class Plan:
    """What the rules made of one alert, holding the lock."""

    verdict: str | None  # None: send ``steps``
    sentence: str = ""
    target: int = 0
    steps: tuple[Step, ...] = ()
    analyze: bool | None = None
    notes: tuple[str, ...] = ()  # said beside the order's own sentence
    tell: str | None = None  # the reason key for the owner's Telegram, when it should hear
    tells: tuple[tuple[str, str], ...] = ()  # (reason, sentence) the owner hears beside an order
    state_values: dict[str, Any] = field(default_factory=dict)
    strategy_values: dict[str, Any] = field(default_factory=dict)
    counts: bool = True


def local_sender_seen_ms() -> int:
    return _local_sender_ms


def _count_refused(why: str, ip: str) -> None:
    """Bounded: one counter per reason, one log line a minute. Never the token."""
    global _local_sender_ms
    if why == "sender":
        try:
            if ipaddress.ip_address(ip).is_loopback:
                _local_sender_ms = int(time.time() * 1000)
        except ValueError:
            pass
    with _count_lock:
        first = why not in _refused
        if first:
            _refused[why] = True
    if first:
        said = {
            "shape": "an alert link that is not one of SUDARSHAN's",
            "sender": "an alert from an address that is not on the sender list",
            "token": "an alert link nobody made, or one that was replaced",
        }[why]
        logger.warning(f"Automation: refused {said} (more in the next minute are not logged)")


def _link_over_limit(strategy_id: int, now: float) -> bool:
    """More than the limit for one link in this clock minute. The count restarts with the minute,
    so a strategy that alerts steadily is never shut out for good."""
    minute = int(now // 60)
    with _count_lock:  # in-memory bookkeeping only
        at, seen = _per_link.get(strategy_id) or (minute, 0)
        seen = seen + 1 if at == minute else 1
        _per_link[strategy_id] = (minute, seen)
    return seen > contract.TOKEN_PER_MINUTE


def order_data(strategy: m.Strategy, exchange: str, symbol: str, target: int, quantity: int,
               held_side: str | None = None) -> dict[str, Any]:  # fmt: skip
    """The smart order for one target. The quantity is the strategy's own, never the alert's: a
    smart order to a size other than 0 works out what to send from the account, and for an exit
    the quantity only marks the order as an exit that must never open a position.

    An option strategy only ever buys: a long target buys its call and a short target buys its
    put, so its order is a BUY to a size above 0 either way, and its exit a SELL to 0."""
    bought = strategy.kind == m.OPTION
    if target == 0:
        action = "BUY" if held_side == m.SHORT and not bought else "SELL"
    else:
        action = "BUY" if target > 0 or bought else "SELL"
    return {
        "strategy": strategy.name,
        "exchange": exchange,
        "symbol": symbol,
        "product": strategy.product,
        "action": action,
        "pricetype": "MARKET",
        "quantity": str(quantity),
        "position_size": str((abs(target) if bought else target) * quantity),
        "price": "0",
        "trigger_price": "0",
        "disclosed_quantity": "0",
    }


def _refuse(verdict: str, sentence: str, target: int, tell: str | None = None, **more) -> Plan:
    return Plan(verdict, sentence, target, tell=tell or verdict, **more)


def plan(
    strategy: m.Strategy,
    state: m.State,
    alert: m.Alert,
    desk: Desk,
    *,
    stage: str,
    claimed: bool = False,
    waited: bool = True,
) -> Plan:
    """What to do with one alert, on rows read holding the strategy's lock. No writes."""
    name, now = strategy.name, desk.now()
    now_ms, day = int(now * 1000), symbols.india_date(int(now * 1000))
    if claimed:
        return Plan("repeat", words.repeat(alert.fired_s), counts=False)
    if not symbols.chart_matches(strategy.chart, alert):
        if alert.ticker is None:
            return _refuse("not_mapped", words.not_mapped(name, "no_chart"), 0)
        sent = f"{alert.exchange}:{alert.ticker}"
        return _refuse("not_mapped", words.wrong_chart(name, sent, strategy.chart or ""), 0)
    indices = desk.indices()
    state_values: dict[str, Any] = {}
    notes: list[str] = []
    cleared = "expired"  # why a held note was cleared with no order, for the owner's Telegram
    if state.held_symbol and symbols.held_expired(
        state.held_symbol, state.held_exchange, now_ms, indices,
        _closes(desk, state.held_symbol, state.held_exchange),
    ):  # fmt: skip
        # The held contract stopped trading for good: the strategy holds nothing, and the chain
        # rule is silent for this one alert.
        notes.append(words.expired(name, state.held_symbol))
        state_values = dict(_NOTHING_HELD)
        state = replace(state, **state_values)
        alert = replace(alert, before=None)
    elif state.held_symbol and symbols.day_ended(strategy.product, state.held_since_ms, day):
        # An intraday position does not outlive its session: the broker (and the sandbox) squared
        # it off before the close. The note of it is cleared at the first alert of a later day,
        # so a flat alert sends nothing for it and an entry reads the price and picks today's
        # contract, never yesterday's strike. The chain rule is silent for this one alert.
        cleared = "day_ended"
        notes.append(words.day_ended(name, state.held_symbol))
        state_values = dict(_NOTHING_HELD)
        state = replace(state, **state_values)
        alert = replace(alert, before=None)
    decision = rules.decide(strategy, state, alert, now, day, waited=waited)
    target = decision.target
    if decision.wait:
        return Plan(None, target=target, notes=("wait",), counts=False)
    cap_values: dict[str, Any] = {}
    if decision.capped_now:
        cap_values = {"state": m.EXITS_ONLY, "stopped_why": "too many alerts today"}
        state_values["told_stopped_day"] = day
    common = {"state_values": state_values, "strategy_values": cap_values}
    if decision.verdict == "out_of_order":
        return _refuse("out_of_order", words.out_of_order(name, alert.fired_s), target, **common)
    if decision.verdict == "stopped":
        said = words.stopped(name, strategy.state == m.EXITS_ONLY, target)
        tell = "stopped" if decision.tell_stopped else ""
        if decision.tell_stopped:
            state_values["told_stopped_day"] = day
        return Plan("stopped", said, target, tell=tell or None, **common)
    if decision.verdict == "capped":
        said = words.capped(name, strategy.max_alerts_day)
        return Plan(
            "capped", said, target, tell="capped" if decision.capped_now else None, **common
        )
    if decision.verdict == "late":
        said = words.late(name, target, decision.late_s or 0, state.last_target)
        return _refuse("late", said, target, **common)
    tells: list[tuple[str, str]] = [(cleared, note) for note in notes]
    if decision.capped_now:
        notes.append(words.capped(name, strategy.max_alerts_day))
        tells.append(("capped", notes[-1]))
    if decision.late_s is not None:
        notes.append(words.late_note(decision.late_s))
    if decision.gap:
        notes.append(words.gap(name, target))
        tells.append(("gap", notes[-1]))
    # where the order goes
    api_key = desk.api_key()
    analyze, why = rules.pipe(
        stage, desk.guard_stage(), strategy.mode, desk.app_in_analyze(), target
    )
    if analyze is None:
        return _refuse(
            "mode_refused", words.mode_refused(name, why or "", target), target, **common
        )
    if alert.delayed and analyze is False:
        return _refuse("mode_refused", words.delayed_chart(name), target, **common)
    if not api_key:
        return _refuse("order_refused", words.order_refused(name, _NO_KEY), target, **common)
    if desk.semi_auto(api_key):
        said = words.mode_refused(name, rules.SEMI_AUTO, target)
        return _refuse("mode_refused", said, target, **common)
    # the contract and the size
    steps = _steps(strategy, state, target, desk, now_ms, indices, alert.size)
    if isinstance(steps, Plan):
        if steps.verdict == rules.NO_ACTION:
            # Nothing held and nothing to close: the state says flat. An option that expired
            # since is said first.
            state_values.update(last_target=0, last_fired_s=rules.mark_of(alert, now_ms))
            said = " ".join([*notes, steps.sentence])
            return replace(steps, sentence=said, tell=tells[0][0] if tells else None, **common)
        return replace(steps, **common)
    for step in steps:
        if step.expires_today and step.target != 0:
            notes.append(words.expires_today(step.symbol))
            tells.append(("expires_today", notes[-1]))
    return Plan(
        None,
        target=target,
        steps=steps,
        analyze=analyze,
        notes=tuple(notes),
        tells=tuple(tells),
        **common,
    )


#: The state's values when a held note is cleared with no order of the strategy's own.
_NOTHING_HELD: dict[str, Any] = {
    "held_exchange": None,
    "held_symbol": None,
    "held_side": None,
    "held_since_ms": None,
    "last_target": 0,
}
_NO_KEY = "No API key is set up in the app. Make one under API Key, then try again."


def _closes(desk: Desk, symbol: str | None, exchange: str | None) -> dict[str, int]:
    found = symbols.expiry_of(symbol or "")
    return desk.closes([found[1]], exchange or "NFO") if found else {}


def _resolve(strategy: m.Strategy, desk: Desk, now_ms: int, indices, entering: bool, target=1):
    if strategy.kind == m.OPTION:
        underlying, exchange = strategy.underlying, strategy.exchange
        expiries = desk.option_expiries(underlying)
        return symbols.resolve(
            strategy,
            now_ms,
            known=desk.known,
            expiries=expiries,
            indices=indices,
            closes=desk.closes(list(expiries), exchange) if expiries else {},
            entering=entering,
            target=target,
            strikes_of=lambda iso: desk.strikes(underlying, iso),
            price=desk.underlying_price(underlying, exchange) if expiries else None,
        )
    underlying = symbols.futures_underlying(strategy)
    expiries = desk.future_expiries(underlying) if underlying else []
    exchange = strategy.exchange or "NFO"
    return symbols.resolve(
        strategy,
        now_ms,
        known=desk.known,
        expiries=expiries,
        indices=indices,
        closes=desk.closes(list(expiries), exchange) if expiries else {},
        entering=entering,
    )


def _steps(strategy, state, target: int, desk: Desk, now_ms: int, indices, size=None):
    """The order, or the two orders, for a target; or the refusal as a ``Plan``."""
    name = strategy.name
    held = (state.held_exchange, state.held_symbol) if state.held_symbol else None
    if target == 0 and held:
        # An exit goes to the contract that was entered, whatever the rule resolves today.
        quantity = _size(strategy, desk, *held, exit_=True)
        return (_step(strategy, *held, 0, quantity, state.held_side),)
    option = strategy.kind == m.OPTION
    if target == 0:
        # A strategy only ever closes what it opened. With nothing held there is nothing of its
        # own to close: an exit sent for the contract its rule means today would close a position
        # the owner opened by hand in that contract and product (and for an option strategy the
        # rule's strike today says nothing about what it once bought). Nothing is sent, in the
        # Sandbox as in Live, so a rehearsal shows what Live will do.
        return Plan(rules.NO_ACTION, words.holds_nothing(name, option), 0)
    if not desk.master_loaded():
        return _refuse("not_mapped", words.not_mapped(name, "master"), target)
    if option and held and symbols.option_side(held[1]) == symbols.wanted_side(target):
        # The same target with its option already bought: the held contract is asked, never a
        # new strike or a later expiry. A strategy never rolls a held position by itself.
        quantity = _size(strategy, desk, *held, size=size)
        if isinstance(quantity, Plan):
            return replace(quantity, target=target)
        return (_step(strategy, *held, target, quantity, None),)
    found = _resolve(strategy, desk, now_ms, indices, entering=True, target=target)
    if isinstance(found, symbols.Unmapped):
        if found.reason == "too_close":
            said = words.too_close(name, found.what, (found.stop_ms or 0) // 1000)
            return _refuse("not_mapped", said, target)
        return _refuse("not_mapped", words.not_mapped(name, found.reason, found.what), target)
    same_side = state.held_side == (m.LONG if target > 0 else m.SHORT)
    if not option and held and held != (found.exchange, found.symbol) and same_side:
        # A strategy never rolls a held position by itself: the same target with an older
        # contract held is asked of the held contract, and reads as already there.
        found = replace(found, exchange=held[0], symbol=held[1])
    quantity = _size(strategy, desk, found.exchange, found.symbol, size=size)
    if isinstance(quantity, Plan):
        return replace(quantity, target=target)
    entry = replace(
        _step(strategy, found.exchange, found.symbol, target, quantity, None),
        expires_today=found.expires_today,
    )
    if held and held != (found.exchange, found.symbol):
        # The other side, in another contract (an option strategy always; a future after a
        # roll): the held contract's exit first, and the entry only if that exit answered clearly.
        out = _size(strategy, desk, *held, exit_=True)
        return (_step(strategy, *held, 0, out, state.held_side), entry)
    return (entry,)


def _size(strategy, desk: Desk, exchange: str, symbol: str, exit_: bool = False, size=None):
    found = symbols.expiry_of(symbol)
    scaled = strategy.size_mode == m.SCALED
    lot = desk.lot_size(exchange, symbol) if strategy.lots or (scaled and found) else 1
    freeze = desk.freeze_qty(found[0], exchange) if found and not exit_ else 0
    if scaled and not exit_:
        # TradingView's position size times the multiplier: whole lots, never above the cap.
        got = symbols.scaled_quantity(strategy, size, lot, freeze)
    else:
        got = symbols.quantity(strategy, lot, freeze)
    if isinstance(got, symbols.NotSized):
        if exit_:
            return 1  # an exit is sized from the account; this only marks the order as an exit
        said = words.not_sized(strategy.name, got.reason, symbol, got.figure)
        return _refuse("not_sized", said, 0)
    return got


def _step(strategy, exchange, symbol, target, quantity, held_side) -> Step:
    order = order_data(strategy, exchange, symbol, target, quantity, held_side)
    return Step(exchange, symbol, target, quantity, order)


@dataclass(frozen=True)
class Outcome:
    status: int
    body: dict[str, Any]
    verdict: str | None = None
    sentence: str = ""
    alert_id: int | None = None


def _out(pair, **more) -> Outcome:
    return Outcome(pair[0], dict(pair[1]), **more)


def check(strategy_id: int, body: str | bytes, desk: Desk | None = None, store=None) -> dict:
    """What one alert's text would do now, for the page's Check sheet. Nothing is written and
    nothing is sent."""
    desk = desk or DESK()
    if store is None:
        from .store import shared

        store = shared()
    both = store.both(strategy_id)
    if both is None:
        return {"verdict": "bad_message", "sentence": words.NOT_FOUND, "order": None}
    strategy, state = both
    try:
        alert = payload.parse(body)
    except payload.BadMessage as bad:
        said = words.bad_message(strategy.name, bad.reason, bad.what)
        return {"verdict": "bad_message", "sentence": said, "order": None}
    stage = flag.stage() if flag.stage() != flag.OFF else flag.SANDBOX
    made = plan(strategy, state, alert, desk, stage=stage)
    if made.verdict is not None:
        return {"verdict": made.verdict, "sentence": made.sentence, "order": None}
    last = made.steps[-1]
    said = " ".join([_would(strategy, made), *made.notes])
    return {"verdict": "sent", "sentence": said, "order": dict(last.order)}


def _would(strategy: m.Strategy, made: Plan) -> str:
    last = made.steps[-1]
    where = "the Sandbox" if made.analyze else "Upstox"
    if last.target == 0:
        return f"Would send an exit for {words.contract_name(last.symbol)} to {where}."
    first = ""
    if len(made.steps) > 1:
        first = (
            f"Would first send an exit for {words.contract_name(made.steps[0].symbol)}, and "
            "only if that answers clearly: "
        )
    if strategy.kind == m.OPTION:
        return (
            f"{first}Would send an order to {where} to buy {last.quantity} "
            f"{words.contract_name(last.symbol)}."
        )
    return (
        f"{first}Would send an order to {where} to be {words.side(last.target)} {last.quantity} "
        f"{words.contract_name(last.symbol)}."
    )


def handle(token: str, body: bytes, ip: str, desk: Desk | None = None, store=None) -> Outcome:
    """One alert. Never raises: anything unexpected is logged and answered as handled, because
    the sender cannot act on an answer and must not be told more than "received"."""
    stage = flag.stage()
    if stage == flag.OFF:
        return _out(UNKNOWN)
    try:
        return _handle(token, body, ip, desk or DESK(), store, stage)
    except Exception:
        logger.exception("Automation: an alert could not be handled")
        return _out(HANDLED)


def _handle(token: str, body: bytes, ip: str, desk: Desk, store, stage: str) -> Outcome:
    if not isinstance(token, str) or not contract.TOKEN.fullmatch(token):
        _count_refused("shape", ip)
        return _out(UNKNOWN)
    if ip not in senders():
        _count_refused("sender", ip)
        return _out(UNKNOWN)
    if store is None:
        from .store import shared

        store = shared()
    strategy = store.by_token(token)
    if strategy is None:
        _count_refused("token", ip)
        return _out(UNKNOWN)
    if len(body) > contract.MAX_BODY:
        return _out(TOO_LARGE)
    if _link_over_limit(strategy.id, desk.now()):
        return _out(TOO_MANY)
    received_ms = int(desk.now() * 1000)
    row = {"received_ms": received_ms, "ip": ip[:45], "payload": payload.redact(body)}
    try:
        alert = payload.parse(body)
    except payload.BadMessage as bad:
        said = words.bad_message(strategy.name, bad.reason, bad.what)
        alert_id = store.note_alert(strategy.id, verdict="bad_message", sentence=said, **row)
        _after(desk, strategy, "bad_message", said, "bad_message")
        return _out(NOT_READ, verdict="bad_message", sentence=said, alert_id=alert_id)
    row.update(
        fired_s=alert.fired_s,
        bar_s=alert.bar_s,
        claim=alert.claim,
        tv_delay_ms=received_ms - alert.fired_s * 1000,
    )
    _wait_for_a_sibling(strategy.id, alert, desk, store, stage)
    wait = LOCK_WAIT_S if runtime.gthread_active() else None
    try:
        with LOCKS.hold(strategy.id, timeout=wait):
            return _claim_and_send(strategy.id, token, alert, row, desk, store, stage)
    except LockBusy:
        said = words.busy(strategy.name)
        alert_id = store.note_alert(strategy.id, verdict="order_refused", sentence=said, **{
            **row, "claim": None,
        })  # fmt: skip
        _after(desk, strategy, "order_refused", said, "busy")
        return _out(HANDLED, verdict="order_refused", sentence=said, alert_id=alert_id)


def _wait_for_a_sibling(strategy_id: int, alert: m.Alert, desk: Desk, store, stage: str) -> None:
    """Two alerts fired in the same second can arrive swapped. The one whose "before" does not
    fit waits, without the lock, up to 1.5 s for the other to be applied."""
    if alert.before is None:
        return
    waited = 0.0
    while waited < rules.CHAIN_WAIT_S:
        both = store.both(strategy_id)
        if both is None:
            return
        strategy, state = both
        now = desk.now()
        day = symbols.india_date(int(now * 1000))
        if not rules.decide(strategy, state, alert, now, day, waited=False).wait:
            return
        desk.sleep(rules.CHAIN_STEP_S)
        waited += rules.CHAIN_STEP_S


def _claim_and_send(strategy_id, token, alert, row, desk, store, stage) -> Outcome:
    made: list[Plan] = []

    def decide(strategy: m.Strategy, state: m.State) -> dict[str, Any] | None:
        chosen = plan(strategy, _may_hold(strategy, state, unread), alert, desk, stage=stage)
        if unread and chosen.verdict is None and chosen.target != 0:
            # What the strategy holds is not known, so nothing that opens, adds or reverses is
            # sent. An exit still goes: sized from the account, it can only close.
            said = words.unsettled(strategy.name, chosen.target)
            chosen = _refuse(
                "order_refused",
                said,
                chosen.target,
                tell="unrecorded",
                state_values=chosen.state_values,
                strategy_values=chosen.strategy_values,
            )
        made.append(chosen)
        # The row names the contract the alert ends in (the entry of a two-step; the held one is
        # in the state): if its answer is never written, both are known when it is settled.
        first = chosen.steps[-1] if chosen.steps else None
        return {
            **row,
            "verdict": chosen.verdict or "claimed",
            "sentence": chosen.sentence,
            "target": chosen.target,
            "mode": m.SANDBOX
            if chosen.analyze
            else (m.LIVE if chosen.analyze is False else strategy.mode),
            "exchange": first.exchange if first else None,
            "symbol": first.symbol if first else None,
            "action": first.order["action"] if first else None,
            "quantity": first.quantity if first else None,
            "counts": chosen.counts,
            "state_values": chosen.state_values,
            "strategy_values": chosen.strategy_values,
        }

    day = symbols.india_date(int(desk.now() * 1000))
    # An earlier alert whose answer was never written is settled first, holding the lock: the
    # rules below must judge this alert on what the strategy holds, not on a note that missed an
    # order.
    unread = settle(strategy_id, desk, store)
    # The link is checked again in the same read as the strategy: one replaced while this alert
    # waited for the lock is nobody's now.
    claimed = store.claim_alert(strategy_id, decide, day, token=token)
    if claimed is None or not made:
        _count_refused("token", row.get("ip", ""))
        return _out(UNKNOWN)
    strategy, state, alert_id, _columns = claimed
    chosen = made[0]
    if alert_id is None:  # the claim was there already: a second delivery of the same alert
        said = words.repeat(alert.fired_s)
        store.note_alert(strategy_id, verdict="repeat", sentence=said, **{**row, "claim": None})
        return _out(ALREADY, verdict="repeat", sentence=said)
    if chosen.verdict is not None:
        _after(desk, strategy, chosen.verdict, chosen.sentence, chosen.tell)
        return _out(HANDLED, verdict=chosen.verdict, sentence=chosen.sentence, alert_id=alert_id)
    try:
        return _send(strategy, _may_hold(strategy, state, unread), alert, alert_id, chosen, row,
                     desk, store)  # fmt: skip
    except Exception:
        # After the claim nothing fails silently: the row stays claimed, the next alert settles
        # it from the books, and the owner is told now.
        logger.exception("Automation: an alert could not be finished after its claim")
        said = words.unrecorded(strategy.name)
        try:
            store.note_unrecorded(alert_id, said)
        except Exception:
            logger.exception("Automation: the note of an unrecorded answer could not be written")
        _tell(desk, strategy, "unrecorded", said)
        return _out(HANDLED, verdict="claimed", sentence=said, alert_id=alert_id)


def _may_hold(strategy: m.Strategy, state: m.State, unread: list[dict[str, Any]]) -> m.State:
    """The state an alert is judged on while an earlier alert is unsettled and the books cannot
    be read: an entry that may have gone is held, as a not confirmed entry is, so this alert's
    exit goes to its contract. Never written: the books decide once they can be read."""
    if state.held_symbol:
        return state
    for row in reversed(unread):
        if row.get("symbol") and int(row.get("target") or 0) != 0:
            long_ = strategy.kind == m.OPTION or int(row["target"]) > 0
            return replace(
                state,
                held_exchange=row.get("exchange"),
                held_symbol=row["symbol"],
                held_side=m.LONG if long_ else m.SHORT,
                held_since_ms=row.get("received_ms"),
            )
    return state


def _send(strategy, state, alert, alert_id, chosen: Plan, row, desk, store) -> Outcome:
    """The order, holding the strategy's lock, with the alert's row already written.

    One smart order to the target; or two when the held contract and the one to enter differ: the
    held contract's exit to 0 first, and the entry only if that exit answered clearly (an order
    went, or there was nothing to close). On anything else the entry is not sent: a refusal costs
    nothing, and both sides are never opened from one alert. Nothing is ever retried.

    Whatever happens here, the claim stays: an alert whose order failed is not handled again.
    """
    name = strategy.name
    api_key = desk.api_key()
    state_values: dict[str, Any] = {}
    verdict, sentence, order_id, done = (
        "order_refused",
        words.order_refused(name, _NO_KEY),
        None,
        None,
    )
    tells = list(chosen.tells)
    now_state = state
    first_verdict = None  # what the first of two orders answered
    for index, step in enumerate(chosen.steps if api_key else ()):
        done = step
        try:
            success, response, status = desk.place(step.order, api_key, chosen.analyze)
        except Exception:
            # It may have reached the broker: never a refusal, never the failure's own words.
            logger.exception("Automation: the smart order call did not return an answer")
            success, response, status = False, {}, 500
        verdict, flat_found, cut = rules.read_answer(success, response, status)
        first_verdict = verdict if index == 0 else first_verdict
        body = response if isinstance(response, dict) else {}
        said = body.get("risk_note") or body.get("message") or ""
        # A reversal the risk gate cut to its exit left the position flat: the state says flat.
        reached = 0 if cut else step.target
        moved = rules.state_after(
            verdict, flat_found, reached, alert, step.exchange, step.symbol,
            int(desk.now() * 1000), now_state, bought=strategy.kind == m.OPTION,
        )  # fmt: skip
        state_values.update(moved)
        now_state = replace(now_state, **moved)
        if verdict == rules.SENT:
            order_id = str(body.get("orderid"))
            sentence = words.sent(name, step.target, step.quantity, step.symbol)
            if cut:
                sentence = words.cut_to_exit(name, step.symbol, said)
        elif verdict == rules.NO_ACTION:
            sentence = words.no_action(name, step.target, step.quantity, step.symbol, flat_found)
        elif verdict == rules.ORDER_REFUSED:
            sentence = words.order_refused(name, said)
        else:
            sentence = words.not_confirmed(name, said)
        if index < len(chosen.steps) - 1 and verdict not in (rules.SENT, rules.NO_ACTION):
            wanted = chosen.steps[-1].symbol
            sentence = words.exit_first_refused(
                name, step.symbol, wanted, verdict == rules.ORDER_REFUSED
            )
            tells.append(("two_step", sentence))
            break
    if len(chosen.steps) > 1 and done is chosen.steps[-1] and verdict == rules.SENT:
        # Two orders went, and the row names only the second: say what the first did, and that
        # an order placed is not an order filled.
        first = chosen.steps[0]
        if first_verdict == rules.SENT:
            sentence = f"{sentence} {words.two_step_sent(name, first.symbol)}"
    sentence = " ".join([sentence, *chosen.notes])
    columns = {}
    if done is not None:
        columns = {
            "exchange": done.exchange,
            "symbol": done.symbol,
            "action": done.order["action"],
            "quantity": done.quantity,
        }
    _record(
        strategy,
        desk,
        store,
        alert_id,
        verdict=verdict,
        sentence=sentence,
        order_id=order_id,
        answer_ms=int(desk.now() * 1000) - row["received_ms"],
        state_values=state_values,
        **columns,
    )
    _after(desk, strategy, verdict, sentence, None)
    for reason, said in tells:
        if reason == "expires_today" and verdict != rules.SENT:
            continue  # nothing was bought, so there is nothing that expires today
        _tell(desk, strategy, reason, said)
    return _out(HANDLED, verdict=verdict, sentence=sentence, alert_id=alert_id)


#: Answers that could not be written, by strategy, until a later write takes them: exactly what
#: the order service said. In memory only (at most one per strategy, and there are at most 50
#: strategies); after a restart the row is settled from the books instead.
_unrecorded: dict[int, dict[str, Any]] = {}
#: A row still claimed this long after it arrived, with the books unreadable ever since, is
#: closed as not confirmed (the longest any hold of the books' lasts).
SETTLE_GIVE_UP_MS = 16 * 3600 * 1000


def _record(strategy: m.Strategy, desk: Desk, store, alert_id: int, **answer: Any) -> None:
    """Write an order's answer on its alert's row, with the state it moved. The order has gone
    (or was refused) by now, so a write that fails must not be silent: it is tried once more,
    then kept in memory for the next alert to write, the row is left ``claimed`` with a sentence,
    and the owner is told at once."""
    for attempt in (1, 2):
        try:
            store.finish_alert(alert_id, **answer)
            _unrecorded.pop(strategy.id, None)
            return
        except Exception:
            logger.exception(f"Automation: an order's answer could not be written (try {attempt})")
    with _count_lock:  # in-memory bookkeeping only
        _unrecorded[strategy.id] = {"alert_id": alert_id, **answer}
    said = words.unrecorded(strategy.name, answer.get("sentence") or "")
    try:
        store.note_unrecorded(alert_id, said)
    except Exception:
        logger.exception("Automation: the note of an unrecorded answer could not be written")
    _tell(desk, strategy, "unrecorded", said)


def settle(strategy_id: int, desk: Desk, store) -> list[dict[str, Any]]:
    """Settle a strategy's alerts whose answer was never written. Call it holding the strategy's
    lock: no alert of the strategy is being handled then, so every row still ``claimed`` is one
    whose order's outcome is not on record. Returns the rows still unsettled (the books could
    not be read): the caller sends nothing that opens until there are none.

    An answer this process still remembers is written as it was. Otherwise the account is read,
    never assumed: the contract the alert named and the one the state holds, in the row's own
    pipe. The strategy holds what the account shows, so an option that was bought is on record
    and its exit alert closes it.
    """
    with _count_lock:
        kept = _unrecorded.get(strategy_id)
    if kept is not None:
        answer = {key: value for key, value in kept.items() if key != "alert_id"}
        store.finish_alert(kept["alert_id"], **answer)  # a failure here refuses this alert
        with _count_lock:
            _unrecorded.pop(strategy_id, None)
    rows = store.unsettled(strategy_id)
    if not rows:
        return []
    both = store.both(strategy_id)
    if both is None:
        return []
    strategy, state = both
    api_key = desk.api_key()
    now_ms = int(desk.now() * 1000)
    unread: list[dict[str, Any]] = []
    for row in rows:
        found = _read_books(strategy, state, row, desk, api_key) if api_key else None
        if found is None:
            if now_ms - (row.get("received_ms") or 0) < SETTLE_GIVE_UP_MS:
                unread.append(row)
                continue
            said = words.unsettled_gave_up(strategy.name)
            store.finish_alert(row["id"], verdict=rules.NOT_CONFIRMED, sentence=said)
            _after(desk, strategy, rules.NOT_CONFIRMED, said, "unrecorded")
            continue
        values = rules.state_settled(found, row, strategy.kind == m.OPTION, now_ms, state)
        said = words.settled(strategy.name, values["held_symbol"], values["held_side"])
        store.finish_alert(
            row["id"], verdict=rules.NOT_CONFIRMED, sentence=said, state_values=values
        )
        state = replace(state, **values)
        _after(desk, strategy, rules.NOT_CONFIRMED, said, "unrecorded")
    return unread


def _read_books(strategy, state, row, desk: Desk, api_key: str):
    """What the account holds of the two contracts an unsettled alert can have touched: the one
    its row names and the one the state holds. ``None`` when either cannot be read."""
    analyze = row.get("mode") != m.LIVE
    named = (row.get("exchange"), row.get("symbol")) if row.get("symbol") else None
    held = (state.held_exchange, state.held_symbol) if state.held_symbol else None
    found: dict[tuple, int] = {}
    for contract_ in dict.fromkeys(c for c in (named, held) if c):
        quantity = desk.held_quantity(*contract_, strategy.product, api_key, analyze)
        if quantity is None:
            return None
        found[contract_] = quantity
    return rules.Books(named, held, found)


def _after(desk: Desk, strategy: m.Strategy, verdict: str, sentence: str, tell: str | None) -> None:
    """The live event for the owner's screens and, when it should hear, his Telegram. Neither
    carries a token or an API key, and neither can fail the alert."""
    desk.emit(
        {
            "kind": "alert",
            "strategy_id": strategy.id,
            "name": strategy.name,
            "verdict": verdict,
            "sentence": sentence,
            "at_ms": int(desk.now() * 1000),
        }
    )
    if tell:
        _tell(desk, strategy, tell, sentence)


def _tell(desk: Desk, strategy: m.Strategy, reason: str, sentence: str) -> None:
    try:
        desk.tell(strategy.id, reason, sentence)
    except Exception:
        logger.exception("Automation: a notice could not be queued")
