"""Every sentence the automation says to the owner (M12.1, M6.8). Pure.

Each names the strategy, says what was not done and what to check, in a trader's words: no status
code, no route, no exception name. Fixed vocabulary: Active, Stopped, Live, Sandbox, alert,
strategy, "not sent", "not confirmed". The sentence scan in the tests walks every function here
with sample values and the source itself for words that must never appear.
"""

from __future__ import annotations

import re

from . import model as m

_MONTHS = {
    "JAN": "Jan", "FEB": "Feb", "MAR": "Mar", "APR": "Apr", "MAY": "May", "JUN": "Jun",
    "JUL": "Jul", "AUG": "Aug", "SEP": "Sep", "OCT": "Oct", "NOV": "Nov", "DEC": "Dec",
}  # fmt: skip
_CONTRACT = re.compile(r"([A-Z0-9&-]+?)(\d{2})([A-Z]{3})(\d{2})(?:FUT|(\d+(?:\.\d+)?)(CE|PE))")
_INDIA_S = 19_800  # +5:30
_SIDE = {1: "long", -1: "short", 0: "flat"}
_ORDER = {1: "buy", -1: "sell", 0: "exit"}


def clock(epoch_s: float) -> str:
    """HH:MM:SS in India."""
    seconds = int(epoch_s + _INDIA_S) % 86_400
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def span(seconds: float) -> str:
    """A length of time as a trader says it: 45 s, 2 min 10 s, 3 h 5 min."""
    whole = abs(int(seconds))
    if whole < 60:
        return f"{whole} s"
    if whole < 3600:
        return f"{whole // 60} min {whole % 60} s" if whole % 60 else f"{whole // 60} min"
    return f"{whole // 3600} h {whole % 3600 // 60} min"


def contract_name(symbol: str | None) -> str:
    """``NIFTY24NOV26FUT`` as ``NIFTY 24 Nov FUT``; ``NIFTY24NOV2625000CE`` as
    ``NIFTY 24 Nov 25000 CE``; a share's symbol as it is."""
    found = _CONTRACT.fullmatch(symbol or "")
    if not found or found.group(3) not in _MONTHS:
        return symbol or "the contract"
    base, day, month, _year, strike, side = found.groups()
    tail = f"{strike} {side}" if strike else "FUT"
    return f"{base} {int(day)} {_MONTHS[month]} {tail}"


def _is_option(symbol: str | None) -> bool:
    """An option's symbol, never a share whose name happens to end in CE or PE (RELIANCE)."""
    found = _CONTRACT.fullmatch(symbol or "")
    return bool(found and found.group(6))


def side(target: int) -> str:
    return _SIDE[(target > 0) - (target < 0)]


def _what(target: int) -> str:
    return _ORDER[(target > 0) - (target < 0)]


def product_name(product: str) -> str:
    return {"MIS": "Intraday", "NRML": "Carry", "CNC": "Delivery"}.get(product, product)


def trades(strategy: m.Strategy) -> str:
    """The card's line: what the strategy trades, how, and how much."""
    if strategy.size_mode == m.SCALED:
        unit = "lot" if strategy.exchange in ("NFO", "BFO") else "share"
        most = strategy.max_lots or 0
        size = f"chart size x {strategy.multiplier:g}, at most {most} {unit}" + (
            "" if most == 1 else "s"
        )
    elif strategy.lots:
        size = f"{strategy.lots} lot" + ("" if strategy.lots == 1 else "s")
    else:
        size = f"{strategy.quantity} share" + ("" if strategy.quantity == 1 else "s")
    if strategy.kind == m.OPTION:
        when = {"nearest": "nearest expiry", "next": "next expiry", "month": "this month"}
        what = (
            f"{strategy.underlying} option, {when.get(strategy.expiry_rule or '', 'nearest expiry')}, "
            f"{strategy.strike_offset or 'ATM'}"
        )
    elif strategy.expiry_rule in ("month", "next_month"):
        when = "this month" if strategy.expiry_rule == "month" else "next month"
        what = f"{strategy.underlying} future, {when}"
    else:
        what = (
            contract_name(strategy.underlying) if strategy.kind == m.SAME else strategy.underlying
        )
        if strategy.kind == m.SAME and strategy.chart:
            what = strategy.chart.split(":", 1)[1]
    return f"{what} · {product_name(strategy.product)} · {size}"


