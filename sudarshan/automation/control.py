"""The owner's switch for one strategy, and its link (M12.1). ORDER PATH: the switch decides
whether an alert's order goes.

Every change here takes the strategy's lock, the one an alert holds from its claim to its order's
answer. So an alert is either applied whole before a stop, or sees the stop; and an alert on a
link that was replaced is either applied before the new link is handed out, or is nobody's. Under
gthread the wait for the lock is bounded, and the answer says an alert is being handled; under
eventlet and the development server it waits, as the smart order's own lock does.
"""

from __future__ import annotations

from typing import Any

from utils import runtime
from utils.keyed_locks import LockBusy
from utils.logging import get_logger

from . import contract, flag, messages, rules, symbols, words
from . import model as m
from .desk import Desk
from .intake import LOCK_WAIT_S, LOCKS, local_sender_seen_ms
from .store import AutomationStore, Conflict, Missing, Taken, shared

logger = get_logger(__name__)

BUSY = "An alert of this strategy is being handled right now. Try again in a few seconds."


class Refused(Exception):
    """The change was not made. ``str()`` is the sentence the owner reads; ``status`` is how the
    route answers (never shown)."""

    def __init__(self, sentence: str, status: int = 409):
        super().__init__(sentence)
        self.status = status


def _held(strategy_id: int):
    wait = LOCK_WAIT_S if runtime.gthread_active() else None
    return LOCKS.hold(strategy_id, timeout=wait)


def _said(desk: Desk, strategy: m.Strategy, sentence: str, reason: str, **more: Any) -> None:
    desk.emit(
        {
            "kind": "strategy",
            "strategy_id": strategy.id,
            "name": strategy.name,
            "state": strategy.state,
            "mode": strategy.mode,
            "sentence": sentence,
            "at_ms": int(desk.now() * 1000),
            **more,
        }
    )
    try:
        desk.tell(strategy.id, reason, sentence)
    except Exception:
        logger.exception("Automation: a notice could not be queued")


def _write(strategy_id: int, change, store: AutomationStore) -> m.Strategy:
    try:
        with _held(strategy_id):
            return change()
    except LockBusy as busy:
        raise Refused(BUSY, 409) from busy
    except Missing as gone:
        raise Refused(words.NOT_FOUND, 404) from gone
    except Conflict as moved:
        raise Refused(words.CHANGED_ELSEWHERE, 409) from moved


def _live_gate(desk: Desk) -> str | None:
    """Why a strategy cannot be Live now, or ``None``."""
    if flag.stage() != flag.ON:
        return words.LIVE_WAITS_FOR_STAGE
    if desk.guard_stage() != "on":
        return words.LIVE_WAITS_FOR_GUARD
    return None


def _taken(name: str, taken: Taken) -> Refused:
    return Refused(words.contract_taken(name, taken.other or "another strategy"))


def switch(
    strategy_id: int,
    version: int | None,
    state: str,
    surface: str,
    *,
    phrase: object = None,
    desk: Desk | None = None,
    store: AutomationStore | None = None,
) -> m.Strategy:
    """Active, stopped for new entries, or Stopped. Holding the strategy's lock.

    Making a strategy Active (or leaving it to its exits) claims its contract and product in its
    mode: the claim is one write against a unique index, so of two strategies started together on
    one contract exactly one is Active, and the other is refused with a sentence. Starting a Live
    strategy needs automation and the risk guard both at ``on``, and the owner's confirm phrase;
    stopping never needs anything.
    """
    if state not in m.STATES:
        raise Refused("Choose Active or Stopped.", 400)
    desk, store = desk or Desk(), store or shared()
    why = None if state == m.ACTIVE else f"by you, from {surface}"

    def change() -> m.Strategy:
        before = store.strategy(strategy_id)
        if before is None:
            raise Missing(strategy_id)
        if state == m.ACTIVE and before.mode == m.LIVE:
            blocked = _live_gate(desk)
            if blocked:
                raise Refused(blocked)
            if not desk.phrase_ok(phrase, f"start {before.name} Live", surface):
                raise Refused(words.WRONG_PHRASE, 400)
        try:
            changed = store.set_state(strategy_id, version, state, why)
        except Taken as taken:
            raise _taken(before.name, taken) from taken
        if state == m.ACTIVE and before.state != m.ACTIVE:
            # Stop and Start: the strategy begins again, and no earlier alert's time decides
            # which alert is out of order.
            store.clear_mark(strategy_id)
        return changed

    changed = _write(strategy_id, change, store)
    _said(desk, changed, words.switched(changed.name, state, surface), "switched")
    return changed


