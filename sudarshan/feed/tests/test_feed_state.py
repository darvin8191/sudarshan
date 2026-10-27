"""Settings, subscriptions and caps, snapshot persistence, recorder + replayer, market window."""

import json
import logging
import os
import re
import time
from pathlib import Path

import pytest
import zmq
from feed_fakes import free_port, ist

from sudarshan.feed.config import PKG_DIR, Settings
from sudarshan.feed.replay import read_ticks, replay
from sudarshan.feed.store import SnapshotStore, TickRecorder
from sudarshan.feed.subscriptions import (
    CapError,
    Subscriptions,
    check_caps,
    parse_keys_file,
    validate,
)
from sudarshan.feed.window import MarketWindow, iso


def test_settings_defaults_and_guards():
    cfg = Settings.from_env({})
    assert (cfg.http_port, cfg.zmq_port, cfg.window, cfg.always_on) == (
        5580,
        5581,
        (538, 950),  # 08:58-15:50: the F&O close is 15:40
        False,
    )
    assert cfg.token_file == Path("/etc/sudarshan/upstox_analytics_token")
    assert (
        cfg.state_dir == Path("/var/lib/sudarshan-feed")
        and cfg.keys_file.name == "default_keys.txt"
    )
    assert Settings.from_env({"STATE_DIRECTORY": "/var/lib/a:/var/lib/b"}).state_dir == Path(
        "/var/lib/a"
    )
    with pytest.raises(ValueError, match="https"):  # the bearer token must not travel in clear
        Settings.from_env({"SUDARSHAN_UPSTOX_API_BASE": "http://api.upstox.com"})


@pytest.mark.parametrize("value", ["15:40-08:58", "08:58-15:500", "8:58-15:5", "08:58-15:50\u00a0"])
def test_a_window_setting_that_is_not_a_window_keeps_the_usual_hours(caplog, value):
    """The looser reading before took some of these: they cost a warning, never the start."""
    caplog.set_level(logging.WARNING, logger="sudarshan.feed")
    assert Settings.from_env({"SUDARSHAN_FEED_WINDOW": value}).window == (538, 950)
    assert [(record.levelname, record.getMessage()) for record in caplog.records] == [
        (
            "WARNING",
            f"SUDARSHAN_FEED_WINDOW is set to {value!r}, which is not a time window (HH:MM-HH:MM, "
            "the start before the end), so the usual 08:58-15:50 is used",
        )
    ]


def test_keys_file_format(tmp_path):
    keys = tmp_path / "keys.txt"
    keys.write_text("# comment\n\nltpc NSE_INDEX|Nifty 50   # trailing\nfull\tNSE_EQ|INE002A01018\n"
                    "ltpc NSE_EQ|INE002A01018\n")  # fmt: skip
    assert parse_keys_file(keys) == {"NSE_INDEX|Nifty 50": "ltpc", "NSE_EQ|INE002A01018": "ltpc"}
    keys.write_text("ltpc NSE_INDEX|Nifty 50\ndepth NSE_EQ|INE002A01018\n")
    with pytest.raises(ValueError, match=r"keys\.txt:2:"):
        parse_keys_file(keys)


def test_default_keys_are_valid_and_within_caps():
    keys = parse_keys_file(PKG_DIR / "default_keys.txt")
    check_caps(keys)
    ltpc = {k for k, mode in keys.items() if mode == "ltpc"}
    assert ltpc == {"NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty Bank", "NSE_INDEX|India VIX"}
    assert all(re.fullmatch(r"NSE_EQ\|INE[0-9A-Z]{9}", k) for k in set(keys) - ltpc)


def test_caps_individual_and_combined():
    check_caps(dict.fromkeys((f"NSE_FO|{n}" for n in range(5000)), "ltpc"))
    with pytest.raises(CapError, match="5000"):
        check_caps(dict.fromkeys((f"NSE_FO|{n}" for n in range(5001)), "ltpc"))
    with pytest.raises(CapError, match="cap of 50 "):
        check_caps(dict.fromkeys((f"NSE_FO|{n}" for n in range(51)), "full_d30"))
    full = dict.fromkeys((f"NSE_FO|{n}" for n in range(1500)), "full")
    check_caps(full)
    with pytest.raises(CapError, match="combined cap of 1500"):
        check_caps(full | {"NSE_INDEX|Nifty 50": "ltpc"})


