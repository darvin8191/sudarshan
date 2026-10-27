"""The replay check (M10.1): a recorded day through the engine, offline and read-only."""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import date

import pytest
from test_market_vectors import VECTORS, World, expand, ms, seen, trades, window_of

from sudarshan.alerts.market import check, sessions, spec
from sudarshan.alerts.market.store import MarketStore

SBIN = "NSE_EQ|INE062A01020"
DAY = date(2026, 10, 21)


def recording(path, ticks) -> None:
    with gzip.open(path, "wt") as out:
        for tick in ticks:
            out.write(json.dumps(tick) + "\n")


def test_the_check_prints_what_would_have_fired_and_writes_nothing(tmp_path, capsys):
    db = tmp_path / "market.db"
    store = MarketStore(db)
    created = ms("2026-10-21", "08:00:00")
    fields = {"instrument_key": SBIN, "symbol": "SBIN", "exchange": "NSE", "tick_size": "0.05",
              "kind": "price", "params": {"op": "crosses_above", "level": "100"},
              "frequency": "every_time"}  # fmt: skip
    store.create("owner", spec.check(fields, created), created)
    paused = store.create("owner", spec.check(fields | {"symbol": "SBIN"}, created), created)
    store.pause("owner", paused["id"], 1, created)
    store.close()
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    day = tmp_path / "2026-10-21.jsonl.gz"
    ticks = []
    for n, (clock, price) in enumerate((("09:20:00", 99.9), ("09:21:00", 100.1), ("09:22:00", 99.8),
                                         ("09:23:00", 100.3), ("15:35:00", 101.0))):  # fmt: skip
        at = ms("2026-10-21", clock)
        ticks.append({"key": SBIN, "ltp": price, "ltt": at, "ltq": n + 1, "ts_recv": at + 15})
    ticks.append({"key": "NSE_EQ|OTHER", "ltp": 5.0, "ltt": 1, "ts_recv": 1})  # not followed
    recording(day, ticks)
    holidays = tmp_path / "holidays.txt"
    holidays.write_text("2026-10-20\n")
    args = [str(day), "--db", str(db), "--holidays", str(holidays), "--extra-days",
            str(tmp_path / "none.txt")]  # fmt: skip
    assert check.main(args) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        "09:21:00.015  #1  Alert: SBIN crossed above ₹100.00: now ₹100.10 (NSE)",
        "09:23:00.015  #1  Alert: SBIN crossed above ₹100.00: now ₹100.30 (NSE)",
        "2026-10-21: 1 alerts, 5 ticks on their instruments, 4 judged, 2 would have fired.",
    ]  # the 15:35 trade is after the session; the Paused alert is left out
    assert check.main([*args, "--all"]) == 0
    assert capsys.readouterr().out.splitlines()[-1].endswith("4 would have fired.")
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before  # read-only


def test_the_check_refuses_what_it_cannot_read(tmp_path, capsys):
    day = tmp_path / "2026-10-21.jsonl.gz"
    recording(day, [])
    assert check.main([str(day), "--db", str(tmp_path / "missing.db")]) == 2
    assert "no market alert record yet" in capsys.readouterr().err
    assert check.main([str(tmp_path / "ticks.jsonl.gz"), "--db", str(tmp_path / "x.db")]) == 2
    assert "must start with its day" in capsys.readouterr().err
    assert not (tmp_path / "x.db").exists()


#: Golden vectors a one-day recording can carry: trades and quotes only, one day, no owner step,
#: nothing the feed's health or a pause decides, every fire told on a tick (the check's timer runs
#: every second, a vector's clock steps at their own times), and each step judged as it arrives
#: (a recording's clock is its arrivals, so a step that arrives earlier than it is judged cannot
#: be one).
PLAIN = ("ltp", "bid", "ask", "at", "ltt", "ltq", "cp", "atp", "vtt", "open", "high", "low",
         "check")  # fmt: skip


def plain(vector: dict) -> bool:
    day = vector.get("day")
    if vector.get("alert", {}).get("frequency") == "at_close" or vector.get("down"):
        return False
    for step in expand(vector["steps"], vector.get("day", "2026-10-21")):
        if set(step) - set(PLAIN) or step.get("day", day) != day:
            return False
    return all(set(fire) <= {"at", "value", "level", "direction", "trade_time", "candle",
                             "auction", "day"} for fire in vector.get("fires", []))  # fmt: skip


VECTORS_AS_RECORDINGS = [v for v in VECTORS["alerts"] if plain(v)]


def test_enough_vectors_run_as_recordings():
    assert len(VECTORS_AS_RECORDINGS) >= 40


@pytest.mark.parametrize(
    "vector", VECTORS_AS_RECORDINGS, ids=[v["id"] for v in VECTORS_AS_RECORDINGS]
)
def test_a_golden_vector_as_a_recording(vector, tmp_path):
    """The check and the vectors' own runner agree on what fires."""
    world = World(vector, tmp_path / "market.db")
    world.store.close()
    on = vector.get("day", "2026-10-21")
    day = date.fromisoformat(on)
    ticks = []
    for raw in expand(vector["steps"], on):
        frame = world.frame(raw, on)
        ticks.append((world.alert.key, frame["ts_recv"], frame))
    found = sessions.sessions_of(day, trading_day=trades(day), window=window_of(day))
    before = sessions.previous_trading_day(day, trades)
    cas = frozenset({world.alert.key}) if vector.get("auction") else frozenset()
    told, _ = check.judge_day(ticks, [world.alert], day, found=found,
                              previous=sessions.day_key(before) if before else 0,
                              cas=cas)  # fmt: skip
    fires = [item.fire for item in told if item.fire is not None]
    if "fire_count" in vector:
        assert len(fires) == vector["fire_count"]
        return
    assert len(fires) == len(vector["fires"]), [item.text for item in told]
    for fire, expected in zip(fires, vector["fires"], strict=True):
        shown = seen(world.alert, fire)
        assert {name: shown[name] for name in expected} == expected
