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

from . import model as m
from . import words
from .desk import Desk
from .intake import LOCK_WAIT_S, LOCKS
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


def switch(
    strategy_id: int,
    version: int | None,
    state: str,
    surface: str,
    *,
    desk: Desk | None = None,
    store: AutomationStore | None = None,
) -> m.Strategy:
    """Active, stopped for new entries, or Stopped. Holding the strategy's lock.

    Making a strategy Active (or leaving it to its exits) claims its contract and product in its
    mode: the claim is one write against a unique index, so of two strategies started together on
    one contract exactly one is Active, and the other is refused with a sentence.
    """
    if state not in m.STATES:
        raise Refused("Choose Active or Stopped.", 400)
    desk, store = desk or Desk(), store or shared()
    why = None if state == m.ACTIVE else f"by you, from {surface}"

    def change() -> m.Strategy:
        before = store.strategy(strategy_id)
        if before is None:
            raise Missing(strategy_id)
        try:
            return store.set_state(strategy_id, version, state, why)
        except Taken as taken:
            raise Refused(
                words.contract_taken(before.name, taken.other or "another strategy")
            ) from taken

    changed = _write(strategy_id, change, store)
    _said(desk, changed, words.switched(changed.name, state, surface), "switched")
    return changed


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
        return strategy

    changed = _write(strategy_id, change, store)
    _said(desk, changed, words.new_link(changed.name, surface), "new_link")
    return changed, made[0]
