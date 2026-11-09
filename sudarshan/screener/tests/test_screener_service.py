"""The screener service and its sealed local API (SV5, SV7): seal, replay, expiry, op mismatch,
Origin, size, principal, the switch, a taken port, /health per R14, settings, the life cycle.

Against a desk and ops the test controls; the store is a fake behind the service's seam.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from pathlib import Path

import httpx
import pytest

from sudarshan.autologin.handoff import NonceBook, seal
from sudarshan.screener import config, contract, ops, service
from sudarshan.screener.api import ScreenerApi
from sudarshan.screener.migrations import SchemaTooNew
from sudarshan.screener.ops import ApiError
from sudarshan.screener.service import Service
from sudarshan.screener.store import StoreError
from sudarshan.vault import (
    PORTFOLIO_API_INFO,
    SCREENER_API_INFO,
    SCREENER_INTERNAL_INFO,
    fernet_for,
)

PEPPER = "p" * 48  # fake, test only
FERNET = fernet_for(PEPPER, SCREENER_API_INFO)


class Desk:
    def __init__(self, enabled: bool = True, off_reason: str | None = None):
        self.enabled, self.off_reason = enabled, off_reason

    def health(self) -> dict:
        return {"service": "sudarshan-screener", "screener_state": {"state": "running"}}


def registry_with(calls: list, **answers) -> dict:
    book: dict = {}
    for name in contract.OP_NAMES:
        if name in answers or name in ("state", "create"):

            async def handler(ctx, args, _name=name):
                calls.append((_name, dict(args)))
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
    server = ScreenerApi(desk or Desk(), FERNET, registry=registry or {}, **options)
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
        server, base = await served(registry=registry_with(calls, state={"screens": []}))
        try:
            answer = await post(base, "state", sealed("state", x=1))
            assert answer.status_code == 200
            assert answer.json() == {"status": "success", "data": {"screens": []}}
            assert calls[0] == ("state", {"x": 1})
            assert answer.headers["cache-control"] == "no-store"
        finally:
            await server.close()

    run(scenario)


def test_the_seal_is_refused_for_the_wrong_op_a_replay_an_expiry_and_another_purpose():
    async def scenario():
        calls: list = []
        clock = [1_000_000.0]
        server, base = await served(registry=registry_with(calls), clock=lambda: clock[0])
        try:
            body = seal(FERNET, "state", now=clock[0], principal="owner", args={})
            assert (await post(base, "create", body)).status_code == 403  # op mismatch
            assert (await post(base, "state", body)).status_code == 200
            replay = await post(base, "state", body)
            assert replay.status_code == 403 and replay.json()["error"]["code"] == "forbidden"
            fresh = seal(FERNET, "state", now=clock[0], principal="owner", args={})
            clock[0] += 61
            assert (await post(base, "state", fresh)).status_code == 403  # expired
            for other in (PORTFOLIO_API_INFO, SCREENER_INTERNAL_INFO):
                foreign = seal(
                    fernet_for(PEPPER, other), "state", now=clock[0], principal="owner", args={}
                )
                assert (await post(base, "state", foreign)).status_code == 403
            assert len(calls) == 1
            words = replay.json()["error"]["message"]
            assert "OpenAlgo" in words and not any(w in words for w in ("403", "Fernet", "nonce"))
        finally:
            await server.close()

    run(scenario)


def test_a_browser_origin_an_oversized_body_and_an_unknown_op_are_refused_on_the_head():
    async def scenario():
        calls: list = []
        server, base = await served(registry=registry_with(calls))
        try:
            answer = await post(base, "state", sealed("state"), Origin="http://localhost:5000")
            assert answer.status_code == 403
            big = await post(base, "state", b"x" * (contract.API_MAX_BYTES + 1))
            assert (
                big.status_code == 413
                and big.json()["error"]["message"] == contract.WORDS["too_big"]
            )
            assert (await post(base, "nothing", sealed("state"))).status_code == 404
            async with httpx.AsyncClient(trust_env=False) as client:
                get = await client.get(f"{base}/v1/state", timeout=10)
            assert get.status_code == 405
            assert calls == []
        finally:
            await server.close()

    run(scenario)


def test_only_the_owner_principal_is_served_and_a_full_nonce_book_answers_busy():
    async def scenario():
        calls: list = []
        server, base = await served(registry=registry_with(calls), nonces=NonceBook(2))
        try:
            viewer = await post(base, "state", sealed("state", principal="viewer"))
            assert viewer.status_code == 403 and viewer.json()["error"]["code"] == "principal"
            assert (await post(base, "state", sealed("state"))).status_code == 200
            busy = await post(base, "state", sealed("state"))
            assert busy.status_code == 503 and busy.json()["error"]["code"] == "busy"
            assert len(calls) == 1
        finally:
            await server.close()

    run(scenario)


def test_switched_off_every_op_answers_off_in_words_and_runs_nothing():
    async def scenario():
        calls: list = []
        server, base = await served(Desk(enabled=False), registry_with(calls))
        try:
            answer = await post(base, "state", sealed("state"))
            assert answer.json() == {
                "status": "success",
                "data": {"enabled": False, "message": contract.WORDS["off"]},
            }
            assert calls == []
        finally:
            await server.close()
        server, base = await served(Desk(False, "Your screens were saved by a newer release."), {})
        try:
            data = (await post(base, "state", sealed("state"))).json()["data"]
            assert data["message"].startswith("Your screens")
        finally:
            await server.close()

    run(scenario)


def test_an_op_not_built_yet_an_api_error_and_a_crash_each_answer_in_the_envelope(caplog):
    async def scenario():
        calls: list = []
        registry = registry_with(
            calls,
            runs=ApiError.of("not_found", contract.WORDS["gone"]),
            preview=ApiError.of("refused", "Pick a timeframe.", field="where.c1"),
            today=RuntimeError("boom"),
        )
        server, base = await served(registry=registry)
        try:
            assert (await post(base, "results", sealed("results"))).status_code == 501
            gone = await post(base, "runs", sealed("runs", id=4))
            assert (
                gone.status_code == 404
                and gone.json()["error"]["message"] == contract.WORDS["gone"]
            )
            refused = await post(base, "preview", sealed("preview"))
            assert refused.status_code == 422 and refused.json()["error"]["field"] == "where.c1"
            with caplog.at_level(logging.ERROR, logger="sudarshan.screener"):
                crash = await post(base, "today", sealed("today"))
            assert crash.status_code == 500 and "boom" not in crash.text
            assert any("failed" in r.message for r in caplog.records)
        finally:
            await server.close()

    run(scenario)


def test_health_is_unsealed_and_carries_the_r14_fields():
    async def scenario():
        server, base = await served()
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                answer = await client.get(f"{base}/health", timeout=10)
            assert answer.status_code == 200
            assert answer.json()["service"] == "sudarshan-screener"
            assert "screener_state" in answer.json()
        finally:
            await server.close()

    run(scenario)


# ---- the ops registry ------------------------------------------------------------------------
def test_the_registry_takes_contract_names_once():
    book: dict = {}

    @ops.op("state", book)
    async def state(ctx, args):
        return {}

    with pytest.raises(ValueError):
        ops.op("state", book)(state)
    with pytest.raises(ValueError):
        ops.op("not_an_op", book)(state)
    assert set(ops.REGISTRY) <= set(contract.OP_NAMES)


# ---- settings --------------------------------------------------------------------------------
def test_settings_default_off_on_5584_and_refuse_a_remote_feed_or_a_bad_switch(tmp_path):
    settings = config.Settings.from_env({})
    assert settings.enabled is False and settings.http_port == 5584
    assert (
        settings.feed_url == "http://127.0.0.1:5580" and settings.feed_zmq == "tcp://127.0.0.1:5581"
    )
    assert settings.max_keys == 320 and settings.keep_days == 100 and settings.replay is False
    on = config.Settings.from_env({"SUDARSHAN_SCREENER": "on", "STATE_DIRECTORY": "/a:/b"})
    assert on.enabled is True and on.state_dir == Path("/a")
    for bad in (
        {"SUDARSHAN_SCREENER": "maybe"},
        {"SUDARSHAN_SCREENER_FEED_URL": "http://10.0.0.5:5580"},
        {"SUDARSHAN_SCREENER_FEED_ZMQ": "tcp://10.0.0.5:5581"},
        {"SUDARSHAN_SCREENER_MAX_KEYS": "701"},
        {"SUDARSHAN_SCREENER_KEEP_DAYS": "30"},
        {"SUDARSHAN_SCREENER_HTTP_PORT": "abc"},
    ):
        with pytest.raises(config.ConfigError):
            config.Settings.from_env(bad)
    with pytest.raises(config.ConfigError):
        config.read_pepper({})
    assert config.read_pepper({"API_KEY_PEPPER": PEPPER}) == PEPPER
    env_file = tmp_path / ".env"
    env_file.write_text("API_KEY_PEPPER='q'\nDATABASE_URL=sqlite:///db/openalgo.db\n")
    merged = config.merged_env({"API_KEY_PEPPER": "r"}, config.read_openalgo_env(env_file))
    assert merged["API_KEY_PEPPER"] == "r" and merged["DATABASE_URL"].endswith("openalgo.db")


# ---- the service -----------------------------------------------------------------------------
class FakeStore:
    closed = False

    def counts(self, who):
        return {"screens": 2, "active": 1, "outbox_pending": 0}

    def close(self):
        self.closed = True


def settings_for(port: int, enabled=True, **extra) -> config.Settings:
    return config.Settings.from_env({
        "SUDARSHAN_SCREENER": "on" if enabled else "off",
        "SUDARSHAN_SCREENER_HTTP_PORT": str(port),
        "SUDARSHAN_DEPLOYED_TAG_FILE": "/nonexistent/deployed_tag",
        **extra,
    })  # fmt: skip


async def until(predicate, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached")


def test_the_service_opens_the_store_serves_health_runs_jobs_and_closes_everything():
    async def scenario():
        port = free_port()
        fake = FakeStore()
        seen: list = []
        closed: list = []

        async def heartbeat(svc):
            seen.append(svc.store)
            await asyncio.sleep(3600)

        svc = Service(
            settings_for(port), FERNET,
            jobs={"heartbeat": heartbeat}, opener=lambda s: fake, closer=closed.append,
        )  # fmt: skip
        task = asyncio.create_task(svc.run())
        await until(lambda: svc.state == "running")
        async with httpx.AsyncClient(trust_env=False) as client:
            health = (await client.get(f"http://127.0.0.1:{port}/health", timeout=5)).json()
        assert health["service"] == "sudarshan-screener" and health["state"] == "running"
        state = health["screener_state"]
        assert state["enabled"] is True and state["store"] == "open"
        assert state["counts"] == {"screens": 2, "active": 1, "outbox_pending": 0}
        assert state["jobs"] == {"heartbeat": "running"}
        assert seen == [fake]
        svc.stop("test")
        await asyncio.wait_for(task, 5)
        assert closed == [fake] and svc.store is None and svc.api.server is None

    run(scenario)


def test_a_newer_file_or_an_unopenable_store_keeps_the_screener_off_and_the_service_up():
    async def scenario():
        for failure, words in (
            (SchemaTooNew(5, 1), "newer SUDARSHAN release"),
            (StoreError("Your screens could not be opened.", "disk full"), "could not be opened"),
        ):
            port = free_port()

            def opener(settings, _f=failure):
                raise _f

            ran: list = []

            async def never(svc, _ran=ran):
                _ran.append(1)

            svc = Service(settings_for(port), FERNET, jobs={"never": never}, opener=opener)
            task = asyncio.create_task(svc.run())
            await until(lambda _svc=svc: _svc.state == "off")
            assert svc.enabled is False and words in (svc.off_reason or "")
            async with httpx.AsyncClient(trust_env=False) as client:
                answer = await client.post(
                    f"http://127.0.0.1:{port}/v1/state", content=sealed("state"), timeout=5
                )
            assert (
                answer.json()["data"]["enabled"] is False
                and words in answer.json()["data"]["message"]
            )
            assert ran == []
            svc.stop()
            await asyncio.wait_for(task, 5)

    run(scenario)


def test_a_taken_port_is_said_once_in_words_and_tried_again_never_another_port(caplog):
    async def scenario():
        port = free_port()
        holder = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", port)
        svc = Service(settings_for(port, enabled=False), FERNET, jobs={}, port_retry_s=0.05)
        with caplog.at_level(logging.ERROR, logger="sudarshan.screener"):
            task = asyncio.create_task(svc.run())
            await until(lambda: svc.state == "port_taken")
            await asyncio.sleep(0.2)
        errors = [r for r in caplog.records if "taken" in r.message]
        assert len(errors) == 1 and str(port) in errors[0].message
        assert "Free the port" in errors[0].message and "OSError" not in errors[0].message
        holder.close()
        await holder.wait_closed()
        await until(lambda: svc.state == "off")
        assert svc.api.port == port
        svc.stop()
        await asyncio.wait_for(task, 5)

    run(scenario)


def test_the_unit_file_is_the_screeners_and_loopback_only():
    unit = Path(__file__).resolve().parents[2] / "ops/systemd/sudarshan-screener.service"
    text = unit.read_text()
    assert "ExecStart=/opt/sudarshan/apps/openalgo/.venv/bin/python -m sudarshan.screener" in text
    assert "StateDirectory=sudarshan-screener" in text
    assert "EnvironmentFile=-/etc/sudarshan/screener.env" in text
    assert "IPAddressDeny=any" in text and "IPAddressAllow=localhost" in text
    assert "RestartPreventExitStatus=2" in text and "User=sudarshan" in text
    assert "MemoryMax=768M" in text and "CPUWeight=40" in text and "5584" in text
    assert "NoNewPrivileges=yes" in text and "ProtectSystem=strict" in text
