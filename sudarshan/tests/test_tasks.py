"""cancel_and_wait: a cancel that is lost is sent again, and no task is left running.

The first test shows the defect itself, so the rest cannot pass vacuously: a task that swallows
its cancel (as anyio's connect_tcp can, under httpx) is still running after ``task.cancel()``.
The last test runs real httpx requests against a local server and cancels them in their first
milliseconds, where the cancel was measured lost.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import time

import httpx
import pytest

from sudarshan.tasks import cancel_and_wait

LOGGER = "sudarshan.tasks"


async def swallows_its_first_cancel(started: asyncio.Event) -> None:
    """What a lost cancel looks like from outside: the task carries on and parks."""
    started.set()
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass  # lost, as inside anyio.connect_tcp
    await asyncio.Event().wait()


def test_a_task_that_swallows_its_cancel_is_still_running_after_one_cancel():
    async def scenario():
        started = asyncio.Event()
        task = asyncio.create_task(swallows_its_first_cancel(started))
        await started.wait()
        task.cancel()
        done, _ = await asyncio.wait([task], timeout=0.3)
        assert not done  # the defect: one cancel and a gather would wait for ever
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_it_is_cancelled_again_until_it_ends_and_logged_once(caplog):
    async def scenario():
        started = asyncio.Event()
        task = asyncio.create_task(swallows_its_first_cancel(started), name="push")
        quick = asyncio.create_task(asyncio.sleep(60))
        await started.wait()
        began = time.monotonic()
        await asyncio.wait_for(cancel_and_wait(task, None, quick, again_after=0.05), 5)
        assert task.cancelled() and quick.cancelled()
        assert time.monotonic() - began < 2

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        asyncio.run(scenario())
    lines = [r.getMessage() for r in caplog.records if r.name == LOGGER]
    assert len(lines) == 1 and lines[0].startswith("Task push was still running")


def test_tasks_that_end_at_once_or_had_ended_are_not_logged_and_their_errors_are_retrieved(
    caplog,
):
    async def scenario():
        async def fails_when_cancelled():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise ValueError("boom") from None  # set after the cancel: still to retrieve

        ended = asyncio.create_task(asyncio.sleep(0))
        await ended
        failing = asyncio.create_task(fails_when_cancelled())
        sleeping = asyncio.create_task(asyncio.sleep(60))
        await asyncio.sleep(0)  # both are waiting
        await cancel_and_wait(ended, failing, sleeping)
        await cancel_and_wait()  # nothing to do
        assert not ended.cancelled() and sleeping.cancelled()
        assert failing.done() and not failing.cancelled()
        del failing
        gc.collect()  # an error never retrieved is reported when its task is collected

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())
    assert [r for r in caplog.records if r.name == LOGGER] == []
    assert not [r for r in caplog.records if "never retrieved" in r.getMessage()]


def test_a_caller_cancelled_meanwhile_still_ends_every_task_then_is_cancelled():
    async def scenario():
        started = asyncio.Event()
        task = asyncio.create_task(swallows_its_first_cancel(started))
        await started.wait()
        stopping = asyncio.create_task(cancel_and_wait(task, again_after=0.1))
        await asyncio.sleep(0.02)
        stopping.cancel()  # e.g. a deadline on the whole stop
        with pytest.raises(asyncio.CancelledError):
            await stopping
        assert task.done()

    asyncio.run(scenario())


def test_real_httpx_requests_cancelled_in_their_first_milliseconds_always_end():
    """Where the cancel was measured lost: inside httpx's connect. Each task asks, then parks."""

    async def scenario():
        async def serve(reader, writer):
            try:
                await reader.readuntil(b"\r\n\r\n")
                await asyncio.sleep(0.02)
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
                await writer.drain()
            except (ConnectionError, asyncio.IncompleteReadError):
                pass
            finally:
                writer.close()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/"
        try:
            async with httpx.AsyncClient(trust_env=False) as client:

                async def asks_then_parks():
                    try:
                        await client.post(
                            url, content=b"x", timeout=httpx.Timeout(3.0, connect=1.0)
                        )
                    except httpx.HTTPError:
                        pass
                    await asyncio.Event().wait()

                for n in range(60):
                    task = asyncio.create_task(asks_then_parks())
                    await asyncio.sleep((n % 12) * 0.0005)
                    await asyncio.wait_for(cancel_and_wait(task, again_after=0.05), 5)
                    assert task.done()
        finally:
            server.close()

    asyncio.run(scenario())
