"""Alert definitions: defaults, the checks in the owner's words, and what the engine runs."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from sudarshan.alerts.market import spec
from sudarshan.alerts.market.spec import SpecError
from sudarshan.autologin.schedule import IST

NOW = int(datetime(2026, 10, 21, 10, 0, tzinfo=IST).timestamp() * 1000)
SBIN = {
    "instrument_key": "NSE_EQ|INE062A01020",
    "symbol": "SBIN",
    "exchange": "NSE",
    "tick_size": 0.05,
}
NIFTY = {
    "instrument_key": "NSE_INDEX|Nifty 50",
    "symbol": "NIFTY",
    "exchange": "NSE_INDEX",
    "tick_size": None,
}
FUT = {
    "instrument_key": "NSE_FO|51714",
    "symbol": "NIFTY25NOV26FUT",
    "exchange": "NFO",
    "tick_size": "0.10",
    "expiry": "2026-11-25",
}


def ms(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=IST).timestamp() * 1000)


def price(op="crosses_above", level="100", **more):
    return {"kind": "price", "params": {"op": op, "level": level, **more.pop("params", {})}, **more}


def test_a_price_alert_with_its_defaults():
    row = spec.check(SBIN | price(), NOW)
    assert row == {
        "instrument_key": "NSE_EQ|INE062A01020",
        "symbol": "SBIN",
        "exchange": "NSE",
        "tick_size": "0.05",
        "expiry": None,
        "kind": "price",
        "params": {"op": "crosses_above", "level": "100", "source": "ltp"},
        "frequency": "once",
        "candle_min": None,
        "cooldown_s": 0,
        "ends_at": ms("2026-12-21T00:00"),  # 60 days on: it works through 20 Dec
        "note": "",
        "channels": None,
    }


def test_an_index_moves_in_hundredths_and_an_fo_contract_ends_at_expiry():
    assert spec.check(NIFTY | price(level="25900"), NOW)["tick_size"] == "0.01"
    row = spec.check(FUT | price(level="26000"), NOW)
    assert row["ends_at"] is None and row["expiry"] == "2026-11-25" and row["tick_size"] == "0.1"
    alert = spec.Alert.of(row | {"id": 3, "version": 1, "status": "active"})
    assert alert.expires_at == ms("2026-11-25T15:40")  # after the F&O close on the expiry day


@pytest.mark.parametrize(
    ("ends", "expected"),
    [
        ("2026-10-25", ms("2026-10-26T00:00")),
        ("2026-10-21T10:30:00+05:30", ms("2026-10-21T10:30")),
        ("2026-10-21T10:30:00", ms("2026-10-21T10:30")),  # no offset: IST
        (None, None),
    ],
)
def test_end_dates(ends, expected):
    assert spec.check(SBIN | price(ends=ends), NOW)["ends_at"] == expected


@pytest.mark.parametrize(
    ("fields", "field", "words"),
    [
        (SBIN | {"exchange": "MCX"} | price(), "instrument", "Market alerts work on NSE, BSE, NFO"),
        ({**SBIN, "instrument_key": "MCX_FO|1"} | price(), "instrument", "Market alerts work on"),
        ({**SBIN, "symbol": " "} | price(), "instrument", "Pick the instrument again"),
        ({**SBIN, "tick_size": None} | price(), "instrument", "has no tick size in the master"),
        ({**FUT, "tick_size": "0.0025"} | price(), "instrument", "steps smaller than a paisa"),
        (SBIN | {"kind": "volume"}, "kind", "This kind of alert is not available yet."),
        (SBIN | price(op="touches"), "op", "Pick the check: crosses above"),
        (SBIN | price(level="0"), "level", "Enter the price level, a number above 0."),
        (SBIN | price(level="-5"), "level", "Enter the price level"),
        (SBIN | price(level="0.009"), "level", "Enter a price level of at least 0.01."),
        (SBIN | price(level="abc"), "level", "Enter the price level"),
        (SBIN | price(params={"source": "mid"}), "source", "Pick the price to watch"),
        (NIFTY | price(params={"source": "bid"}), "source", "An index has no bid or ask."),
        (SBIN | price(frequency="hourly"), "frequency", "Pick how often"),
        (SBIN | price(frequency="per_candle"), "candle", "Pick a candle of 1, 5, 15, 30"),
        (SBIN | price(frequency="at_close", candle=3), "candle", "Pick a candle"),
        (SBIN | price(frequency="per_candle", candle=True), "candle", "Pick a candle"),
        (SBIN | price(cooldown_s=-1), "cooldown_s", "from 0 to 24 hours"),
        (SBIN | price(cooldown_s=90000), "cooldown_s", "from 0 to 24 hours"),
        (SBIN | price(cooldown_s=1.5), "cooldown_s", "in whole seconds"),
        (SBIN | price(cooldown_s=float("nan")), "cooldown_s", "from 0 to 24 hours"),
        (SBIN | price(cooldown_s="60"), "cooldown_s", "in whole seconds"),
        (SBIN | price(ends="soon"), "ends", "Pick an end date, or no end."),
        (SBIN | price(ends=12345), "ends", "Pick an end date, or no end."),
        (SBIN | price(ends="2026-10-20"), "ends", "That end date has already passed."),
        (SBIN | price(note=7), "note", "A note is text."),
        (SBIN | price(note="x" * 121), "note", "A note can be at most 120 characters."),
        (SBIN | price(channels={"telegram": True}), "channels", "Pick where to send it"),
    ],
)
def test_refusals_name_the_field_and_say_what_to_do(fields, field, words):
    with pytest.raises(SpecError) as caught:
        spec.check(fields, NOW)
    assert caught.value.field == field and words in caught.value.message


@pytest.mark.parametrize("op", ["at_or_above", "at_or_below"])
def test_every_time_on_a_level_check_needs_a_minute_between_alerts(op):
    fields = SBIN | price(op=op, frequency="every_time", cooldown_s=59)
    with pytest.raises(SpecError) as caught:
        spec.check(fields, NOW)
    name = spec.OP_WORDS[op]
    assert caught.value.message == f"Every time on '{name}' needs at least 1 minute between alerts."
    assert spec.check(fields | {"cooldown_s": 60}, NOW)["cooldown_s"] == 60
    assert spec.check(SBIN | price(frequency="every_time"), NOW)["cooldown_s"] == 0  # crossings


def test_candles_note_and_channels_are_normalised():
    row = spec.check(
        SBIN
        | price(
            frequency="per_candle",
            candle="day",
            note="  book   half ",
            channels={"telegram": False, "app": True},
        ),
        NOW,
    )
    assert row["candle_min"] == spec.DAY and row["note"] == "book half"
    assert row["channels"] == {"telegram": False, "app": True}
    assert spec.check(SBIN | price(frequency="at_close", candle_min=15), NOW)["candle_min"] == 15
    assert spec.check(SBIN | price(candle=15), NOW)["candle_min"] is None  # once has no candle


def test_alert_of_compiles_the_edges_once():
    row = spec.check(SBIN | price(op="crosses", level="100.03"), NOW)
    alert = spec.Alert.of(row | {"id": 7, "version": 2, "status": "active"})
    assert (alert.cond.mode, alert.cond.direction) == (spec.CROSS, spec.EITHER)
    assert (alert.cond.up, alert.cond.down) == (10003, 10003)  # in paise, whatever the tick
    off = spec.Alert.of(
        spec.check(SBIN | price(op="crosses", level="100.033"), NOW)
        | {"id": 7, "version": 2, "status": "active"}
    )
    assert (off.cond.up, off.cond.down) == (10004, 10003)  # off the paisa: each way's next one
    assert alert.segment == "NSE_EQ" and alert.cooldown_ms == 0 and alert.expires_at is None
    level = spec.Alert.of(
        spec.check(SBIN | price(op="at_or_below", level="25,900"), NOW)
        | {"id": 1, "version": 1, "status": "active"}
    )
    assert (level.cond.mode, level.cond.direction, level.cond.down) == (
        spec.LEVEL,
        spec.DOWN,
        2590000,
    )


def test_the_lowest_level_is_one_paisa_whatever_the_tick():
    row = spec.check(SBIN | price(op="at_or_below", level="0.01"), NOW)
    alert = spec.Alert.of(row | {"id": 1, "version": 1, "status": "active"})
    assert (alert.cond.up, alert.cond.down) == (1, 1)


def test_a_contract_is_refused_once_it_has_expired():
    """An F&O contract trades until its segment's close on the expiry day."""
    expiring = {**FUT, "expiry": "2026-10-21"}
    assert spec.check(expiring | price(level="26000"), ms("2026-10-21T15:39"))["expiry"]
    for now in ("2026-10-21T15:40", "2026-10-22T09:00"):
        with pytest.raises(SpecError) as caught:
            spec.check(expiring | price(level="26000"), ms(now))
        assert (caught.value.field, caught.value.message) == (
            "instrument",
            "This contract expired on 21 Oct. Pick one that is still trading.",
        )


