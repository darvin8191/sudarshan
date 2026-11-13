"""Live MAE and MFE from the feed (JB6; cross-design Y12, Y21): the claims at the feed, the ticks
on its ZMQ bus for exactly the claimed keys, the running low and high, the excursion at close,
and the recording's fallback at 17:00. Against fakes on 127.0.0.1; every value is synthetic."""

from __future__ import annotations

import asyncio
import gzip
import json
import time
from pathlib import Path

import pytest
import zmq
from journal_fakes import FakeFeed, free_port, trade_row
from test_journal_capture import DAY, OPT, ist_ms, scenario
from test_journal_capture import files as files  # noqa: F401  (a fixture)
from test_journal_capture import rig as rig  # noqa: F401  (a fixture)

from sudarshan.journal import feedlink
from sudarshan.journal.feedlink import (
    Excursions,
    FeedLink,
    Track,
    excursions_for,
    price_u,
    span_of,
    tick_point,
)
from sudarshan.journal.jobs import excursion as fallback
from sudarshan.journal.store import Scope
from sudarshan.tasks import cancel_and_wait
from sudarshan.tests.fdwatch import FdWatch

OWNER = Scope.owner()
KEY = OPT["token"]
T0 = ist_ms(DAY, "10:01:00")


def tick(key: str, ms: int, ltp: float) -> bytes:
    return json.dumps({"key": key, "ltp": ltp, "ltt": ms, "ts_recv": ms + 5},
                      separators=(",", ":")).encode()  # fmt: skip


# ---- the pure parts ----------------------------------------------------------------------------
def test_a_tick_is_its_trade_time_and_its_price_in_price_u():
    assert price_u(101.05) == 1_010_500 and price_u(0) is None and price_u(True) is None
    assert tick_point(tick(KEY, T0, 97.5)) == (T0, 975_000)
    assert tick_point(b'{"key":"X","ltp":1.0,"ts_recv":5}') == (5, 10_000)
    assert tick_point(b"not json") is None and tick_point(b'{"key":"X"}') is None


def test_the_path_at_close_drops_ticks_after_the_exit_and_before_the_entry():
    track = Track(1, KEY, start_ms=T0)
    track.fold(T0 - 1_000, 900_000)  # before the entry: never counts
    track.fold(T0 + 60_000, 970_000)
    track.fold(T0 + 120_000, 1_150_000)
    assert (track.low, track.high, track.ticks) == ((T0 + 60_000, 970_000),
                                                    (T0 + 120_000, 1_150_000), 2)  # fmt: skip
    recent = [(T0 + 200_000, 1_100_000), (T0 + 400_000, 1_300_000)]
    span = span_of(track, recent, end_ms=T0 + 300_000)
    assert span.points == [
        (T0 + 60_000, 970_000), (T0 + 120_000, 1_150_000), (T0 + 200_000, 1_100_000),
    ]  # fmt: skip
    assert (span.covered_from, span.ticks) == (T0 + 60_000, 3)


def test_a_recorded_lines_key_is_read_from_its_bytes():
    assert fallback.key_of(tick(KEY, T0, 1.0)) == KEY.encode()
    assert fallback.key_of(b'{"ltp":1}') is None


def test_the_scan_reads_only_wanted_keys_and_ends_quietly_on_a_cut_off_file(tmp_path):
    path = tmp_path / "day.jsonl.gz"
    lines = [tick(KEY, T0 + 1_000, 99.0), tick("NSE_FO|1", T0 + 2_000, 1.0),
             tick(KEY, T0 + 3_000, 104.0), b"garbage", tick(KEY, T0 + 999_000, 50.0)]  # fmt: skip
    with gzip.open(path, "wb") as f:
        f.write(b"\n".join(lines) + b"\n")
    want = fallback.Want(1, KEY, T0, T0 + 10_000)
    assert fallback.scan(path, [want]) == 5
    assert (want.low, want.high, want.ticks) == ((T0 + 1_000, 990_000), (T0 + 3_000, 1_040_000), 2)
    cut = tmp_path / "cut.jsonl.gz"
    cut.write_bytes(path.read_bytes()[:-12])
    other = fallback.Want(1, KEY, T0, T0 + 10_000)
    assert fallback.scan(cut, [other]) >= 1  # read up to where it ends, no exception
    assert fallback.scan(tmp_path / "none.jsonl.gz", [other]) is None


