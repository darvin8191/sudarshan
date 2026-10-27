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
    on = f"on since {since}" if since else "on"
    if verdict.reason == rules.ON_ITS_WAY and claim_sent_at is not None:
        return (
            f"{on_its_way(position, claim_sent_at)} The kill switch is {on}, so nothing beyond "
            "the open position can be sent."
        )
    words = [f"New orders are refused: the kill switch is {on}."]
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


# -- the kill switch: turning it on, its report, resuming (killswitch.py) ---------------------

KS_NOT_HERE = (
    "The kill switch is not available on this server: the risk guard is off in this release's "
    "settings. Nothing was changed."
)
KS_SETTINGS_UNREADABLE = (
    "The risk settings could not be read, so nothing was changed. Try again in a few seconds."
)
PHRASE_WRONG = "The confirm phrase does not match. Nothing was changed."
KS_BEING_TURNED_ON = (
    "The kill switch is still being turned on. Wait for it to finish, then check Settings > Risk."
)
KS_SAVED = "Saved: it stays on if the app restarts."
KS_NOT_SAVED = (
    "The kill switch is on, but it could not be saved: it would be off after a restart of the "
    "app. Check the server."
)
KS_LIVE_NOT_REACHED = (
    "Upstox could not be reached: there is no broker login. Log in to Upstox, then check Orders "
    "and Positions."
)
KS_SANDBOX_NO_KEY = (
    "The sandbox was not touched: there is no API key. Generate one under API Key, then check "
    "the sandbox's orders."
)
KS_ORDERS_NOT_CONFIRMED = (
    "Cancel all is not confirmed: some cancels may not have gone through. Check Orders."
)
KS_GTTS_NOT_READ = (
    "The GTTs could not be read from Upstox, so none were cancelled. Check GTTs and cancel them "
    "there."
)
KS_EXIT_ALL_BUSY = (
    "Exit all was already being placed from another screen, so it was not sent again. Check "
    "Positions."
)
KS_SANDBOX_GTTS_NOT_READ = (
    "The sandbox GTTs could not be read, so none were cancelled. Check the sandbox's GTTs."
)
KS_MCP_OFF = "Remote MCP is off on this server, so there was no access to revoke."
KS_AGENT_ALREADY_STOPPED = "The agent was already stopped."
KS_AGENT_STOPPED = "The agent is stopped; resuming starts it again."
KS_TELEGRAM_OFF = "Not told on Telegram: the bot is stopped."
KS_TELEGRAM_NOT_LINKED = "Not told on Telegram: no Telegram account is linked."
KS_TELEGRAM_SENT = "Told on Telegram."
KS_TELEGRAM_FAILED = "The Telegram message could not be queued."
KS_ALREADY_OFF = "The kill switch is already off."
KS_EXIT_ALL_STILL_PLACED = "Exit all is still being placed. Wait for it, then resume."
KS_NOT_SAVED_OFF = (
    "The kill switch could not be saved as off, so it stays on. Try again in a few seconds."
)
KS_CHANGED_MEANWHILE = (
    "The kill switch changed while it was being turned off. Check Settings > Risk and try again."
)
KS_RESUMED = "Kill switch off: new orders go again."
KS_RESTARTED = "The app restarted while the kill switch was working. Check Orders and Positions."

_SURFACES = {"app": "the app", "telegram": "Telegram", "api": "a script or an alert"}
_STEPS = {
    "saved": "Saving the switch",
    "told": "Telling you on Telegram",
    "drain": "Waiting for orders already on their way",
    "live_orders": "Cancelling open orders",
    "live_gtts": "Cancelling GTTs",
    "live_exit_all": "Exit all",
    "live_orders_again": "The second look for open orders",
    "sandbox_orders": "Cancelling the sandbox's orders",
    "sandbox_gtts": "Cancelling the sandbox's GTTs",
    "sandbox_exit_all": "The sandbox's Exit all",
    "mcp": "Revoking remote MCP access",
    "agent": "Stopping the agent",
}


