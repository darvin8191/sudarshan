"""Derivative pairing, expiry, turnover and income against business.json (T5)."""

from __future__ import annotations

import pytest
from tax_vector_tools import cases, picked

from sudarshan.tax import business, words
from sudarshan.tax.model import Realised, Trade, build

FLAGS = {
    "digital": words.audit_above_digital("Rs 10 crore"),
    "base": words.audit_above_base("Rs 1 crore", "Rs 10 crore"),
    "low": words.LOW_MARGIN,
}


def paired(given: dict) -> business.Paired:
    settlements = {tuple(contract): price for contract, price in given["settlements"]}
    trades = [build(Trade, row) for row in given["trades"]]
    return business.pair(trades, settlements, given["as_of_day"])


def realised(rows: list[dict]) -> list[Realised]:
    contract = ("NFO", "NIFTY", 20260827, 0, "FUT")
    return [
        Realised(1, 1, (contract[0], row.get("scrip", "NIFTY"), *contract[2:]),
                 **{k: v for k, v in row.items() if k != "scrip"})
        for row in rows
    ]  # fmt: skip


@pytest.mark.parametrize("case", cases("business.json", "pair"))
def test_pair(case):
    got, out = paired(case["in"]), case["out"]
    assert picked(got.realised, out["realised"]) == out["realised"]
    assert (len(got.unknown_settlement), len(got.open)) == (out["unknown_settlement"], out["open"])
    assert all(r.pnl_paise == r.sell_paise - r.buy_paise for r in got.realised)


TRADED_ONLY = [
    c for c in cases("business.json", "pair") if "settled" not in c.id + c.values[0]["note"]
]


@pytest.mark.parametrize("case", [c for c in TRADED_ONLY if "worthless" not in c.values[0]["note"]])
def test_every_paisa_traded_is_in_a_piece_or_still_open(case):
    got = paired(case["in"])
    traded = sum(t["amount_paise"] for t in case["in"]["trades"] if t["segment"] == "FO")
    pieces = sum(r.buy_paise + r.sell_paise for r in got.realised)
    assert pieces + sum(row[6] for row in got.unknown_settlement + got.open) == traded


@pytest.mark.parametrize("case", cases("business.json", "income"))
def test_income(case):
    given, out = dict(case["in"]), case["out"]
    got = business.income(realised(given.pop("realised")), given.pop("fy"), **given)
    for bucket in ("non_speculative", "speculative"):
        assert {key: got[bucket][key] for key in out[bucket]} == out[bucket], bucket
    assert got["turnover_paise"] == out["turnover_paise"]
    assert got["flags"] == [FLAGS[name] for name in out["flags"]]
    assert got["turnover_method"] == given.get("method", "abs_pnl")
    assert got["turnover_method"] in words.TURNOVER_METHODS


def test_flags_are_sentences_that_never_give_a_verdict():
    text = " ".join(FLAGS.values()).lower()
    assert "audit required" not in text and "section" not in text and "ask your ca" in text


def test_by_kind_splits_futures_options_and_intraday():
    case = next(c.values[0] for c in cases("business.json", "income") if c.id == "S1-two-buckets")
    got = business.income(realised(case["in"]["realised"]), 2026)
    assert got["non_speculative"]["by_kind"] == {
        "futures": {"gross_paise": 325_000, "turnover_paise": 975_000, "count": 2}
    }
    assert got["speculative"]["by_kind"]["speculative"]["count"] == 2


def test_share_charges_split_and_the_cross_check():
    split, differs = (c.values[0] for c in cases("business.json", "other"))
    assert business.split_share_charges(*split["in"]["split"]) == split["out"]
    given = differs["in"]
    assert (
        business.differs(realised(given["realised"]), given["upstox"], given["fy"])
        == differs["out"]
    )
    assert business.differs(realised(given["realised"]), given["upstox"], 2025) == [
        {"scrip": "BANKNIFTY", "own_paise": None, "upstox_paise": 25_000},
        {"scrip": "NIFTY", "own_paise": None, "upstox_paise": -130_050},
    ]
