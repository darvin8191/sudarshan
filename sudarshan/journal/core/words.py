"""The words of the owner's reports and the family line (JA7, M15.1-M15.3; design 5.7 and 6.4).

Pure templates: every number arrives in paise or as a ready ratio string and the text comes back.
``tests/vectors/words.json`` pins a sample of each.

* The first line of every report is "सुदर्शन · SUDARSHAN" (binding product rule 2).
* Money is Indian grouping (1,82,400.00) with the rupee sign; a profit or loss carries its sign,
  and the minus is the true minus U+2212, never a hyphen. Zero has no sign.
* A trade that ends at exactly zero is "flat" on screen; "scratch" is the code's word only.
* No icons, no emojis. "est." marks every estimated charge.
* A word never claims what was not read: a figure that is not available says so.

The family line (cross-design Y16, Y24) is built from a member's four privacy categories
(``pnl``, ``qty``, ``funds``, ``orders``, each shown or hidden): the trade count only when orders
are shown, won and lost only when the P&L is shown too (P&L is only ever offered together with
quantities, so it counts as hidden while quantities are), and never a word with money in it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sudarshan.journal.core import money
from sudarshan.tax.words import WORKING_PAPER

HEADER = "सुदर्शन · SUDARSHAN"
MINUS = "−"
SEP = " · "
RUPEE = "₹"
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
OPEN_FULL_REPORT = "Open the full report: SUDARSHAN > Reports"
#: The tax core's own sentence (``sudarshan.tax.words``), so one string labels the working
#: paper on the phone's Tax view and on the Tax pack.
FY_NOTE = WORKING_PAPER


# ---- numbers ---------------------------------------------------------------------------------
def group_en_in(digits: str) -> str:
    """``"182400"`` as ``"1,82,400"`` (the last three digits, then pairs)."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    pairs = []
    while len(head) > 2:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    pairs.insert(0, head)
    return ",".join([*pairs, tail])


def rupees(paise: int, signed: bool = False) -> str:
    """Paise as ``₹1,82,400.00``; with ``signed`` a profit is ``+₹..`` and a loss ``−₹..``."""
    whole, cents = divmod(abs(int(paise)), 100)
    text = f"{RUPEE}{group_en_in(str(whole))}.{cents:02d}"
    if not signed or paise == 0:
        return text
    return ("+" if paise > 0 else MINUS) + text


def day_label(day: int) -> str:
    """``20261109`` as ``Mon 9 Nov``."""
    d = money.to_date(day)
    return f"{WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


def day_label_year(day: int) -> str:
    """``20261109`` as ``9 Nov 2026``."""
    d = money.to_date(day)
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def plural(n: int, one: str, many: str | None = None) -> str:
    """``1 trade``, ``6 trades``."""
    return f"{n} {one if n == 1 else (many or one + 's')}"


def signed_pct(text: str) -> str:
    """A ratio string ``"1.20"`` as ``+1.20%`` and ``"-2.50"`` as ``−2.50%``."""
    if text.startswith("-"):
        return f"{MINUS}{text[1:]}%"
    return f"{'+' if text.strip('0.') else ''}{text}%"


def _positions(items: Sequence[Mapping[str, Any]]) -> str:
    if not items:
        return "none"
    inner = "; ".join(f"{p['label']}, {p['qty']}" for p in items)
    return f"{plural(len(items), 'position')} ({inner})"


def _kill_switch(state: bool | None) -> str:
    if state is None:
        return "Kill switch not read"
    return "Kill switch on" if state else "Kill switch off"


def _trades_line(trades: int, wins: int, losses: int, flats: int) -> str:
    if not trades:
        return "No trades"
    text = f"Trades {trades}{SEP}won {wins}, lost {losses}"
    return text + (f", {flats} flat" if flats else "")


def _lines(*lines: str | None) -> str:
    return "\n".join(line for line in lines if line)