def test_a_fall_below_a_paisa_is_refused_when_the_form_sends_the_price():
    move = {"kind": "move", "params": {"direction": "down", "percent": "99.5"}}
    assert spec.check(SBIN | move | {"price": 2.0}, NOW)["params"]["percent"] == "99.5"  # 0.01
    with pytest.raises(SpecError) as caught:
        spec.check(SBIN | move | {"price": 1.98}, NOW)  # 0.0099
    assert caught.value.field == "percent" and "below ₹0.01" in caught.value.message
    assert spec.check(SBIN | move, NOW)  # no price sent: the engine notes it at run time
    either = move | {"params": {"direction": "either", "percent": "99.9"}}
    assert spec.check(SBIN | either | {"price": 2.0}, NOW)  # the rise can still happen


def test_edits_that_change_the_condition():
    old = spec.check(SBIN | price(), NOW)
    assert not spec.changes_condition(old, old | {"note": "trail it", "cooldown_s": 300})
    assert not spec.changes_condition(
        old, old | {"ends_at": None, "channels": {"telegram": True, "app": False}}
    )
    assert spec.changes_condition(old, old | {"params": {**old["params"], "level": "102"}})
    assert spec.changes_condition(old, old | {"frequency": "every_time"})
    assert spec.changes_condition(old, old | {"instrument_key": "NSE_EQ|INE002A01018"})
    assert spec.status_after_edit("stopped", True) == "active"
    assert spec.status_after_edit("paused", True) == "paused"
    assert spec.status_after_edit("stopped", False) == "stopped"
    assert spec.status_after_edit("active", True) == "active"


def test_a_stored_date_object_and_iso_text_both_read():
    assert spec._date(date(2026, 11, 25)) == spec._date("2026-11-25T00:00:00")
