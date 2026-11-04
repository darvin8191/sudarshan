"""The pure rules: golden vectors from raw Upstox position rows, and the invariants (M5.13).

vectors.json carries raw rows in the shape Upstox's short-term positions book returns them and
the verdict for each order on them. A rule change that moves any verdict fails here first.
"""

from __future__ import annotations

import ast
import itertools
import json
from pathlib import Path

import pytest

from sudarshan.guard import rules

HERE = Path(__file__).resolve().parent
GOLDEN = json.loads((HERE / "vectors.json").read_text())
ROWS = GOLDEN["rows"]
VECTORS = GOLDEN["vectors"]


def _book(vector: dict) -> list[dict]:
    return [ROWS[name] for name in vector["book"]]


def _claimed(vector: dict, side: int | None) -> int:
    return sum(
        claim["quantity"] for claim in vector["claims"] if rules.side_of(claim["action"]) == side
    )


def _judge(vector: dict) -> tuple[rules.Verdict | None, dict | None]:
    order = vector["order"]
    product = rules.position_product("live", order["product"])
    position = rules.upstox_position(_book(vector), order["instrument_token"], product)
    kind = vector["kind"]
    if kind == "order":
        side = rules.side_of(order["action"])
        verdict = rules.classify(
            side,
            rules.quantity_of(order["quantity"]),
            position,
            _claimed(vector, side),
            cnc_sell=rules.is_cnc_sell(order["exchange"], order["product"], side),
        )
        return verdict, None
    if kind == "smart":
        sends = rules.smart_order(
            order["position_size"], position, rules.side_of(order["action"]), order["quantity"]
        )
        if sends is None:
            return None, None
        side, quantity = sends
        verdict = rules.classify(side, quantity, position, _claimed(vector, side))
        return verdict, {"action": "BUY" if side > 0 else "SELL", "quantity": quantity}
    if kind == "modify":
        working, new = order["working"], order["new"]
        side = rules.side_of(working["action"])
        terms = rules.OrderTerms(
            working["quantity"], working["pricetype"], working["price"], working["trigger_price"]
        )
        verdict = rules.classify_modify(
            rules.WorkingOrder(side, working["filled"], terms),
            rules.OrderTerms(new["quantity"], new["pricetype"], new["price"], new["trigger_price"]),
            position,
            _claimed(vector, side),
        )
        return verdict, None
    assert kind == "gtt", kind
    side = rules.side_of(order["action"])
    verdict = rules.classify_gtt(
        order["trigger_type"],
        side,
        rules.quantity_of(order["quantity"]),
        position,
        _claimed(vector, side),
        cnc_sell=rules.is_cnc_sell(order["exchange"], order["product"], side),
    )
    return verdict, None


@pytest.mark.parametrize("vector", VECTORS, ids=[vector["name"] for vector in VECTORS])
def test_golden_vector(vector):
    verdict, sends = _judge(vector)
    expect = vector["expect"]
    if expect is None:  # a smart order that sends nothing
        assert verdict is None and sends is None
        return
    if "sends" in expect:
        assert sends == expect["sends"]
    assert verdict.reducing is expect["reducing"]
    assert verdict.reason == expect["reason"]
    assert verdict.position == expect["position"]
    assert verdict.room == expect["room"]
    if "claimed" in expect:
        assert verdict.claimed == expect["claimed"]


def test_there_are_enough_vectors_of_every_kind():
    kinds = {
        kind: sum(v["kind"] == kind for v in VECTORS)
        for kind in ("order", "smart", "modify", "gtt")
    }
    assert len(VECTORS) >= 40 and all(kinds.values()), kinds
    reasons = {v["expect"]["reason"] for v in VECTORS if v["expect"]}
    assert reasons == {
        rules.EXIT,
        rules.CNC_SELL,
        rules.LESS_RISK,
        rules.OPENS,
        rules.ADDS,
        rules.CROSSES,
        rules.ON_ITS_WAY,
        rules.OCO,
        rules.UNKNOWN,
    }


@pytest.mark.parametrize("name", sorted(ROWS))
def test_the_raw_rows_hold_together(name):
    """Each row's figures are Upstox's own arithmetic, so the book is a realistic one."""
    row = ROWS[name]
    assert row["value"] == pytest.approx(row["sell_value"] - row["buy_value"])
    assert row["pnl"] == pytest.approx(
        row["value"] + row["quantity"] * row["last_price"] * row["multiplier"]
    )
    assert row["quantity"] == (
        row["overnight_quantity"] + row["day_buy_quantity"] - row["day_sell_quantity"]
    )
    assert row["product"] in ("I", "D")
    if row["overnight_quantity"] == 0:  # all today's: the day split adds up to the net
        assert row["realised"] + row["unrealised"] == pytest.approx(row["pnl"])


