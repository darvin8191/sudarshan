"""The Upstox tab's allocation and return as the phone reads them (M7.15).

Nothing here is a rule. The bars are ``core/allocation``'s, the money and the counted shares
``core/upstoxbook``'s, the return ``core/performance``'s and the index comparison
``core/history``'s. This module fetches what they are given (the holdings the reader read, the
owner's own assets, the trade history kept by reports.py, the closing records, the applied
bonuses and splits, the index closes) and shapes what they return. It reads Upstox nowhere: the
ops hand it what the reader and the report reader already hold.

The words are the owner's: what is counted, what is not and why, and what to do about it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from . import corpactions
from .core import history, performance, upstoxbook
from .core.allocation import BookLine, allocate_book_all
from .core.days import day_of_instant
from .core.kinds import BENCHMARKS
from .core.money import value_paise
from .core.words import date as day_words
from .core.words import rupees
from .reports import fy_label, fy_span
from .store import Tx

WORDS = {
    "unclassified": "{count} of your holdings {have} no class or sector yet. SUDARSHAN does not "
    "guess them: a share takes the class and sector you gave it in one of your own tabs.",
    "waiting": "Upstox's share trades of this year are not read in full yet, so no return is "
    "shown. Open Trades, choose this year and the EQ segment, and read them.",
    "no_record": "The return needs a closing record of your Upstox holdings from before the "
    "trades were last read, on a day with no delivery trade. The next one is taken after the "
    "close on a trading day.",
    "unpriced": "The closing record of {day} has no price for some holdings, so no return is "
    "shown from it. The next record replaces it.",
    "scope": "Shares and ETFs only, from Upstox's trade history since {day}. Dividends, "
    "brokerage and taxes are not in these figures, and F&O is not part of them.",
    "year_unread": "{label} is not read from Upstox yet, so the history starts on {day}. Open "
    "Trades and choose that year to read it.",
    "uncounted": "{count} {holdings} worth {value} {have} no purchase in that history: bought "
    "before {day}, or received without a trade. They are not counted in the gain, the return "
    "or the index comparison, and are listed below.",
    "left_out": "Sales of such shares, {value} in all, are not counted either.",
    "gone": "Upstox's trades show more of {names} than you hold now. The difference is counted "
    "as lost, so the return shown is lower than the true one if you moved or tendered those "
    "shares outside the market.",
    "unmatched": "{count} {trades} Upstox lists without an ISIN {are} left out.",
    "twr": "TWR is on all your Upstox holdings, from the first closing record.",
}


# ---- allocation ------------------------------------------------------------------------------
def allocation(tx: Tx, part: Mapping[str, Any]) -> dict[str, Any]:
    """types.ts ``UpstoxAllocation``: the holdings part's bars by class and by sector, and the
    class and sector of each holding for the phone to move the bars with ticks."""
    rows = part.get("data")
    out = {
        "state": part.get("state"),
        "read_at": part.get("read_at"),
        "note": part.get("note"),
        "classes": [],
        "allocation": None,
    }
    if rows is None:
        return out
    by_isin, by_key = {}, {}
    for asset in tx.rows("asset", kind="listed"):
        if asset.get("isin"):
            by_isin[asset["isin"]] = asset
        if asset.get("instrument_key"):
            by_key[asset["instrument_key"]] = asset
    lines = []
    for row in rows:
        isin = row.get("isin") or corpactions.isin_of_key(row["instrument_key"])
        asset = by_isin.get(isin) or by_key.get(row["instrument_key"])
        asset_class = asset["asset_class"] if asset else None
        sector = (asset.get("sector") or None) if asset else None
        out["classes"].append(
            {"instrument_key": row["instrument_key"], "asset_class": asset_class, "sector": sector}
        )
        lines.append(
            BookLine(
                row["instrument_key"],
                value_paise(row["qty_u"], row["last_price_u"]),
                asset_class,
                sector,
            )
        )
    out["allocation"] = allocate_book_all(lines)
    loose = sum(1 for line in lines if line.asset_class is None or line.sector is None)
    if loose and not out["note"]:
        out["note"] = WORDS["unclassified"].format(
            count=loose, have="has" if loose == 1 else "have"
        )
    return out


# ---- performance -----------------------------------------------------------------------------
def _waiting(note: str | None) -> dict[str, Any]:
    return {
        "state": "waiting",
        "note": note,
        "from": None,
        "read_at": None,
        "data": None,
        "uncounted": [],
        "uncounted_value_paise": 0,
        "words": [],
    }


def _complete_run(shown: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The years read in full that end with the newest, newest first (``shown`` is newest first)."""
    run = []
    for report in shown:
        if report.get("state") != "ok":
            break
        run.append(report)
    return run


