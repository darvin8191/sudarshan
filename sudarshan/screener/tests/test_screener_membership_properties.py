"""Properties of membership.py over random runs, restated from the rules rather than from the code.

The oracle for one instrument looks at the whole history at once (the last hit, the judged misses
after it) where the code moves a row one run at a time. The list properties hold for any mix of
screens, owner actions and syncs: only the screener's own items are removed, nothing wanted is
missing from the list, and a second sync after the first has nothing left to do.
"""

from __future__ import annotations

import random
from fractions import Fraction

import pytest

from sudarshan.screener import membership as mem
from sudarshan.screener.types import HIT, MISS, NOT_CHECKED, NOT_DUE

FIRST_DAY = 20261026
OUTCOMES = (HIT, HIT, HIT, HIT, MISS, MISS, MISS, MISS, NOT_CHECKED, NOT_DUE)


def run_of(n: int, session: int, last: bool = False, version: int = 1) -> mem.Run:
    return mem.Run(n, n * 60_000, session, version, last)


def one(key: str, outcome: str, rank=None, price=None) -> mem.Result:
    return mem.Result(key, key, "NSE", outcome, rank, price)


def histories(rng: random.Random):
    """(outcome, session, last) triples: runs of a few days, some of them the last of their day."""
    session, out = FIRST_DAY, []
    for _ in range(rng.randint(1, 14)):
        if rng.random() < 0.25:
            session += 1
        out.append((rng.choice(OUTCOMES), session, rng.random() < 0.2))
    return out


# ---- one instrument, restated ----------------------------------------------------------------------------
def standing(history, grace: mem.Grace):
    """(status, hits, misses) after a history, or None before the first hit, from the rules in one pass."""
    decided = [step for step in history if step[0] in (HIT, MISS)]
    hit_at = [i for i, step in enumerate(decided) if step[0] == HIT]
    if not hit_at:
        return None
    since = decided[hit_at[-1] + 1 :]  # the judged misses after the last hit
    streak = 0
    for step in reversed(decided[: hit_at[-1] + 1]):
        if step[0] != HIT:
            break
        streak += 1
    if not since:
        return mem.ACTIVE, streak, 0
    first_session = since[0][1]
    for index, (_, session, last) in enumerate(since, 1):
        if grace.mode == mem.RUNS and index >= grace.n:
            return mem.EXITED, 0, index
        if grace.mode == mem.DAY_END and (last or session != first_session):
            return mem.EXITED, 0, index
    return mem.MISSING, 0, len(since)


def expected_event(before, after, outcome: str):
    if outcome not in (HIT, MISS):
        return None
    was = before[0] if before else None
    if outcome == HIT:
        if was in (None, mem.EXITED):
            return mem.EV_ENTERED
        return mem.EV_BACK if was == mem.MISSING else None
    if was in (None, mem.EXITED):
        return None
    return mem.EV_EXITED if after[0] == mem.EXITED else mem.EV_MISSING


GRACES = [mem.Grace(mem.RUNS, n) for n in (1, 2, 3, 5)] + [
    mem.Grace(mem.DAY_END),
    mem.Grace(mem.NEVER),
]


@pytest.mark.parametrize("grace", GRACES, ids=[f"{g.mode}-{g.n}" for g in GRACES])
def test_one_instrument_follows_its_history(grace):
    rng = random.Random(f"history-{grace.mode}-{grace.n}")
    for _ in range(400):
        history, rows = histories(rng), {}
        for number, (outcome, session, last) in enumerate(history, 1):
            before = standing(history[: number - 1], grace)
            got = mem.apply_run(
                rows, [one("K", outcome, 5, 100)], run_of(number, session, last), grace,
                items={}, max_adds=10, order="desc", free=250,
            )  # fmt: skip
            rows.update(got.rows)
            after = standing(history[:number], grace)
            event = [change.event for change in got.changes]
            assert event == ([expected_event(before, after, outcome)] if expected_event(before, after, outcome) else []), (
                history, number
            )  # fmt: skip
            row = rows.get("K")
            assert (row.status, row.hits, row.misses) == after if after else row is None, (
                history,
                number,
            )