def test_validate_requests():
    assert validate("ltpc", [" NSE_EQ|INE002A01018 ", "NSE_EQ|INE002A01018"]) == [
        "NSE_EQ|INE002A01018"
    ]
    assert validate(None, ["NSE_FO|45450"], need_mode=False) == ["NSE_FO|45450"]
    bad = [("depth", ["NSE_EQ|X1"]), ("ltpc", []), ("ltpc", "NSE_EQ|X1"), ("ltpc", [7])]
    bad += [("ltpc", ["no-separator"]), ("ltpc", ["NSE_EQ|a|b"])]
    for mode, keys in bad:
        with pytest.raises(ValueError):
            validate(mode, keys)


def test_runtime_overlay_survives_a_restart(tmp_path):
    keys, state = tmp_path / "keys.txt", tmp_path / "state" / "subscriptions.json"
    keys.write_text("ltpc NSE_INDEX|Nifty 50\nltpc NSE_INDEX|Nifty Bank\n")
    subs = Subscriptions.load(keys, state)
    new, moved = subs.subscribe("full", ["NSE_EQ|INE002A01018", "NSE_INDEX|Nifty 50"])
    assert (new, moved) == (["NSE_EQ|INE002A01018"], ["NSE_INDEX|Nifty 50"])
    assert subs.unsubscribe(["NSE_INDEX|Nifty Bank", "NSE_EQ|INE009A01021"]) == [
        "NSE_INDEX|Nifty Bank"
    ]
    with pytest.raises(CapError):  # a rejected request changes nothing
        subs.subscribe("full_d30", [f"NSE_FO|{n}" for n in range(51)])
    again = Subscriptions.load(keys, state)
    assert again.desired() == {"NSE_INDEX|Nifty 50": "full", "NSE_EQ|INE002A01018": "full"}
    assert again.counts() == {"ltpc": 0, "full": 2, "option_greeks": 0, "full_d30": 0, "total": 2}
    again.subscribe("ltpc", ["NSE_INDEX|Nifty 50"])  # back to the keys file's own mode
    assert "NSE_INDEX|Nifty 50" not in again.added
    state.write_text("{broken")
    assert Subscriptions.load(keys, state).desired() == {
        "NSE_INDEX|Nifty 50": "ltpc",
        "NSE_INDEX|Nifty Bank": "ltpc",
    }


def test_snapshot_persists_atomically(tmp_path):
    store = SnapshotStore(tmp_path / "state" / "snapshot.json")
    store.update("NSE_FO|45450", {"ltp": 120.25, "greeks": {"delta": 0.52}}, 111)
    store.save()
    assert not store.dirty and not list(store.path.parent.glob("*.tmp"))
    again = SnapshotStore(store.path)
    assert again.load() == 1 and again.ticks == store.ticks
    store.path.write_text("{half written")
    assert SnapshotStore(store.path).load() == 0  # unreadable -> start empty, never crash
    store.update("NSE_FO|1", {"ltp": 1.0}, 5000)
    assert store.prune(keep={"NSE_FO|45450"}, cutoff_ms=1000) == 0  # subscribed or recent: kept
    assert store.prune(keep=set(), cutoff_ms=1000) == 1 and set(store.ticks) == {"NSE_FO|1"}


