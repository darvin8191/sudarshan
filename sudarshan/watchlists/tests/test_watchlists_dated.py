"""A follow row that stays on its date (M8.11, schema 2): the rule against hand-typed vectors, its
words and its refusals. The row through the real bridge is in test_watchlists_rows.py."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from sudarshan.watchlists import dynamic
from sudarshan.watchlists.dynamic import Price, Resolution
from sudarshan.watchlists.store import rows as store_rows
from sudarshan.watchlists.words import dynamic as dwords
from sudarshan.watchlists.words import rows as words

V = json.loads((Path(__file__).parent / "vectors" / "dated.json").read_text(encoding="utf-8"))


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


@pytest.mark.parametrize("c", V["cases"], ids=lambda c: c["name"])
def test_the_date_rule_against_the_vectors(c):
    rule = {"expiry_rule": "date", "expiry_on": c["expiry_on"]}
    closes = {d: ms(t) for d, t in c.get("closes", {}).items()}
    got = dynamic.expiry_of(rule, V["expiries"], ms(c["now"]), closes, index=c["index"])
    assert got == (c["expiry"], c["why"])


def test_the_four_relative_rules_pick_as_before_through_the_same_function():
    now = ms("2026-11-24T15:40:00+05:30")  # the nearest has just stopped
    for name in ("nearest", "next", "month", "next_month"):
        want = dynamic.pick_expiries(V["expiries"], now, index=True)[name]
        assert dynamic.expiry_of({"expiry_rule": name}, V["expiries"], now, index=True) == (
            want, None if want else "no_expiry")  # fmt: skip
    assert dynamic.expiry_of({"expiry_rule": "next_month"}, ["2026-11-24"], 0) == (
        None, "no_expiry")  # fmt: skip


@pytest.mark.parametrize("w", V["words"], ids=lambda w: w["why"])
def test_the_words_of_a_dated_row_that_has_no_contract(w):
    rule = {"kind": "strike_pair", "expiry_rule": "date", "expiry_on": w["expiry_on"]}
    res = Resolution("missing", None, None, None, None, Price(None, None, None, "ok"), w["why"])
    assert dwords.describe(res, w["underlying"], rule, 0) == w["text"]


@pytest.mark.parametrize("r", V["refused"], ids=lambda r: r["name"])
def test_a_date_rule_that_cannot_be_kept_is_refused_in_words(r):
    with pytest.raises(store_rows.Bad) as refused:
        store_rows.normalize(None, r["rule"])
    assert (refused.value.field, refused.value.message) == (r["field"], r["text"])


def test_the_date_is_kept_only_by_the_date_rule():
    base = store_rows.normalize(None, {"kind": "strike_pair", "underlying": "NIFTY",
                                       "underlying_exchange": "NSE_INDEX", "expiry_rule": "date",
                                       "expiry_on": "2026-12-01", "strike_rule": "fixed",
                                       "strike": 25900})  # fmt: skip
    assert base["expiry_on"] == "2026-12-01"
    moved = store_rows.normalize(base, {"expiry_rule": "nearest"})
    assert moved["expiry_rule"] == "nearest" and moved["expiry_on"] is None
