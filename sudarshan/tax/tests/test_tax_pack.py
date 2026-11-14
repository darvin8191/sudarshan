"""The pack end to end against pack.json (PK1), and what the pack says when inputs are short."""

from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest
from tax_vector_tools import cases

from sudarshan.tax import pack, words
from sudarshan.tax.model import Action, Carry, OpeningLot, Settings, Trade, build

PK1 = cases("pack.json")[0].values[0]
ROWS = {"trades": Trade, "opening": OpeningLot, "actions": Action, "carries": Carry}


def inputs(given: dict = PK1["in"], **changes) -> pack.PackInputs:
    values = {key: value for key, value in given.items() if key not in ROWS and key != "sources"}
    values.update({key: tuple(build(cls, row) for row in given[key]) for key, cls in ROWS.items()})
    return replace(pack.PackInputs(sources=tuple(given["sources"]), **values), **changes)


def share(day, side, qty, rupees, isin="INE000Z01019", **more):
    return Trade(
        1, 1, day, day, side, "EQ", isin, qty * 10**6, rupees * 10**4, qty * rupees * 100, **more
    )


def test_pk1_is_the_contract():
    got = pack.build_pack(inputs())
    assert got == PK1["out"]
    assert json.loads(json.dumps(got)) == got  # plain data: stored and sent as it is


def test_running_only_while_the_year_is_running():
    assert pack.build_pack(inputs())["running"] is True
    assert pack.build_pack(inputs(as_of_day=20260401))["running"] is True
    assert pack.build_pack(inputs(as_of_day=20270331))["running"] is True
    assert pack.build_pack(inputs(as_of_day=20270401))["running"] is False
    earlier = pack.build_pack(inputs(fy=2025))
    assert earlier["running"] is False and earlier["fy_label"] == "FY 2025-26"
    assert earlier["capital_gains"]["schedule"] == [] and earlier["business"]["turnover_paise"] == 0


def test_another_taxpayer_s_rows_are_not_in_the_pack():
    got = pack.build_pack(inputs(taxpayer=2))
    assert got["capital_gains"]["schedule"] == [] and got["capital_gains"]["carried"] == []
    assert got["business"]["turnover_paise"] == 0 and got["business"]["open"] == []


def test_no_figure_is_a_float_and_no_sentence_names_a_section():
    def walk(value):
        assert not isinstance(value, float)
        for item in (
            value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
        ):
            walk(item)

    got = pack.build_pack(inputs())
    walk(got)
    text = json.dumps(got) + " ".join(str(v) for v in vars(words).values())
    assert not re.search(r"(?i)\bsection\s*\d|\bs\.\s?\d|\bu/s\b", text)
    assert text.isascii() and got["note"] == words.WORKING_PAPER


def test_charges_left_out_when_the_owner_says_so_or_when_not_read():
    off = pack.build_pack(inputs(settings=Settings(allocate_expenses=False)))
    rows = off["capital_gains"]["schedule"]
    assert [row["expenses_paise"] for row in rows] == [0, 0, 0]
    assert rows[1]["gain_paise"] == 3_500_000 and words.EXPENSES_LEFT_OUT in off["notes"]
    unread = pack.build_pack(inputs(charges={}))
    assert unread["business"]["non_speculative"]["charges_paise"] == 0
    assert words.CHARGES_UNKNOWN.format(segment="EQ") in unread["notes"]
    assert words.CHARGES_UNKNOWN.format(segment="FO") in unread["notes"]


