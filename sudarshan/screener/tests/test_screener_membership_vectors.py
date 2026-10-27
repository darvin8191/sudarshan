"""membership.py against hand-written vectors (design M1-M12 and the cases around them).

A scenario is told run by run, the way the service lives it: a run gives each instrument an outcome,
the sync reads the list, applies the plan and reads it again, and the owner takes things out of the
list or puts them in by hand between runs. Each step states what must happen (the transitions in
order, the matches that wait and why, the list, parts of rows); nothing is computed by the code
under test. A step that states no events must produce none, so a silent extra transition fails.
"""

from __future__ import annotations

import json
import random
from fractions import Fraction
from pathlib import Path

import pytest
from screener_helpers import day_key, ms

from sudarshan.screener import engine
from sudarshan.screener import membership as mem
from sudarshan.screener import types as T

VECTORS = json.loads((Path(__file__).parent / "vectors" / "membership.json").read_text("utf-8"))
DAY = "2026-10-28"
TIMES = ("first_seen_at", "last_seen_at", "exited_at")
SECTION_FLOORS = {"scenarios": 55, "plans": 10, "free": 7, "reconcile": 13, "graces": 8}


def stamp(text, day: str = DAY):
    """'10:15' on the scenario's day, or '2026-10-29 09:30'; None stays None."""
    if text is None:
        return None
    if " " in text:
        day, text = text.split(" ")
    return ms(day, text)


def session_of(text) -> int:
    return text if isinstance(text, int) else day_key(text)


def make_row(spec: dict, day: str = DAY) -> mem.Row:
    fields = dict(spec)
    key, status = fields.pop("key"), fields.pop("status")
    for name in TIMES:
        if name in fields:
            fields[name] = stamp(fields[name], day)
    if "missing_session" in fields:
        fields["missing_session"] = session_of(fields["missing_session"])
    return mem.Row(key, key, "NSE", status, **fields)


def check_row(row: mem.Row | None, want: dict | None, where: str, day: str = DAY) -> None:
    if want is None:
        assert row is None, f"{where}: there should be no row, got {row}"
        return
    assert row is not None, f"{where}: there is no row"
    for field, expected in want.items():
        if field in TIMES:
            expected = stamp(expected, day)
        elif field == "missing_session":
            expected = 0 if expected == 0 else session_of(expected)
        assert getattr(row, field) == expected, (
            f"{where}: {field} is {getattr(row, field)!r}, not {expected!r}"
        )


def check_rows(world: World, step: dict, where: str, day: str = DAY) -> None:
    rows = world.screens[step.get("screen", "A")]
    for key, want in step.get("rows", {}).items():
        check_row(rows.get(key), want, f"{where} row {key}", day)


class World:
    """The service's view: each screen's rows, and OpenAlgo's list (key to whether the screener added it)."""

    def __init__(self, case: dict):
        self.screens = {name: {} for name in case.get("screens", ["A"])}
        self.list = dict(case.get("list", {}))
        self.grace = mem.Grace.of(case.get("grace"))
        self.max_adds = case.get("max_adds", 10)
        self.order = case.get("order", "desc")

    def every_row(self) -> list[mem.Row]:
        return [row for rows in self.screens.values() for row in rows.values()]

    def reconcile(self) -> None:
        for rows in self.screens.values():
            rows.update(mem.reconcile(list(rows.values()), self.list))


def invariants(world: World, where: str) -> None:
    for row in world.every_row():
        assert row.status in mem.STATUSES, where
        if row.pinned:
            assert row.status in (mem.ACTIVE, mem.MISSING), (
                f"{where}: a pinned row is a member ({row.key})"
            )
        if row.status == mem.ACTIVE:
            assert row.misses == 0 and row.exited_at is None, f"{where}: active but {row}"
        if row.status == mem.MISSING:
            assert row.misses >= 1, f"{where}: missing with no misses ({row.key})"
        if row.misses >= 1:
            assert row.hits == 0, f"{where}: a run of hits and a miss at once ({row.key})"
        assert row.entries >= 1 and row.hits >= 0 and row.misses >= 0, where


