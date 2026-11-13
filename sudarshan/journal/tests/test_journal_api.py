"""The journal's sealed local API (JB2): the four purposes, their principals, op sets and size
limits, seals, replays, expiry, Origin, the switch and the transport's words (design 7.2, 8.2)."""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
from journal_fakes import KEYS, sealed

from sudarshan.autologin.handoff import NonceBook
from sudarshan.journal import api as api_module
from sudarshan.journal.api import JournalApi
from sudarshan.journal.core import kinds
from sudarshan.journal.errors import ApiError
from sudarshan.journal.ops import common


class Desk:
    def __init__(self, enabled: bool = True, off_reason: str | None = None):
        self.enabled, self.off_reason = enabled, off_reason

    def health(self) -> dict:
        return {"service": "sudarshan-journal", "journal_state": {"enabled": self.enabled}}


def registry_with(calls: list, **answers) -> dict:
    """A handler for every op of the contract: it records the call and the caller."""
    book: dict = {}
    for name in kinds.OP_NAMES:

        async def handler(ctx, args, _name=name):
            calls.append((_name, dict(args), common.caller()))
            answer = answers.get(_name, {"op": _name})
            if isinstance(answer, Exception):
                raise answer
            return answer

        book[name] = handler
    return book


async def served(desk=None, registry=None, keys=None, **options):
    server = JournalApi(desk or Desk(), KEYS if keys is None else keys,
                        registry={} if registry is None else registry, **options)  # fmt: skip
    await server.start(0)
    return server, f"http://127.0.0.1:{server.port}"


async def post(base: str, op: str, body: bytes, **headers) -> httpx.Response:
    async with httpx.AsyncClient(trust_env=False) as client:
        return await client.post(f"{base}/v1/{op}", content=body, headers=headers, timeout=10)


def run(scenario) -> None:
    asyncio.run(scenario())


def with_api(scenario, desk=None, registry=None, **options):
    """Run ``scenario(base, calls)`` against a served API, closed on every path."""
    calls: list = []

    async def go():
        server, base = await served(
            desk, registry if registry is not None else registry_with(calls), **options
        )
        try:
            await scenario(base, calls)
        finally:
            await server.close()

    run(go)


# ---- the seal ---------------------------------------------------------------------------------
def test_a_sealed_request_reaches_its_op_and_the_answer_is_the_envelope():
    async def scenario(base, calls):
        answer = await post(base, "state", sealed("state", x=1))
        assert answer.status_code == 200
        assert answer.json() == {"status": "success", "data": {"op": "state"}}
        assert answer.headers["cache-control"] == "no-store"
        (name, args, who) = calls[0]
        assert (name, args["x"], who.purpose, who.principal) == ("state", 1, "api", "owner")

    with_api(scenario)


def test_the_op_in_the_path_must_be_the_op_inside_the_seal():
    async def scenario(base, calls):
        answer = await post(base, "trips", sealed("state"))
        assert answer.status_code == 403 and answer.json()["code"] == "forbidden"
        assert calls == []

    with_api(scenario)


def test_a_seal_is_accepted_once():
    async def scenario(base, calls):
        body = sealed("state")
        assert (await post(base, "state", body)).status_code == 200
        assert (await post(base, "state", body)).status_code == 403
        assert len(calls) == 1

    with_api(scenario)


def test_a_seal_lives_sixty_seconds():
    now = 1_800_000_000.0

    async def scenario(base, calls):
        fresh = await post(base, "state", sealed("state", now=now - 59))
        stale = await post(base, "state", sealed("state", now=now - 61))
        assert fresh.status_code == 200 and stale.status_code == 403

    with_api(scenario, clock=lambda: now)


