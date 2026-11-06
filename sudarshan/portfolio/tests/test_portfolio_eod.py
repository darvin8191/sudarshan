"""The closing record, the settle and the catch-up on a fake clock (vectors E1 to E4, design 9.1)."""

from __future__ import annotations

import asyncio
from datetime import date, datetime

import pytest
from portfolio_fakes import FakeFeed, FakeResolver, FakeUpstox
from test_portfolio_service import FERNET, settings_for
from test_portfolio_upstox import TOKEN

from sudarshan.autologin.schedule import IST
from sudarshan.portfolio import eod
from sudarshan.portfolio import store as store_module
from sudarshan.portfolio.book import Book
from sudarshan.portfolio.prices import PriceBook
from sudarshan.portfolio.service import Service
from sudarshan.portfolio.store import DB_FILENAME, PortfolioStore, Scope
from sudarshan.portfolio.timers import Schedule
from sudarshan.portfolio.upstox import UpstoxReader

OWNER = Scope.owner()
TCS = "NSE_EQ|INE467B01029"
NIFTY = "NSE_INDEX|Nifty 50"


def ist(d: int, h: int, m: int = 0, s: int = 0, mo: int = 10) -> int:
    return int(datetime(2026, mo, d, h, m, s, tzinfo=IST).timestamp() * 1000)


@pytest.fixture(autouse=True)
def _stop_thread():
    yield
    store_module.stop()


class Rig:
    def __init__(self, tmp_path, holidays="", extra=""):
        import dataclasses

        (tmp_path / "holidays.txt").write_text(holidays)
        (tmp_path / "extra.txt").write_text(extra)
        token = tmp_path / "token"
        token.write_text(TOKEN)
        self.feed, self.upstox = FakeFeed(), FakeUpstox()
        self.feed.url, self.upstox.url = self.feed.start(), self.upstox.start()
        settings = dataclasses.replace(
            settings_for(tmp_path), holidays_file=tmp_path / "holidays.txt",
            extra_days_file=tmp_path / "extra.txt", token_file=token,
        )  # fmt: skip
        self.svc = Service(settings, FERNET, jobs={})
        self.svc.store = PortfolioStore(tmp_path / "s" / DB_FILENAME, now=ist(1, 9))
        self.now = ist(26, 9)
        self.svc.clock = lambda: self.now
        self.svc.resolver = FakeResolver()
        self.svc.prices = PriceBook(self.feed.url, self.svc.resolver, self.svc.ensure_calendar())
        self.svc.upstox = UpstoxReader(token, self.upstox.url, clock=lambda: self.now)
        self.book = Book(self.svc.store)
        self.tcs = self.book.asset_create(
            OWNER, "a-1", "listed", now=ist(1, 9), exchange="NSE", symbol="TCS",
            isin="INE467B01029", name="TCS", instrument_key=TCS,
        )  # fmt: skip

    def tick(self, key, ltp, at, cp=None):
        self.feed.ticks[key] = {"key": key, "ltp": ltp, "ltt": at, **({"cp": cp} if cp else {})}

    def run(self, step, at):
        self.now = at
        return asyncio.run(self._go(step, at))

    async def _go(self, step, at):
        try:
            return await step(self.svc, at)
        finally:
            await self.svc.prices.aclose()
            await self.svc.upstox.aclose()

    def eod_row(self, day, asset=None):
        asset = asset or self.tcs
        return self.svc.store.read(OWNER, lambda tx: tx.price_eod_get(asset["id"], day))

    def job(self, name, day):
        return self.svc.store.read(OWNER, lambda tx: tx.job_get(name, day))

    def close(self):
        self.feed.stop()
        self.upstox.stop()


@pytest.fixture
def make(tmp_path):
    made = []

    def build(**kw):
        made.append(Rig(tmp_path, **kw))
        return made[-1]

    yield build
    for rig in made:
        rig.close()


def trade(rig, day=26, mo=10):
    rig.tick(TCS, 3500.55, ist(day, 15, 29, 58, mo), cp=3450.0)
    rig.tick(NIFTY, 25900.0, ist(day, 15, 29, 59, mo), cp=25800.0)