def test_recorder_and_replayer_round_trip(tmp_path):
    recorder = TickRecorder(tmp_path / "ticks", 1 << 20, 10, clock=lambda: ist(2026, 10, 21, 10))
    lines = [
        json.dumps({"key": f"NSE_FO|{n}", "ltp": n, "ts_recv": 1000 + 10 * n}).encode()
        for n in range(5)
    ]
    for line in lines[:3]:
        recorder.add(line)
    recorder.flush()
    for line in lines[3:]:
        recorder.add(line)
    recorder.flush()  # a second gzip member in the same day file
    path = tmp_path / "ticks" / "2026-10-21.jsonl.gz"
    assert [payload for _, _, payload in read_ticks(path)] == lines
    assert recorder.status()["lines_written"] == 5

    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    port, slept = free_port(), []
    sub.connect(f"tcp://127.0.0.1:{port}")
    sub.subscribe(b"NSE_FO|")
    try:
        assert (
            replay(
                path,
                port=port,
                speed=2.0,
                wait=0.3,
                sleep=lambda s: (slept.append(s), time.sleep(s)),
            )
            == 5
        )
        assert slept == pytest.approx([0.3, 0.005, 0.005, 0.005, 0.005])  # 10 ms gaps at 2x speed
        got = []
        while sub.poll(2000):
            got.append(sub.recv_multipart())
            if len(got) == 5:
                break
        assert got == [[f"NSE_FO|{n}".encode(), lines[n]] for n in range(5)]
    finally:
        sub.close(0)
        ctx.term()

    cut = tmp_path / "cut.jsonl.gz"  # a crash in the middle of the second member
    cut.write_bytes(path.read_bytes()[:-20])
    assert [payload for _, _, payload in read_ticks(cut)][:3] == lines[:3]


def test_recorder_is_size_bounded_and_prunes_old_days(tmp_path):
    ticks = tmp_path / "ticks"
    ticks.mkdir()
    for name in ("2026-10-01.jsonl.gz", "2026-10-15.jsonl.gz", "notes.txt"):
        (ticks / name).write_bytes(b"")
    recorder = TickRecorder(ticks, max_bytes=10, keep_days=10, clock=lambda: ist(2026, 10, 21, 10))
    recorder.add(b'{"key":"NSE_FO|1","ltp":1}')
    recorder.flush()
    today = ticks / "2026-10-21.jsonl.gz"
    size = today.stat().st_size
    assert sorted(p.name for p in ticks.iterdir()) == [
        "2026-10-15.jsonl.gz",
        "2026-10-21.jsonl.gz",
        "notes.txt",
    ]
    recorder.add(b'{"key":"NSE_FO|1","ltp":2}')
    recorder.flush()  # today's file is over the cap: dropped, counted, file untouched
    assert recorder.status()["capped"] and recorder.dropped == 1 and today.stat().st_size == size


def test_market_window(tmp_path):
    holidays = tmp_path / "nse_holidays.txt"
    holidays.write_text("# NSE 2026\n2026-10-22\n2026-11-09   # trailing comment\nnot-a-date\n")
    window = MarketWindow(holidays)  # 21 Oct 2026 is a Wednesday
    assert window.status(ist(2026, 10, 21, 8, 57, 59)) == (False, "before the market window")
    assert window.status(ist(2026, 10, 21, 8, 58)) == (True, "market window")
    assert window.status(ist(2026, 10, 21, 15, 49, 59))[0]
    assert window.status(ist(2026, 10, 21, 15, 50)) == (False, "after the market window")
    assert window.status(ist(2026, 10, 22, 10)) == (False, "NSE holiday")
    assert window.status(ist(2026, 10, 24, 10)) == (False, "weekend")
    assert window.note == "2 dates, 1 unreadable line"
    assert iso(window.next_open(ist(2026, 10, 21, 16))) == "2026-10-23T08:58:00+05:30"  # skips Thu
    assert (
        iso(window.next_open(ist(2026, 10, 23, 16))) == "2026-10-26T08:58:00+05:30"
    )  # skips weekend
    assert window.in_core_session(ist(2026, 10, 21, 9, 15)) and not window.in_core_session(
        ist(2026, 10, 21, 9)
    )
    holidays.write_text("2026-10-21\n")
    os.utime(holidays, ns=(time.time_ns(), time.time_ns() + 10**9))  # make the change visible
    assert window.status(ist(2026, 10, 21, 10)) == (False, "NSE holiday")  # re-read, no restart
    assert MarketWindow(holidays, always_on=True).status(ist(2026, 10, 24, 3))[0]
    missing = MarketWindow(tmp_path / "absent.txt")
    assert (
        missing.status(ist(2026, 10, 22, 10)) == (True, "market window")
        and missing.note == "missing"
    )
