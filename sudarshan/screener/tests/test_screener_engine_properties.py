"""Properties of engine.py that hold for every input, checked over many random ones.

The vectors pin the answers a trader would notice; these pin the rules underneath them with an
oracle that does not share the engine's code: three-valued groups against every completion of the
unknown lines, a judgement at a boundary against the same judgement on the candles that had ended
by then, a pause against the answer it must never flip, the ordering of matches, and the words for
a number against decimal arithmetic.
"""

from __future__ import annotations

import itertools
import random
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction

import pytest
from screener_helpers import day_key, ms

from sudarshan.screener import engine, spec
from sudarshan.screener import types as T
from sudarshan.screener.types import (
    FALSE,
    TRUE,
    UNKNOWN,
    Candle,
    Clause,
    Group,
    Not,
    Outcome,
    Series,
    Why,
)

Y, TODAY = "2026-10-27", "2026-10-28"


# ---- three-valued groups against every completion ---------------------------------------------------
def random_tree(rng: random.Random, leaves: list, depth: int = 0):
    """A condition tree whose leaves are fresh lines (each appears once): read-once, three levels."""
    if depth >= 3 or (depth > 0 and rng.random() < 0.35):
        line = Clause(f"c{len(leaves) + 1}", None, "gt", None)
        leaves.append(line)
        return line
    kind = rng.choice(("all", "any", "none", "not"))
    if kind == "not":
        return Not(random_tree(rng, leaves, depth + 1))
    size = rng.randint(1, 4)
    return Group(kind, tuple(random_tree(rng, leaves, depth + 1) for _ in range(size)))


def boolean(node, value) -> bool:
    """The tree on plain booleans: ``value(line)`` is the line's truth."""
    if isinstance(node, Clause):
        return value(node)
    if isinstance(node, Not):
        return not boolean(node.item, value)
    parts = [boolean(item, value) for item in node.items]
    return {"all": all, "any": any, "none": lambda p: not any(p)}[node.kind](parts)


def reading(known: dict, chosen: dict):
    """A line's plain truth: the guess made for an unknown line, else what it is known to be."""
    return lambda line: chosen[line] if line in chosen else known[line] is TRUE


def forced(read, line, value: bool):
    """``read`` with one line held at ``value``."""
    return lambda item: value if item is line else read(item)


def guesses(lines: list):
    """Every way of settling ``lines`` as true or false."""
    for guess in itertools.product((False, True), repeat=len(lines)):
        yield dict(zip(lines, guess, strict=True))


def settled(node, known: dict) -> T.Tri:
    """The tree when each unknown line may be either: TRUE or FALSE only if every completion agrees."""
    open_ = [line for line, state in known.items() if state is UNKNOWN]
    seen = {boolean(node, reading(known, chosen)) for chosen in guesses(open_)}
    return UNKNOWN if len(seen) == 2 else (TRUE if seen == {True} else FALSE)


def pivotal(tree, known: dict, line) -> bool:
    """Some completion of the other unknown lines lets this one alone decide the whole tree."""
    others = [other for other, state in known.items() if state is UNKNOWN and other is not line]
    for chosen in guesses(others):
        read = reading(known, chosen)
        if boolean(tree, forced(read, line, True)) != boolean(tree, forced(read, line, False)):
            return True
    return False


def draw(rng: random.Random, leaves: list, weights=(3, 3, 2)) -> dict:
    return dict(
        zip(leaves, rng.choices((TRUE, FALSE, UNKNOWN), weights, k=len(leaves)), strict=True)
    )


def test_groups_say_what_every_completion_of_the_unknown_lines_agrees_on():
    rng, kinds = random.Random(101), set()
    for _ in range(1500):
        leaves: list = []
        tree = random_tree(rng, leaves)
        known = draw(rng, leaves)
        got = engine.fold(tree, known.__getitem__)
        assert got is settled(tree, known), (tree, known)
        kinds.add(got)
    assert kinds == {TRUE, FALSE, UNKNOWN}, "the sample should reach every answer"


def info_le(a: T.Tri, b: T.Tri) -> bool:
    """``a`` says no more than ``b``: unknown, or the same."""
    return a is UNKNOWN or a is b


def test_knowing_less_never_changes_an_answer_it_only_withholds_it():
    rng = random.Random(103)
    for _ in range(1500):
        leaves: list = []
        tree = random_tree(rng, leaves)
        known = draw(rng, leaves, (1, 1, 0))
        before = engine.fold(tree, known.__getitem__)
        less = {line: (UNKNOWN if rng.random() < 0.4 else state) for line, state in known.items()}
        less_still = {
            line: (UNKNOWN if rng.random() < 0.4 else state) for line, state in less.items()
        }
        middle, last = (engine.fold(tree, found.__getitem__) for found in (less, less_still))
        assert info_le(middle, before) and info_le(last, middle)


