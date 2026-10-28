"""The limit price band (M20.7, design section 6): golden vectors for the rule, and which band
an instrument takes."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sudarshan.guard import band, readers, rules

ALL = json.loads((Path(__file__).parent / "band_vectors.json").read_text())
VECTORS = ALL["cases"]
RAW = ALL["raw_cases"]


@pytest.mark.parametrize("case", VECTORS, ids=lambda case: case["name"])
def test_the_band(case):
    quote = None if case["quote"] is None else band.Quote(**case["quote"])
    _expect(case, quote)


@pytest.mark.parametrize("case", RAW, ids=lambda case: case["name"])
def test_the_band_on_upstox_s_own_quote(case):
    """Limits review L7: Upstox's raw full quote, through the Upstox module's own mapping and
    the reader the gate uses, never a quote shaped by hand."""
    from broker.upstox.api.data import BrokerData

    mapped = BrokerData._quote_from_full_v3(None, case["upstox_quote"])
    _expect(case, readers.quote_of(mapped))


def _expect(case: dict, quote) -> None:
    breach = band.check(
        rules.BUY if case["side"] == 1 else rules.SELL,
        case["type"],
        case["price"],
        case["trigger"],
        quote,
        band.Band(case["band"]["pct"], case["band"]["floor"]),
        both_sides=case.get("both_sides", False),
    )
    if case["breach"] is None:
        assert breach is None
    else:
        assert breach is not None
        assert breach.what == case["breach"]["what"]
        assert breach.reference == pytest.approx(case["breach"]["reference"])
        assert breach.away_pct == pytest.approx(case["breach"]["away_pct"])
        assert breach.above is case["breach"]["above"]


@pytest.mark.parametrize(
    ("symbol", "exchange", "segment"),
    [
        ("NIFTY27OCT2625900CE", "NFO", band.OPTION),
        ("SENSEX28OCT2685000PE", "BFO", band.OPTION),
        ("CRUDEOILM20NOV265400CE", "MCX", band.OPTION),
        ("BANKNIFTY27OCT26FUT", "NFO", band.FUTURE),
        ("GOLDM05DEC26FUT", "MCX", band.FUTURE),
        ("SBIN", "NSE", band.CASH),
        ("RELIANCE", "BSE", band.CASH),
        ("NIFTY", "NSE_INDEX", None),
    ],
)
def test_which_band_an_instrument_takes(symbol, exchange, segment):
    assert band.segment_of(symbol, exchange) == segment


def test_each_segment_takes_its_own_setting():
    settings = SimpleNamespace(
        band_opt_pct=20.0, band_opt_min_rs=2.0, band_fut_pct=1.0, band_cash_pct=5.0
    )
    assert band.band_for(band.OPTION, settings) == band.Band(20.0, 2.0)
    assert band.band_for(band.FUTURE, settings) == band.Band(1.0)
    assert band.band_for(band.CASH, settings) == band.Band(5.0)
    assert band.band_for(None, settings) is None
