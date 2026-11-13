"""MAE and MFE from a price path (JA4): vectors X1-X6 and the candle form."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sudarshan.journal.core import excursion, kinds

DATA = json.loads(
    (Path(__file__).parent / "vectors" / "excursion.json").read_text(encoding="utf-8")
)


def run(v):
    return excursion.excursion(
        v["side"],
        v["entry_avg_u"],
        v["qty"],
        [tuple(p) for p in v["path"]],
        v["start_ms"],
        v["end_ms"],
        v["net_paise"],
        v["source"],
    )


@pytest.mark.parametrize("vec", DATA["vectors"], ids=[v["id"] for v in DATA["vectors"]])
def test_excursion_vector(vec):
    got = run(vec)
    assert got == vec["expect"]
    assert set(got) == {f.name for f in kinds.record("Excursion")}


def test_candles_give_two_points_each():
    c = DATA["candles"]
    points = excursion.candle_points([tuple(x) for x in c["candles"]])
    assert len(points) == 6
    got = excursion.excursion(
        c["side"], c["entry_avg_u"], c["qty"], points, c["start_ms"], c["end_ms"],
        c["net_paise"], "candles",
    )  # fmt: skip
    assert got == c["expect"]


def test_a_short_uses_the_high_for_mae_and_the_low_for_mfe():
    """Mutation 12: with the long's rule a short's MAE would read the low (+780, not -260)."""
    v = next(x for x in DATA["vectors"] if x["id"] == "X2")
    got = run(v)
    assert got["mae_paise"] < 0 < got["mfe_paise"]
    assert got["mae_paise"] == -26000


def test_the_path_is_clipped_to_the_trips_span_both_ends_included():
    """Mutation 13: X1 has a 80.00 point before the entry and a 130.00 point after the exit."""
    v = next(x for x in DATA["vectors"] if x["id"] == "X1")
    assert run(v)["mfe_paise"] == 97500  # not (130-100) x 65 = 1,950.00
    on_the_edge = [(v["start_ms"], 970000), (v["end_ms"], 1150000)]
    got = excursion.excursion(
        "long", v["entry_avg_u"], 65, on_the_edge, v["start_ms"], v["end_ms"], 0
    )
    assert (got["mae_paise"], got["mfe_paise"]) == (-19500, 97500)


def test_no_capture_when_mfe_is_zero():
    """Mutation 14: dividing by a zero MFE must give no capture, never an error or a number."""
    v = next(x for x in DATA["vectors"] if x["id"] == "X5")
    assert run(v)["capture_pct"] is None


def test_the_first_instant_of_an_extreme_wins():
    path = [(10, 90), (20, 80), (30, 80), (40, 120), (50, 120)]
    got = excursion.excursion("long", 100, 1, path, 0, 100, 0)
    assert (got["mae_ms"], got["mfe_ms"]) == (20, 40)


def test_a_path_in_the_wrong_order_is_sorted():
    path = [(40, 120), (10, 90)]
    got = excursion.excursion("long", 100, 1, path, 0, 100, 0)
    assert (got["mae_ms"], got["mfe_ms"]) == (10, 40)


def test_bad_arguments_are_refused():
    with pytest.raises(ValueError):
        excursion.excursion("flat", 1, 1, [], 0, 1, 0)
    with pytest.raises(ValueError):
        excursion.excursion("long", 1, 1, [], 0, 1, 0, "guess")
