"""Every sentence the risk layer puts in front of a trader (M5.13).

Each names the cause and the next action. None carries a status code, an exception's name, an
endpoint, a machine code, "arm" or "paper" (CLAUDE.md "written for a trader"): tests scan every
one. The machine codes go to the audit and to SUDARSHAN's own endpoints, never into a sentence.
Positions are named as the phone names them (sudarshan/books/exit_guard.trader_name).
"""

from __future__ import annotations

from sudarshan.books.exit_guard import clock, on_its_way, trader_name

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


def kill_switch_on_at_send(since: str) -> str:
    """An order let through before the kill switch went on, still waiting for its turn."""
    on = f"on since {since}" if since else "on"
    return (
        f"New orders are refused: the kill switch is {on}, and this order was still waiting "
        "to be sent when it went on, so it was not sent. If it was meant to close a position, "
        f"send it again. {EXITS_GO} {RESUME_HOW}"
    )


def _refusing_why(since: str | None, locked: tuple[float, float] | None) -> str:
    """What refuses new risk now, in a clause: the day's lock, or the kill switch (``since``:
    "HH:MM", "" when on at an unknown time, None when its state could not be read)."""
    if locked is not None:
        loss, limit = locked
        return (
            f"new orders are refused for today, the day's loss of {rupees(abs(loss))} having "
            f"reached your daily limit of {rupees(abs(limit))}"
        )
    if since is None:
        return "SUDARSHAN could not read whether the kill switch is on"
    return f"the kill switch is on since {since}" if since else "the kill switch is on"


def book_unreadable(
    pipe: str, since: str | None, *, locked: tuple[float, float] | None = None
) -> str:
    """Why an order is refused when the switch is on (or its state unknown), or the day is
    ``locked`` (its loss and limit), and the positions, which tell an exit from a new order,
    could not be read, not even in the last few seconds."""
    return (
        f"This order was refused: {_refusing_why(since, locked)}, and your positions could not "
        f"be read from {_where(pipe)}, so SUDARSHAN cannot tell whether it is an exit. Try again "
        f"in a few seconds.{_urgent(pipe)}"
    )


def check_broke(pipe: str) -> str:
    """Why an order is refused when the risk check itself failed while the switch is on."""
    return (
        "This order was refused: the risk check could not finish while the kill switch is on. "
        f"Try again in a few seconds.{_urgent(pipe)}"
    )


