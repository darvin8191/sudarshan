"""The owner's words: golden texts for Telegram and the app (market_vectors.json "words")."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from test_market_vectors import VECTORS, fields_of, make_row, ms

from sudarshan.alerts.market import book, clock, conditions, engine, spec, words
from sudarshan.autologin.schedule import IST

TODAY = date(2026, 10, 21)
DAY = "2026-10-21"


def alert_of(vector: dict) -> spec.Alert:
    return spec.Alert.of(make_row(vector, fields_of(vector)))


def fire_of(alert: spec.Alert, given: dict) -> engine.Fire:
    def ticks(text):  # the vectors' prices in paise
        return None if text is None else int(Decimal(text) * 100)

    def span(pair):
        return None if pair is None else (ms(DAY, pair[0]), ms(DAY, pair[1]))

    return engine.Fire(
        alert.id, "1:v1:once", 20261021, 0, ms(DAY, "10:42:05"), ms(DAY, "10:42:05"),
        ticks(given["value"]), ticks(given["level"]), spec.UP if given["direction"] == "up" else spec.DOWN,
        1, ticks(given.get("previous")), span(given.get("found_later")), given.get("opening", False),
        ticks(given.get("base")), ticks(given.get("price")), span(given.get("span")),
    )  # fmt: skip


WORDS = VECTORS["words"]


@pytest.mark.parametrize("vector", WORDS, ids=[v["id"] for v in WORDS])
def test_golden_words(vector):
    alert = alert_of(vector)
    fire = fire_of(alert, vector["fire"])
    title, detail = words.describe(alert, fire, TODAY)
    if "title" in vector:
        assert (title, detail) == (vector["title"], vector["detail"])
    assert words.line(alert, fire, TODAY) == vector["line"]


def test_every_line_is_plain_text():
    for vector in WORDS:
        alert = alert_of(vector)
        text = words.line(alert, fire_of(alert, vector["fire"]), TODAY)
        assert text.isprintable() and not any(ch in text for ch in "*_`[]")
        assert all(ord(ch) < 0x2000 or ch in "₹" for ch in text)  # no symbols or emojis


def at(clock_text: str) -> datetime:
    hh, mm, ss = (int(x) for x in clock_text.split(":"))
    return datetime(2026, 10, 21, hh, mm, ss, tzinfo=IST)


def test_the_message_has_the_header_its_lines_and_the_time():
    lines = ["Alert: NIFTY crossed above 25,900.00: now 25,903.15 (index)"]
    assert words.message(lines, at("10:42:05"), at("10:42:06")) == (
        "सुदर्शन / SUDARSHAN\n"
        "Alert: NIFTY crossed above 25,900.00: now 25,903.15 (index)\n"
        "10:42:05 IST"
    )
    text = words.message(lines * 2, at("10:42:05"), at("10:43:05"), dropped=1234)
    assert text.splitlines()[-2:] == [
        "1,234 more market alerts could not be listed here. The Alerts page has them.",
        "10:42:05 to 10:43:05 IST",
    ]


def test_pause_lines_each_way():
    start, end = ms(DAY, "10:41:05"), ms(DAY, "10:44:10")
    assert words.pause_started(start) == (
        "Market alerts paused since 10:41:05 IST: the price feed is reconnecting."
    )
    assert words.pause_ended(start, end) == (
        "Market alerts paused 10:41:05 to 10:44:10 IST: the price feed was reconnecting. "
        "A level crossed and crossed back inside that time was not seen."
    )
    assert words.pause_ended(start, end, "service").startswith(
        "Market alerts paused 10:41:05 to 10:44:10 IST: market alerts were not running."
    )
    assert words.tells_pause(start, start + 60_001, active=1)
    assert not words.tells_pause(start, start + 60_000, active=1)
    assert not words.tells_pause(start, end, active=0)
    assert words.TEST_LINE == "Test: market alerts reach this chat (sent from the Alerts page)."


@pytest.mark.parametrize(
    ("code", "moment", "text"),
    [
        ("fired_once", "2026-10-21T10:42:30", "Fired once at 10:42 on 21 Oct"),
        ("ended", "2026-12-21T00:00:00", "Ended on 20 Dec"),
        ("ended", "2026-10-21T10:30:00", "Ended at 10:30 on 21 Oct"),
        ("expired", "2026-11-25T15:40:00", "Contract expired on 25 Nov"),
        (
            "fire_limit",
            "2026-10-21T10:01:41",
            "Fired 50 times today; paused so Telegram is not flooded",
        ),
    ],
)
def test_status_reasons(code, moment, text):
    when = int(datetime.fromisoformat(moment).replace(tzinfo=IST).timestamp() * 1000)
    assert words.status_reason(engine.Reason(code, when)) == text


def test_an_unknown_reason_is_a_bug_not_words():
    with pytest.raises(ValueError):
        words.status_reason(engine.Reason("arm", 0))


def test_notes_on_an_alert():
    at_ten = ms(DAY, "10:00:00")
    assert words.note_words(engine.Reason(engine.NOT_CHECKED, at_ten)) == (
        "Not checked: the price feed paused during the 10:00 candle"
    )
    assert words.note_words(engine.Reason(engine.NOT_JUDGED, ms(DAY, "15:15:00"))) == (
        "Not checked: the close of the 15:15 candle on 21 Oct"
    )
    assert words.note_words(engine.Reason(conditions.WAITING_CLOSE, at_ten)) == (
        "Waiting for the previous close"
    )
    assert words.note_words(engine.Reason(conditions.WAITING_OPEN, 0)) == "Waiting for today's open"
    assert (
        words.note_words(engine.Reason(conditions.WAITING_VWAP, 0)) == "Waiting for the day's VWAP"
    )
    assert words.note_words(engine.Reason(book.BASE_CHANGED, 0)).startswith("The previous close")
    assert words.note_words(engine.Reason(conditions.UNREACHABLE, 0)) == (
        "This fall would take the price below 0.01, so it cannot happen today"
    )


@pytest.mark.parametrize(
    ("code", "opens", "text"),
    [
        (clock.CLOSED_TODAY, None, "Market closed today"),
        (clock.CLOSED_TODAY, "2026-10-22T09:15:00", "Market closed today · checks from Thu 09:15"),
        (clock.OUTSIDE_HOURS, "2026-10-21T14:20:00", "Market closed · checks from 14:20"),
        (clock.OUTSIDE_HOURS, "2026-10-26T09:15:00", "Market closed · checks from Mon 09:15"),
        (clock.OUTSIDE_HOURS, "2026-11-09T09:15:00", "Market closed · checks from 9 Nov 09:15"),
        (clock.NOT_OPEN_YET, None, "The exchange has not opened this segment yet"),
        (clock.CLOSED_BY_EXCHANGE, None, "Closed by the exchange"),
        (clock.OK, None, "Watching · Real-time"),
    ],
)
def test_the_status_line(code, opens, text):
    now = ms(DAY, "09:00:00")
    opens_at = (
        None
        if opens is None
        else int(datetime.fromisoformat(opens).replace(tzinfo=IST).timestamp() * 1000)
    )
    assert words.check_words(code, now=now, opens_at=opens_at) == text


@pytest.mark.parametrize(
    ("state", "age_s", "text"),
    [
        ("backoff", 1, "the price feed is reconnecting"),
        ("connecting", 1, "the price feed is reconnecting"),
        ("connected", 1, "the price feed is reconnecting"),  # its subscriptions are not live yet
        (
            "token_rejected",
            1,
            "Upstox refused the price feed's token: put the current Analytics Token in the "
            "feed's token file",
        ),
        (
            "no_token",
            1,
            "the price feed has no token: put the Analytics Token in the feed's token file",
        ),  # fmt: skip
        ("stopped", 1, "the price feed is stopped: start sudarshan-feed again"),
        ("closed", 1, "the price feed's calendar says the market is closed"),
        ("connected", 13, "the price feed is not answering: check that sudarshan-feed is running"),
        (None, 0, "the price feed is not answering: check that sudarshan-feed is running"),
    ],
)
def test_the_status_line_says_why_the_feed_is_down(state, age_s, text):
    """A stopped or refused feed needs the owner: it is never called "reconnecting"."""
    now = ms(DAY, "10:42:00")
    feed = None if state is None else clock.Feed(state, False, now - age_s * 1000)
    assert words.check_words(clock.FEED_DOWN, now=now, feed=feed) == f"Not checking · {text}"
    since = words.check_words(clock.FEED_DOWN, now=now, since=ms(DAY, "10:41:05"), feed=feed)
    assert since == f"Not checking since 10:41:05 · {text}"


@pytest.mark.parametrize(
    ("op", "level", "text"),
    [
        ("crosses_above", "100.03", "Checks at ₹100.05 (nearest tick)"),
        ("at_or_below", "100.03", "Checks at ₹100.00 (nearest tick)"),
        ("crosses", "100.03", "Checks at ₹100.05 going up and ₹100.00 going down (nearest ticks)"),
        ("crosses_above", "100.05", None),
    ],
)
def test_where_an_off_grid_level_checks(op, level, text):
    alert = alert_of({"alert": {"params": {"op": op, "level": level}}})
    assert words.checks_at(alert) == text


def test_a_move_without_a_known_base_says_its_threshold():
    alert = alert_of({"alert": {"kind": "move", "params": {"direction": "up", "percent": "2"}}})
    fire = fire_of(alert, {"value": "102.00", "level": "102.00", "direction": "up"})
    assert words.describe(alert, fire, TODAY) == ("SBIN up 2% on the day", "now ₹102.00")
