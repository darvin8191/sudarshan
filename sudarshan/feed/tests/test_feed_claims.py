"""Subscription claims per client (M18.1): the effective set, caps, persistence, the socket."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import feed_fakes as ff
import httpx
import pytest
import zmq
import zmq.asyncio

from sudarshan.feed.bus import HEALTH_TOPIC
from sudarshan.feed.config import Settings
from sudarshan.feed.service import FeedService
from sudarshan.feed.subscriptions import (
    CLAIM_TTL_S,
    CapError,
    Subscriptions,
    validate_claims,
)

VECTORS = json.loads(Path(__file__).with_name("claims_vectors.json").read_text(encoding="utf-8"))
REL, INFY, NIFTY = "NSE_EQ|INE002A01018", "NSE_EQ|INE009A01021", "NSE_INDEX|Nifty 50"
A, S = "sudarshan-alerts", "sudarshan-screener"
NOW = 1_792_557_000.0


def subs_with(tmp_path, base: dict, claims: dict | None = None) -> Subscriptions:
    subs = Subscriptions(dict(base), tmp_path / "state" / "subscriptions.json")
    for client, keys in (claims or {}).items():
        subs.claims[client] = {"renewed_at": int(NOW), "keys": dict(keys)}
    return subs


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["id"] for c in VECTORS["cases"]])
def test_claim_vector(case, tmp_path):
    subs = subs_with(tmp_path, case["base"], case.get("claims"))
    do = case["do"]
    if "put" in do:
        change = subs.put_claims(do["put"], do["keys"], NOW)
    elif "drop" in do:
        change = subs.drop_claims(do["drop"])
    elif "unsubscribe" in do:
        _, change = subs.unsubscribe(do["unsubscribe"])
    else:
        change = subs.subscribe(do["subscribe"], do["keys"])
    assert [[method, mode, keys] for method, keys, mode in change.batches()] == case["send"]
    assert subs.desired() == case["effective"]


def test_every_vector_has_a_unique_id():
    ids = [case["id"] for case in VECTORS["cases"]]
    assert len(ids) == len(set(ids))


def test_a_claim_that_breaks_a_cap_changes_nothing(tmp_path):
    subs = subs_with(tmp_path, {NIFTY: "ltpc"}, {A: {REL: "full"}})
    with pytest.raises(CapError, match="51 keys in full_d30"):
        subs.put_claims(S, {f"NSE_FO|{n}": "full_d30" for n in range(51)}, NOW)
    full = {f"NSE_FO|{n}": "full" for n in range(1500)}  # with ltpc in use: combined 1,500
    with pytest.raises(CapError, match="combined cap of 1500"):
        subs.put_claims(A, full, NOW)
    assert subs.desired() == {NIFTY: "ltpc", REL: "full"} and set(subs.claims) == {A}
    assert not subs.state_file.exists()  # nothing was saved either


def test_claims_persist_as_version_2_and_a_version_1_file_loads(tmp_path):
    keys, state = tmp_path / "keys.txt", tmp_path / "state" / "subscriptions.json"
    keys.write_text(f"ltpc {NIFTY}\n")
    state.parent.mkdir()
    state.write_text(json.dumps({"version": 1, "added": {INFY: "ltpc"}, "removed": []}))
    subs = Subscriptions.load(keys, state, NOW)
    assert subs.desired() == {NIFTY: "ltpc", INFY: "ltpc"} and subs.claims == {}
    subs.put_claims(A, {REL: "full"}, NOW)
    saved = json.loads(state.read_text())
    assert saved["version"] == 2
    assert saved["claims"] == {A: {"renewed_at": int(NOW), "keys": {REL: "full"}}}
    again = Subscriptions.load(keys, state, NOW + 60)
    assert again.desired() == {NIFTY: "ltpc", INFY: "ltpc", REL: "full"}
    assert again.claim_counts() == {A: 1}


def test_claims_not_renewed_for_7_days_are_dropped(tmp_path, caplog):
    keys, state = tmp_path / "keys.txt", tmp_path / "state" / "subscriptions.json"
    keys.write_text(f"ltpc {NIFTY}\n")
    subs = Subscriptions.load(keys, state, NOW)
    subs.put_claims(A, {REL: "full"}, NOW)
    subs.put_claims(S, {INFY: "ltpc"}, NOW + CLAIM_TTL_S)  # renewed later
    assert Subscriptions.load(keys, state, NOW + CLAIM_TTL_S).claim_counts() == {A: 1, S: 1}
    with caplog.at_level(logging.WARNING, logger="sudarshan.feed"):
        later = Subscriptions.load(keys, state, NOW + CLAIM_TTL_S + 1)
    assert later.claim_counts() == {S: 1} and "sudarshan-alerts" in caplog.text
    assert REL not in Subscriptions.load(keys, state, NOW + CLAIM_TTL_S + 2).desired()
    change = later.expire(NOW + 2 * CLAIM_TTL_S + 1)
    assert change.gone == (INFY,) and later.claims == {}


def test_claims_that_would_break_a_cap_at_start_are_dropped_and_the_feed_starts(tmp_path, caplog):
    keys, state = tmp_path / "keys.txt", tmp_path / "state" / "subscriptions.json"
    keys.write_text("".join(f"full NSE_EQ|K{n}\n" for n in range(1400)))
    state.parent.mkdir()
    claims = {A: {"renewed_at": int(NOW), "keys": {f"NSE_FO|{n}": "ltpc" for n in range(200)}}}
    state.write_text(json.dumps({"version": 2, "added": {}, "removed": [], "claims": claims}))
    with caplog.at_level(logging.ERROR, logger="sudarshan.feed"):
        subs = Subscriptions.load(keys, state, NOW)
    assert subs.claims == {} and len(subs.desired()) == 1400
    assert "dropping every client's claims at start" in caplog.text
    assert json.loads(state.read_text())["claims"] == {}


def test_a_damaged_claim_is_dropped_not_the_file(tmp_path, caplog):
    keys, state = tmp_path / "keys.txt", tmp_path / "state" / "subscriptions.json"
    keys.write_text(f"ltpc {NIFTY}\n")
    state.parent.mkdir()
    claims = {
        A: {"renewed_at": int(NOW), "keys": {REL: "full"}},
        "Bad Client": {"renewed_at": int(NOW), "keys": {INFY: "ltpc"}},
        S: {"renewed_at": "yesterday", "keys": {INFY: "ltpc"}},
        "sudarshan-x": {"renewed_at": int(NOW), "keys": {INFY: "depth"}},
    }
    state.write_text(json.dumps({"version": 2, "added": {}, "removed": [], "claims": claims}))
    with caplog.at_level(logging.ERROR, logger="sudarshan.feed"):
        subs = Subscriptions.load(keys, state, NOW)
    assert subs.claim_counts() == {A: 1} and caplog.text.count("ignoring the stored claims") == 3


@pytest.mark.parametrize(
    ("client", "keys", "words"),
    [
        ("Sudarshan", {REL: "full"}, "client id"),
        ("sudarshan alerts", {REL: "full"}, "client id"),
        ("a" * 41, {REL: "full"}, "client id"),
        (7, {REL: "full"}, "client id"),
        (A, [REL], "JSON object"),
        (A, {"not a key": "full"}, "instrument key"),
        (A, {REL: "depth"}, "mode must be one of"),
    ],
)
def test_bad_claims_are_refused_in_words(client, keys, words):
    with pytest.raises(ValueError, match=words):
        validate_claims(client, keys)


# ---- the service, against a fake Upstox ----------------------------------------------------


def test_claims_reach_the_socket_and_a_reconnect_resubscribes_them(feed_env, fast):
    async def scenario():
        async with ff.FakeUpstox() as fake:
            cfg = Settings.from_env(feed_env(fake))
            svc = FeedService(cfg)
            run = asyncio.create_task(svc.run())
            await ff.wait_until(lambda: svc.http is not None)
            base = f"http://127.0.0.1:{svc.http_port}"
            async with httpx.AsyncClient(base_url=base) as http:
                await ff.wait_until(lambda: svc.health()["subscriptions_live"])
                sent = len(fake.requests[0])
                body = {"keys": {INFY: "full", NIFTY: "full", REL: "ltpc"}}
                ok = (await http.put(f"/claims/{A}", json=body)).json()
                assert (ok["claimed"], ok["applied_live"], ok["subscribed"]["full"]) == (3, True, 3)
                await ff.wait_until(lambda: len(fake.requests[0]) == sent + 2)
                got = [(r["method"], r["data"].get("mode"), r["data"]["instrumentKeys"])
                       for r in fake.requests[0][sent:]]  # fmt: skip
                assert got == [("sub", "full", [INFY]), ("change_mode", "full", [NIFTY])]
                over = await http.put(f"/claims/{S}", json={"keys": {
                    f"NSE_FO|{n}": "full_d30" for n in range(51)}})  # fmt: skip
                assert over.status_code == 409 and "51 keys in full_d30" in over.json()["error"]
                bad = await http.put("/claims/Not-Valid", json=body)
                assert bad.status_code == 400 and "client id" in bad.json()["error"]
                listed = (await http.get("/claims")).json()["clients"]
                assert listed[A]["keys"] == 3 and listed[A]["by_mode"] == {"full": 2, "ltpc": 1}
                health = (await http.get("/health")).json()
                assert health["claims"] == {A: 3} and health["started_at"] == svc.started_at
                await fake.sockets[0].close()  # Upstox drops the socket: all of it is sent again
                await ff.wait_until(lambda: len(fake.requests) == 2 and svc.health()[
                    "subscriptions_live"])  # fmt: skip
                again = {k for r in fake.requests[1] for k in r["data"]["instrumentKeys"]}
                assert {INFY, NIFTY, REL} <= again
                sent = len(fake.requests[1])
                gone = (await http.delete(f"/claims/{A}")).json()
                assert gone["applied_live"] and gone["subscribed"]["full"] == 1  # RELIANCE base
                await ff.wait_until(lambda: len(fake.requests[1]) == sent + 2)
                got = [(r["method"], r["data"].get("mode"), r["data"]["instrumentKeys"])
                       for r in fake.requests[1][sent:]]  # fmt: skip
                assert got == [("change_mode", "ltpc", [NIFTY]), ("unsub", None, [INFY])]
            svc.stop("test")
            await asyncio.wait_for(run, 10)

    asyncio.run(scenario())


def test_a_market_status_change_is_published_at_once(feed_env, fast, monkeypatch):
    """The gate of every consumer reads the exchange's status from __health__; waiting for the
    5 s cycle left the first seconds of a session (14:20 on a special day) reading pre-open."""
    from sudarshan.feed import service as feed_service

    monkeypatch.setattr(feed_service, "HEALTH_EVERY_S", 3600.0)  # only events publish now

    async def scenario():
        async with ff.FakeUpstox() as fake:
            svc = FeedService(Settings.from_env(feed_env(fake)))
            run = asyncio.create_task(svc.run())
            await ff.wait_until(lambda: svc.pub is not None)
            ctx = zmq.asyncio.Context()
            sub = ctx.socket(zmq.SUB)
            sub.connect(svc.pub.endpoint)
            sub.subscribe(HEALTH_TOPIC.encode())
            try:
                await ff.wait_until(lambda: svc.health()["subscriptions_live"])
                await asyncio.sleep(0.2)  # the SUB is connected; drain state-change health
                while await sub.poll(50):
                    await sub.recv_multipart()
                status = {"NSE_EQ": "NORMAL_OPEN", "NSE_FO": "NORMAL_OPEN"}
                await fake.sockets[0].send(ff.response({}, "market_info", status))
                _, payload = await asyncio.wait_for(sub.recv_multipart(), 2)
                assert json.loads(payload)["market_status"] == status
            finally:
                sub.close(0)
                ctx.term()
            svc.stop("test")
            await asyncio.wait_for(run, 10)

    asyncio.run(scenario())