def _many(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def ks_already_on(since: str) -> str:
    when = f" since {since}" if since else ""
    return (
        f"The kill switch is already on{when}. New orders are refused; exits and cancels still go."
    )


def ks_turned_on(clock: str, surface: str | None) -> str:
    where = _SURFACES.get(surface or "", "SUDARSHAN")
    return (
        f"Kill switch on at {clock} from {where}. New orders are refused; exits and cancels "
        "still go. Open orders and GTTs are being cancelled."
    )


def ks_step_broke(name: str) -> str:
    if name == "saved":
        return KS_NOT_SAVED
    return f"{_STEPS.get(name, 'A step')} could not finish. Check Orders and Positions."


def ks_drained(sent: int, waiting: int) -> str:
    if not sent:
        return "No order was on its way."
    if not waiting:
        return f"{_many(sent, 'order', 'orders')} already on the way got their answer."
    return (
        f"{waiting} of {_many(sent, 'order', 'orders')} on the way got no answer in time, so "
        "Orders is looked at again after the cancel."
    )


def ks_orders_cancelled(count: int, before: dict) -> str:
    if not count:
        return "No open orders to cancel."
    said = f"{_many(count, 'open order', 'open orders')} cancelled"
    stops = int(before.get("stops") or 0)
    if stops:
        said += (
            f", including {_many(stops, 'stop-loss order', 'stop-loss orders')} that protected "
            f"{_many(int(before.get('protected') or 0), 'position', 'positions')}"
        )
    return said + "."


def ks_gtts_cancelled(cancelled: int, failed: int) -> str:
    if not cancelled and not failed:
        return "No GTTs to cancel."
    said = f"{_many(cancelled, 'GTT', 'GTTs')} cancelled."
    if failed:
        said += f" {_many(failed, 'GTT', 'GTTs')} could not be cancelled; check GTTs."
    return said


def _ended(message: str) -> str:
    message = message.strip()
    return message if message.endswith(".") else f"{message}."


def ks_exit_all(message: str) -> str:
    return f"Exit all: {_ended(message)}" if message.strip() else "Exit all sent."


def ks_swept_again(said: str) -> str:
    return f"Looked again two seconds later: {said}"


def ks_sandbox_orders(count: int, ok: bool) -> str:
    if not ok:
        return "The sandbox's open orders could not all be cancelled. Check the sandbox's orders."
    if not count:
        return "No open sandbox orders to cancel."
    return f"{_many(count, 'sandbox order', 'sandbox orders')} cancelled."


def ks_sandbox_gtts(cancelled: int, failed: int) -> str:
    if not cancelled and not failed:
        return "No sandbox GTTs to cancel."
    said = f"{_many(cancelled, 'sandbox GTT', 'sandbox GTTs')} cancelled."
    if failed:
        said += f" {_many(failed, 'sandbox GTT', 'sandbox GTTs')} could not be cancelled."
    return said


def ks_sandbox_exit_all(message: str) -> str:
    if not message.strip():
        return "The sandbox's Exit all sent."
    return f"The sandbox's Exit all: {_ended(message)}"


def ks_mcp_revoked(count: int) -> str:
    return f"Remote MCP access revoked ({_many(count, 'sign-in', 'sign-ins')})."


def ks_resume_wait(seconds: int) -> str:
    return (
        f"The kill switch can be turned off {seconds} seconds from now: it stays on for at least "
        "a minute. Try again then."
    )


def ks_report_lead(report: dict | None) -> str:
    """The first line of an activation's answer: on, and whether any step needs a look."""
    steps = (report or {}).get("steps") or []
    failed = [s for s in steps if not s.get("ok") and s.get("step") not in ("told", "mcp")]
    lead = "Kill switch on: new orders are refused; exits and cancels still go."
    if (report or {}).get("restarted"):
        return f"{lead} {(report or {})['restarted']}"
    if failed:
        return (
            f"{lead} {_many(len(failed), 'step needs', 'steps need')} a look: {failed[0]['words']}"
        )
    return lead


def ks_report_message(report: dict | None) -> str:
    """The activation's report for Telegram: the lead, then each step's words on a line."""
    steps = (report or {}).get("steps") or []
    lines = [ks_report_lead(report), ""]
    lines += [s["words"] for s in steps if s.get("step") not in ("saved", "told")]
    return "\n".join(lines)


def ks_preview(counts: dict | None, *, telegram: bool = False) -> str:
    """What turning the switch on will cancel, counted fresh (amendment R1)."""
    tick = (
        "unless you send /killswitch exit with your phrase"
        if telegram
        else "unless you also tick Exit all"
    )
    if not counts:
        return (
            "The open orders and GTTs could not be counted just now. Every open order and GTT "
            "will be cancelled, stop-loss orders too."
        )
    orders, gtts = int(counts.get("orders") or 0), counts.get("gtts")
    what = _many(orders, "open order", "open orders")
    if gtts is not None:
        what += f" and {_many(int(gtts), 'GTT', 'GTTs')}"
    said = f"{what} will be cancelled"
    stops = int(counts.get("stops") or 0)
    if stops:
        protected = int(counts.get("protected") or 0)
        verb = "protects" if stops == 1 else "protect"
        those = "That position stays" if protected == 1 else "Those positions stay"
        said += (
            f", including {_many(stops, 'stop-loss order', 'stop-loss orders')} that {verb} "
            f"{_many(protected, 'position', 'positions')}. {those} open without a stop {tick}."
        )
    else:
        said += "."
    return said[0].upper() + said[1:]


#: Every fixed sentence, for the tests that scan them.
FIXED = (
    RESUME_HOW,
    EXITS_GO,
    STATE_UNREADABLE,
    KS_NOT_HERE,
    KS_SETTINGS_UNREADABLE,
    PHRASE_WRONG,
    KS_BEING_TURNED_ON,
    KS_SAVED,
    KS_NOT_SAVED,
    KS_LIVE_NOT_REACHED,
    KS_SANDBOX_NO_KEY,
    KS_ORDERS_NOT_CONFIRMED,
    KS_GTTS_NOT_READ,
    KS_EXIT_ALL_BUSY,
    KS_SANDBOX_GTTS_NOT_READ,
    KS_MCP_OFF,
    KS_AGENT_ALREADY_STOPPED,
    KS_AGENT_STOPPED,
    KS_TELEGRAM_OFF,
    KS_TELEGRAM_NOT_LINKED,
    KS_TELEGRAM_SENT,
    KS_TELEGRAM_FAILED,
    KS_ALREADY_OFF,
    KS_EXIT_ALL_STILL_PLACED,
    KS_NOT_SAVED_OFF,
    KS_CHANGED_MEANWHILE,
    KS_RESUMED,
    KS_RESTARTED,
)