# ---- one alert's verdict ------------------------------------------------------------------------


def repeat(fired_s: int) -> str:
    return f"Repeat of the alert fired at {clock(fired_s)}. Nothing was sent."


def out_of_order(name: str, fired_s: int) -> str:
    return (
        f"{name}: an alert fired at {clock(fired_s)} arrived after a newer one and was not applied."
    )


def late(name: str, target: int, late_s: int, last_target: int) -> str:
    if late_s < 0:
        return (
            f"{name}: a {_what(target)} alert carries a time {span(late_s)} ahead of this server's "
            "clock and was not sent. Check the alert message in Alert set-up."
        )
    return (
        f"{name}: a {_what(target)} alert arrived {span(late_s)} late and was not sent. The chart "
        f"says {side(target)}; SUDARSHAN last left {name} {side(last_target)}. Enter by hand if "
        "you still want it."
    )


def late_note(late_s: int) -> str:
    if late_s < 0:
        return (
            f"(the alert carries a time {span(late_s)} ahead of this server's clock; check the "
            "alert message in Alert set-up)"
        )
    return f"(alert was {span(late_s)} late)"


def stopped(name: str, exits_only: bool, target: int) -> str:
    if exits_only:
        return (
            f"{name} is stopped for new entries, so its {_what(target)} alert was not sent. Its "
            "exit alert still goes."
        )
    return f"{name} is stopped. Its alerts are being ignored."


def capped(name: str, most: int) -> str:
    return (
        f"{name} sent {most} alerts today, the most you allow. It will take no new entries today. "
        "Its exit alert still goes."
    )


def gap(name: str, target: int) -> str:
    return f"{name}: an earlier alert did not arrive. This one was applied: now {side(target)}."


BAD = {
    "not_json": "the alert message is not the one SUDARSHAN gave you",
    "not_object": "the alert message is not the one SUDARSHAN gave you",
    "version": "the alert message is from another version of SUDARSHAN",
    "unknown_key": "the alert message has a part SUDARSHAN did not give you",
    "too_long": "a value in the alert message is too long",
    "position": "the alert message does not say long, short or flat",
    "before": "the alert message does not say where the strategy was before",
    "fired": "the alert message does not carry the time it fired",
    "bar": "the alert message carries a bar time that cannot be read",
    "number": "a quantity in the alert message is not a number",
    "mismatch": "the direction and the size in the alert message disagree",
}


def bad_message(name: str, reason: str, what: str = "") -> str:
    if reason == "placeholder":
        left = what if what.startswith("{{") and what.endswith("}}") else "a placeholder"
        return (
            f"{name}: the alert message still has {left.replace('{{', '').replace('}}', '')} in "
            "it, not filled in. That placeholder works only in a strategy's alert. Nothing was "
            "sent. Copy the message for your kind of alert from Alert set-up."
        )
    return f"{name}: {BAD.get(reason, BAD['not_json'])}. Nothing was sent. Copy it again from Alert set-up."


def wrong_chart(name: str, sent: str, chart: str) -> str:
    return (
        f"{name}: the alert came from the chart {sent}, and this strategy is set up for {chart}. "
        "Nothing was sent. Put the alert on the right chart, or change the chart in the strategy."
    )


def delayed_chart(name: str) -> str:
    return (
        f"{name}: this chart shows delayed prices, so its alert was not sent to Upstox. Use a "
        "chart with real-time prices for a Live strategy."
    )