# ---- E1: a normal day ------------------------------------------------------------------------
def test_e1_the_record_is_taken_at_fifteen_fifty_five_once(make):
    rig = make()
    trade(rig)
    assert rig.run(eod.eod_step, ist(26, 15, 54, 59)) is None
    assert rig.eod_row(20261026) is None
    assert rig.run(eod.eod_step, ist(26, 15, 55)) == "ok"
    row = rig.eod_row(20261026)
    assert (row["price_u"], row["prev_close_u"], row["basis"], row["source"], row["late"]) == (
        35_005_500, 34_500_000, "last", "feed", False,
    )  # fmt: skip
    job = rig.job("eod", 20261026)
    assert job["status"] == "ok" and "1 prices" in job["detail"]
    assert rig.run(eod.eod_step, ist(26, 16, 30)) is None  # once a day
    nifty = rig.svc.store.read(OWNER, lambda tx: tx.index_close_get("nifty50", 20261026))
    assert nifty["close_u"] == 259_000_000 and nifty["source"] == "feed"


def test_the_upstox_part_is_written_through_the_reader(make):
    rig = make()
    trade(rig)
    rig.run(eod.eod_step, ist(26, 15, 55))
    head = rig.svc.store.read(OWNER, lambda tx: tx.rows("upstox_eod", "day"))[0]
    assert head["day"] == 20261026 and head["read_at"] == ist(26, 15, 55)
    # HOLDINGS: 10 x 3500.55 + 3 x 90 = 35_275.50 rupees; cost 10 x 3400.005 + 300
    assert head["holdings_value_paise"] == 3_500_550 + 27_000
    assert head["holdings_cost_paise"] == 3_400_005 + 30_000
    assert head["positions_pnl_paise"] == 66_625
    assert '"available_paise": 12500050' in head["funds"].replace(" ", "").replace(
        '"available_paise":12500050', '"available_paise": 12500050'
    )
    lines = rig.svc.store.read(OWNER, lambda tx: tx.rows("upstox_eod_holding", "instrument_key"))
    assert [ln["instrument_key"] for ln in lines] == ["NSE_EQ|INE000Z01011", TCS]
    assert lines[1]["asset_id"] == rig.tcs["id"] and lines[1]["qty"] == 10_000_000
    assert lines[0]["asset_id"] is None


def test_an_upstox_that_does_not_answer_leaves_the_rest_of_the_record(make):
    rig = make()
    trade(rig)
    for part in ("holdings", "positions", "funds", "mtf"):
        rig.upstox.status[part] = 503
    assert rig.run(eod.eod_step, ist(26, 15, 55)) == "ok"
    assert rig.eod_row(20261026) is not None
    assert rig.svc.store.read(OWNER, lambda tx: tx.rows("upstox_eod", "day")) == []
    assert "Upstox not read" in rig.job("eod", 20261026)["detail"]


def test_a_stock_with_no_price_today_gets_no_record(make):
    rig = make()
    rig.tick(TCS, 3500.0, ist(23, 15, 29))  # Friday's tick
    rig.run(eod.eod_step, ist(26, 15, 55))
    assert rig.eod_row(20261026) is None


# ---- E2: Muhurat, holiday, weekend ------------------------------------------------------------
def test_e2_muhurat_day_records_at_fifteen_fifty_five_of_its_own_window(make):
    rig = make(extra="2026-11-08 14:05-15:40  # Muhurat\n")
    rig.tick(TCS, 3600.0, ist(8, 15, 39, mo=11))
    assert rig.run(eod.eod_step, ist(8, 15, 54, 59, mo=11)) is None
    assert rig.run(eod.eod_step, ist(8, 15, 55, mo=11)) == "ok"
    assert rig.eod_row(20261108)["price_u"] == 36_000_000


