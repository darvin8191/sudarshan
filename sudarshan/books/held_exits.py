"""What the trader is told while a Live strategy exit is held, and how often (M6.9, O1).

Since GA1.1 a strategy exit whose size Upstox's books cannot confirm is held, not sent
(``sudarshan.books.surface_exits``). The engine tries it again on every tick, and until this
module each try wrote an order row, a ``leg_exit_held`` event and a push to the page, while the
trader, away from the run's Events tab, was told nothing.

One record per leg of a run, kept while the leg keeps being held:

- The first hold of a leg writes one event. Later holds of the same leg write none.
- The first hold for a size that could not be confirmed is logged at critical and told to the
  owner on Telegram; while it lasts he is told again at most once in ``REMIND_S``.
- When the books do answer and say the leg cannot be closed (nothing held, held the other way,
  held in another product), that is told once, with what the books say, and never repeated: it
  does not end by waiting.
- When the exit goes after he was told of a hold, he is told once that it went.
- A hold that is only a wait for another exit (one being placed, an earlier one not seen ended,
  a market order working on the closing side) is one event and no Telegram message, as before.

Deciding is done under one real lock held for dict work only (two ticks racing on one leg send
one first message; a tick can come from a green thread or a real one). Telling is queued through
the path the risk guard and the kill switch use (``sudarshan.guard.gate._tell_owner``, OpenAlgo's
alert executor), after the lock is released: nothing here waits for Telegram, and nothing here
starts a thread, a timer or a loop. Nothing here places, sizes or stops an order: a failure in
this module is logged and the hold is recorded as it was before.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from sudarshan.books import exit_guard
from sudarshan.books.surface_exits import ELSEWHERE, NO_POSITION, OTHER_WAY, UNCONFIRMED
from utils import real_threading
from utils.logging import get_logger

logger = get_logger(__name__)

#: The shortest time between two Telegram messages about one leg's unconfirmed size.
REMIND_S = 60.0
#: A leg not held for this long has stopped asking (its exit went another way, its run ended, or
#: no tick came): its record is dropped, and a later hold of it is a first hold again. The engine
#: asks on every tick, so a hold that lasts is seen far more often than this.
STALE_S = 300.0

#: What the books said when they answered: told once, never repeated.
_SAID_ONCE = (NO_POSITION, OTHER_WAY, ELSEWHERE)

FIRST, AGAIN, ONCE, GONE = "first", "again", "once", "gone"

#: What made the exit due, by the order kind the engine gives it (database.strategy_module_db).
_DUE = {
    "exit_sl": "The stop loss fired",
    "exit_trail": "The trailing stop fired",
    "exit_target": "The target was reached",
    "exit_overall_sl": "The strategy's overall stop loss fired",
    "exit_overall_target": "The strategy's overall target was reached",
    "exit_lock_profit": "The strategy's profit lock fired",
    "exit_daily_loss_limit": "The strategy's daily loss limit was reached",
    "exit_eod": "The end-of-day square-off was due",
    "exit_expiry": "The expiry square-off was due",
    "exit_close_all": "The run was asked to close every leg",
    "exit_leg_manual": "You asked to close this leg",
    "exit_signal": "An exit alert arrived",
}
_DUE_OTHER = "An exit was due"

CLOSE_IN_UPSTOX = "If you want out now, close it in the Upstox app."
NO_POSITION_SAID = "Upstox shows no position for this leg"
RUN_STILL_LISTS = "The run still shows the leg as open; check Positions in the Upstox app."


@dataclass
class _Hold:
    since: float
    seen: float
    told: float = 0.0  # when the owner was last told the size is not confirmed
    alerted: bool = False  # he was told of it on Telegram at least once
    said_once: set = field(default_factory=set)


@dataclass(frozen=True)
class Note:
    """What one hold calls for: an event row, a Telegram message (``tell``: FIRST, AGAIN, ONCE or
    GONE, or "" for none), and how long the size has been unconfirmed, in whole minutes."""

    event: bool = False
    tell: str = ""
    minutes: int = 0


class Holds:
    """The legs being held, by ``(run id, leg id)``. No I/O; the clock is passed in."""

    def __init__(self) -> None:
        self._lock = real_threading.Lock()
        self._held: dict[tuple, _Hold] = {}

    def note(self, key: tuple, why: str, now: float) -> Note:
        """One hold of ``key`` for ``why``; says what to write and whom to tell, once."""
        with self._lock:
            hold = self._held.get(key)
            if hold is not None and now - hold.seen > STALE_S:
                hold = None
            first = hold is None
            if first:
                for each, old in list(self._held.items()):
                    if now - old.seen > STALE_S:
                        del self._held[each]
                hold = self._held[key] = _Hold(since=now, seen=now)
            hold.seen = now
            if why == UNCONFIRMED:
                if not hold.alerted:
                    hold.alerted, hold.told, hold.since = True, now, now
                    return Note(event=True, tell=FIRST)
                if now - hold.told >= REMIND_S:
                    hold.told = now
                    return Note(tell=AGAIN, minutes=int((now - hold.since) // 60))
                return Note()
            if why in _SAID_ONCE:
                if why in hold.said_once:
                    return Note()
                hold.said_once.add(why)
                after = hold.alerted and why == NO_POSITION
                return Note(event=True, tell=GONE if after else ONCE)
            return Note(event=first)

    def clear(self, key: tuple, now: float) -> int | None:
        """The exit of ``key`` went. The whole minutes it was held when the owner had been told
        its size was not confirmed (so he is told it went), else None."""
        with self._lock:
            hold = self._held.pop(key, None)
        if hold is None or not hold.alerted or now - hold.seen > STALE_S:
            return None
        return int((now - hold.since) // 60)

    def run_is_held(self, run_id: object, now: float) -> bool:
        with self._lock:
            return any(
                key[0] == run_id and now - hold.seen <= STALE_S for key, hold in self._held.items()
            )

    def reset(self) -> None:
        with self._lock:
            self._held.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._held)


HOLDS = Holds()


# ---- the words ---------------------------------------------------------------------------------
def _name(order: dict[str, Any]) -> str:
    return exit_guard.trader_name(
        exit_guard.position_of(order.get("exchange"), order.get("symbol"), order.get("product"))
    )


def _what(order: dict[str, Any], quantity: object = None) -> str:
    """ "SELL 65 NIFTY 25900 CE"."""
    return f"{order.get('action')} {quantity or order.get('quantity')} {_name(order)}"


def _head(title: str, leg_id: object, order: dict[str, Any], said: str) -> str:
    return f"{title}\nStrategy: {order.get('strategy') or '(no name)'}\nLeg {leg_id}: {said}"


def _lasted(minutes: int) -> str:
    return "under a minute" if minutes < 1 else f"{minutes} min"


def first_text(leg_id: object, kind: str, order: dict[str, Any]) -> str:
    return (
        f"{_head('Exit NOT sent (Live)', leg_id, order, _what(order))}\n"
        f"{_DUE.get(kind, _DUE_OTHER)}, but the exit was not sent because SUDARSHAN could not "
        "confirm the position size from Upstox. SUDARSHAN keeps trying and will tell you when "
        f"the exit goes. {CLOSE_IN_UPSTOX}"
    )


def again_text(leg_id: object, order: dict[str, Any], minutes: int) -> str:
    return (
        f"{_head(f'Exit still NOT sent (Live), {_lasted(minutes)} now', leg_id, order, _what(order))}\n"
        f"SUDARSHAN still cannot confirm the position size from Upstox. {CLOSE_IN_UPSTOX}"
    )


def once_text(leg_id: object, kind: str, order: dict[str, Any], why: str, sentence: str) -> str:
    due = _DUE.get(kind, _DUE_OTHER)
    if why == NO_POSITION:
        said = f"{due}, but {NO_POSITION_SAID}, so no exit was sent. {RUN_STILL_LISTS}"
    else:
        said = f"{due}, but the exit was not sent. {sentence}"
    return f"{_head('Exit NOT sent (Live)', leg_id, order, _what(order))}\n{said}"


def gone_text(leg_id: object, order: dict[str, Any]) -> str:
    return (
        f"{_head('Nothing left to close (Live)', leg_id, order, _name(order))}\n"
        f"{NO_POSITION_SAID} now, so no exit was sent and none is needed. {RUN_STILL_LISTS}"
    )


def sent_text(leg_id: object, order: dict[str, Any], quantity: object, minutes: int) -> str:
    return (
        f"{_head('Exit sent (Live)', leg_id, order, _what(order, quantity))}\n"
        f"The exit that was held for {_lasted(minutes)} has now been sent. Check Orders for "
        "its fill."
    )


def event_line(leg_id: object, order: dict[str, Any], why: str, sentence: str) -> tuple[str, str]:
    """The run's Events line for the one event a hold writes, and its severity."""
    if why == UNCONFIRMED:
        return (
            f"Exit NOT sent for leg {leg_id} ({_what(order)}): SUDARSHAN could not confirm the "
            "position size from Upstox. It keeps trying and writes here when the exit goes. "
            "Close the position in the Upstox app if you want out now.",
            "critical",
        )
    if why == NO_POSITION:
        return (
            f"No exit sent for leg {leg_id} ({_name(order)}): {NO_POSITION_SAID}. "
            f"{RUN_STILL_LISTS}",
            "warn",
        )
    return f"Exit NOT sent for leg {leg_id} ({_what(order)}): {sentence}", "critical"


