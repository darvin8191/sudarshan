"""Recording fills (JB3'): the nudge and its debounce, the portfolio's journal purpose as the one
account reader, OpenAlgo's records read-only, the labels and the 60 s rule (design 3.1-3.3, 8.2;
cross-design Y4, Y17, Y29, Y30). Against fakes on 127.0.0.1 and throw-away files."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from journal_fakes import (
    KEYS,
    PORTFOLIO_KEY,
    FakePortfolio,
    make_latency_db,
    make_openalgo_db,
    make_risk_db,
    make_sandbox_db,
    sealed,
    trade_row,
)

from sudarshan.journal import capture as capture_module
from sudarshan.journal import nudge as nudge_module
from sudarshan.journal import store
from sudarshan.journal.capture import (
    Capture,
    capture_for,
    fill_from_sandbox,
    fill_from_trade,
    label_of,
    upstox_product,
)
from sudarshan.journal.config import Settings
from sudarshan.journal.core.money import IST
from sudarshan.journal.nudge import Debounce, Nudges
from sudarshan.journal.openalgo_ro import (
    Listing,
    OpenAlgoRecords,
    Placement,
    RecordsUnavailable,
    expiry_day,
    master_expiry,
    placements_of,
)
from sudarshan.journal.portfolio import PortfolioLink, PortfolioUnavailable
from sudarshan.journal.risk_ro import NOT_READ, RiskReader
from sudarshan.journal.service import Service
from sudarshan.journal.store import JournalStore, Scope
from sudarshan.tests.fdwatch import FdWatch

OWNER = Scope.owner()
DAY = 20261109


def ist_ms(day: int, clock: str) -> int:
    h, m, s = (int(x) for x in clock.split(":"))
    stamp = datetime(day // 10000, day // 100 % 100, day % 100, h, m, s, tzinfo=IST)
    return int(stamp.timestamp() * 1000)


NOW = ist_ms(DAY, "10:30:00")
OPT = {"token": "NSE_FO|50001", "symbol": "NIFTY25NOV2625000CE", "exchange": "NFO",
       "expiry": "25-NOV-26", "lotsize": 65, "instrumenttype": "CE", "name": "NIFTY"}  # fmt: skip
STOCK = {"token": "NSE_EQ|INE000A00001", "symbol": "ACME", "exchange": "NSE",
         "lotsize": 1, "instrumenttype": "EQ"}  # fmt: skip


def placed(order_id, strategy="ORB", api_type="placeorder", at="2026-11-09 10:00:59", **more):
    request = {"apikey": "secret-test-key", "strategy": strategy, "symbol": "NIFTY25NOV2625000CE",
               "exchange": "NFO", "action": "BUY", "quantity": "65", "pricetype": "LIMIT",
               "product": "MIS", "price": "100.05", "trigger_price": "0", **more}  # fmt: skip
    return {"api_type": api_type, "request_data": request,
            "response_data": {"status": "success", "orderid": order_id}, "created_at": at}  # fmt: skip


# ---- the mapping, pure --------------------------------------------------------------------------
def test_upstox_products_are_written_as_openalgo_writes_them():
    assert upstox_product("I", "NFO") == "MIS" and upstox_product("CO", "NSE") == "MIS"
    assert upstox_product("D", "NFO") == "NRML" and upstox_product("D", "MCX") == "NRML"
    assert upstox_product("D", "NSE") == "CNC" and upstox_product("MTF", "NSE") == "CNC"
    assert upstox_product("X", "NFO") is None and upstox_product(None, "NFO") is None


def test_a_trade_row_becomes_a_fill_in_units_price_u_and_openalgos_names():
    listing = Listing("NSE_FO|50001", "NIFTY25NOV2625000CE", "NFO", 20261125, 65, "CE")
    at = ist_ms(DAY, "10:01:00")
    fill = fill_from_trade(
        trade_row("t1", "o1", at=at, placed=at - 900), listing, "stream_read", NOW
    )
    assert fill == {
        "mode": "live", "day": DAY, "trade_id": "t1", "order_id": "o1", "exchange": "NFO",
        "symbol": "NIFTY25NOV2625000CE", "instrument_key": "NSE_FO|50001", "product": "MIS",
        "action": "BUY", "qty": 65, "price_u": 1_000_000, "value_paise": 650_000,
        "exch_ts_ms": at, "order_ts_ms": at - 900, "source": "stream_read",
    }  # fmt: skip


def test_without_the_master_the_segment_names_the_exchange_and_upstox_the_symbol():
    fill = fill_from_trade(trade_row("t1", "o1", product="D", at=NOW), None, "stream_read", NOW)
    assert (fill["exchange"], fill["symbol"], fill["product"]) == (
        "NFO", "NIFTY 25000 CE 25 NOV 26", "NRML",
    )  # fmt: skip
    no_time = fill_from_trade(trade_row("t2", "o1"), None, "stream_read", NOW)
    assert no_time["exch_ts_ms"] == NOW and no_time["day"] == DAY


@pytest.mark.parametrize(
    "change",
    [{"product": "ZZ"}, {"qty_u": 65_500_000}, {"qty_u": 0}, {"transaction_type": "HOLD"},
     {"trade_id": ""}, {"order_id": None}, {"price_u": None}],
)  # fmt: skip
def test_a_row_the_journal_does_not_model_is_left_out(change):
    assert fill_from_trade({**trade_row("t1", "o1", at=NOW), **change}, None, "x", NOW) is None


def test_a_sandbox_trade_becomes_a_sandbox_fill():
    row = {"tradeid": "77", "orderid": "S1", "symbol": "ACME", "exchange": "NSE", "action": "BUY",
           "quantity": 10, "price": "250.35", "product": "CNC", "strategy": "Test",
           "trade_timestamp": "2026-11-09 10:05:00.123456"}  # fmt: skip
    listing = Listing("NSE_EQ|INE000A00001", "ACME", "NSE", None, 1, "EQ")
    fill = fill_from_sandbox(row, listing)
    assert fill["mode"] == "sandbox" and fill["trade_id"] == "sb:77"
    assert fill["price_u"] == 2_503_500 and fill["value_paise"] == 250_350
    assert fill["exch_ts_ms"] == ist_ms(DAY, "10:05:00") + 123
    assert fill["instrument_key"] == "NSE_EQ|INE000A00001" and fill["source"] == "sandbox_book"
    assert fill_from_sandbox({**row, "quantity": 0}, listing) is None
    assert fill_from_sandbox({**row, "product": "BO"}, None) is None


# ---- placements and labels ---------------------------------------------------------------------
def test_placements_are_found_by_order_id_with_strategy_type_and_prices():
    rows = [
        placed("o1"),
        placed("o1", strategy="Later", at="2026-11-09 10:05:00"),
        {"api_type": "basketorder",
         "request_data": {"strategy": "Basket", "orders": [
             {"symbol": "A", "pricetype": "MARKET", "product": "NRML"},
             {"symbol": "B", "pricetype": "LIMIT", "product": "MIS", "price": "12.5"}]},
         "response_data": {"status": "success", "results": [
             {"symbol": "A", "orderid": "b1"}, {"symbol": "B", "orderid": "b2"}]},
         "created_at": "2026-11-09 11:00:00"},
        {"api_type": "modifyorder", "request_data": {"strategy": "Mod"},
         "response_data": {"orderid": "m1"}, "created_at": "2026-11-09 11:00:00"},
        placed("o2", strategy="  " + "x" * 60),
        placed("o3", strategy=""),
    ]  # fmt: skip
    found = placements_of(rows)
    assert found["o1"].strategy == "ORB" and found["o1"].pricetype == "LIMIT"
    assert found["o1"].limit_price_u == 1_000_500 and found["o1"].trigger_price_u is None
    assert found["o1"].placed_ms == ist_ms(DAY, "10:00:59")
    assert (found["b1"].pricetype, found["b2"].product, found["b2"].limit_price_u) == (
        "MARKET", "MIS", 125_000,
    )  # fmt: skip
    assert "m1" not in found  # a modify names an order; it never placed it
    assert found["o2"].strategy == "x" * 40 and found["o3"].strategy == "Manual"
    assert "secret-test-key" not in repr(found)


def test_the_upstox_app_label_waits_sixty_seconds():
    """Mutation target 23: no placement 59 s after the first fill is still unknown; at 60 s the
    order was placed outside OpenAlgo."""
    ref = {"order_id": "o9", "first_fill_ms": NOW}
    assert label_of("live", ref, None, NOW + 59_999) is None
    assert label_of("live", ref, None, NOW + 60_000) == {
        "strategy": "Upstox app", "surface": "upstox_app",
    }  # fmt: skip
    assert label_of("live", ref, Placement("o9", "placeorder", "ORB", NOW), NOW)["surface"] == "api"


def test_a_sandbox_order_never_waits_and_takes_its_trades_strategy():
    ref = {"order_id": "S1", "first_fill_ms": NOW}
    assert label_of("sandbox", ref, None, NOW, "Test run") == {
        "strategy": "Test run",
        "surface": "api",
    }
    assert label_of("sandbox", ref, None, NOW, None)["strategy"] == "Manual"


# ---- OpenAlgo's files, read-only ---------------------------------------------------------------
def snapshot(folder: Path) -> dict:
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in folder.iterdir()}


@pytest.fixture
def files(tmp_path):
    db = tmp_path / "db"
    make_openalgo_db(
        db / "openalgo.db",
        symtoken=[OPT, STOCK,
                  {"token": "NSE_FO|60001", "symbol": "ACME25NOV26300CE", "exchange": "NFO",
                   "expiry": "25-NOV-26", "instrumenttype": "CE", "name": "ACME"},
                  {"token": "NSE_FO|50002", "symbol": "NIFTY18NOV2625000CE", "exchange": "NFO",
                   "expiry": "18-NOV-26", "instrumenttype": "CE", "name": "NIFTY"}],
        order_logs=[placed("o1"), placed("o2", strategy="Breakout", at="2026-11-09 10:10:00")],
        analyzer_logs=[placed("S1", strategy="Sandbox ORB")],
    )  # fmt: skip
    make_latency_db(db / "latency.db", [("o1", 412.4, 455.6)])
    make_sandbox_db(
        db / "sandbox.db",
        [{"tradeid": "77", "orderid": "S1", "symbol": "ACME", "exchange": "NSE", "action": "BUY",
          "quantity": 10, "price": 250.35, "product": "CNC", "strategy": None,
          "trade_timestamp": "2026-11-09 10:05:00.000000"},
         {"tradeid": "78", "orderid": "S2", "symbol": "ACME", "exchange": "NSE", "action": "SELL",
          "quantity": 10, "price": 251.0, "product": "CNC", "strategy": "Own",
          "trade_timestamp": "2026-11-09 10:06:00.000000"},
         {"tradeid": "70", "orderid": "S0", "symbol": "ACME", "exchange": "NSE", "action": "BUY",
          "quantity": 1, "price": 1.0, "product": "CNC", "strategy": None,
          "trade_timestamp": "2026-11-08 15:00:00.000000"}],
    )  # fmt: skip
    make_risk_db(db / "sudarshan_risk.db", kill_switch=False,
                 locks=[("live", "2026-11-09", "2026-11-09 08:12:00")])  # fmt: skip
    return db


def test_the_records_are_read_without_changing_the_files(files):
    before = snapshot(files)
    records = OpenAlgoRecords(files / "openalgo.db", files / "latency.db", files / "sandbox.db")
    assert set(records.placements("live", DAY)) == {"o1", "o2"}
    assert set(records.placements("sandbox", DAY)) == {"S1"}
    assert records.latency(["o1", "o2"]) == {"o1": (412, 456)}
    assert (
        records.instruments(["NSE_FO|50001", "NSE_FO|404"])["NSE_FO|50001"].expiry_day == 20261125
    )
    assert records.instrument_by_symbol([("ACME", "NSE")])[("ACME", "NSE")].key == STOCK["token"]
    assert [r["tradeid"] for r in records.sandbox_trades(DAY)] == ["77", "78"]
    assert snapshot(files) == before  # nothing written, no side file made


def test_a_missing_file_is_unavailable_and_never_made(tmp_path):
    records = OpenAlgoRecords(tmp_path / "none.db", tmp_path / "none2.db", None)
    with pytest.raises(RecordsUnavailable):
        records.placements("live", DAY)
    with pytest.raises(RecordsUnavailable):
        records.latency(["o1"])
    with pytest.raises(RecordsUnavailable):
        records.sandbox_trades(DAY)
    assert list(tmp_path.iterdir()) == []


def test_index_expiry_days_come_from_the_masters_index_options(files):
    records = OpenAlgoRecords(files / "openalgo.db")
    assert records.index_expiry_days([20261118, 20261125, 20261126]) == {20261118, 20261125}
    assert master_expiry(20261125) == "25-NOV-26" and expiry_day("25-NOV-26") == 20261125
    assert expiry_day("2026-11-25") is None


def test_the_risk_state_is_two_tables_read_only(files):
    """Y30: reading the kill switch never writes to its database (no table, row or side file)."""
    before = snapshot(files)
    state = RiskReader(files / "sudarshan_risk.db").state(DAY)
    assert state.read and state.kill_switch is False and state.kill_since_ms is None
    assert state.lock_clock() == "13:42"  # 08:12 UTC
    assert RiskReader(files / "sudarshan_risk.db").state(20261110).lock_at_ms is None
    assert snapshot(files) == before


def test_a_risk_file_that_cannot_be_read_says_not_read(tmp_path):
    assert RiskReader(tmp_path / "missing.db").state(DAY) == NOT_READ
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"not a database at all")
    assert RiskReader(broken).state(DAY) == NOT_READ


# ---- capture, end to end ------------------------------------------------------------------------
class Rig:
    def __init__(self, tmp_path: Path, files: Path):
        self.portfolio = FakePortfolio()
        url = self.portfolio.start()
        settings = Settings(
            enabled=True,
            state_dir=tmp_path / "state",
            portfolio_url=url,
            openalgo_db=files / "openalgo.db",
            latency_db=files / "latency.db",
            sandbox_db=files / "sandbox.db",
            risk_db=files / "sudarshan_risk.db",
            deployed_tag_file=tmp_path / "tag",
        )
        self.svc = Service(settings, KEYS, portfolio_key=PORTFOLIO_KEY, jobs={})
        self.svc.store = JournalStore(settings.state_dir / store.DB_FILENAME)
        self.now = NOW
        self.svc.clock = lambda: self.now
        self.capture = capture_for(self.svc)

    def answer_trades(self, rows, state="ok"):
        self.portfolio.answers["trades_day"] = {"state": state, "read_at": self.now,
                                                "note": None, "data": rows}  # fmt: skip

    def fills(self, mode="live"):
        return self.svc.store.read(OWNER, lambda tx: tx.fills_of(mode))

    def refs(self, mode="live"):
        return self.svc.store.read(OWNER, lambda tx: tx.order_refs(mode))

    async def close(self):
        link = self.svc.parts.get("portfolio")
        if link is not None:
            await link.aclose()

    def stop(self):
        self.portfolio.stop()
        self.svc.store.close()
        store.stop()


@pytest.fixture
def rig(tmp_path, files):
    made = Rig(tmp_path, files)
    yield made
    made.stop()


def scenario(rig, coro):
    async def go():
        try:
            return await coro()
        finally:
            await rig.close()

    return asyncio.run(go())


def test_a_live_read_records_each_trade_once_and_labels_it(rig):
    """P7: the same trade id from two reads is one fill; the label comes from order_logs, the
    latency from latency.db (M12.12), and the read went through the portfolio as the journal."""
    at = ist_ms(DAY, "10:01:00")
    rig.answer_trades([trade_row("t1", "o1", at=at), trade_row("t2", "o1", at=at + 500)])

    async def go():
        first = await rig.capture.read_live()
        second = await rig.capture.read_live("close_reconcile")
        return first, second

    first, second = scenario(rig, go)
    assert (first.new, second.new, second.fixed) == (2, 0, 0)
    fills = rig.fills()
    assert [f["trade_id"] for f in fills] == ["t1", "t2"]
    assert {f["source"] for f in fills} == {"stream_read"}  # the first source stays
    assert {f["symbol"] for f in fills} == {"NIFTY25NOV2625000CE"}
    ref = rig.refs()["o1"]
    assert (ref["strategy"], ref["surface"], ref["rtt_ms"], ref["total_latency_ms"]) == (
        "ORB", "api", 412, 456,
    )  # fmt: skip
    assert (ref["pricetype"], ref["limit_price_u"]) == ("LIMIT", 1_000_500)
    assert {c[0] for c in rig.portfolio.calls} == {"trades_day"}
    assert {c[2] for c in rig.portfolio.calls} == {"sudarshan-journal"}


def test_a_trade_with_no_placement_waits_then_is_the_upstox_app_with_its_history(rig):
    at = ist_ms(DAY, "10:20:00")
    rig.answer_trades([trade_row("t9", "o9", at=at)])
    rig.portfolio.answers["order_history"] = {
        "state": "ok", "read_at": NOW, "note": None, "order_id": "o9",
        "rows": [{"order_type": "SL", "placed_at": at - 5_000, "status": "trigger pending",
                  "price_u": 990_000, "trigger_price_u": 995_000},
                 {"order_type": "SL", "placed_at": at - 1_000, "status": "complete"}],
    }  # fmt: skip
    rig.now = at + 30_000

    async def go():
        await rig.capture.read_live()
        waiting = rig.refs()["o9"]["surface"]
        rig.now = at + 60_000
        labelled = await rig.capture.relabel("live")
        return waiting, labelled

    waiting, labelled = scenario(rig, go)
    ref = rig.refs()["o9"]
    assert waiting == "unknown" and labelled == 1
    assert (ref["strategy"], ref["surface"], ref["pricetype"]) == ("Upstox app", "upstox_app", "SL")
    assert (ref["placed_ms"], ref["limit_price_u"], ref["trigger_price_u"]) == (
        at - 5_000, 990_000, 995_000,
    )  # fmt: skip
    assert ref["status"] == "complete"
    assert ("order_history", {"order_id": "o9"}, "sudarshan-journal") in rig.portfolio.calls


def test_the_close_check_marks_a_fill_upstox_no_longer_lists_as_withdrawn(rig):
    at = ist_ms(DAY, "10:01:00")
    rig.answer_trades([trade_row("t1", "o1", at=at), trade_row("t2", "o1", at=at + 1)])

    async def go():
        await rig.capture.read_live()
        rig.answer_trades([trade_row("t1", "o1", at=at)])
        quiet = await rig.capture.read_live("stream_read")  # a plain read never withdraws
        checked = await rig.capture.read_live("close_reconcile", withdraw=True)
        rig.answer_trades([trade_row("t1", "o1", at=at), trade_row("t2", "o1", at=at + 1)])
        back = await rig.capture.read_live("close_reconcile", withdraw=True)
        return quiet, checked, back

    quiet, checked, back = scenario(rig, go)
    assert quiet.withdrawn == 0 and checked.withdrawn == 1 and back.back == 1
    assert {f["trade_id"]: f["source"] for f in rig.fills()} == {
        "t1": "stream_read", "t2": "close_reconcile",
    }  # fmt: skip


@pytest.mark.parametrize(
    ("answer", "words"),
    [
        ({"enabled": False, "message": "off"}, "switched off"),
        ((403, {"status": "error", "code": "forbidden", "message": "x"}), "different keys"),
        ((500, {"status": "error", "code": "failed", "message": "x"}), "could not read"),
        ({"state": "unavailable", "read_at": None, "note": "Upstox did not answer.", "data": None},
         "Upstox did not answer."),
    ],
)  # fmt: skip
def test_a_portfolio_that_cannot_read_writes_nothing_and_says_why(rig, answer, words):
    rig.portfolio.answers["trades_day"] = answer
    outcome = scenario(rig, rig.capture.read_live)
    assert outcome.state == "not_read" and words in outcome.words
    assert rig.fills() == []


def test_a_portfolio_that_is_not_answering_is_said_in_words(rig):
    rig.portfolio.down = True
    outcome = scenario(rig, rig.capture.read_live)
    assert outcome.state == "not_read" and "not answering" in outcome.words


def test_a_sandbox_read_records_the_sandbox_book_from_its_file(rig):
    outcome = scenario(rig, rig.capture.read_sandbox)
    assert outcome.new == 2 and rig.fills() == []
    fills = rig.fills("sandbox")
    assert [f["trade_id"] for f in fills] == ["sb:77", "sb:78"]
    assert {f["instrument_key"] for f in fills} == {STOCK["token"]}
    refs = rig.refs("sandbox")
    assert refs["S1"]["strategy"] == "Sandbox ORB"  # its placement in analyzer_logs
    assert refs["S2"]["strategy"] == "Own" and refs["S2"]["surface"] == "api"
    assert rig.portfolio.calls == []  # the sandbox never goes through the account reader


def test_the_hooks_hear_what_a_read_wrote_and_only_then(rig):
    heard: list = []

    async def hook(mode, days):
        heard.append((mode, sorted(days)))

    rig.capture.hooks.append(hook)
    rig.answer_trades([trade_row("t1", "o1", at=ist_ms(DAY, "10:01:00"))])

    async def go():
        await rig.capture.read_live()
        first = list(heard)
        await rig.capture.read_live()  # nothing new and nothing to label: no hook
        return first

    first = scenario(rig, go)
    # once for the fills written, once for the order labelled from its placement
    assert first == [("live", [DAY]), ("live", [DAY])]
    assert heard == first


def test_the_link_seals_as_the_journal_and_refuses_a_port_another_account_holds(rig):
    rig.answer_trades([])

    async def go():
        link = PortfolioLink(rig.svc.settings.portfolio_url, PORTFOLIO_KEY)
        try:
            assert (await link.trades_day())["state"] == "ok"
            stranger = PortfolioLink(
                rig.svc.settings.portfolio_url, PORTFOLIO_KEY, uid=lambda: os.getuid() + 1
            )
            with pytest.raises(PortfolioUnavailable) as caught:
                await stranger.trades_day()
            await stranger.aclose()
            return caught.value
        finally:
            await link.aclose()

    refused = scenario(rig, go)
    assert refused.kind == "held" and len(rig.portfolio.calls) == 1


# ---- the debounce and the nudge ----------------------------------------------------------------
def test_a_burst_of_nudges_starts_one_read():
    """Mutation target 21: eight nudges in two seconds are one read, three seconds after the
    first (here 0.3 s). Order and counts, never an upper bound on real time (review M4): the
    read comes no sooner than the first wait after the first nudge, and only one comes."""
    reads: list[float] = []

    async def go():
        clock = asyncio.get_running_loop().time
        debounce = Debounce(first_s=0.3, gap_s=1.0, clock=clock)
        start = clock()

        async def reader():
            while True:
                await debounce.next()
                reads.append(clock() - start)

        task = asyncio.create_task(reader())
        for _ in range(8):
            debounce.note()
            await asyncio.sleep(0.025)
        deadline = clock() + 5.0  # a generous cap for a loaded runner, never a measured bound
        while not reads and clock() < deadline:
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)  # room for a second read, if one were (wrongly) started
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return debounce.noted

    noted = asyncio.run(go())
    assert noted == 8 and len(reads) == 1 and reads[0] >= 0.28


def test_reads_are_at_least_the_gap_apart_while_nudges_keep_coming():
    reads: list[float] = []

    async def go():
        clock = asyncio.get_running_loop().time
        debounce = Debounce(first_s=0.1, gap_s=0.5, clock=clock)
        start = clock()

        async def reader():
            while True:
                await debounce.next()
                reads.append(clock() - start)

        task = asyncio.create_task(reader())
        for _ in range(12):  # a nudge every 0.1 s for 1.2 s
            debounce.note()
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.6)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(go())
    gaps = [b - a for a, b in zip(reads, reads[1:], strict=False)]
    assert len(reads) in (3, 4) and all(g >= 0.49 for g in gaps), reads


def test_a_nudge_through_the_api_reaches_the_debounce_of_its_mode(tmp_path):
    from sudarshan.journal.api import JournalApi

    class Desk:
        enabled, off_reason = True, None

        def __init__(self):
            self.parts = {"nudges": Nudges(first_s=60, gap_s=60)}

        def part(self, name, make):
            return self.parts[name]

        def health(self):
            return {}

    desk = Desk()

    async def go():
        server = JournalApi(desk, KEYS)
        await server.start(0)
        base = f"http://127.0.0.1:{server.port}"
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                ok = await client.post(
                    f"{base}/v1/nudge",
                    content=sealed("nudge", "nudge", kind="holdings", sandbox=True, at=NOW),
                )
                bad = await client.post(
                    f"{base}/v1/nudge",
                    content=sealed("nudge", "nudge", kind="positions", sandbox=False, at=NOW),
                )
            return ok, bad
        finally:
            await server.close()

    ok, bad = asyncio.run(go())
    assert ok.status_code == 200 and ok.json()["data"] == {
        "id": None,
        "version": None,
        "words": None,
    }
    assert bad.status_code == 400
    nudges = desk.parts["nudges"]
    assert (nudges.sandbox.noted, nudges.live.noted) == (1, 0)


def test_the_nudge_loop_reads_the_mode_that_was_nudged(rig, monkeypatch):
    calls: list[str] = []

    async def fake_live(self, source="stream_read", withdraw=False):
        calls.append(f"live:{source}")

    async def fake_sandbox(self, source="sandbox_book"):
        calls.append(f"sandbox:{source}")

    monkeypatch.setattr(Capture, "read_live", fake_live)
    monkeypatch.setattr(Capture, "read_sandbox", fake_sandbox)
    nudges = Nudges(first_s=0.05, gap_s=0.2)
    rig.svc.parts["nudges"] = nudges

    async def go():
        task = asyncio.create_task(nudges.run(rig.svc))
        nudges.note(sandbox=False)
        nudges.note(sandbox=False)
        nudges.note(sandbox=True)
        await asyncio.sleep(0.3)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    scenario(rig, go)
    assert sorted(calls) == ["live:stream_read", "sandbox:sandbox_book"]


def test_waiting_orders_are_labelled_again_by_the_loop(rig, monkeypatch):
    done: list[str] = []

    async def fake_pending(self, mode):
        return 1 if mode == "live" else 0

    async def fake_relabel(self, mode):
        done.append(mode)
        return 1

    monkeypatch.setattr(Capture, "pending", fake_pending)
    monkeypatch.setattr(Capture, "relabel", fake_relabel)

    async def go():
        task = asyncio.create_task(nudge_module.relabel_forever(rig.svc, every=0.05))
        await asyncio.sleep(0.17)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    scenario(rig, go)
    assert done and set(done) == {"live"}


# ---- descriptors --------------------------------------------------------------------------------
def test_reading_again_and_again_leaks_no_descriptors(rig):
    rig.answer_trades([trade_row("t1", "o1", at=ist_ms(DAY, "10:01:00"))])

    async def round_():
        await rig.capture.read_live()
        await rig.capture.read_sandbox()
        await rig.capture.relabel("live")

    scenario(rig, round_)  # warm: the store thread, the client, the imports
    watch = FdWatch()
    for _ in range(8):
        scenario(rig, round_)
    RiskReader(rig.svc.settings.risk_db).state(DAY)
    watch.assert_clean()


def test_capture_module_names_the_sixty_seconds_of_the_contract():
    assert capture_module.OUTSIDE_WAIT_MS == 60_000
