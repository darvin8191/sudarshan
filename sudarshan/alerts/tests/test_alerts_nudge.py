"""AL2: the "orders changed" nudge to the family service and the journal (Y17, Y14).

The receivers are fakes behind httpx's mock transport (no socket); the owner of a port comes from
a fake /proc table, so the rule that the family listener must belong to another account is tested
on a host where every account is one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import httpx
import pytest
from cryptography.fernet import Fernet, InvalidToken

from sudarshan.alerts import loopback, nudge
from sudarshan.alerts.config import Settings
from sudarshan.alerts.nudge import (
    FAMILY_URL,
    JOURNAL_URL,
    PRINCIPAL,
    NudgeSender,
    OnceIn,
    Receiver,
    account_uid,
    nudge_kind,
    receivers_for,
)
from sudarshan.autologin.handoff import NonceBook, open_sealed
from sudarshan.tasks import cancel_and_wait

ME = os.getuid()
FAMILY_UID = ME + 4242  # the account sudarshan-family, another one
FAMILY_KEY, JOURNAL_KEY = Fernet.generate_key(), Fernet.generate_key()
HEADER = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid\n"


def row(port: int, uid: int, address: str = "0100007F", state: str = "0A") -> str:
    return (
        f"   0: {address}:{port:04X} 00000000:0000 {state} 00000000:00000000 00:00000000 "
        f"00000000 {uid:>5}        0 12345 1 0000000000000000 100 0 0 10 0\n"
    )


def proc(tmp_path, *rows: str):
    tcp = tmp_path / "tcp"
    tcp.write_text(HEADER + "".join(rows))
    return (tcp, tmp_path / "no-tcp6")


class Receiving:
    """A mock transport that keeps what was posted."""

    def __init__(self, status: int = 200, delay: float = 0.0, error: Exception | None = None):
        self.status, self.delay, self.error = status, delay, error
        self.posts: list[tuple[str, bytes]] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.posts.append((str(request.url), request.content))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return httpx.Response(self.status, json={"ok": True})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))

    def bodies(self, key: bytes) -> list[dict]:
        seen = NonceBook(100)
        return [
            open_sealed(Fernet(key), self_blob, op="nudge", seen=seen)
            for _, self_blob in self.posts
        ]


def family(uid=lambda: FAMILY_UID) -> Receiver:
    return Receiver("family", FAMILY_URL, Fernet(FAMILY_KEY), uid)


def journal() -> Receiver:
    return Receiver("journal", JOURNAL_URL, Fernet(JOURNAL_KEY), os.getuid)


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(nudge, "COALESCE_S", 0.2)
    monkeypatch.setattr(nudge, "TIMEOUT_S", 0.3)


def run(coro):
    return asyncio.run(coro)


async def running(sender: NudgeSender):
    task = asyncio.create_task(sender.run())
    await asyncio.sleep(0)
    return task


async def stop(sender: NudgeSender, task) -> None:
    await sender.close()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


# ---- what is sent ------------------------------------------------------------------------------


def test_the_body_is_sealed_under_the_nudge_purpose_and_carries_no_order_data(tmp_path, quick):
    async def scenario():
        fake = Receiving()
        tables = proc(tmp_path, row(5585, FAMILY_UID), row(5586, ME))
        async with fake.client() as client:
            sender = NudgeSender([family(), journal()], client, tables=tables)
            task = await running(sender)
            sender.note("orders", False, 1_700_000_000_123)
            await asyncio.sleep(0.1)
            await stop(sender, task)
        assert sorted(url for url, _ in fake.posts) == [FAMILY_URL, JOURNAL_URL]
        for url, blob in fake.posts:
            key = FAMILY_KEY if url == FAMILY_URL else JOURNAL_KEY
            other = JOURNAL_KEY if url == FAMILY_URL else FAMILY_KEY
            body = open_sealed(Fernet(key), blob, op="nudge", seen=NonceBook(10))
            assert body["principal"] == PRINCIPAL == "sudarshan-alerts"
            assert body["kind"] == "orders" and body["sandbox"] is False
            assert body["at"] == 1_700_000_000_123
            assert set(body) == {"op", "nonce", "principal", "kind", "sandbox", "at"}
            with pytest.raises(InvalidToken):  # the other purpose's key cannot open it
                Fernet(other).decrypt(blob)

    run(scenario())


def test_no_order_data_reaches_the_body_from_an_order_update(tmp_path, quick):
    update = {
        "type": "order_update", "orderid": "2610130012345", "symbol": "INFY", "exchange": "NSE",
        "action": "BUY", "quantity": "75", "filled_quantity": "75", "average_price": "1534.55",
        "price": "1534.55", "product": "CNC", "order_status": "complete", "mode": "live",
        "user_id": "owner",
    }  # fmt: skip

    async def scenario():
        fake = Receiving()
        tables = proc(tmp_path, row(5585, FAMILY_UID))
        async with fake.client() as client:
            sender = NudgeSender([family()], client, tables=tables)
            task = await running(sender)
            sender.note(nudge_kind(update), nudge.is_sandbox(update))
            await asyncio.sleep(0.1)
            await stop(sender, task)
        (blob,) = (blob for _, blob in fake.posts)
        body = json.loads(Fernet(FAMILY_KEY).decrypt(blob))
        assert set(body) == {"op", "nonce", "principal", "kind", "sandbox", "at"}
        # the nonce and the time are random digits; every other field is checked for the order
        fields = json.dumps({k: v for k, v in body.items() if k not in ("nonce", "at")})
        for secret in ("2610130012345", "INFY", "NSE", "BUY", "75", "1534.55", "complete", "owner"):
            assert secret not in fields
        assert body["kind"] == "holdings"

    run(scenario())


@pytest.mark.parametrize(
    ("update", "kind"),
    [
        ({"product": "CNC", "order_status": "complete"}, "holdings"),
        ({"product": "CNC", "order_status": "open", "filled_quantity": "5"}, "holdings"),
        ({"product": "cnc", "order_status": "completed"}, "holdings"),
        ({"product": "CNC", "order_status": "open"}, "orders"),
        ({"product": "CNC", "order_status": "rejected"}, "orders"),
        ({"product": "MIS", "order_status": "complete"}, "orders"),
        ({"product": "NRML", "order_status": "complete"}, "orders"),
        ({}, "orders"),
    ],
)
def test_holdings_only_when_a_cnc_fill_can_change_them(update, kind):
    assert nudge_kind({"orderid": "1", **update}) == kind


def test_sandbox_is_read_from_the_updates_mode():
    assert nudge.is_sandbox({"mode": "analyze"}) and nudge.is_sandbox({"mode": "ANALYZE"})
    assert not nudge.is_sandbox({"mode": "live"}) and not nudge.is_sandbox({})


# ---- coalescing, timeout, no retry ---------------------------------------------------------------


def test_updates_in_the_same_moment_are_one_nudge_and_holdings_wins(tmp_path, quick):
    async def scenario():
        fake = Receiving()
        async with fake.client() as client:
            sender = NudgeSender([family()], client, tables=proc(tmp_path, row(5585, FAMILY_UID)))
            task = await running(sender)
            for kind, sandbox in (("orders", True), ("holdings", True), ("orders", True)):
                sender.note(kind, sandbox)
            await asyncio.sleep(0.1)
            await stop(sender, task)
        (body,) = fake.bodies(FAMILY_KEY)
        assert body["kind"] == "holdings" and body["sandbox"] is True

    run(scenario())


def test_sandbox_is_true_only_when_every_update_in_the_nudge_was(tmp_path, quick):
    async def scenario():
        fake = Receiving()
        async with fake.client() as client:
            sender = NudgeSender([family()], client, tables=proc(tmp_path, row(5585, FAMILY_UID)))
            task = await running(sender)
            sender.note("orders", True)
            sender.note("orders", False)
            await asyncio.sleep(0.1)
            await stop(sender, task)
        assert fake.bodies(FAMILY_KEY)[0]["sandbox"] is False

    run(scenario())


def test_at_most_one_post_a_second_for_each_receiver(tmp_path, monkeypatch):
    monkeypatch.setattr(nudge, "COALESCE_S", 0.3)

    async def scenario():
        fake = Receiving()
        starts: dict[str, list[float]] = {FAMILY_URL: [], JOURNAL_URL: []}
        loop = asyncio.get_running_loop()
        original = fake.__call__

        async def timed(request):
            starts[str(request.url)].append(loop.time())
            return await original(request)

        tables = proc(tmp_path, row(5585, FAMILY_UID), row(5586, ME))
        async with httpx.AsyncClient(transport=httpx.MockTransport(timed)) as client:
            sender = NudgeSender([family(), journal()], client, tables=tables)
            task = await running(sender)
            for _ in range(30):  # an update every 20 ms
                sender.note("orders", False)
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.5)
            await stop(sender, task)
        for url, at in starts.items():
            assert 2 <= len(at) < 30, url  # coalesced, and nothing lost to silence
            gaps = [later - earlier for earlier, later in zip(at, at[1:], strict=False)]
            assert all(gap >= 0.3 - 0.02 for gap in gaps), (url, gaps)  # one a COALESCE_S at most

    run(scenario())


def test_the_post_times_out_and_is_not_retried(tmp_path, quick, caplog):
    async def scenario():
        fake = Receiving(delay=5.0)
        async with fake.client() as client:
            sender = NudgeSender([family()], client, tables=proc(tmp_path, row(5585, FAMILY_UID)))
            task = await running(sender)
            started = asyncio.get_running_loop().time()
            sender.note("orders", False)
            while sender.stats["failed"] == 0:
                await asyncio.sleep(0.02)
            assert asyncio.get_running_loop().time() - started < 1.0  # the 0.3 s timeout, not 5 s
            await asyncio.sleep(0.6)  # time to retry, if it did
            await stop(sender, task)
        assert len(fake.posts) == 1

    with caplog.at_level(logging.WARNING, logger="sudarshan.alerts"):
        run(scenario())
    assert "family" in caplog.text and "not delivered" in caplog.text


def test_a_refused_connection_is_not_retried_and_a_later_update_still_goes(tmp_path, quick):
    async def scenario():
        fake = Receiving(error=httpx.ConnectError("refused"))
        async with fake.client() as client:
            sender = NudgeSender([family()], client, tables=proc(tmp_path, row(5585, FAMILY_UID)))
            task = await running(sender)
            sender.note("orders", False)
            await asyncio.sleep(0.5)
            assert len(fake.posts) == 1  # no retry of that one
            sender.note("orders", False)
            await asyncio.sleep(0.3)
            await stop(sender, task)
        assert len(fake.posts) == 2

    run(scenario())


def test_a_slow_receiver_does_not_hold_the_other_one(tmp_path, quick):
    class Split(Receiving):
        async def __call__(self, request):
            self.posts.append((str(request.url), request.content))
            if str(request.url) == FAMILY_URL:
                await asyncio.sleep(5.0)
            return httpx.Response(200, json={})

    async def scenario():
        fake = Split()
        tables = proc(tmp_path, row(5585, FAMILY_UID), row(5586, ME))
        async with fake.client() as client:
            sender = NudgeSender([family(), journal()], client, tables=tables)
            task = await running(sender)
            sender.note("orders", False)
            await asyncio.sleep(0.1)
            assert sender.stats["posted"] == 1  # the journal's, while the family's waits
            await stop(sender, task)

    run(scenario())


class LosingCancel(Receiving):
    """A receiver whose connection loses the first cancel and then answers: httpx 0.28 with anyio
    4.14 can swallow a cancel while it connects (sudarshan/tasks.py), and the sending task then
    carries on to its next wait."""

    def __init__(self):
        super().__init__()
        self.entered: asyncio.Event | None = None
        self.lost = 0

    async def __call__(self, request):
        self.posts.append((str(request.url), request.content))
        self.entered.set()
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            self.lost += 1  # swallowed
        return httpx.Response(204)


def test_a_stop_with_a_nudge_in_flight_ends_in_bounded_time_when_a_cancel_is_lost(tmp_path):
    """M2: close() stops the senders with cancel_and_wait, never one cancel and a gather."""

    async def scenario():
        fake = LosingCancel()
        fake.entered = asyncio.Event()
        async with fake.client() as client:
            sender = NudgeSender([journal()], client, tables=proc(tmp_path, row(5586, ME)))
            task = await running(sender)
            sender.note("orders", False)
            await asyncio.wait_for(fake.entered.wait(), 2.0)
            try:
                began = asyncio.get_running_loop().time()
                await asyncio.wait_for(sender.close(), 2.0)
                assert asyncio.get_running_loop().time() - began < 1.5
                assert fake.lost == 1 and all(t.done() for t in sender._tasks)
            finally:
                await cancel_and_wait(task, *sender._tasks)

    run(scenario())


def test_one_cancel_of_the_running_sender_ends_it_when_a_cancel_is_lost(tmp_path):
    """M2: run() waits on its senders without gather, so one cancel of it is enough, and its own
    stop cancels each sender until it has ended."""

    async def scenario():
        fake = LosingCancel()
        fake.entered = asyncio.Event()
        async with fake.client() as client:
            sender = NudgeSender([journal()], client, tables=proc(tmp_path, row(5586, ME)))
            task = await running(sender)
            sender.note("orders", False)
            await asyncio.wait_for(fake.entered.wait(), 2.0)
            try:
                task.cancel()
                done, _ = await asyncio.wait({task}, timeout=2.0)
                assert task in done and task.cancelled()
                assert fake.lost == 1 and all(t.done() for t in sender._tasks)
            finally:
                await cancel_and_wait(task, *sender._tasks)

    run(scenario())


def test_a_failure_is_logged_once_in_ten_minutes(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(nudge, "COALESCE_S", 0.0)
    now = {"t": 1000.0}

    async def scenario():
        fake = Receiving(error=httpx.ConnectError("refused"))
        async with fake.client() as client:
            sender = NudgeSender(
                [family()], client, tables=proc(tmp_path, row(5585, FAMILY_UID)),
                clock=lambda: now["t"],
            )  # fmt: skip
            task = await running(sender)
            for number, step in enumerate((0, 5, 100, 599, 601, 700), start=1):
                now["t"] = 1000.0 + step  # seconds since the first failure
                sender.note("orders", False)
                while sender.stats["failed"] < number:
                    await asyncio.sleep(0.005)
            await stop(sender, task)
        return sender

    with caplog.at_level(logging.WARNING, logger="sudarshan.alerts"):
        sender = run(scenario())
    said = [r for r in caplog.records if "not delivered" in r.getMessage()]
    assert sender.stats["failed"] == 6
    assert len(said) == 2  # the first, and the first after ten minutes


def test_once_in_remembers_each_key_separately():
    now = {"t": 0.0}
    once = OnceIn(600.0, lambda: now["t"])
    assert once(("family", "down")) and not once(("family", "down"))
    assert once(("journal", "down"))
    now["t"] = 599.0
    assert not once(("family", "down"))
    now["t"] = 601.0
    assert once(("family", "down"))


# ---- who may hold the port ---------------------------------------------------------------------


def test_the_family_port_must_belong_to_the_family_account(tmp_path, quick):
    async def scenario(listener_uid):
        fake = Receiving()
        async with fake.client() as client:
            sender = NudgeSender([family()], client, tables=proc(tmp_path, row(5585, listener_uid)))
            task = await running(sender)
            sender.note("orders", False)
            await asyncio.sleep(0.1)
            await stop(sender, task)
        return fake, sender

    fake, sender = run(scenario(FAMILY_UID))
    assert len(fake.posts) == 1  # held by sudarshan-family: the right one
    fake, sender = run(scenario(ME))
    assert fake.posts == [] and sender.stats["refused"] == 1  # our own uid is the WRONG one here
    fake, sender = run(scenario(FAMILY_UID + 1))
    assert fake.posts == []  # any other account


def test_the_journal_port_must_belong_to_the_senders_own_account(tmp_path, quick):
    async def scenario(listener_uid):
        fake = Receiving()
        async with fake.client() as client:
            sender = NudgeSender(
                [journal()], client, tables=proc(tmp_path, row(5586, listener_uid))
            )
            task = await running(sender)
            sender.note("orders", False)
            await asyncio.sleep(0.1)
            await stop(sender, task)
        return fake

    assert len(run(scenario(ME)).posts) == 1
    assert run(scenario(FAMILY_UID)).posts == []


def test_a_port_shared_between_the_family_account_and_another_is_refused(tmp_path, quick):
    async def scenario():
        fake = Receiving()
        tables = proc(tmp_path, row(5585, FAMILY_UID), row(5585, ME, address="00000000"))
        async with fake.client() as client:
            sender = NudgeSender([family()], client, tables=tables)
            task = await running(sender)
            sender.note("orders", False)
            await asyncio.sleep(0.1)
            await stop(sender, task)
        return fake

    assert run(scenario()).posts == []


def test_nothing_listening_means_no_post_and_one_log_line(tmp_path, quick, caplog):
    async def scenario():
        fake = Receiving()
        async with fake.client() as client:
            sender = NudgeSender([family()], client, tables=proc(tmp_path))
            task = await running(sender)
            for _ in range(3):
                sender.note("orders", False)
                await asyncio.sleep(0.3)
            await stop(sender, task)
        return fake

    with caplog.at_level(logging.WARNING, logger="sudarshan.alerts"):
        fake = run(scenario())
    assert fake.posts == []
    assert len([r for r in caplog.records if "not delivered" in r.getMessage()]) == 1


def test_when_the_family_account_does_not_exist_the_receiver_is_absent(tmp_path, quick, caplog):
    async def scenario():
        fake = Receiving()
        tables = proc(tmp_path, row(5585, ME))
        async with fake.client() as client:
            sender = NudgeSender([family(uid=lambda: None)], client, tables=tables)
            task = await running(sender)
            for _ in range(3):
                sender.note("orders", False)
                await asyncio.sleep(0.3)
            await stop(sender, task)
        return fake

    with caplog.at_level(logging.INFO, logger="sudarshan.alerts"):
        fake = run(scenario())
    assert fake.posts == []  # not even to our own listener
    assert len([r for r in caplog.records if "account does not exist" in r.getMessage()]) == 1


def test_the_account_is_looked_up_by_name():
    class Entry:
        pw_uid = 4321

    seen = []

    def lookup(name):
        seen.append(name)
        if name == "sudarshan-family":
            return Entry()
        raise KeyError(name)

    assert account_uid("sudarshan-family", lookup) == 4321 and seen == ["sudarshan-family"]
    assert account_uid("nobody-here", lookup) is None
    assert account_uid("root") == 0
    assert account_uid("no-such-account-x9q") is None


def test_the_loopback_check_can_expect_another_uid(tmp_path):
    tables = proc(
        tmp_path, row(5585, FAMILY_UID), row(6000, ME), row(7000, ME), row(7000, 1, "00000000")
    )
    url = "http://127.0.0.1:5585/v1/nudge"
    assert loopback.holder(url, tables) == loopback.OTHER  # the old rule: not ours
    assert loopback.holder(url, tables, expected_uid=FAMILY_UID) == loopback.OURS
    assert loopback.holder(url, tables, expected_uid=ME) == loopback.OTHER
    assert loopback.holder("http://127.0.0.1:6000", tables) == loopback.OURS  # default unchanged
    assert loopback.holder_uid(url, tables) == FAMILY_UID
    assert loopback.holder_uid("http://127.0.0.1:9", tables) is None
    assert loopback.holder_uid("http://127.0.0.1:7000", tables) is None  # two users
    assert loopback.refusal(url, tables, expected_uid=FAMILY_UID) is None
    assert "another user" in loopback.refusal(url, tables)


# ---- off by default --------------------------------------------------------------------------


def test_both_receivers_are_off_unless_switched_on():
    assert not Settings.from_env({}).nudge_family and not Settings.from_env({}).nudge_journal
    on = Settings.from_env({"SUDARSHAN_FAMILY": "on"})
    assert on.nudge_family and not on.nudge_journal
    both = Settings.from_env({"SUDARSHAN_FAMILY": "1", "SUDARSHAN_JOURNAL": "ON"})
    assert both.nudge_family and both.nudge_journal
    for value in ("off", "0", "", "maybe", "no"):  # a typo is off, and never an error
        got = Settings.from_env({"SUDARSHAN_FAMILY": value, "SUDARSHAN_JOURNAL": value})
        assert not got.nudge_family and not got.nudge_journal


def test_with_nothing_switched_on_there_are_no_receivers_and_nothing_is_posted():
    assert receivers_for(False, False, {}) == []

    async def scenario():
        fake = Receiving()
        async with fake.client() as client:
            sender = NudgeSender([], client)
            assert not sender
            sender.note("orders", False)
            task = await running(sender)
            await asyncio.sleep(0.1)
            await stop(sender, task)
        assert fake.posts == []

    run(scenario())


def test_the_receivers_are_keyed_from_the_pepper_and_a_missing_pepper_means_none(caplog):
    pepper = "p" * 64
    got = receivers_for(True, True, {"API_KEY_PEPPER": pepper})
    assert [(r.name, r.url) for r in got] == [("family", FAMILY_URL), ("journal", JOURNAL_URL)]
    from sudarshan.vault import FAMILY_NUDGE_INFO, JOURNAL_NUDGE_INFO, fernet_for

    blob = got[0].fernet.encrypt(b"x")
    assert fernet_for(pepper, FAMILY_NUDGE_INFO).decrypt(blob) == b"x"
    with pytest.raises(InvalidToken):
        fernet_for(pepper, JOURNAL_NUDGE_INFO).decrypt(blob)  # each has its own purpose
    only = receivers_for(False, True, {"API_KEY_PEPPER": pepper})
    assert [r.name for r in only] == ["journal"] and only[0].uid() == ME
    with caplog.at_level(logging.ERROR, logger="sudarshan.alerts"):
        assert receivers_for(True, True, {}) == []
    assert "cannot be sealed" in caplog.text


# ---- the service ---------------------------------------------------------------------------


class _Tracker:
    def __init__(self, alerts=(), error: Exception | None = None):
        self.alerts, self.error = list(alerts), error

    def on_update(self, message, now, found_later=False):
        if self.error is not None:
            raise self.error
        return self.alerts


class _Outbox:
    def __init__(self):
        self.added = []

    def add(self, alerts):
        self.added.append(list(alerts))


class _Nudges:
    def __init__(self, error: Exception | None = None):
        self.notes, self.error = [], error

    def __bool__(self):
        return True

    def note(self, kind, sandbox):
        self.notes.append((kind, sandbox))
        if self.error is not None:
            raise self.error


def _service(settings_for, tracker, nudges):
    from alerts_fakes import OWNER

    from sudarshan.alerts.owner import Owner
    from sudarshan.alerts.service import AlertService

    svc = AlertService(settings_for(), owner_reader=lambda: Owner(OWNER, "k"))
    svc.tracker, svc.outbox, svc.nudges = tracker, _Outbox(), nudges
    return svc, Owner(OWNER, "k")


def test_the_service_notes_each_order_update_after_it_queued_the_alert(settings_for):
    from sudarshan.alerts.orders import Alert

    alert = Alert("k1", "fill", 1, "Filled", __import__("datetime").datetime.now())
    nudges = _Nudges()
    svc, owner = _service(settings_for, _Tracker([alert]), nudges)
    message = {"type": "order_update", "orderid": "9", "product": "CNC",
               "order_status": "complete", "mode": "analyze"}  # fmt: skip
    svc._on_message(json.dumps(message), owner)
    assert svc.outbox.added == [[alert]]  # the alert went first
    assert nudges.notes == [("holdings", True)]
    svc._on_message(json.dumps({"type": "something_else"}), owner)
    assert len(nudges.notes) == 1  # only order updates


def test_a_nudge_that_fails_never_costs_the_alert(settings_for):
    from sudarshan.alerts.orders import Alert

    alert = Alert("k1", "fill", 1, "Filled", __import__("datetime").datetime.now())
    svc, owner = _service(settings_for, _Tracker([alert]), _Nudges(error=RuntimeError("boom")))
    svc._on_message(json.dumps({"type": "order_update", "orderid": "9"}), owner)
    assert svc.outbox.added == [[alert]]


def test_an_update_the_tracker_cannot_handle_is_still_nudged(settings_for):
    nudges = _Nudges()
    svc, owner = _service(settings_for, _Tracker(error=ValueError("bad")), nudges)
    svc._on_message(json.dumps({"type": "order_update", "orderid": "9"}), owner)
    assert nudges.notes == [("orders", False)]


def test_a_service_that_is_not_switched_on_has_no_nudges_and_posts_nothing(settings_for):
    svc, owner = _service(settings_for, _Tracker(), None)
    svc._on_message(json.dumps({"type": "order_update", "orderid": "9"}), owner)  # no error
    assert svc.nudges is None


def test_a_running_service_builds_no_sender_by_default(settings_for, monkeypatch):
    from alerts_fakes import OWNER, FakeOpenAlgo, RunningClock, ist, wait_until

    from sudarshan.alerts import notices
    from sudarshan.alerts.owner import Owner
    from sudarshan.alerts.service import AlertService

    monkeypatch.setattr(notices, "POLL_S", 0.05)

    async def scenario():
        async with FakeOpenAlgo() as notify:
            settings = settings_for(None, notify, SUDARSHAN_ALERTS_ALWAYS_ON="0")
            svc = AlertService(
                settings,
                owner_reader=lambda: Owner(OWNER, "k"),
                clock=RunningClock(ist(2026, 10, 25, 20, 0)),
            )
            task = asyncio.create_task(svc.run())
            await wait_until(lambda: svc.nudges is not None, 10)
            assert not svc.nudges and svc.nudges.stats == {"posted": 0, "failed": 0, "refused": 0}
            svc.stop("test")
            await asyncio.wait_for(task, 10)

    run(scenario())
