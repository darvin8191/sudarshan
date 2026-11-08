"""M18.1: the feed's stall clock counts from the session's open, never from before it.

Until now a socket that heard nothing in the minutes before 09:15 was reconnected at 09:15:00, the
second continuous trading began: the watchdog measured the silence from the last message, and the
last message was a quarter of an hour old. Upstox is quiet before the open, so the reconnect cost a
fresh authorize call and a gap in the opening ticks, for nothing.

The silence is now the shorter of two ages: the time since the last message, and the time since the
watchdog's span began (09:15 on a usual day; the day's own start plus the pre-open's length on a
special one). Everything else the watchdog does is as it was: nothing subscribed, outside the
span, the wait for a reopened day's first trade, and a real stall after the open.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import feed_fakes as ff
import pytest

from sudarshan.feed import service
from sudarshan.feed.config import Settings
from sudarshan.feed.service import STALL_S, FeedService
from sudarshan.feed.window import MarketWindow, ist_date

WEDNESDAY = (2026, 10, 21)
MUHURAT = "2026-11-08 14:05-15:40  # Muhurat\n"


def wednesday(h: int, m: int = 0, s: int = 0) -> float:
    return ff.ist(*WEDNESDAY, h, m, s)


def sunday(h: int, m: int = 0, s: int = 0) -> float:
    return ff.ist(2026, 11, 8, h, m, s)


def _window(tmp_path, extra: str = "", holidays: str = "2026-11-09\n") -> MarketWindow:
    (tmp_path / "nse_holidays.txt").write_text(holidays)
    (tmp_path / "nse_extra_trading_days.txt").write_text(extra)
    return MarketWindow(
        tmp_path / "nse_holidays.txt", extra_days_file=tmp_path / "nse_extra_trading_days.txt"
    )


# ---- the window: how long the watchdog's span has been running ---------------------------------


@pytest.mark.parametrize(
    ("at", "age"),
    [
        (wednesday(9, 14, 59), None),  # the pre-open: no watchdog
        (wednesday(9, 15), 0.0),  # the open, to the second
        (wednesday(9, 15, 30), 30.0),
        (wednesday(10), 2700.0),
        (wednesday(15, 39, 59), 23_099.0),
        (wednesday(15, 40), None),  # the F&O close
        (wednesday(8, 58), None),
        (ff.ist(2026, 11, 7, 10), None),  # a Saturday
        (ff.ist(2026, 11, 9, 10), None),  # a listed holiday
    ],
)
def test_the_age_runs_from_the_start_of_continuous_trading(tmp_path, at, age):
    window = _window(tmp_path)
    assert window.core_age(at) == age
    assert window.in_core_session(at) is (age is not None)


@pytest.mark.parametrize(
    ("at", "age"),
    [
        (sunday(14, 5), None),  # the special day's own start is its pre-open, not the open
        (sunday(14, 19, 59), None),
        (sunday(14, 20), 0.0),  # the pre-open's length later: continuous trading
        (sunday(14, 35), 900.0),
        (sunday(15, 39, 59), 4799.0),
        (sunday(15, 40), None),
    ],
)
def test_a_special_days_age_runs_from_its_own_continuous_trading(tmp_path, at, age):
    assert _window(tmp_path, MUHURAT).core_age(at) == age


def test_each_span_of_a_day_that_trades_anyway_has_its_own_start(tmp_path):
    evening = _window(tmp_path, "2026-10-21 18:00-19:40  # an evening session\n")
    assert evening.core_age(wednesday(10)) == 2700.0  # the usual session, from 09:15
    assert evening.core_age(wednesday(17)) is None  # between the two
    assert evening.core_age(wednesday(18, 14, 59)) is None
    assert evening.core_age(wednesday(18, 15)) == 0.0  # not 09:15: the evening's own open
    assert evening.core_age(wednesday(18, 16)) == 60.0
    overlapping = _window(tmp_path, "2026-10-21 14:05-15:40  # inside the usual session\n")
    assert overlapping.core_age(wednesday(14, 30)) == 18_900.0  # one span, from 09:15


def test_a_reopened_day_without_hours_runs_the_usual_span(tmp_path):
    reopened = _window(tmp_path, "2026-11-08  # a date alone\n")
    assert reopened.core_age(sunday(9, 14)) is None
    assert reopened.core_age(sunday(9, 15, 30)) == 30.0
    assert reopened.waits_for_first_trade(sunday(10))  # the first-trade rule is still its own


# ---- the verdict: the silence is the shorter of the two ages ----------------------------------


def _watching(feed_env, quiet_s: float, keys: str | None = None) -> FeedService:
    """A service whose socket is up with its keys subscribed and last heard ``quiet_s`` ago."""
    env = feed_env(None, SUDARSHAN_FEED_ALWAYS_ON="0")
    if keys is not None:
        Path(env["SUDARSHAN_FEED_KEYS"]).write_text(keys)
    svc = FeedService(Settings.from_env(env))
    svc._live = True
    svc._last_msg = time.monotonic() - quiet_s
    return svc


def test_the_quiet_before_the_open_is_not_counted(feed_env):
    async def scenario():
        svc = _watching(feed_env, quiet_s=900)  # connected at 09:00, nothing since
        assert svc.subs.desired()
        assert not svc._stalled(wednesday(9, 14, 59))  # not continuous trading yet
        assert not svc._stalled(wednesday(9, 15))  # the old clock said stalled here
        assert not svc._stalled(wednesday(9, 15, 30))
        assert not svc._stalled(wednesday(9, 16, 59))
        assert not svc._stalled(wednesday(9, 17))  # exactly STALL_S after the open
        assert svc._stalled(wednesday(9, 17, 1))  # the open itself brought nothing: a stall

    asyncio.run(scenario())


def test_a_stall_after_the_open_is_still_a_stall(feed_env):
    async def scenario():
        for quiet_s, stalled in (
            (30, False),
            (STALL_S - 1, False),
            (STALL_S + 1, True),
            (900, True),
        ):
            svc = _watching(feed_env, quiet_s)
            assert svc._stalled(wednesday(11)) is stalled, quiet_s
        # A message after the open restarts the count from that message, as before.
        svc = _watching(feed_env, quiet_s=STALL_S + 1)
        assert svc._stalled(wednesday(11))
        svc._last_msg = time.monotonic()
        assert not svc._stalled(wednesday(11))

    asyncio.run(scenario())


def test_the_shorter_age_decides_from_either_side(feed_env):
    async def scenario():
        # Heard 121 s ago but the session opened 30 s ago: the open is the later of the two.
        assert not _watching(feed_env, STALL_S + 1)._stalled(wednesday(9, 15, 30))
        # Opened 150 s ago but heard 30 s ago: the message is the later of the two.
        assert not _watching(feed_env, 30)._stalled(wednesday(9, 17, 30))
        # Both older than STALL_S: stalled.
        assert _watching(feed_env, STALL_S + 1)._stalled(wednesday(9, 17, 30))

    asyncio.run(scenario())


def test_a_special_days_pre_open_is_not_counted_either(feed_env, tmp_path):
    (tmp_path / "nse_holidays.txt").write_text("2026-11-09\n")
    (tmp_path / "nse_extra_trading_days.txt").write_text(MUHURAT)

    async def scenario():
        svc = _watching(feed_env, quiet_s=900)  # connected at 14:03, nothing since
        assert not svc._stalled(sunday(14, 19, 59))
        assert not svc._stalled(sunday(14, 20))  # continuous trading begins
        assert not svc._stalled(sunday(14, 22))
        assert svc._stalled(sunday(14, 22, 1))

    asyncio.run(scenario())


def test_the_rest_of_the_watchdog_is_as_it_was(feed_env, tmp_path):
    (tmp_path / "nse_holidays.txt").write_text("2026-11-09\n")
    (tmp_path / "nse_extra_trading_days.txt").write_text("2026-11-08  # a date alone\n")

    async def scenario():
        quiet = 900
        svc = _watching(feed_env, quiet)
        svc._live = False  # the desired set is not on a socket yet
        assert not svc._stalled(wednesday(11))
        svc = _watching(feed_env, quiet, keys="")
        assert not svc.subs.desired()
        assert not svc._stalled(wednesday(11))  # nothing subscribed: nothing to hear
        # A reopened day at the usual hours waits for its own first trade, long after the open.
        svc = _watching(feed_env, quiet)
        assert not svc._stalled(sunday(11))
        svc._traded_on = ist_date(sunday(11))
        assert svc._stalled(sunday(11))
        svc._traded_on = ist_date(sunday(11) - 2 * 86_400)  # Friday's trade does not count
        assert not svc._stalled(sunday(11))

    asyncio.run(scenario())


# ---- the service: a real socket, a clock the test moves ---------------------------------------


@pytest.mark.parametrize(
    ("before_open", "open_at", "extra"),
    [
        (wednesday(9, 14, 59), wednesday(9, 15), ""),
        (sunday(14, 19, 59), sunday(14, 20), MUHURAT),  # a special day's own continuous trading
    ],
)
def test_a_quiet_pre_open_does_not_reconnect_at_the_open_and_a_quiet_open_does(
    feed_env, fast, monkeypatch, tmp_path, before_open, open_at, extra
):
    monkeypatch.setattr(service, "STALL_S", 0.2)
    (tmp_path / "nse_holidays.txt").write_text("2026-11-09\n")
    (tmp_path / "nse_extra_trading_days.txt").write_text(extra)
    clock = [before_open]

    async def scenario():
        async with ff.FakeUpstox() as fake:  # sends nothing unless told: Upstox before the open
            env = feed_env(fake, SUDARSHAN_FEED_ALWAYS_ON="0")
            svc = FeedService(Settings.from_env(env), clock=lambda: clock[0])
            run = asyncio.create_task(svc.run())
            await ff.wait_until(lambda: svc.health()["state"] == "connected")
            await asyncio.sleep(1.0)  # five times STALL_S of silence, none of it trading
            assert fake.authorize_calls == 1
            clock[0] = open_at  # continuous trading begins, and the clock stands there
            await asyncio.sleep(1.0)  # five times STALL_S of real silence, none after the open
            assert fake.authorize_calls == 1, "the pre-open's silence was counted"
            clock[0] = open_at + 0.5  # more than STALL_S after the open, and still nothing
            await ff.wait_until(lambda: fake.authorize_calls > 1)  # now it is a stall
            svc.stop("test")
            await asyncio.wait_for(run, 10)

    asyncio.run(scenario())