def exact(rest: list) -> list:
    """A rank written as text ("100000000000000001", "1/10") is an exact number, as the engine gives."""
    return [Fraction(item) if isinstance(item, str) else item for item in rest]


def do_run(world: World, step: dict, where: str) -> None:
    rows = world.screens[step.get("screen", "A")]
    day = step.get("day", DAY)
    run = mem.Run(
        step["run"],
        ms(day, step["at"]),
        day_key(day),
        step.get("version", 1),
        step.get("last", False),
    )
    results = [
        mem.Result(key, key, "NSE", outcome, *exact(rest))
        for key, outcome, *rest in step["results"]
    ]
    free = step.get("free", mem.free_slots(world.every_row(), world.list))
    before_rows, before_list = dict(rows), dict(world.list)
    got = mem.apply_run(
        rows, results, run, world.grace,
        items=world.list, max_adds=step.get("max_adds", world.max_adds), order=world.order, free=free,
    )  # fmt: skip
    assert rows == before_rows and world.list == before_list, f"{where}: the inputs were changed"
    assert [(change.key, change.event) for change in got.changes] == [
        tuple(e) for e in step["events"]
    ], where
    assert list(got.waiting) == step.get("waiting", []), f"{where}: waiting"
    assert got.blocked == step.get("blocked", ""), f"{where}: blocked"
    if "touched" in step:
        assert sorted(got.rows) == sorted(step["touched"]), f"{where}: touched"
    for change in got.changes:
        assert got.rows[change.key] == change.row, f"{where}: {change.key} change and row differ"
    rows.update(got.rows)
    check_rows(world, step, where, day)


def do_sync(world: World, step: dict, where: str) -> None:
    world.reconcile()
    plan = mem.plan_sync(world.every_row(), world.list)
    assert [row.key for row in plan.add] == step["add"], f"{where}: add"
    assert list(plan.remove) == step["remove"], f"{where}: remove"
    assert list(plan.held) == step.get("held", []), f"{where}: held"
    for key in plan.remove:
        del world.list[key]
    for row in plan.add:
        world.list[row.key] = True
    world.reconcile()
    if "list" in step:
        assert world.list == step["list"], f"{where}: the list"
    check_rows(world, step, where)


def do_action(world: World, step: dict, where: str) -> None:
    verb = next(name for name in ("pin", "unpin", "dismiss", "restore") if name in step)
    rows = world.screens[step.get("screen", "A")]
    key = step[verb]
    row = rows.get(key)
    if step.get("refused"):
        assert row is None or getattr(mem, verb)(row) is None, f"{where}: {verb} should be refused"
        return
    moved = getattr(mem, verb)(row)
    assert moved is not None, f"{where}: {verb} was refused"
    rows[key] = moved
    check_rows(world, step, where)


def play(case: dict) -> None:
    world = World(case)
    for key, spec in case.get("rows", {}).items():
        world.screens["A"][key] = make_row({"key": key, **spec})
    for number, step in enumerate(case["steps"], 1):
        where = f"{case['id']} step {number}"
        if "results" in step:
            do_run(world, step, where)
        elif "sync" in step:
            do_sync(world, step, where)
        elif "reconcile" in step:
            world.reconcile()
            assert "list" not in step or world.list == step["list"], where
            check_rows(world, step, where)
        elif "owner_removes" in step:
            for key in step["owner_removes"]:
                del world.list[key]
        elif "owner_adds" in step:
            for key in step["owner_adds"]:
                world.list[key] = False
        else:
            do_action(world, step, where)
        invariants(world, where)


# ---- the vectors file ------------------------------------------------------------------------------
def test_the_vectors_are_what_the_tests_expect():
    for section, floor in SECTION_FLOORS.items():
        ids = [case["id"] for case in VECTORS[section]]
        assert len(ids) == len(set(ids)), f"{section}: ids repeat"
        assert len(ids) >= floor, f"{section}: {len(ids)} cases, expected at least {floor}"
    told = {
        event
        for case in VECTORS["scenarios"]
        for step in case["steps"]
        for _, event in step.get("events", [])
    }
    assert told == {"ENTERED", "MISSING", "BACK", "EXITED", "FORGOTTEN"}, (
        "every transition is told somewhere"
    )
    verbs = {verb for case in VECTORS["scenarios"] for step in case["steps"] for verb in step}
    for verb in (
        "pin",
        "unpin",
        "dismiss",
        "restore",
        "sync",
        "owner_removes",
        "owner_adds",
        "reconcile",
        "refused",
    ):
        assert verb in verbs, f"no scenario uses {verb}"
    modes = {case.get("grace", {}).get("mode", "runs") for case in VECTORS["scenarios"]}
    assert modes == {"runs", "day_end", "never"}
    design = {f"M{n}" for n in range(1, 13)}
    assert design <= {case["id"] for case in VECTORS["scenarios"]}, "the design's M1-M12"