def test_a_sunday_without_a_listing_and_a_holiday_have_no_record_and_no_job(make):
    rig = make(holidays="2026-11-10\n")
    rig.tick(TCS, 3600.0, ist(8, 15, 0, mo=11))
    assert rig.run(eod.eod_step, ist(8, 16, mo=11)) is None
    rig.tick(TCS, 3600.0, ist(10, 15, 0, mo=11))
    assert rig.run(eod.eod_step, ist(10, 16, mo=11)) is None
    assert rig.run(eod.settle_step, ist(10, 9, 30, mo=11)) is None
    assert rig.svc.store.read(OWNER, lambda tx: tx.job_runs()) == []


def test_the_times_come_from_the_sessions(make):
    rig = make(extra="2026-11-08 14:05-15:40\n")
    sched = Schedule(rig.svc.ensure_calendar(), eod_after_min=15)
    assert sched.eod_at(date(2026, 10, 26)) == ist(26, 15, 55)
    assert sched.settle_at(date(2026, 10, 26)) == ist(26, 9, 20)
    assert sched.eod_at(date(2026, 11, 8)) == ist(8, 15, 55, mo=11)
    assert sched.settle_at(date(2026, 11, 8)) == ist(
        8, 14, 25, mo=11
    )  # its window's session start (sessions.py) + 5
    assert sched.eod_at(date(2026, 10, 25)) is None  # a plain Sunday


# ---- E3: down at 15:55, up at 17:00 -----------------------------------------------------------
def test_e3_a_missed_record_runs_at_the_next_start_that_day_marked_late(make):
    rig = make()
    trade(rig)
    assert rig.run(eod.eod_step, ist(26, 17, 0)) == "late"
    assert rig.eod_row(20261026)["late"] is True
    assert rig.job("eod", 20261026)["status"] == "late"
    assert rig.run(eod.eod_step, ist(26, 17, 1)) is None
    assert rig.run(eod.eod_step, ist(27, 0, 30)) is None  # the next day does not take it


def test_five_minutes_of_delay_is_not_late(make):
    rig = make()
    trade(rig)
    assert rig.run(eod.eod_step, ist(26, 16, 0)) == "ok"
    assert rig.eod_row(20261026)["late"] is False


def test_a_day_missed_entirely_is_recorded_from_the_settle_and_has_no_upstox_part(make):
    rig = make()
    rig.tick(TCS, 3510.0, ist(27, 9, 15, 5), cp=3501.0)
    assert rig.run(eod.settle_step, ist(27, 9, 20)) == "ok"
    row = rig.eod_row(20261026)
    assert (row["price_u"], row["basis"], row["source"], row["late"]) == (
        35_010_000, "close", "cp", True,
    )  # fmt: skip
    assert rig.svc.store.read(OWNER, lambda tx: tx.rows("upstox_eod", "day")) == []
    assert "1 taken from the settle" in rig.job("settle", 20261027)["detail"]


# ---- E4: the settle ---------------------------------------------------------------------------
def settled(rig, tick_at, cp, quotes=None):
    trade(rig)
    rig.run(eod.eod_step, ist(26, 15, 55))
    rig.tick(TCS, 3510.0, tick_at, cp=cp)
    rig.tick(NIFTY, 25950.0, tick_at, cp=25920.0)
    if quotes:
        rig.feed.governor = "on"
        rig.feed.quotes.update(quotes)
    rig.run(eod.settle_step, ist(27, 9, 20))
    return rig.eod_row(20261026)


def test_e4_the_settle_by_cp_when_the_tick_is_from_today(make):
    rig = make()
    row = settled(rig, ist(27, 9, 15, 5), 3501.0)
    assert (row["price_u"], row["basis"], row["source"]) == (35_010_000, "close", "cp")
    assert row["prev_close_u"] == 34_500_000 and row["late"] is False
    nifty = rig.svc.store.read(OWNER, lambda tx: tx.index_close_get("nifty50", 20261026))
    assert (nifty["close_u"], nifty["source"]) == (259_200_000, "cp")


