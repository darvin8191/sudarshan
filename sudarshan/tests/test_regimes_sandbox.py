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
