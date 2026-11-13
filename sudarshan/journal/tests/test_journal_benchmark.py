"""Week against NIFTY 50 and beta (JA5): vectors B1-B7."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sudarshan.journal.core import benchmark, kinds

DATA = json.loads(
    (Path(__file__).parent / "vectors" / "benchmark.json").read_text(encoding="utf-8")
)
CAP = DATA["beta"]["capital_base_paise"]


@pytest.mark.parametrize(
    "vec", DATA["beta"]["vectors"], ids=[v["id"] for v in DATA["beta"]["vectors"]]
)
def test_beta_vector(vec):
    kwargs = {}
    if "min_days" in vec:
        kwargs["min_days"] = vec["min_days"]
    if "ready_day" in vec:
        kwargs["ready_day"] = vec["ready_day"]
    got = benchmark.correlation_beta(vec["days"], CAP, **kwargs)
    assert got == vec["expect"]
    assert set(got) == {f.name for f in kinds.record("Beta")}


@pytest.mark.parametrize("vec", DATA["week"]["vectors"], ids=[v["id"] for v in DATA["week"]["vectors"]])  # fmt: skip
def test_week_vector(vec):
    got = benchmark.week_vs_index(
        vec["net_paise"], vec["capital_base_paise"], vec["index_close_u"], vec["index_prev_close_u"]
    )
    assert got == vec["expect"]


def test_nineteen_days_is_not_enough():
    """Mutation 15: with the floor at 19 instead of 20, B3 would show a beta."""
    b3 = next(v for v in DATA["beta"]["vectors"] if v["id"] == "B3")
    assert benchmark.correlation_beta(b3["days"], CAP)["state"] == "short"
    assert kinds.LIMITS["beta_min_days"] == 20


def test_only_the_last_sixty_days_count():
    old = [{"net_paise": 500000, "close_u": 1010000, "prev_close_u": 1000000}] * 30
    recent = [
        {
            "net_paise": (200000 if i % 2 else -200000),
            "close_u": 1010000 if i % 2 else 990000,
            "prev_close_u": 1000000,
        }  # fmt: skip
        for i in range(60)
    ]
    assert benchmark.correlation_beta(old + recent, CAP) == benchmark.correlation_beta(recent, CAP)
    assert benchmark.correlation_beta(old + recent, CAP)["days"] == 60


def test_without_capital_there_is_no_beta():
    got = benchmark.correlation_beta(DATA["beta"]["vectors"][0]["days"], None, min_days=4)
    assert got["state"] == "no_capital" and got["note"] == benchmark.NO_CAPITAL_NOTE
    assert got["beta"] is None


def test_the_note_without_a_ready_day():
    assert benchmark.beta_note(3, 20) == "shown after 20 trading days"
    assert benchmark.beta_note(3, 20, 20270105) == "shown after 20 trading days (from 5 Jan 2027)"
