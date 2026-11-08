"""The API's ops (SV7) end to end: a real store and a real listener, every op of the contract
reached through a sealed request, refusals in the owner's words with the contract's codes."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from sudarshan.autologin.handoff import seal
from sudarshan.screener import contract, spec
from sudarshan.screener import scheduler as sch
from sudarshan.screener import store as store_module
from sudarshan.screener.ops import REGISTRY
from sudarshan.screener.runs import Judged, Member, record
from sudarshan.screener.service import Service, close_store, open_store
from sudarshan.screener.store import OWNER, ScreenerStore
from sudarshan.screener.tests.test_screener_runs import NOW, SESSION, SESSIONS, SPEC, due_for
from sudarshan.screener.tests.test_screener_service import FERNET, free_port, settings_for, until
from sudarshan.screener.types import HIT, MISS, NOT_CHECKED, Outcome, Why

HOLIDAYS = Path(__file__).resolve().parents[3] / "sudarshan/ops/deploy"


class Client:
    def __init__(self, base: str):
        self.base = base

    async def op(self, name: str, **args):
        blob = seal(FERNET, name, principal="owner", args=args)
        async with httpx.AsyncClient(trust_env=False) as client:
            answer = await client.post(f"{self.base}/v1/{name}", content=blob, timeout=10)
        return answer.status_code, answer.json()


async def served(tmp_path):
    port = free_port()
    settings = settings_for(
        port,
        SUDARSHAN_SCREENER_STATE_DIR=str(tmp_path),
        SUDARSHAN_NSE_HOLIDAYS_FILE=str(tmp_path / "none-holidays.txt"),
        SUDARSHAN_NSE_EXTRA_DAYS_FILE=str(tmp_path / "none-extra.txt"),
    )
    svc = Service(settings, FERNET, jobs={})
    svc.clock = lambda: NOW
    task = asyncio.create_task(svc.run())
    await until(lambda: svc.state == "running")
    return svc, task, Client(f"http://127.0.0.1:{port}")


def test_every_contract_op_is_registered():
    assert set(REGISTRY) == set(contract.OP_NAMES)


def test_screens_are_created_edited_paused_and_deleted_with_versions(tmp_path):
    async def scenario():
        svc, task, client = await served(tmp_path)
        try:
            status, body = await client.op("state")
            assert (
                status == 200 and body["data"]["screens"] == [] and body["data"]["enabled"] is True
            )
            assert body["data"]["limits"] == {"active": 12, "active_used": 0, "max_keys": 320}
            engine = body["data"]["engine"]
            assert engine["line"].startswith(
                ("Market closed", "Real-time", "Waiting", "Not checking")
            )
            assert engine["real_time"] is False and body["data"]["last_run_id"] == 0
            assert body["data"]["server_time"] == NOW

            status, body = await client.op("create", spec=SPEC, request_id="r1")
            assert status == 200, body
            made = body["data"]
            assert (
                made["name"] == "Above hundred"
                and made["version"] == 1
                and made["status"] == "active"
            )
            assert made["where"] and made["alerts"] == "Alerts on" and made["list_id"] == 7
            assert made["matches"] == 0 and made["entered_today"] == 0 and made["last_run_id"] == 0
            assert made["status_reason"] is None and made["target_name"] is None
            status, again = await client.op("create", spec=SPEC, request_id="r1")
            assert again["data"]["id"] == made["id"], "a retried create returns the same screen"

            bad = {
                **SPEC,
                "where": {
                    "id": "c1",
                    "left": {"field": "close", "tf": 15},
                    "op": "gt",
                    "right": {"num": "abc"},
                },
            }
            status, body = await client.op("create", spec=bad)
            assert status == 422 and body["error"]["code"] == "refused"
            assert body["error"]["field"] == "c1.right" and body["error"]["message"].endswith(".")

            status, body = await client.op("update", id=made["id"], version=1, action="pause")
            assert (
                status == 200
                and body["data"]["status"] == "paused"
                and body["data"]["version"] == 2
            )
            status, body = await client.op("update", id=made["id"], version=1, action="resume")
            assert status == 409 and body["error"]["code"] == "version_conflict"
            assert "another screen" in body["error"]["message"]
            status, body = await client.op("update", id=made["id"], version=2, action="alerts_off")
            assert (
                status == 200
                and body["data"]["alerts_paused"] is True
                and body["data"]["version"] == 3
            )
            edited = {**SPEC, "name": "Renamed"}
            status, body = await client.op(
                "update", id=made["id"], version=3, action="edit", spec=edited
            )
            assert (
                status == 200 and body["data"]["name"] == "Renamed" and body["data"]["version"] == 4
            )
            status, body = await client.op("update", id=made["id"], version=4, action="explode")
            assert status == 400

            status, body = await client.op("delete", id=made["id"])
            assert status == 200 and body["data"]["deleted"] == made["id"]
            status, body = await client.op("results", id=made["id"])
            assert status == 404 and body["error"]["code"] == "not_found"
            status, body = await client.op("delete", id=made["id"])
            assert status == 404
        finally:
            svc.stop()
            await asyncio.wait_for(task, 5)

    asyncio.run(scenario())


def test_the_intraday_screen_limit_is_refused_in_words_and_after_close_is_free(tmp_path):
    async def scenario():
        svc, task, client = await served(tmp_path)
        try:
            for n in range(12):
                status, body = await client.op("create", spec={**SPEC, "name": f"S{n}"})
                assert status == 200, body
            status, body = await client.op("create", spec={**SPEC, "name": "one more"})
            assert status == 422 and "Up to 12 screens" in body["error"]["message"]
            eod = {**SPEC, "name": "EOD", "run": {"on": "after_close"},
                   "where": {"id": "c1", "left": {"field": "close", "tf": "D"}, "op": "gt", "right": {"num": "100"}},
                   "rank": {"by": {"field": "close", "tf": "D"}, "order": "desc"}}  # fmt: skip
            status, body = await client.op("create", spec=eod)
            assert status == 200, body
            status, body = await client.op(
                "create", spec={**SPEC, "name": "paused one"}, status="paused"
            )
            assert status == 200 and body["data"]["status"] == "paused"
            status, body = await client.op("today")
            assert status == 200 and body["data"]["screens"] == [] and body["data"]["recent"] == []
            status, body = await client.op("state")
            assert body["data"]["limits"]["active_used"] == 12
        finally:
            svc.stop()
            await asyncio.wait_for(task, 5)

    asyncio.run(scenario())


def test_results_runs_run_since_membership_and_pins_read_a_recorded_run(tmp_path):
    async def scenario():
        svc, task, client = await served(tmp_path)
        try:
            status, body = await client.op("create", spec=SPEC)
            screen = body["data"]
            judged = [
                Judged(Member("NSE_EQ|A", "AAA", "NSE", "NSE_EQ"), Outcome(HIT, values={"c1": {"l": "120.00", "r": "100.00", "forming": False}}, rank=12000), (12000, 9000)),
                Judged(Member("NSE_EQ|B", "BBB", "NSE", "NSE_EQ"), Outcome(MISS), (9000, 9000)),
                Judged(Member("NSE_EQ|C", "CCC", "NSE", "NSE_EQ"), Outcome(NOT_CHECKED, (Why("no_feed"),)), (None, None)),
            ]  # fmt: skip
            due = due_for(screen, 3)
            report = svc.store.write(
                OWNER,
                lambda tx: record(
                    tx, tx.screen(screen["id"]), tx.state(screen["id"]), due, judged, NOW
                ),
            )
            assert report.status == "done"

            status, body = await client.op("results", id=screen["id"])
            data = body["data"]
            assert data["screen_id"] == screen["id"]
            assert data["run"]["id"] == report.run_id and data["run"]["matches"] == 1
            assert data["run"]["candle"] == "10:00 to 10:15" and data["run"]["marks"] == []
            assert [r["symbol"] for r in data["rows"]] == ["AAA"]
            row = data["rows"][0]
            assert row["rank"] == 1 and row["values"] == "Close 120.00" and row["ltp"] == 12000
            assert row["change_pct"] == 33.33 and row["member_id"] is not None
            assert row["forming"] is False
            assert row["chip"]["icon"] and row["chip"]["text"], "always an icon and a word"
            assert data["not_checked"] == {
                "count": 1,
                "reasons": ["Not checked: no price from Upstox yet"],
            }

            status, body = await client.op("state")
            card = body["data"]["screens"][0]
            assert card["matches"] == 1 and card["entered_today"] == 1
            assert card["last_run_id"] == report.run_id
            assert body["data"]["last_run_id"] == report.run_id

            status, body = await client.op("runs", id=screen["id"], limit=10)
            assert [r["id"] for r in body["data"]["runs"]] == [report.run_id]
            assert body["data"]["more"] is False
            status, body = await client.op("run", id=report.run_id)
            assert (
                body["data"]["values_note"] is None and body["data"]["run"]["id"] == report.run_id
            )
            assert body["data"]["rows"][0]["symbol"] == "AAA"
            status, body = await client.op("run", id=999)
            assert status == 404 and "history" in body["error"]["message"]
            status, body = await client.op("runs_since", after=0)
            assert [r["id"] for r in body["data"]["runs"]] == [report.run_id]
            status, body = await client.op("today")
            assert body["data"]["screens"] == [
                {
                    "id": screen["id"],
                    "name": "Above hundred",
                    "entered": 1,
                    "exited": 0,
                    "matches": 1,
                }
            ]
            assert body["data"]["recent"][0]["symbol"] == "AAA"
            assert body["data"]["recent"][0]["kind"] == "entered"

            status, body = await client.op("membership", list=7)
            member = body["data"]["rows"][0]
            assert (
                member["symbol"] == "AAA" and member["pinned"] is False and member["chip"]["kind"]
            )
            status, body = await client.op(
                "membership_update", id=member["id"], version=member["version"], action="pin"
            )
            assert status == 200 and body["data"]["pinned"] is True and body["data"]["chip"]
            status, body = await client.op(
                "membership_update", id=member["id"], version=member["version"], action="unpin"
            )
            assert status == 409
            status, body = await client.op(
                "membership_update",
                id=member["id"],
                version=member["version"] + 1,
                action="dismiss",
            )
            assert status == 200 and body["data"]["status"] == "dismissed"
            status, body = await client.op(
                "membership_update", id=member["id"], version=member["version"] + 2, action="pin"
            )
            assert status == 422 and body["error"]["message"].endswith(".")
            status, body = await client.op(
                "membership_update",
                id=member["id"],
                version=member["version"] + 2,
                action="restore",
            )
            assert status == 200 and body["data"]["status"] == "exited"
            status, body = await client.op("membership")
            assert status == 400
            pending = svc.store.read(OWNER, lambda tx: [r["kind"] for r in tx.outbox_due(NOW)])
            assert pending.count("list_sync") == 1, "owner moves coalesce into one list sync"

            status, body = await client.op("presets")
            cards = body["data"]["presets"]
            assert len(cards) >= 8 and all(c["name"] for c in cards)
            status, body = await client.op("universes")
            kinds = {u["kind"] for u in body["data"]["universes"]}
            assert "fno_stocks" in kinds and "watchlist" not in kinds
            assert any(u["note"] for u in body["data"]["universes"] if u["kind"] == "index_list")
            status, body = await client.op("preview", spec=SPEC)
            assert status == 200 and body["data"]["count"] == 0 and body["data"]["label"]
            status, body = await client.op("preview", screen_id=screen["id"])
            assert status == 200 and body["data"]["needs"]
            status, body = await client.op("preview", screen_id=999)
            assert status == 404
        finally:
            svc.stop()
            await asyncio.wait_for(task, 5)

    asyncio.run(scenario())
