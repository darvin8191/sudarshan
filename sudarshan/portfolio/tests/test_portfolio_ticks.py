"""A tab moved by ticks, frame by frame: the Python core on the sequence the phone also runs (PD2).

vectors/valuation_ticks.json is written by make_pd2.py (independent, exact fractions). Here
core/valuation.py prices the same rows with the same rule and must give the same totals, so the
file the phone's liveTab.ts is held to is also the engine's.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sudarshan.portfolio.core.money import feed_price_u
from sudarshan.portfolio.core.valuation import Fixed, Row, tab_totals

FILE = json.loads((Path(__file__).parent / "vectors" / "valuation_ticks.json").read_text("utf-8"))


def moves(row, tick, read_at, allowed):
    price = feed_price_u(tick["ltp"]) if tick else None
    return bool(allowed and tick and tick["live"] and tick["at"] > read_at and price is not None)


@pytest.mark.parametrize("case", FILE["replays"], ids=lambda case: case["id"])
def test_the_engine_prices_every_frame_as_the_vectors_do(case):
    listed = [
        n
        for n, r in enumerate(case["rows"])
        if r["asset"]["kind"] == "listed" and any(ln["qty_u"] for ln in r["lines"])
    ]
    allowed = set(listed[: case["cap"]])
    newest = {}
    for frame in case["frames"]:
        for tick in frame["ticks"]:
            if feed_price_u(tick["ltp"]) is not None:
                newest[tick["key"]] = tick
        rows, moved = [], []
        for n, source in enumerate(case["rows"]):
            asset = source["asset"]
            tick = newest.get(f"{asset['exchange']}:{asset['symbol']}")
            price = dict(source["price"])
            ok = moves(source, tick, case["read_at"], n in allowed)
            if ok:
                price["price_u"] = feed_price_u(tick["ltp"])
                prev = feed_price_u(tick["prev_close"])
                if prev is not None:
                    price["prev_close_u"] = prev
            rows.append(Row.of({**source, "price": price}))
            moved.append(ok)
        got = tab_totals(rows, Fixed.of(case["fixed"])).as_dict()
        want = frame["expect"]
        assert got == want["totals"], frame["note"]
        assert moved == want["moved"], frame["note"]


def test_a_half_paisa_rounds_to_even_like_the_feed_does():
    assert feed_price_u(10.125) == 101_200  # 1012.5 paise: half to even, not 1013
