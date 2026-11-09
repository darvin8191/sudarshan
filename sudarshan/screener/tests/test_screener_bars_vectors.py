"""bars.py and candles.py against hand-written vectors (design B1 to B14, G1 to G9 and the cases
around them, with the closing auction of X6 and the pauses of S14).

A case is told frame by frame, the way the service lives it: ticks arrive, the clock seals what is
due, the runs ask when they may judge and read the candles, a pause is repaired from REST, a restart
reads the store. Each step states what must happen (the check a frame got, the bars it sealed, the
time a run is due, the candles a read gives); a step that states no sealed bars must seal none.
Every number is typed by hand from the design; none is computed by the code under test.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from screener_helpers import day_key, hms, ms

from sudarshan.alerts.market import book as alerts_book
from sudarshan.alerts.market import clock, sessions
from sudarshan.screener import bars as B
from sudarshan.screener import candles as C
from sudarshan.screener import types as T

DOC = json.loads((Path(__file__).parent / "vectors" / "bars.json").read_text("utf-8"))
DEFAULTS = DOC["defaults"]
FLAGS = DOC["flags"]
CALENDAR = DOC["calendar"]
CASES = {case["id"]: case for case in DOC["cases"]}
NAN = float("nan")


def bits(names) -> int:
    return sum(FLAGS[name] for name in names)


def number(value):
    """A vector number: the text 'NaN' is the float, everything else as written."""
    return NAN if value == "NaN" else value


def clock_ms(text: str, day: str) -> int:
    """'10:00:05.050', '15:30' or '2026-10-19 15:29:59.000' on ``day`` -> epoch ms."""
    if " " in text:
        day, text = text.split(" ")
    return ms(day, text)


def sessions_on(day: str) -> dict:
    """The day's sessions: a weekday that is no holiday, or a day with a special window."""
    when = date.fromisoformat(day)
    window = CALENDAR["windows"].get(day)
    span = None
    if window is not None:
        opens, closes = (int(part[:2]) * 60 + int(part[3:]) for part in window.split("-"))
        span = (opens, closes)
    trading = day not in CALENDAR["holidays"] and (when.weekday() < 5 or window is not None)
    return sessions.sessions_of(when, trading_day=trading, window=span)


def pauses_of(rows, day: str) -> tuple:
    return tuple(
        clock.Pause(clock_ms(start, day), None if end is None else clock_ms(end, day), cause)
        for start, end, cause in rows
    )


def bar_of(spec: dict, day: str) -> B.Bar:
    start = clock_ms(spec["at"], day)
    return B.Bar(
        start,
        spec["o"],
        spec["h"],
        spec["l"],
        spec["c"],
        spec["v"],
        spec.get("oi", -1),
        spec.get("atp", 0),
        bits(spec.get("flags", ())),
        clock_ms(spec["until"], day) if "until" in spec else start,
    )


def bar_view(bar: B.Bar, until: bool) -> dict:
    out = {
        "at": hms(bar.start),
        "o": bar.o,
        "h": bar.h,
        "l": bar.low,
        "c": bar.c,
        "v": bar.v,
        "oi": bar.oi,
        "atp": bar.atp,
        "flags": bar.flags,
    }
    if until:
        out["until"] = hms(bar.until)
    return out


def expect_bars(got, wanted, day: str) -> None:
    views = [bar_view(bar, False) for bar in got]
    expected = []
    for spec in wanted:
        view = bar_view(bar_of(spec, day), "until" in spec)
        view.pop("until", None)
        expected.append(view)
    assert views == expected, "bars"
    for bar, spec in zip(got, wanted, strict=True):
        if "until" in spec:
            assert hms(bar.until) == hms(clock_ms(spec["until"], day)), f"until of {spec['at']}"


def candle_view(candle: T.Candle) -> dict:
    return {
        "start": hms(candle.start),
        "end": hms(candle.end),
        "session": candle.session,
        "o": candle.o,
        "h": candle.h,
        "l": candle.low,
        "c": candle.c,
        "v": candle.v,
        "oi": candle.oi,
        "atp": candle.atp,
        "flags": candle.flags,
    }


def candle_spec(spec: dict, day: str) -> dict:
    return {
        "start": hms(clock_ms(spec["start"], day)),
        "end": hms(clock_ms(spec["end"], day)),
        "session": day_key(day),
        "o": spec["o"],
        "h": spec["h"],
        "l": spec["l"],
        "c": spec["c"],
        "v": spec["v"],
        "oi": spec.get("oi", -1),
        "atp": spec.get("atp", 0),
        "flags": bits(spec.get("flags", ())),
    }