def test_a_seal_from_another_purpose_of_the_ring_never_opens():
    from sudarshan.autologin.handoff import seal
    from sudarshan.vault import PORTFOLIO_API_INFO, fernet_for

    async def scenario(base, calls):
        foreign = seal(fernet_for("p" * 48, PORTFOLIO_API_INFO), "state", principal="owner")
        answer = await post(base, "state", foreign)
        assert answer.status_code == 403 and calls == []
        assert answer.json()["message"] == api_module.WORDS["forbidden"]

    with_api(scenario)


# ---- the purposes -----------------------------------------------------------------------------
def test_the_delivery_op_is_refused_under_the_bridges_key_and_logged(caplog):
    """Mutation target 27: ``delivery`` sealed with the API purpose is a 403, said once."""

    async def scenario(base, calls):
        with caplog.at_level(logging.WARNING, "sudarshan.journal"):
            for _ in range(3):
                answer = await post(
                    base, "delivery", sealed("delivery", principal="owner", report_id=1)
                )
                assert answer.status_code == 403 and answer.json()["code"] == "forbidden"
        assert calls == []
        lines = [r for r in caplog.records if "refused the op delivery" in r.getMessage()]
        assert len(lines) == 1 and "api purpose" in lines[0].getMessage()

    with_api(scenario)


def test_the_delivery_purpose_opens_exactly_its_three_ops():
    """Y8: the alerts' report desk reads a report and its file and says how it went; nothing else."""

    async def scenario(base, calls):
        for name in sorted(set(kinds.OP_NAMES)):
            answer = await post(base, name, sealed(name, "delivery", id=1))
            if name in ("report_get", "report_file", "delivery"):
                assert answer.status_code == 200, name
            else:
                assert answer.status_code in (403, 413), name
        assert sorted(c[0] for c in calls) == ["delivery", "report_file", "report_get"]
        assert {c[2].purpose for c in calls} == {"delivery"}

    with_api(scenario)


def test_the_family_purpose_opens_only_the_counts_and_only_for_the_family_service():
    async def scenario(base, calls):
        assert (
            await post(base, "family_day", sealed("family_day", "family", day=1))
        ).status_code == 200
        assert (await post(base, "trips", sealed("trips", "family"))).status_code == 403
        wrong = await post(base, "family_day", sealed("family_day", "family", principal="viewer:7"))
        assert wrong.status_code == 403 and wrong.json()["code"] == "principal"
        assert [c[0] for c in calls] == ["family_day"]

    with_api(scenario)


def test_the_nudge_carries_its_fields_in_the_seal_itself():
    """Y17: the alerts' sender writes kind, sandbox and at beside the op, not under args."""

    async def scenario(base, calls):
        body = sealed("nudge", "nudge", kind="orders", sandbox=False, at=123)
        assert (await post(base, "nudge", body)).status_code == 200
        assert calls[0][1] == {"kind": "orders", "sandbox": False, "at": 123}
        assert calls[0][2].purpose == "nudge"
        refused = await post(base, "state", sealed("state", "nudge", kind="orders"))
        assert refused.status_code == 403

    with_api(scenario)


def test_the_bridge_key_never_opens_a_nudge_and_the_principal_must_be_the_alerts():
    async def scenario(base, calls):
        under_api = await post(base, "nudge", sealed("nudge", principal="owner", kind="orders"))
        assert under_api.status_code == 403
        stranger = await post(
            base, "nudge", sealed("nudge", "nudge", principal="someone", kind="orders")
        )
        assert stranger.status_code == 403 and stranger.json()["code"] == "principal"
        assert calls == []

    with_api(scenario)


def test_the_owners_principal_is_the_only_one_the_bridge_key_serves():
    async def scenario(base, calls):
        answer = await post(base, "state", sealed("state", principal="sudarshan-alerts"))
        assert answer.status_code == 403
        assert answer.json()["message"] == api_module.WORDS["principal"]

    with_api(scenario)


def test_a_purpose_without_a_key_is_not_served():
    async def scenario(base, calls):
        answer = await post(base, "report_get", sealed("report_get", "delivery", id=1))
        assert answer.status_code == 403 and calls == []

    with_api(scenario, keys={"api": KEYS["api"]})


