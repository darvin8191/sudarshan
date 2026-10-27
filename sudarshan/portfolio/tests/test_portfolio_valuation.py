"""core/valuation.py (PA3): value, Today and a tab's totals, from integers.

Three witnesses, as for the ledger. The golden vectors (vectors/valuation.json) are worked out by
hand and by an evaluator in make_pa3.py that imports nothing from sudarshan; the phone runs the same
file. That evaluator is then run against the engine on random rows, and identities that must hold
whatever the numbers are close the gaps: every figure is a sum of figures, a price that rises never
lowers a value or a Today, the counts add up, and no float is ever written.
"""

from __future__ import annotations

import dataclasses
import json
import random
import re
from pathlib import Path

import pytest
from portfolio_ts import interfaces, members
from test_portfolio_vectors import load

from sudarshan.portfolio.core import kinds
from sudarshan.portfolio.core.valuation import (
    Fixed,
    Line,
    Row,
    Today,
    closed_today,
    previous_close_u,
    row_figures,
    row_today,
    tab_today,
    tab_totals,
    today_mode,
)

VECTORS = Path(__file__).parent / "vectors"
FILE = json.loads((VECTORS / "valuation.json").read_text(encoding="utf-8"))
TYPES_TS = Path(__file__).resolve().parents[3] / "frontend/src/sudarshan/portfolio/types.ts"
GEN = load("make_pa3.py")  # the independent evaluator
U = kinds.QTY_SCALE
TODAY = 20261027


def rows_of(rows):
    return [Row.of(row) for row in rows]


# -- the golden vectors ---------------------------------------------------------------------------


def test_the_file_has_what_the_design_needs():
    assert {"VA3", "VA5", "VA6", "VA7", "VA8r"} <= {case["id"] for case in FILE["rows"]}
    assert "VA8" in {case["id"] for case in FILE["tabs"]}
    assert {case["id"] for case in FILE["replays"]} == {"R1", "R2", "R3"}
    for section in ("rows", "tabs", "closed", "replays"):
        assert all(case["title"] and case["why"] for case in FILE[section]), section
    assert all(case["why"] for case in FILE["previous_close"] + FILE["modes"])


@pytest.mark.parametrize("case", FILE["rows"], ids=lambda case: case["id"])
def test_a_rows_figures_are_the_vectors(case):
    assert row_figures(Row.of(case["row"])).as_dict() == case["expect"]


@pytest.mark.parametrize("case", FILE["tabs"], ids=lambda case: case["id"])
def test_a_tabs_totals_are_the_vectors(case):
    got = tab_totals(rows_of(case["rows"]), Fixed.of(case["fixed"]))
    assert got.as_dict() == case["expect"]


@pytest.mark.parametrize("case", FILE["closed"], ids=lambda case: case["id"])
def test_the_holdings_sold_out_today_are_the_vectors(case):
    assert list(closed_today(rows_of(case["rows"]))) == case["expect"]


@pytest.mark.parametrize("case", FILE["previous_close"], ids=lambda case: case["id"])
def test_the_close_today_is_measured_from_is_the_vectors(case):
    got = previous_close_u(case["price_u"], case["prev_close_u"], case["price_day"], case["today"])
    assert got == case["expect"]


@pytest.mark.parametrize(
    "case", FILE["modes"], ids=lambda case: f"{case['asset_kind']}-{case['basis']}-{case['expect']}"
)
def test_what_today_means_for_a_holding_is_the_vectors(case):
    got = today_mode(case["asset_kind"], case["basis"], case["price_day"], case["today"])
    assert got == case["expect"]


def frames_of(replay):
    """Each frame with the rows as they stand once the ticks so far have arrived."""
    rows = json.loads(json.dumps(replay["rows"]))
    for frame in replay["frames"]:
        for tick in frame["set"]:
            target = rows[tick["row"]]
            for key in ("price_u", "prev_close_u"):
                if key in tick:
                    target["price"][key] = tick[key]
            if "today_mode" in tick:
                target["today_mode"] = tick["today_mode"]
        yield frame, rows


