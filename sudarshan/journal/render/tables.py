"""The tables of the period reports and the monthly CSV (JB9, M15.3 to M15.5).

Pure: trips, metrics and calendar rows go in as the journal's own dicts (paise, ``price_u``), blocks
for ``pdf.render_pdf`` and the CSV text come out. A money cell is a ``doc.Money`` (always signed);
a side or result is a chip with its word. Nothing here reads a clock or a file.

The CSV (cross-design Y32, review L1): a text cell that starts with ``=``, ``@``, a tab or a
carriage return, or with ``-`` or ``+`` and is not wholly a number, gets a leading apostrophe,
because strategy labels arrive from webhooks and a spreadsheet would run them as formulas
(``-1+1`` is one). Numbers are plain (``-1234.56``): a cell that is wholly a number passes.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any

from sudarshan.journal.core import words
from sudarshan.journal.core.metrics import strategy_of
from sudarshan.journal.core.money import to_date
from sudarshan.journal.render import doc as D
from sudarshan.tax import classify

PRICE_SCALE = 10_000
DAY = 86_400
CSV_HEADER = (
    "Close day", "Open day", "Exchange", "Symbol", "Product", "Side", "Quantity",
    "Entry average", "Exit average", "Gross", "Charges", "Net", "Strategy", "Hold seconds",
)  # fmt: skip
NO_NUMBER = {
    "no_trades": "No trades",
    "no_losing_trades": "No losing trades",
    "no_variation": "No variation",
    "short": "Too few days",
    "no_capital": "Set your capital",
}


# ---- words ---------------------------------------------------------------------------------
def day_text(day: int) -> str:
    """``20261109`` as ``9 Nov 2026``."""
    return words.day_label_year(day)


def ratio_text(value: str | None, state: str | None, percent: bool = False) -> str:
    """A ratio as text, or the reason it has no number (never a zero standing in for it)."""
    if value is None:
        return NO_NUMBER.get(state or "", "Not available")
    return f"{value}%" if percent else value


def result_chip(net_paise: int) -> D.Chip:
    return D.Chip("PROFIT" if net_paise > 0 else "LOSS" if net_paise < 0 else "FLAT")


# ---- the figures and tables ----------------------------------------------------------------
def summary_figures(m: Mapping[str, Any], money_label: str = "Net after est. charges") -> D.Figures:
    """Net, trades, win rate and profit factor of a metrics block."""
    return D.Figures(
        (
            D.Figure(money_label, D.Money(int(m["net_paise"]))),
            D.Figure("Trades", str(m["trips"])),
            D.Figure("Win rate", ratio_text(m.get("win_rate"), m.get("win_rate_state"), True)),
            D.Figure(
                "Profit factor",
                ratio_text(m.get("profit_factor"), m.get("profit_factor_state")),
            ),
        )
    )


def strategy_table(blocks: Sequence[Mapping[str, Any]]) -> D.Table:
    """One row per strategy: trades, won, win rate and net, best first."""
    rows = sorted(blocks, key=lambda b: (-int(b["metrics"]["net_paise"]), b["name"]))
    return D.Table(
        (
            D.Column("Strategy", 3),
            D.Column("Trades", 1, "r"),
            D.Column("Won", 1, "r"),
            D.Column("Win rate", 1.3, "r"),
            D.Column("Net", 2, "r"),
        ),
        tuple(
            (
                b["name"],
                str(b["metrics"]["trips"]),
                str(b["metrics"]["wins"]),
                ratio_text(b["metrics"].get("win_rate"), b["metrics"].get("win_rate_state"), True),
                D.Money(int(b["metrics"]["net_paise"])),
            )
            for b in rows
        ),
        empty="No trades closed in this period.",
    )


def _period_table(first: str, rows: Iterable[Mapping[str, Any]], label) -> D.Table:
    data = list(rows)
    total = sum(int(r["net_paise"]) for r in data)
    return D.Table(
        (
            D.Column(first, 2),
            D.Column("Trades", 1, "r"),
            D.Column("Won", 1, "r"),
            D.Column("Net", 2, "r"),
            D.Column("Result", 1.2, "c"),
        ),
        tuple(
            (
                label(r["key"]),
                str(r["trips"]),
                str(r["wins"]),
                D.Money(int(r["net_paise"])),
                result_chip(int(r["net_paise"])),
            )
            for r in data
        ),
        total=(
            D.Ink("Total", True),
            str(sum(int(r["trips"]) for r in data)),
            str(sum(int(r["wins"]) for r in data)),
            D.Money(total),
            "",
        )
        if data
        else None,
        empty="No trades closed in this period.",
    )


def days_table(days: Iterable[Mapping[str, Any]]) -> D.Table:
    """The calendar's days: net per day, oldest first."""
    return _period_table("Day", days, lambda key: words.day_label(int(key)))


