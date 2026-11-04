"""What the positions book does not show yet, from the order book's fills (M6.9, N2 and N4).

The golden vectors in fill_vectors.json are raw Upstox rows (a short-term positions row and the
day's order book rows) with the figures sudarshan.books.fills must work out from them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sudarshan.books import fills

VECTORS = json.loads((Path(__file__).parent / "fill_vectors.json").read_text())["cases"]


@pytest.mark.parametrize("case", VECTORS, ids=[case["name"] for case in VECTORS])
def test_golden_vectors(case):
    orders = case["orders"]
    assert (fills.unseen_net(case["position"], orders) if orders is not None else None) == case[
        "unseen"
    ]
    assert fills.held_now(case["position"], orders) == case["held"]


def test_no_clock_is_involved(monkeypatch):
    """The figure depends on the rows alone, however long the positions book has lagged."""
    import time

    case = VECTORS[1]
    monkeypatch.setattr(time, "monotonic", lambda: 1e9)
    assert fills.held_now(case["position"], case["orders"]) == case["held"]


def test_the_module_adds_no_field_to_what_the_api_returns():
    """It reads Upstox's raw rows inside the broker module and returns plain numbers."""
    case = VECTORS[1]
    before = json.dumps(case, sort_keys=True)
    fills.held_now(case["position"], case["orders"])
    assert json.dumps(case, sort_keys=True) == before