@pytest.mark.parametrize("replay", FILE["replays"], ids=lambda replay: replay["id"])
def test_every_frame_of_a_replay_is_the_vectors(replay):
    seen = 0
    for frame, rows in frames_of(replay):
        got = tab_totals(rows_of(rows), Fixed.of(replay["fixed"]))
        assert got.as_dict() == frame["expect"], frame["note"]
        seen += 1
    assert seen == len(replay["frames"]) >= 3


def test_a_tick_that_arrives_twice_changes_nothing():
    for replay in FILE["replays"]:
        for frame, rows in frames_of(replay):
            for tick in frame["set"]:  # the same ticks arriving a second time
                for key in ("price_u", "prev_close_u"):
                    if key in tick:
                        rows[tick["row"]]["price"][key] = tick[key]
            got = tab_totals(rows_of(rows), Fixed.of(replay["fixed"])).as_dict()
            assert got == frame["expect"], (replay["id"], frame["note"])


# -- the rules, one at a time ---------------------------------------------------------------------


def line(qty=0, opened=0, cost=0, inn=0, out=0, account=1, over=0):
    return Line(account, qty, opened, cost, inn, out, over)


def row(*lines, price=None, prev=None, mode="ok"):
    return Row(mode, price, prev, tuple(lines))


def test_a_value_is_rounded_half_away_from_zero_line_by_line():
    # 1 share at 0.0050 is half a paisa, which is a whole paisa; two lines are two, not one.
    one = row(line(U, U, 1), price=50, prev=50)
    assert row_figures(one).value_paise == 1
    two = row(line(U, U, 1), line(U, U, 1, account=2), price=50, prev=50)
    assert row_figures(two).value_paise == 2


def test_each_part_of_todays_figure_is_rounded_before_the_subtraction():
    # The open is worth 0.0050 (1 paisa) and now 0.0149 (1 paisa): the exact difference is 0.99
    # of a paisa, but the screen shows two whole-paisa values, so Today is their difference.
    figure = row_today(row(line(U, U, 1), price=149, prev=50))
    assert (figure.state, figure.paise, figure.base_paise) == ("ok", 0, 1)


def test_today_is_value_now_less_the_open_and_money_in_plus_money_out():
    # 10 at the open at 100.00; 5 bought for 520.00; 4 sold for 430.00; 11 now at 110.00.
    held = row(line(11 * U, 10 * U, 0, inn=52_000, out=43_000), price=1_100_000, prev=1_000_000)
    figure = row_today(held)
    assert figure.paise == 121_000 - (100_000 + 52_000) + 43_000 == 12_000
    assert figure.base_paise == 152_000
    assert figure.pct == "7.8947"


def test_today_needs_the_previous_close_only_for_units_held_at_the_open():
    bought_today = row(line(5 * U, 0, 500, inn=500), price=110_000, prev=None)
    assert row_today(bought_today).state == "ok"
    held_since = row(line(5 * U, 5 * U, 500), price=110_000, prev=None)
    assert row_today(held_since).state == "unknown"


def test_today_needs_the_price_only_for_units_held_now():
    # 5 shares at the open at 10.00 (5000 paise), all sold for 52.00 (5200): +200, no price needed.
    sold_out = row(line(0, 5 * U, 0, out=5_200), price=None, prev=100_000)
    figure = row_today(sold_out)
    assert (figure.state, figure.paise, figure.base_paise) == ("ok", 200, 5_000)
    still_held = row(line(U, 5 * U, 0, out=5_200), price=None, prev=100_000)
    assert row_today(still_held).state == "unknown"


def test_one_line_that_cannot_be_worked_out_leaves_the_whole_holding_unknown():
    mixed = row(line(U, U, 100), line(U, 0, 100, inn=100, account=2), price=110_000, prev=None)
    figure = row_today(mixed)
    assert (figure.state, figure.paise, figure.unknown, figure.counted) == ("unknown", None, 1, 0)


def test_a_holding_that_cannot_have_a_today_says_so_whatever_its_prices():
    assert row_today(row(line(U, U, 1), price=1, prev=1, mode="none")).state == "none"
    waiting = row_today(row(line(U, U, 1), price=1, prev=1, mode="pending"))
    assert (waiting.state, waiting.pending, waiting.counted) == ("pending", 1, 0)


