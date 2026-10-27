"""M18.1: the alerts' health page, GET /health on 127.0.0.1, and the release it names.

Before this the alerts had no health surface at all. The page says which release is installed
(``release``, from the deploy tool's record) beside the service's own ``version``, what the service
is doing, whether the owner's key was found and what has gone to Telegram, and carries no secret.
It is a convenience: whatever goes wrong with it must cost the page and nothing else.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import time

import httpx
import pytest
from alerts_fakes import (
    API_KEY,
    OWNER,
    FakeOpenAlgo,
    FakeProxy,
    RunningClock,
    ist,
    update,
    wait_until,
)

from sudarshan.alerts import __version__, health
from sudarshan.alerts.config import HEALTH_PORT, ConfigError, Settings
from sudarshan.alerts.health import HealthPage, snapshot
from sudarshan.alerts.owner import Owner
from sudarshan.alerts.service import AlertService
from sudarshan.ops.release import DEPLOYED_TAG_FILE

KEYS = {
    "service", "release", "version", "state", "detail", "window", "owner_ready", "owner_problem",
    "stats", "telegram",
}  # fmt: skip


NAME = "a-user-name-nobody-would-guess"  # a fake


def _owner() -> Owner:
    return Owner(OWNER, API_KEY)


def _service(settings_for, **overrides) -> AlertService:
    return AlertService(settings_for(**overrides), owner_reader=_owner)


async def _get(port: int, path: str = "/health", **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(trust_env=False, base_url=f"http://127.0.0.1:{port}") as client:
        return await client.get(path, **kwargs)


async def _raw(port: int, request: bytes, wait: float = 3.0) -> bytes:
    """What the page sends back for exactly these bytes, whatever they are."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(request)
        await writer.drain()
        return await asyncio.wait_for(reader.read(), wait)
    finally:
        writer.close()


# ---- the document -------------------------------------------------------------------------------


def test_the_document_before_the_service_has_run(settings_for):
    svc = _service(settings_for)
    page = snapshot(svc)
    assert set(page) == KEYS
    assert page["service"] == "sudarshan-alerts" and page["version"] == __version__
    assert page["release"] is None  # no deploy record on this machine
    assert (page["state"], page["detail"]) == ("starting", "")
    assert page["window"] == "always on"
    assert (page["owner_ready"], page["owner_problem"]) == (False, None)
    assert page["stats"] == {"connects": 0, "updates": 0, "alerts": 0}
    assert page["telegram"] is None  # no outbox yet
    json.dumps(page)  # plain data all the way down


def test_the_release_is_the_recorded_tag_and_not_the_services_version(settings_for, tmp_path):
    svc = _service(settings_for)
    record = tmp_path / "deployed_tag"
    record.write_text("v0.3.3\n")
    page = snapshot(svc)
    assert page["release"] == "v0.3.3" and page["version"] == __version__
    assert page["release"] != page["version"]
    keys = list(page)
    assert keys.index("release") == keys.index("version") - 1
    record.write_text("v0.3.4\n")  # an install while the service runs
    assert snapshot(svc)["release"] == "v0.3.4"


@pytest.mark.parametrize(
    "text", ["", "none\n", "v0.3\n", "0.3.3\n", "v0.3.3-rc1\n", "v0.3.3\nv0.3.4\n", "v1.2.3\x00"]
)
def test_no_release_when_the_record_is_not_a_tag(settings_for, tmp_path, text):
    svc = _service(settings_for)
    (tmp_path / "deployed_tag").write_bytes(text.encode())
    page = snapshot(svc)
    assert page["release"] is None
    assert page["service"] == "sudarshan-alerts" and page["state"] == "starting"


def test_no_release_when_the_record_is_a_folder_or_a_link(settings_for, tmp_path):
    svc = _service(settings_for)
    record = tmp_path / "deployed_tag"
    record.mkdir()
    assert snapshot(svc)["release"] is None
    record.rmdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_text("v9.9.9\n")
    record.symlink_to(elsewhere)
    assert snapshot(svc)["release"] is None  # a link is not followed