def left_out_line(unmatched: int, expired: int, lead: str = "Not in these figures",
                  unclosed: int = 0) -> str | None:  # fmt: skip
    """What a report's figures leave out (review B1), or None when nothing: positions the
    journal did not see opened, contracts that ended at expiry (an expiry has no fill), and
    trades the journal did not see closed (verification N2)."""
    parts = []
    if unmatched:
        parts.append(f"{plural(unmatched, 'position')} opened before the journal started")
    if expired:
        parts.append(f"{plural(expired, 'contract')} that ended at expiry")
    if unclosed:
        parts.append(f"{plural(unclosed, 'trade')} the journal did not see closed")
    if not parts:
        return None
    them = "it" if unmatched + expired + unclosed == 1 else "them"
    return f"{lead}: {', '.join(parts)}. Upstox's own report has {them}."


# ---- the owner's reports ---------------------------------------------------------------------
def eod(
    day: int,
    realised_paise: int,
    unrealised_paise: int,
    charges_est_paise: int,
    trades: int,
    wins: int,
    losses: int,
    flats: int,
    by_strategy: Sequence[Mapping[str, Any]] = (),
    carried: Sequence[Mapping[str, Any]] = (),
    kill_switch: bool | None = False,
    daily_lock_at: str | None = None,
    fills_recorded: int = 0,
    check_at: str | None = None,
    close_check: Mapping[str, Any] | None = None,
    left_out: Mapping[str, int] | None = None,
) -> str:
    """The day report (M15.2). ``by_strategy`` rows are ``{name, net_paise, trades}``;
    ``left_out`` the counts the trade count leaves out (``unmatched``, ``expired``, ``unclosed``)."""
    net = realised_paise + unrealised_paise
    trades_line = _trades_line(trades, wins, losses, flats) if trades else "No trades today"
    strategies = SEP.join(
        f"{s['name']} {rupees(s['net_paise'], True)} ({s['trades']})" for s in by_strategy
    )
    if daily_lock_at:
        lock = f"Daily loss limit reached at {daily_lock_at}; new orders refused for the day"
    else:
        lock = "Daily loss limit not reached"
    fills = f"Fills: {fills_recorded} recorded live"
    if close_check and close_check.get("state") == "agreed":
        fills += "; the close check agreed."
    elif close_check and close_check.get("state") == "found":
        more = int(close_check["more"])
        fills += f"; the close check found {plural(more, 'more fill', 'more fills')}; the journal is corrected."  # fmt: skip
    elif check_at:
        fills += f"; the day's book is checked at {check_at}."
    else:
        fills += "."
    return _lines(
        HEADER,
        f"{day_label(day)}{SEP}Day report",
        f"Realised {rupees(realised_paise, True)}{SEP}Unrealised {rupees(unrealised_paise, True)}"
        f"{SEP}Net {rupees(net, True)} (before charges, est. {rupees(charges_est_paise)})",
        trades_line,
        f"By strategy: {strategies}" if strategies and trades else None,
        left_out_line(
            int((left_out or {}).get("unmatched") or 0),
            int((left_out or {}).get("expired") or 0),
            "Not in the trade count",
            unclosed=int((left_out or {}).get("unclosed") or 0),
        ),
        f"Carried overnight: {_positions(carried)}",
        f"{_kill_switch(kill_switch)}{SEP}{lock}",
        fills,
    )


