"""M0.1: a test's login stamp cannot straddle the 03:00 IST rollover.

CI run 45071655734 stamped a login at 02:59:59 IST and sent its next request at 03:00:01: the
session check ends every login made before the most recent rollover, so the request was refused as
signed out. The first test reproduces that with the check's own clock held, the way a run that
crosses the rollover does it, and the rest hold the helper that prevents it. No test here waits.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import pytz
from flask import Flask, session

from sudarshan.tests import session_clock
from utils import session as session_utils

IST = pytz.timezone("Asia/Kolkata")


def at(hour: int, minute: int, second: int = 0) -> datetime:
    return IST.localize(datetime(2026, 11, 23, hour, minute, second))


class FakeTime:
    """One clock for the helper and for the session check, moved by the helper's own sleep."""

    def __init__(self, start: datetime):
        self.moment = start
        self.slept: list[float] = []

    def now(self, tz=None):
        return self.moment.astimezone(tz) if tz is not None else self.moment.replace(tzinfo=None)

    def clock(self) -> datetime:
        return self.moment

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.moment += timedelta(seconds=seconds)


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("DISABLE_SESSION_EXPIRY", "false")
    monkeypatch.delenv("SESSION_EXPIRY_TIME", raising=False)
    monkeypatch.setattr(session_utils, "session_ended_elsewhere", lambda: False)
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test only", TESTING=True)

    def start(moment: datetime) -> FakeTime:
        fake = FakeTime(moment)

        class Held(datetime):
            @classmethod
            def now(cls, tz=None):
                return fake.now(tz)

        monkeypatch.setattr(session_utils, "datetime", Held)
        return fake

    def admitted(stamp: str) -> bool:
        with app.test_request_context():
            session.update(user="owner", logged_in=True, login_time=stamp)
            return session_utils.is_session_valid()

    return start, admitted


def test_a_stamp_taken_at_02_59_59_is_refused_at_03_00_01_the_way_ci_saw_it(world):
    start, admitted = world
    fake = start(at(2, 59, 59))
    old_way = fake.clock().isoformat()  # what every helper did: the wall clock, as it was
    fake.moment = at(3, 0, 1)
    assert admitted(old_way) is False


def test_the_helper_waits_out_the_rollover_and_its_stamp_holds(world):
    start, admitted = world
    fake = start(at(2, 59, 59))
    stamp = session_clock.login_stamp(clock=fake.clock, sleep=fake.sleep)
    assert fake.slept == [1 + session_clock.SETTLE]
    fake.moment += timedelta(seconds=2)  # the request after it
    assert datetime.fromisoformat(stamp) >= at(3, 0, 0)
    assert admitted(stamp) is True


@pytest.mark.parametrize(
    "moment",
    [at(2, 58, 59), at(3, 0, 0), at(3, 0, 1), at(9, 20), at(23, 59, 59), at(0, 0, 1)],
    ids=[
        "a minute before",
        "on the rollover",
        "just after",
        "morning",
        "midnight eve",
        "after midnight",
    ],
)
def test_the_helper_stamps_now_without_waiting_away_from_the_rollover(world, moment):
    start, admitted = world
    fake = start(moment)
    stamp = session_clock.login_stamp(clock=fake.clock, sleep=fake.sleep)
    assert fake.slept == [] and datetime.fromisoformat(stamp) == moment
    assert admitted(stamp) is True


@pytest.mark.parametrize("seconds_before", [59, 30, 5, 1])
def test_every_stamp_inside_the_margin_waits_and_holds(world, seconds_before):
    start, admitted = world
    fake = start(at(3, 0, 0) - timedelta(seconds=seconds_before))
    stamp = session_clock.login_stamp(clock=fake.clock, sleep=fake.sleep)
    assert len(fake.slept) == 1
    assert datetime.fromisoformat(stamp) > at(3, 0, 0)
    assert admitted(stamp) is True


def test_the_helper_follows_a_rollover_time_the_product_is_set_to(world, monkeypatch):
    start, admitted = world
    monkeypatch.setenv("SESSION_EXPIRY_TIME", "04:30")
    fake = start(at(4, 29, 50))
    stamp = session_clock.login_stamp(clock=fake.clock, sleep=fake.sleep)
    assert len(fake.slept) == 1 and admitted(stamp) is True
