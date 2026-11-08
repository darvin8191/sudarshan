"""End to end (BR1): the real bridge, the real service with its real ops and a temporary store,
through the owner's flow. A screen is created from the page's route, a run is made from a fake
price source by the real run loop, the results, the history, the rewind and the membership are
read back in the shapes types.ts declares, a membership is pinned, the alerts are switched off, the
list sync reaches the real internal watchlist route, and the screen is deleted."""

from __future__ import annotations

import asyncio
import os
import threading

import httpx
import pytest
from screener_bridge_app import csrf_headers, login_session, make_app, reset_users, reset_watchlists
from screener_helpers import ms
from test_screener_runs import SPEC, FakeSource
from test_screener_runtime import Calendar

import blueprints.sudarshan_screener as bp_module
from database import watchlist_db
from sudarshan.screener import config, contract, listsync, runner
from sudarshan.screener.link import Link
from sudarshan.screener.service import Service
from sudarshan.screener.store import OWNER
from sudarshan.vault import (
    ALERTS_SCREENER_DIGEST_INFO,
    SCREENER_API_INFO,
    SCREENER_INTERNAL_INFO,
    fernet_for,
)
from utils.thread_safe_cache import LockedTTLCache

PEPPER = os.environ["API_KEY_PEPPER"]
PREFIX = contract.API_PREFIX
JSON = {"Accept": "application/json"}
NOW = ms("2026-10-28", "10:15:05")


class RealService:
    """The real Service (ops, store) in a thread with its own loop, no feed, clock fixed."""

    def __init__(self, tmp_path, port: int):
        settings = config.Settings.from_env({
            "SUDARSHAN_SCREENER": "on",
            "SUDARSHAN_SCREENER_HTTP_PORT": str(port),
            "SUDARSHAN_SCREENER_STATE_DIR": str(tmp_path),
            "SUDARSHAN_DEPLOYED_TAG_FILE": "/nonexistent/deployed_tag",
            "SUDARSHAN_NSE_HOLIDAYS_FILE": str(tmp_path / "none-holidays.txt"),
            "SUDARSHAN_NSE_EXTRA_DAYS_FILE": str(tmp_path / "none-extra.txt"),
        })  # fmt: skip
        self.service = Service(settings, fernet_for(PEPPER, SCREENER_API_INFO), jobs={})
        self.service.clock = lambda: NOW
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="screener-e2e", daemon=True)
        self.task = None

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.task = self.loop.create_task(self.service.run())
        self.loop.run_forever()
        self.loop.run_until_complete(self.task)
        self.loop.close()

    def __enter__(self):
        self.thread.start()
        for _ in range(200):
            if self.service.state == "running":
                return self
            threading.Event().wait(0.02)
        raise AssertionError("the service did not come up")

    def __exit__(self, *exc) -> None:
        self.loop.call_soon_threadsafe(self.service.stop, "test")
        self.thread.join(10)


def free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def world(tmp_path, monkeypatch):
    reset_users()
    reset_watchlists()
    http = httpx.Client(trust_env=False)
    monkeypatch.setattr(bp_module, "get_httpx_client", lambda: http)
    monkeypatch.setattr(bp_module, "_warned", LockedTTLCache(maxsize=8, ttl=600))
    port = free_port()
    monkeypatch.setenv("SUDARSHAN_SCREENER_API_URL", f"http://127.0.0.1:{port}")
    app = make_app()
    client = app.test_client()
    login_session(client)
    with RealService(tmp_path, port) as running:
        yield client, running.service, csrf_headers(client)
    http.close()


def data_of(response):
    body = response.get_json()
    assert body["status"] == "success", body
    assert body["enabled"] is True
    return body["data"]


