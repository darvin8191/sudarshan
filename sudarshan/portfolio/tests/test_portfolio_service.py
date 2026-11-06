"""The portfolio service and its sealed local API (M7.18): transport, switch, port, life cycle.

Against a desk and ops the test controls: nothing here needs a store, Upstox or OpenAlgo. The
store is a fake behind the service's seam.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import threading
from pathlib import Path

import httpx
import pytest

from sudarshan.autologin.handoff import NonceBook, seal
from sudarshan.portfolio import api as api_module
from sudarshan.portfolio import config, ops, service
from sudarshan.portfolio.api import ApiError, PortfolioApi
from sudarshan.portfolio.core import kinds
from sudarshan.portfolio.service import Service
from sudarshan.vault import ALERTS_API_INFO, PORTFOLIO_API_INFO, fernet_for

PEPPER = "p" * 48  # fake, test only
FERNET = fernet_for(PEPPER, PORTFOLIO_API_INFO)
UNIT = Path(__file__).resolve().parents[2] / "ops/systemd/sudarshan-portfolio.service"


class Desk:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def health(self) -> dict:
        return {"service": "sudarshan-portfolio", "portfolio_state": {"state": "running"}}


def registry_with(calls: list, **answers) -> dict:
    book: dict = {}
    for name in kinds.OP_NAMES:
        if name in answers or name in ("state", "import_preview", "tab_create"):

            async def handler(ctx, message, _name=name):
                calls.append((_name, dict(message)))
                answer = answers.get(_name, {"op": _name})
                if isinstance(answer, Exception):
                    raise answer
                return answer

            book[name] = handler
    return book


def sealed(op: str, fernet=FERNET, **fields) -> bytes:
    principal = fields.pop("principal", "owner")
    return seal(fernet, op, principal=principal, args=fields)


async def served(desk=None, registry=None, **options):
    server = PortfolioApi(desk or Desk(), FERNET, registry=registry or {}, **options)
    await server.start(0)
    return server, f"http://127.0.0.1:{server.port}"


async def post(base: str, op: str, body: bytes, **headers) -> httpx.Response:
    async with httpx.AsyncClient(trust_env=False) as client:
        return await client.post(f"{base}/v1/{op}", content=body, headers=headers, timeout=10)


def run(scenario) -> None:
    asyncio.run(scenario())


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# ---- the seal ---------------------------------------------------------------------------------


def test_a_sealed_request_reaches_its_op_and_the_answer_is_the_envelope():
    async def scenario():
        calls: list = []
        server, base = await served(registry=registry_with(calls, state={"tabs": []}))
        try:
            answer = await post(base, "state", sealed("state", x=1))
            assert answer.status_code == 200
            assert answer.json() == {"status": "success", "data": {"tabs": []}}
            assert calls[0][0] == "state" and calls[0][1]["x"] == 1
            assert answer.headers["cache-control"] == "no-store"
        finally:
            await server.close()

    run(scenario)


def test_the_op_in_the_path_must_be_the_op_inside_the_seal():
    async def scenario():
        calls: list = []
        server, base = await served(registry=registry_with(calls))
        try:
            answer = await post(base, "tab_create", sealed("state"))
            assert answer.status_code == 403 and answer.json()["code"] == "forbidden"
            assert calls == []
        finally:
            await server.close()

    run(scenario)


def test_a_seal_is_accepted_once():
    async def scenario():
        calls: list = []
        server, base = await served(registry=registry_with(calls))
        try:
            body = sealed("state")
            assert (await post(base, "state", body)).status_code == 200
            again = await post(base, "state", body)
            assert again.status_code == 403
            assert len(calls) == 1
        finally:
            await server.close()

    run(scenario)


def test_a_seal_lives_sixty_seconds():
    """Mutation target 25: a seal 59 s old opens, one 61 s old does not."""
    now = 1_800_000_000.0

    async def scenario():
        calls: list = []
        server, base = await served(registry=registry_with(calls), clock=lambda: now)
        try:
            ok = await post(base, "state", seal(FERNET, "state", now=now - 59, principal="owner"))
            late = await post(base, "state", seal(FERNET, "state", now=now - 61, principal="owner"))
            future = await post(
                base, "state", seal(FERNET, "state", now=now + 61, principal="owner")
            )
            assert ok.status_code == 200
            assert late.status_code == 403 and future.status_code == 403
            assert len(calls) == 1
        finally:
            await server.close()

    run(scenario)
    assert api_module.SEAL_TTL_S == 60


def test_a_seal_under_another_key_or_another_purpose_never_opens():
    async def scenario():
        calls: list = []
        server, base = await served(registry=registry_with(calls))
        try:
            other = fernet_for(PEPPER, ALERTS_API_INFO)
            assert (await post(base, "state", sealed("state", other))).status_code == 403
            wrong_pepper = fernet_for("q" * 48, PORTFOLIO_API_INFO)
            assert (await post(base, "state", sealed("state", wrong_pepper))).status_code == 403
            assert (await post(base, "state", b"not a token")).status_code == 403
            assert (await post(base, "state", b"")).status_code in (403, 411)
            assert calls == []
        finally:
            await server.close()

    run(scenario)


def test_only_the_owner_is_served():
    async def scenario():
        calls: list = []
        server, base = await served(registry=registry_with(calls))
        try:
            for principal in ("viewer", "", None):
                body = seal(FERNET, "state", principal=principal)
                assert (await post(base, "state", body)).status_code == 403
            body = seal(FERNET, "state")
            assert (await post(base, "state", body)).json()["code"] == "principal"
            assert calls == []
        finally:
            await server.close()

    run(scenario)


def test_a_full_nonce_book_answers_busy_and_forgets_nothing():
    async def scenario():
        calls: list = []
        server, base = await served(registry=registry_with(calls), nonces=NonceBook(2))
        try:
            first, second = sealed("state"), sealed("state")
            assert (await post(base, "state", first)).status_code == 200
            assert (await post(base, "state", second)).status_code == 200
            busy = await post(base, "state", sealed("state"))
            assert busy.status_code == 503 and busy.json()["code"] == "busy"
            assert (await post(base, "state", first)).status_code == 403  # still remembered
        finally:
            await server.close()

    run(scenario)


# ---- the transport ------------------------------------------------------------------------------


def test_a_request_carrying_an_origin_is_refused():
    async def scenario():
        calls: list = []
        server, base = await served(registry=registry_with(calls))
        try:
            answer = await post(base, "state", sealed("state"), Origin="http://evil.example")
            assert answer.status_code == 403
            async with httpx.AsyncClient(trust_env=False) as client:
                health = await client.get(f"{base}/health", headers={"Origin": "http://x"})
            assert health.status_code == 403
            assert calls == []
        finally:
            await server.close()

    run(scenario)


async def raw(port: int, head: bytes, body: bytes = b"") -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(head + body)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), 10)
    writer.close()
    return data


def test_the_size_limit_is_checked_from_the_head_before_the_body_is_read():
    async def scenario():
        server, _ = await served(registry=registry_with([]))
        try:
            for op, size in (("state", 64 * 1024 + 1), ("import_preview", 2 * 1024 * 1024 + 1)):
                head = f"POST /v1/{op} HTTP/1.1\r\nContent-Length: {size}\r\n\r\n".encode()
                answer = await raw(server.port, head)  # no body is ever sent
                assert answer.startswith(b"HTTP/1.1 413"), op
        finally:
            await server.close()

    run(scenario)


def test_an_import_may_be_far_larger_than_any_other_request():
    """X9: 64 KiB for the API, the import limit for an import (a file of 1.2 MB seals to 1.9 MiB)."""
    big = "x" * 1_400_000

    async def scenario():
        calls: list = []
        server, base = await served(registry=registry_with(calls))
        try:
            body = sealed("import_preview", text=big)
            assert 64 * 1024 < len(body) <= kinds.IMPORT_MAX_BYTES
            assert (await post(base, "import_preview", body)).status_code == 200
            assert calls[0][1]["text"] == big
            small = sealed("state", pad="y" * 70_000)
            assert (await post(base, "state", small)).status_code == 413
            assert (await post(base, "state", sealed("state", pad="y" * 40_000))).status_code == 200
        finally:
            await server.close()

    run(scenario)


def test_every_op_has_a_limit_and_only_the_upload_has_the_larger_one():
    assert set(api_module.LIMITS) == set(kinds.OP_NAMES)
    larger = {name for name, size in api_module.LIMITS.items() if size > 64 * 1024}
    assert larger == {spec.name for spec in kinds.OPS if spec.upload} == {"import_preview"}


def test_the_head_is_checked_in_the_servers_own_words():
    async def scenario():
        server, base = await served(registry=registry_with([]))
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                assert (await client.get(f"{base}/v1/state", timeout=5)).status_code == 405
                assert (await client.get(f"{base}/v1/nothing", timeout=5)).status_code == 404
                assert (await client.post(f"{base}/health", timeout=5)).status_code == 405
            no_length = b"POST /v1/state HTTP/1.1\r\n\r\n"
            assert (await raw(server.port, no_length)).startswith(b"HTTP/1.1 411")
            assert (await raw(server.port, b"nonsense\r\n\r\n")).startswith(b"HTTP/1.1 400")
        finally:
            await server.close()

    run(scenario)


def test_an_op_that_fails_answers_in_words_and_an_error_is_passed_on():
    async def scenario():
        calls: list = []
        errors = {"tab_create": ApiError(409, "conflict", "Another change came first.", "version")}
        server, base = await served(registry=registry_with(calls, **errors))
        try:
            answer = await post(base, "tab_create", sealed("tab_create"))
            assert answer.status_code == 409
            assert answer.json() == {"status": "error", "code": "conflict",
                                     "message": "Another change came first.", "field": "version"}  # fmt: skip

            async def broken(ctx, message):
                raise RuntimeError("secret detail")

            server.registry = {"state": broken}
            answer = await post(base, "state", sealed("state"))
            assert answer.status_code == 500
            assert "secret" not in answer.text and "RuntimeError" not in answer.text
        finally:
            await server.close()

    run(scenario)


# ---- switched off, and ops not built yet --------------------------------------------------------


def test_switched_off_every_op_answers_enabled_false_and_does_nothing():
    async def scenario():
        calls: list = []
        server, base = await served(Desk(enabled=False), registry_with(calls))
        try:
            for name in kinds.OP_NAMES:
                answer = await post(base, name, sealed(name))
                assert answer.status_code == 200, name
                data = answer.json()["data"]
                assert data["enabled"] is False
                assert data["message"] == "The portfolio is switched off on the server."
            assert calls == []
            # a seal that does not open is still refused: off is not a way in
            assert (await post(base, "state", b"junk")).status_code == 403
        finally:
            await server.close()

    run(scenario)


def test_an_op_not_built_yet_says_so():
    async def scenario():
        server, base = await served(registry={})
        try:
            answer = await post(base, "tab_get", sealed("tab_get", id=1))
            assert answer.status_code == 501
            assert answer.json() == {"status": "error", "code": "not_ready",
                                     "message": "This part of the portfolio is not ready yet."}  # fmt: skip
        finally:
            await server.close()

    run(scenario)


def test_the_registry_takes_contract_names_once_each():
    book: dict = {}

    @ops.op("state", book)
    async def state(ctx, message):
        return {}

    assert book["state"] is state
    with pytest.raises(ValueError, match="twice"):
        ops.op("state", book)(state)
    with pytest.raises(ValueError, match="not an op"):
        ops.op("made_up", book)(state)


# ---- health ---------------------------------------------------------------------------------------


def settings_for(tmp_path: Path, port: int = 0, enabled: bool = True) -> config.Settings:
    tag = tmp_path / "deployed_tag"
    tag.write_text("v9.8.7\n")
    return config.Settings(enabled=enabled, http_port=port, deployed_tag_file=tag,
                           state_dir=tmp_path)  # fmt: skip


def test_health_is_unsealed_names_the_release_and_carries_no_owner_data(tmp_path):
    async def scenario():
        svc = Service(settings_for(tmp_path), FERNET, jobs={})
        server = svc.api
        await server.start(0)
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                answer = await client.get(f"http://127.0.0.1:{server.port}/health", timeout=5)
            body = answer.json()
            assert answer.status_code == 200
            assert body["service"] == "sudarshan-portfolio"
            assert body["release"] == "v9.8.7"
            assert body["state"] == "running"
            assert set(body["portfolio_state"]) == {"enabled", "state", "store", "jobs"}
            assert body["portfolio_state"]["store"] == "closed"
            text = answer.text
            assert PEPPER not in text and "owner" not in text.replace('"owner"', "")
        finally:
            await server.close()

    run(scenario)


# ---- port taken, jobs, life cycle ---------------------------------------------------------------


class FakeStore:
    def __init__(self):
        self.closed = 0


def fds() -> int:
    return len(os.listdir("/proc/self/fd"))


def test_a_taken_port_is_said_in_words_the_jobs_run_and_the_port_is_tried_again(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="sudarshan.portfolio")
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen(1)
    port = held.getsockname()[1]
    ticks: list[int] = []
    jobs: dict = {}

    @service.job("count", jobs)
    async def count(svc):
        while True:
            ticks.append(1)
            await asyncio.sleep(0.01)

    async def scenario():
        svc = Service(settings_for(tmp_path, port), FERNET, jobs=jobs, port_retry_s=0.05)
        task = asyncio.create_task(svc.run())
        await asyncio.sleep(0.3)
        assert svc.state == "port_taken"  # it did not exit, and no other port was tried
        assert len(ticks) > 5 and not task.done()
        assert svc.health()["portfolio_state"]["jobs"] == {"count": "running"}
        held.close()
        for _ in range(600):
            if svc.state == "running":
                break
            await asyncio.sleep(0.05)
        assert svc.state == "running"
        async with httpx.AsyncClient(trust_env=False) as client:
            assert (
                await client.get(f"http://127.0.0.1:{port}/health", timeout=5)
            ).status_code == 200
        svc.stop()
        await asyncio.wait_for(task, 10)

    run(scenario)
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1  # said once, not every minute
    assert f"port {port} on this server is taken" in errors[0]
    assert "tries again every minute" in errors[0]
    assert "Errno" not in errors[0] and "OSError" not in errors[0]


def test_start_then_stop_opens_the_store_once_and_leaves_nothing_open(tmp_path):
    opened: list[FakeStore] = []

    def opener(settings):
        store = FakeStore()
        opened.append(store)
        return store

    def closer(store):
        store.closed += 1

    seen: list = []
    jobs: dict = {}

    @service.job("watch", jobs)
    async def watch(svc):
        seen.append(svc.store)
        await asyncio.sleep(3600)

    async def scenario():
        svc = Service(settings_for(tmp_path, free_port()), FERNET, jobs=jobs,
                      opener=opener, closer=closer)  # fmt: skip
        task = asyncio.create_task(svc.run())
        for _ in range(1500):
            if svc.state == "running":
                break
            await asyncio.sleep(0.02)
        assert svc.health()["portfolio_state"]["store"] == "open"
        assert seen == [opened[0]]
        svc.stop()
        await asyncio.wait_for(task, 10)
        assert svc.health()["portfolio_state"]["store"] == "closed"

    before_threads, before_fds = set(threading.enumerate()), fds()
    run(scenario)
    assert [s.closed for s in opened] == [1]
    assert set(threading.enumerate()) <= before_threads
    assert fds() == before_fds


def test_a_job_that_fails_does_not_stop_the_api_and_the_store_still_closes(tmp_path):
    closed: list = []
    jobs: dict = {}

    @service.job("bad", jobs)
    async def bad(svc):
        raise RuntimeError("boom")

    async def scenario():
        svc = Service(settings_for(tmp_path, free_port()), FERNET, jobs=jobs,
                      opener=lambda s: FakeStore(), closer=closed.append)  # fmt: skip
        task = asyncio.create_task(svc.run())
        await asyncio.sleep(0.2)
        assert svc.health()["portfolio_state"]["jobs"] == {"bad": "ended"}
        assert svc.state == "running"
        svc.stop()
        await asyncio.wait_for(task, 10)

    run(scenario)
    assert len(closed) == 1


def test_a_store_that_will_not_close_is_logged_and_the_stop_finishes(tmp_path, caplog):
    def closer(store):
        raise OSError("disk")

    async def scenario():
        svc = Service(settings_for(tmp_path, free_port()), FERNET, jobs={},
                      opener=lambda s: FakeStore(), closer=closer)  # fmt: skip
        task = asyncio.create_task(svc.run())
        await asyncio.sleep(0.1)
        svc.stop()
        await asyncio.wait_for(task, 10)

    run(scenario)
    assert any("did not close cleanly" in r.getMessage() for r in caplog.records)


def test_switched_off_the_store_is_not_opened_and_no_job_runs(tmp_path):
    opened: list = []
    ran: list = []
    jobs: dict = {}

    @service.job("never", jobs)
    async def never(svc):
        ran.append(1)

    async def scenario():
        port = free_port()
        svc = Service(settings_for(tmp_path, port, enabled=False), FERNET, jobs=jobs,
                      opener=lambda s: opened.append(1), closer=lambda s: None)  # fmt: skip
        task = asyncio.create_task(svc.run())
        for _ in range(1500):
            if svc.state == "off":
                break
            await asyncio.sleep(0.02)
        assert svc.state == "off"
        answer = await post(f"http://127.0.0.1:{port}", "state", sealed("state"))
        assert answer.json()["data"]["enabled"] is False
        svc.stop()
        await asyncio.wait_for(task, 10)

    run(scenario)
    assert opened == [] and ran == []


def test_the_job_registry_refuses_a_name_twice():
    jobs: dict = {}

    async def one(svc): ...

    service.job("a", jobs)(one)
    with pytest.raises(ValueError, match="twice"):
        service.job("a", jobs)(one)


# ---- settings -------------------------------------------------------------------------------------


def test_the_defaults_are_the_designs():
    s = config.Settings.from_env({})
    assert (s.enabled, s.http_port, s.feed_url) == (False, 5583, "http://127.0.0.1:5580")
    assert (s.max_claims, s.amfi, s.eod_after_min, s.settle_at_min) == (200, True, 15, 560)
    assert str(s.token_file) == "/etc/sudarshan/upstox_analytics_token" and s.log_level == "INFO"
    assert str(s.state_dir) == "/var/lib/sudarshan-portfolio"


def test_every_setting_is_read():
    s = config.Settings.from_env({
        "SUDARSHAN_PORTFOLIO": "on", "SUDARSHAN_PORTFOLIO_HTTP_PORT": "6000",
        "SUDARSHAN_PORTFOLIO_FEED_URL": "http://localhost:5590/",
        "SUDARSHAN_PORTFOLIO_MAX_CLAIMS": "50", "SUDARSHAN_PORTFOLIO_AMFI": "off",
        "SUDARSHAN_PORTFOLIO_EOD_AFTER_MIN": "30", "SUDARSHAN_PORTFOLIO_SETTLE_AT": "9:05",
        "SUDARSHAN_UPSTOX_TOKEN_FILE": "/tmp/t", "SUDARSHAN_PORTFOLIO_LOG_LEVEL": "debug",
        "STATE_DIRECTORY": "/var/lib/a:/var/lib/b",
    })  # fmt: skip
    assert (s.enabled, s.http_port, s.feed_url, s.max_claims) == (
        True,
        6000,
        "http://localhost:5590",
        50,
    )
    assert (s.amfi, s.eod_after_min, s.settle_at_min) == (False, 30, 545)
    assert (str(s.token_file), s.log_level, str(s.state_dir)) == ("/tmp/t", "DEBUG", "/var/lib/a")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SUDARSHAN_PORTFOLIO", "maybe"),
        ("SUDARSHAN_PORTFOLIO_HTTP_PORT", "0"),
        ("SUDARSHAN_PORTFOLIO_HTTP_PORT", "70000"),
        ("SUDARSHAN_PORTFOLIO_FEED_URL", "http://example.com:5580"),
        ("SUDARSHAN_PORTFOLIO_FEED_URL", "https://127.0.0.1:5580"),
        ("SUDARSHAN_PORTFOLIO_MAX_CLAIMS", "201"),
        ("SUDARSHAN_PORTFOLIO_AMFI", "2"),
        ("SUDARSHAN_PORTFOLIO_EOD_AFTER_MIN", "-1"),
        ("SUDARSHAN_PORTFOLIO_SETTLE_AT", "25:00"),
        ("SUDARSHAN_PORTFOLIO_SETTLE_AT", "noon"),
    ],
)
def test_a_setting_that_cannot_be_used_is_refused_naming_it(name, value):
    with pytest.raises(config.ConfigError, match=name):
        config.Settings.from_env({name: value})


def test_openalgos_env_sits_under_the_environment(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("SUDARSHAN_PORTFOLIO='on'\nAPI_KEY_PEPPER=" + PEPPER + "\nOTHER=1\n")
    under = config.read_openalgo_env(env_file)
    assert under["SUDARSHAN_PORTFOLIO"] == "on"
    assert config.Settings.from_env(config.merged_env({}, under)).enabled is True
    assert (
        config.Settings.from_env(config.merged_env({"SUDARSHAN_PORTFOLIO": "off"}, under)).enabled
        is False
    )
    assert config.read_pepper(config.merged_env({}, under)) == PEPPER
    with pytest.raises(config.ConfigError, match="API_KEY_PEPPER"):
        config.read_pepper({"API_KEY_PEPPER": "short"})
    with pytest.raises(config.ConfigError, match="cannot read"):
        config.read_openalgo_env(tmp_path / "missing")


def test_the_pepper_is_not_a_setting():
    s = config.Settings.from_env({"API_KEY_PEPPER": PEPPER})
    assert PEPPER not in repr(s)


def test_a_missing_pepper_stops_the_service_with_status_2(tmp_path, monkeypatch):
    monkeypatch.setenv("SUDARSHAN_OPENALGO_ENV_FILE", str(tmp_path / "no.env"))
    monkeypatch.delenv("API_KEY_PEPPER", raising=False)
    assert service.main() == 2


# ---- the unit -------------------------------------------------------------------------------------


def unit_lines() -> list[str]:
    return [line.strip() for line in UNIT.read_text(encoding="utf-8").splitlines()]


def test_the_unit_follows_the_deploys_policy_and_the_designs_limits():
    lines = unit_lines()
    for needed in ("User=sudarshan", "Group=sudarshan", "NoNewPrivileges=yes",
                   "StateDirectory=sudarshan-portfolio", "MemoryMax=256M", "MemoryHigh=192M",
                   "CPUWeight=40", "Restart=on-failure", "RestartPreventExitStatus=2",
                   "TimeoutStopSec=20", "ReadOnlyPaths=-/etc/sudarshan",
                   "EnvironmentFile=-/etc/sudarshan/portfolio.env"):  # fmt: skip
        assert needed in lines
    assert any(
        line.endswith("-m sudarshan.portfolio") and line.startswith("ExecStart=") for line in lines
    )
    assert not any(line.startswith("IPAddress") for line in lines)
    assert 192 == 256 * 3 // 4


def test_the_port_table_names_the_listener():
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    row = next(line for line in readme.splitlines() if line.startswith("| 5583 |"))
    assert "/health" in row and "sealed local API" in row


def _release_rig(tmp_path, enabled, feed_down=False):
    import dataclasses

    from portfolio_fakes import FakeFeed

    feed = FakeFeed()
    feed.url = feed.start()
    feed.down = feed_down
    settings = dataclasses.replace(
        settings_for(tmp_path, free_port(), enabled=enabled), feed_url=feed.url
    )
    return feed, Service(settings, FERNET, jobs={}, opener=lambda s: None, closer=lambda s: None)


def test_switched_off_the_claims_at_the_feed_are_dropped(tmp_path):
    feed, svc = _release_rig(tmp_path, enabled=False)

    async def scenario():
        task = asyncio.create_task(svc.run())
        for _ in range(500):
            if any(c[0] == "DELETE" for c in feed.calls):
                break
            await asyncio.sleep(0.02)
        svc.stop()
        await asyncio.wait_for(task, 10)

    try:
        run(scenario)
    finally:
        feed.stop()
    assert any(c[0] == "DELETE" and c[1] == "/claims/sudarshan-portfolio" for c in feed.calls)


def test_the_release_is_retried_while_the_feed_does_not_answer(tmp_path):
    from sudarshan.portfolio import feedlink

    feed, _ = _release_rig(tmp_path, enabled=False, feed_down=True)
    waits = []

    async def wait(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            feed.down = False  # the feed is back after two waits

    try:
        done = asyncio.run(feedlink.release(feed.url, delays=(0.0, 1.0, 2.0, 3.0), sleep=wait))
        gave_up = asyncio.run(feedlink.release("http://127.0.0.1:9", delays=(0.0, 0.5), sleep=wait))
    finally:
        feed.stop()
    assert done is True and waits[:2] == [1.0, 2.0]
    assert gave_up is False


def test_a_plain_stop_keeps_the_claims(tmp_path):
    feed, svc = _release_rig(tmp_path, enabled=True)

    async def scenario():
        task = asyncio.create_task(svc.run())
        for _ in range(500):
            if svc.state == "running":
                break
            await asyncio.sleep(0.02)
        svc.stop()
        await asyncio.wait_for(task, 10)

    try:
        run(scenario)
    finally:
        feed.stop()
    assert not any(c[0] == "DELETE" for c in feed.calls)
