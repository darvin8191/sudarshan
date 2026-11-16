"""B2: the push to the owner's open screens (push.py), from a running service to a stand-in for
OpenAlgo's internal route on 127.0.0.1 that opens each push with the real key for
SIGNALS_PUSH_INFO. OpenAlgo's own side of it is tested in sudarshan/tests/test_signals_push_route.py.
"""

from __future__ import annotations

import asyncio
import json
import logging

from signals_fakes import (
    AT,
    KEYS,
    PEPPER,
    Box,
    ask,
    document,
    free_port,
    reading,
    settings_for,
    until,
)

from sudarshan.autologin.handoff import HandoffRejected, NonceBook, open_sealed
from sudarshan.signals import contract as c
from sudarshan.signals import push
from sudarshan.signals.service import Service
from sudarshan.tests.fdwatch import FdWatch
from sudarshan.vault import SIGNALS_API_INFO, SIGNALS_PUSH_INFO, fernet_for

PUSH_KEY = fernet_for(PEPPER, SIGNALS_PUSH_INFO)


class FakeOpenAlgo:
    """OpenAlgo's push route as the service meets it: each push opened and kept, then answered."""

    def __init__(self, status: int = 200, answer: dict | None = None):
        self.status = status
        self.answer = answer if answer is not None else {"status": "success", "screens": 2}
        self.pushes: list[dict] = []
        self.paths: list[str] = []
        self.refused: list[str] = []
        self.nonces = NonceBook(64)
        self.server: asyncio.Server | None = None

    async def start(self) -> str:
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        return f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"

    async def _serve(self, reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.split(b"\r\n")
            self.paths.append(lines[0].split(b" ")[1].decode())
            length = next(int(line.split(b":", 1)[1]) for line in lines
                          if line.lower().startswith(b"content-length:"))  # fmt: skip
            body = await reader.readexactly(length)
            try:
                self.pushes.append(open_sealed(PUSH_KEY, body, op="push", seen=self.nonces,
                                               max_bytes=c.SEAL_LIMITS["push"]))  # fmt: skip
                status, answer = self.status, self.answer
            except HandoffRejected as exc:
                self.refused.append(str(exc))
                status, answer = 403, {"status": "error", "message": "Forbidden"}
            data = json.dumps(answer).encode()
            writer.write(b"HTTP/1.1 %d X\r\nContent-Type: application/json\r\nContent-Length: %d"
                         b"\r\nConnection: close\r\n\r\n%s" % (status, len(data), data))  # fmt: skip
            await writer.drain()
        finally:
            writer.close()

    async def stop(self) -> None:
        self.server.close()
        await self.server.wait_closed()


def make(tmp_path, openalgo_url: str, *, keys=None) -> tuple[Service, Box, int]:
    port = free_port()
    box = Box(tmp_path / "in")
    svc = Service(settings_for(tmp_path, port, openalgo_url=openalgo_url),
                  {**KEYS, "push": PUSH_KEY} if keys is None else keys, hooks={},
                  jobs={"push": push.pusher}, letterbox=box.reader, scan_s=0.02,
                  setup_every_s=0.05)  # fmt: skip
    svc.clock = lambda: AT + 60_000
    return svc, box, port


async def running(svc: Service) -> asyncio.Task:
    task = asyncio.create_task(svc.run())
    await until(lambda: svc.state == "running")
    return task


async def stopped(svc: Service, task: asyncio.Task) -> None:
    svc.stop()
    await asyncio.wait_for(task, 10)


def data_of(message: dict) -> dict:
    assert message["op"] == "push" and message["principal"] == "sudarshan-signals"
    [item] = message["items"]
    assert item["event"] == c.EVENT_UPDATE and set(item["data"]) == {"cause", "state"}
    return item["data"]


def test_a_document_and_a_settings_change_each_push_the_state_now(tmp_path):
    async def scenario():
        fake = FakeOpenAlgo()
        svc, box, port = make(tmp_path, await fake.start())
        task = await running(svc)
        try:
            box.drop(document(seq=1))
            await until(lambda: len(fake.pushes) == 1)
            first = data_of(fake.pushes[0])
            assert first["cause"] == "document" and fake.paths == [c.PUSH_PATH]
            assert set(first["state"]) == {f.name for f in c.record("State")}
            assert [(r["module"], r["state"]) for r in first["state"]["readings"]] == [
                ("regime", "DISTRIBUTION")]  # fmt: skip
            answer = await ask(port, "settings_set", version=1, lang="gu")
            assert answer.status_code == 200
            await until(lambda: len(fake.pushes) == 2)
            second = data_of(fake.pushes[1])
            assert second["cause"] == "settings" and second["state"]["lang"] == "gu"
            box.drop(document(seq=2))  # the same readings again: nothing changed, nothing pushed
            await until(lambda: not box.names())
            await asyncio.sleep(0.3)
            assert len(fake.pushes) == 2
        finally:
            await stopped(svc, task)
            await fake.stop()

    asyncio.run(scenario())


def test_a_burst_is_one_push_with_the_newest_state(tmp_path):
    async def scenario():
        fake = FakeOpenAlgo()
        svc, box, _port = make(tmp_path, await fake.start())
        box.drop(document(seq=1))  # both wait in the letterbox: one cycle reads them together
        box.drop(
            document(
                seq=2,
                at=AT + 1000,
                signals=[reading(state="ACCUMULATION", label="Buying", bias="bull")],
            )
        )
        task = await running(svc)
        try:
            await until(lambda: len(fake.pushes) == 1)
            await asyncio.sleep(0.3)
            assert len(fake.pushes) == 1
            state = data_of(fake.pushes[0])["state"]
            assert [r["state"] for r in state["readings"]] == ["ACCUMULATION"]
            svc.updated("settings")
            svc.updated("lapse")  # two updates together: one push, named for the lapse
            await until(lambda: len(fake.pushes) == 2)
            await asyncio.sleep(0.3)
            assert len(fake.pushes) == 2 and data_of(fake.pushes[1])["cause"] == "lapse"
        finally:
            await stopped(svc, task)
            await fake.stop()

    asyncio.run(scenario())


def test_a_refused_push_is_never_retried_and_the_service_carries_on(tmp_path, caplog):
    async def scenario():
        fake = FakeOpenAlgo(status=503, answer={"status": "error", "message": "Busy"})
        svc, box, _port = make(tmp_path, await fake.start())
        task = await running(svc)
        try:
            box.drop(document(seq=1))
            await until(lambda: len(fake.pushes) == 1)
            await asyncio.sleep(0.4)
            assert len(fake.pushes) == 1 and svc.state == "running"
            box.drop(document(seq=2, at=AT + 1000, signals=[reading(state="ACCUMULATION")]))
            await until(lambda: len(fake.pushes) == 2)  # the next update is pushed as usual
        finally:
            await stopped(svc, task)
            await fake.stop()

    with caplog.at_level(logging.WARNING, logger="sudarshan.signals"):
        asyncio.run(scenario())
    refused = [r.getMessage() for r in caplog.records if r.getMessage().startswith("OpenAlgo")]
    assert refused == ["OpenAlgo refused the TRINETRA view's update for the open screens (it "
                       "answered 503)"]  # fmt: skip


def test_nothing_listening_sends_nothing_and_says_so(tmp_path, caplog):
    nobody = f"http://127.0.0.1:{free_port()}"

    async def scenario():
        svc, box, _port = make(tmp_path, nobody)
        task = await running(svc)
        try:
            box.drop(document(seq=1))
            await until(lambda: not box.names())
            assert await push.push_once(svc, "document") is None
            assert svc.state == "running"
        finally:
            await stopped(svc, task)

    with caplog.at_level(logging.WARNING, logger="sudarshan.signals"):
        asyncio.run(scenario())
    said = [r.getMessage() for r in caplog.records if "not pushed" in r.getMessage()]
    assert said and "nothing listens on port" in said[0] and len(said) == 1  # once an hour


def test_without_its_key_the_service_pushes_nothing(tmp_path):
    async def scenario():
        fake = FakeOpenAlgo()
        svc, box, _port = make(tmp_path, await fake.start(), keys=KEYS)
        task = await running(svc)
        try:
            box.drop(document(seq=1))
            await until(lambda: not box.names())
            await asyncio.sleep(0.3)
            assert fake.pushes == [] and fake.paths == []
        finally:
            await stopped(svc, task)
            await fake.stop()

    asyncio.run(scenario())


def test_the_seal_carries_the_state_or_null_when_it_would_not_fit():
    state = {"now_ms": AT, "readings": []}
    token = push.sealed(PUSH_KEY, "document", state)
    message = open_sealed(PUSH_KEY, token, op="push", seen=NonceBook(4), max_bytes=32 * 1024)
    assert data_of(message) == {"cause": "document", "state": state}
    huge = {"now_ms": AT, "readings": [{"why": "x" * 600}] * 60}
    token = push.sealed(PUSH_KEY, "lapse", huge)
    assert len(token) <= c.SEAL_LIMITS["push"]
    message = open_sealed(PUSH_KEY, token, op="push", seen=NonceBook(4), max_bytes=32 * 1024)
    assert data_of(message) == {"cause": "lapse", "state": None}
    other = fernet_for(PEPPER, SIGNALS_API_INFO)  # another purpose's key never opens it
    try:
        open_sealed(other, push.sealed(PUSH_KEY, "lapse", state), op="push", seen=NonceBook(4))
    except HandoffRejected as exc:
        assert str(exc) == "seal"
    else:
        raise AssertionError("the push opened under another purpose")


def test_a_folded_push_names_one_cause():
    assert push.cause_of({"settings", "lapse", "document"}) == "document"
    assert push.cause_of({"settings", "lapse"}) == "lapse"
    assert push.cause_of({"settings"}) == "settings"
    assert push.cause_of(set()) is None


def test_pushes_leave_no_descriptor_behind(tmp_path):
    async def scenario():
        fake = FakeOpenAlgo()
        svc, box, _port = make(tmp_path, await fake.start())
        task = await running(svc)
        try:
            box.drop(document(seq=1))
            await until(lambda: len(fake.pushes) == 1)
            watch = FdWatch()
            for n in range(20):
                svc.updated("settings")
                await until(lambda n=n: len(fake.pushes) == n + 2)
            await watch.aassert_clean()
        finally:
            await stopped(svc, task)
            await fake.stop()

    asyncio.run(scenario())