def brief(
    day: int,
    kind: str = "normal",
    hours: str | None = None,
    fno_close: str | None = None,
    window: str | None = None,
    session_name: str = "Special session",
    holiday_reason: str | None = None,
    next_session_day: int | None = None,
    next_session_open: str | None = None,
    expiry_label: str | None = None,
    holiday_ahead_day: int | None = None,
    funds_available_paise: int | None = None,
    funds_used_paise: int | None = None,
    overnight: Sequence[Mapping[str, Any]] = (),
    yesterday: Mapping[str, Any] | None = None,
    kill_switch: bool | None = False,
    autologin: str | None = None,
) -> str:
    """The morning brief (M15.1). ``kind`` is ``normal``, ``muhurat`` (any special session, named
    by ``session_name``) or ``holiday``."""
    if kind not in ("normal", "muhurat", "holiday"):
        raise ValueError(f"unknown brief kind: {kind!r}")
    head = [HEADER, f"{day_label(day)}{SEP}Morning brief"]
    if kind == "holiday":
        why = f": {holiday_reason}" if holiday_reason else ""
        nxt = None
        if next_session_day:
            at = f" {next_session_open}" if next_session_open else ""
            nxt = f"Next session {day_label(next_session_day)}{at}"
        return _lines(
            *head,
            f"Market closed today{why}",
            nxt,
            f"Overnight: {_positions(overnight)}",
            f"{_kill_switch(kill_switch)}" + (f"{SEP}Auto login: {autologin}" if autologin else ""),
        )
    if kind == "muhurat":
        market = f"{session_name} {window}"
    else:
        market = f"Market open {hours}" + (f" (F&O to {fno_close})" if fno_close else "")
    extras = []
    if expiry_label:
        text = f"{expiry_label} expiry today"
        if holiday_ahead_day:
            text += f" ({day_label(holiday_ahead_day)} is a holiday)"
        extras.append(text)
    elif holiday_ahead_day:
        extras.append(f"{day_label(holiday_ahead_day)} is a holiday")
    if funds_available_paise is None:
        funds = "Funds not available right now"
    else:
        funds = f"Funds available {rupees(funds_available_paise)}"
        if funds_used_paise is not None:
            funds += f"{SEP}used {rupees(funds_used_paise)}"
    if yesterday is None:
        y = None
    elif not yesterday.get("trades"):
        y = "Yesterday: no trades"
    else:
        y = (
            f"Yesterday: net {rupees(yesterday['net_paise'], True)}"
            f"{SEP}{plural(yesterday['trades'], 'trade')}"
        )
    tail = _kill_switch(kill_switch) + (f"{SEP}Auto login: {autologin}" if autologin else "")
    return _lines(
        *head,
        SEP.join([market, *extras]),
        funds,
        f"Overnight: {_positions(overnight)}",
        y,
        tail,
    )


def _win_rate_words(m: Mapping[str, Any]) -> str:
    if m["win_rate"] is None:
        return "Win rate: no trades"
    return f"Win rate {m['win_rate']}%"


def _pf_words(m: Mapping[str, Any]) -> str:
    if m["profit_factor"] is not None:
        return f"Profit factor {m['profit_factor']}"
    if m["profit_factor_state"] == "no_losing_trades":
        return "Profit factor: no losing trades"
    return "Profit factor: no trades"


