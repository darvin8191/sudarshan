"""When the sandbox settles an expiring NFO or BFO contract, on both sides of 7 Dec 2026
(sandbox/position_manager.py, ``expiry_settlement_timing`` = ``expiry_day_close``, its default).

Until 6 Dec 2026 every contract stops at 15:40. From 7 Dec an expiring index contract stops at
15:30 and a stock contract trades to 15:45. The MIS square-off time is the owner's setting and is
not read here. Real expiry days: NIFTY on Tuesdays (1 and 8 Dec), SENSEX on Thursdays (3 and
10 Dec), the stock monthly on the last Tuesday (24 Nov, 29 Dec).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from datetime import time as dt_time
from pathlib import Path

import pytest
import pytz

IST = pytz.timezone("Asia/Kolkata")
VECTORS = json.loads(Path(__file__).with_name("regimes_vectors.json").read_text(encoding="utf-8"))


@pytest.fixture
def pm(monkeypatch):
    import sandbox.position_manager as module

    settings = {"expiry_settlement_timing": "expiry_day_close"}
    monkeypatch.setattr(module, "get_config", lambda key, default=None: settings.get(key, default))
    monkeypatch.setattr(module, "settings", settings, raising=False)  # a test may change the mode
    return module


def at(day: date, hhmm: str) -> datetime:
    hours, minutes = map(int, hhmm.split(":"))
    return IST.localize(datetime(day.year, day.month, day.day, hours, minutes))


def minute_before(hhmm: str) -> str:
    hours, minutes = map(int, hhmm.split(":"))
    total = hours * 60 + minutes - 1
    return f"{total // 60:02d}:{total % 60:02d}"


@pytest.mark.parametrize("case", VECTORS["expiries"], ids=[c["symbol"] for c in VECTORS["expiries"]])
def test_each_contract_settles_at_its_own_stop(pm, case):
    expiry = date.fromisoformat(case["expiry"])
    stop = case["stop"]
    assert pm.expiry_close_time(expiry, case["exchange"], case["symbol"]) == dt_time(
        *map(int, stop.split(":"))
    )
    assert not pm.is_contract_expired_now(
        expiry, case["exchange"], now=at(expiry, minute_before(stop)), symbol=case["symbol"]
    )
    assert pm.is_contract_expired_now(
        expiry, case["exchange"], now=at(expiry, stop), symbol=case["symbol"]
    )


def test_thursday_3_december_a_sensex_contract_still_trades_to_15_40(pm):
    expiry = date(2026, 12, 3)
    assert not pm.is_contract_expired_now(expiry, "BFO", now=at(expiry, "15:35"),
                                          symbol="SENSEX03DEC2682000PE")  # fmt: skip
    assert pm.is_contract_expired_now(expiry, "BFO", now=at(expiry, "15:40"),
                                      symbol="SENSEX03DEC2682000PE")  # fmt: skip


def test_tuesday_8_december_a_nifty_contract_stops_at_15_30_and_a_stock_one_trades_on(pm):
    expiry = date(2026, 12, 8)
    nifty, stock = "NIFTY08DEC2625900CE", "RELIANCE08DEC261400CE"
    assert pm.is_contract_expired_now(expiry, "NFO", now=at(expiry, "15:30"), symbol=nifty)
    assert not pm.is_contract_expired_now(expiry, "NFO", now=at(expiry, "15:30"), symbol=stock)
    assert not pm.is_contract_expired_now(expiry, "NFO", now=at(expiry, "15:44"), symbol=stock)
    assert pm.is_contract_expired_now(expiry, "NFO", now=at(expiry, "15:45"), symbol=stock)


def test_without_a_symbol_the_f_and_o_close_of_the_day_applies(pm):
    assert pm.expiry_close_time(date(2026, 12, 4), "NFO") == dt_time(15, 40)
    assert pm.expiry_close_time(date(2026, 12, 8), "NFO") == dt_time(15, 45)


def test_other_exchanges_keep_their_own_close(pm):
    for day in (date(2026, 12, 4), date(2026, 12, 10)):
        assert pm.expiry_close_time(day, "MCX", "CRUDEOIL17DEC26FUT") == dt_time(23, 30)
        assert pm.expiry_close_time(day, "CDS", "USDINR29DEC26FUT") == dt_time(17, 0)
        assert pm.expiry_close_time(day, "NSE", "RELIANCE") == dt_time(15, 30)
    assert "NFO" not in pm.EXCHANGE_CLOSE_TIMES and "BFO" not in pm.EXCHANGE_CLOSE_TIMES


def test_the_days_around_expiry_and_the_next_day_mode_are_unchanged(pm):
    expiry = date(2026, 12, 8)
    nifty = "NIFTY08DEC2625900CE"
    assert pm.is_contract_expired_now(expiry, "NFO", now=at(date(2026, 12, 9), "09:00"), symbol=nifty)
    assert not pm.is_contract_expired_now(expiry, "NFO", now=at(date(2026, 12, 7), "15:50"),
                                          symbol=nifty)  # fmt: skip
    assert not pm.is_contract_expired_now(None, "NFO", now=at(expiry, "15:50"), symbol=nifty)
    pm.settings["expiry_settlement_timing"] = "next_day"
    assert not pm.is_contract_expired_now(expiry, "NFO", now=at(expiry, "23:59"), symbol=nifty)


# ---- index contracts from the master (review L1) --------------------------------------------------
@pytest.fixture
def master():
    """OpenAlgo's symbol table in the throwaway database, with Upstox's marks (every future FUT)."""
    from database.symbol import Base, SymToken, db_session, engine
    from sudarshan import index_contracts

    Base.metadata.create_all(engine, tables=[SymToken.__table__])

    def fill(rows):
        for symbol, exchange, kind in rows:
            db_session.add(SymToken(symbol=symbol, brsymbol=symbol, exchange=exchange,
                                    instrumenttype=kind, token=f"{exchange}|{symbol}"))  # fmt: skip
        db_session.commit()
        index_contracts.forget()

    yield fill
    db_session.query(SymToken).delete()
    db_session.commit()
    db_session.remove()
    index_contracts.forget()


UPSTOX_MASTER = [
    ("NIFTY29DEC26FUT", "NFO", "FUT"),
    ("NIFTYFPI29DEC26FUT", "NFO", "FUT"),
    ("RELIANCE29DEC26FUT", "NFO", "FUT"),
    ("FOCIT31DEC26FUT", "BFO", "FUT"),  # BSE's Focused IT, under a name no list of ours holds
    ("RELIANCE", "NSE", "EQ"),
    ("NIFTY", "NSE_INDEX", "INDEX"),
]


def test_an_index_contract_the_master_lists_settles_at_15_30_from_7_december(pm, master):
    """NIFTYFPI and BSE's Focused IT were read as stock contracts: from 7 Dec they settled at 15:45
    in Analyze mode instead of 15:30. The master says they are an index's; RELIANCE stays a stock."""
    master(UPSTOX_MASTER)
    dec29, dec31 = date(2026, 12, 29), date(2026, 12, 31)
    assert pm.expiry_close_time(dec29, "NFO", "NIFTYFPI29DEC2618000CE") == dt_time(15, 30)
    assert pm.expiry_close_time(dec31, "BFO", "FOCIT31DEC2640000CE") == dt_time(15, 30)
    assert pm.expiry_close_time(dec29, "NFO", "RELIANCE29DEC261400CE") == dt_time(15, 45)
    assert pm.is_contract_expired_now(dec31, "BFO", now=at(dec31, "15:30"),
                                      symbol="FOCIT31DEC2640000CE")  # fmt: skip
    # Before the change every contract stops with the F&O close.
    dec1 = date(2026, 12, 1)
    assert pm.expiry_close_time(dec1, "NFO", "NIFTYFPI01DEC2618000CE") == dt_time(15, 40)
    assert pm.expiry_close_time(dec1, "NFO", "RELIANCE01DEC261400CE") == dt_time(15, 40)