def test_the_reasons_given_are_those_of_the_lines_that_could_still_decide_the_answer():
    rng, seen_reasons = random.Random(107), 0
    for _ in range(1500):
        leaves: list = []
        tree = random_tree(rng, leaves)
        known = draw(rng, leaves)
        results = {
            line.id: engine.Result(state, Why(T.WARMING, (index, 0)) if state is UNKNOWN else None)
            for index, (line, state) in enumerate(known.items())
        }
        state, reasons = engine._walk(tree, results)
        assert state is settled(tree, known)
        if state is not UNKNOWN:
            assert reasons == []
            continue
        wanted = [
            results[line.id].why
            for line in leaves
            if known[line] is UNKNOWN and pivotal(tree, known, line)
        ]
        assert wanted and reasons == wanted
        seen_reasons += len(reasons)
    assert seen_reasons > 300


# ---- comparing numbers --------------------------------------------------------------------------------
def numbers(rng: random.Random):
    """Pairs that include exact ones, floats and floats a rounding apart."""
    while True:
        base = rng.choice((0, 1, 7, 100, 1000, 25000, 10**9, 10**12))
        kind = rng.random()
        if kind < 0.3:
            yield rng.randint(-50, 50), rng.randint(-50, 50)
        elif kind < 0.5:
            yield (
                Fraction(rng.randint(-60, 60), rng.randint(1, 9)),
                Fraction(rng.randint(-60, 60), rng.randint(1, 9)),
            )
        elif kind < 0.7:
            x = base + rng.random()
            yield x, x + rng.choice((0.0, 1e-13, -1e-13, 5e-7, -5e-7, 3e-6, -3e-6, 0.01, -0.01))
        elif kind < 0.85:
            x = float(base) * (1 + rng.choice((0.0, 1e-13, -1e-13, 1e-5, -1e-5)))
            yield x, base
        else:
            yield rng.randint(-50, 50), rng.uniform(-50, 50)


def test_two_numbers_are_greater_less_or_the_same_and_the_orders_agree():
    stream = numbers(random.Random(109))
    for _ in range(4000):
        a, b = next(stream)
        gt, lt, eq = (engine.compare(op, a, b) for op in ("gt", "lt", "eq"))
        assert [gt, lt, eq].count(True) == 1, (a, b)
        assert engine.compare("gte", a, b) is (gt or eq)
        assert engine.compare("lte", a, b) is (lt or eq)
        assert engine.compare("gt", b, a) is lt and engine.compare("eq", b, a) is eq


def test_exact_numbers_are_compared_exactly_however_large_they_are():
    big = 2**60
    assert engine.compare("gt", big + 1, big) and not engine.compare("eq", big + 1, big)
    assert engine.compare("lt", Fraction(big, 3), Fraction(big + 1, 3))
    rng = random.Random(113)
    for _ in range(2000):
        a, b = rng.randint(-big, big), rng.randint(-big, big)
        assert engine.compare("gt", a, b) is (a > b)
        assert engine.compare("lte", a, b) is (a <= b)
        x, y = Fraction(a, rng.randint(1, 10**6)), Fraction(b, rng.randint(1, 10**6))
        assert engine.compare("lt", x, y) is (x < y) and engine.compare("eq", x, y) is (x == y)


def test_floats_far_apart_compare_as_they_do_in_python():
    rng = random.Random(127)
    for _ in range(2000):
        x, y = rng.uniform(-1e6, 1e6), rng.uniform(-1e6, 1e6)
        if abs(x - y) > 1e-3:
            assert engine.compare("gt", x, y) is (x > y)
            assert engine.compare("lt", x, y) is (x < y)


# ---- the order of the matches -------------------------------------------------------------------------
def test_matches_come_out_in_one_order_whatever_order_they_went_in():
    rng = random.Random(131)
    for _ in range(600):
        count = rng.randint(0, 12)
        names = rng.sample("ABCDEFGHIJKLMNOP", count)
        pool = [None, 1, 2, 3, Fraction(5, 2), 2.5, Fraction(-1, 3), -0.3, 7]
        hits = [(name, Outcome(T.HIT, rank=rng.choice(pool))) for name in names]
        for order in ("desc", "asc"):
            got = engine.ranked(hits, order)
            shuffled = rng.sample(hits, len(hits))
            assert got == engine.ranked(shuffled, order)
            assert sorted(got) == sorted(names)
            known = [(Fraction(o.rank), s) for s, o in hits if o.rank is not None]
            known.sort(key=lambda item: ((-item[0] if order == "desc" else item[0]), item[1]))
            unknown = sorted(s for s, o in hits if o.rank is None)
            assert got == [s for _, s in known] + unknown


