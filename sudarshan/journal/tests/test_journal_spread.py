"""The spread of Upstox's actual charges (P7 J2, M14.2): vectors C1 to C6 and the rules."""

from __future__ import annotations

import ast
import json
from fractions import Fraction
from pathlib import Path

import pytest

from sudarshan.journal.core import spread as S

DATA = json.loads((Path(__file__).parent / "vectors" / "spread.json").read_text(encoding="utf-8"))
VEC = {v["id"]: v for v in DATA["spread"]}


@pytest.mark.parametrize("vec", DATA["spread"], ids=[v["id"] for v in DATA["spread"]])
def test_spread_vector(vec):
    got = S.spread(vec["actual"], vec["orders"])
    assert {k: v["parts"] for k, v in got["orders"].items()} == vec["expect"]
    assert got["missing"] == vec["missing"] and got["partial"] is vec["partial"]
    for key, order in got["orders"].items():
        assert order["total"] == sum(order["parts"].values())
        if "totals" in vec:
            assert order["total"] == vec["totals"][key]
    # Whatever the weights, each part's shares are Upstox's figure exactly (mutation 13).
    for part, amount in vec["actual"].items():
        if amount is not None:
            assert sum(o["parts"][part] for o in got["orders"].values()) == amount, part


@pytest.mark.parametrize("vec", DATA["to_trips"], ids=[v["id"] for v in DATA["to_trips"]])
def test_order_to_trips_vector(vec):
    holders = {int(k): q for k, q in vec["holders"].items()}
    got = S.to_trips(vec["parts"], holders)
    assert got == {int(k): v for k, v in vec["expect"].items()}
    for part, amount in vec["parts"].items():
        assert sum(t[part] for t in got.values()) == amount


def test_the_vectors_are_what_their_maker_writes():
    import importlib.util

    path = Path(__file__).parent / "vectors" / "make_spread.py"
    spec = importlib.util.spec_from_file_location("make_spread", path)
    made = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(made)
    assert made.SPREAD == DATA["spread"] and made.TRIPS == DATA["to_trips"]
    assert "sudarshan" not in {  # the maker uses none of the code under test
        n.split(".")[0]
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for n in ([a.name for a in node.names] + [getattr(node, "module", None) or ""])
    }


def test_the_remainder_is_never_dropped():
    """Mutation 13: floors alone lose a paisa in C1 (15,319 of 15,320)."""
    got = S.largest_remainder(15320, [("A", 4000), ("B", 6000), ("C", 5000)])
    assert sum(got.values()) == 15320 and got["C"] == 5107
    for amount in range(0, 200):
        for weights in ([("a", 1), ("b", 1), ("c", 1)], [("a", 7), ("b", 0), ("c", 13)]):
            shares = S.largest_remainder(amount, weights)
            assert sum(shares.values()) == amount
            assert all(s >= 0 for s in shares.values())
            assert sum(S.largest_remainder(-amount, weights).values()) == -amount
    assert S.largest_remainder(100, [("a", 7), ("b", 0), ("c", 13)])["b"] == 0


def test_no_share_is_more_than_a_paisa_from_its_exact_part():
    weights = [("a", 3), ("b", 5), ("c", 11), ("d", 2)]
    for amount in (1, 17, 999, 15320):
        got = S.largest_remainder(amount, weights)
        for key, weight in weights:
            assert abs(got[key] - Fraction(amount * weight, 21)) < 1


def test_the_estimate_weighs_before_the_turnover():
    """Mutation 13: weights from turnover while estimates exist would give the big order most."""
    orders = [
        {"key": "A", "estimate": {"stt": 900}, "turnover": 100, "dp_group": None},
        {"key": "B", "estimate": {"stt": 100}, "turnover": 900_000, "dp_group": None},
    ]
    got = S.spread({"stt": 1000}, orders)["orders"]
    assert (got["A"]["parts"]["stt"], got["B"]["parts"]["stt"]) == (900, 100)


def test_an_empty_part_is_not_zero():
    """Mutation 13: a None read as 0 would put ``stt: 0`` on every order and call the day whole."""
    got = S.spread(VEC["C4"]["actual"], VEC["C4"]["orders"])
    assert all("stt" not in o["parts"] for o in got["orders"].values())
    zero = S.spread({**VEC["C4"]["actual"], "stt": 0, "clearing": 0}, VEC["C4"]["orders"])
    assert all(o["parts"]["stt"] == 0 for o in zero["orders"].values())
    assert zero["partial"] is False and zero["missing"] == []


def test_a_reversals_order_is_split_between_its_two_trips():
    """Mutation 13: the whole share to one trade would give 2,926 and 0."""
    got = S.to_trips({"stt": 2926}, {11: 65, 12: 65})
    assert got == {11: {"stt": 1463}, 12: {"stt": 1463}}


def test_what_cannot_be_shared_is_refused():
    with pytest.raises(ValueError):
        S.spread({"brokerage": 1}, [{"key": "A"}, {"key": "A"}])
    with pytest.raises(ValueError):
        S.spread({"tips": 1}, [{"key": "A"}])
    with pytest.raises(ValueError):
        S.largest_remainder(5, [])
    with pytest.raises(ValueError):
        S.largest_remainder(5, [("a", -1)])
    with pytest.raises(ValueError):
        S.to_trips({"stt": 5}, {1: 0})
    assert S.largest_remainder(0, []) == {}
    assert S.spread({"stt": None}, []) == {"orders": {}, "missing": ["stt"], "partial": True}


def test_the_spread_does_no_io():
    tree = ast.parse(Path(S.__file__).read_text(encoding="utf-8"))
    imported = {
        (node.module if isinstance(node, ast.ImportFrom) else a.name)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for a in node.names
    }
    assert imported <= {"__future__", "collections.abc", "fractions", "typing"}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not names & {"open", "print", "time", "logging", "datetime"}
