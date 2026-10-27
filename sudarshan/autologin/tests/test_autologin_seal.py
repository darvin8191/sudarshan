"""Sealed requests (cross-design X0, X9): a size limit per route, and a nonce book that refuses
when full instead of forgetting a nonce that could still be replayed."""

from __future__ import annotations

import pytest

from sudarshan.autologin.handoff import (
    MAX_SEALED_BYTES,
    SEAL_TTL_S,
    HandoffRejected,
    NonceBook,
    NoncesFull,
    open_sealed,
    seal,
)
from sudarshan.vault import ALERTS_API_INFO, HANDOFF_INFO, fernet_for

PEPPER = "c3" * 32  # fake
T0 = 1_792_557_000


class Clock:
    def __init__(self, at: float = 0.0):
        self.at = at

    def __call__(self) -> float:
        return self.at


def test_the_default_limit_is_unchanged_and_a_route_may_raise_its_own():
    fernet = fernet_for(PEPPER, ALERTS_API_INFO)
    big = seal(fernet, "create", now=T0, note="x" * 20_000)
    assert MAX_SEALED_BYTES < len(big) < 64 * 1024
    with pytest.raises(HandoffRejected, match="size"):
        open_sealed(fernet, big, op="create", seen={}, now=T0)
    opened = open_sealed(fernet, big, op="create", seen={}, now=T0, max_bytes=64 * 1024)
    assert opened["note"] == "x" * 20_000
    with pytest.raises(HandoffRejected, match="size"):
        open_sealed(fernet, b"x" * (MAX_SEALED_BYTES + 1), op="create", seen={}, now=T0)


def test_a_seal_of_one_purpose_never_opens_under_another():
    blob = seal(fernet_for(PEPPER, HANDOFF_INFO), "state", now=T0)
    with pytest.raises(HandoffRejected, match="seal"):
        open_sealed(fernet_for(PEPPER, ALERTS_API_INFO), blob, op="state", seen={}, now=T0)


def test_a_nonce_book_refuses_a_replay_and_refuses_when_full_without_forgetting():
    fernet, clock = fernet_for(PEPPER, ALERTS_API_INFO), Clock()
    book = NonceBook(2, clock=clock)
    first, second, third = (seal(fernet, "state", now=T0) for _ in range(3))
    open_sealed(fernet, first, op="state", seen=book, now=T0)
    with pytest.raises(HandoffRejected, match="replay"):
        open_sealed(fernet, first, op="state", seen=book, now=T0)
    open_sealed(fernet, second, op="state", seen=book, now=T0)
    with pytest.raises(NoncesFull):  # a TTL cache would have dropped the first nonce here
        open_sealed(fernet, third, op="state", seen=book, now=T0)
    with pytest.raises(HandoffRejected, match="replay"):
        open_sealed(fernet, first, op="state", seen=book, now=T0)  # still remembered
    assert len(book) == 2
    clock.at = 2 * SEAL_TTL_S + 0.001  # the first two can no longer be replayed
    assert len(book) == 0 and list(book) == []
    assert open_sealed(fernet, third, op="state", seen=book, now=T0 + 1)["op"] == "state"
    assert len(book) == 1 and book.maxsize == 2


def test_a_nonce_book_needs_room_for_one():
    with pytest.raises(ValueError):
        NonceBook(0)
