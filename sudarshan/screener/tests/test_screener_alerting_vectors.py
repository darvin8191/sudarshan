"""alerting.py against hand-written vectors (design A1-A6 and the cases around them).

A case is a screen's alert settings and a story told run by run. The test carries what the day has
told and whether the alerts are paused from one step to the next, as the service does, and states
nothing the code under test computed: a field the vector does not list must be empty.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from screener_helpers import ms

from sudarshan.screener import alerting as al
from sudarshan.screener import membership as mem
from sudarshan.screener import types as T

VECTORS = json.loads((Path(__file__).parent / "vectors" / "alerting.json").read_text("utf-8"))
SESSION = VECTORS["session"]
DAY = "2026-10-28"
EMPTY = {
    "entered": [],
    "more_entered": 0,
    "exited": [],
    "more_exited": 0,
    "held": 0,
    "late": None,
    "pause": False,
    "told": {},
}


def names(symbols) -> tuple[al.Named, ...]:
    return tuple(al.Named(symbol, symbol, "NSE") for symbol in symbols)


def rows_of(raw: dict) -> dict[str, al.Told]:
    return {symbol: al.Told(*row) for symbol, row in raw.items()}


def check(decision: al.Decision, expect: dict, where: str) -> None:
    want = {**EMPTY, **expect}
    late = ms(DAY, want["late"]) if want["late"] else None
    got = {
        "tell": decision.tell,
        "why": decision.why,
        "matches": decision.matches,
        "entered": [item.symbol for item in decision.entered],
        "more_entered": decision.more_entered,
        "exited": [item.symbol for item in decision.exited],
        "more_exited": decision.more_exited,
        "held": decision.held,
        "late": decision.late_at,
        "pause": decision.pause,
        "told": {key: [row.session, row.count, row.last] for key, row in decision.told.items()},
    }
    want["late"] = late
    assert got == want, where
    for item in decision.entered + decision.exited:
        assert (item.key, item.exchange) == (item.symbol, "NSE"), where


def play(case: dict) -> None:
    policy = al.Policy.of(case["policy"])
    told = rows_of(case.get("told", {}))
    paused = case.get("paused", False)
    for number, step in enumerate(case["steps"], 1):
        where = f"{case['id']} step {number}"
        if step.get("resume"):
            paused = False
            continue
        facts = al.Facts(
            step.get("session", SESSION),
            step["k"],
            step["matches"],
            names(step["entered"]),
            names(step["exited"]),
            ms(DAY, step["late"]) if step.get("late") else None,
        )
        before = dict(told)
        decision = al.decide(policy, facts, told, paused=paused)
        assert told == before, f"{where}: the state was changed"
        check(decision, step["expect"], where)
        told.update(decision.told)
        paused = paused or decision.pause


# ---- the vectors file ------------------------------------------------------------------------------
def test_the_vectors_are_what_the_tests_expect():
    ids = [case["id"] for case in VECTORS["cases"]]
    assert len(ids) == len(set(ids)) >= 29
    assert {"A1", "A2", "A3", "A4", "A5", "A6"} <= set(ids), "the design's A1-A6"
    whys = {
        step["expect"]["why"]
        for case in VECTORS["cases"]
        for step in case["steps"]
        if "expect" in step
    }
    assert whys == {al.TOLD, al.OFF, al.PAUSED, al.FEW, al.NOTHING}, (
        "every reason is shown somewhere"
    )
    settings = {key for case in VECTORS["cases"] for key in case["policy"]}
    assert settings >= {"on", "min_matches", "unique_per_day", "skip_zero", "tell_exits"}
    assert len(VECTORS["policies"]) >= 7


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["id"] for c in VECTORS["cases"]])
def test_a_screen_over_a_day(case):
    play(case)


@pytest.mark.parametrize("case", VECTORS["policies"], ids=[c["id"] for c in VECTORS["policies"]])
def test_the_settings_a_screen_stores(case):
    if case["expect"] == "error":
        with pytest.raises(ValueError):
            al.Policy.of(case["raw"])
        return
    got = al.Policy.of(case["raw"])
    assert {name: getattr(got, name) for name in case["expect"]} == case["expect"]


# ---- from membership to the digest ------------------------------------------------------------------
def result(key: str, outcome: str, rank=None) -> mem.Result:
    return mem.Result(key, key, "NSE", outcome, rank=rank, price=100000)


def change(key: str, event: str, symbol: str = "", exchange: str = "NSE") -> mem.Change:
    return mem.Change(key, event, mem.Row(key, symbol or key, exchange, mem.ACTIVE))


def test_only_entries_and_exits_are_told():
    changes = [
        change("NSE_EQ|INE002A01018", mem.EV_ENTERED, "RELIANCE"),
        change("B", mem.EV_MISSING),
        change("C", mem.EV_BACK),
        change("NFO_FO|52618", mem.EV_EXITED, "NIFTY28OCT2625900CE", "NFO"),
        change("E", mem.EV_FORGOTTEN),
        change("NSE_EQ|INE062A01020", mem.EV_ENTERED, "SBIN"),
        change("G", mem.EV_STILL),
        change("NSE_EQ|INE009A01021", mem.EV_EXITED, "INFY"),
    ]
    entered, exited = al.named(changes)
    assert entered == (
        al.Named("NSE_EQ|INE002A01018", "RELIANCE", "NSE"),
        al.Named("NSE_EQ|INE062A01020", "SBIN", "NSE"),
    )
    assert exited == (
        al.Named("NFO_FO|52618", "NIFTY28OCT2625900CE", "NFO"),
        al.Named("NSE_EQ|INE009A01021", "INFY", "NSE"),
    )
    assert al.named([]) == ((), ())
    assert al.named(iter(changes[:1])) == (
        (al.Named("NSE_EQ|INE002A01018", "RELIANCE", "NSE"),),
        (),
    )


def test_a3_told_once_while_the_list_adds_it_again():
    """X enters 10:15, leaves 10:45 and enters again 11:30: told at 10:15 only, but listed again."""
    policy = al.Policy()
    grace = mem.Grace(mem.RUNS, 1)
    rows: dict[str, mem.Row] = {}
    told: dict[str, al.Told] = {}
    items: dict[str, bool] = {}
    story = [
        # (run, minutes into the session, results, who is told, what the list holds afterwards)
        (1, "10:15", [result("X", T.HIT, 5), result("Y", T.HIT, 4)], ["X", "Y"], {"X", "Y"}),
        (3, "10:45", [result("X", T.MISS), result("Y", T.HIT, 4)], [], {"Y"}),
        (6, "11:30", [result("X", T.HIT, 5), result("Y", T.HIT, 4)], [], {"X", "Y"}),
    ]
    for k, clock, results, expected, listed in story:
        run = mem.Run(k, ms(DAY, clock), SESSION)
        applied = mem.apply_run(
            rows, results, run, grace, items=items, max_adds=10, order="desc", free=250
        )
        rows.update(applied.rows)
        entered, exited = al.named(applied.changes)
        facts = al.Facts(SESSION, k, sum(r.outcome == T.HIT for r in results), entered, exited)
        decision = al.decide(policy, facts, told)
        told.update(decision.told)
        assert [item.symbol for item in decision.entered] == expected, f"run {k}"
        plan = mem.plan_sync(list(rows.values()), items)
        for key in plan.remove:
            del items[key]
        for pick in plan.add:
            items[pick.key] = True
        assert set(items) == listed, f"run {k}: the list"
    assert told["X"].count == 1 and told["Y"].count == 1


def test_an_entry_of_a_run_that_waits_for_room_is_not_told():
    """A match that could not be added is not an entry: nothing to tell yet."""
    rows: dict[str, mem.Row] = {}
    results = [result("A", T.HIT, 2), result("B", T.HIT, 1)]
    applied = mem.apply_run(
        rows, results, mem.Run(1, ms(DAY, "10:15"), SESSION), mem.Grace(),
        items={}, max_adds=1, order="desc", free=250,
    )  # fmt: skip
    entered, exited = al.named(applied.changes)
    decision = al.decide(al.Policy(), al.Facts(SESSION, 1, 2, entered, exited))
    assert [item.symbol for item in decision.entered] == ["A"]
    assert applied.waiting == ("B",)
    assert decision.held == 0 and decision.more_entered == 0
