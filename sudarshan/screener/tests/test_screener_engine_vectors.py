"""engine.py against hand-computed vectors (vectors/engine.json).

A case names the candles each timeframe holds, the screen's lines, the boundary being judged and
what the run knows (previous close, open interest, options, the opening time of the session, the
index). ``expect`` gives the outcome kind and, where listed, the reasons, the figures a hit keeps,
the rank and the marks. Every case also states its marks: "forming" and "repaired" are asserted
False unless the case says True, and a hit with no rank must have none.

The expectations are typed from the rules in engine.py's notes, never produced by the engine.
"""

from __future__ import annotations

import json
from fractions import Fraction
from functools import cache
from pathlib import Path

import pytest
from screener_helpers import assert_same, candle, day_key, ladder, ms, want

from sudarshan.screener import engine, library, options, spec
from sudarshan.screener import types as T
from sudarshan.screener.types import Candle, Outcome, Series, Why

VECTORS = json.loads((Path(__file__).parent / "vectors" / "engine.json").read_text("utf-8"))
CASES = VECTORS["judge"]
MIN_CASES = 200


# ---- turning the vectors' words into the engine's objects ---------------------------------------
def span(day: str, start: str, minutes: int):
    begin = ms(day, start)
    return begin, begin + minutes * 60_000, day_key(day)


def pairs(day: str, start: str, minutes: int, oc, v: int) -> list[Candle]:
    begin, _, key = span(day, start, minutes)
    step = minutes * 60_000
    return [
        Candle(begin + k * step, begin + (k + 1) * step, key, o, max(o, c), min(o, c), c, v)
        for k, (o, c) in enumerate(oc)
    ]


def flat_run(day: str, start: str, minutes: int, price: int, count: int, v: int) -> list[Candle]:
    begin, _, key = span(day, start, minutes)
    step = minutes * 60_000
    return [
        Candle(begin + k * step, begin + (k + 1) * step, key, price, price, price, price, v)
        for k in range(count)
    ]


@cache
def candles_of(name: str) -> tuple[Candle, ...]:
    rows: list[Candle] = []
    for item in VECTORS["series"][name]["candles"]:
        if isinstance(item, dict) and "from" in item:
            rows += candles_of(item["from"])
        elif isinstance(item, dict) and "ladder" in item:
            rows += ladder(**item["ladder"])
        elif isinstance(item, dict) and "pairs" in item:
            rows += pairs(**item["pairs"])
        elif isinstance(item, dict) and "flat" in item:
            rows += flat_run(**item["flat"])
        else:
            day, start, minutes, o, h, low, c, v, *extra = item
            rows.append(candle(day, start, minutes, o, h, low, c, v, **(extra[0] if extra else {})))
    for index, flags in VECTORS["series"][name].get("flags", {}).items():
        rows[int(index)] = rows[int(index)]._replace(flags=flags)
    return tuple(rows)


def timeframe(key: str) -> T.TF:
    return int(key) if key.isdigit() else key


def series_named(tf_key: str, name: str) -> Series:
    entry = VECTORS["series"][name]
    assert str(entry["tf"]) == tf_key, f"{name} holds {entry['tf']} candles, not {tf_key}"
    return Series.of(timeframe(tf_key), candles_of(name), entry["forming"])


def expand(node):
    """A tree of line names ('T', 'off:F') and groups, as the screen's own JSON."""
    lines = VECTORS["lines"]
    if isinstance(node, str):
        on = not node.startswith("off:")
        key = node.removeprefix("off:")
        return {**lines[key], "id": key if on else f"off_{key}", "on": on}
    for kind in ("all", "any", "none"):
        if kind in node:
            return {kind: [expand(item) for item in node[kind]]}
    if "not" in node:
        return {"not": expand(node["not"])}
    return node


def screen_of(case: dict) -> dict:
    return {
        "schema": 1,
        "name": case["id"],
        "universe": {"kind": case.get("universe", "fno_stocks")},
        "run": case.get("run", {"on": "close", "tf": 15}),
        "where": expand(case["where"]),
        "rank": case.get("rank"),
        "target": None,
        "alerts": {},
    }


def view_of(raw: dict) -> options.View:
    chains = {
        name: options.Chain(
            name, item["spot"], tuple(options.Strike(**row) for row in item["strikes"])
        )
        for name, item in raw.get("chains", {}).items()
    }
    return options.View(
        chains=chains, vix_now=raw.get("vix_now"), vix_history=raw.get("vix_history", ())
    )