@pytest.mark.parametrize("case", VECTORS["scenarios"], ids=[c["id"] for c in VECTORS["scenarios"]])
def test_a_scenario(case):
    play(case)


@pytest.mark.parametrize("case", VECTORS["plans"], ids=[c["id"] for c in VECTORS["plans"]])
def test_what_to_add_and_remove(case):
    rows = [make_row(spec) for spec in case["rows"]]
    before = list(rows)
    got = mem.plan_sync(rows, case["items"], case.get("capacity", mem.LIST_MAX))
    assert [row.key for row in got.add] == case["expect"]["add"]
    assert list(got.remove) == case["expect"]["remove"]
    assert list(got.held) == case["expect"]["held"]
    assert rows == before, "the rows were changed"
    shuffled = rows[:]
    random.Random(7).shuffle(shuffled)
    again = mem.plan_sync(shuffled, case["items"], case.get("capacity", mem.LIST_MAX))
    assert (again.add, again.remove, again.held) == (got.add, got.remove, got.held), (
        "the order of the rows matters"
    )


@pytest.mark.parametrize("case", VECTORS["free"], ids=[c["id"] for c in VECTORS["free"]])
def test_room_left_in_the_list(case):
    rows = [make_row(spec) for spec in case["rows"]]
    assert mem.free_slots(rows, case["items"], case.get("capacity", mem.LIST_MAX)) == case["expect"]


@pytest.mark.parametrize("case", VECTORS["reconcile"], ids=[c["id"] for c in VECTORS["reconcile"]])
def test_reading_the_list(case):
    rows = [make_row(spec) for spec in case["rows"]]
    before = list(rows)
    moved = mem.reconcile(rows, case["items"])
    assert rows == before, "the rows were changed"
    assert set(moved) == set(case["expect"]), f"rows that moved: {sorted(moved)}"
    for key, want in case["expect"].items():
        check_row(moved[key], want, f"{case['id']} row {key}")


@pytest.mark.parametrize("case", VECTORS["graces"], ids=[c["id"] for c in VECTORS["graces"]])
def test_a_grace_from_the_screen(case):
    if case["expect"] == "error":
        with pytest.raises(ValueError):
            mem.Grace.of(case["raw"])
        return
    got = mem.Grace.of(case["raw"])
    assert [got.mode, got.n] == case["expect"]


# ---- the order of entries is the order of the results page -----------------------------------------------
def test_matches_enter_in_the_order_the_page_ranks_them():
    """Admission and the results list rank by the same rule: rank, then symbol, unranked last."""
    rng = random.Random(1234)
    for _ in range(200):
        count = rng.randint(1, 9)
        symbols = rng.sample(["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH", "III"], count)
        ranks = [rng.choice([None, 0, 1, 1, 2, 2.5, -1, 3]) for _ in symbols]
        order = rng.choice(["asc", "desc"])
        keys = [
            f"{rng.random():.9f}" for _ in symbols
        ]  # key order has nothing to do with symbol order
        results = [
            mem.Result(k, s, "NSE", T.HIT, rank=r)
            for k, s, r in zip(keys, symbols, ranks, strict=True)
        ]
        got = mem.apply_run(
            {}, results, mem.Run(1, 1, 20261028), mem.Grace(),
            items={}, max_adds=250, order=order, free=250,
        )  # fmt: skip
        page = engine.ranked([(r.symbol, T.Outcome(T.HIT, rank=r.rank)) for r in results], order)
        assert [change.row.symbol for change in got.changes] == page, (symbols, ranks, order)