# ---- the words for a number ---------------------------------------------------------------------------
def decimal_text(value: Fraction, places: int) -> str:
    """``value`` rounded half away from zero to ``places``, by decimal arithmetic."""
    with localcontext() as ctx:
        ctx.prec = 80
        exact = Decimal(value.numerator) / Decimal(value.denominator)
        rounded = exact.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    if rounded == 0:
        rounded = abs(rounded)
    return f"{rounded:.{places}f}"


def trimmed(text: str) -> str:
    return text.rstrip("0").rstrip(".") if "." in text else text


def test_a_value_is_written_the_way_decimal_arithmetic_rounds_it():
    rng = random.Random(137)
    denominators = (1, 2, 3, 4, 5, 6, 7, 8, 10, 16, 20, 25, 40, 100, 125, 1000, 200, 800, 3200)
    for _ in range(4000):
        value = Fraction(rng.randint(-(10**7), 10**7), rng.choice(denominators))
        assert engine.show(value, T.PAISE) == decimal_text(value / 100, 2)
        assert engine.show(value, T.RUPEES) == decimal_text(value, 2)
        assert engine.show(value, T.PCT) == trimmed(decimal_text(value, 4))
        assert engine.show(value, T.NUM) == trimmed(decimal_text(value, 4))
        whole = value.denominator == 1
        assert engine.show(value, T.QTY) == (
            str(int(value)) if whole else trimmed(decimal_text(value, 2))
        )


# ---- judging a random instrument at random boundaries ---------------------------------------------------------
def walk(rng: random.Random, days=(Y, TODAY)) -> list[Candle]:
    """Two sessions of 15-minute candles on a random walk, in paise."""
    rows, last = [], 10000
    for day in days:
        begin = ms(day, "09:15")
        for slot in range(25):
            o, c = last, max(100, last + rng.randint(-60, 60))
            high, low = max(o, c) + rng.randint(0, 25), max(1, min(o, c) - rng.randint(0, 25))
            start = begin + slot * 15 * 60_000
            rows.append(
                Candle(
                    start,
                    start + 15 * 60_000,
                    day_key(day),
                    o,
                    high,
                    low,
                    c,
                    rng.randint(100, 9000),
                )
            )
            last = c
    return rows


CLOSE = {"field": "close", "tf": 15}


def f(name: str, at=None) -> dict:
    return {"field": name, "tf": 15, **({"at": at} if at is not None else {})}


def i(name: str, at=None, **params) -> dict:
    return {"ind": name, "p": params, "tf": 15, **({"at": at} if at is not None else {})}


TEMPLATES = [
    {"left": CLOSE, "op": "gt", "right": i("sma", length=5)},
    {"left": CLOSE, "op": "lte", "right": i("ema", length=5, source="close")},
    {"left": CLOSE, "op": "crosses_above", "right": i("ema", length=5)},
    {"left": CLOSE, "op": "crosses_below", "right": i("sma", length=3)},
    {"left": CLOSE, "op": "rising", "args": {"n": 2}},
    {"left": CLOSE, "op": "falling", "args": {"n": 3}},
    {"left": CLOSE, "op": "gt", "right": i("highest", {"ago": 1}, length=5, source="high")},
    {"left": CLOSE, "op": "lt", "right": i("lowest", {"ago": 1}, length=5, source="low")},
    {
        "left": f("volume"),
        "op": "gt",
        "right": {
            "math": "mul",
            "args": [{"num": "1.5"}, i("sma", {"ago": 1}, length=5, source="volume")],
        },
    },
    {"left": CLOSE, "op": "within_pct", "right": f("open"), "args": {"pct": "0.3"}},
    {"left": i("rsi", length=7), "op": "gt", "right": {"num": "50"}},
    {"left": CLOSE, "op": "gt", "right": f("close", "first_today")},
    {"left": CLOSE, "op": "lt", "right": f("close", "last_prev_day")},
    {"left": CLOSE, "op": "gt", "right": f("close", {"ago": 3})},
    {"left": f("change_pct"), "op": "gt", "right": {"num": "0.1"}},
    {"left": CLOSE, "op": "between", "args": {"low": "99", "high": "101"}},
    {"left": i("pattern"), "op": "is", "args": {"name": "doji"}},
    {"left": i("pattern"), "op": "is", "args": {"name": "inside_bar"}},
    {"left": i("pattern"), "op": "is", "args": {"name": "nr7"}},
    {"left": i("pattern"), "op": "is", "args": {"name": "bullish_engulfing"}},
    {"left": f("turnover"), "op": "gt", "right": {"num": "500000"}},
]


