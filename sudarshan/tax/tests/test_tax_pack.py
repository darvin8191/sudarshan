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


#: What trades in the cash segment and is no company's equity share, by its ISIN.
NOT_SHARES = {
    "gold ETF": "INF000B01019",
    "liquid ETF": "INF000C01017",
    "equity ETF": "INF000D01015",
    "REIT": "INE000R25014",
    "InvIT": "INE000V23012",
    "gold bond": "IN0020200013",
    "debenture": "INE000A07017",
    "unknown": "XX0000000000",
}


@pytest.mark.parametrize("isin", NOT_SHARES.values(), ids=NOT_SHARES.keys())
def test_a_cash_segment_sale_of_what_is_no_share_is_listed_and_changes_no_figure(isin):
    base = inputs(charges={})  # the charges have their own test below
    extra = (  # bought in an earlier year, sold in this one at a profit of Rs 5,00,000
        share(20240610, "BUY", 1000, 1000, isin, asset_class="equity"),
        share(20260610, "SELL", 1000, 1500, isin, asset_class="equity"),
        share(20260611, "BUY", 10, 1500, isin),  # and a same-day round trip: no intraday either
        share(20260611, "SELL", 10, 1600, isin),
    )
    lot = OpeningLot(1, 1, isin, 20160310, 5 * 10**6, 100_000)
    before = pack.build_pack(base)
    got = pack.build_pack(replace(base, trades=base.trades + extra, opening=base.opening + (lot,)))
    for part in ("capital_gains", "business", "dividends"):
        assert got[part] == before[part], part
    assert got["not_computed"][0] == words.not_computed_sales(2, "Rs 15,16,000")
    assert got["not_computed_sales"] == [
        {"instrument": isin, "day": 20260610, "amount_paise": 150_000_000},
        {"instrument": isin, "day": 20260611, "amount_paise": 1_600_000},
    ]
    assert any("only a company's equity share is computed" in line for line in got["not_computed"])


@pytest.mark.parametrize("isin", NOT_SHARES.values(), ids=NOT_SHARES.keys())
def test_the_yearly_exemption_never_reaches_what_is_no_share(isin):
    """A long-term profit of Rs 5,00,000 on such a unit alone: no gain, no exemption, no tax,
    though the same trades in a share use the whole Rs 1,25,000."""

    def year(instrument):
        trades = (
            share(20240610, "BUY", 1000, 1000, instrument),
            share(20260610, "SELL", 1000, 1500, instrument),
        )
        return pack.build_pack(pack.PackInputs(2026, 1, 20261114, 0, trades=trades))

    as_share, got = year("INE000Z01019")["capital_gains"], year(isin)["capital_gains"]
    assert as_share["lt_gain_paise"] == 50_000_000
    assert as_share["exemption"]["used_paise"] == 12_500_000
    assert got["exemption"]["used_paise"] == 0
    assert (got["st_gain_paise"], got["lt_gain_paise"]) == (0, 0)
    assert got["taxable"] == {**got["taxable"], "st_paise": 0, "lt_paise": 0}
    assert got["schedule"] == [] and got["needs_fmv"] == [] and got["unknown_cost"] == []
    assert not any(v for k, v in got["indicative_tax"].items() if k.endswith("_paise"))


def test_the_charges_of_what_is_not_computed_are_taken_off_nothing():
    """Upstox's Rs 1,000 covers Rs 1,00,000 of a share and Rs 3,00,000 of a gold ETF: the share's
    sale takes its own quarter, by traded value, and never the ETF's part."""

    def year(*more):
        trades = (share(20260610, "BUY", 100, 400), share(20260710, "SELL", 100, 600), *more)
        charges = {"EQ": {"total_paise": 100_000, "stt_paise": 0}}
        return pack.build_pack(pack.PackInputs(2026, 1, 20261114, 0, trades=trades,
                                               charges=charges))  # fmt: skip

    alone = year()
    etf = NOT_SHARES["gold ETF"]
    mixed = year(share(20260611, "BUY", 100, 1400, etf), share(20260712, "SELL", 100, 1600, etf))
    assert alone["capital_gains"]["st_gain_paise"] == 2_000_000 - 100_000
    assert mixed["capital_gains"]["st_gain_paise"] == 2_000_000 - 25_000
    assert words.CHARGES_OF_OTHERS in mixed["notes"]
    assert words.CHARGES_OF_OTHERS not in alone["notes"]


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
