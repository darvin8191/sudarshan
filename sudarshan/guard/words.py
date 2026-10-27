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


def ks_resume_refused(said: str) -> str:
    """The audit's line for a resume that was refused, with what the owner was told."""
    return f"Resume refused. {said}"


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
) -> str:
    """Why a new order is refused once the day's loss has reached the limit (amendment R5)."""
    words = [
        f"New orders are refused for today: the day's loss of {rupees(abs(loss))} has reached "
        f"your daily limit of {rupees(abs(limit))} (Settings > Risk)."
    ]
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


def band_refused(breach) -> str:
    """Why a priced order too far from the market is refused (design section 6): the price, how
    far it is from what, and the band, so the trader sees the mistake at once."""
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


def size_refused(excess, verdict: rules.Verdict, *, position: tuple[str, str, str], side) -> str:
    """Why an order is refused for its size: what it adds and the owner's cap."""
    cap = (
        f"{_amount('lots', excess.cap)} per F&O order"
        if excess.unit == "lots"
        else f"{_amount('shares', excess.cap)} per cash order"
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


def together_refused(what: str, part: int | None, said: str) -> str:
    """Why a whole batch is refused: one part is over a limit, and nothing of it was sent, so no
    part of a hedge goes without the others; a split, as the one order it was asked as."""
    reason = said.removesuffix(" Nothing was sent.")
    if what == "split":
        return (
            "Nothing of this split order was sent: it is held to your limits as the one order it "
            f"is. {reason}"
        )
    where, name = ("this basket", "Order") if what == "basket" else ("this multi-leg order", "Leg")
    return (
        f"Nothing in {where} was sent, so no part goes without the others. {name} {part}: {reason}"
    )


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
    SETTINGS_SAVED,
    SETTINGS_CHANGED_MEANWHILE,
    SETTINGS_NOT_SAVED,
    BAND_NOT_CHECKED,
    SIZE_NOT_CHECKED,
)