def subject_of(case: dict) -> engine.Subject:
    return engine.Subject(
        "TEST",
        {timeframe(key): series_named(key, name) for key, name in case["tfs"].items()},
        cp=case.get("cp"),
        year=tuple(case["year"]) if "year" in case else None,
        prev_oi=case.get("prev_oi"),
        underlying=case.get("underlying"),
        auction_end=ms(*case["auction_end"]) if "auction_end" in case else None,
        printed=case.get("printed", False),
    )


def context_of(case: dict) -> engine.Context:
    day, clock = case["at"]
    opens = {day_key(d): ms(d, c) for d, c in case.get("opens", {}).items()}
    bench = {}
    for key, name in case.get("bench", {}).items():
        index, tf = key.split(":")
        bench[(index, int(tf))] = series_named(tf, name)
    return engine.Context(
        boundary=ms(day, clock),
        session=day_key(day),
        previous=day_key(case["previous"]) if "previous" in case else 0,
        opens=opens.get,
        bench=lambda index, tf: bench.get((index, tf)),
        options=view_of(case["view"]) if "view" in case else None,
    )


def judged(case: dict) -> Outcome:
    plan = spec.compile_plan(screen_of(case))
    return engine.judge(plan, subject_of(case), context_of(case))


def assert_outcome(got: Outcome, expect: dict) -> None:
    assert got.kind == expect["kind"]
    assert list(got.why) == [want(item) for item in expect.get("why", [])]
    assert got.forming is expect.get("forming", False)
    assert got.repaired is expect.get("repaired", False)
    if got.kind != T.HIT:
        assert got.values is None and got.rank is None
        return
    for line, shown in expect.get("values", {}).items():
        assert got.values[line] == shown, line
    if "value_ids" in expect:
        assert set(got.values) == set(expect["value_ids"])
    if "rank" in expect:
        assert_same(got.rank, expect["rank"])
    else:
        assert got.rank is None


# ---- the vectors file -------------------------------------------------------------------------
def reached(names) -> set:
    """The series named and every series they are built from."""
    seen, stack = set(), list(names)
    while stack:
        name = stack.pop()
        if name not in seen:
            seen.add(name)
            stack += [
                item["from"]
                for item in VECTORS["series"][name]["candles"]
                if isinstance(item, dict) and "from" in item
            ]
    return seen


def test_the_vectors_are_what_the_tests_expect():
    ids = [case["id"] for case in CASES]
    assert len(ids) == len(set(ids)), "two cases share an id"
    assert len(ids) >= MIN_CASES, "the engine vectors lost cases"
    used = set()
    for case in CASES:
        used |= set(case["tfs"].values()) | set(case.get("bench", {}).values())
    assert set(VECTORS["series"]) == reached(used), "a series no case reads"
    for section, floor in (
        ("ranked", 9),
        ("show", 20),
        ("compare", 35),
        ("arithmetic", 20),
        ("within", 24),
    ):
        assert len(VECTORS[section]) >= floor


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_a_judgement(case):
    assert_outcome(judged(case), case["expect"])


# ---- the order of the matches ------------------------------------------------------------------
def exact(raw):
    return Fraction(raw) if isinstance(raw, str) else raw


@pytest.mark.parametrize("case", VECTORS["ranked"], ids=[c["id"] for c in VECTORS["ranked"]])
def test_matches_are_ordered(case):
    hits = [(symbol, Outcome(T.HIT, rank=exact(rank))) for symbol, rank in case["items"]]
    assert engine.ranked(hits, case["order"]) == case["expect"]
    assert engine.ranked(reversed(hits), case["order"]) == case["expect"], "input order matters"


# ---- the words for a value ---------------------------------------------------------------------
@pytest.mark.parametrize("case", VECTORS["show"], ids=[c["id"] for c in VECTORS["show"]])
def test_a_value_is_shown_as_the_page_writes_it(case):
    value = case["value"] if case["unit"] == T.STATE else exact(case["value"])
    assert engine.show(value, case["unit"]) == case["expect"]


# ---- comparing numbers -------------------------------------------------------------------------
@pytest.mark.parametrize(
    "row", VECTORS["compare"], ids=[f"C{k}" for k in range(len(VECTORS["compare"]))]
)
def test_two_numbers_are_compared(row):
    op, a, b, expected = row
    assert engine.compare(op, exact(a), exact(b)) is expected


@pytest.mark.parametrize(
    "row", VECTORS["arithmetic"], ids=[f"A{k}" for k in range(len(VECTORS["arithmetic"]))]
)
def test_numbers_are_added_subtracted_multiplied_and_divided(row):
    op, a, b, expected = row
    assert_same(engine.arithmetic(op, exact(a), exact(b)), expected)


@pytest.mark.parametrize(
    "row", VECTORS["within"], ids=[f"W{k}" for k in range(len(VECTORS["within"]))]
)
def test_a_number_is_within_a_percent_of_another(row):
    a, b, pct, expected = row
    got = engine.within(exact(a), exact(b), Fraction(pct))
    if isinstance(expected, dict):
        assert got == want(expected)
    else:
        assert got is expected