def months_table(months: Iterable[Mapping[str, Any]]) -> D.Table:
    """The calendar's months (key ``202610``): net per month, oldest first."""

    def label(key: str) -> str:
        d = to_date(int(key) * 100 + 1)
        return f"{words.MONTHS[d.month - 1]} {d.year}"

    return _period_table("Month", months, label)


NOT_ESTIMATED = "not estimated"


def unpriced(trip: Mapping[str, Any]) -> bool:
    """A trade on an exchange the charges estimate does not cover, with no actual figure: its
    charges are said to be not estimated, never shown as 0.00 (review M1)."""
    return trip.get("charges_modelled") is False and trip.get("charges_actual_paise") is None


def trades_table(trips: Sequence[Mapping[str, Any]]) -> D.Table:
    """Every closed trade: day, symbol, side, quantity, charges, net and result."""
    rows = []
    for t in trips:
        net = int(t["net_paise"])
        rows.append(
            (
                words.day_label(int(t["close_day"])),
                str(t["symbol"]),
                D.Chip("LONG" if t["side"] == "long" else "SHORT"),
                str(t["qty_max"]),
                D.Ink(NOT_ESTIMATED) if unpriced(t) else D.Money(-int(t.get("charges_paise") or 0)),
                D.Money(net),
                result_chip(net),
            )
        )
    return D.Table(
        (
            D.Column("Closed", 1.5),
            D.Column("Symbol", 3.4),
            D.Column("Side", 1.1, "c"),
            D.Column("Qty", 0.9, "r"),
            D.Column("Charges", 1.5, "r"),
            D.Column("Net", 1.8, "r"),
            D.Column("Result", 1.2, "c"),
        ),
        tuple(rows),
        total=(
            D.Ink("Total", True),
            "",
            "",
            "",
            D.Money(-sum(int(t.get("charges_paise") or 0) for t in trips)),
            D.Money(sum(int(t["net_paise"]) for t in trips)),
            "",
        )
        if rows
        else None,
        empty="No trades closed in this period.",
    )


#: A closed trade's kind in the journal's own activity table: the head ``sudarshan.tax``'s
#: classifier names for it (the one rule for it), in the words of the phone's Tax view. No
#: holding period: how long shares were held, and what that means, is the working paper's.
HEAD_LABELS = {
    classify.DERIVATIVE: "F&O and other derivatives",
    classify.INTRADAY: "Shares, bought and sold the same day",
    classify.DELIVERY: "Shares, taken or given in delivery",
    classify.NOT_COMPUTED: "Other",
}
KIND_ORDER = tuple(HEAD_LABELS.values())


def isin_of(trip: Mapping[str, Any]) -> str | None:
    """A cash trade's ISIN: the part of Upstox's instrument key after the bar (``NSE_EQ|INE...``).
    None where the journal kept no key; the classifier then does not take it for a share."""
    key = str(trip.get("instrument_key") or "")
    return key.rsplit("|", 1)[1].strip().upper() if "|" in key else None


def kind_of_trade(trip: Mapping[str, Any]) -> str:
    """The row a closed trade is counted in (see ``HEAD_LABELS``). The journal keeps Upstox's
    product codes too (I, D), so the product goes to the classifier by its class. A cash trade is
    a share's only when its ISIN is a company's equity share (the classifier's rule): an ETF's,
    a REIT's or a gold bond's is "Other", as the working paper leaves them out."""
    product = "MIS" if trip.get("product_class") == "intraday" else "CNC"
    same_day = trip.get("open_day") is not None and trip.get("open_day") == trip.get("close_day")
    head = classify.head_of_product(str(trip["exchange"]), str(trip.get("symbol") or ""),
                                    product, same_day=same_day, isin=isin_of(trip))  # fmt: skip
    return HEAD_LABELS[head]


