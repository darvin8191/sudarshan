"""The exchange clock: candles anchored at the session's start, the gate's edges, pauses."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from sudarshan.alerts.market import clock, sessions
from sudarshan.alerts.market.clock import Feed, Market, Pause
from sudarshan.autologin.schedule import IST

DAY = date(2026, 10, 21)
CASH = sessions.session("NSE_EQ", DAY, trading_day=True)
FO = sessions.session("NSE_FO", DAY, trading_day=True)


def at(hhmmss: str) -> int:
    hh, mm, ss = hhmmss.split(":")
    moment = datetime(2026, 10, 21, int(hh), int(mm), tzinfo=IST)
    return int(moment.timestamp()) * 1000 + round(float(ss) * 1000)


def hm(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, IST).strftime("%H:%M")


@pytest.mark.parametrize(
    ("session", "minutes", "when", "expected"),
    [
        (CASH, 15, "09:15:00", (0, "09:15", "09:30")),
        (CASH, 15, "10:14:59.999", (3, "10:00", "10:15")),
        (CASH, 15, "10:15:00", (4, "10:15", "10:30")),
        (CASH, 15, "15:29:59.999", (24, "15:15", "15:30")),
        (FO, 15, "15:35:00", (25, "15:30", "15:40")),  # F&O's short last candle
        (CASH, 60, "15:20:00", (6, "15:15", "15:30")),  # an hour candle cut at the close
        (FO, 60, "15:20:00", (6, "15:15", "15:40")),
        (CASH, 1, "09:15:59.999", (0, "09:15", "09:16")),
        (CASH, 5, "09:20:00", (1, "09:20", "09:25")),
        (CASH, 30, "12:44:00", (6, "12:15", "12:45")),
        (CASH, 0, "12:44:00", (0, "09:15", "15:30")),  # the day
    ],
)
def test_candles_are_anchored_at_the_session_start(session, minutes, when, expected):
    k, start, end = clock.candle(session, minutes, at(when))
    assert (k, hm(start), hm(end)) == expected


def test_a_special_day_anchors_candles_at_its_own_start():
    window = (14 * 60 + 5, 15 * 60 + 40)
    muhurat = sessions.session("NSE_FO", date(2026, 11, 8), trading_day=True, window=window)
    k, start, end = clock.candle(muhurat, 15, muhurat.start + 31 * 60_000)
    assert (k, hm(start), hm(end)) == (2, "14:50", "15:05")
    k, start, end = clock.candle(muhurat, 15, muhurat.end - 1)
    assert (k, hm(start), hm(end)) == (5, "15:35", "15:40")


def test_paused_between_takes_only_pauses_over_ten_seconds():
    seen, now = at("10:00:00"), at("10:10:00")
    ten = Pause(at("10:01:00"), at("10:01:10"))
    longer = Pause(at("10:02:00"), at("10:02:10.001"))
    assert clock.paused_between((ten,), seen, now) is None
    assert clock.paused_between((ten, longer), seen, now) == (longer.start, longer.end)


def test_paused_between_spans_every_pause_between_two_values():
    first, second = Pause(at("10:01:00"), at("10:02:00")), Pause(at("10:05:00"), at("10:06:00"))
    span = clock.paused_between((first, second), at("10:00:00"), at("10:07:00"))
    assert span == (first.start, second.end)
    assert clock.paused_between((first, second), at("10:03:00"), at("10:07:00")) == (
        second.start,
        second.end,
    )
    assert clock.paused_between((first,), at("10:02:00"), at("10:07:00")) is None  # seen after
    assert clock.paused_between((second,), at("10:00:00"), at("10:05:00")) is None  # not yet
    assert clock.paused_between((first,), None, at("10:07:00")) is None  # nothing seen before


def test_a_pause_still_going_counts_to_the_value_judged():
    going = Pause(at("10:01:00"))
    assert clock.paused_between((going,), at("10:00:00"), at("10:01:30")) == (
        going.start,
        at("10:01:30"),
    )
    assert clock.paused_during((going,), at("10:00:00"), at("10:15:00"))


@pytest.mark.parametrize(
    ("pause", "inside"),
    [
        (("09:05:00", "09:14:00", "feed"), None),  # before the open: nothing trades then
        (("08:00:00", "09:20:00", "service"), ("09:15:00", "09:20:00", "service")),
        (("09:10:00", None, "feed"), ("09:15:00", None, "feed")),  # still going
        (("10:00:00", "10:01:00", "feed"), ("10:00:00", "10:01:00", "feed")),
        (("15:29:00", "15:45:00", "feed"), ("15:29:00", "15:30:00", "feed")),  # to the close
        (("15:31:00", None, "service"), None),  # after the close
        (("09:05:00", "09:15:00", "feed"), None),  # ends as the session opens: wholly before
    ],
)
def test_only_the_part_of_a_pause_inside_the_session_counts(pause, inside):
    start, end, cause = pause
    given = Pause(at(start), at(end) if end else None, cause)
    found = clock.within((given,), CASH.start, CASH.end)
    if inside is None:
        assert found == ()
    else:
        begin, stop, why = inside
        assert found == (Pause(at(begin), at(stop) if stop else None, why),)


@pytest.mark.parametrize(
    ("pause", "touches"),
    [
        (("10:05:00", "10:06:00"), True),
        (("09:59:00", "10:00:30"), True),  # into the candle's first seconds
        (("10:14:30", "10:20:00"), True),
        (("09:58:00", "10:00:00"), False),  # ends as the candle starts
        (("10:15:00", "10:16:00"), False),  # starts as the candle ends
        (("10:05:00", "10:05:10"), False),  # ten seconds: not a pause
    ],
)
def test_paused_during_a_candle(pause, touches):
    span = Pause(at(pause[0]), at(pause[1]))
    assert clock.paused_during((span,), at("10:00:00"), at("10:15:00")) is touches


def test_gate_status_words_from_a_newer_exchange_code_close_the_segment():
    feed = Feed("connected", True, at("10:00:00"))
    world = Market(20261021, {"NSE_EQ": CASH}, status={"NSE_EQ": "6"}, feed=feed)
    assert clock.gate(
        world, "NSE_EQ", at("10:00:00"), at("10:00:00"), at("10:00:00"), trade=True
    ) == (clock.CLOSED_BY_EXCHANGE)


def test_gate_without_health_yet_is_feed_down():
    world = Market(20261021, {"NSE_EQ": CASH})
    moment = at("10:00:00")
    assert clock.gate(world, "NSE_EQ", moment, moment, moment, trade=True) == clock.FEED_DOWN


AUCTION_DAY = sessions.sessions_of(DAY, trading_day=True)


def auction_market(now: int, **more) -> Market:
    more.setdefault("feed", Feed("connected", True, now))
    return Market(20261021, AUCTION_DAY, 20261019, auction=frozenset({"NSE_EQ|X"}), **more)


@pytest.mark.parametrize(
    ("at", "recv", "now", "feed", "check"),
    [
        ("15:31:10", "15:31:10", "15:31:10", "connected", clock.AUCTION_PRINT),
        ("15:15:00", "15:15:01", "15:15:01", "connected", clock.AUCTION_PRINT),  # from 15:15
        ("15:14:59", "15:15:01", "15:15:01", "connected", clock.OUTSIDE_HOURS),  # continuous
        ("15:49:59", "15:49:59", "15:49:59", "connected", clock.AUCTION_PRINT),
        ("15:50:00", "15:50:00", "15:50:00", "connected", clock.OUTSIDE_HOURS),  # post-close
        ("15:31:10", "15:31:10", "15:31:10", "backoff", clock.FEED_DOWN),
        ("15:31:10", "15:31:10", "15:31:16", "connected", clock.STALE),
    ],
)
def test_the_closing_auctions_print_has_its_own_gate(at, recv, now, feed, check):
    market = auction_market(clock_ms(now), feed=Feed(feed, feed == "connected", clock_ms(now)),
                            status={"NSE_EQ": "NORMAL_CLOSE"})  # fmt: skip
    assert clock.auction(market, "NSE_EQ", clock_ms(at), clock_ms(recv), clock_ms(now)) == check


def test_a_stock_that_closes_by_auction_is_gated_on_its_own_session():
    now = clock_ms("15:16:00")
    market = auction_market(now, status={"NSE_EQ": "NORMAL_OPEN"})
    assert clock.gate(market, "NSE_EQ+auction", now, now, now, trade=True) == clock.OUTSIDE_HOURS
    assert clock.gate(market, "NSE_EQ", now, now, now, trade=True) == clock.OK
    early = clock_ms("09:14:00")
    pre = auction_market(early, status={"NSE_EQ": "PRE_OPEN_START"})
    assert clock.gate(pre, "NSE_EQ+auction", clock_ms("09:15:01"), clock_ms("09:15:01"),
                      clock_ms("09:15:01"), trade=True) == clock.NOT_OPEN_YET  # fmt: skip
    plain = Market(20261021, AUCTION_DAY, 20261019, feed=Feed("connected", True, now))
    assert clock.auction(plain, "NSE_INDEX", now, now, now) == clock.OUTSIDE_HOURS


def clock_ms(text: str) -> int:
    return at(text)