def not_mapped(name: str, reason: str, what: str = "") -> str:
    said = {
        "unknown": (
            f"TradingView sent {what}, which SUDARSHAN cannot match to a contract. Nothing was "
            "sent. Open the strategy and choose what it trades."
        ),
        "no_chart": (
            "the alert does not name its chart, and this strategy trades what its chart shows. "
            "Nothing was sent. Copy the alert message again from Alert set-up."
        ),
        "not_listed": (
            f"{contract_name(what)} is not in today's contract list yet. Nothing was sent."
        ),
        "no_expiry": (f"no open {what} future is in today's contract list. Nothing was sent."),
        "no_option_expiry": (
            f"no open {what} option expiry is in today's contract list. Nothing was sent."
        ),
        "no_price": (
            f"the last price of {what} could not be read, so no strike was chosen. Nothing was "
            "sent."
        ),
        "no_strike": (
            f"the strike this strategy asks for is not in today's contract list for {what}. "
            "Nothing was sent."
        ),
        "master": (
            "today's contract list is not loaded yet. Nothing was sent. It loads when you log in "
            "to Upstox."
        ),
        "commodity": "Commodity strategies are not supported yet. Nothing was sent.",
        "index": (
            f"{what} is an index and cannot be traded itself. Nothing was sent. Open the strategy "
            "and choose its future as what it trades."
        ),
        "unchecked": (
            f"TradingView sent {what}, a future whose name SUDARSHAN has not checked yet. Nothing "
            "was sent. Open the strategy and name the underlying whose future it trades."
        ),
    }
    return f"{name}: {said.get(reason, said['unknown'])}"


def too_close(name: str, symbol: str, stop_s: int) -> str:
    return (
        f"{name}: {contract_name(symbol)} stops trading at {clock(stop_s)[:5]}. Too close for a "
        "new position. Nothing was sent."
    )


def not_sized(name: str, reason: str, symbol: str = "", figure: int = 0) -> str:
    if reason == "no_size":
        return (
            f"{name}: this alert does not carry the chart's position size, and {name} takes its "
            "size from it. Nothing was sent. Use the strategy alert message from Alert set-up, or "
            "give the strategy a fixed size."
        )
    if reason == "not_whole":
        unit = "a whole number of shares" if figure <= 1 else f"whole lots of {figure}"
        return (
            f"{name}: the chart's size times this strategy's multiplier does not come to {unit} "
            f"of {contract_name(symbol)}. Nothing was sent. Check the multiplier in the strategy."
        )
    if reason == "cap":
        return (
            f"{name}: the chart's size times this strategy's multiplier is above its cap for "
            f"{contract_name(symbol)} ({figure}). Nothing was sent. Raise the cap in the strategy "
            "if you mean to trade this size."
        )
    if reason == "freeze":
        return (
            f"{name}: its size is above the most the exchange takes in one order for "
            f"{contract_name(symbol)} ({figure}). Nothing was sent. Make the strategy smaller."
        )
    return (
        f"{name}: the lot size of {contract_name(symbol)} could not be read from today's contract "
        "list. Nothing was sent."
    )


def mode_refused(name: str, reason: str, target: int) -> str:
    what = _what(target)
    if reason == "app_in_analyze":
        return (
            f"The app is in Analyze mode, so {name}'s {what} was not sent. Its exit alerts still "
            "go to Upstox."
        )
    if reason == "guard_not_on":
        return (
            f"{name}'s {what} was not sent: Live automation waits for the risk guard to cover "
            "Live orders. Its exit alerts still go to Upstox."
        )
    if reason == "semi_auto":
        return (
            f"{name}'s {what} was not sent: the app is set to hold orders in the Action Center "
            "for your tap, and automation does not use it. Set order mode to Auto, or trade this "
            "one by hand."
        )
    return (
        f"{name} is a Live strategy, and automation on this server is in Sandbox only, so its "
        f"{what} was not sent anywhere. If {name} still has a position at Upstox, close it "
        "yourself in Positions."
    )


def busy(name: str) -> str:
    return (
        f"{name}: an earlier alert of this strategy is still being handled, so this one was not "
        "sent. If the chart and your position now differ, set the position by hand."
    )


def expires_today(symbol: str) -> str:
    return f"{contract_name(symbol)} expires today."


def holds_nothing(name: str, option: bool = False) -> str:
    """A flat alert for a strategy that holds nothing: a strategy only closes what it opened."""
    if option:
        return f"{name} holds no option. Nothing to send."
    return f"{name} holds no position of its own. Nothing to send."


def expired(name: str, symbol: str) -> str:
    return f"{contract_name(symbol)} expired. {name} now holds nothing."


# ---- the order's answer -------------------------------------------------------------------------