# ---- the candle at an edge ---------------------------------------------------------------------
def tiny(forming: bool, *starts: str) -> Series:
    day = "2026-10-28"
    return Series.of(
        "D", [candle(day, start, 375, 1, 1, 1, 1) for start in starts], forming=forming
    )


def test_a_forming_candle_that_has_not_started_is_not_read():
    day, forming = ms("2026-10-28", "09:15"), tiny(True, "09:15")
    assert engine.base_index(forming, day) is None, "it starts now: nothing has happened in it"
    assert engine.base_index(forming, day + 60_000) == (0, True)
    assert engine.base_index(forming, day + 375 * 60_000) == (0, False), "the end ends it"
    assert engine.base_index(tiny(False, "09:15"), day + 60_000) is None, (
        "a sealed series cannot form"
    )
    assert engine.base_index(tiny(False, "09:15"), day + 375 * 60_000) == (0, False)


def test_a_candle_ends_at_a_boundary_only_when_one_ends_exactly_there():
    day, series = ms("2026-10-28", "09:15"), tiny(False, "09:15")
    end = day + 375 * 60_000
    assert engine.ends_at(series, end)
    assert not engine.ends_at(series, end - 1)
    assert not engine.ends_at(series, end + 1)
    assert not engine.ends_at(tiny(False), end)


# ---- what is ready ---------------------------------------------------------------------------------
def line(left, op="gt", right=None, **extra):
    return {"id": "c1", "left": left, "op": op, "right": right or {"num": "1"}, **extra}


def plan_of(where, run=None):
    screen = {
        "schema": 1, "name": "t", "universe": {"kind": "fno_stocks"},
        "run": run or {"on": "close", "tf": 15}, "where": where, "rank": None,
        "target": None, "alerts": {},
    }  # fmt: skip
    return spec.compile_plan(screen)


def held(count: int, days: int = 1) -> dict:
    """15-minute and day candles in the numbers a plan asks for: a day candle per day held."""
    closes = [100 + k for k in range(count)]
    day_candles = [candle(f"2026-10-{20 + k}", "09:15", 375, 1, 1, 1, 1) for k in range(days)]
    return {
        15: Series.of(15, ladder("2026-10-28", "09:15", 15, closes)),
        "D": Series.of("D", day_candles),
    }


def test_the_shortfall_names_each_timeframe_that_is_not_ready():
    ema = {"ind": "ema", "p": {"length": 20}, "tf": 15}
    day = {**line({"field": "close", "tf": "D", "at": {"ago": 1}}), "id": "c2"}
    plan = plan_of({"all": [line(ema), day]})
    assert plan.need == {15: 60, "D": 2}
    assert engine.shortfall(plan, engine.Subject("X", {})) == {15: (60, 0), "D": (2, 0)}
    assert engine.shortfall(plan, engine.Subject("X", held(29, 0))) == {15: (60, 29), "D": (2, 0)}
    assert engine.shortfall(plan, engine.Subject("X", held(60, 1))) == {"D": (2, 1)}
    assert engine.shortfall(plan, engine.Subject("X", held(59, 2))) == {15: (60, 59)}
    assert engine.shortfall(plan, engine.Subject("X", held(61, 2))) == {}


# ---- one indicator, computed once ----------------------------------------------------------------------
def test_an_indicator_is_computed_once_for_every_line_and_offset_that_reads_it(monkeypatch):
    calls = []
    real = library.compute

    def counting(name, params, series):
        calls.append((name, dict(params), series.tf))
        return real(name, params, series)

    monkeypatch.setattr(library, "compute", counting)
    ema = {"ind": "ema", "p": {"length": 14}, "tf": 1}
    where = {
        "all": [
            line({"field": "close", "tf": 1}, "gte", ema),
            {**line({"field": "close", "tf": 1}, "crosses_above", ema), "id": "c2"},
            {**line(ema, "gt", {"num": "1"}), "id": "c3"},
        ]
    }
    plan = plan_of(where, {"on": "close", "tf": 1})
    case = {"tfs": {"1": "flat100"}, "at": ["2026-10-28", "10:55"]}
    subject, ctx = subject_of(case), context_of(case)
    engine.judge(plan, subject, ctx)
    assert len(calls) == 1, calls
    memo: dict = {}
    engine.judge(plan, subject, ctx, memo)
    engine.judge(plan, subject, ctx, memo)
    assert len(calls) == 2, "a shared memo serves a second plan on the same instrument"
    engine.judge(plan, subject, ctx)
    assert len(calls) == 3, "no memo, no sharing"