def test_what_the_service_knows_shows_and_a_secret_does_not(settings_for):
    svc = _service(settings_for)
    svc.owner = Owner(NAME, API_KEY)
    svc._set_state("connected", "receiving order updates")
    svc.stats.update(connects=2, updates=7, alerts=5)
    svc.outbox = type(
        "Outbox", (), {"stats": {"messages": 4, "lines": 5, "dropped": 0, "failed": 1}}
    )()
    page = snapshot(svc)
    assert (page["state"], page["detail"]) == ("connected", "receiving order updates")
    assert page["owner_ready"] is True
    assert page["stats"] == {"connects": 2, "updates": 7, "alerts": 5}
    assert page["telegram"] == {"messages": 4, "lines": 5, "dropped": 0, "failed": 1}
    svc.owner, svc._owner_problem = None, "no_key"
    page = snapshot(svc)
    assert (page["owner_ready"], page["owner_problem"]) == (False, "no_key")
    text = json.dumps(page)
    assert API_KEY not in text and NAME not in text


def test_the_page_is_a_copy_not_the_services_own_counters(settings_for):
    svc = _service(settings_for)
    snapshot(svc)["stats"]["updates"] = 99
    assert svc.stats["updates"] == 0


# ---- the settings --------------------------------------------------------------------------------


def test_the_port_setting(settings_for):
    assert settings_for().health_port is None  # the fixture switches the page off
    assert Settings.from_env({}).health_port == HEALTH_PORT == 5585
    for value, port in (("", 5585), ("  ", 5585), ("5599", 5599), ("0", 0), (" 5599 ", 5599)):
        assert Settings.from_env({"SUDARSHAN_ALERTS_HEALTH_PORT": value}).health_port == port
    for value in ("off", "OFF", "no", "false"):
        assert Settings.from_env({"SUDARSHAN_ALERTS_HEALTH_PORT": value}).health_port is None


@pytest.mark.parametrize("value", ["abc", "-1", "65536", "70000", "55.85", "5585x", "٥٥٨٥", "on"])
def test_a_port_that_is_not_one_is_refused_in_plain_words(value):
    with pytest.raises(ConfigError, match="SUDARSHAN_ALERTS_HEALTH_PORT must be a port number"):
        Settings.from_env({"SUDARSHAN_ALERTS_HEALTH_PORT": value})


def test_the_page_keeps_clear_of_every_port_the_ring_has_planned():
    """5582 is the market alerts' sealed API in this very service: if the health page held it,
    market alerts could not open theirs and would stay off. 5580 to 5584 are the feed's two, the
    market alerts', the portfolio's and the screener's (the port table in the README)."""
    assert HEALTH_PORT not in range(5580, 5585)
    assert Settings.from_env({}).health_port not in range(5580, 5585)


def test_the_record_setting(tmp_path):
    assert Settings.from_env({}).deployed_tag_file == DEPLOYED_TAG_FILE
    moved = Settings.from_env({"SUDARSHAN_DEPLOYED_TAG_FILE": str(tmp_path / "tag")})
    assert moved.deployed_tag_file == tmp_path / "tag"
    assert Settings.from_env({"SUDARSHAN_DEPLOYED_TAG_FILE": ""}).deployed_tag_file == (
        DEPLOYED_TAG_FILE
    )


# ---- the listener --------------------------------------------------------------------------------


def test_the_page_answers_get_health_on_loopback_only(settings_for, tmp_path):
    async def scenario():
        svc = _service(settings_for)
        (tmp_path / "deployed_tag").write_text("v0.3.3\n")
        page = HealthPage(svc)
        assert await page.start(0) and page.port
        try:
            answer = await _get(page.port)
            assert answer.status_code == 200
            assert answer.headers["content-type"] == "application/json"
            assert answer.headers["cache-control"] == "no-store"
            assert answer.headers["connection"] == "close"
            assert answer.json() == snapshot(svc)
            assert answer.json()["release"] == "v0.3.3"
            # The same page with a trailing slash or a query string.
            for path in ("/health/", "/health?pretty=1"):
                assert (await _get(page.port, path)).json() == snapshot(svc)
            # Bound to 127.0.0.1 only: nothing listens on this machine's other addresses.
            (host, port) = page._server.sockets[0].getsockname()[:2]
            assert (host, port) == ("127.0.0.1", page.port)
            assert len(page._server.sockets) == 1
        finally:
            await page.close()

    asyncio.run(scenario())