def order_not_found(
    pipe: str, since: str | None = "", *, locked: tuple[float, float] | None = None
) -> str:
    """Why a modify is refused while the switch is on, or the day is ``locked``, when its order
    or the positions it acts on cannot be found or read."""
    return (
        f"This change was refused: {_refusing_why(since, locked)}, and the order could not be "
        f"read from {_where(pipe)}, so SUDARSHAN cannot tell whether the change adds risk. Check "
        f"Orders and try again in a few seconds.{_urgent(pipe)}"
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
KS_ORDERS_CANCELLED_UNREAD = (
    "Upstox could not show its open orders, so Cancel all was sent to Upstox without them. "
    "Check Orders to see that none is left."
)
KS_NOTHING_TO_CANCEL_UNREAD = (
    "Upstox could not show its open orders, so Cancel all was sent to Upstox without them, and "
    "Upstox answered that no order was open: there was nothing to cancel."
)
KS_ORDERS_NOT_CANCELLED_UNREAD = (
    "Upstox could not show its open orders, and its Cancel all did not go through either. "
    "Open orders may still be working. Check Orders in the Upstox app."
)
KS_ORDERS_KEPT_UNREAD = (
    "Upstox could not show its open orders, so no cancel was sent: a cancel without them would "
    "take any stop-loss orders, and Exit all could not size an exit without them either. Your "
    "orders stand as they were. Check Orders in the Upstox app."
)
KS_NO_EXIT_ORDERS_UNREAD = (
    "Exit all was not sent, because Upstox could not show its open orders: no exit could be "
    "sized, and an order still working could close a position twice. No order was cancelled, so "
    "any stop-loss orders you placed still stand. Close your positions from the Upstox app."
)
KS_GTTS_KEPT_UNREAD = (
    "GTTs were not cancelled: Upstox could not show its open orders, so Exit all could not be "
    "sized, and a GTT stop left standing still protects its position. Cancel them in the Upstox "
    "app if you need to."
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
KS_RESUMED_LOCKED = (
    "Kill switch off, but new orders are still refused for today: the daily loss limit was "
    "reached. They go again from the next trading day."
)
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


def _sandbox_only() -> bool:
    """The guard covers the sandbox only (stage ``sandbox``): Live orders are not checked."""
    from . import flag

    return flag.stage() == flag.SANDBOX


KS_SANDBOX_ONLY = "for the sandbox only: Live orders are not checked, and Upstox was not touched"
KS_EXIT_ALL_NOT_RUN = (
    "Exit all was not run, because the kill switch was already on. To close the positions, "
    "use Exit all on Positions or send /closeall."
)


def ks_already_on(since: str, *, exit_all_asked: bool = False) -> str:
    when = f" since {since}" if since else ""
    if _sandbox_only():
        said = f"The kill switch is already on{when}, {KS_SANDBOX_ONLY}. New sandbox orders are refused."
    else:
        said = (
            f"The kill switch is already on{when}. New orders are refused; exits and cancels "
            "still go."
        )
    return f"{said} {KS_EXIT_ALL_NOT_RUN}" if exit_all_asked else said


def ks_turned_on(clock: str, surface: str | None) -> str:
    where = _SURFACES.get(surface or "", "SUDARSHAN")
    if _sandbox_only():
        return (
            f"Kill switch on at {clock} from {where}, {KS_SANDBOX_ONLY}. New sandbox orders are "
            "refused. The sandbox's open orders and GTTs are being cancelled."
        )
    return (
        f"Kill switch on at {clock} from {where}. New orders are refused; exits and cancels "
        "still go. Open orders and GTTs are being cancelled."
    )


def ks_step_broke(name: str) -> str:
    if name == "saved":
        return KS_NOT_SAVED
    return f"{_STEPS.get(name, 'A step')} could not finish. Check Orders and Positions."


def ks_drained(sent: int, waiting: int, stopped: int = 0) -> str:
    if not sent:
        return "No order was on its way."
    said = []
    if stopped:
        said.append(
            f"{_many(stopped, 'order', 'orders')} still waiting for its turn at Upstox "
            f"{'was' if stopped == 1 else 'were'} stopped before being sent."
        )
    answered = sent - waiting - stopped
    if answered > 0:
        said.append(f"{_many(answered, 'order', 'orders')} already on the way got their answer.")
    if waiting:
        said.append(
            f"{waiting} of {_many(sent, 'order', 'orders')} on the way got no answer in time, so "
            "Orders is looked at again after the cancel."
        )
    return " ".join(said)


def ks_orders_left(cancelled: int, left: int) -> str:
    """Cancelled by the cancel, and these are still working at Upstox."""
    return (
        f"{_many(cancelled, 'open order', 'open orders')} cancelled; "
        f"{_many(left, 'order', 'orders')} could not be cancelled and may still be working. "
        "Check Orders."
    )


def ks_orders_not_cancelled_unread(done: int | None, total: int | None, not_confirmed: bool) -> str:
    """The order book could not be read and Upstox's own Cancel all did not cancel every order:
    some went (it says how many), or it may have gone and cannot be told, or it did not go."""
    if not_confirmed:
        return (
            "Upstox could not show its open orders, and its Cancel all is not confirmed: it may "
            "have cancelled some or all of them. Open orders may still be working. Check "
            "Orders in the Upstox app."
        )
    if done is not None and total is not None and total > 0 and done < total:
        return (
            "Upstox could not show its open orders, and its Cancel all cancelled "
            f"{done} of {_many(total, 'order', 'orders')}. The rest may still be working. "
            "Check Orders in the Upstox app."
        )
    return KS_ORDERS_NOT_CANCELLED_UNREAD


def _cancels_before(cancelled: int, stops: int, unsure: int, gtts: int) -> str:
    """What the switch's earlier passes did, for the words of a later one: never nothing."""
    parts = []
    if cancelled:
        said = f"{_many(cancelled, 'order was', 'orders were')} cancelled"
        if stops:
            said += f", {_many(stops, 'stop-loss order', 'stop-loss orders')} among them"
        parts.append(said)
    if unsure:
        parts.append(
            f"{_many(unsure, 'cancel is', 'cancels are')} not confirmed and may have gone through"
        )
    if gtts:
        parts.append(f"{_many(gtts, 'GTT was', 'GTTs were')} cancelled")
    return "; ".join(parts) if parts else "no order was cancelled"


def ks_orders_unread_after_cancels(cancelled: int, stops: int, unsure: int, gtts: int) -> str:
    """The order book could not be read on a later pass, after an earlier one cancelled, or may
    have: no more is cancelled, and nothing is denied."""
    return (
        "Upstox could not show its open orders, so no more cancels were sent and Exit all could "
        f"not be sized. Before that, {_cancels_before(cancelled, stops, unsure, gtts)}. A position "
        "whose stop-loss order was cancelled is open with no stop. Close your positions from the "
        "Upstox app."
    )


def ks_no_exit_orders_unread(cancelled: int, stops: int, unsure: int, gtts: int) -> str:
    """Exit all not sent for an unreadable order book; what the cancel steps did goes first."""
    if not (cancelled or unsure or gtts):
        return KS_NO_EXIT_ORDERS_UNREAD
    return (
        "Exit all was not sent, because Upstox could not show its open orders: no exit could be "
        "sized, and an order still working could close a position twice. Before that, "
        f"{_cancels_before(cancelled, stops, unsure, gtts)}. A position whose stop-loss order was "
        "cancelled is open with no stop. Close your positions from the Upstox app."
    )


def ks_cancels_not_confirmed(cancelled: int, unsure: int) -> str:
    """Cancels sent whose answers did not come: they may have gone through, so they are never
    called refused."""
    lead = f"{_many(cancelled, 'open order', 'open orders')} cancelled; " if cancelled else ""
    return (
        f"{lead}{_many(unsure, 'cancel is', 'cancels are')} not confirmed: Upstox did not answer "
        "in time, so the order may be cancelled or may still be working. Check Orders."
    )


def ks_orders_unchecked(cancelled: int) -> str:
    return (
        f"{_many(cancelled, 'open order', 'open orders')} cancelled, but the order book could "
        "not be read to check that none is left. Check Orders."
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


def ks_exit_all(message: str, tried_again: bool = False) -> str:
    lead = (
        "Exit all, sent again after Upstox did not send the orders in time"
        if tried_again
        else "Exit all"
    )
    return f"{lead}: {_ended(message)}" if message.strip() else f"{lead} sent."


def ks_left_without_stops(known: int, maybe: int = 0) -> str:
    """Exit all left positions open with nothing watching them: ``known`` had a stop-loss order
    the switch cancelled; ``maybe`` may have had one (a cancel not confirmed, a cancel without
    the book, a GTT cancelled, or orders the switch never saw). Never reassures."""
    said = []
    if known == 1:
        said.append(
            "The position Exit all did not close is open with its stop-loss order cancelled."
        )
    elif known:
        said.append(
            f"The {known} positions Exit all did not close are open with their stop-loss orders "
            "cancelled."
        )
    if maybe == 1:
        said.append(
            ("Another position" if known else "The position")
            + " Exit all did not close is open, and any stop-loss order it had may have been "
            "cancelled."
        )
    elif maybe:
        said.append(
            (f"{maybe} more positions" if known else f"The {maybe} positions")
            + " Exit all did not close are open, and any stop-loss orders they had may have been "
            "cancelled."
        )
    them = "it" if known + maybe == 1 else "them"
    return " ".join(said) + f" Close {them} from the Upstox app."


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


def ks_sandbox_exit_all(message: str, closed: object = None, failed: object = None) -> str:
    """The sandbox's Exit all, in our words when it gave counts (the sandbox's own sentence reads
    "Closed 1 positions"); its message only when it gave none."""
    counted = isinstance(closed, int) and isinstance(failed, int)
    if counted and not isinstance(closed, bool) and not isinstance(failed, bool):
        if closed == 0 and failed == 0:
            return "The sandbox's Exit all found no open positions to close."
        said = f"The sandbox's Exit all closed {_many(closed, 'position', 'positions')}"
        if failed:
            said += f" and could not close {_many(failed, 'position', 'positions')}"
        return f"{said}."
    if not message.strip():
        return "The sandbox's Exit all sent."
    return f"The sandbox's Exit all: {_ended(message)}"


def ks_mcp_revoked(count: int) -> str:
    return (
        f"Remote MCP sign-ins revoked ({_many(count, 'sign-in', 'sign-ins')}). Access already "
        "granted can still work for up to 15 minutes."
    )


def ks_resume_wait(seconds: int) -> str:
    return (
        f"The kill switch can be turned off {seconds} seconds from now: it stays on for at least "
        "a minute. Try again then."
    )


def ks_resume_refused(said: str) -> str:
    """The audit's line for a resume that was refused, with what the owner was told."""
    return f"Resume refused. {said}"


def ks_report_lead(report: dict | None) -> str:
    """The first line of an activation's answer: on, and whether any step needs a look."""
    steps = (report or {}).get("steps") or []
    failed = [s for s in steps if not s.get("ok") and s.get("step") not in ("told", "mcp")]
    if _sandbox_only():
        lead = f"Kill switch on {KS_SANDBOX_ONLY}. New sandbox orders are refused."
    else:
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
    gtt_stops = int(counts.get("gtt_stops") or 0)
    if stops or gtt_stops:
        protected = int(counts.get("protected") or 0)
        verb = "protects" if stops + gtt_stops == 1 else "protect"
        those = "That position stays" if protected == 1 else "Those positions stay"
        kinds = []
        if stops:
            kinds.append(_many(stops, "stop-loss order", "stop-loss orders"))
        if gtt_stops:
            kinds.append(_many(gtt_stops, "stop-loss GTT", "stop-loss GTTs"))
        said += (
            f", including {' and '.join(kinds)} that {verb} "
            f"{_many(protected, 'position', 'positions')}. {those} open without a stop {tick}."
        )
    else:
        said += "."
    return said[0].upper() + said[1:]


# -- the routes and Telegram's /killswitch (blueprints/sudarshan_risk.py, telegram.py) ----------

OWNER_ONLY = "Only the owner's account can do this."
TOO_MANY_REQUESTS = "Too many requests. Wait a minute, then try again."
API_KEY_NOT_VALID = "The API key is not valid, so nothing was changed."
EXIT_ALL_NOT_UNDERSTOOD = "Exit all must be given as true or false. Nothing was changed."
AUDIT_UNREADABLE = "The risk history could not be read. Try again in a few seconds."

_UPSTOX_SEGMENTS = {
    "NSE_EQ": "NSE cash",
    "BSE_EQ": "BSE cash",
    "NSE_FO": "NSE F&O",
    "BSE_FO": "BSE F&O",
    "NCD_FO": "NSE currency",
    "BCD_FO": "BSE currency",
    "MCX_FO": "MCX",
    "NSE_COM": "NSE commodity",
}


def upstox_segment(code: str) -> str:
    """A segment of Upstox's own kill switch, as a trader names it."""
    return _UPSTOX_SEGMENTS.get(code, code.replace("_", " "))


TG_ONLY_YOUR_CHAT = "This command works only in your own chat with the bot."
TG_OFF = "The kill switch is off."
TG_USAGE = (
    "Send /killswitch on followed by your phrase to stop new orders and cancel open orders and "
    "GTTs; /killswitch exit followed by your phrase to also exit every position; /killswitch off "
    "followed by your phrase to resume."
)
TG_RESUME_HOW = (
    "To resume, send /killswitch off followed by your phrase, a minute or more after it went on."
)
TG_STILL_WORKING = "Still working. Send /killswitch to see where it is."
TG_BROKE = "The kill switch command could not finish. Check Settings > Risk in the app."

# -- the risk settings (settings_rules.py) ---------------------------------------------------------

SETTINGS_NOT_UNDERSTOOD = "These settings could not be read. Nothing was saved."
PHRASE_INVALID = "The confirm phrase must be 1 to 40 letters, digits or spaces. Nothing was saved."
SETTINGS_UNCHANGED = "Nothing to change."
LOOSEN_NEEDS_PHRASE = "Loosening a limit needs your confirm phrase. Nothing was saved."
PHRASE_CHANGE_NEEDS_PHRASE = (
    "Changing the confirm phrase needs the phrase it replaces. Nothing was saved."
)
SETTINGS_SAVED = "Saved."
SETTINGS_CHANGED_MEANWHILE = (
    "Your risk settings were changed from another screen just now, so nothing was saved. Check "
    "them and try again."
)
SETTINGS_NOT_SAVED = "The risk settings could not be saved. Try again in a few seconds."

_SETTING_LABELS = {
    "daily_loss_limit": "the daily loss limit",
    "band_on": "the price band",
    "band_opt_pct": "the options band",
    "band_opt_min_rs": "the options band's minimum",
    "band_fut_pct": "the futures band",
    "band_cash_pct": "the cash band",
    "band_both_sides": "checking both sides",
    "size_on": "the order size limit",
    "max_lots_fo": "lots per F&O order",
    "max_qty_cash": "shares per cash order",
    "burst_on": "the burst brake",
    "burst_n": "orders in a burst",
    "burst_window_s": "the burst window",
    "confirm_phrase": "the confirm phrase",
    "revoke_mcp": "revoking remote MCP access",
}
_SETTING_RANGES = {
    "daily_loss_limit": "The daily loss limit must be at least ₹1.",
    "band_opt_pct": "The options band must be between 1 and 100%.",
    "band_opt_min_rs": "The options band's minimum must be between ₹0 and ₹10,000.",
    "band_fut_pct": "The futures band must be between 0.1 and 50%.",
    "band_cash_pct": "The cash band must be between 0.1 and 50%.",
    "max_lots_fo": "Lots per F&O order must be a whole number from 1 to 10,000.",
    "max_qty_cash": "Shares per cash order must be a whole number from 1 to 1,00,00,000.",
    "burst_n": "Orders in a burst must be a whole number from 1 to 10,000.",
    "burst_window_s": "The burst window must be between 1 and 3,600 seconds.",
}


def setting_invalid(name: str) -> str:
    """Why one setting cannot be saved."""
    said = _SETTING_RANGES.get(name) or "A switch must be on or off."
    return f"{said} Nothing was saved."


def settings_changed(names: list[str], looser: bool) -> str:
    """The audit's line for a change of settings (the phrase itself never)."""
    listed = ", ".join(_SETTING_LABELS.get(name, name) for name in names)
    return f"Risk settings changed{', loosened' if looser else ''}: {listed}."


# -- the daily loss limit (M5.14, daily.py) ------------------------------------------------------


def rupees(amount: float) -> str:
    """₹4,000 or ₹1,23,456.50: India's grouping, paise only when there are some."""
    sign = "-" if amount < 0 else ""
    paise = round(abs(amount) * 100)
    whole, part = divmod(paise, 100)
    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join([*groups, tail])
    return f"{sign}₹{digits}" + (f".{part:02d}" if part else "")


def daily_locked(
    loss: float,
    limit: float,
    verdict: rules.Verdict,
    *,
    position: tuple[str, str, str],
    side: int | None,
    claim_sent_at: float | None = None,
) -> str:
    """Why a new order is refused once the day's loss has reached the limit (amendment R5); for
    one whose position is already being exited, that exit."""
    reached = (
        f"the day's loss of {rupees(abs(loss))} has reached your daily limit of "
        f"{rupees(abs(limit))} (Settings > Risk)"
    )
    if verdict.reason == rules.ON_ITS_WAY and claim_sent_at is not None:
        return (
            f"{on_its_way(position, claim_sent_at)} New orders are refused for today: {reached}, "
            "so nothing beyond the open position can be sent."
        )
    words = [f"New orders are refused for today: {reached}."]
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
    words += [EXITS_GO, "New orders open again tomorrow."]
    return " ".join(words)


def daily_unreadable(pipe: str) -> str:
    """Why an order is refused while the day's P&L cannot be read (a two-leg GTT: new risk
    whatever the book says)."""
    return (
        f"New orders are paused: today's P&L could not be read from {_where(pipe)}, so the daily "
        f"loss limit cannot be checked. {EXITS_GO} Try again in a few seconds."
    )


def daily_lock_told(loss: float, limit: float, pipe: str) -> str:
    """Telegram, once, when the day locks."""
    where = "" if pipe == "live" else " in the sandbox"
    return (
        f"Daily loss limit reached{where}: the day's loss is {rupees(abs(loss))} against your "
        f"limit of {rupees(abs(limit))}. New orders are refused until tomorrow; exits and "
        "cancels still go."
    )


def daily_loosened_told(before: float, after: float | None) -> str:
    """Telegram, when the daily limit is loosened (raised or turned off) on a day it has
    locked."""
    if after is None:
        return (
            f"The daily loss limit of {rupees(before)} was turned off after it locked today. "
            "Today's lock stays: new orders open again tomorrow."
        )
    return (
        f"The daily loss limit was raised from {rupees(before)} to {rupees(after)} after it "
        "locked today. Today's lock stays: new orders open again tomorrow, under the new limit."
    )


def daily_not_checked(pipe: str) -> str:
    """The daily limit could not be checked for an order, which went (the audit and banner)."""
    return (
        f"Daily loss limit not being checked: positions cannot be read from {_where(pipe)}. "
        "An order whose book cannot be read goes, because it may be an exit."
    )


# -- the limit price band (M20.7, band.py) -------------------------------------------------------


def price(amount: float) -> str:
    """A price as an order ticket shows it, paise always: ₹186.00, ₹1,234.50."""
    shown = rupees(round(amount, 2))
    return shown if "." in shown else f"{shown}.00"


def _band_size(pct: float, floor: float) -> str:
    return f"{pct:g}%, at least {rupees(floor)}" if floor else f"{pct:g}%"


def band_refused(
    breach, verdict: rules.Verdict | None = None, *, position: tuple[str, str, str] | None = None
) -> str:
    """Why a priced order too far from the market is refused (design section 6): the price, how
    far it is from what, and the band, so the trader sees the mistake at once; for one that would
    close a position and open it the other way, the exit that fits, which is never held to it."""
    side = "buy" if breach.side == rules.BUY else "sell"
    where = "above" if breach.above else "below"
    away = f"{breach.away_pct:g}%"
    size = _band_size(breach.band_pct, breach.floor)
    if breach.what == "limit":
        said = (
            f"Your stop-loss {side} price {price(breach.price)} is {away} {where} its trigger "
            f"({price(breach.reference)}); the price band is {size} (Settings > Risk). Check the "
            "price."
        )
    elif breach.what == "trigger":
        said = (
            f"Your stop-loss {side} trigger {price(breach.price)} is already {away} {where} the "
            f"market ({price(breach.reference)}), so it would go at once; the price band is "
            f"{size} (Settings > Risk). Check the trigger."
        )
    else:
        said = (
            f"Your {side} price {price(breach.price)} is {away} {where} the market "
            f"({price(breach.reference)}); the price band is {size} (Settings > Risk). Check "
            "the price."
        )
    if verdict is not None and position is not None and verdict.reason == rules.CROSSES:
        if verdict.room:
            said += (
                f" This order would close {trader_name(position)} and open it the other way; "
                f"to exit, {side} {verdict.room}: an exit is never held to the band."
            )
    return f"{said} Nothing was sent."


#: The band could not be checked for an order, which went (the audit and the Risk page).
BAND_NOT_CHECKED = (
    "Price band not checked for one order: the market price could not be read from Upstox. The "
    "order went; the exchange's own price check still applies."
)


def checks_not_run(pipe: str) -> str:
    """The owner's limits could not be checked for an order, which went (the audit)."""
    return (
        f"Risk checks skipped for one order: positions cannot be read from {_where(pipe)}. An "
        "order whose book cannot be read goes, because it may be an exit."
    )


# -- the order size limit (M20.7, size.py) and batches held whole (batch.py) ----------------------


def _grouped(count: float) -> str:
    """A count the Indian way: 1,500 shares, 1,00,000 shares; a part lot as it is: 2.31 lots."""
    if count != int(count):
        return f"{count:g}"
    return rupees(int(count)).removeprefix("₹")


def _amount(unit: str, count: float) -> str:
    one = "lot" if unit == "lots" else "share"
    return f"{_grouped(count)} {one if count == 1 else unit}"


def size_refused(
    excess,
    verdict: rules.Verdict,
    *,
    position: tuple[str, str, str],
    side,
    claim_sent_at: float | None = None,
) -> str:
    """Why an order is refused for its size: what it adds and the owner's cap; for one whose
    position is already being exited, that exit."""
    cap = (
        f"{_amount('lots', excess.cap)} per F&O order"
        if excess.unit == "lots"
        else f"{_amount('shares', excess.cap)} per cash order"
    )
    if verdict.reason == rules.ON_ITS_WAY and claim_sent_at is not None:
        return (
            f"{on_its_way(position, claim_sent_at)} Beyond it, this order would open "
            f"{_amount(excess.unit, excess.amount)} the other way; your limit is {cap} (Settings > "
            "Risk). Nothing was sent."
        )
    if verdict.reason in (rules.CROSSES, rules.ON_ITS_WAY) and verdict.room:
        return (
            f"This order would close {trader_name(position)} and open "
            f"{_amount(excess.unit, excess.amount)} the other way; your limit is {cap} (Settings > "
            f"Risk). To exit, {_verb(side)} {verdict.room}. Nothing was sent."
        )
    return (
        f"This order is {_amount(excess.unit, excess.amount)} of {trader_name(position)}; your "
        f"limit is {cap} (Settings > Risk). Check the quantity. Nothing was sent."
    )


#: The size limit could not be checked for an order, which went (the audit and the Risk page).
SIZE_NOT_CHECKED = (
    "Order size not checked for one order: its lot size could not be read from the master "
    "contract. The order went."
)


def together_refused(what: str, part: int | None, said: str, exits: list[str] = ()) -> str:
    """Why a whole batch is refused: one part would be refused alone, and nothing of it was
    sent, so no part of a hedge goes without the others; a split, as the one order it was asked
    as. The exits the batch carried are named (they were not sent either), so the owner can
    send them on their own (the coordinator's decisions b and c)."""
    reason = said.removesuffix(" Nothing was sent.")
    if what == "split":
        return (
            "Nothing of this split order was sent: it is held to your limits as the one order it "
            f"is. {reason}"
        )
    where, name = ("this basket", "Order") if what == "basket" else ("this multi-leg order", "Leg")
    if part is None:  # the batch as a whole: the burst brake counts it once
        said = f"Nothing in {where} was sent. {reason}"
    else:
        said = (
            f"Nothing in {where} was sent, so no part goes without the others. {name} {part}: "
            f"{reason}"
        )
    if exits:
        which = "Its exit was" if len(exits) == 1 else "Its exits were"
        said += (
            f" {which} not sent either: {', '.join(exits)}. An exit sent on its own always goes."
        )
    return said


def batch_exit(label: str, number: int, side: int | None, quantity: int | None, position) -> str:
    """One exit a refused batch carried: "Order 1 (sell 65 NIFTY 25900 CE)"."""
    return f"{label} {number} ({_verb(side)} {quantity or 0} {trader_name(position)})"


# -- the burst brake (M5.14) --------------------------------------------------------------------------


def _window(seconds: float) -> str:
    """60 seconds as "1 minute", 90 as "90 seconds"."""
    whole = int(seconds)
    if whole == seconds and whole >= 60 and whole % 60 == 0:
        return _many(whole // 60, "minute", "minutes")
    return f"{seconds:g} seconds"


def reversal_cut(
    position: tuple[str, str, str],
    side: int | None,
    sent: int,
    beyond: int,
    *,
    since: str | None = "",
    locked: tuple[float, float] | None = None,
    unread: str | None = None,
) -> str:
    """A reversing order under the kill switch or the day's lock, or while the day's P&L cannot
    be read (``unread``: the pipe): its exit was sent, the part beyond it refused (the
    coordinator's decision a)."""
    name = trader_name(position)
    were = "was" if beyond == 1 else "were"
    if unread is not None:
        why = (
            f"new orders are paused, because today's P&L could not be read from {_where(unread)} "
            "and the daily loss limit cannot be checked. Try again in a few seconds."
        )
    elif locked is not None:
        loss, limit = locked
        why = (
            f"the day's loss of {rupees(abs(loss))} has reached your daily limit of "
            f"{rupees(abs(limit))} (Settings > Risk). New orders open again tomorrow."
        )
    elif since is None:
        why = f"SUDARSHAN could not read whether the kill switch is on. {RESUME_HOW}"
    else:
        on = f"on since {since}" if since else "on"
        why = f"the kill switch is {on}. {RESUME_HOW}"
    return (
        f"Only the exit was sent: {_verb(side)} {sent} to close {name}. The other {beyond}, which "
        f"would have opened it the other way, {were} refused: {why}"
    )


def burst_refused(count: int, window_s: float, again_at: float) -> str:
    """Why a new order is refused by the burst brake, and when new orders go again."""
    return (
        f"New orders are paused by your burst brake: {_many(count, 'new order', 'new orders')} "
        f"went in the last {_window(window_s)}, the most it allows (Settings > Risk). {EXITS_GO} "
        f"New orders go again at {clock(again_at)}."
    )


def burst_told(count: int, window_s: float, again_at: float, pipe: str) -> str:
    """Telegram, once a window, when the burst brake engages."""
    where = "" if pipe == "live" else " in the sandbox"
    return (
        f"Burst brake{where}: {_many(count, 'new order', 'new orders')} went in "
        f"{_window(window_s)}, so new orders are paused until {clock(again_at)}. Exits and "
        "cancels still go. Check what is sending them."
    )


def sandbox_gtt_refused(name: str, paused: bool = False) -> str:
    """Telegram, once, when a sandbox GTT's order is refused as it triggers: the GTT is
    rejected rather than tried again on every tick. ``paused``: new orders were paused because
    the day's P&L could not be read (the order's own sentence says to try again; a GTT cannot)."""
    if paused:
        return (
            f"Sandbox GTT on {name} rejected: when it triggered, new orders were paused because "
            "today's P&L could not be read from the sandbox. It will not fire again, and it is "
            "not tried again by itself; place it again if you still want it."
        )
    return (
        f"Sandbox GTT on {name} rejected: when it triggered, its order was refused by your risk "
        "settings (the kill switch, the daily loss limit or another limit). It will not fire "
        "again; place it again if you still want it. Check Settings > Risk."
    )


def refused_again(untold: int) -> str:
    """What a refusal told on Telegram adds about those like it not told in the last minute."""
    if not untold:
        return ""
    times = "time" if untold == 1 else "times"
    return f" Refused {untold} more {times} like this since the last message."


def refusal_told(said: str, untold: int, pipe: str, *, cut: int | None = None) -> str:
    """Telegram, at most once a minute per symbol and reason: a risk refusal of an order that
    did not come from the app's own screens (the coordinator's decision b), in its own words,
    which name any exit it left unsent."""
    where = "" if pipe == "live" else " in the sandbox"
    title = "Order cut to its exit" if cut is not None else "Order not sent"
    return f"{title}{where} (Settings > Risk)\n{said}{refused_again(untold)}"


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
    KS_ORDERS_CANCELLED_UNREAD,
    KS_NOTHING_TO_CANCEL_UNREAD,
    KS_ORDERS_NOT_CANCELLED_UNREAD,
    KS_ORDERS_KEPT_UNREAD,
    KS_NO_EXIT_ORDERS_UNREAD,
    KS_GTTS_KEPT_UNREAD,
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
    KS_RESUMED_LOCKED,
    KS_EXIT_ALL_NOT_RUN,
    KS_RESTARTED,
    OWNER_ONLY,
    TOO_MANY_REQUESTS,
    API_KEY_NOT_VALID,
    EXIT_ALL_NOT_UNDERSTOOD,
    AUDIT_UNREADABLE,
    TG_ONLY_YOUR_CHAT,
    TG_OFF,
    TG_USAGE,
    TG_RESUME_HOW,
    TG_STILL_WORKING,
    TG_BROKE,
    SETTINGS_NOT_UNDERSTOOD,
    PHRASE_INVALID,
    SETTINGS_UNCHANGED,
    LOOSEN_NEEDS_PHRASE,
    PHRASE_CHANGE_NEEDS_PHRASE,
    SETTINGS_SAVED,
    SETTINGS_CHANGED_MEANWHILE,
    SETTINGS_NOT_SAVED,
    BAND_NOT_CHECKED,
    SIZE_NOT_CHECKED,
)
