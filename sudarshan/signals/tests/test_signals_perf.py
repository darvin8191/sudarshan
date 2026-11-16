"""T8: the TRINETRA view's score (perf.py): the sessions, the daily samples of the feed's
snapshot, the marks, and the performance op. Every figure is invented."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest

from sudarshan.signals import contract as c
from sudarshan.signals import diff, perf
from sudarshan.signals.store import SignalsStore

IST = timezone(timedelta(hours=5, minutes=30))
HOLIDAY = date(2026, 11, 24)  # an invented holiday, a Tuesday


def trading(day: date) -> bool:
    return day.weekday() < 5 and day != HOLIDAY


def at(day: date, hh: int, mm: int = 0) -> int:
    return int(datetime.combine(day, time(hh, mm), IST).timestamp() * 1000)


MON = date(2026, 11, 16)


def change(day, hh, bias="bull", module="regime", instrument="NIFTY", kind="state", ident=1):
    return {"id": ident, "at_ms": at(day, hh), "module": module, "instrument": instrument,
            "kind": kind, "bias": bias}  # fmt: skip


def closes(start: date, values: list[float], instrument="NIFTY") -> dict:
    """Closes on consecutive trading days from ``start``."""
    out, day = {}, start
    for value in values:
        while not trading(day):
            day += timedelta(days=1)
        out[(perf.day_key(day), instrument)] = {"close_px": value}
        day += timedelta(days=1)
    return out


def test_the_sessions_of_a_change():
    assert perf.day0(at(MON, 11), trading) == MON
    sat = date(2026, 11, 21)
    assert perf.day0(at(sat, 10), trading) == date(2026, 11, 20)  # the Friday before
    assert perf.after(MON, 1, trading) == date(2026, 11, 17)
    assert perf.after(date(2026, 11, 20), 3, trading) == date(2026, 11, 26)  # over the holiday
    assert perf.gap_day(at(MON, 8, 45), trading) == MON
    assert perf.gap_day(at(MON, 9, 30), trading) == date(2026, 11, 17)  # after the open: the next
    assert perf.gap_day(at(date(2026, 11, 23), 18), trading) == date(2026, 11, 25)  # past a holiday


def test_a_bull_change_scored_after_1_3_and_5_sessions():
    levels = closes(MON, [100, 101, 99, 102, 98, 97])  # Mon 16 to Mon 23 Nov: 5 sessions on, a fall
    got = perf.score([change(MON, 11)], levels, trading)
    assert [(r["sessions"], r["cases"], r["hits"]) for r in got["rows"]] == [(1, 1, 1), (3, 1, 1),
                                                                            (5, 1, 0)]  # fmt: skip
    row = got["rows"][0]
    assert set(row) == {f.name for f in c.record("PerfRow")}
    assert row["judged"] is False and row["hit_pct"] is None and row["median_move_pct"] is None
    assert row["words"] == "too few cases to judge"
    assert got["as_of_day"] == max(day for day, _ in levels) and got["words"] is None


def test_thirty_cases_are_judged_with_a_share_and_a_median():
    changes, levels = [], {}
    day = MON
    for n in range(30):
        while not trading(day):
            day += timedelta(days=1)
        changes.append(change(day, 11, bias="bear", ident=n + 1))
        day += timedelta(days=1)
    # every close one point under the day before: every bear case is right
    levels = closes(MON, [1000 - n for n in range(60)])
    got = perf.score(changes, levels, trading)
    one = next(r for r in got["rows"] if r["sessions"] == 1)
    assert one["cases"] == 30 and one["hits"] == 30 and one["judged"] is True
    assert one["hit_pct"] == "100.00" and one["words"] is None
    assert float(one["median_move_pct"]) < 0


def test_neutral_names_no_direction_and_lapses_do_not_count():
    levels = closes(MON, [100, 101, 102, 103, 104, 105])
    changes = [change(MON, 11, bias="neutral", ident=1), change(MON, 12, kind="lapsed", ident=2),
               change(MON, 12, kind="no_data", ident=3)]  # fmt: skip
    rows = perf.score(changes, levels, trading)["rows"]
    assert {r["bias"] for r in rows} == {"neutral"} and all(r["hits"] == 0 for r in rows)
    assert all(r["hit_pct"] is None for r in rows)


def test_a_gap_bias_is_scored_by_the_open_it_was_about():
    levels = {(perf.day_key(MON), "BANKNIFTY"): {"open_px": 101.0, "prev_close_px": 100.0}}
    changes = [
        change(MON, 8, module="gap_bias", instrument="BANKNIFTY", kind="first"),
        change(MON, 8, bias="bear", module="gap_bias", instrument="BANKNIFTY", ident=2),
    ]
    rows = perf.score(changes, levels, trading)["rows"]
    assert [(r["bias"], r["sessions"], r["cases"], r["hits"]) for r in rows] == [
        ("bull", 0, 1, 1), ("bear", 0, 1, 0)]  # fmt: skip


def test_nothing_to_score_says_so_and_a_module_filter():
    assert perf.score([], {}, trading) == {"module": None, "as_of_day": None, "rows": [],
                                           "words": c.WORDS["no_scores"]}  # fmt: skip
    levels = closes(MON, [100, 101])
    got = perf.score([change(MON, 11), change(MON, 11, module="swing_eod", ident=2)], levels,
                     trading, module="swing_eod")  # fmt: skip
    assert got["module"] == "swing_eod" and {r["module"] for r in got["rows"]} == {"swing_eod"}


def test_the_next_sample():
    assert perf.next_wake(at(MON, 9), trading) == (at(MON, 9, 16) + 30_000, "open")
    assert perf.next_wake(at(MON, 10), trading) == (at(MON, 16, 20), "close")
    fri = date(2026, 11, 20)
    assert perf.next_wake(at(fri, 17), trading) == (at(date(2026, 11, 23), 9, 16) + 30_000, "open")
    assert perf.next_wake(at(date(2026, 11, 23), 17), trading)[0] == (
        at(date(2026, 11, 25), 9, 16) + 30_000)  # fmt: skip


def tick(ltp, ltt, cp=None) -> dict:
    return {"ltp": ltp, "ltt": ltt, "ltq": 0, "cp": cp}


def test_levels_from_the_feeds_ticks():
    ticks = {
        perf.INDEX_KEYS["NIFTY"]: tick(25900.5, at(MON, 9, 16), 25800.0),
        perf.INDEX_KEYS["BANKNIFTY"]: tick(58000.0, at(MON, 9, 10), 57900.0),
    }
    got = perf.levels_from(ticks, "open", at(MON, 9, 17))
    assert got == [{"day": 20261116, "instrument": "NIFTY", "open_px": 25900.5,
                    "prev_close_px": 25800.0, "at_ms": at(MON, 9, 16)}]  # fmt: skip
    closing = {perf.INDEX_KEYS["NIFTY"]: tick(26000.0, at(MON, 15, 30)),
               perf.INDEX_KEYS["BANKNIFTY"]: tick(58100.0, at(MON - timedelta(days=3), 15, 30)),
               "junk": "x"}  # fmt: skip
    assert [r["instrument"] for r in perf.levels_from(closing, "close", at(MON, 16, 20))] == [
        "NIFTY"
    ]
    bad = {perf.INDEX_KEYS["NIFTY"]: {"ltp": True, "ltt": at(MON, 15, 30)}}
    assert perf.levels_from(bad, "close", at(MON, 16, 20)) == []


class FakeFeed:
    """The feed's open /snapshot on 127.0.0.1, answering what the test put in ``ticks``."""

    def __init__(self, ticks: dict):
        self.ticks, self.paths, self.server = ticks, [], None

    async def start(self) -> str:
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        return f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"

    async def _serve(self, reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        self.paths.append(head.split(b" ")[1].decode())
        body = json.dumps({"ticks": self.ticks}).encode()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                     + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)  # fmt: skip
        await writer.drain()
        writer.close()

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()