def test_the_page_refuses_what_it_does_not_serve(settings_for):
    async def scenario():
        page = HealthPage(_service(settings_for))
        assert await page.start(0)
        try:
            assert (await _get(page.port, "/")).status_code == 404
            assert (await _get(page.port, "/healthz")).status_code == 404
            assert (await _get(page.port, "/health/extra")).status_code == 404
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{page.port}") as client:
                assert (await client.post("/health", content=b"{}")).status_code == 405
                assert (await client.delete("/health")).status_code == 405
            # A browser page (DNS rebinding) is turned away; a local client sends no Origin.
            browser = await _get(page.port, headers={"Origin": "https://example.org"})
            assert browser.status_code == 403
            junk = await _raw(page.port, b"GARBAGE\r\n\r\n")  # not even a request line
            assert junk.startswith(b"HTTP/1.1 400 ")
            huge = await _raw(page.port, b"GET /health HTTP/1.1\r\nX: " + b"a" * 9000 + b"\r\n\r\n")
            assert huge.startswith(b"HTTP/1.1 400 ")
            # And still answers afterwards.
            assert (await _get(page.port)).status_code == 200
        finally:
            await page.close()

    asyncio.run(scenario())


def test_a_request_that_never_finishes_is_given_up_on(settings_for, monkeypatch):
    monkeypatch.setattr(health, "READ_TIMEOUT_S", 0.2)

    async def scenario():
        page = HealthPage(_service(settings_for))
        assert await page.start(0)
        try:
            started = time.monotonic()
            late = await _raw(page.port, b"GET /health HTTP/1.1\r\nHost: x\r\n")  # no blank line
            assert late.startswith(b"HTTP/1.1 408 ")
            assert time.monotonic() - started < 2
            assert (await _get(page.port)).status_code == 200
        finally:
            await page.close()

    asyncio.run(scenario())


def test_a_client_that_hangs_on_does_not_hold_up_a_stop(settings_for):
    async def scenario():
        page = HealthPage(_service(settings_for))
        assert await page.start(0)
        reader, writer = await asyncio.open_connection("127.0.0.1", page.port)
        try:
            writer.write(b"GET /hea")  # starts a request and goes quiet
            await writer.drain()
            await wait_until(lambda: len(page._open) == 1)
            started = time.monotonic()
            await page.close()
            assert time.monotonic() - started < health.CLOSE_WAIT_S
            assert not page._open
            try:  # the page hung up: a clean end, or a reset when its 8 bytes were not yet read
                assert await asyncio.wait_for(reader.read(), 2) == b""
            except ConnectionResetError:
                pass
        finally:
            writer.close()
        with pytest.raises(OSError):  # nothing listens any more
            await asyncio.open_connection("127.0.0.1", page.port)
        await page.close()  # and a second close is harmless

    asyncio.run(scenario())


def test_a_bug_while_building_the_page_costs_one_answer(settings_for, monkeypatch, caplog):
    async def scenario():
        page = HealthPage(_service(settings_for))
        assert await page.start(0)
        try:
            calls = iter([RuntimeError("boom")])

            def flaky(service):
                error = next(calls, None)
                if error:
                    raise error
                return {"ok": True}

            monkeypatch.setattr(health, "snapshot", flaky)
            failed = await _get(page.port)
            assert failed.status_code == 500 and failed.json() == {"error": "internal error"}
            assert "boom" not in failed.text  # the detail goes to the log, not the wire
            assert (await _get(page.port)).json() == {"ok": True}
        finally:
            await page.close()

    caplog.set_level(logging.ERROR, logger="sudarshan.alerts")
    asyncio.run(scenario())
    assert "the health page could not be built" in caplog.text


def test_switched_off_means_no_listener(settings_for):
    async def scenario():
        page = HealthPage(_service(settings_for))
        assert await page.start(None) is False and page.port is None
        await page.close()  # nothing to close

    asyncio.run(scenario())


