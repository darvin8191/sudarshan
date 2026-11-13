"""The regime table (sudarshan/regimes.py) through its vectors: every day and expiry in
regimes_vectors.json, as the table and sessions.py give them. The same file pins the market
calendar and the sandbox (test_regimes_calendar.py, test_regimes_sandbox.py) and the phone chart's
hours (frontend sessionHours.test.ts), so the four can only move together."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from sudarshan import regimes
from sudarshan.alerts.market import sessions

VECTORS = json.loads(Path(__file__).with_name("regimes_vectors.json").read_text(encoding="utf-8"))
DAYS = VECTORS["days"]
EXPIRIES = VECTORS["expiries"]


def hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def clock(epoch_ms: int, day: date) -> str:
    return hhmm((epoch_ms - sessions.epoch_ms(day, 0)) // 60_000)


def test_the_vectors_hold_both_sides_of_the_change():
    days = {date.fromisoformat(case["date"]) for case in DAYS}
    assert date(2026, 12, 4) in days and date(2026, 12, 10) in days
    assert {case["stop"] for case in EXPIRIES} == {"15:30", "15:40", "15:45"}


@pytest.mark.parametrize("case", DAYS, ids=[case["date"] for case in DAYS])
def test_each_day_s_hours_from_the_table(case):
    day = date.fromisoformat(case["date"])
    regime = regimes.regime_on(day)
    assert [hhmm(m) for m in regime.cash] == case["cash"]
    assert [hhmm(m) for m in regime.fo] == case["fo"]
    assert hhmm(regimes.fo_close(day)) == case["fo"][1]
    assert [hhmm(regimes.PRE_OPEN), hhmm(regimes.fo_close(day))] == case["session"]
    assert regime.every_stock is case["every_stock"]


@pytest.mark.parametrize("case", DAYS, ids=[case["date"] for case in DAYS])
def test_each_day_s_sessions_and_auction_as_sessions_py_gives_them(case):
    day = date.fromisoformat(case["date"])
    for segment, key in (("NSE_EQ", "cash"), ("NSE_INDEX", "cash"), ("NSE_FO", "fo"),
                         ("BSE_FO", "fo")):  # fmt: skip
        assert [hhmm(m) for m in sessions.hours(segment, day=day)] == case[key]
    window = sessions.auction_window("NSE_EQ", day, trading_day=True)
    assert [clock(window[0], day), clock(window[1], day)] == case["auction"]
    closes = sessions.auction_close("NSE_EQ", day, trading_day=True)
    assert clock(closes, day) == case["auction_close"]
    assert sessions.closes_by_auction("NSE_EQ", day, derivatives=False) is case["every_stock"]
    assert sessions.closes_by_auction("NSE_EQ", day, derivatives=True) is True


def test_on_10_december_a_stock_with_derivatives_closes_by_auction_and_one_without_does_not():
    """NSE's circulars of 6 Nov 2026: from 7 Dec the auction stays with stocks that have
    derivatives. Such a stock trades to 15:30, then its auction runs to 15:40 with the price
    expected at 15:40 and taken until 15:45 (L4: five minutes after the auction's latest close,
    as the first regime keeps); a stock without derivatives ends at 15:30 with no auction."""
    day = date(2026, 12, 10)
    assert sessions.closes_by_auction("NSE_EQ", day, derivatives=True)
    cas = sessions.session("NSE_EQ", day, trading_day=True, auction=True)
    assert cas.segment == "NSE_EQ+auction"
    assert [clock(cas.start, day), clock(cas.end, day)] == ["09:15", "15:30"]
    assert clock(cas.auction_until, day) == "15:45" and clock(cas.auction_close, day) == "15:40"
    assert not sessions.closes_by_auction("NSE_EQ", day, derivatives=False)
    assert not sessions.closes_by_auction("BSE_EQ", day, derivatives=False)
    plain = sessions.session("NSE_EQ", day, trading_day=True)
    assert plain.segment == "NSE_EQ"
    assert [clock(plain.start, day), clock(plain.end, day)] == ["09:15", "15:30"]
    assert (plain.auction_until, plain.auction_close) == (None, None)


@pytest.mark.parametrize("case", EXPIRIES, ids=[case["symbol"] for case in EXPIRIES])
def test_each_contract_stops_on_its_expiry_day(case):
    expiry = date.fromisoformat(case["expiry"])
    assert regimes.index_derivative(case["symbol"]) is case["index"]
    assert hhmm(regimes.expiry_stop(expiry, index=case["index"])) == case["stop"]
    segment = {"NFO": "NSE_FO", "BFO": "BSE_FO"}[case["exchange"]]
    stops = sessions.expiry_close(segment, expiry, index=case["index"])
    assert clock(stops, expiry) == case["stop"]


def test_the_changes_a_shipped_close_follows_are_the_later_regimes():
    """The market calendar's rows follow these; the vectors' timings carry the same."""
    assert regimes.fo_changes() == ((date(2026, 12, 7), (555, 945)),)
    nfo = next(row for row in VECTORS["timings"] if row["exchange"] == "NFO")
    assert nfo["changes"] == [{"from": "2026-12-07", "end_offset": 945 * 60_000, "end_time": "15:45"}]


def test_sessions_py_reads_the_one_table():
    """No second table with the same numbers: sessions.py's names are the regime module's."""
    assert sessions.REGIMES is regimes.REGIMES and sessions.Regime is regimes.Regime
    assert sessions.INDEX_UNDERLYINGS is regimes.INDEX_UNDERLYINGS
    assert sessions.SEGMENTS["NSE_FO"] == regimes.REGIMES[0].fo
    assert sessions.SEGMENTS["NSE_EQ"] == regimes.REGIMES[0].cash


# ---- which contracts are an index's (review L1) ---------------------------------------------------
@pytest.mark.parametrize(
    ("symbol", "underlying", "index"),
    [
        ("NIFTY08DEC2625900CE", "NIFTY", True),
        ("NIFTYFPI29DEC2618000CE", "NIFTYFPI", True),  # read as NIFTY's before L1
        ("NIFTYFPI27OCT26FUT", "NIFTYFPI", True),
        ("NIFTYNXT5029DEC26FUT", "NIFTYNXT50", True),
        ("MIDCPNIFTY29DEC2613000PE", "MIDCPNIFTY", True),
        ("FINNIFTY29DEC2627000CE", "FINNIFTY", True),
        ("BANKNIFTY29DEC26FUT", "BANKNIFTY", True),
        ("SENSEX10DEC2682000PE", "SENSEX", True),
        ("SENSEX5029DEC26FUT", "SENSEX50", True),
        ("BANKEX29DEC2660000CE", "BANKEX", True),
        ("RELIANCE29DEC261400CE", "RELIANCE", False),
        ("BANKBARODA29DEC26FUT", "BANKBARODA", False),
        ("BANKINDIA29DEC26120CE", "BANKINDIA", False),
        ("M&M29DEC263000CE", "M&M", False),
        ("BAJAJ-AUTO29DEC269000PE", "BAJAJ-AUTO", False),
        ("360ONE29DEC26FUT", "360ONE", False),
        ("VEDL25APR24292.5CE", "VEDL", False),
        ("NIFTY", None, False),  # the index itself, or a cash symbol
        ("NIFTYBEES", None, False),
        ("NIFTYIT", None, False),
        ("NIFTY08DEC26", None, False),  # no FUT, no strike: not a contract
    ],
)
def test_an_index_contract_is_told_by_its_whole_underlying(symbol, underlying, index):
    assert regimes.underlying_of(symbol) == underlying
    assert regimes.index_derivative(symbol) is index
    names = regimes.index_underlyings([(symbol, "FUT")], cash={"RELIANCE", "BANKBARODA",
                                      "BANKINDIA", "M&M", "BAJAJ-AUTO", "360ONE", "VEDL"})  # fmt: skip
    assert regimes.index_derivative(symbol, names) is index


def test_the_master_says_which_futures_are_an_index_s():
    """Upstox marks every future FUT: an underlying listed as an index, or not listed as a cash
    stock, is an index's. A master that marks FUTIDX and FUTSTK says it outright."""
    upstox = [
        ("NIFTY29DEC26FUT", "FUT"), ("NIFTYFPI29DEC26FUT", "FUT"), ("FOCIT29DEC26FUT", "FUT"),
        ("NEWINDEX29DEC26FUT", "FUT"), ("RELIANCE29DEC26FUT", "FUT"), ("M&M29DEC26FUT", "FUT"),
        ("360ONE29DEC26FUT", "FUT"), ("BANKINDIA29DEC26FUT", "FUT"), ("NIFTY50X29DEC26FUT", "FUT"),
    ]  # fmt: skip
    cash = {"RELIANCE", "M&M", "360ONE", "BANKINDIA"}
    names = regimes.index_underlyings(upstox, cash=cash, indices={"NIFTY", "NIFTY50X"})
    assert {"FOCIT", "NEWINDEX", "NIFTYFPI", "NIFTY50X"} <= names
    assert not names & cash
    assert regimes.index_derivative("FOCIT31DEC2640000CE", names)
    assert not regimes.index_derivative("BANKINDIA29DEC26120CE", names)
    marked = [
        ("NIFTYFPI29DEC26FUT", "FUTIDX"),
        ("XYZ29DEC26FUT", "FUTSTK"),
        ("ABC29DEC26FUT", "FUTSTK"),
    ]
    assert regimes.index_underlyings(marked) == frozenset(regimes.INDEX_UNDERLYINGS) | {"NIFTYFPI"}


def test_a_master_with_no_cash_stocks_makes_no_guess():
    """Read while the morning's download refills the table: a stock is never taken for an index."""
    rows = [("RELIANCE29DEC26FUT", "FUT"), ("NIFTYFPI29DEC26FUT", "FUT")]
    assert regimes.index_underlyings(rows) == frozenset(regimes.INDEX_UNDERLYINGS)
    assert regimes.index_underlyings([]) == frozenset(regimes.INDEX_UNDERLYINGS)