def test_the_close_sample_stores_levels_and_fills_marks(tmp_path):
    store = SignalsStore(tmp_path / "signals.db")
    snap = c.read_document(json.dumps({
        "v": 1, "run": "r", "seq": 1, "at": at(MON, 11), "next_by": at(MON, 11) + 300_000,
        "signals": [{"module": "regime", "instrument": "NIFTY", "state": "UP", "label": "Up",
                     "bias": "bull", "as_of": at(MON, 11), "horizon": "swing", "quality": "ok"}],
    }).encode(), now_ms=at(MON, 11), written_ms=at(MON, 11))  # fmt: skip
    out = diff.apply({}, snap, at(MON, 11))
    store.accept(snap, name="x", received_ms=at(MON, 11), current=out.current, changes=out.changes)
    feed = FakeFeed({perf.INDEX_KEYS["NIFTY"]: tick(26000.0, at(MON, 15, 30), 25900.0)})

    async def scenario():
        url = await feed.start()
        service = SimpleNamespace(
            clock=lambda: at(MON, 16, 20),
            settings=SimpleNamespace(feed_url=url),
            store=store,
            calendar=SimpleNamespace(is_trading_day=trading),
        )
        assert await perf.sample(service, "close") == 1
        await feed.stop()
        service.settings.feed_url = url  # the feed is gone now: nothing stored, nothing raised
        assert await perf.sample(service, "close") == 0

    try:
        asyncio.run(scenario())
        assert feed.paths == ["/snapshot?keys=NSE_INDEX%7CNifty%2050,NSE_INDEX%7CNifty%20Bank"]
        assert store.levels()[(20261116, "NIFTY")]["close_px"] == 26000.0
        assert store.timeline()["changes"][0]["id"] == 1
        with store.engine.connect() as conn:
            marks = conn.exec_driver_sql("SELECT change_id, session_n, close_px FROM mark").all()
            mark_px = conn.exec_driver_sql("SELECT mark_px FROM change WHERE id = 1").scalar()
        assert marks == [(1, 0, 26000.0)] and mark_px == 26000.0
        assert store.fill_marks(trading) == 0  # once
    finally:
        store.close()


@pytest.mark.parametrize("kind", ["open", "close"])
def test_a_sample_of_another_day_is_not_stored(tmp_path, kind):
    store = SignalsStore(tmp_path / "s.db")
    feed = FakeFeed({perf.INDEX_KEYS["NIFTY"]: tick(26000.0, at(MON - timedelta(days=3), 15, 30))})

    async def scenario():
        url = await feed.start()
        service = SimpleNamespace(
            clock=lambda: at(MON, 16, 20),
            settings=SimpleNamespace(feed_url=url),
            store=store,
            calendar=SimpleNamespace(is_trading_day=trading),
        )
        try:
            assert await perf.sample(service, kind) == 0
        finally:
            await feed.stop()

    try:
        asyncio.run(scenario())
        assert store.levels() == {}
    finally:
        store.close()
