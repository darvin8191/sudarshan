"""dashboard.py against hand-typed numbers and lines (dashboards design 3.2, 3.6, 3.8 and 7).

Every expected value is typed in vectors/dashboard.json. A string that starts with "@" is a time in
IST; the test turns it into epoch milliseconds and nothing else.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from screener_helpers import ms

from sudarshan.screener import dashboard as d
from sudarshan.screener import words as w

VECTORS = json.loads((Path(__file__).parent / "vectors" / "dashboard.json").read_text("utf-8"))
FLOORS = {"modes": 4, "day_only": 5, "waits": 8, "clauses": 4, "sorts": 4, "series": 4, "lines": 15}


def at(text: str) -> int:
    day, clock = text[1:].split(" ")
    return ms(day, clock)


def need_of(raw: dict) -> dict:
    return {(k if k in ("D", "W") else int(k)): v for k, v in raw.items()}


def test_the_vectors_are_not_thinned():
    for name, floor in FLOORS.items():
        assert len(VECTORS[name]) >= floor, name
    assert len(VECTORS["order"]["cases"]) >= 9


@pytest.mark.parametrize("v", VECTORS["modes"], ids=lambda v: v["id"])
def test_how_a_screen_is_checked(v):
    assert d.mode_of(v["run"], v["day_only"]) == (v["mode"], v["words"])


@pytest.mark.parametrize("v", VECTORS["day_only"], ids=lambda v: v["id"])
def test_day_only_from_a_plans_needs(v):
    assert d.day_only(need_of(v["need"])) is v["expect"]


@pytest.mark.parametrize("v", VECTORS["waits"], ids=lambda v: v["id"])
def test_what_a_screen_waits_for(v):
    assert d.waits_of(need_of(v["need"])) == v["expect"]


@pytest.mark.parametrize("v", VECTORS["clauses"], ids=lambda v: v["id"])
def test_value_columns_the_screen_lacks_are_dropped(v):
    assert d.usable_clauses(v["asked"], v["has"]) == v["expect"]


@pytest.mark.parametrize("v", VECTORS["sorts"], ids=lambda v: v["id"])
def test_a_sort_by_a_missing_line_falls_back_to_the_screens_rank(v):
    assert d.usable_sort(v["sort"], v["has"]) == v["expect"]


@pytest.mark.parametrize("v", VECTORS["order"]["cases"], ids=lambda v: v["id"])
def test_row_order_for_each_sort_with_ties_by_symbol(v):
    order = VECTORS["order"]
    rows = d.sort_rows(order["rows"], v["sort"], entered=order["entered"], found=order["found"])
    assert [r["symbol"] for r in rows] == v["expect"]
    if v["sort"]:
        assert [r["rank"] for r in rows] == [1, 2, 3, 4]


@pytest.mark.parametrize("v", VECTORS["series"], ids=lambda v: v["id"])
def test_the_series_is_thinned(v):
    start = at(v["from"])
    points = [
        (start + k * v["step_min"] * 60_000, k * v["step_min"]) for k in range(1, v["count"] + 1)
    ]
    kept = d.thin_series(points)
    assert len(kept) == v["kept"]
    assert kept[0] == (at(v["first"][0]), v["first"][1])
    assert kept[-1] == (at(v["last"][0]), v["last"][1])


@pytest.mark.parametrize("v", VECTORS["series_words"], ids=lambda v: v["id"])
def test_the_series_in_words_for_a_screen_reader(v):
    assert w.series_words([(at(t), m) for t, m in v["points"]]) == v["expect"]


@pytest.mark.parametrize("v", VECTORS["lines"], ids=lambda v: v["id"])
def test_the_cards_line(v):
    given = v["in"]
    found = d.line(
        status=given.get("status", "active"),
        reason=given.get("reason"),
        mode=given.get("mode", "intraday"),
        waits=given.get("waits"),
        engine_state=given["state"],
        engine_line=given["line"],
        run_at=at(given["run_at"]) if "run_at" in given else None,
        now=at(given["now"]) if "now" in given else at("@2026-10-28 11:00"),
    )
    assert (found["kind"], found["words"], found["live"]) == (v["kind"], v["words"], v["live"])
    assert found["state"] == given["state"] and found["kind"] in d.LINE_KINDS


def test_a_card_that_is_not_live_never_says_real_time_unless_it_is():
    for v in VECTORS["lines"]:
        if not v["live"]:
            assert not v["words"].startswith("Real-time"), v["id"]
