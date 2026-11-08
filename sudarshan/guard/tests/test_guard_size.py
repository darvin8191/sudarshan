"""The order size limit (M20.7): golden vectors for what an order adds and whether it is over
the owner's cap."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sudarshan.guard import rules, size

VECTORS = json.loads((Path(__file__).parent / "size_vectors.json").read_text())["cases"]


@pytest.mark.parametrize("case", VECTORS, ids=lambda case: case["name"])
def test_the_size_limit(case):
    side = rules.BUY if case["side"] == 1 else rules.SELL
    verdict = rules.classify(side, case["quantity"], case["position"])
    adding = size.adding(verdict, case["quantity"])
    assert adding == case["adding"]
    settings = SimpleNamespace(
        max_lots_fo=case["caps"]["lots"], max_qty_cash=case["caps"]["shares"]
    )
    excess = size.check(case["exchange"], adding, case["lot"], settings)
    if case["excess"] is None:
        assert excess is None
    else:
        assert excess == size.Excess(
            case["excess"]["unit"], case["excess"]["amount"], case["excess"]["cap"]
        )


def test_an_order_whose_exits_are_already_on_their_way_counts_whole():
    """p is 65 but an exit of 65 was sent: a sell of 130 opens 130 the other way."""
    verdict = rules.classify(rules.SELL, 130, 65, claimed=65)
    assert verdict.reason == rules.ON_ITS_WAY
    assert size.adding(verdict, 130) == 130


@pytest.mark.parametrize(
    ("exchange", "unit"),
    [("NFO", size.LOTS), ("BFO", size.LOTS), ("MCX", size.LOTS), ("CDS", size.LOTS),
     ("NSE", size.SHARES), ("BSE", size.SHARES), ("NSE_INDEX", None), ("", None)],
)  # fmt: skip
def test_each_exchange_takes_its_own_unit(exchange, unit):
    assert size.unit_of(exchange) == unit