def test_a_taken_port_is_logged_and_costs_nothing_else(settings_for, caplog):
    caplog.set_level(logging.INFO, logger="sudarshan.alerts")

    async def scenario():
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        try:
            page = HealthPage(_service(settings_for))
            assert await page.start(busy.getsockname()[1]) is False
            assert page.port is None
            await page.close()
        finally:
            busy.close()

    asyncio.run(scenario())
    (warning,) = [r for r in caplog.records if r.levelno == logging.WARNING]
    text = warning.getMessage()
    assert "The health page could not start" in text and "Order alerts are not affected" in text
    assert "Traceback" not in caplog.text and "OSError" not in text  # no developer words


# ---- inside the running service ------------------------------------------------------------------


def test_the_running_service_serves_its_page_and_closes_it_on_stop(settings_for, fast, tmp_path):
    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as notify:
            clock = RunningClock(ist(2026, 10, 21, 10, 0))
            (tmp_path / "deployed_tag").write_text("v0.3.3\n")
            settings = settings_for(proxy, notify, SUDARSHAN_ALERTS_HEALTH_PORT="0")
            svc = AlertService(settings, owner_reader=_owner, clock=clock)
            task = asyncio.create_task(svc.run())
            await wait_until(lambda: proxy.subscribed)
            port = svc.health_page.port
            assert port
            page = (await _get(port)).json()
            assert (page["service"], page["release"], page["version"]) == (
                "sudarshan-alerts", "v0.3.3", __version__,
            )  # fmt: skip
            assert (page["state"], page["owner_ready"], page["owner_problem"]) == (
                "connected", True, None,
            )  # fmt: skip
            assert page["window"] == "always on" and page["stats"]["connects"] == 1
            await proxy.push(update("h1", "complete"))
            await wait_until(lambda: len(notify.bodies) == 1)
            await wait_until(lambda: svc.outbox.stats["messages"] == 1)
            page = (await _get(port)).json()
            assert page["stats"]["updates"] == 1 and page["stats"]["alerts"] == 1
            assert page["telegram"]["messages"] == 1 and page["telegram"]["failed"] == 0
            # No key, no user name, no message text on the page.
            assert set(page) == KEYS and API_KEY not in json.dumps(page)
            svc.stop("test")
            await asyncio.wait_for(task, 10)
            with pytest.raises(OSError):
                await asyncio.open_connection("127.0.0.1", port)

    asyncio.run(scenario())


def test_the_service_runs_without_the_page_when_its_port_is_taken(settings_for, fast, caplog):
    caplog.set_level(logging.WARNING, logger="sudarshan.alerts")

    async def scenario():
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        try:
            async with FakeProxy() as proxy, FakeOpenAlgo() as notify:
                clock = RunningClock(ist(2026, 10, 21, 10, 0))
                port = str(busy.getsockname()[1])
                settings = settings_for(proxy, notify, SUDARSHAN_ALERTS_HEALTH_PORT=port)
                svc = AlertService(settings, owner_reader=_owner, clock=clock)
                task = asyncio.create_task(svc.run())
                await wait_until(lambda: proxy.subscribed)
                await proxy.push(update("t1", "complete"))
                await wait_until(lambda: len(notify.bodies) == 1)  # the alerts work regardless
                assert svc.health_page.port is None and svc.state == "connected"
                svc.stop("test")
                await asyncio.wait_for(task, 10)
        finally:
            busy.close()

    asyncio.run(scenario())
    assert "The health page could not start" in caplog.text


def test_with_the_page_off_the_service_opens_no_listener(settings_for, fast):
    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as notify:
            svc = AlertService(
                settings_for(proxy, notify),
                owner_reader=_owner,
                clock=RunningClock(ist(2026, 10, 21, 10, 0)),
            )
            task = asyncio.create_task(svc.run())
            await wait_until(lambda: proxy.subscribed)
            assert svc.health_page.port is None and svc.health_page._server is None
            svc.stop("test")
            await asyncio.wait_for(task, 10)

    asyncio.run(scenario())
