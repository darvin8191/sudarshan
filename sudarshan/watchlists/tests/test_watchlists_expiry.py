"""The one rule for when an expiring contract stops (M8.10): the regime of the day, for the helper,
for the Index futures list (W12) and for follow rows (W15)."""

from __future__ import annotations

from datetime import date, datetime

import pytest
import watchlists_follow_master as fmaster
from presets_master import V, index_of, keys
from presets_master import ms as pms
from test_watchlists_follow import World, item, one, rule
from test_watchlists_follow import ms as fms

from sudarshan.watchlists import dynamic, expiry, numfmt, presets


def at(day: str, hm: str) -> int:
    return fms(f"{day}T{hm}:00")


# -- the helper -----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("day", "index", "stops"),
    [
        ("2026-12-04", True, "15:40"),
        ("2026-12-04", False, "15:40"),
        ("2026-12-10", True, "15:30"),
        ("2026-12-10", False, "15:45"),
        ("2026-12-06", True, "15:40"),  # the last day of the first regime
        ("2026-12-07", True, "15:30"),  # the first day of the second
        ("2026-12-07", False, "15:45"),
    ],
)
def test_the_stop_by_the_regime_of_the_day(day, index, stops):
    for exchange in ("NFO", "BFO"):
        got = expiry.stop_ms(day, index=index, exchange=exchange)
        assert numfmt.hm(got) == stops
        assert got == expiry.stop_ms(date.fromisoformat(day), index=index, exchange=exchange)


def test_a_special_session_day_takes_the_calendars_end_in_either_regime():
    for day in ("2026-11-08", "2026-12-10"):
        for index in (True, False):
            end = at(day, "15:55")
            assert expiry.stop_ms(day, index=index, window_end_ms=end) == end


def test_the_underlyings_that_are_indices():
    assert all(expiry.is_index(n) for n in ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY"))
    assert not expiry.is_index("RELIANCE") and not expiry.is_index("NIFTYBEES")


def test_close_ms_asks_the_one_helper_and_a_dated_close_wins():
    assert dynamic.close_ms("2026-12-10", index=True) == expiry.stop_ms("2026-12-10", index=True)
    assert dynamic.close_ms("2026-12-10") == expiry.stop_ms("2026-12-10", index=False)
    one_day = {"2026-12-10": at("2026-12-10", "13:00")}
    assert dynamic.close_ms("2026-12-10", one_day, index=True) == one_day["2026-12-10"]


# -- Index futures (W12) ----------------------------------------------------------------------------
def _futures_at(when: str, closes=None):
    entry = presets.entry("index_futures")
    prepared = entry.prepare(index_of(V["master"]))
    return entry.members(prepared, pms(when), closes or {})


@pytest.mark.parametrize(
    ("when", "nifty"),
    [
        ("2026-12-29T15:29:59+05:30", "NIFTY29DEC26FUT"),
        ("2026-12-29T15:30:00+05:30", "NIFTY26JAN27FUT"),
    ],
)
def test_index_futures_roll_at_15_30_from_7_dec(when, nifty):
    got = _futures_at(when)
    assert "NFO:" + nifty in keys(got.rows)


def test_index_futures_roll_at_15_40_before_7_dec_and_valid_until_follows_the_regime():
    before = _futures_at("2026-11-24T15:39:59+05:30")
    assert "NFO:NIFTY24NOV26FUT" in keys(before.rows)
    after = _futures_at("2026-11-24T15:40:00+05:30")
    assert "NFO:NIFTY29DEC26FUT" in keys(after.rows)
    # the soonest close among the rows now: SENSEX's expiry of 26 Nov, 15:40
    assert numfmt.hm(after.valid_until) == "15:40"
    december = _futures_at("2026-12-08T10:00:00+05:30")
    assert numfmt.hm(december.valid_until) == "15:30"  # NIFTY and BANKNIFTY, 29 Dec


def test_index_futures_on_a_special_session_day_use_the_calendars_end():
    held = _futures_at(
        "2026-12-29T15:35:00+05:30", {"2026-12-29": pms("2026-12-29T15:55:00+05:30")}
    )
    assert "NFO:NIFTY29DEC26FUT" in keys(held.rows)
    assert numfmt.hm(held.valid_until) == "15:55"


# -- follow rows (W15) ------------------------------------------------------------------------------
@pytest.fixture
def december(monkeypatch):
    monkeypatch.setattr(fmaster, "EXPIRIES", ("01-DEC-26", "08-DEC-26", "15-DEC-26"))


@pytest.mark.parametrize(
    ("now", "expiry_on", "until", "words"),
    [
        ("2026-12-01T15:39:59", "2026-12-01", "15:40", "Now: NIFTY 25,900 CE, 1 Dec 2026"),
        ("2026-12-01T15:40:00", "2026-12-08", "15:30", "Rolled to 8 Dec 2026 at 15:40"),
        ("2026-12-08T15:29:59", "2026-12-08", "15:30", "Now: NIFTY 25,900 CE, 8 Dec 2026"),
        ("2026-12-08T15:30:00", "2026-12-15", "15:30", "Rolled to 15 Dec 2026 at 15:30"),
    ],
)
def test_resolve_rolls_and_says_the_time_of_the_regime(december, now, expiry_on, until, words):
    got = one(World(), rule(), item(at=None), now)
    assert got["ce"]["expiry"] == expiry_on
    assert numfmt.hm(got["valid_until"]) == until
    assert got["words"].startswith(words)


def test_a_future_row_rolls_by_the_same_rule(december):
    r = rule(kind="month_future", expiry_rule="nearest")
    before = one(World(), r, item(), "2026-12-01T15:39:59")
    assert before["future"]["symbol"] == "NIFTY01DEC26FUT"
    assert numfmt.hm(before["valid_until"]) == "15:40"
    rolled = one(World(), r, item(), "2026-12-01T15:40:00")
    assert rolled["future"]["symbol"] == "NIFTY08DEC26FUT"
    assert rolled["words"] == "Rolled to 8 Dec 2026 at 15:40"
    after = one(World(), r, item(), "2026-12-08T15:30:00")
    assert after["future"]["symbol"] == "NIFTY15DEC26FUT"
    assert after["words"] == "Rolled to 15 Dec 2026 at 15:30"


def test_a_special_session_day_moves_the_roll_of_a_row_in_the_new_regime(december):
    w = World(closes={"2026-12-08": "2026-12-08T18:00:00"})
    still = one(w, rule(), item(at=None), "2026-12-08T16:00:00")
    assert still["ce"]["expiry"] == "2026-12-08" and still["valid_until"] == at(
        "2026-12-08", "18:00"
    )
    assert datetime.fromtimestamp(still["valid_until"] / 1000).year == 2026