# ---- gaps and pins ---------------------------------------------------------------------------------------
def random_world(rng: random.Random, grace: mem.Grace, keys, runs: int = 8):
    """Rows after some random judged runs."""
    rows = {}
    session = FIRST_DAY
    for number in range(1, runs + 1):
        if rng.random() < 0.3:
            session += 1
        results = [one(key, rng.choice(OUTCOMES), rng.choice([None, 1, 2, 3]), rng.choice([None, 100])) for key in keys
                   if rng.random() < 0.8]  # fmt: skip
        got = mem.apply_run(
            rows, results, run_of(number, session, rng.random() < 0.2), grace,
            items={}, max_adds=rng.choice([1, 3, 10]), order=rng.choice(["asc", "desc"]), free=250,
        )  # fmt: skip
        rows.update(got.rows)
    return rows, session


def test_a_gap_changes_nothing_at_all():
    rng = random.Random(11)
    keys = [f"K{n}" for n in range(8)]
    for _ in range(300):
        grace = rng.choice(GRACES)
        rows, session = random_world(rng, grace, keys)
        gaps = [one(key, rng.choice([NOT_CHECKED, NOT_DUE]), 9, 999) for key in keys]
        got = mem.apply_run(
            rows, gaps, run_of(99, session + rng.choice([0, 1]), rng.random() < 0.5, 7), grace,
            items={}, max_adds=10, order="desc", free=250,
        )  # fmt: skip
        assert (
            dict(got.rows) == {} and got.changes == () and got.waiting == () and got.blocked == ""
        )


def test_a_pinned_row_never_leaves():
    rng = random.Random(12)
    for _ in range(300):
        grace, rows, session = rng.choice(GRACES), {}, FIRST_DAY
        for number in range(1, 25):
            if rng.random() < 0.3:
                session += 1
            pinned_before = "K" in rows and rows["K"].pinned
            got = mem.apply_run(
                rows, [one("K", rng.choice(OUTCOMES), 5, 100)], run_of(number, session, rng.random() < 0.3), grace,
                items={}, max_adds=10, order="desc", free=250,
            )  # fmt: skip
            rows.update(got.rows)
            if pinned_before:
                assert rows["K"].pinned and rows["K"].status in (mem.ACTIVE, mem.MISSING)
                assert all(change.event != mem.EV_EXITED for change in got.changes)
            if "K" in rows and rng.random() < 0.3:
                pinned = mem.pin(rows["K"])
                if pinned is not None:
                    rows["K"] = pinned


def test_unpinning_gives_the_grace_back_from_the_misses_already_counted():
    for grace in (mem.Grace(mem.RUNS, 2), mem.Grace(mem.RUNS, 4)):
        rows = {}
        for number, outcome in enumerate([HIT, MISS, MISS, MISS], 1):
            if number == 2:
                rows["K"] = mem.pin(rows["K"])
            rows.update(mem.apply_run(rows, [one("K", outcome)], run_of(number, FIRST_DAY), grace,
                                      items={}, max_adds=1, order="desc", free=1).rows)  # fmt: skip
        assert rows["K"].misses == 3 and rows["K"].status == mem.MISSING
        rows["K"] = mem.unpin(rows["K"])
        got = mem.apply_run(rows, [one("K", MISS)], run_of(5, FIRST_DAY), grace, items={}, max_adds=1,
                            order="desc", free=1)  # fmt: skip
        assert [change.event for change in got.changes] == [mem.EV_EXITED]


# ---- admission -------------------------------------------------------------------------------------------
def best_first(result: mem.Result, order: str):
    if result.rank is None:
        return (1, 0)
    return (0, -Fraction(result.rank) if order == "desc" else Fraction(result.rank))


