"""Every sentence the risk layer puts in front of a trader (M5.13).

Each names the cause and the next action. None carries a status code, an exception's name, an
endpoint, a machine code, "arm" or "paper" (CLAUDE.md "written for a trader"): tests scan every
one. The machine codes go to the audit and to SUDARSHAN's own endpoints, never into a sentence.
Positions are named as the phone names them (sudarshan/books/exit_guard.trader_name).
"""

from __future__ import annotations

from sudarshan.books.exit_guard import on_its_way, trader_name

from . import rules

RESUME_HOW = "To trade again, resume from Settings > Risk or send /killswitch in Telegram."
EXITS_GO = "Exits and cancels still go."

STATE_UNREADABLE = (
    "New orders are refused: SUDARSHAN could not read its risk settings, so it cannot tell "
    "whether the kill switch is on. Exits and cancels still go. Try again in a few seconds; if "
    "this keeps happening, the risk database on the server needs a look."
)


def _where(pipe: str) -> str:
    return "Upstox" if pipe == "live" else "the sandbox"


def _urgent(pipe: str) -> str:
    return " For an urgent exit, use the Upstox app." if pipe == "live" else ""


def _verb(side: int | None) -> str:
    return "buy" if side == rules.BUY else "sell"


def kill_switch_on(
    since: str,
    verdict: rules.Verdict,
    *,
    position: tuple[str, str, str],
    side: int | None,
    claim_sent_at: float | None = None,
) -> str:
    """Why a new order is refused while the kill switch is on (``since``: "HH:MM", India).

    The sentence says what would let the order go: for one that would cross through zero, the
    exit that fits; for one whose position is already being exited, that exit.
    """
    if verdict.reason == rules.ON_ITS_WAY and claim_sent_at is not None:
        return (
            f"{on_its_way(position, claim_sent_at)} The kill switch is on since {since}, so "
            "nothing beyond the open position can be sent."
        )
    words = [f"New orders are refused: the kill switch is on since {since}."]
    if verdict.reason == rules.CROSSES:
        words.append(
            f"This order would close {trader_name(position)} and open it the other way; to "
            f"exit, {_verb(side)} {verdict.room}."
        )
    elif verdict.reason == rules.OCO:
        words.append(
            "A two-leg GTT opens a position at Upstox, so it counts as a new order; for a stop "
            "or a target on a position you hold, place a single GTT."
        )
    words += [EXITS_GO, RESUME_HOW]
    return " ".join(words)


def book_unreadable(pipe: str, since: str | None) -> str:
    """Why an order is refused when the switch is on (or its state unknown) and the positions,
    which tell an exit from a new order, could not be read."""
    why = (
        f"the kill switch is on since {since}"
        if since
        else "SUDARSHAN could not read whether the kill switch is on"
    )
    return (
        f"This order was refused: {why}, and your positions could not be read from "
        f"{_where(pipe)}, so SUDARSHAN cannot tell whether it is an exit. Try again in a few "
        f"seconds.{_urgent(pipe)}"
    )


def check_broke(pipe: str) -> str:
    """Why an order is refused when the risk check itself failed while the switch is on."""
    return (
        "This order was refused: the risk check could not finish while the kill switch is on. "
        f"Try again in a few seconds.{_urgent(pipe)}"
    )


def order_not_found(pipe: str) -> str:
    """Why a modify is refused while the switch is on when its order cannot be found or read."""
    return (
        "This change was refused: the kill switch is on and the order could not be read from "
        f"{_where(pipe)}, so SUDARSHAN cannot tell whether the change adds risk. Check Orders and "
        f"try again in a few seconds.{_urgent(pipe)}"
    )


#: Every fixed sentence, for the tests that scan them.
FIXED = (RESUME_HOW, EXITS_GO, STATE_UNREADABLE)