def expect_series(found: T.Series, tf, forming: bool, wanted, day: str) -> None:
    assert found.tf == tf
    assert found.forming is forming, f"forming at tf {tf}"
    got = [candle_view(found.candle(i)) for i in range(len(found))]
    assert got == [candle_spec(spec, day) for spec in wanted], f"candles at tf {tf}"


class Run:
    """One case, played against a fresh Bars."""

    def __init__(self, case: dict):
        self.case = case
        self.day = case.get("day", DEFAULTS["day"])
        self.key = case.get("key", DEFAULTS["key"])
        self.bars = B.Bars()
        self.cums: dict[tuple[str, int], int] = {}

    # ---- the world at one step -------------------------------------------------------------
    def market(self, now: int, step: dict) -> clock.Market:
        status = step.get("status", self.case.get("status", {}))
        age = step.get("feed_age_ms", 0)
        return clock.Market(
            today=day_key(self.day),
            sessions=sessions_on(self.day),
            previous=day_key(DEFAULTS["previous"]),
            status=status,
            feed=clock.Feed("connected", True, now - age),
            pauses=pauses_of(self.case.get("pauses", ()), self.day),
            auction=frozenset(self.case.get("auction", ())),
        )

    def frame(self, tick: dict, step: dict, now: int) -> dict:
        out: dict = {}
        key = step["key"] if "key" in step else self.key
        if key is not None:
            out["key"] = key
        for name in ("ltt", "ltp", "ltq", "vtt", "oi", "atp", "bid", "ask"):
            value = tick.get(name)
            if value is None:
                continue
            if name == "ltt" and isinstance(value, str):
                value = NAN if value == "NaN" else clock_ms(value, self.day)
            out[name] = number(value)
        if "recv" not in tick:
            out["ts_recv"] = now
        elif tick["recv"] is not None:
            out["ts_recv"] = clock_ms(tick["recv"], self.day)
        if "ohlc" in tick:  # the exchange's day candle, as every full-mode frame carries it
            spec = tick["ohlc"]
            out["ohlc"] = {
                "open": number(spec["open"]),
                "high": number(spec["high"]),
                "low": number(spec["low"]),
                "close": number(spec["close"]),
                "vol": spec["vol"],
                "ts": clock_ms(spec["ts"], self.day),
            }
        if "i1" in tick:
            spec = tick["i1"]
            out["ohlc_1m"] = {
                "open": number(spec["open"]),
                "high": number(spec["high"]),
                "low": number(spec["low"]),
                "close": number(spec["close"]),
                "vol": spec["vol"],
                "ts": clock_ms(spec["ts"], self.day),
            }
        return out

    def starts(self, entries) -> list[tuple[str, int]]:
        found = []
        for entry in entries:
            key, text = (self.key, entry) if isinstance(entry, str) else entry
            found.append((key, clock_ms(text, self.day)))
        return found

    def record(self, rows) -> list[tuple[str, int]]:
        for row in rows:
            self.cums[(row.key, row.bar.start)] = row.cum
        return [(row.key, row.bar.start) for row in rows]

    # ---- the steps -------------------------------------------------------------------------
    def play(self) -> None:
        for number_, step in enumerate(self.case["steps"]):
            if "at" not in step:
                self.day = step["day"]
                continue
            now = clock_ms(step["at"], self.day)
            market = self.market(now, step)
            where = f"step {number_} at {step['at']}"
            if "tick" in step:
                self.tick(step, now, market, where)
            elif "clock" in step:
                made = self.record(self.bars.on_clock(now, market))
                assert made == self.starts(step["sealed"]), where
            elif "due" in step:
                spec = step["due"]
                found = self.bars.due(
                    self.key,
                    clock_ms(spec["boundary"], self.day),
                    market,
                    unhealed=spec.get("unhealed", False),
                )
                assert hms(found) == hms(clock_ms(step["is"], self.day)), where
            elif "read" in step:
                spec = step["read"]
                found = C.read(
                    self.bars, self.key, spec["tf"], market, clock_ms(spec["boundary"], self.day)
                )
                expect_series(found, spec["tf"], step["forming"], step["candles"], self.day)
            elif "repair" in step:
                self.repair(step, market, where)
            elif "restore" in step:
                self.restore(step["restore"])
            elif "follow" in step:
                self.follow(step, now, market, where)
            elif "auction" in step:
                end, printed = self.bars.auction_of(self.key, market)
                spec = step["auction"]
                want_end = None if spec["end"] is None else clock_ms(spec["end"], self.day)
                assert (end, printed) == (want_end, spec["printed"]), where
            else:
                raise AssertionError(f"unknown step {step}")

    def tick(self, step: dict, now: int, market: clock.Market, where: str) -> None:
        result = self.bars.on_tick(self.frame(step["tick"], step, now), now, market)
        assert result.check == step.get("check", "ok"), where
        assert self.record(result.sealed) == self.starts(step.get("sealed", [])), where

    def repair(self, step: dict, market: clock.Market, where: str) -> None:
        spec = step["repair"]
        session = day_key(self.day)
        rest = []
        for row in spec["rest"]:
            start = clock_ms(row["start"], self.day)
            rest.append(
                T.Candle(
                    start,
                    start + 60_000,
                    session,
                    row["o"],
                    row["h"],
                    row["l"],
                    row["c"],
                    row["v"],
                    row.get("oi", -1),
                )  # fmt: skip
            )
        result = self.bars.repair(self.key, rest, clock_ms(spec["through"], self.day), market)
        wrote = [(row.key, row.bar.start, row.cum) for row in result.rows]
        assert wrote == [
            (self.key, clock_ms(row["at"], self.day), row["cum"]) for row in step["rows"]
        ], where
        assert list(result.removed) == [clock_ms(text, self.day) for text in step["removed"]], where
        for row in result.rows:
            assert row.bar.flags & T.REPAIRED, where
        self.record(result.rows)

    def restore(self, spec: dict) -> None:
        rows = [
            B.Sealed(row.get("key", self.key), bar_of(row["bar"], self.day), row["cum"])
            for row in spec["rows"]
        ]
        since = clock_ms(spec["since"], self.day) if "since" in spec else None
        self.bars.restore(day_key(self.day), rows, spec["printed"], since=since)
        self.record(rows)

    def follow(self, step: dict, now: int, market: clock.Market, where: str) -> None:
        """The whole set of keys followed from now on, the bars it sealed and the gaps it kept."""
        made = self.bars.follow_from(step["follow"], now, market)
        assert self.record(made.sealed) == self.starts(step.get("sealed", [])), where
        found = [(key, hms(gap.start), hms(gap.end)) for key, gap in made.gaps]
        wanted = [
            (key, hms(clock_ms(start, self.day)), hms(clock_ms(end, self.day)))
            for key, start, end in step.get("gaps", [])
        ]
        assert found == wanted, f"{where}: gaps"

    # ---- what the case ends with -----------------------------------------------------------
    def finish(self) -> None:
        case = self.case
        wanted = case.get("bars")
        if wanted is not None:
            by_key = wanted if isinstance(wanted, dict) else {self.key: wanted}
            for key, specs in by_key.items():
                expect_bars(self.bars.bars(key), specs, self.day)
        if "cum" in case:
            last = max(bar.start for bar in self.bars.bars(self.key))
            assert self.cums[(self.key, last)] == case["cum"], "cum"
        assert self.bars.late(self.key) == case.get("late", 0), "late"
        if "printed" in case:
            assert self.bars.printed(self.key, day_key(self.day)) is case["printed"], "printed"
        if "gaps" in case:  # every stretch the key was not followed, kept for the day
            held = [(hms(gap.start), hms(gap.end)) for gap in self.bars.gaps(self.key)]
            assert held == [
                (hms(clock_ms(start, self.day)), hms(clock_ms(end, self.day)))
                for start, end in case["gaps"]
            ], "gaps"