# ---- sizes and the transport ------------------------------------------------------------------
def test_each_purpose_has_its_size_limit():
    assert api_module.LIMITS["state"] == 64 * 1024
    assert api_module.LIMITS["report_get"] == 64 * 1024  # opened by the bridge's purpose too
    assert api_module.LIMITS["delivery"] == 16 * 1024
    assert api_module.LIMITS["nudge"] == 4 * 1024
    assert api_module.LIMITS["family_day"] == 4 * 1024


def test_a_body_over_the_ops_limit_is_refused_before_it_is_read():
    async def scenario(base, calls):
        async with httpx.AsyncClient(trust_env=False) as client:
            answer = await client.post(
                f"{base}/v1/nudge", content=b"x" * (4 * 1024 + 1), timeout=10
            )
        assert answer.status_code == 413 and answer.json()["code"] == "size"
        big = sealed("delivery", "delivery", report_id=1, reason="r" * 14_000)
        assert 16 * 1024 < len(big) <= 64 * 1024
        refused = await post(base, "delivery", big)
        assert refused.status_code == 413
        assert calls == []

    with_api(scenario)


def test_the_delivery_purpose_takes_no_more_than_its_sixteen_kib_on_a_shared_op():
    """report_get is opened by the bridge (64 KiB) and the desk (16 KiB): a desk seal past 16 KiB
    is refused even though the path's limit lets it be read."""

    async def scenario(base, calls):
        big = sealed("report_get", "delivery", id=1, pad="p" * 14_000)
        assert 16 * 1024 < len(big) < 64 * 1024
        assert (await post(base, "report_get", big)).status_code == 403
        small = sealed("report_get", "api", id=1, pad="p" * 14_000)
        assert (await post(base, "report_get", small)).status_code == 200
        assert [c[2].purpose for c in calls] == ["api"]

    with_api(scenario)


def test_a_browser_page_is_refused():
    async def scenario(base, calls):
        answer = await post(base, "state", sealed("state"), Origin="https://example.com")
        assert answer.status_code == 403 and calls == []

    with_api(scenario)


def test_a_post_without_a_length_and_the_wrong_methods_are_refused():
    async def scenario(base, calls):
        reader, writer = await asyncio.open_connection("127.0.0.1", int(base.rsplit(":", 1)[1]))
        writer.write(b"POST /v1/state HTTP/1.1\r\nHost: x\r\n\r\n")
        await writer.drain()
        head = await reader.read(64)
        writer.close()
        assert head.startswith(b"HTTP/1.1 411")
        async with httpx.AsyncClient(trust_env=False) as client:
            assert (await client.get(f"{base}/v1/state", timeout=10)).status_code == 405
            assert (await client.post(f"{base}/health", content=b"", timeout=10)).status_code == 405
            assert (
                await client.post(f"{base}/v1/nothing", content=b"x", timeout=10)
            ).status_code == 404
        assert calls == []

    with_api(scenario)


def test_a_slow_client_is_cut_off_with_its_words(monkeypatch):
    monkeypatch.setattr(api_module, "READ_TIMEOUT_S", 0.2)

    async def scenario(base, calls):
        reader, writer = await asyncio.open_connection("127.0.0.1", int(base.rsplit(":", 1)[1]))
        writer.write(b"POST /v1/state HTTP/1.1\r\nContent-Length: 10\r\n\r\nabc")
        await writer.drain()
        answer = await asyncio.wait_for(reader.read(), 5)
        writer.close()
        assert answer.startswith(b"HTTP/1.1 408") and api_module.WORDS["failed"].encode() in answer

    with_api(scenario)


def test_a_full_nonce_book_answers_busy():
    async def scenario(base, calls):
        assert (await post(base, "state", sealed("state"))).status_code == 200
        answer = await post(base, "state", sealed("state"))
        assert answer.status_code == 503 and answer.json()["message"] == api_module.WORDS["busy"]

    with_api(scenario, nonces=NonceBook(1))