def test_what_cannot_be_worked_out_is_listed_in_words_and_in_no_total():
    extra = (
        share(20260505, "SELL", 10, 500),  # nothing held: no purchase record
        share(20260506, "SELL", 100, 500, "INE000Y01012"),  # a 2016 lot, 2018 price unknown
        Trade(1, 1, 20260507, 1, "SELL", "MF", "INF000A01011", 10**7, 10**6, 123_400),  # no tag
    )
    old = OpeningLot(1, 1, "INE000Y01012", 20160310, 100 * 10**6, 2_000_000)
    base = inputs()
    got = pack.build_pack(replace(base, trades=base.trades + extra, opening=base.opening + (old,)))
    gains = got["capital_gains"]
    assert (len(gains["unknown_cost"]), len(gains["needs_fmv"])) == (1, 1)
    assert gains["st_gain_paise"] < 3_500_000 and gains["lt_gain_paise"] == 2_000_000
    assert words.unknown_cost(1) in got["notes"] and words.needs_fmv(1) in got["notes"]
    assert got["not_computed"][0] == words.not_computed_sales(1, "Rs 1,234")
    assert got["not_computed_sales"] == [
        {"instrument": "INF000A01011", "day": 20260507, "amount_paise": 123_400}
    ]
    known = pack.build_pack(replace(base, trades=base.trades + extra, opening=base.opening + (old,),
                                    fmv={"INE000Y01012": 350 * 10**4}))  # fmt: skip
    assert known["capital_gains"]["needs_fmv"] == []
    assert known["capital_gains"]["lt_gain_paise"] > 2_000_000 + 1_490_000  # 50,000 - 35,000 more


def test_a_buyback_of_the_dividend_years_puts_its_receipt_under_dividends():
    base = inputs(fy=2025, as_of_day=20261114)
    tender = share(20260210, "SELL", 100, 600, "INE000C01016", kind_hint="buyback")
    trades = tuple(t for t in base.trades if t.kind_hint != "buyback") + (tender,)
    got = pack.build_pack(replace(base, trades=trades))
    row = got["capital_gains"]["schedule"][0]
    assert (row["sale_paise"], row["gain_paise"], row["term"]) == (0, -4_000_000, "long")
    assert got["dividends"]["buyback_paise"] == 6_000_000 == got["dividends"]["total_paise"]


def test_the_split_rate_year_names_the_order_it_used():
    lot = OpeningLot(1, 1, "INE000Z01019", 20200105, 300 * 10**6, 300_000)
    sales = (share(20240610, "SELL", 100, 800), share(20240910, "SELL", 100, 1000))
    for order, tax in (("highest_rate_first", 550_000), ("chronological", 687_500)):
        given = pack.PackInputs(
            2024, 1, 20261114, 0, sales, (lot,), settings=Settings(exempt_order=order)
        )
        got = pack.build_pack(given)
        # gains 79,000 and 99,000 after cost 1,000 each: Y4's shape, 1,000 less in each part
        assert got["capital_gains"]["exemption"]["order"] == order
        assert words.exempt_order(order) in got["notes"]
        assert got["capital_gains"]["indicative_tax"]["tax_paise"] == tax - (
            20_000 if order == "highest_rate_first" else 25_000
        )


@pytest.mark.parametrize("name", ["NOT_COMPUTED", "WORKING_PAPER", "LOW_MARGIN", "SET_OFF_ORDER"])
def test_the_words_the_owner_reads_never_give_a_verdict(name):
    text = " ".join(getattr(words, name)) if name == "NOT_COMPUTED" else getattr(words, name)
    assert "audit required" not in text.lower() and "must" not in text.lower()


def test_a_trade_is_in_the_year_it_closed_in():
    march = ("NFO", "NIFTY", 20260428, 230000000, "PE")
    bought = Trade(1, 1, 20260320, 1, "BUY", "FO", march, 65 * 10**6, 20 * 10**4, 130_000)
    base = inputs()
    later = pack.build_pack(replace(base, trades=base.trades + (bought,)))
    assert later["business"]["assumed_zero_count"] == 2  # expired worthless in April 2026
    assert later["business"]["non_speculative"]["gross_paise"] == 130_000 - 130_000
    earlier = pack.build_pack(replace(base, fy=2025, trades=base.trades + (bought,)))
    assert earlier["business"]["assumed_zero_count"] == 0
    assert earlier["business"]["non_speculative"]["gross_paise"] == 0