def test_a_row_about_the_position_that_cannot_be_read_makes_it_unknown():
    row = {**ROWS["nifty_ce_long_65_mis"], "quantity": "lots"}
    with pytest.raises(ValueError):
        rules.upstox_position([row], "NSE_FO|49213", "I")
    half = {**ROWS["nifty_ce_long_65_mis"], "quantity": 32.5}
    with pytest.raises(ValueError):
        rules.upstox_position([half], "NSE_FO|49213", "I")
    # A broken row about another position is not read.
    assert (
        rules.upstox_position([{**row, "instrument_token": "NSE_FO|1"}], "NSE_FO|49213", "I") == 0
    )
    assert rules.upstox_position(["not a row", None], "NSE_FO|49213", "I") == 0
    assert rules.upstox_position(None, "NSE_FO|49213", "I") == 0


def test_two_rows_for_one_position_are_added_up():
    """Upstox keeps one row per instrument and product; were it ever to split one, the net is
    the sum, never the last row read."""
    row = ROWS["nifty_ce_long_65_mis"]
    assert rules.upstox_position([row, row], "NSE_FO|49213", "I") == 130
    short = ROWS["nifty_ce_crossed_short_65_mis"]
    assert rules.upstox_position([row, short], "NSE_FO|49213", "I") == 0


@pytest.mark.parametrize(
    ("target", "position"), list(itertools.product(range(-130, 131, 65), range(-130, 131, 65)))
)
def test_a_smart_order_reduces_exactly_when_its_target_lies_between_0_and_p(target, position):
    """The design's equivalence, over every pair of whole lots: judging the order the broker
    sends is the same as asking whether T lies between 0 and p inclusive."""
    sends = rules.smart_order(target, position, rules.BUY, 65)
    if sends is None:
        assert target == position
        return
    verdict = rules.classify(*sends, position)
    between = position != 0 and (0 <= target <= position or position <= target <= 0)
    assert verdict.reducing is between, (target, position, sends)


@pytest.mark.parametrize(
    ("side", "quantity", "position", "claimed"),
    list(
        itertools.product((rules.BUY, rules.SELL), (1, 65, 130), (-130, -65, 0, 65, 130), (0, 65))
    ),
)
def test_an_admitted_exit_never_crosses_even_after_every_claim_fills(
    side, quantity, position, claimed
):
    """What makes it an exit: the position plus every exit sent and this one never crosses zero."""
    verdict = rules.classify(side, quantity, position, claimed)
    if verdict.reducing:
        after = position + side * (claimed + quantity)
        assert after == 0 or (after > 0) == (position > 0)
        assert quantity <= verdict.room


def test_an_exit_is_shown_once_the_book_has_caught_up():
    expect = rules.exit_expectation(130, 0, 65)
    assert expect == 65
    assert not rules.exit_shown(130, 130, expect)  # the book lags
    assert rules.exit_shown(65, 130, expect)
    assert rules.exit_shown(0, 130, expect)
    assert rules.exit_shown(-65, 130, expect)  # crossed: it has filled
    assert rules.exit_shown(-130, 130, expect)  # crossed further than the exit: still shown
    assert rules.exit_shown(-65, -130, rules.exit_expectation(-130, 0, 65))
    second = rules.exit_expectation(130, 65, 65)  # claimed after the first
    assert second == 0 and not rules.exit_shown(65, 130, second)
    assert rules.exit_shown(0, 130, second)
    assert rules.exit_expectation(65, 100, 65) == 0


@pytest.mark.parametrize(
    ("value", "quantity"),
    [(65, 65), ("65", 65), (" 130 ", 130), ("65.0", 65), (65.0, 65), (0, None), ("-65", None),
     ("32.5", None), ("", None), (None, None), ("abc", None), ("nan", None), ("inf", None)],
)  # fmt: skip
def test_quantity_of(value, quantity):
    assert rules.quantity_of(value) == quantity


def test_sides_and_products():
    assert (rules.side_of("buy"), rules.side_of(" SELL "), rules.side_of("hold")) == (1, -1, None)
    assert [rules.position_product("live", p) for p in ("MIS", "NRML", "CNC", "mis", "MTF")] == [
        "I", "D", "D", "I", "I"
    ]  # fmt: skip
    assert rules.position_product("sandbox", " nrml ") == "NRML"
    assert rules.is_cnc_sell("NSE", "CNC", rules.SELL) and rules.is_cnc_sell("bse", "cnc", -1)
    assert not rules.is_cnc_sell("NFO", "CNC", rules.SELL)  # NRML at Upstox
    assert not rules.is_cnc_sell("NSE", "CNC", rules.BUY)
    assert not rules.is_cnc_sell("NSE", "MIS", rules.SELL)


def test_the_rules_are_pure():
    """No I/O, no clock, no logging: only the standard library's dataclasses."""
    tree = ast.parse((HERE.parent / "rules.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"open", "print", "input", "exec", "eval"}
    assert imported <= {"__future__", "dataclasses"}, imported