def test_a_holding_with_no_price_is_counted_at_its_cost():
    figures = row_figures(row(line(10 * U, 10 * U, 102_000), price=None, prev=109_000))
    assert (figures.value_paise, figures.cost_paise, figures.unrealised_paise) == (
        102_000,
        102_000,
        0,
    )
    assert figures.priced is False and figures.gain_pct is None


def test_a_gain_on_nothing_paid_has_no_percentage():
    bonus = row_figures(row(line(10 * U, 10 * U, 0), price=1_000_000, prev=1_000_000))
    assert bonus.unrealised_paise == bonus.value_paise == 100_000
    assert bonus.gain_pct is None


def test_a_loss_is_written_with_a_plain_minus():
    figures = row_figures(row(line(10 * U, 10 * U, 100_000), price=900_000, prev=900_000))
    assert (figures.unrealised_paise, figures.gain_pct) == (-10_000, "-10.0000")


def test_a_tab_skips_a_holding_nobody_holds_and_counts_the_rest_once():
    sold_out = row(line(0, 10 * U, 0, out=105_000), price=None, prev=1_000_000)
    held = row(line(10 * U, 10 * U, 90_000), price=1_100_000, prev=1_000_000)
    both = tab_totals([sold_out, held], Fixed())
    assert both == tab_totals([held], Fixed())
    assert both.today.counted == 1


def test_the_tabs_overall_gain_and_return_follow_the_design():
    held = row(line(100 * U, 100 * U, 1_000_000), price=1_080_000, prev=1_080_000)
    fixed = Fixed(
        realised_paise=15_000,
        income_paise=2_500,
        fees_paise=1_000,
        money_in_paise=1_000_000,
        money_out_paise=120_000,
    )
    totals = tab_totals([held], fixed)
    assert totals.overall_gain_paise == 80_000 + 15_000 + 2_500 - 1_000
    # Fees count as money put in and money already taken out counts as returned:
    # (1080000 + 120000 - 1001000) / 1001000 = 199000 / 1001000 = 19.8801%.
    assert totals.absolute_return_pct == "19.8801"
    assert tab_totals([held], Fixed()).absolute_return_pct is None  # nothing put in


def test_a_tabs_states_are_ranked_ok_then_unknown_then_pending_then_none():
    ok = Today("ok", 100, 1000, "10.0000", counted=1)
    unknown = Today("unknown", None, 0, None, unknown=1)
    pending = Today("pending", None, 0, None, pending=1)
    none = Today("none", None, 0, None)
    nothing = Fixed()

    def state(*todays, fixed=nothing):
        return tab_today(todays, fixed).state

    assert state(ok, unknown, pending, none) == "ok"
    assert state(unknown, pending, none) == "unknown"
    assert state(pending, none) == "pending"
    assert state(none) == "none" and state() == "none"
    assert tab_today([ok, unknown, pending], nothing).as_dict() == {
        "state": "ok",
        "paise": 100,
        "base_paise": 1000,
        "pct": "10.0000",
        "counted": 1,
        "unknown": 1,
        "pending": 1,
    }
    cannot = Fixed(closed_today_paise=None)  # something sold today has no previous close
    assert state(pending, none, fixed=cannot) == "unknown"
    assert state(ok, fixed=cannot) == "ok"


def test_a_closed_figure_counts_and_is_not_a_listed_holding():
    closed = Fixed(closed_today_paise=5_000, closed_today_base_paise=100_000)
    figure = tab_today([Today("pending", None, 0, None, pending=1)], closed)
    assert (figure.state, figure.paise, figure.base_paise, figure.pct) == (
        "ok",
        5_000,
        100_000,
        "5.0000",
    )
    assert (figure.counted, figure.pending) == (0, 1)


