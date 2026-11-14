"""The symbol map and the expiry rules (A3), against a master fixture, under both regimes."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sudarshan.automation import model as m
from sudarshan.automation import symbols

VECTORS = json.loads((Path(__file__).parent / "symbol_vectors.json").read_text())
MASTER = VECTORS["master"]
IST = timezone(timedelta(hours=5, minutes=30))
LISTED = {(ex, s) for ex, names in MASTER["symbols"].items() for s in names}
OPTIONS = MASTER["options"]
for _underlying, _by_expiry in OPTIONS.items():
    for _day, _strikes in _by_expiry.items():
        for _strike in _strikes:
            _written = str(int(_strike)) if _strike == int(_strike) else str(_strike)
            for _side in ("CE", "PE"):
                _symbol = f"{_underlying}{symbols.expiry_code(_day)}{_written}{_side}"
                if _symbol not in MASTER["options_not_listed"]:
                    LISTED.add(("BFO" if _underlying == "SENSEX" else "NFO", _symbol))


def ms(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST).timestamp() * 1000)


def known(exchange: str, symbol: str) -> bool:
    return (exchange, symbol) in LISTED


def resolve(case, **change):
    strategy = m.Strategy(id=1, name="ORB", **{**case["strategy"], **change})
    underlying = symbols.futures_underlying(strategy)
    expiries = MASTER["futures"].get(underlying, [])
    by_expiry = OPTIONS.get(strategy.underlying, {})
    if strategy.kind == m.OPTION:
        expiries = sorted(by_expiry)
    return symbols.resolve(
        strategy,
        ms(case["now"]),
        known=known,
        expiries=expiries,
        indices=MASTER["indices"],
        closes={day: ms(at) for day, at in case.get("closes", {}).items()},
        entering=case["entering"],
        target=case.get("target", 1),
        strikes_of=lambda iso: by_expiry.get(iso, []),
        price=case.get("price"),
    )


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["name"] for c in VECTORS["cases"]])
def test_symbol_vector(case):
    got, expect = resolve(case), dict(case["expect"])
    if "unmapped" in expect:
        assert isinstance(got, symbols.Unmapped), got
        assert got.reason == expect["unmapped"]
        if "what" in expect:
            assert got.what == expect["what"]
        if "stop" in expect:
            assert got.stop_ms == ms(expect["stop"])
        return
    assert isinstance(got, symbols.Contract), got
    if "stop" in expect:
        assert got.stop_ms == ms(expect.pop("stop"))
    for key, value in expect.items():
        assert getattr(got, key) == value, (key, getattr(got, key))
    assert known(got.exchange, got.symbol)  # never a contract the master does not list


def test_every_reason_has_a_vector():
    seen = {c["expect"].get("unmapped") for c in VECTORS["cases"]} - {None}
    assert seen == {
        "too_close", "no_expiry", "not_listed", "commodity", "unknown", "index", "unchecked",
        "no_chart", "no_option_expiry", "no_price", "no_strike", "nothing_held",
    }  # fmt: skip


@pytest.mark.parametrize("case", VECTORS["sizes"], ids=[c["name"] for c in VECTORS["sizes"]])
def test_size_vector(case):
    strategy = m.Strategy(id=1, name="ORB", **case["strategy"])
    got = symbols.quantity(strategy, case["lot"], case["freeze"])
    if isinstance(case["expect"], dict):
        assert isinstance(got, symbols.NotSized) and got.reason == case["expect"]["not_sized"]
        assert got.figure == case["expect"].get("figure", 0)
    else:
        assert got == case["expect"]


@pytest.mark.parametrize("case", VECTORS["scaled"], ids=[c["name"] for c in VECTORS["scaled"]])
def test_scaled_size_vector(case):
    """A8b: whole lots, never above the cap, never rounded."""
    strategy = m.Strategy(id=1, name="SCL", **case["strategy"])
    got = symbols.scaled_quantity(strategy, case["size"], case["lot"], case["freeze"])
    if isinstance(case["expect"], dict):
        assert isinstance(got, symbols.NotSized) and got.reason == case["expect"]["not_sized"]
        if "figure" in case["expect"]:
            assert got.figure == case["expect"]["figure"]
    else:
        assert got == case["expect"] and got % case["lot"] == 0
        assert got // case["lot"] <= strategy.max_lots


@pytest.mark.parametrize("case", VECTORS["held"], ids=[c["name"] for c in VECTORS["held"]])
def test_a_held_contract_past_its_stop(case):
    got = symbols.held_expired(case["symbol"], case["exchange"], ms(case["now"]), MASTER["indices"])
    assert got is case["expect"]


def test_the_expiry_comes_from_the_masters_list_never_from_a_weekday():
    """Mutation 14. Move every expiry of the list to days no weekday rule would pick (a Wednesday,
    a Friday, a Sunday): the contract follows the list."""
    odd = ["2026-11-18", "2026-12-25", "2027-01-31"]
    listed = {("NFO", symbols.future_symbol("NIFTY", iso)) for iso in odd}
    strategy = m.Strategy(id=1, name="ORB", underlying="NIFTY")
    seen = []
    for now in ("2026-11-16 10:00:00", "2026-11-18 15:40:00", "2026-12-25 15:30:00"):
        got = symbols.resolve(
            strategy, ms(now), known=lambda ex, s: (ex, s) in listed, expiries=odd,
            indices=MASTER["indices"], entering=False,
        )  # fmt: skip
        seen.append(got.symbol)
    assert seen == ["NIFTY18NOV26FUT", "NIFTY25DEC26FUT", "NIFTY31JAN27FUT"]
    # an option strategy's expiry too: the nearest, the next and the month's all follow the list
    option = m.Strategy(id=1, name="OPT", kind=m.OPTION, underlying="NIFTY", expiry_rule="nearest")
    strikes, seen = [24950.0, 25000.0, 25050.0], []
    for rule in m.OPTION_RULES:
        got = symbols.resolve(
            m.Strategy(**{**option.__dict__, "expiry_rule": rule}), ms("2026-11-16 10:00:00"),
            known=lambda ex, s: True, expiries=odd, indices=MASTER["indices"], target=-1,
            strikes_of=lambda iso: strikes, price=25001.0,
        )  # fmt: skip
        seen.append(got.symbol)
    assert seen == ["NIFTY18NOV2625000PE", "NIFTY25DEC2625000PE", "NIFTY18NOV2625000PE"]
    source = Path(symbols.__file__).read_text()
    assert "weekday" not in source.replace("a weekday", "") and "isoweekday" not in source


def test_the_lot_size_is_never_typed_into_the_code():
    source = Path(symbols.__file__).read_text()
    for figure in ("65", "30", "75", "25", "50", "20"):
        assert f" {figure}\n" not in source and f"= {figure}" not in source


def test_expiry_of_and_the_symbol_it_came_from():
    assert symbols.expiry_of("NIFTY24NOV26FUT") == ("NIFTY", "2026-11-24")
    assert symbols.expiry_of("NIFTY24NOV2625000CE") == ("NIFTY", "2026-11-24")
    assert symbols.expiry_of("M&M29DEC26FUT") == ("M&M", "2026-12-29")
    assert symbols.expiry_of("BAJAJ-AUTO29DEC261000.5PE") == ("BAJAJ-AUTO", "2026-12-29")
    for share in ("RELIANCE", "M&M", "", "NIFTY31FEB26FUT", "NIFTY24XXX26FUT"):
        assert symbols.expiry_of(share) is None
    assert symbols.future_symbol("NIFTY", "2027-01-25") == "NIFTY25JAN27FUT"
    assert symbols.india_date(ms("2026-11-24 00:00:00")) == "2026-11-24"
    assert symbols.india_date(ms("2026-11-23 23:59:59")) == "2026-11-23"


def test_the_alert_must_come_from_the_strategys_chart():
    alert = m.Alert(position="long", fired_s=1, exchange="NSE", ticker="NIFTY1!")
    assert symbols.chart_matches(None, alert) and symbols.chart_matches("", alert)
    assert symbols.chart_matches("NSE:NIFTY1!", alert) and symbols.chart_matches(
        "nse:nifty1!", alert
    )
    assert not symbols.chart_matches("NSE:BANKNIFTY1!", alert)
    assert not symbols.chart_matches("BSE:NIFTY1!", alert)
    assert not symbols.chart_matches("NSE:NIFTY1!", m.Alert(position="long", fired_s=1))


def test_futures_underlying_is_what_resolve_needs():
    make = lambda **k: m.Strategy(id=1, name="S", **k)  # noqa: E731
    assert symbols.futures_underlying(make(underlying="NIFTY")) == "NIFTY"
    assert symbols.futures_underlying(make(kind="same", chart="NSE:NIFTY2!")) == "NIFTY"
    assert symbols.futures_underlying(make(kind="same", chart="NSE:CNXFINANCE1!")) == "FINNIFTY"
    assert symbols.futures_underlying(make(kind="same", chart="NSE:RELIANCE")) is None