def random_where(rng: random.Random) -> dict:
    pick = [dict(rng.choice(TEMPLATES)) for _ in range(3)]
    for number, item in enumerate(pick, 1):
        item["id"] = f"c{number}"
    shape = rng.random()
    if shape < 0.35:
        return pick[0]
    if shape < 0.55:
        return {"all": pick[:2]}
    if shape < 0.75:
        return {"any": pick[:2]}
    if shape < 0.85:
        return {"not": pick[0]}
    return {"all": [pick[0], {"any": pick[1:]}]}


def plan_for(where: dict):
    screen = {
        "schema": 1, "name": "random", "universe": {"kind": "fno_stocks"}, "run": {"on": "close", "tf": 15},
        "where": where, "rank": {"by": f("close"), "order": "asc"}, "target": None, "alerts": {},
    }  # fmt: skip
    return spec.compile_plan(screen)


def judge_at(plan, rows: list[Candle], upto: int, flags: dict | None = None) -> Outcome:
    """Judge at the end of candle ``upto`` with ``rows`` as the candles the instrument holds."""
    held = [
        row._replace(flags=flags[k]) if flags and k in flags else row for k, row in enumerate(rows)
    ]
    subject = engine.Subject("R", {15: Series.of(15, held)}, cp=10000)
    return engine.judge(plan, subject, engine.Context(rows[upto].end, int(rows[upto].session)))


def test_a_judgement_never_reads_a_candle_that_ends_after_the_boundary():
    rng, kinds = random.Random(139), set()
    for _ in range(400):
        rows = walk(rng)
        upto = rng.randrange(len(rows))
        plan = plan_for(random_where(rng))
        full = judge_at(plan, rows, upto)
        cut = judge_at(plan, rows[: upto + 1], upto)
        assert full == cut, (upto, plan.root)
        kinds.add(full.kind)
        if full.kind == T.HIT:
            assert full.rank == rows[upto].c
        else:
            assert full.values is None and full.rank is None
        assert (full.kind == T.NOT_CHECKED) == bool(full.why)
        if full.kind == T.NOT_CHECKED:
            assert not (full.forming or full.repaired)
    assert kinds >= {T.HIT, T.MISS, T.NOT_CHECKED}


def test_a_pause_can_withhold_an_answer_but_never_turn_it_round():
    rng, withheld = random.Random(149), 0
    for _ in range(500):
        rows = walk(rng)
        upto = rng.randrange(len(rows))
        plan = plan_for(random_where(rng))
        paused = rng.randint(max(0, upto - 6), upto)
        before = judge_at(plan, rows, upto)
        after = judge_at(plan, rows, upto, {paused: T.UNVERIFIED})
        if before.kind in (T.HIT, T.MISS):
            assert after.kind in (before.kind, T.NOT_CHECKED), (before, after)
        else:
            assert after.kind == T.NOT_CHECKED, "a pause cannot settle what was not settled"
        if after.kind == T.NOT_CHECKED and before.kind != T.NOT_CHECKED:
            withheld += 1
            assert any(why.code == T.PAUSED for why in after.why)
        if after.kind == before.kind == T.HIT and paused != upto:
            assert after.rank == before.rank, "the rank reads the candle at the boundary only"
    assert withheld > 10, "the sample should pause something that mattered"


def test_a_shared_memo_changes_nothing_but_the_work():
    rng = random.Random(151)
    for _ in range(150):
        rows = walk(rng)
        upto = rng.randrange(len(rows))
        plan = plan_for(random_where(rng))
        subject = engine.Subject("R", {15: Series.of(15, rows)}, cp=10000)
        ctx = engine.Context(rows[upto].end, int(rows[upto].session))
        alone = engine.judge(plan, subject, ctx)
        memo: dict = {}
        assert engine.judge(plan, subject, ctx, memo) == alone
        assert engine.judge(plan, subject, ctx, memo) == alone


def test_every_template_is_a_screen_the_checker_accepts():
    assert len(TEMPLATES) >= 20
    for item in TEMPLATES:
        plan_for({**item, "id": "c1"})


@pytest.mark.parametrize("op", ["gt", "gte", "lt", "lte", "eq"])
def test_an_unknown_side_makes_a_comparison_unknown_whatever_the_operator(op):
    why = Why(T.WARMING, (9, 3))
    for a, b in ((why, 1), (1, why), (why, why)):
        state, found = engine._test(op, a, b)
        assert state is UNKNOWN and isinstance(found, Why)