@pytest.mark.parametrize("kind", kinds.ASSET_KINDS)
@pytest.mark.parametrize("basis", [None, *kinds.PRICE_BASES])
@pytest.mark.parametrize("price_day", [None, 20261024, 20261026, TODAY, 20261028])
def test_what_today_means_for_every_kind_of_holding(kind, basis, price_day):
    if kind == "listed":
        want = "ok"
    elif kind == "mf" and basis == "nav":
        want = "ok" if price_day == TODAY else "pending"
    else:
        want = "none"
    assert today_mode(kind, basis, price_day, TODAY) == want


@pytest.mark.parametrize("price", [None, 100])
@pytest.mark.parametrize("prev", [None, 90])
@pytest.mark.parametrize("price_day", [None, 20261024, 20261026, TODAY, 20261028])
def test_the_close_today_is_measured_from_for_every_combination(price, prev, price_day):
    earlier = price is not None and price_day is not None and price_day < TODAY
    assert previous_close_u(price, prev, price_day, TODAY) == (price if earlier else prev)


# -- the independent evaluator against the engine on random rows ------------------------------------


def random_price(rng):
    return int(10 ** rng.uniform(0, 8))  # from 0.0001 to 10,000.00 rupees


def random_line(rng, account):
    """A line whose numbers agree with each other: the open, today's trades, what is held now."""
    opened = rng.choice([0, rng.randint(1, 30) * U, rng.randint(1, 9_999_999)])
    bought = rng.choice([0, 0, rng.randint(1, 20) * U, rng.randint(1, 999_999)])
    if not opened and not bought:
        opened = rng.randint(1, 30) * U
    sold = rng.randint(0, opened + bought) if rng.random() < 0.4 else 0
    qty = opened + bought - sold
    return {
        "account_id": account,
        "qty_u": qty,
        "open_qty_u": opened,
        "cost_paise": rng.randint(0, 50_000_000) if qty else 0,
        "in_today_paise": rng.randint(1, 5_000_000) if bought else 0,
        "out_today_paise": rng.randint(1, 5_000_000) if sold else 0,
        "over_year_qty_u": rng.randint(0, qty) if qty and rng.random() < 0.3 else 0,
    }


def random_row(rng, mode=None, known=False, weights=(8, 1, 1)):
    price = random_price(rng)
    prev = max(1, int(price * rng.uniform(0.9, 1.1)))
    if not known:
        price = None if rng.random() < 0.12 else price
        prev = None if rng.random() < 0.12 else prev
    return {
        "today_mode": mode or rng.choices(["ok", "pending", "none"], weights)[0],
        "price": {"price_u": price, "prev_close_u": prev},
        "lines": [random_line(rng, a) for a in rng.sample([1, 2, 3], rng.randint(1, 3))],
    }


def random_fixed(rng):
    way = rng.random()
    closed = None if way < 0.15 else (0 if way < 0.45 else rng.randint(-500_000, 500_000))
    return {
        "realised_paise": rng.randint(-1_000_000, 1_000_000),
        "income_paise": rng.randint(0, 100_000),
        "fees_paise": rng.randint(0, 10_000),
        "money_in_paise": rng.randint(0, 100_000_000),
        "money_out_paise": rng.randint(0, 10_000_000),
        "closed_today_paise": closed,
        "closed_today_base_paise": rng.randint(1, 10**7) if closed else 0,
    }


def random_tab(rng, **more):
    return [random_row(rng, **more) for _ in range(rng.randint(0, 7))], random_fixed(rng)


def test_the_independent_evaluator_agrees_with_the_engine_on_random_tabs():
    rng = random.Random(20261028)
    states = {"ok": 0, "unknown": 0, "pending": 0, "none": 0}
    unpriced = closed_none = 0
    for _ in range(1500):
        rows, fixed = random_tab(
            rng, weights=rng.choice([(8, 1, 1), (1, 4, 4), (1, 1, 0), (0, 1, 1)])
        )
        got = tab_totals(rows_of(rows), Fixed.of(fixed))
        assert got.as_dict() == GEN.totals_of(rows, fixed)
        states[got.today.state] += 1
        unpriced += got.unpriced > 0
        closed_none += fixed["closed_today_paise"] is None
        for found in rows:
            assert row_figures(Row.of(found)).as_dict() == GEN.figures_of(found)
        assert list(closed_today(rows_of(rows))) == list(GEN.closed_of(rows))
    # The generator must exercise what it is there for, or the comparison proves nothing.
    assert all(count >= 40 for count in states.values()), states
    assert unpriced >= 300 and closed_none >= 100


