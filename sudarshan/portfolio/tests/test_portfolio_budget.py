"""The service's own cap on Upstox reads: 2 a second, 30 a minute, 200 in 30 minutes per API."""

from __future__ import annotations

from sudarshan.portfolio.budget import LIMITS, Budget


class Mono:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_the_figures_are_a_tenth_of_upstoxs_rounded_for_a_page():
    assert LIMITS == ((2, 1.0), (30, 60.0), (200, 1800.0))


def test_two_a_second_then_the_bucket_refills():
    mono = Mono()
    budget = Budget(clock=mono)
    assert [budget.take("positions") for _ in range(3)] == [True, True, False]
    mono.t += 0.49
    assert not budget.take("positions")
    mono.t += 0.02  # half a second: one token is back
    assert budget.take("positions") and not budget.take("positions")


def test_the_minute_and_the_half_hour_bound_a_steady_pace():
    """A bucket is full at the start and refills evenly, so a window holds at most its size plus
    what comes back during it."""
    mono = Mono()
    budget = Budget(clock=mono)
    taken = 0
    for _ in range(60):
        mono.t += 0.5  # the per-second bucket would allow all of these
        taken += budget.take("holdings")
    assert 30 <= taken <= 45  # 30 held, 15 refilled in the 30 seconds
    assert budget.take("funds")  # another API has its own buckets
    for _ in range(900):
        mono.t += 2.0  # 1800 seconds at 30 a minute is far under the minute bucket
        taken += budget.take("holdings")
    assert taken <= 45 + 200 + 200  # the 30-minute bucket: 200 held, 200 refilled


def test_a_refusal_spends_nothing_and_a_pause_is_exactly_sixty_seconds():
    mono = Mono()
    budget = Budget(clock=mono)
    budget.pause("funds")
    mono.t += 59.9
    assert budget.paused_for("funds") > 0 and not budget.take("funds")
    mono.t += 0.2
    assert budget.paused_for("funds") == 0 and budget.take("funds")
    assert budget.take("funds") and not budget.take("funds")  # two, not one fewer