def test_a_run_admits_the_best_within_the_limit_and_the_room():
    rng = random.Random(21)
    for _ in range(500):
        keys = [f"K{n:02d}" for n in range(rng.randint(1, 14))]
        order, max_adds, free = rng.choice(["asc", "desc"]), rng.randint(1, 12), rng.randint(0, 14)
        items = {key: rng.random() < 0.5 for key in keys if rng.random() < 0.25}
        rows = {}
        for key in keys:
            if rng.random() < 0.3:
                rows[key] = mem.Row(key, key, "NSE", rng.choice(mem.STATUSES), in_list=key in items)
        results = [one(key, rng.choice([HIT, HIT, HIT, MISS, NOT_CHECKED]), rng.choice([None, 1, 2, 2, 3.5, -1, 0]), 100)
                   for key in keys]  # fmt: skip
        got = mem.apply_run(rows, results, run_of(1, FIRST_DAY), mem.Grace(), items=items, max_adds=max_adds,
                            order=order, free=free)  # fmt: skip
        by_key = {result.key: result for result in results}
        candidates = [
            r
            for r in results
            if r.outcome == HIT and (r.key not in rows or rows[r.key].status == mem.EXITED)
        ]
        entered = [c.key for c in got.changes if c.event == mem.EV_ENTERED]
        # every entry and every waiting match is a candidate, once, and together they are all of them
        assert sorted(entered + list(got.waiting)) == sorted(r.key for r in candidates)
        new = [key for key in entered if key not in items]
        assert len(new) <= min(max_adds, free)
        for key in items:  # an instrument already in the list costs the list nothing
            if key in {r.key for r in candidates}:
                assert key in entered
        # the new instruments admitted are the best of the new ones (ties are decided by symbol, below)
        waiting_new = [by_key[key] for key in got.waiting]
        assert all(key not in items for key in got.waiting)
        for taken in new:
            for left in waiting_new:
                assert (best_first(by_key[taken], order), taken) < (
                    best_first(left, order),
                    left.key,
                )
        assert len(new) == min(max_adds, free, len(new) + len(got.waiting))
        # why they wait
        assert got.blocked == (
            "" if not got.waiting else mem.FULL if free < max_adds else mem.LIMIT
        )
        # nothing happens to an instrument the run could not judge, and a key is told once
        told = [c.key for c in got.changes]
        assert len(told) == len(set(told))
        for result in results:
            if result.outcome in (NOT_CHECKED, NOT_DUE):
                assert result.key not in got.rows


def test_admission_does_not_depend_on_the_order_the_results_arrive_in():
    rng = random.Random(22)
    for _ in range(200):
        keys = [f"K{n:02d}" for n in range(rng.randint(2, 12))]
        results = [  # a few symbols on several keys (one name on two exchanges): the key breaks the ties
            mem.Result(
                key,
                rng.choice(["AAA", "BBB", "CCC"]),
                "NSE",
                rng.choice([HIT, HIT, MISS]),
                rng.choice([None, 1, 2, 2, 3]),
                100,
            )
            for key in keys
        ]
        rows = {
            key: mem.Row(key, key, "NSE", mem.ACTIVE, hits=1) for key in keys if rng.random() < 0.3
        }
        args = {
            "items": {},
            "max_adds": rng.randint(1, 5),
            "order": rng.choice(["asc", "desc"]),
            "free": 250,
        }
        first = mem.apply_run(rows, results, run_of(1, FIRST_DAY), mem.Grace(), **args)
        shuffled = results[:]
        rng.shuffle(shuffled)
        second = mem.apply_run(rows, shuffled, run_of(1, FIRST_DAY), mem.Grace(), **args)
        assert dict(first.rows) == dict(second.rows)
        assert [c for c in first.changes if c.event == mem.EV_ENTERED] == [
            c for c in second.changes if c.event == mem.EV_ENTERED
        ]
        assert first.waiting == second.waiting and first.blocked == second.blocked


# ---- the list ---------------------------------------------------------------------------------------------
def random_rows(rng: random.Random, keys, per_key=(0, 1, 1, 2)):
    """Rows for a list: several screens may each hold a row for the same key."""
    rows = []
    for key in keys:
        for _ in range(rng.choice(per_key)):
            status = rng.choice(mem.STATUSES)
            rows.append(
                mem.Row(
                    key,
                    key,
                    "NSE",
                    status,
                    pinned=status in (mem.ACTIVE, mem.MISSING) and rng.random() < 0.2,
                    in_list=rng.random() < 0.5,
                    first_seen_at=rng.randint(0, 5),
                )  # fmt: skip
            )
    return rows


def test_a_plan_keeps_the_owners_items_and_what_is_wanted():
    rng = random.Random(31)
    keys = [f"K{n:02d}" for n in range(12)]
    for _ in range(600):
        rows = random_rows(rng, keys)
        items = {key: rng.random() < 0.5 for key in keys if rng.random() < 0.5}
        capacity = rng.randint(1, 14)
        plan = mem.plan_sync(rows, items, capacity)
        wanted = {row.key for row in rows if mem.wants(row)}
        assert all(items.get(key) is True and key not in wanted for key in plan.remove), (
            "only unwanted screener items"
        )
        assert {key for key, ours in items.items() if ours and key not in wanted} == set(
            plan.remove
        )
        added = [row.key for row in plan.add]
        assert (
            len(added) == len(set(added)) and set(added) <= wanted and not set(added) & set(items)
        )
        assert set(added) | set(plan.held) == wanted - set(items)
        assert not set(added) & set(plan.held)
        size = len(items) - len(plan.remove) + len(added)
        assert size <= max(capacity, len(items) - len(plan.remove)), (
            "the plan never overfills the list"
        )
        if plan.held:
            assert size >= capacity, "something waits only when the list is full"
        shuffled = rows[:]
        rng.shuffle(shuffled)
        again = mem.plan_sync(shuffled, items, capacity)
        assert (again.add, again.remove, again.held) == (plan.add, plan.remove, plan.held)