def test_e4_the_settle_by_quote_when_the_tick_is_not_from_today(make):
    rig = make()
    quotes = {
        TCS: {"instrument_token": TCS, "prev_close_price": 3502.5},
        NIFTY: {"instrument_token": NIFTY, "prev_close_price": 25921.5},
    }
    row = settled(rig, ist(26, 15, 29), 3450.0, quotes)  # a tick that is still Monday's
    assert (row["price_u"], row["basis"], row["source"]) == (35_025_000, "close", "quote")
    nifty = rig.svc.store.read(OWNER, lambda tx: tx.index_close_get("nifty50", 20261026))
    assert (nifty["close_u"], nifty["source"]) == (259_215_000, "quote")


def test_a_settle_with_no_official_close_leaves_the_record_as_it_was(make):
    rig = make()
    row = settled(rig, ist(26, 15, 29), 3450.0)  # old tick, governor off
    assert row["basis"] == "last" and row["price_u"] == 35_005_500
    assert "1 without a close" in rig.job("settle", 20261027)["detail"]
    assert rig.job("settle", 20261027)["status"] == "failed"


def test_a_settle_with_no_close_is_tried_again_at_the_next_start_that_day_only(make):
    rig = make()
    settled(rig, ist(26, 15, 29), 3450.0)  # old tick, governor off: nothing official
    assert rig.run(eod.settle_step, ist(27, 11, 0)) is None  # the same process does not ask again
    rig.feed.governor = "on"
    rig.feed.quotes[TCS] = {"instrument_token": TCS, "prev_close_price": 3502.5}
    rig.svc.settle_tried = None  # a restart
    assert rig.run(eod.settle_step, ist(27, 11, 5)) == "ok"
    assert rig.eod_row(20261026)["basis"] == "close"
    assert rig.job("settle", 20261027)["status"] == "ok"
    rig.svc.settle_tried = None
    assert rig.run(eod.settle_step, ist(27, 11, 6)) is None  # done


def test_the_retry_does_not_touch_a_day_that_has_frozen(make, monkeypatch):
    rig = make()
    settled(rig, ist(26, 15, 29), 3450.0)
    rig.feed.governor = "on"
    rig.feed.quotes[TCS] = {"instrument_token": TCS, "prev_close_price": 3502.5}
    monkeypatch.setattr(Schedule, "frozen", lambda self, day, today: True)
    rig.svc.settle_tried = None
    rig.run(eod.settle_step, ist(27, 11, 5))
    assert rig.eod_row(20261026)["basis"] == "last"


def test_the_settle_runs_once_and_not_before_nine_twenty(make):
    rig = make()
    trade(rig)
    rig.run(eod.eod_step, ist(26, 15, 55))
    rig.tick(TCS, 3510.0, ist(27, 9, 15, 5), cp=3501.0)
    assert rig.run(eod.settle_step, ist(27, 9, 19, 59)) is None
    assert rig.run(eod.settle_step, ist(27, 9, 20)) == "ok"
    assert rig.run(eod.settle_step, ist(27, 9, 25)) is None


# ---- frozen after three trading days (mutation target 22) -----------------------------------------
@pytest.mark.parametrize(
    ("today", "frozen"),
    [(20261026, False), (20261027, False), (20261028, False), (20261029, True), (20261030, True)],
)
def test_history_is_frozen_after_three_trading_days(make, today, frozen):
    rig = make()
    sched = Schedule(rig.svc.ensure_calendar())
    # Friday 23 October: Monday, Tuesday and Wednesday are the three days it can still change
    assert sched.frozen(20261023, today) is frozen


def test_a_holiday_is_not_one_of_the_three_days(make):
    rig = make(holidays="2026-10-27\n")
    assert Schedule(rig.svc.ensure_calendar()).frozen(20261023, 20261029) is False
    assert Schedule(rig.svc.ensure_calendar()).frozen(20261023, 20261030) is True


def test_a_frozen_day_is_never_rewritten_by_the_settle(make, monkeypatch):
    rig = make()
    trade(rig)
    rig.run(eod.eod_step, ist(26, 15, 55))
    rig.tick(TCS, 3510.0, ist(27, 9, 15, 5), cp=3501.0)
    monkeypatch.setattr(Schedule, "frozen", lambda self, day, today: True)
    rig.run(eod.settle_step, ist(27, 9, 20))
    assert rig.eod_row(20261026)["basis"] == "last"