def kinds_table(trips: Sequence[Mapping[str, Any]]) -> D.Table:
    """The trades the journal saw close in the period, by kind: count, gross, charges, net."""
    acc: dict[str, list[int]] = {}
    for t in trips:
        gross = t["gross_paise"] if t.get("gross_paise") is not None else t["net_paise"]
        row = acc.setdefault(kind_of_trade(t), [0, 0, 0, 0])
        row[0] += 1
        row[1] += int(gross)
        row[2] += int(t.get("charges_paise") or 0)
        row[3] += int(t["net_paise"])
    rows = [
        (name, str(v[0]), D.Money(v[1]), D.Money(-v[2]), D.Money(v[3]))
        for name in KIND_ORDER
        if (v := acc.get(name))
    ]
    return D.Table(
        (
            D.Column("Kind of trade", 3.6),
            D.Column("Trades", 0.9, "r"),
            D.Column("Gross", 1.8, "r"),
            D.Column("Charges", 1.6, "r"),
            D.Column("Net", 1.8, "r"),
        ),
        tuple(rows),
        total=(
            D.Ink("Total", True),
            str(sum(v[0] for v in acc.values())),
            D.Money(sum(v[1] for v in acc.values())),
            D.Money(-sum(v[2] for v in acc.values())),
            D.Money(sum(v[3] for v in acc.values())),
        )
        if rows
        else None,
        empty="No trades closed in this financial year.",
    )


# ---- the CSV -------------------------------------------------------------------------------
_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?")


def csv_cell(value: Any) -> str:
    """A text cell safe to open in a spreadsheet (Y32, review L1); a number passes as it is."""
    text = str(value)
    first = text[:1]
    if first in ("=", "@", "\t", "\r") or (first in ("-", "+") and not _NUMBER.fullmatch(text)):
        return "'" + text
    return text


def paise_text(paise: int | None) -> str:
    """Paise as rupees with two places and a plain minus: ``-1234.56``."""
    if paise is None:
        return ""
    whole, cents = divmod(abs(int(paise)), 100)
    return f"{'-' if paise < 0 else ''}{whole}.{cents:02d}"


def price_text(price_u: int | None) -> str:
    """A ``price_u`` as rupees, at least two places and no zeros past that: ``100.50``,
    ``0.0025``."""
    if price_u is None:
        return ""
    text = f"{Decimal(int(price_u)) / PRICE_SCALE:.4f}"
    while text.endswith("0") and len(text.partition(".")[2]) > 2:
        text = text[:-1]
    return text


def day_iso(day: int | None) -> str:
    return "" if day is None else f"{day // 10000:04d}-{day // 100 % 100:02d}-{day % 100:02d}"


def trades_csv(trips: Sequence[Mapping[str, Any]]) -> str:
    """The month's closed trades as CSV text (header, then one row per trade, oldest first)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(CSV_HEADER)
    for t in sorted(
        trips, key=lambda t: (int(t["close_day"]), int(t.get("closed_ms") or 0), int(t["id"]))
    ):
        writer.writerow(
            [
                day_iso(t["close_day"]),
                day_iso(t.get("open_day")),
                csv_cell(t["exchange"]),
                csv_cell(t["symbol"]),
                csv_cell(t["product"]),
                csv_cell(t["side"]),
                int(t["qty_max"]),
                price_text(t.get("entry_avg_u")),
                price_text(t.get("exit_avg_u")),
                paise_text(t.get("gross_paise")),
                NOT_ESTIMATED if unpriced(t) else paise_text(t.get("charges_paise")),
                paise_text(t["net_paise"]),
                csv_cell(strategy_of(t)),
                "" if t.get("hold_s") is None else int(t["hold_s"]),
            ]
        )
    return out.getvalue()