def test_reading_the_list_twice_changes_nothing_the_second_time():
    rng = random.Random(32)
    keys = [f"K{n:02d}" for n in range(10)]
    for _ in range(400):
        rows = random_rows(rng, keys, per_key=(0, 1))  # one screen: one row for a key
        items = {key: rng.random() < 0.5 for key in keys if rng.random() < 0.5}
        moved = mem.reconcile(rows, items)
        now = [moved.get(row.key, row) for row in rows]
        assert mem.reconcile(now, items) == {}
        assert all(row.in_list == (row.key in items) for row in now), (
            "the rows know what the list holds"
        )
        for before, after in zip(rows, now, strict=True):
            if (
                before.key not in items
                and before.in_list
                and before.status in (mem.ACTIVE, mem.MISSING)
            ):
                assert after.status == mem.DISMISSED and not after.pinned
            elif before.key in items:
                assert after.added_by_screener == items[before.key]


def play_days(rng: random.Random, screens: int):
    """Runs, hand actions and syncs in random order on one list; the properties are checked after each sync."""
    keys = [f"K{n}" for n in range(6)]
    world: list[dict[str, mem.Row]] = [{} for _ in range(screens)]
    items: dict[str, bool] = {}
    graces = [rng.choice(GRACES) for _ in range(screens)]
    session = FIRST_DAY
    for number in range(1, 40):
        action = rng.random()
        if action < 0.45:
            index = rng.randrange(screens)
            if rng.random() < 0.2:
                session += 1
            results = [
                one(key, rng.choice(OUTCOMES), rng.choice([None, 1, 2, 3]), 100)
                for key in keys
                if rng.random() < 0.7
            ]
            every = [row for rows in world for row in rows.values()]
            got = mem.apply_run(world[index], results, run_of(number, session, rng.random() < 0.2), graces[index],
                                items=items, max_adds=rng.choice([1, 2, 10]), order="desc",
                                free=mem.free_slots(every, items))  # fmt: skip
            world[index].update(got.rows)
        elif action < 0.55:
            if items:
                del items[rng.choice(sorted(items))]
        elif action < 0.65:
            items.setdefault(rng.choice(keys), False)
        elif action < 0.75:
            rows = world[rng.randrange(screens)]
            if rows:
                key = rng.choice(sorted(rows))
                change = rng.choice([mem.pin, mem.unpin, mem.dismiss, mem.restore])(rows[key])
                if change is not None:
                    rows[key] = change
        else:
            for rows in world:
                rows.update(mem.reconcile(list(rows.values()), items))
            every = [row for rows in world for row in rows.values()]
            plan = mem.plan_sync(every, items)
            assert not plan.held, "six keys never fill a list of 250"
            for key in plan.remove:
                del items[key]
            for row in plan.add:
                items[row.key] = True
            for rows in world:
                rows.update(mem.reconcile(list(rows.values()), items))
            every = [row for rows in world for row in rows.values()]
            wanted = {row.key for row in every if mem.wants(row)}
            assert wanted <= set(items), "everything wanted is in the list"
            assert all(key in wanted for key, ours in items.items() if ours), (
                "no screener item stays unwanted"
            )
            assert all(row.in_list == (row.key in items) for row in every), (
                "rows know what the list holds"
            )
            again = mem.plan_sync(every, items)
            assert not again.add and not again.remove, "a second sync has nothing to do"


@pytest.mark.parametrize("screens", [1, 2, 3])
def test_the_list_follows_the_rows(screens):
    rng = random.Random(f"days-{screens}")
    for _ in range(150):
        play_days(rng, screens)


def test_a_rank_that_is_not_a_number_ranks_last_and_never_stops_a_run():
    results = [
        one("A", HIT, float("nan")),
        one("B", HIT, 3),
        one("C", HIT, float("inf")),
        one("D", HIT, 1),
    ]
    got = mem.apply_run(
        {}, results, run_of(1, FIRST_DAY), mem.Grace(), items={}, max_adds=2, order="desc", free=250
    )
    assert [change.key for change in got.changes] == ["B", "D"]
    assert got.waiting == ("A", "C")
