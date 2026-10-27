"""words.py against hand-written lines (design 4.3, 5.3 and 5.5, and the market alerts' wording).

Every expected line is typed in vectors/words.json. A string that starts with "@" in an input is a
time in IST; the test turns it into epoch milliseconds and nothing else.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

import pytest
from screener_helpers import ms

from sudarshan.screener import alerting as al
from sudarshan.screener import membership as mem
from sudarshan.screener import types as T
from sudarshan.screener import words as w

VECTORS = json.loads((Path(__file__).parent / "vectors" / "words.json").read_text("utf-8"))
CODES = {
    T.WARMING,
    T.NO_CANDLE,
    T.NO_DATA,
    T.ZERO,
    T.NOT_A_NUMBER,
    T.PAUSED,
    T.NO_AUCTION,
    T.NO_OPTIONS,
    T.SHORT_STORE,
    T.WINDOW_OPEN,
    T.OTHER_DAY,
    T.NO_FEED,
}
FLOORS = {
    "reasons": 49,
    "not_checked": 11,
    "titles": 4,
    "auctions": 2,
    "run_marks": 14,
    "row_marks": 4,
    "alerts": 4,
    "told": 19,
    "engines": 33,
    "chips": 34,
    "exits": 15,
    "times": 13,
}


@pytest.fixture
def server_zone(monkeypatch):
    """Set the process's own time zone, as a server kept on UTC (or anywhere) has it."""

    def set_zone(name: str) -> None:
        monkeypatch.setenv("TZ", name)
        time.tzset()

    yield set_zone
    monkeypatch.undo()
    time.tzset()


@pytest.fixture(autouse=True)
def not_in_india(server_zone):
    """Every line below is checked on a machine that is not on India's clock, so a time taken from
    the machine's own zone instead of IST shows at once (the author's machine is on IST)."""
    server_zone("America/New_York")


def convert(value):
    """'@2026-10-28 10:45[:05[.9]]' -> epoch ms in IST, through lists and objects."""
    if isinstance(value, str) and value.startswith("@"):
        day, clock = value[1:].split(" ")
        return ms(day, clock)
    if isinstance(value, list):
        return [convert(item) for item in value]
    if isinstance(value, dict):
        return {key: convert(item) for key, item in value.items()}
    return value


def why_of(raw: list) -> T.Why:
    return T.Why(raw[0], tuple(convert(raw[1:])))


def ids(section: str) -> list[str]:
    return [str(item.get("id", i)) for i, item in enumerate(VECTORS[section])]


def test_the_vectors_are_what_the_tests_expect():
    for section, floor in FLOORS.items():
        assert len(VECTORS[section]) >= floor, f"{section}: the vectors file lost cases"
    assert {case["why"][0] for case in VECTORS["reasons"]} >= CODES, "every reason code has words"
    assert {case["state"] for case in VECTORS["engines"]} == set(w.STATES), "every state is shown"
    kinds = {case["chip"]["kind"] for case in VECTORS["chips"] if case["chip"]}
    assert kinds == {w.CHIP_NEW, w.CHIP_SINCE, w.CHIP_MISSING, w.CHIP_PINNED, w.CHIP_REMOVED}
    assert {case["why"] for case in VECTORS["told"]} == {
        al.TOLD,
        al.OFF,
        al.PAUSED,
        al.FEW,
        al.NOTHING,
    }
    assert len(set(w.STATES)) == 9


@pytest.mark.parametrize("case", VECTORS["reasons"], ids=ids("reasons"))
def test_a_reason(case):
    why = why_of(case["why"])
    assert w.why_words(why) == case["sentence"]
    assert w.not_checked_words([why]) == case["line"]


@pytest.mark.parametrize("case", VECTORS["not_checked"], ids=ids("not_checked"))
def test_the_not_checked_line(case):
    extra = {"limit": case["limit"]} if "limit" in case else {}
    assert w.not_checked_words([why_of(raw) for raw in case["whys"]], **extra) == case["line"]


@pytest.mark.parametrize("case", VECTORS["titles"], ids=ids("titles"))
def test_the_not_checked_row_title(case):
    assert w.not_checked_title(case["count"]) == case["line"]