def _records(
    tx: Tx,
) -> tuple[dict[int, dict[str, list[int]]], dict[int, int | None], dict[int, int]]:
    """The closing records with holdings: each day's ``{isin: [units, price]}``, its value (None
    when a line has no price) and when it was read."""
    read = {
        row["day"]: row["read_at"]
        for row in tx.rows("upstox_eod", "day")
        if row["holdings_value_paise"] is not None
    }
    held: dict[int, dict[str, list[int]]] = {day: {} for day in read}
    values: dict[int, int | None] = dict.fromkeys(read, 0)
    for line in tx.rows("upstox_eod_holding", "instrument_key"):
        day = line["day"]
        if day not in read:
            continue
        units = line["qty"] + (line["t1_qty"] or 0)
        price = line["price_u"]
        if price is None:
            values[day] = None
        elif values[day] is not None:
            values[day] += value_paise(units, price)
        isin = corpactions.isin_of_key(line["instrument_key"]) or line["instrument_key"]
        slot = held[day].setdefault(isin, [0, price])
        slot[0] += units
    return held, values, read


def _names(tx: Tx, rows: Iterable[Mapping[str, Any]]) -> dict[str, tuple[str | None, str]]:
    """``{isin: (symbol, name)}`` from the owner's assets, else from Upstox's trade rows."""
    found: dict[str, tuple[str | None, str]] = {}
    for row in rows:
        if row.get("isin"):
            found[row["isin"]] = (row.get("symbol"), row.get("scrip_name") or row["isin"])
    for asset in tx.rows("asset", kind="listed"):
        if asset.get("isin"):
            found[asset["isin"]] = (asset.get("symbol"), asset["name"])
    return found


def _count(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def performance_report(
    tx: Tx, shown: Sequence[Mapping[str, Any]], holidays: Iterable[int] = ()
) -> dict[str, Any]:
    """types.ts ``UpstoxPerformance``. ``shown`` are the share-trade reports of the financial
    years Upstox keeps, newest first, as the report reader answered them."""
    run = _complete_run(shown)
    if not run:
        newest = shown[0] if shown else {}
        return _waiting(newest.get("note") or WORDS["waiting"])
    rows = [row for report in run for row in report["rows"]]
    trades = [upstoxbook.Trade.of(row) for row in rows]
    first_day = fy_span(run[-1]["fy"])[0]
    read_day = day_of_instant(run[0]["read_at"])
    held, values, read = _records(tx)
    day = upstoxbook.last_quiet_day(held, trades, read_day)
    if day is None:
        return _waiting(WORDS["no_record"])
    if values[day] is None:
        return _waiting(WORDS["unpriced"].format(day=day_words(day)))
    actions = [
        upstoxbook.Action(a["ex_day"], a["instrument"], a["kind"], a["num"], a["den"])
        for a in corpactions.accepted_actions(tx)
        if a["accepted"] and a["ex_day"] >= first_day
    ]
    lines = [upstoxbook.Held(isin, units, price) for isin, (units, price) in held[day].items()]
    got = upstoxbook.counted(trades, lines, actions, day)
    out = performance.summary(got.flows, got.value_paise, day)
    shut = sorted(holidays)
    points = [p for p in upstoxbook.points(values.items(), trades) if p.day <= day]
    twr = performance.twr(points)
    periods = performance.periods(points)
    span = (periods[0].start, periods[-1].day) if periods else None
    moves = history.day_returns(periods, shut)
    closes = tx.index_closes()
    out["twr"] = twr.as_dict()
    out["benchmarks"] = [
        history.compared(
            name, got.flows, closes.get(name, ()), day, None, span, moves, twr.pct, shut
        )
        for name in BENCHMARKS
    ]
    names = _names(tx, rows)
    words = [WORDS["scope"].format(day=day_words(first_day))]
    if len(run) < len(shown):
        missing = shown[len(run)]
        words.append(
            WORDS["year_unread"].format(label=fy_label(missing["fy"]), day=day_words(first_day))
        )
    loose_value = sum(u.value_paise for u in got.uncounted)
    if got.uncounted:
        n = len(got.uncounted)
        words.append(
            WORDS["uncounted"].format(
                count=n, holdings=_count(n, "holding", "holdings"), value=rupees(loose_value),
                have=_count(n, "has", "have"), day=day_words(first_day),
            )
        )  # fmt: skip
    if got.left_out_paise:
        words.append(WORDS["left_out"].format(value=rupees(got.left_out_paise)))
    if got.gone:
        said = [names.get(isin, (None, isin)) for isin, _ in got.gone]
        words.append(WORDS["gone"].format(names=", ".join(s or n for s, n in said)))
    if got.unmatched:
        n = got.unmatched
        words.append(
            WORDS["unmatched"].format(
                count=n, trades=_count(n, "trade", "trades"), are=_count(n, "is", "are")
            )
        )
    if twr.pct is not None:
        words.append(WORDS["twr"])
    return {
        "state": "ok",
        "note": None,
        "from": first_day,
        "read_at": read[day],
        "data": out,
        "uncounted": [
            {
                "isin": u.isin,
                "symbol": names.get(u.isin, (None, u.isin))[0],
                "name": names.get(u.isin, (None, u.isin))[1],
                "qty_u": u.qty_u,
                "value_paise": u.value_paise,
            }
            for u in sorted(got.uncounted, key=lambda u: (-u.value_paise, u.isin))
        ],
        "uncounted_value_paise": loose_value,
        "words": words,
    }
