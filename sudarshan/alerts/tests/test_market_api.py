"""The market alerts' sealed local API (M10.1): its transport, against a desk the test controls."""

from __future__ import annotations

import asyncio
import json
import socket

import httpx
import pytest

from sudarshan.alerts.market import api
from sudarshan.alerts.market.api import ApiError, MarketApi, OffDesk
from sudarshan.autologin.handoff import NonceBook, seal
from sudarshan.vault import (
    ALERTS_API_INFO,
    ALERTS_PUSH_INFO,
    ALERTS_SCREENER_DIGEST_INFO,
    fernet_for,
)

PEPPER = "p" * 48  # fake, test only
FERNET = fernet_for(PEPPER, ALERTS_API_INFO)


class Desk:
    """Records what reached it; answers ``answers[op]`` (a value, or an exception to raise)."""

    def __init__(self, **answers):
        self.answers, self.calls = answers, []

    def health(self) -> dict:
        return {"service": "sudarshan-alerts", "market_alerts": "on", "state": "running",
                "alerts_active": 3, "instruments": 2}  # fmt: skip

    async def call(self, op: str, message) -> object:
        self.calls.append((op, dict(message)))
        answer = self.answers.get(op, {"op": op})
        if isinstance(answer, Exception):
            raise answer
        return answer


async def served(desk, **options) -> tuple[MarketApi, str]:
    server = MarketApi(desk, FERNET, **options)
    await server.start(0)
    port = server.server.sockets[0].getsockname()[1]
    return server, f"http://127.0.0.1:{port}"


def sealed(op: str, fernet=FERNET, **fields) -> bytes:
    return seal(fernet, op, **({"principal": "owner"} | fields))


def run(scenario) -> None:
    asyncio.run(scenario())


async def post(base: str, op: str, body: bytes, **headers) -> httpx.Response:
    async with httpx.AsyncClient(trust_env=False) as client:
        return await client.post(f"{base}/v1/{op}", content=body, headers=headers, timeout=5)


def test_health_is_unsealed_and_carries_counts_only():
    async def scenario():
        server, base = await served(Desk())
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                answer = await client.get(f"{base}/health", timeout=5)
                assert answer.status_code == 200
                assert answer.json() == {"service": "sudarshan-alerts", "market_alerts": "on",
                                         "state": "running", "alerts_active": 3,
                                         "instruments": 2}  # fmt: skip
                assert (await client.post(f"{base}/health", timeout=5)).status_code == 405
                assert (await client.get(f"{base}/v1/state", timeout=5)).status_code == 405
                assert (await client.get(f"{base}/v1/nothing", timeout=5)).status_code == 404
        finally:
            await server.close()

    run(scenario)


def test_a_sealed_op_reaches_the_desk_with_its_fields_once():
    async def scenario():
        desk = Desk(create={"id": 7})
        server, base = await served(desk)
        try:
            body = sealed("create", alert={"symbol": "SBIN"})
            answer = await post(base, "create", body)
            assert answer.status_code == 200
            assert answer.json() == {"status": "success", "data": {"id": 7}}
            assert desk.calls[0][0] == "create"
            assert desk.calls[0][1]["alert"] == {"symbol": "SBIN"}
            again = await post(base, "create", body)  # the same seal: its nonce is spent
            assert again.status_code == 403 and again.json()["code"] == "forbidden"
            assert len(desk.calls) == 1
        finally:
            await server.close()

    run(scenario)


def test_a_seal_for_another_op_or_purpose_or_key_never_opens():
    async def scenario():
        desk = Desk()
        server, base = await served(desk)
        try:
            refused = [
                await post(base, "delete", sealed("create")),  # the path's op is not the seal's
                await post(base, "state", sealed("state", fernet_for(PEPPER, ALERTS_PUSH_INFO))),
                await post(
                    base, "state", sealed("state", fernet_for(PEPPER, ALERTS_SCREENER_DIGEST_INFO))
                ),  # X9
                await post(base, "state", sealed("state", fernet_for("q" * 48, ALERTS_API_INFO))),
                await post(base, "state", b"not a seal"),
            ]
            assert [r.status_code for r in refused] == [403] * 5
            assert {r.json()["code"] for r in refused} == {"forbidden"}
            assert "OpenAlgo's .env" in refused[0].json()["message"]
            assert desk.calls == []
        finally:
            await server.close()

    run(scenario)


def test_an_expired_seal_is_refused():
    async def scenario():
        desk = Desk()
        server, base = await served(desk)
        try:
            old = seal(FERNET, "state", now=1_000_000_000, principal="owner")
            assert (await post(base, "state", old)).status_code == 403
            assert desk.calls == []
        finally:
            await server.close()

    run(scenario)


