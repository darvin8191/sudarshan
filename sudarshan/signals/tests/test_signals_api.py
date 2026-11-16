"""T6: the sealed local API (api.py, ops.py) through real sealed requests on 127.0.0.1."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from signals_fakes import (
    AT,
    KEYS,
    Box,
    ask,
    document,
    free_port,
    reading,
    sealed,
    settings_for,
    until,
)

from sudarshan.autologin.handoff import NonceBook, seal
from sudarshan.signals import contract as c
from sudarshan.signals.service import Service


@pytest.fixture
def served(tmp_path):
    """A running service on a free port, clocked at AT plus a minute, with its letterbox."""
    port = free_port()
    box = Box(tmp_path / "in")
    svc = Service(settings_for(tmp_path, port), KEYS, hooks={}, jobs={}, letterbox=box.reader,
                  scan_s=0.02, setup_every_s=0.05)  # fmt: skip
    svc.clock = lambda: AT + 60_000
    loop = asyncio.new_event_loop()
    task = loop.create_task(svc.run())
    loop.run_until_complete(until(lambda: svc.state == "running"))
    yield svc, box, port, loop
    svc.stop()
    loop.run_until_complete(asyncio.wait_for(task, 10))
    loop.close()


def call(loop, coro):
    return loop.run_until_complete(coro)


async def post(port: int, path: str, body: bytes = b"", **headers) -> httpx.Response:
    async with httpx.AsyncClient(trust_env=False) as client:
        return await client.post(f"http://127.0.0.1:{port}{path}", content=body, headers=headers,
                                 timeout=10)  # fmt: skip


def test_the_transport_refuses_what_is_not_a_sealed_op(served):
    svc, _box, port, loop = served
    assert call(loop, post(port, "/v1/nothing", sealed("state"))).status_code == 404
    assert call(loop, post(port, "/health")).status_code == 405
    assert (
        call(loop, post(port, "/v1/state", sealed("state"), origin="http://x")).status_code == 403
    )
    big = call(loop, post(port, "/v1/state_at", b"x" * (c.SEAL_LIMITS["journal"] + 1)))
    assert big.status_code == 413
    forged = call(loop, post(port, "/v1/state", b"not a seal"))
    assert forged.status_code == 403 and forged.json()["message"] == c.WORDS["sealed"]
    blob = sealed("state")
    assert call(loop, post(port, "/v1/state", blob)).status_code == 200
    assert call(loop, post(port, "/v1/state", blob)).status_code == 403  # a nonce once
    assert call(loop, post(port, "/v1/timeline", sealed("state"))).status_code == 403  # wrong op
    stranger = seal(KEYS["api"], "state", principal="sudarshan-family", args={})
    assert call(loop, post(port, "/v1/state", stranger)).json()["code"] == "principal"


def test_each_purpose_opens_only_its_own_ops(served):
    svc, _box, port, loop = served
    assert call(loop, ask(port, "state_at", "journal", ts=AT)).status_code == 200
    assert call(loop, ask(port, "state", "journal")).status_code == 200
    assert call(loop, ask(port, "timeline", "journal")).status_code == 403
    assert call(loop, ask(port, "settings_set", "journal", version=1)).status_code == 403
    assert call(loop, ask(port, "state_at", "api", ts=AT)).status_code == 403


def test_a_full_nonce_book_answers_busy(served):
    svc, _box, port, loop = served
    svc.api.nonces = NonceBook(1)
    assert call(loop, ask(port, "state")).status_code == 200
    busy = call(loop, ask(port, "state"))
    assert busy.status_code == 503 and busy.json()["message"] == c.WORDS["busy"]


def test_state_before_and_after_a_document(served):
    svc, box, port, loop = served
    data = call(loop, ask(port, "state")).json()["data"]
    assert set(data) == {f.name for f in c.record("State")}
    assert data["freshness"]["status"] == "never" and data["readings"] == []
    alert = {"id": "a-9", "module": "regime", "instrument": None, "level": "info",
             "title": "For the app", "text": "", "lang": None}  # fmt: skip
    box.drop(document(signals=[reading(), reading("gap_bias", "BANKNIFTY", bias="bull"),
                               reading("breadth")], alerts=[alert]), written_ms=AT)  # fmt: skip
    call(loop, until(lambda: svc.last is not None))
    data = call(loop, ask(port, "state")).json()["data"]
    assert data["now_ms"] == AT + 60_000 and data["lang"] == "hi"
    fresh = data["freshness"]
    assert fresh["status"] == "fresh" and fresh["words"] == "TRINETRA · updated 09:15:12"
    assert set(fresh) == {f.name for f in c.record("Freshness")}
    keys = [(r["module"], r["instrument"]) for r in data["readings"]]
    assert keys == [("regime", "NIFTY"), ("gap_bias", "BANKNIFTY"), ("breadth", "NIFTY")]
    first = data["readings"][0]
    assert set(first) == {f.name for f in c.record("Reading")}
    assert first["lapsed"] is False and first["published_ms"] == AT and first["doc_seq"] == 1
    assert first["sessions"] == 1  # since Sun 15 Nov: Monday 16 Nov is its one trading session
    assert [a["id"] for a in data["alerts"]] == ["a-9"]
    assert set(data["alerts"][0]) == {f.name for f in c.record("AlertRow")}
    assert data["notes"] == []


def test_notes_say_refused_files_gaps_and_a_newer_format(served):
    svc, box, port, loop = served
    box.drop(document(seq=1), written_ms=AT)
    box.drop(document(seq=4, at=AT + 10), written_ms=AT + 10)
    box.drop(document(seq=5, at=AT + 20, signals=[reading(label="L" * 41)]), written_ms=AT + 20)
    box.drop(document(seq=6, at=AT + 30, v=2), written_ms=AT + 30)
    call(loop, until(lambda: svc.today.get("refused") == 1 and svc.newer_format_ms is not None))
    notes = call(loop, ask(port, "state")).json()["data"]["notes"]
    assert [(n["key"], n["count"]) for n in notes] == [
        ("refused", 1), ("newer_format", None), ("gaps", 2)]  # fmt: skip
    assert notes[0]["words"] == (
        "1 file from TRINETRA could not be read today. Its last good reading is shown."
    )


def test_the_timeline_and_one_change(served):
    svc, box, port, loop = served
    for n, state in enumerate(["UP", "DOWN", "UP", "DOWN"], start=1):
        bias = "bull" if state == "UP" else "bear"
        box.drop(document(seq=n, at=AT + n * 1000,
                          signals=[reading(state=state, label=state.title(), bias=bias)]),
                 written_ms=AT + n * 1000)  # fmt: skip
    call(loop, until(lambda: svc.last is not None and svc.last["seq"] == 4))
    page = call(loop, ask(port, "timeline", limit=3)).json()["data"]
    assert [ch["kind"] for ch in page["changes"]] == ["state", "state", "state"]
    assert page["next_before"] == page["changes"][-1]["id"]
    older = call(loop, ask(port, "timeline", before=page["next_before"])).json()["data"]
    assert [ch["kind"] for ch in older["changes"]] == ["first"] and older["next_before"] is None
    one = call(loop, ask(port, "change_get", id=older["changes"][0]["id"])).json()["data"]
    assert one == older["changes"][0]
    missing = call(loop, ask(port, "change_get", id=999))
    assert missing.status_code == 404 and missing.json()["message"] == c.WORDS["not_found"]
    for args, field, word in (({"module": "Bad Name"}, "module", "bad_module"),
                              ({"instrument": "FINNIFTY"}, "instrument", "bad_instrument"),
                              ({"limit": 201}, "limit", "bad_request"),
                              ({"before": "x"}, "before", "bad_request")):  # fmt: skip
        got = call(loop, ask(port, "timeline", **args))
        assert got.status_code == 400 and got.json()["field"] == field
        assert got.json()["message"] == c.WORDS[word]


def test_settings_change_and_conflict(served):
    svc, _box, port, loop = served
    got = call(loop, ask(port, "settings_get")).json()["data"]
    assert got == {"lang": "hi", "relay": True, "night": True, "version": 1}
    new = call(loop, ask(port, "settings_set", version=1, lang="gu", relay=False)).json()["data"]
    assert new == {"lang": "gu", "relay": False, "night": True, "version": 2}
    assert svc.prefs == new and call(loop, ask(port, "state")).json()["data"]["lang"] == "gu"
    stale = call(loop, ask(port, "settings_set", version=1, lang="en"))
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"
    bad = call(loop, ask(port, "settings_set", version=2, lang="ta"))
    assert bad.status_code == 400 and bad.json()["field"] == "lang"
    assert bad.json()["message"] == c.WORDS["bad_lang"]
    assert call(loop, ask(port, "settings_set", version=2, relay="yes")).status_code == 400
    assert call(loop, ask(port, "settings_set")).json()["field"] == "version"


def test_a_change_made_with_the_relay_off_is_not_handed_to_the_desk(served):
    svc, box, port, loop = served
    call(loop, ask(port, "settings_set", version=1, relay=False))
    box.drop(document(), written_ms=AT)
    call(loop, until(lambda: svc.last is not None))
    assert call(loop, svc.store.call(svc.store.pending_relay)) == ([], [])


def test_state_at_for_the_journal(served):
    svc, box, port, loop = served
    box.drop(document(), written_ms=AT)
    call(loop, until(lambda: svc.last is not None))
    got = call(loop, ask(port, "state_at", "journal", ts=AT + 1000)).json()["data"]
    assert got["fresh"] and [r["state"] for r in got["readings"]] == ["DISTRIBUTION"]
    before = call(loop, ask(port, "state_at", "journal", ts=AT - 1)).json()["data"]
    assert before == {"ts_ms": AT - 1, "fresh": False, "readings": []}
    assert call(loop, ask(port, "state_at", "journal", ts="x")).json()["field"] == "ts"


def test_performance_is_not_ready_until_its_op_exists(served):
    svc, _box, port, loop = served
    if "performance" in __import__("sudarshan.signals.ops", fromlist=["REGISTRY"]).REGISTRY:
        pytest.skip("the performance op is built")
    got = call(loop, ask(port, "performance"))
    assert got.status_code == 501 and got.json()["message"] == c.WORDS["not_ready"]


def test_every_op_answers_off_while_switched_off(tmp_path):
    port = free_port()
    svc = Service(settings_for(tmp_path, port, enabled=False), KEYS, hooks={}, jobs={})

    async def scenario():
        task = asyncio.create_task(svc.run())
        await until(lambda: svc.state == "off")
        for op in c.PURPOSE_OPS["api"]:
            data = (await ask(port, op, version=1, id=1)).json()["data"]
            assert data == {"enabled": False, "message": c.WORDS["off"]}, op
        svc.stop()
        await asyncio.wait_for(task, 10)

    asyncio.run(scenario())
