"""The service opens the real store at start and closes it on stop (M7.18), in a temporary folder."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sqlite3
import threading
from pathlib import Path

from test_portfolio_service import FERNET, free_port, post, run, sealed, settings_for

from sudarshan.portfolio import migrations, service
from sudarshan.portfolio.service import Service
from sudarshan.portfolio.store import DB_FILENAME, THREAD_PREFIX, Scope, StoreError


def fds() -> int:
    return len(os.listdir("/proc/self/fd"))


def targets() -> dict:
    out = {}
    for name in os.listdir("/proc/self/fd"):
        try:
            out[name] = os.readlink(f"/proc/self/fd/{name}")
        except OSError:
            pass
    return out


def store_threads() -> list[str]:
    return [t.name for t in threading.enumerate() if t.name.startswith(THREAD_PREFIX)]


async def started(svc: Service) -> asyncio.Task:
    task = asyncio.create_task(svc.run())
    for _ in range(1500):
        if svc.state in ("running", "off"):
            return task
        await asyncio.sleep(0.02)
    raise AssertionError(svc.state)


def version_of(path: Path) -> int:
    with contextlib.closing(sqlite3.connect(path)) as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


def test_start_migrates_seeds_and_stop_leaves_nothing_open(tmp_path):
    seen: dict = {}
    before_fds, before_threads = fds(), set(threading.enumerate())
    t0 = targets()

    async def scenario():
        svc = Service(settings_for(tmp_path, free_port()), FERNET, jobs={})
        task = await started(svc)
        seen["store"] = svc.health()["portfolio_state"]["store"]
        seen["counts"] = svc.store.counts(Scope.owner())
        seen["threads"] = store_threads()
        svc.stop()
        await asyncio.wait_for(task, 10)

    run(scenario)
    path = tmp_path / DB_FILENAME
    assert seen["store"] == "open"
    assert seen["counts"]["tab"] == 1  # the first-start seed: the Upstox tab
    assert len(seen["threads"]) == 1  # the store's one writer thread ...
    assert store_threads() == []  # ... is gone after the stop
    assert set(threading.enumerate()) <= before_threads
    assert fds() == before_fds, {k: v for k, v in targets().items() if k not in t0}
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert version_of(path) == migrations.LATEST  # migrations ran at start


def test_a_second_start_runs_nothing_again_and_seeds_nothing_twice(tmp_path):
    counts: list = []

    async def once():
        svc = Service(settings_for(tmp_path, free_port()), FERNET, jobs={})
        task = await started(svc)
        counts.append(svc.store.counts(Scope.owner()))
        svc.stop()
        await asyncio.wait_for(task, 10)

    run(once)
    before = fds()
    run(once)
    assert counts[0] == counts[1]
    assert fds() == before


def test_a_file_newer_than_the_code_keeps_the_portfolio_off_in_the_designs_words(tmp_path):
    path = tmp_path / DB_FILENAME
    with contextlib.closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE keep (x)")
        conn.execute(f"PRAGMA user_version = {migrations.LATEST + 1}")
    ran: list = []
    jobs: dict = {}

    @service.job("never", jobs)
    async def never(svc):
        ran.append(1)

    answers: dict = {}

    async def scenario():
        port = free_port()
        svc = Service(settings_for(tmp_path, port), FERNET, jobs=jobs)
        task = await started(svc)
        answers["data"] = (await post(f"http://127.0.0.1:{port}", "state", sealed("state"))).json()
        answers["health"] = svc.health()["portfolio_state"]
        svc.stop()
        await asyncio.wait_for(task, 10)

    before = fds()
    run(scenario)
    sentence = (
        "Your portfolio data was written by a newer release. Install that release again; "
        "nothing was changed."
    )
    assert answers["data"]["data"] == {"enabled": False, "message": sentence}
    assert answers["health"]["enabled"] is False and answers["health"]["reason"] == sentence
    assert answers["health"]["store"] == "closed"
    assert ran == []
    # not written to: the same version and the same tables (opening may set the journal mode)
    assert version_of(path) == migrations.LATEST + 1
    with contextlib.closing(sqlite3.connect(path)) as conn:
        assert [r[0] for r in conn.execute("SELECT name FROM sqlite_master")] == ["keep"]
    assert store_threads() == [] and fds() == before


def test_a_store_that_will_not_open_keeps_the_portfolio_off_and_the_service_up(tmp_path):
    def opener(settings):
        raise StoreError("The portfolio could not be opened. Check the server's disk.", "disk full")

    answers: dict = {}

    async def scenario():
        port = free_port()
        svc = Service(settings_for(tmp_path, port), FERNET, jobs={}, opener=opener)
        task = await started(svc)
        answers["data"] = (await post(f"http://127.0.0.1:{port}", "state", sealed("state"))).json()
        svc.stop()
        await asyncio.wait_for(task, 10)

    run(scenario)
    assert answers["data"]["data"]["enabled"] is False
    assert "disk full" not in str(answers)
