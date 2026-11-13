"""AL1: the shared pacer, with the lanes as parameters (cross-design Y18, Y43).

Pure: a fake clock, no event loop, no network. The owner bot's own lanes are tested in
``sudarshan/alerts/tests/test_alerts_pacer_lanes.py``; here the class itself, and the family bot's
construction (one lane, Telegram's own limit of one message a second to a chat).
"""

from __future__ import annotations

import pytest

from sudarshan.common.pacer import Lane, Pacer


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def owner_like(clock: Clock, **limits) -> Pacer:
    """Four lanes in an order of service, the shape the owner bot uses."""
    return Pacer(
        (
            Lane("orders"),
            Lane("market", 12, yields_to=("orders",)),
            Lane("notices", 1, yields_to=("orders",)),
            Lane("reports", 4, yields_to=("orders", "market")),
        ),
        gap_s=limits.get("gap_s", 1.1),
        per_minute=limits.get("per_minute", 20),
        reserved={"orders": 8},
        clock=clock,
    )


def test_a_pacer_needs_named_lanes():
    with pytest.raises(ValueError):
        Pacer((), gap_s=1)
    with pytest.raises(ValueError):
        Pacer((Lane("a"), Lane("a")), gap_s=1)
    with pytest.raises(ValueError):
        Pacer((Lane("a", yields_to=("b",)),), gap_s=1)
    with pytest.raises(ValueError):
        Pacer((Lane("a"),), gap_s=1, reserved={"b": 1})
    with pytest.raises(KeyError):
        Pacer((Lane("a"),), gap_s=1).allowed_at("b")


def test_the_gap_holds_across_lanes():
    clock = Clock()
    pacer = owner_like(clock)
    assert pacer.allowed_at("orders") == 0.0
    pacer.took("market")
    for lane in ("orders", "market", "notices", "reports"):
        assert pacer.allowed_at(lane) == pytest.approx(clock.now + 1.1)
    clock.now += 1.1
    assert pacer.allowed_at("orders") <= clock.now


def test_a_lanes_own_cap_a_minute():
    clock = Clock()
    pacer = owner_like(clock, gap_s=0.0)
    pacer.took("notices")
    assert pacer.allowed_at("notices") == pytest.approx(clock.now + 60.0)
    assert pacer.allowed_at("orders") <= clock.now  # the cap is the notice lane's, not the others'
    clock.now += 30
    assert pacer.allowed_at("notices") == pytest.approx(clock.now + 30.0)
    clock.now += 31
    assert pacer.allowed_at("notices") <= clock.now


def test_the_reserved_lane_keeps_its_share_whatever_the_others_do():
    clock = Clock()
    pacer = owner_like(clock, gap_s=0.0)
    for _ in range(12):  # market at its cap
        pacer.took("market")
    assert pacer.allowed_at("market") > clock.now
    # the others together have used the 12 they may: reports (own cap 4) and notices wait, too
    assert pacer.allowed_at("reports") > clock.now
    assert pacer.allowed_at("notices") > clock.now
    # but the order lane has its 8 of the 20
    for _ in range(8):
        assert pacer.allowed_at("orders") <= clock.now
        pacer.took("orders")
    assert pacer.allowed_at("orders") > clock.now  # the 20 a minute are used


def test_a_report_takes_two_slots():
    clock = Clock()
    pacer = owner_like(clock, gap_s=0.0)
    pacer.took("reports", 2)
    pacer.took("reports", 2)
    assert pacer.allowed_at("reports") == pytest.approx(clock.now + 60.0)  # 4 slots = 2 reports
    assert pacer.allowed_at("notices") <= clock.now


def test_the_waiting_callbacks_and_who_gives_way_to_whom():
    clock = Clock()
    pacer = owner_like(clock)
    assert not any(pacer.blocked(lane) for lane in pacer.lanes)
    flags = {"orders": False, "market": False}
    pacer.set_waiting("orders", lambda: flags["orders"])
    pacer.set_waiting("market", lambda: flags["market"])
    flags["market"] = True
    assert [lane for lane in pacer.lanes if pacer.blocked(lane)] == ["reports"]
    flags["orders"] = True
    assert [lane for lane in pacer.lanes if pacer.blocked(lane)] == ["market", "notices", "reports"]
    assert not pacer.blocked("orders")  # the first lane gives way to nobody
    assert pacer.waiting("orders") and not pacer.waiting("notices")


def test_flushing_the_reserved_lane_ignores_what_the_others_sent():
    clock = Clock()
    pacer = owner_like(clock, gap_s=0.0, per_minute=5)
    pacer.reserved["orders"] = 2
    for _ in range(3):
        pacer.took("market")
    assert pacer.allowed_at("orders") <= clock.now  # 3 of 5 used
    pacer.took("orders")
    pacer.took("orders")
    assert pacer.allowed_at("orders") > clock.now  # 5 of 5
    pacer.flushing_reserved = True
    assert pacer.allowed_at("orders") <= clock.now  # only its own 2 count now


def test_limits_are_read_when_asked():
    clock = Clock()
    gap = {"s": 1.0}
    pacer = Pacer((Lane("a"),), gap_s=lambda: gap["s"], clock=clock)
    pacer.took("a")
    assert pacer.allowed_at("a") == pytest.approx(clock.now + 1.0)
    gap["s"] = 5.0
    assert pacer.allowed_at("a") == pytest.approx(clock.now + 5.0)


def test_a_clock_set_back_does_not_leave_old_starts_forever():
    clock = Clock()
    pacer = owner_like(clock)
    pacer.took("orders")
    clock.now += 61
    assert pacer.allowed_at("orders") == 0.0  # a minute on, the start is forgotten


def test_the_family_bots_pacer_is_one_lane_at_telegrams_own_limit():
    """P6 F8 builds one per chat from this same class: one message a second, no minute budget."""
    clock = Clock()
    chat = Pacer((Lane("alert"), Lane("digest", yields_to=("alert",))), gap_s=1.0, clock=clock)
    assert chat.allowed_at("alert") == 0.0
    chat.took("alert")
    assert chat.allowed_at("digest") == pytest.approx(clock.now + 1.0)
    for _ in range(30):  # no 20-a-minute cap sized for the owner bot's /notify limiter
        clock.now += 1.0
        assert chat.allowed_at("alert") <= clock.now
        chat.took("alert")
    chat.set_waiting("alert", lambda: True)
    assert chat.blocked("digest")
