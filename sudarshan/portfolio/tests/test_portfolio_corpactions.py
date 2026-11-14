"""Upstox's corporate actions as integers (M7.36; core/corpactions.py) against corpactions.json,
typed by hand from Upstox's documented fields: the rows, the dates, the two readings of a ratio
and what makes an event the same event on the next read."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from sudarshan.portfolio.core import corpactions as ca

# Read as the reader reads Upstox: every fraction a Decimal, never a float.
FILE = json.loads(
    (Path(__file__).parent / "vectors" / "corpactions.json").read_text(encoding="utf-8"),
    parse_float=Decimal,
)
ISIN = FILE["isin"]


@pytest.mark.parametrize("case", FILE["rows"], ids=lambda c: c["id"])
def test_a_row_becomes_the_vectors_integers_or_is_left_out_with_its_reason(case):
    assert ca.parse_row(ISIN, case["raw"]) == (case["expect"], case["skip"])


@pytest.mark.parametrize("case", FILE["days"], ids=lambda c: c["id"])
def test_a_date_is_a_real_day_or_nothing(case):
    assert ca.parse_day(case["text"]) == case["day"]


@pytest.mark.parametrize("case", FILE["readings"], ids=lambda c: c["id"])
def test_a_ratio_reads_two_ways_the_proposed_one_first(case):
    assert [list(way) for way in ca.readings(*case["in"])] == case["out"]


@pytest.mark.parametrize("case", [c for c in FILE["readings"] if len(c["out"]) == 2],
                         ids=lambda c: c["id"])  # fmt: skip
def test_the_other_way_round_swaps_and_swaps_back(case):
    """Mutation target 18: "the other way round" that does not swap fails here."""
    kind, first, second = case["in"]
    proposed, other = (tuple(way) for way in case["out"])
    assert proposed != other
    assert ca.swap(kind, first, second, *proposed) == other
    assert ca.swap(kind, first, second, *other) == proposed


def test_a_ratio_that_is_no_reading_cannot_be_turned_round():
    with pytest.raises(ValueError):
        ca.swap("bonus", 1, 2, 3, 4)
    with pytest.raises(ValueError):
        ca.swap("dividend", 1, 2, 1, 2)


@pytest.mark.parametrize("case", FILE["identity"], ids=lambda c: c["id"])
def test_an_event_is_the_same_event_by_its_facts(case):
    assert ca.identity(case["event"]) == case["text"]


def test_one_answer_gives_its_events_once_and_counts_what_it_left_out():
    case = FILE["answer"]
    read = ca.parse_answer(ISIN, case["payload"])
    assert read == {"events": case["events"], "skipped": case["skipped"]}


@pytest.mark.parametrize("payload", [None, [], {}, {"data": None}, {"data": {"name": "Bonus"}}])
def test_an_answer_that_is_not_upstoxs_shape_is_never_read_as_no_events(payload):
    with pytest.raises(ValueError):
        ca.parse_answer(ISIN, payload)


def test_no_events_is_an_answer():
    assert ca.parse_answer(ISIN, {"status": "success", "data": []})["events"] == []


def test_a_float_amount_is_read_through_its_text():
    assert ca.parse_amount(5.5) == 55000
    assert ca.parse_amount(Decimal("0.1")) == 1000
    assert ca.parse_amount("1,250.50") == 12505000
    for nothing in (None, True, "", "abc", 0, -2, "NaN", "Infinity"):
        assert ca.parse_amount(nothing) is None