def test_only_the_owner_is_served():
    async def scenario():
        desk = Desk()
        server, base = await served(desk)
        try:
            for who in ("family", "", None):
                answer = await post(base, "state", seal(FERNET, "state", principal=who))
                assert answer.status_code == 403
                assert answer.json() == {"status": "error", "code": "principal",
                                         "message": api.WORDS["principal"]}  # fmt: skip
            answer = await post(base, "state", seal(FERNET, "state"))  # no principal at all
            assert answer.status_code == 403 and desk.calls == []
        finally:
            await server.close()

    run(scenario)


def test_size_origin_and_length_are_checked_before_the_seal():
    async def scenario():
        desk = Desk()
        server, base = await served(desk)
        try:
            big = sealed("create", note="x" * (api.MAX_SEALED + 1))
            assert (await post(base, "create", big)).status_code == 413
            from_page = await post(base, "state", sealed("state"), Origin="http://example.test")
            assert from_page.status_code == 403
            reader, writer = await asyncio.open_connection("127.0.0.1", server.server.sockets[0]
                                                           .getsockname()[1])  # fmt: skip
            writer.write(b"POST /v1/state HTTP/1.1\r\nHost: x\r\n\r\n")  # no Content-Length
            await writer.drain()
            reply = await reader.read()
            writer.close()
            assert reply.startswith(b"HTTP/1.1 411 ")
            assert desk.calls == []
        finally:
            await server.close()

    run(scenario)


def test_a_smaller_limit_per_route_holds():
    async def scenario():
        desk = Desk()
        server, base = await served(desk, max_bytes=512)
        try:
            assert (await post(base, "create", sealed("create", note="x" * 600))).status_code == 413
            assert (await post(base, "create", sealed("create", note="x"))).status_code == 200
        finally:
            await server.close()

    run(scenario)


def test_a_full_nonce_book_answers_busy_and_forgets_nothing_valid():
    async def scenario():
        desk = Desk()
        server, base = await served(desk, nonces=NonceBook(2))
        try:
            first = sealed("state")
            assert (await post(base, "state", first)).status_code == 200
            assert (await post(base, "state", sealed("state"))).status_code == 200
            busy = await post(base, "state", sealed("state"))
            assert busy.status_code == 503
            assert busy.json() == {"status": "error", "code": "busy", "message": api.WORDS["busy"]}
            assert (await post(base, "state", first)).status_code == 403  # still remembered
            assert len(desk.calls) == 2
        finally:
            await server.close()

    run(scenario)


def test_the_desk_s_words_pass_through_and_a_failure_is_said_plainly(caplog):
    async def scenario():
        desk = Desk(
            create=ApiError(400, "invalid", "Enter the price level, a number above 0.", "level"),
            delete=RuntimeError("database is locked"),
        )
        server, base = await served(desk)
        try:
            refused = await post(base, "create", sealed("create"))
            assert refused.status_code == 400
            assert refused.json() == {"status": "error", "code": "invalid", "field": "level",
                                      "message": "Enter the price level, a number above 0."}  # fmt: skip
            failed = await post(base, "delete", sealed("delete", id=1))
            assert failed.status_code == 500
            assert failed.json()["message"] == api.WORDS["failed"]
            assert "database is locked" not in failed.text
        finally:
            await server.close()

    run(scenario)
    assert "database is locked" in caplog.text  # in the journal, with its traceback


def test_switched_off_the_state_says_so_and_every_change_is_refused_in_words():
    async def scenario():
        server, base = await served(OffDesk())
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                health = (await client.get(f"{base}/health", timeout=5)).json()
            assert health["market_alerts"] == "off"
            state = (await post(base, "state", sealed("state"))).json()["data"]
            assert state["enabled"] is False and state["alerts"] == []
            assert state["engine"] == {"code": "off", "line": "Switched off on the server",
                                       "real_time": False}  # fmt: skip
            refused = await post(base, "create", sealed("create"))
            assert refused.status_code == 409
            assert refused.json() == {
                "status": "error",
                "code": "off",
                "message": "Market alerts are switched off on the server.",
            }
            assert (await post(base, "state", b"junk")).status_code == 403  # sealed even so
        finally:
            await server.close()

    run(scenario)


def test_a_taken_port_is_an_error_never_another_port():
    async def scenario():
        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        try:
            server = MarketApi(Desk(), FERNET)
            with pytest.raises(OSError):
                await server.start(holder.getsockname()[1])
            assert server.server is None
        finally:
            holder.close()

    run(scenario)


def test_the_answer_is_json_in_the_owner_s_words_never_a_trace():
    async def scenario():
        server, base = await served(Desk(state={"line": "Watching · Real-time"}))
        try:
            answer = await post(base, "state", sealed("state"))
            assert answer.headers["content-type"].startswith("application/json")
            assert answer.headers["cache-control"] == "no-store"
            assert json.loads(answer.content.decode("utf-8"))["data"]["line"] == (
                "Watching · Real-time"
            )
        finally:
            await server.close()

    run(scenario)
