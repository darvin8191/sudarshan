"""Review H2: the sign-in work is bounded inside the family service, whatever nginx does.

At most ``PASSWORD_SLOTS`` password checks are taken at a time, running or waiting; past them a
sign-in is answered busy at once, before any name is looked up; a check that waited too long is
dropped unrun; and the one listener holds at most ``MAX_CONNECTIONS`` connections. Every burst here
is started together behind a barrier, so the checks that run are the ones the bound let in, not the
ones that happened to arrive first. On the code before the bound every one of these is red: 30
sign-ins ran 30 checks one after another, and the last caller waited for all of them.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest
from family_net import running, settings
from family_testkit import close, open_store

from sudarshan.family import accounts, listener
from sudarshan.family.accounts import PASSWORD_SLOTS, Accounts, Hasher
from sudarshan.family.errors import FamilyError
from sudarshan.family.listener import Listener, Response, Router
from sudarshan.family.service import FamilyService

PHONE = accounts.device_from("Mozilla/5.0 (Linux; Android 15; K) Chrome/142.0.0.0 Mobile", "a" * 64)
PASSWORD = "a long synthetic passphrase"


def fast_argon():
    from argon2 import PasswordHasher, Type

    return PasswordHasher(time_cost=1, memory_cost=8, parallelism=1, hash_len=16, salt_len=16,
                          type=Type.ID)  # fmt: skip


class Slow(Hasher):
    """A check that takes ``seconds`` on the password thread, counted as it starts."""

    def __init__(self, seconds: float = 0.2, **kw):
        super().__init__(fast_argon(), **kw)
        self.seconds, self.runs = seconds, 0
        self.gate: threading.Event | None = None

    def verify_now(self, stored, password):
        self.runs += 1
        if self.gate is not None:
            self.gate.wait(10)
        time.sleep(self.seconds)
        return super().verify_now(stored, password)


class Counting(Hasher):
    """The production argon2id, counting the checks that ran."""

    def __init__(self):
        super().__init__()
        self.runs = 0

    def verify_now(self, stored, password):
        self.runs += 1
        return super().verify_now(stored, password)


@pytest.fixture
def store(tmp_path):
    made = open_store(tmp_path / "state")
    yield made
    close(made)
    accounts.stop_hasher()


async def burst(acc: Accounts, count: int) -> list[object]:
    """``count`` sign-ins of names nobody has, one name each (so that no name's own count locks,
    review M2), released together; each one's outcome or refusal."""
    barrier = asyncio.Barrier(count)

    async def one(n: int):
        await barrier.wait()
        try:
            return await acc.login(f"nobody_{n:02d}", PASSWORD, "000000", PHONE)
        except FamilyError as exc:
            return exc

    return await asyncio.gather(*(one(n) for n in range(count)))


def codes(found: list[object]) -> list[str]:
    return [x.code if isinstance(x, FamilyError) else "ok" for x in found]


# ---- the slots ------------------------------------------------------------------------------------
def test_a_burst_runs_at_most_the_slots_and_the_rest_are_busy_at_once(store):
    hasher = Slow(0.2)
    acc = Accounts(store, clock=lambda: 1_763_100_000_000, hasher=hasher)

    async def scenario():
        began = time.monotonic()
        first = asyncio.create_task(burst(acc, 30))
        await asyncio.sleep(0.05)  # the burst is in: every slot is taken
        late_began = time.monotonic()
        try:
            await acc.login("someone_else", PASSWORD, "000000", PHONE)
            late = "ok"
        except FamilyError as exc:
            late = exc.code
        late_took = time.monotonic() - late_began
        found = await first
        return codes(found), late, late_took, time.monotonic() - began

    found, late, late_took, took = asyncio.run(scenario())
    assert found.count("busy") >= 30 - PASSWORD_SLOTS - 2, found  # the rest, at once
    assert set(found) <= {"busy", "signin_failed"}
    assert hasher.runs <= PASSWORD_SLOTS + 2, hasher.runs  # never 30 one after another
    assert (late, late_took < 0.5) == ("busy", True), (late, late_took)
    assert took < 0.2 * (PASSWORD_SLOTS + 2) + 1.5, took
    assert hasher.taken == 0  # every slot came back


def test_busy_is_said_before_any_name_is_looked_up(store):
    hasher = Slow(0.0)
    acc = Accounts(store, clock=lambda: 1_763_100_000_000, hasher=hasher)
    reads = []
    original = store.read

    async def counted(fn):
        reads.append(1)
        return await original(fn)

    store.read = counted  # type: ignore[method-assign]
    hasher.taken = hasher.slots  # every slot is taken by others
    for handle in ("nobody_here", "member_a", "x"):
        with pytest.raises(FamilyError) as caught:
            asyncio.run(acc.login(handle, PASSWORD, "000000", PHONE))
        assert (caught.value.code, caught.value.status) == ("busy", 503)
        assert str(caught.value) == "The family view is busy. Wait a minute, then try again."
    with pytest.raises(FamilyError) as caught:
        asyncio.run(acc.join("a" * 27, "member_b", PASSWORD, "000000", PHONE))
    assert caught.value.code == "busy"
    assert reads == [] and hasher.runs == 0