@pytest.mark.parametrize("case", DOC["cases"], ids=[case["id"] for case in DOC["cases"]])
def test_the_case_plays_out_as_written(case):
    run = Run(case)
    run.play()
    run.finish()


def test_the_vectors_cover_what_the_design_names():
    names = {
        "B1", "B2", "B2b", "B3", "B4", "B5", "B6", "B7", "B8", "B9", "B10", "B11", "B12", "B13",
        "B13b", "B13c", "G1", "G2", "G3", "G4", "G5", "G6", "G7",
    }  # fmt: skip
    assert names <= set(CASES)
    assert len(DOC["cases"]) >= 80
    assert len(CASES) == len(DOC["cases"]), "case ids are unique"
    for case in DOC["cases"]:
        reads = [step for step in case["steps"] if "read" in step]
        assert "bars" in case or reads, f"{case['id']} states neither its bars nor a read"
    assert len(DOC["aggregate"]) >= 9 and len(DOC["weekly"]) >= 3 and len(DOC["timing"]) >= 13


# ---- the typed tables are the code's ---------------------------------------------------------------
def test_the_flag_bits_and_check_names_typed_in_the_vectors_are_the_codes():
    assert FLAGS == {
        "unverified": T.UNVERIFIED,
        "repaired": T.REPAIRED,
        "late": T.LATE,
        "i1": T.FROM_I1,
        "no_exchange_day": T.NO_EXCHANGE_DAY,
    }
    codes = {
        "ok": clock.OK,
        "late": B.LATE_TRADE,
        "closed_today": clock.CLOSED_TODAY,
        "outside_hours": clock.OUTSIDE_HOURS,
        "not_open_yet": clock.NOT_OPEN_YET,
        "closed_by_exchange": clock.CLOSED_BY_EXCHANGE,
        "feed_down": clock.FEED_DOWN,
        "stale": clock.STALE,
        "auction": clock.AUCTION_PRINT,
        "not_a_trade": alerts_book.NOT_A_TRADE,
        "bad_value": alerts_book.BAD_VALUE,
        "nothing_new": alerts_book.NOTHING_NEW,
    }
    assert DOC["checks"] == codes
    used = {step.get("check") for case in DOC["cases"] for step in case["steps"] if "tick" in step}
    assert used <= set(DOC["checks"]) | {None}