# -- identities ---------------------------------------------------------------------------------------


def exact_value(row_dict):
    """The unrounded value of a row in tenths of a billionth of a rupee."""
    price = row_dict["price"]["price_u"]
    return sum(found["qty_u"] * price for found in row_dict["lines"])


def test_a_rows_value_is_within_half_a_paisa_a_line_of_the_exact_value():
    rng = random.Random(5)
    for _ in range(600):
        found = random_row(rng, known=True)
        shown = row_figures(Row.of(found)).value_paise
        assert abs(shown * 10**8 - exact_value(found)) <= len(found["lines"]) * 5 * 10**7


def test_a_tabs_figures_are_the_sums_of_its_rows_figures():
    rng = random.Random(6)
    for _ in range(300):
        rows, fixed = random_tab(rng)
        parts = [row_figures(Row.of(r)) for r in rows if any(ln["qty_u"] for ln in r["lines"])]
        totals = tab_totals(rows_of(rows), Fixed.of(fixed))
        assert totals.value_paise == sum(p.value_paise for p in parts)
        assert totals.cost_paise == sum(p.cost_paise for p in parts)
        assert totals.unrealised_paise == totals.value_paise - totals.cost_paise
        assert totals.unpriced == sum(not p.priced for p in parts)
        listed = [p.today for p in parts]
        assert totals.today.counted == sum(t.state == "ok" for t in listed)
        assert totals.today.unknown == sum(t.state == "unknown" for t in listed)
        assert totals.today.pending == sum(t.state == "pending" for t in listed)
        figure, shut = totals.today, fixed["closed_today_paise"]
        if figure.state == "ok":
            closed, closed_base = (
                (0, 0) if shut is None else (shut, fixed["closed_today_base_paise"])
            )
            assert figure.paise == sum(t.paise for t in listed if t.state == "ok") + closed
            assert (
                figure.base_paise
                == sum(t.base_paise for t in listed if t.state == "ok") + closed_base
            )
        else:
            assert (figure.paise, figure.base_paise, figure.pct) == (None, 0, None)


def test_the_order_of_rows_and_lines_changes_nothing():
    rng = random.Random(7)
    for _ in range(300):
        rows, fixed = random_tab(rng)
        want = tab_totals(rows_of(rows), Fixed.of(fixed)).as_dict()
        shuffled = [dict(r, lines=rng.sample(r["lines"], len(r["lines"]))) for r in rows]
        rng.shuffle(shuffled)
        assert tab_totals(rows_of(shuffled), Fixed.of(fixed)).as_dict() == want


def test_a_dearer_price_never_lowers_a_value_a_gain_or_a_today():
    rng = random.Random(9)
    checked = 0
    for _ in range(500):
        found = random_row(rng, mode="ok", known=True)
        lower = random_price(rng)
        higher = lower + int(10 ** rng.uniform(0, 6))
        figures = []
        for price in (lower, higher):
            found["price"]["price_u"] = price
            figures.append(row_figures(Row.of(found)))
        assert figures[1].value_paise >= figures[0].value_paise
        assert figures[1].unrealised_paise >= figures[0].unrealised_paise
        assert figures[1].today.paise >= figures[0].today.paise
        assert figures[1].today.base_paise == figures[0].today.base_paise
        checked += 1
    assert checked == 500


def test_a_price_that_has_not_moved_and_no_trades_make_today_nothing():
    rng = random.Random(10)
    for _ in range(300):
        found = random_row(rng, mode="ok", known=True)
        found["price"]["prev_close_u"] = found["price"]["price_u"]
        for each in found["lines"]:
            each.update(qty_u=each["open_qty_u"], in_today_paise=0, out_today_paise=0)
        figure = row_today(Row.of(found))
        assert (figure.state, figure.paise) == ("ok", 0)


