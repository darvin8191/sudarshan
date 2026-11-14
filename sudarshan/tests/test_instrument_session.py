"""An instrument's own session facts (sudarshan/instrument_session.py, v0.8.1 review M1, M2, M3),
from the shared vectors on both sides of 7 Dec 2026: when an expiring contract stops on its expiry
day, and the window after the cash close in which a closing print belongs to the last candle. The
chart and the order dialogs read them (frontend lib/trading/instrumentSession.ts)."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from sudarshan import index_contracts
from sudarshan import instrument_session as facts_module
from sudarshan.alerts.market import sessions
from sudarshan.autologin.schedule import IST

VECTORS = json.loads(Path(__file__).with_name("regimes_vectors.json").read_text(encoding="utf-8"))


def ms(day: date, hhmm: str) -> int:
    hours, minutes = map(int, hhmm.split(":"))
    return sessions.epoch_ms(day, hours * 60 + minutes)


def hhmm(day: date, epoch_ms: int) -> str:
    minutes = (epoch_ms - sessions.epoch_ms(day, 0)) // 60_000
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@pytest.mark.parametrize("case", VECTORS["days"], ids=[case["date"] for case in VECTORS["days"]])
def test_the_closing_window_is_the_server_s_on_each_vector_day(case):
    """A stock with futures and an index from 7 Dec; none before, and none for a stock without."""
    day = date.fromisoformat(case["date"])
    end = ms(day, case["cash"][1])
    for exchange, auction in (("NSE", True), ("BSE", True), ("NSE_INDEX", False)):
        found = facts_module.closing_of(exchange, day, end, auction=auction)
        assert (found and [hhmm(day, found[0]), hhmm(day, found[1])]) == case["closing"]
        if found:  # the same window market alerts and the screener use
            assert found == sessions.closing_window(
                "NSE_INDEX" if exchange.endswith("INDEX") else "NSE_EQ", day, trading_day=True
            )
    assert facts_module.closing_of("NSE", day, end, auction=False) is None
    assert facts_module.closing_of("NFO", day, end, auction=True) is None
    assert facts_module.closing_of("NSE", day, None, auction=True) is None  # a holiday


@pytest.mark.parametrize(
    "case", VECTORS["expiries"], ids=[c["symbol"] for c in VECTORS["expiries"]]
)
def test_an_expiring_contract_stops_at_its_regime_s_stop(case):
    expiry = date.fromisoformat(case["expiry"])
    fo_end = ms(expiry, "15:45" if expiry >= date(2026, 12, 7) else "15:40")
    stop = facts_module.stop_of(case["exchange"], expiry, expiry, index=case["index"],
                                session_end_ms=fo_end)  # fmt: skip
    assert hhmm(expiry, stop) == case["stop"]
    other = date(2026, 12, 9)
    assert facts_module.stop_of(case["exchange"], other, expiry, index=case["index"],
                                session_end_ms=ms(other, "15:45")) is None  # fmt: skip
    assert facts_module.stop_of(case["exchange"], expiry, expiry, index=case["index"],
                                session_end_ms=None) is None  # fmt: skip
    # A special session that ends earlier ends the contract with it.
    early = ms(expiry, "15:00")
    assert facts_module.stop_of(case["exchange"], expiry, expiry, index=case["index"],
                                session_end_ms=early) == early  # fmt: skip


@pytest.fixture
def world(monkeypatch):
    """OpenAlgo's calendar (normal days, a holiday on 25 Dec) and master, as plain functions."""
    import database.market_calendar_db as calendar
    import database.token_db as token_db

    expiries = {
        ("NIFTY08DEC2625900CE", "NFO"): "08-DEC-26",
        ("NIFTY01DEC2625900CE", "NFO"): "01-DEC-26",
        ("RELIANCE29DEC261400CE", "NFO"): "29-DEC-26",
        ("NIFTYFPI29DEC2618000CE", "NFO"): "29-DEC-26",
    }

    def window(day, exchange):
        if day == date(2026, 12, 25) or day.weekday() >= 5:
            return None
        close = "15:30" if exchange in ("NSE", "BSE") else "15:45"
        if exchange in ("NFO", "BFO") and day < date(2026, 12, 7):
            close = "15:40"
        return {"start_ms": ms(day, "09:15"), "end_ms": ms(day, close), "is_special": False}

    def info(symbol, exchange):
        found = expiries.get((symbol, exchange))
        return SimpleNamespace(expiry=found) if found else None

    monkeypatch.setattr(calendar, "get_effective_session_window", window)
    monkeypatch.setattr(token_db, "get_symbol_info", info)
    monkeypatch.setattr(index_contracts, "underlyings", lambda: index_contracts.Underlyings(
        frozenset(index_contracts.regimes.INDEX_UNDERLYINGS), frozenset({"RELIANCE"})))  # fmt: skip
    return facts_module.facts