def test_a_check_that_waited_too_long_is_dropped_unrun(store):
    hasher = Slow(0.0, wait_s=0.3)
    hasher.gate = threading.Event()  # the first check holds the thread past the wait
    acc = Accounts(store, clock=lambda: 1_763_100_000_000, hasher=hasher)

    async def scenario():
        first = asyncio.create_task(acc.login("nobody_one", PASSWORD, "0", PHONE))
        await asyncio.sleep(0.05)
        second = asyncio.create_task(acc.login("nobody_two", PASSWORD, "0", PHONE))
        await asyncio.sleep(0.5)  # the second has waited past its 0.3 s
        hasher.gate.set()
        return await asyncio.gather(first, second, return_exceptions=True)

    first, second = asyncio.run(scenario())
    assert (first.code, second.code) == ("signin_failed", "busy")
    assert hasher.runs == 1  # the second never ran
    assert hasher.taken == 0


# ---- over the listener, with the production argon2id (the review's probe P2) ---------------------
def test_thirty_unknown_names_cost_at_most_the_slots_and_a_late_sign_in_is_answered(tmp_path):
    hasher = Counting()
    csrf = "c" * 43

    async def post(port: int, body: dict, barrier: asyncio.Barrier | None = None):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        raw = json.dumps(body).encode()
        head = (
            "POST /family/api/login HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            f"Cookie: __Host-sudarshan_family_csrf={csrf}\r\nX-Family-CSRF: {csrf}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(raw)}\r\n\r\n"
        )
        if barrier is not None:
            await barrier.wait()
        writer.write(head.encode() + raw)
        await writer.drain()
        answer = await asyncio.wait_for(reader.read(), 60)
        writer.close()
        return int(answer.split(b" ", 2)[1])

    async def scenario():
        service = FamilyService(settings(tmp_path), _keys(), hasher=hasher)
        async with running(service):
            port = service.listener.port
            hasher.runs = 0  # the dummy hash made at start is not a sign-in
            barrier = asyncio.Barrier(30)

            def body(n: int) -> dict:  # one name each: no name's own count locks (review M2)
                return {"handle": f"nobody_{n:02d}", "password": "x" * 12, "totp": "000000"}

            tasks = [asyncio.create_task(post(port, body(n), barrier)) for n in range(30)]
            await asyncio.sleep(0.05)
            began = time.monotonic()
            late = await post(port, body(99))
            late_took = time.monotonic() - began
            statuses = await asyncio.gather(*tasks)
            return statuses, late, late_took

    try:
        statuses, late, late_took = asyncio.run(scenario())
    finally:
        close(None)
    assert late in (401, 503) and late_took < 2.0, (late, late_took)
    assert set(statuses) <= {401, 503} and statuses.count(503) >= 30 - PASSWORD_SLOTS - 2
    assert hasher.runs <= PASSWORD_SLOTS + 2, hasher.runs


def _keys():
    from family_net import keys

    return keys()


# ---- the listener's connections -------------------------------------------------------------------
class Echo(Router):
    async def http(self, request):
        return Response.ok({"path": request.path})


def test_past_its_connections_the_listener_answers_busy_at_once_and_drains(tmp_path):
    assert listener.MAX_CONNECTIONS == 200

    async def scenario():
        door = Listener(Echo(), max_connections=3)
        await door.start(0)
        idle = [await asyncio.open_connection("127.0.0.1", door.port) for _ in range(3)]
        for _ in range(100):
            if len(door._connections) == 3:
                break
            await asyncio.sleep(0.01)
        assert len(door._connections) == 3
        reader, writer = await asyncio.open_connection("127.0.0.1", door.port)
        began = time.monotonic()
        writer.write(b"POST /family/api/login HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n\r\n{}")
        await writer.drain()
        answer = await asyncio.wait_for(reader.read(), 5)  # read in full, never reset
        took = time.monotonic() - began
        writer.close()
        head, _, body = answer.partition(b"\r\n\r\n")
        assert head.split(b"\r\n")[0] == b"HTTP/1.1 503 Service Unavailable" and took < 1.0
        assert json.loads(body)["message"] == (
            "The family view is busy. Wait a minute, then try again."
        )
        assert door.refused == 1
        for _, w in idle:
            w.close()
        for _ in range(200):
            if not door._connections:
                break
            await asyncio.sleep(0.01)
        reader, writer = await asyncio.open_connection("127.0.0.1", door.port)
        writer.write(b"GET /family/api/me HTTP/1.1\r\nHost: x\r\n\r\n")
        await writer.drain()
        assert (await asyncio.wait_for(reader.read(), 5)).startswith(b"HTTP/1.1 200")
        writer.close()
        await door.close()
        assert not door._connections and not door._refusing

    asyncio.run(scenario())