@pytest.mark.parametrize("case", VECTORS["auctions"], ids=ids("auctions"))
def test_the_closing_auction_wait(case):
    assert w.auction_words(convert(case["at"])) == case["line"]


@pytest.mark.parametrize("case", VECTORS["run_marks"], ids=ids("run_marks"))
def test_a_runs_marks(case):
    if "args" in case:
        marks = w.run_marks(**convert(case["args"]))
        assert marks == convert(case["marks"])
        assert w.mark_words(marks) == tuple(case["words"])
    else:
        assert w.mark_words(convert(case["marks_in"])) == tuple(case["words"])


@pytest.mark.parametrize("case", VECTORS["row_marks"], ids=ids("row_marks"))
def test_a_rows_marks(case):
    got = w.row_marks(forming=case["forming"], repaired=case["repaired"])
    assert got == tuple(case["words"])


@pytest.mark.parametrize("case", VECTORS["alerts"], ids=ids("alerts"))
def test_the_alert_line_of_a_screen(case):
    assert w.alerts_words(al.Policy(on=case["on"]), paused=case["paused"]) == case["line"]


def decision_of(case: dict) -> al.Decision:
    def named(symbols):
        return tuple(al.Named(symbol, symbol, "NSE") for symbol in symbols)

    return al.Decision(
        case["why"] == al.TOLD,
        case["why"],
        entered=named(case.get("entered", [])),
        more_entered=case.get("more_entered", 0),
        exited=named(case.get("exited", [])),
        more_exited=case.get("more_exited", 0),
        held=case.get("held", 0),
        matches=case["matches"],
    )


@pytest.mark.parametrize("case", VECTORS["told"], ids=ids("told"))
def test_why_a_run_did_or_did_not_raise_an_alert(case):
    policy = al.Policy(min_matches=case["min"])
    assert w.told_words(decision_of(case), policy) == case["line"]


@pytest.mark.parametrize("case", VECTORS["engines"], ids=ids("engines"))
def test_the_engine_line(case):
    engine = w.Engine(**convert(case["facts"]))
    assert w.engine_state(engine) == case["state"]
    assert w.engine_words(engine) == case["line"]


def row_of(raw: dict) -> mem.Row:
    return mem.Row("K", "SBIN", "NSE", **convert(raw))


@pytest.mark.parametrize("case", VECTORS["chips"], ids=ids("chips"))
def test_the_chip_of_a_list_row(case):
    grace = mem.Grace(case["grace"]["mode"], case["grace"].get("n", 2))
    got = w.chip(
        row_of(case["row"]),
        now=convert(case["now"]),
        last_run=case["last_run"],
        grace=grace,
        screen=case["screen"],
    )
    assert (None if got is None else asdict(got)) == case["chip"]


@pytest.mark.parametrize("case", VECTORS["exits"], ids=ids("exits"))
def test_the_exited_today_line(case):
    extra = {"limit": case["limit"]} if "limit" in case else {}
    exits = [(symbol, convert(at)) for symbol, at in case["exits"]]
    assert w.exited_words(exits, **extra) == case["line"]


@pytest.mark.parametrize("case", VECTORS["times"], ids=ids("times"))
def test_times_in_ist(case):
    at, now = convert(case["at"]), convert(case["now"])
    assert w.hm(at) == case["hm"]
    assert w.hms(at) == case["hms"]
    assert w.when(at, now) == case["when"]
    assert w.day_words(at, now) == case["day"]


@pytest.mark.parametrize("zone", ["UTC", "America/New_York", "Asia/Kolkata", "Pacific/Kiritimati"])
def test_times_are_india_s_whatever_the_server_s_zone(server_zone, zone):
    server_zone(zone)
    at = ms("2026-10-28", "10:41:05")
    assert (w.hm(at), w.hms(at)) == ("10:41", "10:41:05")
    assert w.when(at, at + 3_600_000) == "10:41"
    assert w.day_words(at, at + 86_400_000) == "Wed"
    late = ms("2026-10-28", "00:30")
    assert w.when(late, ms("2026-10-28", "23:30")) == "00:30", "the same day in India"
    assert w.day_words(late, ms("2026-10-29", "00:10")) == "Wed", "the day before in India"