def set_mode(
    strategy_id: int,
    version: int,
    mode: str,
    surface: str,
    *,
    phrase: object = None,
    desk: Desk | None = None,
    store: AutomationStore | None = None,
) -> m.Strategy:
    """Sandbox or Live for one strategy. Holding the strategy's lock.

    Live needs automation at ``on``, the risk guard at ``on`` and the confirm phrase. A strategy
    holding a position cannot change its mode: it closes the position where it opened it.
    """
    if mode not in m.MODES:
        raise Refused("Choose Sandbox or Live.", 400)
    desk, store = desk or Desk(), store or shared()

    def change() -> m.Strategy:
        both = store.both(strategy_id)
        if both is None:
            raise Missing(strategy_id)
        before, state = both
        if before.mode == mode:
            return before
        if state.held_symbol:
            raise Refused(
                words.holds_position(
                    before.name, state.held_symbol, state.held_side or "", before.mode
                )
            )
        if mode == m.LIVE:
            blocked = _live_gate(desk)
            if blocked:
                raise Refused(blocked)
            if not desk.phrase_ok(phrase, f"set {before.name} to Live", surface):
                raise Refused(words.WRONG_PHRASE, 400)
        try:
            return store.set_mode(strategy_id, version, mode)
        except Taken as taken:
            raise _taken(before.name, taken) from taken

    changed = _write(strategy_id, change, store)
    _said(desk, changed, words.mode_changed(changed.name, mode, surface), "mode")
    return changed


def create(
    form: dict[str, Any], *, desk: Desk | None = None, store: AutomationStore | None = None
) -> tuple[m.Strategy, str]:
    """A new strategy, Sandbox and Stopped, and its link's token (given once)."""
    desk, store = desk or Desk(), store or shared()
    try:
        columns = contract.clean(form)
        if store.count() >= contract.STRATEGIES_MOST:
            raise contract.Refused(f"You can have at most {contract.STRATEGIES_MOST} strategies.")
        return store.create(columns)
    except contract.Refused as refused:
        raise Refused(str(refused), 400) from refused


def update(
    strategy_id: int,
    version: int,
    changes: dict[str, Any],
    *,
    desk: Desk | None = None,
    store: AutomationStore | None = None,
) -> m.Strategy:
    """An edit. While the strategy holds a position only its name and its limits may change:
    what it trades, its product and its size decide which position its exit closes."""
    desk, store = desk or Desk(), store or shared()
    if not isinstance(changes, dict) or not changes:
        raise Refused("Nothing was changed.", 400)

    def change() -> m.Strategy:
        both = store.both(strategy_id)
        if both is None:
            raise Missing(strategy_id)
        before, state = both
        try:
            unknown = set(changes) - contract.EDITABLE
            if unknown:
                raise contract.Refused("The strategy has a setting this release does not know.")
            columns = contract.clean({**contract.form_of(before), **changes})
        except contract.Refused as refused:
            raise Refused(str(refused), 400) from refused
        moved = {k for k, v in columns.items() if getattr(before, k) != v}
        if state.held_symbol and moved - contract.HELD_EDITABLE:
            raise Refused(
                words.holds_position(
                    before.name, state.held_symbol, state.held_side or "", before.mode
                )
            )
        if not moved:
            return before
        try:
            return store.update(strategy_id, version, {k: columns[k] for k in moved})
        except Taken as taken:
            raise _taken(before.name, taken) from taken
        except contract.Refused as refused:
            raise Refused(str(refused), 400) from refused

    return _write(strategy_id, change, store)


CLAIMED_STALE_MS = 180_000


def alert_line(row: dict[str, Any], name: str, now_ms: int) -> dict[str, Any]:
    """An alert's line for the page. A line still ``claimed`` minutes later was being handled
    when the server stopped: its order is not confirmed."""
    line = {key: row.get(key) for key in contract.ALERT}
    if line["verdict"] == "claimed" and now_ms - (line["received_ms"] or 0) > CLAIMED_STALE_MS:
        line["verdict"], line["sentence"] = "not_confirmed", words.interrupted(name)
    return line