# ---- live, end to end --------------------------------------------------------------------------
class Pub:
    """The feed's ZMQ PUB as a test owns it: bound on a free loopback port."""

    def __init__(self):
        self.ctx = zmq.Context()
        self.sock = self.ctx.socket(zmq.PUB)
        self.port = free_port()
        self.sock.bind(f"tcp://127.0.0.1:{self.port}")

    def send(self, key: str, payload: bytes) -> None:
        self.sock.send_multipart([key.encode(), payload])

    def close(self):
        self.sock.close(0)
        self.ctx.term()


@pytest.fixture
def feed(rig):
    fake, pub = FakeFeed(), Pub()
    url = fake.start()
    rig.svc.settings = type(rig.svc.settings)(**{
        **rig.svc.settings.__dict__, "feed_url": url,
        "feed_zmq": f"tcp://127.0.0.1:{pub.port}", "max_claims": 2,
    })  # fmt: skip
    yield fake, pub
    fake.stop()
    pub.close()


async def wait_until(check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.02)


def trip_row(rig):
    return rig.svc.store.read(OWNER, lambda tx: tx.rows("round_trip", order="id"))


def test_a_trip_is_claimed_followed_and_its_excursion_written_at_close(rig, feed):
    """X1 live: entry at 10:01 (100.00), low 97.00, high 115.00, exit at 10:20 (112.50); a tick
    after the exit never counts; the claim goes when the trip closes."""
    fake, pub = feed
    rig.answer_trades([trade_row("t1", "o1", at=T0)])

    async def go():
        ex = excursions_for(rig.svc)
        feedlink.RECENT_MS = 1_000_000_000  # every tick stays recent: the exit cuts the path
        tasks = [asyncio.create_task(ex.run_ticks()), asyncio.create_task(ex.run_claims())]
        try:
            await rig.capture.read_live()
            await wait_until(lambda: fake.claims.get("sudarshan-journal") == {KEY: "ltpc"})
            assert ex.link.topics == {KEY} and rig.svc.counts["claims"] == 1
            seen = asyncio.Event()
            original = ex.on_tick

            def spy(key, payload):
                original(key, payload)
                seen.set()

            ex.on_tick = spy
            deadline = time.monotonic() + 10
            while not seen.is_set():  # the SUB joins a moment after it connects
                assert time.monotonic() < deadline, "no tick arrived"
                pub.send(KEY, tick(KEY, T0 + 30_000, 100.0))
                pub.send("NSE_FO|500011", tick("NSE_FO|500011", T0, 1.0))  # a prefix: ignored
                await asyncio.sleep(0.05)
            for ms, ltp in ((T0 + 180_000, 97.0), (T0 + 840_000, 115.0),
                            (ist_ms(DAY, "10:25:00"), 140.0)):  # fmt: skip
                pub.send(KEY, tick(KEY, ms, ltp))
            await wait_until(lambda: len(ex.recent.get(KEY, ())) >= 4)
            assert "NSE_FO|500011" not in ex.recent
            rig.answer_trades([trade_row("t1", "o1", at=T0),
                               trade_row("t2", "o2", side="SELL", price_u=1_125_000,
                                         at=ist_ms(DAY, "10:20:00"))])  # fmt: skip
            await rig.capture.read_live()
            await wait_until(lambda: "sudarshan-journal" not in fake.claims)
        finally:
            feedlink.RECENT_MS = 90_000
            await cancel_and_wait(*tasks)
            await rig.svc.parts.pop("excursions").aclose()

    scenario(rig, go)
    (trip,) = trip_row(rig)
    assert trip["status"] == "closed"
    assert (trip["mae_paise"], trip["mfe_paise"]) == (-19_500, 97_500)
    assert (trip["mae_ms"], trip["mfe_ms"]) == (T0 + 180_000, T0 + 840_000)
    assert trip["excursion_source"] == "recording" and trip["excursion_note"] == "from 10:01:30"
    span = rig.svc.store.read(OWNER, lambda tx: tx.trip_track_get(trip["id"]))
    assert (span["low_u"], span["high_u"], span["from_ms"]) == (970_000, 1_150_000, T0 + 30_000)


def test_claims_keep_the_most_recent_keys_and_a_refusal_is_tried_again(rig, feed):
    fake, _ = feed
    rows = [trade_row(f"t{n}", f"o{n}", key=f"NSE_FO|5000{n}", at=T0 + n * 60_000)
            for n in range(1, 4)]  # fmt: skip
    rig.answer_trades(rows)

    async def go():
        ex = Excursions(rig.svc)
        try:
            await rig.capture.read_live()
            await ex.sync()
            assert ex.wanted == ("NSE_FO|50003", "NSE_FO|50002")  # max_claims 2, newest first
            fake.claim_status = 409
            assert await ex.claim() is False and ex.claimed is None
            fake.claim_status = 200
            assert await ex.claim() is True
            assert fake.claims["sudarshan-journal"] == {"NSE_FO|50003": "ltpc",
                                                        "NSE_FO|50002": "ltpc"}  # fmt: skip
            ex.due.clear()  # the claims loop clears it when it puts the claim
            ex.on_health(b'{"started_at":"2026-11-09T09:00:00"}')
            assert not ex.due.is_set()
            ex.on_health(b'{"started_at":"2026-11-09T11:00:00"}')  # the feed restarted
            assert ex.due.is_set()
        finally:
            await ex.aclose()

    scenario(rig, go)