def test_the_owner_flow_end_to_end(world):
    client, service, headers = world

    # the page opens: nothing yet, the engine line is honest about the feed
    state = data_of(client.get(f"{PREFIX}/state", headers=JSON))
    assert state["screens"] == [] and state["limits"] == {
        "active": 12,
        "active_used": 0,
        "max_keys": 320,
    }
    assert state["engine"]["real_time"] is False and "price feed" in state["engine"]["line"]
    assert set(state) >= {"enabled", "engine", "screens", "limits", "last_run_id", "server_time"}

    # the owner saves a screen (with the page's request id)
    made = data_of(
        client.post(
            f"{PREFIX}/screens", json={"spec": SPEC, "request_id": "page-1"}, headers=headers
        )
    )
    for field in (
        "id",
        "version",
        "name",
        "status",
        "status_reason",
        "where",
        "spec",
        "matches",
        "entered_today",
        "added_count",
        "target_name",
        "next_check",
        "last_run_id",
        "updated_at",
    ):
        assert field in made, field  # fmt: skip
    assert made["status"] == "active" and made["matches"] == 0 and made["list_id"] == 7
    screen_id = made["id"]
    again = data_of(
        client.post(
            f"{PREFIX}/screens", json={"spec": SPEC, "request_id": "page-1"}, headers=headers
        )
    )
    assert again["id"] == screen_id

    # a bad screen is refused on its line, in words
    bad = {
        **SPEC,
        "where": {
            "id": "c1",
            "left": {"field": "close", "tf": 15},
            "op": "gt",
            "right": {"num": "abc"},
        },
    }
    refused = client.post(f"{PREFIX}/screens", json={"spec": bad}, headers=headers)
    assert refused.status_code == 422
    assert refused.get_json()["error"] == {
        "code": "refused",
        "message": "Type a number, like 55 or 2500.50.",
        "field": "c1.right",
    }

    # the real run loop makes the due runs from a fake price source (no feed in this process)
    source = FakeSource({"NSE_EQ|A": 12000, "NSE_EQ|B": 11000, "NSE_EQ|C": None})
    loop_runner = runner.Runner(service.store, source, lambda: NOW, Calendar())
    assert asyncio.run(loop_runner.tick()) == 4  # 09:30, 09:45, 10:00 and 10:15

    # the results, as types.ts declares them
    results = data_of(client.get(f"{PREFIX}/screens/{screen_id}/results", headers=JSON))
    assert results["screen_id"] == screen_id and results["run"]["candle"] == "10:00 to 10:15"
    assert results["run"]["matches"] == 2 and results["not_checked"] == {
        "count": 1,
        "reasons": ["Not checked: no price from Upstox yet"],
    }
    rows = results["rows"]
    assert [r["symbol"] for r in rows] == ["AAA", "BBB"]
    for row in rows:
        assert set(row) >= {
            "member_id",
            "symbol",
            "exchange",
            "rank",
            "values",
            "forming",
            "chip",
            "ltp",
            "change_pct",
        }
        assert row["chip"]["icon"] and row["chip"]["text"] and row["chip"]["name"]
    assert (
        rows[0]["ltp"] == 12000
        and rows[0]["change_pct"] == 33.33
        and rows[0]["values"] == "Close 120.00"
    )

    state = data_of(client.get(f"{PREFIX}/state", headers=JSON))
    card = state["screens"][0]
    assert (
        card["matches"] == 2
        and card["entered_today"] == 2
        and card["last_run_id"] == results["run"]["id"]
    )
    assert state["last_run_id"] == results["run"]["id"] and state["limits"]["active_used"] == 1

    # history, catch-up and rewind
    page = data_of(client.get(f"{PREFIX}/screens/{screen_id}/runs?limit=3", headers=JSON))
    assert len(page["runs"]) == 3 and page["more"] is True
    assert page["runs"][0]["marks"] == [] and page["runs"][-1]["marks"] == [
        "checked late at 10:15 after a restart"
    ]
    older = data_of(
        client.get(
            f"{PREFIX}/screens/{screen_id}/runs?limit=3&before={page['runs'][-1]['id']}",
            headers=JSON,
        )
    )
    assert len(older["runs"]) == 1 and older["more"] is False
    since = data_of(client.get(f"{PREFIX}/runs/since?after=0", headers=JSON))
    assert [r["id"] for r in since["runs"]] == sorted(r["id"] for r in older["runs"] + page["runs"])
    rewind = data_of(client.get(f"{PREFIX}/runs/{older['runs'][0]['id']}", headers=JSON))
    assert rewind["run"]["candle"] == "09:15 to 09:30" and [
        r["symbol"] for r in rewind["rows"]
    ] == ["AAA", "BBB"]
    assert rewind["values_note"] is None
    gone = client.get(f"{PREFIX}/runs/999999", headers=JSON)
    assert gone.status_code == 404 and gone.get_json()["error"]["code"] == "not_found"

    # the membership: a pin with the row's version, a stale one refused
    members = data_of(client.get(f"{PREFIX}/membership?list=7", headers=JSON))["rows"]
    member = next(m for m in members if m["symbol"] == "AAA")
    assert set(member) >= {
        "id",
        "version",
        "screen_id",
        "list_id",
        "symbol",
        "exchange",
        "status",
        "pinned",
        "first_seen_at",
        "chip",
    }
    pinned = data_of(
        client.patch(
            f"{PREFIX}/membership/{member['id']}",
            json={"action": "pin", "version": member["version"]},
            headers=headers,
        )
    )
    assert pinned["pinned"] is True and pinned["chip"]["kind"] == "pinned"
    stale = client.patch(
        f"{PREFIX}/membership/{member['id']}",
        json={"action": "unpin", "version": member["version"]},
        headers=headers,
    )
    assert stale.status_code == 409 and stale.get_json()["error"]["code"] == "version_conflict"

    # alerts off, then the edit, each from the version the page saw
    off = data_of(
        client.patch(
            f"{PREFIX}/screens/{screen_id}",
            json={"action": "alerts_off", "version": made["version"]},
            headers=headers,
        )
    )
    assert off["alerts_paused"] is True and off["version"] == made["version"] + 1
    edited = data_of(
        client.patch(
            f"{PREFIX}/screens/{screen_id}",
            json={"action": "edit", "version": off["version"], "spec": {**SPEC, "name": "Renamed"}},
            headers=headers,
        )
    )
    assert edited["name"] == "Renamed" and edited["version"] == off["version"] + 1
    conflict = client.patch(
        f"{PREFIX}/screens/{screen_id}",
        json={"action": "pause", "version": off["version"]},
        headers=headers,
    )
    assert conflict.status_code == 409

    # the list sync reaches the real internal route: the outbox's list_sync row, through the Link
    emitted: list = []
    bp_module.emit_from_any_thread = lambda event, data, **kw: emitted.append((event, data))

    def to_bridge(request: httpx.Request) -> httpx.Response:
        path = request.url.path + (f"?{request.url.query.decode()}" if request.url.query else "")
        answer = client.post(
            path, data=request.content, headers={"Content-Type": "application/octet-stream"}
        )
        return httpx.Response(
            answer.status_code, content=answer.data, headers={"Content-Type": "application/json"}
        )

    watchlist_db.create_watchlist("owner", "Mine")  # the page's list 7 does not exist: make one
    list_id = watchlist_db.get_watchlists("owner")[0]["id"]
    link = Link("http://openalgo", "http://alerts", fernet_for(PEPPER, SCREENER_INTERNAL_INFO),
                fernet_for(PEPPER, ALERTS_SCREENER_DIGEST_INFO), client=httpx.AsyncClient(transport=httpx.MockTransport(to_bridge)))  # fmt: skip
    service.store.write(
        OWNER,
        lambda tx: (
            tx.membership_relist(screen_id, 7, list_id, NOW),
            tx.state_put(screen_id, NOW, list_id=list_id),
        ),
    )
    synced = asyncio.run(
        listsync.sync(
            service.store,
            link,
            screen_id=screen_id,
            list_id=list_id,
            create_name=None,
            ref="e2e",
            now=NOW,
        )
    )
    asyncio.run(link.aclose())
    assert synced.status == "done" and set(synced.added) == {"NSE_EQ|A", "NSE_EQ|B"}
    items = {i["symbol"] for i in watchlist_db.get_watchlists("owner")[0]["items"]}
    assert items == {"AAA", "BBB"} and emitted == [(contract.EVENT_LISTS, {"list_id": list_id})]

    # today's card, the presets and the universes read as the page expects
    today = data_of(client.get(f"{PREFIX}/today", headers=JSON))
    assert (
        today["screens"][0]["matches"] == 2
        and today["recent"][0]["kind"] == "entered"
        and today["unseen_alerts"] == 0
    )
    presets = data_of(client.get(f"{PREFIX}/presets", headers=JSON))["presets"]
    assert len(presets) >= 8 and all(
        {"key", "name", "about", "usable", "where", "spec", "reason"} <= set(p) for p in presets
    )
    universes = data_of(client.get(f"{PREFIX}/universes", headers=JSON))["universes"]
    assert all({"kind", "label", "note"} <= set(u) for u in universes)
    preview = data_of(
        client.post(f"{PREFIX}/preview", json={"screen_id": screen_id}, headers=headers)
    )
    assert set(preview) >= {"label", "count", "first", "not_checked"}

    # delete, removing what the screener added: one list sync is queued, the screen is gone
    deleted = data_of(
        client.delete(f"{PREFIX}/screens/{screen_id}", json={"remove_added": True}, headers=headers)
    )
    assert deleted == {"deleted": screen_id, "removed_from_list": 2}
    assert client.get(f"{PREFIX}/screens/{screen_id}/results", headers=JSON).status_code == 404
    pending = service.store.read(
        OWNER, lambda tx: [r["kind"] for r in tx.outbox_due(NOW + 10**6, 100)]
    )
    assert "list_sync" in pending