def period_report(
    title: str,
    label: str,
    period_word: str,
    trading_days: int,
    metrics: Mapping[str, Any],
    best_day: Sequence[int] | None = None,
    worst_day: Sequence[int] | None = None,
    by_strategy: Sequence[Mapping[str, Any]] = (),
    benchmark: Mapping[str, Any] | None = None,
    beta: Mapping[str, Any] | None = None,
    drawdown: Mapping[str, Any] | None = None,
    footer: Sequence[str] = (),
    left_out: Mapping[str, int] | None = None,
) -> str:
    """The shared body of the week, month and financial-year reports. ``left_out`` the counts
    the figures leave out (``unmatched``, ``expired``): one line when there are any."""
    head = [HEADER, f"{title}{SEP}{label}"]
    aside = left_out_line(int((left_out or {}).get("unmatched") or 0),
                          int((left_out or {}).get("expired") or 0),
                          unclosed=int((left_out or {}).get("unclosed") or 0))  # fmt: skip
    if not trading_days:
        return _lines(*head, f"No trading day this {period_word}.", aside, *footer)
    m = metrics
    net = f"Net {rupees(m['net_paise'], True)} (after est. charges {rupees(m['charges_paise'])})"
    count = f"Trading days {trading_days}{SEP}"
    if m["trips"]:
        count += f"trades {m['trips']}{SEP}won {m['wins']}, lost {m['losses']}"
        count += f", {m['flats']} flat" if m["flats"] else ""
    else:
        count += "no trades"
    extremes = None
    if best_day and worst_day:
        extremes = (
            f"Best day {day_label(best_day[0])} {rupees(best_day[1], True)}{SEP}"
            f"worst day {day_label(worst_day[0])} {rupees(worst_day[1], True)}"
        )
    strategies = SEP.join(
        f"{s['name']} {rupees(s['net_paise'], True)} ({s['trades']})" for s in by_strategy
    )
    bench = None
    if benchmark:
        idx = benchmark.get("index_pct")
        mine = benchmark.get("account_pct")
        parts = []
        if mine is not None:
            parts.append(f"you {signed_pct(mine)}")
        if idx is not None:
            parts.append(f"index {signed_pct(idx)}")
        if mine is None:
            parts.append("set your trading capital under Reports to see yours")
        bench = f"Against NIFTY 50: {SEP.join(parts)}" if parts else None
    beta_line = None
    if beta:
        if beta.get("beta") is not None:
            beta_line = f"Beta {beta['beta']} over {beta['days']} days"
        elif beta.get("note"):
            beta_line = f"Beta: {beta['note']}"
    dd = None
    if drawdown and drawdown.get("max_drawdown_paise"):
        dd = f"Deepest drawdown {rupees(drawdown['max_drawdown_paise'], True)}"
        if drawdown.get("max_drawdown_pct") is not None:
            dd += f" ({signed_pct(drawdown['max_drawdown_pct'])})"
    return _lines(
        *head,
        net,
        count,
        f"{_win_rate_words(m)}{SEP}{_pf_words(m)}" if m["trips"] else None,
        extremes,
        f"By strategy: {strategies}" if strategies else None,
        bench,
        beta_line,
        dd,
        aside,
        *footer,
    )


def weekly(
    start_day: int, end_day: int, trading_days: int, metrics: Mapping[str, Any], **extra: Any
) -> str:
    """The week in review (M15.3), Monday to Sunday."""
    label = f"{day_label(start_day)} - {day_label_year(end_day)}"
    return period_report("Week in review", label, "week", trading_days, metrics, **extra)


def monthly(label: str, trading_days: int, metrics: Mapping[str, Any], **extra: Any) -> str:
    """The monthly statement (M15.4); ``label`` is e.g. ``October 2026``."""
    footer = (OPEN_FULL_REPORT,)
    return period_report("Monthly statement", label, "month", trading_days, metrics, footer=footer, **extra)  # fmt: skip


def fy(label: str, trading_days: int, metrics: Mapping[str, Any],
       paper: Sequence[str] = (), **extra: Any) -> str:  # fmt: skip
    """The tax pack (M15.5); ``label`` is e.g. ``FY 2025-26``. ``paper`` is the tax working
    paper's own lines (``taxpack.pack_lines``), or the sentence saying why there is none."""
    footer = (*paper, FY_NOTE, OPEN_FULL_REPORT)
    return period_report("Tax pack", label, "year", trading_days, metrics, footer=footer, **extra)


# ---- the family line (Y16, Y24) --------------------------------------------------------------
def family_line(trades: int, won: int, lost: int, categories: Mapping[str, bool]) -> str | None:
    """ "Trades today: 6 · won 3 · lost 2", only as the member's categories allow.

    Orders hidden: no line at all (a won and lost count would give the count away). Orders
    shown, P&L hidden: the count alone. Orders and P&L shown: won and lost too (none of them when
    there were no trades). A category that is missing is hidden. P&L counts as shown only while
    quantities are shown too. Never a word with money in it.
    """
    orders = categories.get("orders") is True
    pnl = categories.get("pnl") is True and categories.get("qty") is True
    if not orders:
        return None
    line = f"Trades today: {int(trades)}"
    if pnl and trades:
        line += f"{SEP}won {int(won)}{SEP}lost {int(lost)}"
    return line


def family_day(
    day: int, trades: int, won: int, lost: int, categories: Mapping[str, bool]
) -> dict[str, Any]:
    """The answer of the counts-only ``family_day`` op: ``{day, line}`` and nothing else."""
    return {"day": int(day), "line": family_line(trades, won, lost, categories)}