def sent(name: str, target: int, quantity: int, symbol: str) -> str:
    if target == 0:
        return f"{name}: exit sent for {contract_name(symbol)}."
    if _is_option(symbol):
        return f"{name}: order sent to buy {quantity} {contract_name(symbol)}."
    return f"{name}: order sent to be {side(target)} {quantity} {contract_name(symbol)}."


def cut_to_exit(name: str, symbol: str, said: str) -> str:
    """The risk gate cut a reversal to its exit; its own sentence says what went and why."""
    passed_on = _service(said)
    return f"{name}: {passed_on or f'only the exit of {contract_name(symbol)} was sent.'}"


def no_action(name: str, target: int, quantity: int, symbol: str, flat_found: bool) -> str:
    if target == 0 or flat_found:
        return f"{name}: no open position in {contract_name(symbol)}. Nothing to send."
    if _is_option(symbol):
        return f"{name}: already holds {quantity} {contract_name(symbol)}. Nothing to send."
    return f"{name}: already {side(target)} {quantity} {contract_name(symbol)}. Nothing to send."


def order_refused(name: str, said: str) -> str:
    return f"{name}: the order was not sent. {_service(said)}"


def not_confirmed(name: str, said: str = "") -> str:
    return f"{name}: the order is not confirmed. Check Orders. {_service(said)}".rstrip()


def exit_first_refused(name: str, held: str, wanted: str, confirmed: bool) -> str:
    how = "was not sent" if confirmed else "is not confirmed"
    return (
        f"{name}: the exit of {contract_name(held)} {how}, so {contract_name(wanted)} was not "
        "entered. Check Orders."
    )


def interrupted(name: str) -> str:
    return (
        f"{name}: the server stopped while this alert was being handled, so its order is not "
        "confirmed. Check Orders."
    )


def _service(said: str) -> str:
    """The order service's own sentence, passed on; never a code or a brace."""
    text = " ".join(str(said or "").split())[:300]
    return re.sub(r"[{}]", "", text)


# ---- the switch, for Telegram and the log -------------------------------------------------------


def switched(name: str, state: str, surface: str) -> str:
    said = {
        m.ACTIVE: f"{name} is Active.",
        m.EXITS_ONLY: f"{name} is Stopped for new entries. Its exit alert still goes.",
        m.STOPPED: f"{name} is Stopped. Its alerts are ignored.",
    }[state]
    return f"{said} Changed from {surface}."


def mode_changed(name: str, mode: str, surface: str) -> str:
    return f"{name} now sends to {'Upstox (Live)' if mode == m.LIVE else 'the Sandbox'}. Changed from {surface}."


def new_link(name: str, surface: str) -> str:
    return f"{name} has a new alert link. The old one no longer works. Changed from {surface}."


def contract_taken(name: str, other: str) -> str:
    return (
        f"{name} was not started: {other} already trades the same contract and product. Two "
        "strategies cannot share one position. Stop the other first, or choose another product."
    )


def holds_position(name: str, symbol: str, side_: str, mode: str) -> str:
    where = "at Upstox" if mode == m.LIVE else "in the Sandbox"
    return f"{name} holds {contract_name(symbol)} {side_} {where}. Close it, then change this."


LIVE_WAITS_FOR_GUARD = "Live automation waits for the risk guard to cover Live orders."
LIVE_WAITS_FOR_STAGE = "Automation is in Sandbox only. Live comes in a later release."
WRONG_PHRASE = "That is not your confirm phrase. Nothing was changed."
CHANGED_ELSEWHERE = "This strategy was changed on another screen. Reload and try again."
NOT_FOUND = "This strategy is no longer there. Reload the page."
OWNER_ONLY = "Only the account owner can use Automation."
TOO_MANY_REQUESTS = "Too many requests. Try again in a minute."
SENDER_HIDDEN = (
    "An alert reached this server without its sender's address, so it was refused. The server's "
    "proxy setting needs a check: see The fence around the alert links in the README."
)
STAGE_WORDS = {
    "off": "Automation is switched off.",
    "sandbox": "Automation is in Sandbox only.",
    "on": "Automation may send Live orders for strategies you set to Live.",
}