def test_a_master_being_downloaded_leaves_the_known_index_names(pm, master):
    """No futures in the table (the morning's download empties it first): the names every reader
    knows stand in, and nothing is kept, so the next question reads the master again."""
    from sudarshan import index_contracts

    master([])
    assert index_contracts.underlyings() == index_contracts.KNOWN
    fpi = pm.expiry_close_time(date(2026, 12, 29), "NFO", "NIFTYFPI29DEC2618000CE")
    assert fpi == dt_time(15, 30)  # a name every reader knows
    focit = pm.expiry_close_time(date(2026, 12, 31), "BFO", "FOCIT31DEC2640000CE")
    assert focit == dt_time(15, 45)  # known only from the master, which has nothing yet
    from database.symbol import SymToken, db_session

    for symbol, exchange, kind in UPSTOX_MASTER:  # filled now, the cache left as it is
        db_session.add(SymToken(symbol=symbol, brsymbol=symbol, exchange=exchange,
                                instrumenttype=kind, token=f"{exchange}|{symbol}"))  # fmt: skip
    db_session.commit()
    assert "FOCIT" in index_contracts.underlyings().indices  # read at once, not in ten minutes
    assert index_contracts.has_futures("RELIANCE") and not index_contracts.has_futures("NIFTY")


# ---- an order after the contract's stop (review L5) -----------------------------------------------
@pytest.mark.parametrize(
    ("expiry", "symbol", "clock", "stop"),
    [
        (date(2026, 12, 1), "NIFTY01DEC2625900CE", "15:39", None),  # before the change: 15:40
        (date(2026, 12, 1), "NIFTY01DEC2625900CE", "15:40", "15:40"),
        (date(2026, 12, 8), "NIFTY08DEC2625900CE", "15:29", None),
        (date(2026, 12, 8), "NIFTY08DEC2625900CE", "15:30", "15:30"),  # an index's from 7 Dec
        (date(2026, 12, 8), "NIFTY08DEC2625900CE", "15:35", "15:30"),
        (date(2026, 12, 29), "RELIANCE29DEC261400CE", "15:35", None),  # a stock's trades on
        (date(2026, 12, 29), "RELIANCE29DEC261400CE", "15:45", "15:45"),
    ],
)
def test_the_sandbox_says_a_contract_has_stopped_trading(pm, expiry, symbol, clock, stop):
    words = pm.stopped_contract_words(symbol, "NFO", expiry.strftime("%d-%b-%y").upper(),
                                      now=at(expiry, clock))  # fmt: skip
    expected = None
    if stop is not None:
        expected = (
            f"This contract stopped trading at {stop} on its expiry day; "
            "the exchange would refuse it."
        )
    assert words == expected
    # The settlement timing does not move the exchange's stop.
    pm.settings["expiry_settlement_timing"] = "next_day"
    assert pm.stopped_contract_words(symbol, "NFO", expiry, now=at(expiry, clock)) == expected
    # The day before trades; the day after has stopped; other exchanges are not asked.
    assert pm.stopped_contract_words(symbol, "NFO", expiry, now=at(date(2026, 11, 30), "15:50")) is None
    assert pm.stopped_contract_words(symbol, "MCX", expiry, now=at(expiry, "23:00")) is None