def test_the_recording_finishes_a_trip_the_live_tracker_did_not_see(rig, tmp_path):
    rig.answer_trades([trade_row("t1", "o1", at=T0),
                       trade_row("t2", "o2", side="SELL", price_u=1_125_000,
                                 at=ist_ms(DAY, "10:20:00"))])  # fmt: skip
    scenario(rig, rig.capture.read_live)
    ticks = tmp_path / "ticks"
    ticks.mkdir()
    with gzip.open(ticks / "2026-11-09.jsonl.gz", "wb") as f:
        for ms, ltp in ((T0, 100.0), (T0 + 180_000, 97.0), (T0 + 840_000, 115.0),
                        (ist_ms(DAY, "10:25:00"), 140.0)):  # fmt: skip
            f.write(tick(KEY, ms, ltp) + b"\n")
    rig.svc.settings = type(rig.svc.settings)(**{**rig.svc.settings.__dict__,
                                                 "ticks_dir": ticks})  # fmt: skip
    line = scenario(rig, lambda: fallback.finish_from_recording(rig.svc, DAY, rig.now, False))
    (trip,) = trip_row(rig)
    assert (trip["mae_paise"], trip["mfe_paise"], trip["excursion_note"]) == (-19_500, 97_500, None)
    assert line == "1 trades asked, 1 found in the recording (4 lines read)"
    again = scenario(rig, lambda: fallback.finish_from_recording(rig.svc, DAY, rig.now, False))
    assert again == "no trade needs the recording"  # the tracked span now starts at the entry


def test_without_a_recording_the_trip_says_its_path_was_not_recorded(rig, tmp_path):
    rig.answer_trades([trade_row("t1", "o1", at=T0),
                       trade_row("t2", "o2", side="SELL", at=ist_ms(DAY, "10:20:00"))])  # fmt: skip
    scenario(rig, rig.capture.read_live)
    rig.svc.settings = type(rig.svc.settings)(**{**rig.svc.settings.__dict__,
                                                 "ticks_dir": tmp_path / "none"})  # fmt: skip
    line = scenario(rig, lambda: fallback.finish_from_recording(rig.svc, DAY, rig.now, False))
    (trip,) = trip_row(rig)
    assert trip["excursion_source"] == "none"
    assert trip["excursion_note"] == "the price path was not recorded"
    assert line.endswith("(no recording of today)")


def test_a_restart_gap_is_asked_of_the_recording_and_a_claims_few_seconds_are_not():
    trip = {"excursion_source": "recording", "opened_ms": T0}
    assert fallback.wanted(trip, {"from_ms": T0 + 5_000}) is False
    assert fallback.wanted(trip, {"from_ms": T0 + 61_000}) is True
    assert fallback.wanted({"excursion_source": "none", "opened_ms": T0}, None) is True


def test_the_link_and_the_scanner_leak_no_descriptor(rig, tmp_path):
    """fd-audit (JB6): one ZMQ context and socket and one HTTP client per link, closed by
    ``aclose``; the scan's one thread ends with the scanner; the recording is opened per pass in
    a ``with``."""
    path = tmp_path / "day.jsonl.gz"
    with gzip.open(path, "wb") as f:
        f.write(tick(KEY, T0, 1.0) + b"\n")

    async def round_():
        link = FeedLink(f"tcp://127.0.0.1:{free_port()}", "http://127.0.0.1:9")
        link.follow([KEY])
        await link.aclose()
        scanner = fallback.Scanner()
        await scanner.run(path, [fallback.Want(1, KEY, T0, T0 + 1)])
        await scanner.aclose()
        scanner.pool.shutdown(wait=True)

    asyncio.run(round_())
    watch = FdWatch()
    for _ in range(8):
        asyncio.run(round_())
    watch.assert_clean()


def test_recording_file_names_are_the_feeds(tmp_path):
    assert Path(fallback.__file__).name == "excursion.py"
    assert fallback.STOP_EVERY == 10_000 and fallback.LOWEST == 19