def view(strategy: m.Strategy, store: AutomationStore, now_ms: int, trading=None) -> dict[str, Any]:
    """One strategy as the page reads it (contract.STRATEGY). Never a token or its hash.

    ``trading`` says whether the market traded on a date; with it, an Active strategy that has
    had no alert since it last heard one (or since it was started) carries the trading days gone.
    """
    state = store.state(strategy.id)
    last = store.last_alert(strategy.id)
    quiet = 0
    if trading is not None and strategy.state == m.ACTIVE:
        heard = (last or {}).get("received_ms") or strategy.updated_ms or strategy.created_ms
        if heard:
            quiet = rules.quiet_days(symbols.india_date(heard), symbols.india_date(now_ms), trading)
    out = {key: getattr(strategy, key) for key in contract.STRATEGY if hasattr(strategy, key)}
    out.update(
        trades=words.trades(strategy),
        last_target=state.last_target,
        held_exchange=state.held_exchange,
        held_symbol=state.held_symbol,
        held_side=state.held_side,
        held_since_ms=state.held_since_ms,
        alerts_today=state.alerts_today,
        last_alert=alert_line(last, strategy.name, now_ms) if last else None,
        sandbox_run=store.sandbox_run(strategy.id),
        quiet_days=quiet,
    )
    return out


#: The India date the alert log was last pruned on, by this process. In memory: a restart
#: prunes once more, which costs one small delete.
_pruned_on: dict[int, str] = {}


def _prune_once_a_day(store: AutomationStore, now_ms: int) -> None:
    """The alert log keeps 90 days and at most 20,000 lines a strategy. It is pruned from the
    owner's own page read, once a day, never from an alert's request and never by a timer inside
    the app. A failure costs nothing: the page is read as before and tomorrow tries again."""
    day = symbols.india_date(now_ms)
    if _pruned_on.get(id(store)) == day:
        return
    _pruned_on.clear()
    _pruned_on[id(store)] = day
    try:
        gone = store.prune(now_ms)
        if gone:
            logger.info(f"Automation: {gone} old alert lines were removed")
    except Exception:
        logger.exception("Automation: the alert log could not be pruned")


def listing(*, desk: Desk | None = None, store: AutomationStore | None = None) -> dict[str, Any]:
    """Everything the page's first read needs (contract.ANSWERS["ListAnswer"])."""
    desk, store = desk or Desk(), store or shared()
    now_ms = int(desk.now() * 1000)
    _prune_once_a_day(store, now_ms)
    stage = flag.stage()
    blocked = _live_gate(desk)
    notes = []
    if now_ms - local_sender_seen_ms() < 86_400_000 and local_sender_seen_ms():
        notes.append(words.SENDER_HIDDEN)
    if desk.sender_banned():
        notes.append(words.SENDER_BANNED)
    return {
        "status": "success",
        "stage": stage,
        "stage_words": words.STAGE_WORDS[stage],
        "live_allowed": blocked is None,
        "live_blocked_why": blocked,
        "kill_switch_on": desk.kill_switch_on(),
        "notes": notes,
        "strategies": [
            view(strategy, store, now_ms, desk.trading_day) for strategy in store.strategies()
        ],
    }


def created(
    strategy: m.Strategy, token: str, store: AutomationStore, now_ms: int
) -> dict[str, Any]:
    """The answer that carries a link's token: the only one that ever does."""
    return {
        "status": "success",
        "strategy": view(strategy, store, now_ms),
        "token": token,
        "link_path": f"{contract.PUBLIC_PREFIX}/{token}",
        "messages": messages.for_strategy(strategy),
    }


def new_link(
    strategy_id: int,
    version: int,
    surface: str,
    *,
    desk: Desk | None = None,
    store: AutomationStore | None = None,
) -> tuple[m.Strategy, str]:
    """A new link for the strategy. When this returns, the old one is nobody's: an alert on it
    that was already being handled finished first, and no later one is applied."""
    desk, store = desk or Desk(), store or shared()
    made: list[str] = []

    def change() -> m.Strategy:
        strategy, token = store.replace_token(strategy_id, version)
        made.append(token)
        store.clear_mark(strategy_id)  # a new link starts the order of alerts again
        return strategy

    changed = _write(strategy_id, change, store)
    _said(desk, changed, words.new_link(changed.name, surface), "new_link")
    return changed, made[0]