def test_the_timings_come_from_the_alerts_lane_not_from_a_copy():
    assert B.MINUTE_MS == 60_000
    assert B.seal_at(0) == clock.ARRIVAL_GRACE_MS == 5000
    assert B.REPAIR_WAIT_MS == 60_000


# ---- the pure timing functions ---------------------------------------------------------------------
def timing_args(args: dict) -> dict:
    day = DEFAULTS["day"]
    out = {}
    for name, value in args.items():
        out[name] = clock_ms(value, day) if isinstance(value, str) else value
    return out


@pytest.mark.parametrize(
    "spec", DOC["timing"], ids=[f"timing{i}" for i in range(len(DOC["timing"]))]
)
def test_when_a_run_judges(spec):
    function = getattr(B, spec["fn"])
    args = timing_args(spec["args"])
    if spec["fn"] == "seal_at":
        found = function(args["end"])
    elif spec["fn"] == "repair_by":
        found = function(args["boundary"])
    else:
        boundary = args.pop("boundary")
        found = function(boundary, **args)
    assert hms(found) == hms(clock_ms(spec["is"], DEFAULTS["day"]))


# ---- aggregation of typed bars ---------------------------------------------------------------------
def aggregate_bars(specs, day):
    return [bar_of(spec, day) for spec in specs]


@pytest.mark.parametrize("spec", DOC["aggregate"], ids=[spec["id"] for spec in DOC["aggregate"]])
def test_candles_from_bars(spec):
    day = DEFAULTS["day"]
    grid = sessions_on(day)["NSE_EQ"]
    found_bars = aggregate_bars(spec["bars"], day)
    pauses = pauses_of([(a, b, "feed") for a, b in spec["pauses"]], day)
    for read in spec["reads"]:
        found = C.series(found_bars, read["tf"], grid, clock_ms(read["boundary"], day), pauses)
        expect_series(found, read["tf"], read["forming"], read["candles"], day)


def day_candle(spec: dict) -> T.Candle:
    day = spec["day"]
    return T.Candle(
        clock_ms("09:15", day),
        clock_ms("15:30", day),
        day_key(day),
        spec["o"],
        spec["h"],
        spec["l"],
        spec["c"],
        spec["v"],
        spec["oi"],
        spec["atp"],
        bits(spec["flags"]),
    )


@pytest.mark.parametrize("spec", DOC["weekly"], ids=[spec["id"] for spec in DOC["weekly"]])
def test_weeks_from_days(spec):
    weeks = C.weekly([day_candle(day) for day in spec["days"]])
    got = [candle_view(week) for week in weeks]
    wanted = []
    for week in spec["weeks"]:
        wanted.append(
            {
                "start": hms(clock_ms("09:15", week["first"])),
                "end": hms(clock_ms("15:30", week["last"])),
                "session": day_key(week["first"]),
                "o": week["o"],
                "h": week["h"],
                "l": week["l"],
                "c": week["c"],
                "v": week["v"],
                "oi": week["oi"],
                "atp": 0,
                "flags": bits(week["flags"]),
            }
        )
    assert got == wanted
    # hms drops the date: compare the days too
    assert [week.start for week in weeks] == [clock_ms("09:15", w["first"]) for w in spec["weeks"]]
    assert [week.end for week in weeks] == [clock_ms("15:30", w["last"]) for w in spec["weeks"]]


def test_a_timeframe_not_built_from_minutes_is_refused():
    grid = sessions_on(DEFAULTS["day"])["NSE_EQ"]
    for tf in ("W", "M", 2, 7, 120, None):
        with pytest.raises(ValueError):
            C.series([], tf, grid, 0)