def test_tuesday_8_december_the_expiring_nifty_contract_stops_at_15_30(world):
    day = date(2026, 12, 8)
    found = world("NIFTY08DEC2625900CE", "NFO", ms(day, "12:00"))
    assert found == {
        "day": "2026-12-08", "stops_at": ms(day, "15:30"), "closing": None, "delivery": None,
    }  # fmt: skip
    before = date(2026, 12, 1)  # until 6 Dec every contract stops with the F&O close
    assert world("NIFTY01DEC2625900CE", "NFO", ms(before, "12:00"))["stops_at"] == ms(
        before, "15:40"
    )
    dec29 = date(2026, 12, 29)
    assert world("RELIANCE29DEC261400CE", "NFO", ms(dec29, "09:00"))["stops_at"] == ms(
        dec29, "15:45"
    )
    assert world("NIFTYFPI29DEC2618000CE", "NFO", ms(dec29, "09:00"))["stops_at"] == ms(
        dec29, "15:30"
    )
    # Not its expiry day, or a contract the master does not list: nothing.
    assert world("NIFTY08DEC2625900CE", "NFO", ms(date(2026, 12, 7), "12:00"))["stops_at"] is None
    assert world("NIFTY15DEC2625900CE", "NFO", ms(day, "12:00"))["stops_at"] is None


def test_thursday_10_december_a_stock_with_futures_and_an_index_close_after_15_30(world):
    day = date(2026, 12, 10)
    window = {"from": ms(day, "15:30"), "until": ms(day, "15:45")}
    assert world("RELIANCE", "NSE", ms(day, "11:00"))["closing"] == window
    assert world("NIFTY", "NSE_INDEX", ms(day, "11:00"))["closing"] == window
    assert world("TCS", "NSE", ms(day, "11:00"))["closing"] is None  # no futures, no auction
    friday = date(2026, 12, 4)  # before the change the print comes inside the session
    assert world("RELIANCE", "NSE", ms(friday, "11:00"))["closing"] is None
    assert world("NIFTY", "NSE_INDEX", ms(friday, "11:00"))["closing"] is None
    christmas = date(2026, 12, 25)
    assert world("RELIANCE", "NSE", ms(christmas, "11:00"))["closing"] is None


def test_the_route_answers_the_facts_and_refuses_a_request_without_an_instrument(
    world, monkeypatch
):
    from flask import Flask

    import blueprints.sudarshan_session as route
    import utils.session as user_session

    monkeypatch.setattr(user_session, "session_ended_elsewhere", lambda: False)
    monkeypatch.setattr(route, "_now_ms", lambda: ms(date(2026, 12, 8), "12:00"))
    app = Flask("sudarshan-session-tests")
    app.secret_key = "test-only-secret-key-" + "1" * 32  # pragma: allowlist secret
    app.register_blueprint(route.sudarshan_session_bp)
    client = app.test_client()
    path = "/sudarshan/session/instrument"
    asked = {"symbol": "NIFTY08DEC2625900CE", "exchange": "NFO"}
    refused = client.get(path, query_string=asked, headers={"Accept": "application/json"})
    assert refused.status_code == 401  # no session: the logged-in owner only
    with client.session_transaction() as session:
        session.update(logged_in=True, user="owner", login_time=datetime.now(IST).isoformat())
    answer = client.get(path, query_string=asked)
    assert answer.status_code == 200
    assert answer.get_json()["data"]["stops_at"] == ms(date(2026, 12, 8), "15:30")
    missing = client.get(path, query_string={"symbol": "NIFTY08DEC2625900CE"})
    assert missing.status_code == 400 and missing.get_json()["message"] == route.PICK_AGAIN


def test_a_stock_contract_in_its_expiry_week_is_settled_by_delivery_an_index_s_never(world):
    """B7 (M5.21): said from six days before the expiry to the expiry day, for a stock only."""
    stock, said = "RELIANCE29DEC261400CE", []
    for day in (date(2026, 12, 22), date(2026, 12, 23), date(2026, 12, 28), date(2026, 12, 29)):
        said.append(world(stock, "NFO", ms(day, "10:00"))["delivery"])
    assert said == [None, "2026-12-29", "2026-12-29", "2026-12-29"]
    assert world(stock, "NFO", ms(date(2026, 12, 30), "10:00"))["delivery"] is None  # expired
    for index in ("NIFTYFPI29DEC2618000CE", "NIFTY08DEC2625900CE"):
        for day in (date(2026, 12, 7), date(2026, 12, 8), date(2026, 12, 28), date(2026, 12, 29)):
            assert world(index, "NFO", ms(day, "10:00"))["delivery"] is None
    # a share, an index and a contract the master does not list say nothing
    week = ms(date(2026, 12, 28), "10:00")
    assert world("RELIANCE", "NSE", week)["delivery"] is None
    assert world("NIFTY", "NSE_INDEX", week)["delivery"] is None
    assert world("RELIANCE29DEC261500CE", "NFO", week)["delivery"] is None