def test_a_tabs_today_is_the_sum_of_every_line_sold_out_ones_included():
    rng = random.Random(11)
    for _ in range(300):
        rows = [random_row(rng, mode="ok", known=True) for _ in range(rng.randint(1, 6))]
        for found in rows:
            if rng.random() < 0.3:  # sold out today
                for each in found["lines"]:
                    each.update(qty_u=0, cost_paise=0, over_year_qty_u=0)
                    each["out_today_paise"] = rng.randint(1, 5_000_000)
        objects = rows_of(rows)
        closed, base = closed_today(objects)
        totals = tab_totals(objects, Fixed(closed_today_paise=closed, closed_today_base_paise=base))
        flat = flat_base = 0
        for found in rows:
            price, prev = found["price"]["price_u"], found["price"]["prev_close_u"]
            for each in found["lines"]:
                now = GEN.val(each["qty_u"], price) if each["qty_u"] else 0
                opened = GEN.val(each["open_qty_u"], prev) if each["open_qty_u"] else 0
                flat += now - (opened + each["in_today_paise"] - each["out_today_paise"])
                flat_base += opened + each["in_today_paise"]
        assert totals.today.unknown == totals.today.pending == 0
        if totals.today.state == "ok":
            assert (totals.today.paise, totals.today.base_paise) == (flat, flat_base)
        else:
            assert (flat, flat_base) == (0, 0)


def test_the_figures_hold_whole_numbers_and_texts_and_never_a_float():
    rng = random.Random(12)
    percent = re.compile(r"^-?\d+\.\d{4}$")

    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                yield from walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                yield from walk(value)
        else:
            yield node

    for _ in range(300):
        rows, fixed = random_tab(rng)
        totals = tab_totals(rows_of(rows), Fixed.of(fixed)).as_dict()
        parts = [row_figures(Row.of(r)).as_dict() for r in rows]
        for value in walk([totals, parts]):
            assert value is None or isinstance(value, (bool, int, str)), value
        for pct in (totals["absolute_return_pct"], totals["today"]["pct"]):
            assert pct is None or percent.match(pct), pct


def test_a_line_and_the_fixed_sums_read_back_what_they_were_sent():
    rng = random.Random(13)
    for _ in range(200):
        for each in random_row(rng)["lines"]:
            assert Line.of(each).as_dict() == each
        fixed = random_fixed(rng)
        read = Fixed.of(fixed)
        assert {each.name: getattr(read, each.name) for each in dataclasses.fields(Fixed)} == fixed
    for case in FILE["rows"]:
        for each in case["row"]["lines"]:
            assert Line.of(each).as_dict() == each


def test_the_units_held_over_a_year_may_be_left_out_of_a_line():
    sent = {k: v for k, v in FILE["rows"][0]["row"]["lines"][0].items() if k != "over_year_qty_u"}
    assert Line.of(sent).over_year_qty_u == 0


# -- the dicts carry the fields the phone declares ------------------------------------------------------


def test_the_figures_have_the_fields_the_phone_declares():
    declared = {
        name: set(members(body))
        for name, body in interfaces(TYPES_TS.read_text(encoding="utf-8")).items()
    }
    assert set(line().as_dict()) == declared["HoldingLine"]
    assert set(Today("none", None, 0, None).as_dict()) == declared["TodayFigure"]
    assert set(tab_totals([], Fixed()).as_dict()) == declared["TabTotals"]
    fixed = {each.name for each in dataclasses.fields(Fixed)}
    assert fixed == declared["TabFixed"] - {"liabilities_paise"}


def test_a_row_is_read_from_the_shape_the_phone_is_sent():
    declared = {
        name: set(members(body))
        for name, body in interfaces(TYPES_TS.read_text(encoding="utf-8")).items()
    }
    sent = FILE["rows"][0]["row"]
    assert set(sent["price"]) <= declared["PriceInfo"]
    assert "today_mode" in declared["HoldingRow"] and "lines" in declared["HoldingRow"]
    assert set(sent["lines"][0]) == declared["HoldingLine"]
