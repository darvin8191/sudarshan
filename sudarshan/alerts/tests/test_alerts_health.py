"""M18.1: the alerts' health page, GET /health on 127.0.0.1, and the release it names.

Before this the alerts had no health surface at all. The page says which release is installed
(``release``, from the deploy tool's record) beside the service's own ``version``, what the service
is doing, whether the owner's key was found and what has gone to Telegram, and carries no secret.
It is a convenience: whatever goes wrong with it must cost the page and nothing else.
"""

from __future__ import annotations

import asyncio
import inspect
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
from sudarshan.alerts import service as service_module
from sudarshan.alerts.config import Settings
from sudarshan.alerts.health import health_document
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


# ---- the document -------------------------------------------------------------------------------


def test_the_document_before_the_service_has_run(settings_for):
    svc = _service(settings_for)
    page = health_document(svc)
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
    page = health_document(svc)
    assert page["release"] == "v0.3.3" and page["version"] == __version__
    assert page["release"] != page["version"]
    keys = list(page)
    assert keys.index("release") == keys.index("version") - 1
    record.write_text("v0.3.4\n")  # an install while the service runs
    assert health_document(svc)["release"] == "v0.3.4"


@pytest.mark.parametrize(
    "text", ["", "none\n", "v0.3\n", "0.3.3\n", "v0.3.3-rc1\n", "v0.3.3\nv0.3.4\n", "v1.2.3\x00"]
)
def test_no_release_when_the_record_is_not_a_tag(settings_for, tmp_path, text):
    svc = _service(settings_for)
    (tmp_path / "deployed_tag").write_bytes(text.encode())
    page = health_document(svc)
    assert page["release"] is None
    assert page["service"] == "sudarshan-alerts" and page["state"] == "starting"


def test_no_release_when_the_record_is_a_folder_or_a_link(settings_for, tmp_path):
    svc = _service(settings_for)
    record = tmp_path / "deployed_tag"
    record.mkdir()
    assert health_document(svc)["release"] is None
    record.rmdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_text("v9.9.9\n")
    record.symlink_to(elsewhere)
    assert health_document(svc)["release"] is None  # a link is not followed


def test_what_the_service_knows_shows_and_a_secret_does_not(settings_for):
    svc = _service(settings_for)
    svc.owner = Owner(NAME, API_KEY)
    svc._set_state("connected", "receiving order updates")
    svc.stats.update(connects=2, updates=7, alerts=5)
    svc.outbox = type(
        "Outbox", (), {"stats": {"messages": 4, "lines": 5, "dropped": 0, "failed": 1}}
    )()
    page = health_document(svc)
    assert (page["state"], page["detail"]) == ("connected", "receiving order updates")
    assert page["owner_ready"] is True
    assert page["stats"] == {"connects": 2, "updates": 7, "alerts": 5}
    assert page["telegram"] == {"messages": 4, "lines": 5, "dropped": 0, "failed": 1}
    svc.owner, svc._owner_problem = None, "no_key"
    page = health_document(svc)
    assert (page["owner_ready"], page["owner_problem"]) == (False, "no_key")
    text = json.dumps(page)
    assert API_KEY not in text and NAME not in text


def test_the_page_is_a_copy_not_the_services_own_counters(settings_for):
    svc = _service(settings_for)
    health_document(svc)["stats"]["updates"] = 99
    assert svc.stats["updates"] == 0


def test_the_document_is_a_plain_function_another_server_can_serve(settings_for):
    """The market alerts' API answers /health with this document plus its own fields: it must be
    callable from their handler (synchronous, no await), change nothing and be the same twice."""
    assert not inspect.iscoroutinefunction(health_document)
    svc = _service(settings_for)
    before = (svc.state, dict(svc.stats), svc.owner, svc._owner_problem, svc.outbox)
    first = health_document(svc)
    merged = first | {"market_state": "running"}  # the other side adds fields beside these
    assert health_document(svc) == first and "market_state" not in health_document(svc)
    assert set(merged) == KEYS | {"market_state"}
    assert (svc.state, dict(svc.stats), svc.owner, svc._owner_problem, svc.outbox) == before


# ---- the settings --------------------------------------------------------------------------------


def test_the_record_setting(tmp_path):
    assert Settings.from_env({}).deployed_tag_file == DEPLOYED_TAG_FILE
    moved = Settings.from_env({"SUDARSHAN_DEPLOYED_TAG_FILE": str(tmp_path / "tag")})
    assert moved.deployed_tag_file == tmp_path / "tag"
    assert Settings.from_env({"SUDARSHAN_DEPLOYED_TAG_FILE": ""}).deployed_tag_file == (
        DEPLOYED_TAG_FILE
    )


# ---- the one listener: the market alerts' API on 5582 ------------------------------------------


def _market_api(service):
    from cryptography.fernet import Fernet

    from sudarshan.alerts.market.api import MarketApi, OffDesk

    api = MarketApi(OffDesk(), Fernet(Fernet.generate_key()))
    api.base_health = lambda: health_document(service)
    return api


def test_the_one_health_answer_carries_the_service_and_the_market_fields(settings_for, tmp_path):
    tag = tmp_path / "tag"
    tag.write_text("v9.8.7\n")

    async def scenario():
        svc = _service(settings_for, SUDARSHAN_DEPLOYED_TAG_FILE=str(tag))
        api = _market_api(svc)
        await api.start(0)
        try:
            port = api.server.sockets[0].getsockname()[1]
            answer = await _get(port)
        finally:
            await api.close()
        body = answer.json()
        assert answer.status_code == 200
        assert body["release"] == "v9.8.7"
        assert body["state"] == svc.state
        assert body["market_alerts"] == "off"
        assert {"market_state", "alerts_active", "instruments"} <= set(body)
        assert KEYS <= set(body)

    asyncio.run(scenario())


def test_nothing_listens_on_5585_in_the_running_service(settings_for, fast):
    assert not hasattr(Settings.from_env({}), "health_port")

    async def scenario():
        svc = _service(settings_for)
        api = _market_api(svc)
        task = asyncio.create_task(service_module._serve(svc, None, api, 0))
        try:
            await wait_until(lambda: api.server is not None)
            port = api.server.sockets[0].getsockname()[1]
            assert (await _get(port)).json()["service"] == "sudarshan-alerts"
            with pytest.raises(OSError):
                await asyncio.open_connection("127.0.0.1", 5585)
        finally:
            svc.stop("test")
            await asyncio.wait_for(task, 10)

    asyncio.run(scenario())