@pytest.mark.parametrize(("clock", "refused"), [("15:29", False), ("15:30", True)])
def test_analyze_mode_refuses_an_order_on_8_december_after_the_stop(pm, monkeypatch, clock, refused):
    """Tue 8 Dec 15:35 in Analyze mode, a MARKET buy of the expiring NIFTY contract filled at its last
    price; the exchange refuses it. It is refused with a sentence from 15:30, before any fill, fund
    or position is touched."""
    from types import SimpleNamespace

    import sandbox.order_manager as order_manager

    day = date(2026, 12, 8)
    monkeypatch.setattr(pm, "_now_ist", lambda: at(day, clock))
    monkeypatch.setattr(order_manager, "get_symbol_info", lambda symbol, exchange: SimpleNamespace(
        symbol=symbol, exchange=exchange, expiry="08-DEC-26", lotsize=65))  # fmt: skip
    reached = []

    def margins_reached(self, *args, **kwargs):  # the first step after the checks this adds
        reached.append(True)
        raise RuntimeError("stop here: the order went past the expiry check")

    monkeypatch.setattr(order_manager.OrderManager, "_validate_order", lambda self, data: (True, ""))
    manager = order_manager.OrderManager("tester")
    monkeypatch.setattr(manager.fund_manager, "check_and_block_margin", margins_reached, raising=False)
    order = {"symbol": "NIFTY08DEC2625900CE", "exchange": "NFO", "action": "BUY", "quantity": 65,
             "price_type": "MARKET", "product": "NRML"}  # fmt: skip
    try:
        ok, answer, code = manager.place_order(order)
    except RuntimeError:
        ok, answer, code = None, {}, None
    if refused:
        assert (ok, code) == (False, 400)
        assert answer["message"] == (
            "This contract stopped trading at 15:30 on its expiry day; the exchange would refuse it."
        )
        assert answer["mode"] == "analyze"
    else:
        assert answer.get("message", "").find("stopped trading") == -1