# ---- the switch and the ops -------------------------------------------------------------------
def test_switched_off_every_op_answers_off_and_does_nothing():
    async def scenario(base, calls):
        for name, purpose in (("state", "api"), ("trips", "api"), ("nudge", "nudge"),
                              ("report_get", "delivery"), ("family_day", "family")):  # fmt: skip
            answer = await post(base, name, sealed(name, purpose))
            assert answer.status_code == 200, name
            assert answer.json() == {
                "status": "success",
                "data": {"enabled": False, "message": "The journal is switched off on the server."},
            }
        assert calls == []

    with_api(scenario, desk=Desk(enabled=False))


def test_kept_off_by_its_store_the_reason_is_the_answer():
    reason = "Your trade journal was written by a newer release. Install that release again; nothing was changed."

    async def scenario(base, calls):
        answer = await post(base, "state", sealed("state"))
        assert answer.json()["data"] == {"enabled": False, "message": reason}

    with_api(scenario, desk=Desk(enabled=False, off_reason=reason))


def test_an_op_nobody_built_yet_says_so_in_words():
    async def scenario(base, calls):
        answer = await post(base, "analytics", sealed("analytics"))
        assert answer.status_code == 501
        assert answer.json() == {
            "status": "error", "code": "not_ready",
            "message": "This part of the journal is not ready yet.",
        }  # fmt: skip

    with_api(scenario, registry={})


def test_an_ops_refusal_and_its_failure_come_back_in_words():
    calls: list = []
    registry = registry_with(
        calls,
        trip_get=ApiError(404, "not_found", "This trade is not in the journal any more.", "id"),
        trips=RuntimeError("a bug, with a class name"),
    )

    async def scenario(base, _calls):
        gone = await post(base, "trip_get", sealed("trip_get", id=9))
        assert gone.status_code == 404 and gone.json()["field"] == "id"
        broken = await post(base, "trips", sealed("trips"))
        assert broken.status_code == 500
        assert broken.json()["message"] == api_module.WORDS["failed"]
        assert "RuntimeError" not in broken.text

    with_api(scenario, registry=registry)


def test_args_that_are_not_an_object_are_refused():
    from sudarshan.autologin.handoff import seal

    async def scenario(base, calls):
        body = seal(KEYS["api"], "state", principal="owner", args=[1, 2])
        answer = await post(base, "state", body)
        assert answer.status_code == 400 and calls == []

    with_api(scenario)


def test_health_is_unsealed_and_shows_the_desks_document():
    async def scenario(base, calls):
        async with httpx.AsyncClient(trust_env=False) as client:
            answer = await client.get(f"{base}/health", timeout=10)
        assert answer.status_code == 200
        assert answer.json()["journal_state"] == {"enabled": True}

    with_api(scenario)


def test_the_op_names_of_every_purpose_are_in_the_contract():
    assert set().union(*api_module.ALLOWED.values()) == set(kinds.OP_NAMES)
    assert api_module.ALLOWED["delivery"] == {"report_get", "report_file", "delivery"}
    assert "delivery" not in api_module.ALLOWED["api"]
    assert "family_day" not in api_module.ALLOWED["api"]
    assert "nudge" not in api_module.ALLOWED["api"]
    assert kinds.PRINCIPALS.keys() == api_module.ALLOWED.keys() == kinds.SEAL_LIMITS.keys()


@pytest.mark.parametrize("purpose", ["api", "delivery", "nudge", "family"])
def test_every_purpose_has_a_key_of_its_own(purpose):
    others = [k for name, k in KEYS.items() if name != purpose]
    body = sealed("state", purpose)
    for key in others:
        with pytest.raises(Exception):  # noqa: B017 - any refusal: the key is not theirs
            key.decrypt(body)