# ---- what the strategy module calls ------------------------------------------------------------
@dataclass(frozen=True)
class Said:
    """What the caller records for one held dispatch: whether to write an event at all, its line
    (None: the caller's own "held, to be sent again" line) and its severity."""

    event: bool = True
    message: str | None = None
    severity: str = "warn"


def _tell(text: str) -> None:
    """Queue one Telegram message to the owner; never waits for Telegram and never raises."""
    try:
        from sudarshan.guard import gate

        gate._tell_owner(text)
    except Exception:
        logger.exception("A held strategy exit could not be told on Telegram")


def _why(result: Any) -> str:
    response = getattr(result, "response", None)
    return str(response.get("why") or "") if isinstance(response, dict) else ""


def held(run_id: object, leg_id: object, kind: str, order: dict[str, Any], result: Any) -> Said:
    """One held Live exit of a run's leg (a dispatch marked ``held``, or the kept refusal the
    engine found before it wrote a row). Tells the owner when the rules above say so."""
    try:
        why = _why(result)
        sentence = str(getattr(result, "error", "") or "")
        note = HOLDS.note((run_id, leg_id), why, time.monotonic())
        if note.tell == FIRST:
            logger.critical(
                f"Live exit NOT sent for run {run_id} leg {leg_id} ({_what(order)}): its size "
                "could not be confirmed from Upstox's books; the leg stays open and managed"
            )
            _tell(first_text(leg_id, kind, order))
        elif note.tell == AGAIN:
            _tell(again_text(leg_id, order, note.minutes))
        elif note.tell == ONCE:
            log = logger.warning if why == NO_POSITION else logger.critical
            log(f"Live exit not sent for run {run_id} leg {leg_id} ({_what(order)}): {why}")
            _tell(once_text(leg_id, kind, order, why, sentence))
        elif note.tell == GONE:
            logger.warning(f"Run {run_id} leg {leg_id}: Upstox shows no position; no exit sent")
            _tell(gone_text(leg_id, order))
        if not note.event:
            return Said(event=False)
        if why == UNCONFIRMED or why in _SAID_ONCE:
            return Said(True, *event_line(leg_id, order, why, sentence))
        return Said()
    except Exception:
        logger.exception("A held strategy exit could not be noted")
        return Said()


def sent(run_id: object, leg_id: object, order: dict[str, Any], result: Any) -> str:
    """A Live exit of a run's leg went. Ends its hold; when the owner had been told the size was
    not confirmed he is told once that it went, and the words to add to the event are returned
    ("" otherwise)."""
    try:
        minutes = HOLDS.clear((run_id, leg_id), time.monotonic())
        if minutes is None:
            return ""
        response = getattr(result, "response", None)
        quantity = response.get("sent_quantity") if isinstance(response, dict) else None
        _tell(sent_text(leg_id, order, quantity, minutes))
        return f"; it had been held {_lasted(minutes)}"
    except Exception:
        logger.exception("A strategy exit sent after a hold could not be told")
        return ""


def run_is_held(run_id: object) -> bool:
    """Whether a leg of this run has an exit being held now (its stop is being tried again)."""
    try:
        return HOLDS.run_is_held(run_id, time.monotonic())
    except Exception:
        return False
