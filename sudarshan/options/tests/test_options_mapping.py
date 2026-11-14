"""The chain's target and mapping (M11.1, O1): pure, pinned by ``vectors/chain.json``."""

from __future__ import annotations

import copy

import pytest
from options_master import V, world

from sudarshan.options import mapping

IX, BY_TOKEN, _ = world()


def nifty():
    return mapping.target_of(IX, "NFO", "nifty", "2026-11-17")


def test_the_target_names_the_underlying_s_own_key_and_the_expiry_s_ladder():
    target = nifty()
    assert target.underlying == "NIFTY" and target.exchange == "NFO"
    assert target.instrument_key == "NSE_INDEX|Nifty 50"
    assert target.underlying_exchange == "NSE_INDEX"
    assert (
        len(target.ladder) == 10 and target.ladder[(25900.0, "PE")].symbol == "NIFTY17NOV2625900PE"
    )
    stock = mapping.target_of(IX, "NFO", "ACME", "2026-11-24")
    assert stock.instrument_key == "NSE_EQ|INE000A01018" and stock.underlying_exchange == "NSE"
    assert mapping.target_of(IX, "BFO", "SENSEX", "2026-11-19").instrument_key == "BSE_INDEX|SENSEX"


def test_the_listing_names_the_spot_every_expiry_and_every_future():
    got = mapping.listing_of(IX, "NFO", "nifty")
    assert got == {
        "underlying": "NIFTY", "exchange": "NFO", "underlying_exchange": "NSE_INDEX",
        "symbol": "NIFTY", "expiries": ["2026-11-17", "2026-11-24"],
        "futures": [("2026-11-24", "NIFTY24NOV26FUT"), ("2026-12-29", "NIFTY29DEC26FUT")],
    }  # fmt: skip
    assert mapping.listing_of(IX, "BFO", "SENSEX")["futures"] == []
    for exchange, name in (("MCX", "MCXONLY"), ("NFO", "NOSUCH"), ("BFO", "NIFTY")):
        assert mapping.listing_of(IX, exchange, name) is None


@pytest.mark.parametrize(
    ("exchange", "underlying", "expiry"),
    [
        ("MCX", "MCXONLY", "2026-11-17"),  # Upstox offers no chain for MCX
        ("NFO", "NOSUCH", "2026-11-17"),
        ("NFO", "NIFTY", "2026-12-29"),  # an expiry the master does not list
        ("BFO", "NIFTY", "2026-11-17"),  # the wrong exchange for the underlying
    ],
)
def test_no_target_for_a_chain_the_master_does_not_list(exchange, underlying, expiry):
    assert mapping.target_of(IX, exchange, underlying, expiry) is None


def test_the_vector_chain_maps_to_openalgo_symbols():
    got = mapping.map_chain(V["answer"], nifty(), BY_TOKEN)
    want = V["expected"]
    assert got.strikes == want["strikes"]
    assert got.spot == want["spot"]
    assert got.pcr == pytest.approx(want["pcr"])
    assert (got.left_out, got.by_strike) == (want["left_out"], want["by_strike"])


def test_a_figure_upstox_did_not_send_is_absent_and_a_sent_zero_stays():
    by = {s["strike"]: s for s in mapping.map_chain(V["answer"], nifty(), BY_TOKEN).strikes}
    thin = by[25950.0]["ce"]
    for absent in ("ltp", "bid", "prev_oi", "iv", "delta"):
        assert absent not in thin
    assert thin["oi"] == 800.0  # a number sent as text is read
    assert by[25950.0]["pe"]["ltp"] == 0.0 and by[25900.0]["pe"]["prev_oi"] == 0.0
    assert "pe" not in by[25800.0]  # the side Upstox did not name


def test_with_keys_that_are_not_the_master_s_every_leg_still_maps_by_strike_and_side():
    answer = copy.deepcopy(V["answer"])
    for row in answer["data"]:
        for side in ("call_options", "put_options"):
            if isinstance(row, dict) and side in row:
                row[side]["instrument_key"] = "SOMETHING|ELSE"
    got = mapping.map_chain(answer, nifty(), BY_TOKEN)
    assert got.strikes == V["expected"]["strikes"]
    assert got.by_strike == 7 and got.left_out == 2


def test_a_key_naming_another_contract_is_not_believed_over_the_row_s_strike_and_side():
    by = {s["strike"]: s for s in mapping.map_chain(V["answer"], nifty(), BY_TOKEN).strikes}
    assert by[25850.0]["pe"]["symbol"] == "NIFTY17NOV2625850PE"


def test_pcr_is_absent_while_no_call_carries_open_interest():
    answer = copy.deepcopy(V["answer"])
    for row in answer["data"]:
        if isinstance(row, dict) and "call_options" in row:
            row["call_options"]["market_data"]["oi"] = None
    assert mapping.map_chain(answer, nifty(), BY_TOKEN).pcr is None


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        ({"status": "success"}, mapping.NO_ROWS_LIST),
        ({"status": "success", "data": {"rows": []}}, mapping.NO_ROWS_LIST),
        ("text", mapping.NO_ROWS_LIST),
        ({"data": [{"strike_price": 1}, "x"]}, mapping.NO_LEGS),
        ({"data": []}, None),
        (V["answer"], None),
    ],
)
def test_an_answer_that_cannot_be_read_as_a_chain_is_named(answer, why):
    assert mapping.unusable(answer) == why


def test_the_shape_of_an_answer_is_key_names_only():
    shape = mapping.shape_of(V["answer"])
    assert shape["answer"] == ["data", "status"]
    assert "strike_price" in shape["row"] and "instrument_key" in shape["call"]
    assert "25900" not in repr(shape) and "NSE_FO" not in repr(shape)
    assert mapping.shape_of(None) == {"answer": [], "row": [], "call": []}


@pytest.mark.parametrize(
    ("value", "want"),
    [(1, 1.0), ("2.5", 2.5), (" 3 ", 3.0), (None, None), (True, None), ("x", None),
     (float("nan"), None), (float("inf"), None), ("NaN", None), ([], None), (0, 0.0)],
)  # fmt: skip
def test_number_reads_finite_numbers_only(value, want):
    assert mapping.number(value) == want
