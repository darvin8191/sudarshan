"""The run loop and the outbox sender (SV6 runtime): due runs made and recorded from a fake source,
a failed evaluation recorded once, the sender's order, backoff and give-up per kind, and the
sealed link against the real bridge routes and a fake alerts endpoint."""

from __future__ import annotations

import asyncio
import os
from datetime import date

import httpx
import pytest
from screener_bridge_app import make_app, reset_users, reset_watchlists
from screener_helpers import ms
from test_screener_runs import SESSION, SPEC, FakeSource, make_screen

import blueprints.sudarshan_screener as bp_module
from sudarshan.autologin.handoff import open_sealed
from sudarshan.screener import outbox, runner
from sudarshan.screener import store as store_module
from sudarshan.screener.link import Link, LinkError
from sudarshan.screener.store import OWNER, ScreenerStore
from sudarshan.vault import ALERTS_SCREENER_DIGEST_INFO, SCREENER_INTERNAL_INFO, fernet_for

DAY = "2026-10-28"
PEPPER = os.environ["API_KEY_PEPPER"]


class Calendar:
    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5

    def windows(self):
        return {}


@pytest.fixture
def opened(tmp_path):
    made = ScreenerStore(tmp_path / "screener.db")
    yield made
    made.close()
    store_module.stop()


def test_the_runner_makes_every_due_run_once_and_records_a_failed_evaluation(opened):
    screen = make_screen(opened)
    source = FakeSource({"NSE_EQ|A": 12000, "NSE_EQ|B": 9000})
    clock = {"now": ms(DAY, "10:15:05")}
    made = runner.Runner(opened, source, lambda: clock["now"], Calendar())
    # 09:30, 09:45, 10:00 and 10:15 are due at 10:15:05 (the first three late)
    assert asyncio.run(made.tick()) == 4
    assert made.made == 4 and made.status()["last_run_key"].endswith(":15:3")  # k counts from 0
    assert asyncio.run(made.tick()) == 0, "recorded keys are not run again"
    runs = opened.read(OWNER, lambda tx: tx.runs_page(screen["id"], None, 10))
    assert [r["run_key"].rsplit(":", 1)[1] for r in runs] == ["3", "2", "1", "0"]
    assert runs[-1]["marks"].get("late") == ms(DAY, "10:15:05") and "late" not in runs[0]["marks"]
    assert runs[0]["matched"] == 1
    clock["now"] = ms(DAY, "10:30:05")
    source.closes["NSE_EQ|A"] = None  # no price: judged not checked, still a run

    def boom(*args, **kwargs):
        raise RuntimeError("the source broke")

    source.context = boom
    assert asyncio.run(made.tick()) == 1 and made.failed == 1
    latest = opened.read(OWNER, lambda tx: tx.runs_page(screen["id"], None, 1))[0]
    assert latest["status"] == "failed" and latest["run_key"].endswith(":15:4")
    assert asyncio.run(made.tick()) == 0, "a failed boundary is not tried for ever"
    assert asyncio.run(made.tick(ms("2026-10-25", "12:00"))) == 0, "a Sunday has no sessions"


def test_the_retention_runs_at_start_and_once_a_day_at_16_30(opened):
    make_screen(opened)
    clock = {"now": ms(DAY, "10:15:05")}
    made = runner.Runner(opened, FakeSource({}), lambda: clock["now"], Calendar())
    assert made.prune(clock["now"]) == {"run_change": 0, "run_value": 0, "run": 0, "outbox": 0}
    assert made.pruned_day == 20261028
    assert made.prune(ms(DAY, "16:29")) is None and made.prune(ms(DAY, "16:31")) is None, (
        "once today"
    )
    assert made.prune(ms("2026-10-29", "16:29")) is None, "the next day waits for 16:30"
    assert made.prune(ms("2026-10-29", "16:30")) is not None and made.pruned_day == 20261029


class FakeLink:
    def __init__(self, fail: set[str] = frozenset()):
        self.fail = set(fail)
        self.sent: list[tuple] = []

    async def watchlist_read(self, list_id):
        if "list_sync" in self.fail:
            raise LinkError(503, "busy")
        return {"list_id": list_id, "name": "Mine", "items": []}

    async def watchlist_apply(self, **kwargs):
        self.sent.append(("apply", kwargs))
        return {"list_id": kwargs.get("list_id") or 11, "name": "Mine", "items": []}

    async def digest(self, payload):
        if "digest" in self.fail:
            raise LinkError(None, "not answering")
        self.sent.append(("digest", payload))
        return {"status": "success"}

    async def push(self, items):
        if "push" in self.fail:
            raise LinkError(None, "not answering")
        self.sent.append(("push", items))
        return 1


def test_the_sender_sends_in_order_retries_with_backoff_and_gives_up_per_kind(opened):
    now = {"t": ms(DAY, "10:15:06")}
    opened.write(OWNER, lambda tx: tx.screen_insert("S", {"schema": 1}, "active", now["t"]))

    def queue(tx):
        tx.outbox_add("push", "k1", {"screen_id": 1, "run_id": 1}, now["t"] - 2)
        tx.outbox_add("digest", "k1", {"screen": "S"}, now["t"] - 1)
        tx.outbox_add(
            "list_sync",
            "1:7",
            {"screen_id": 1, "list_id": 7, "create_name": None},
            now["t"],
            coalesce=True,
        )

    opened.write(OWNER, queue)
    link = FakeLink(fail={"push", "digest", "list_sync"})
    sender = outbox.Sender(opened, link, lambda: now["t"])
    assert asyncio.run(sender.tick()) == 3
    rows = {
        r["kind"]: r for r in opened.read(OWNER, lambda tx: tx.outbox_due(now["t"] + 10**9, 100))
    }
    assert "push" not in rows, "a push is never retried"
    assert sender.failed["push"] == 1
    assert rows["digest"]["attempts"] == 1 and rows["digest"]["next_at"] == now["t"] + 2000
    assert rows["list_sync"]["next_at"] == now["t"] + 2000
    assert asyncio.run(sender.tick()) == 0, "not due yet"
    now["t"] += 2000
    assert asyncio.run(sender.tick()) == 2
    rows = {
        r["kind"]: r for r in opened.read(OWNER, lambda tx: tx.outbox_due(now["t"] + 10**9, 100))
    }
    assert rows["digest"]["attempts"] == 2 and rows["digest"]["next_at"] == now["t"] + 5000
    # the digest gives up after 5 minutes, the list sync keeps trying up to 30
    now["t"] += 6 * 60_000
    assert asyncio.run(sender.tick()) == 2
    rows = {
        r["kind"]: r for r in opened.read(OWNER, lambda tx: tx.outbox_due(now["t"] + 10**9, 100))
    }
    assert "digest" not in rows and sender.failed["digest"] == 1
    assert "list_sync" in rows
    link.fail.clear()
    now["t"] += 60_000
    assert asyncio.run(sender.tick()) == 1
    assert (
        sender.sent["list_sync"] == 1
        and opened.read(OWNER, lambda tx: tx.outbox_count("pending")) == 0
    )
    assert outbox.next_attempt("list_sync", 99, now["t"], now["t"]) == now["t"] + 60_000
    assert outbox.next_attempt("push", 0, now["t"], now["t"]) is None


def test_the_sealed_link_reaches_the_bridges_routes_and_a_fake_alerts_endpoint():
    """The service's Link against the real bridge app (its internal routes) and a fake alerts
    API, over a loopback socket each: the seals open, the envelope is read, refusals are
    LinkErrors with the server's words."""
    reset_users()
    reset_watchlists()
    app = make_app()
    bridge = app.test_client()
    emitted: list = []
    received: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("http://alerts"):
            try:
                message = open_sealed(
                    fernet_for(PEPPER, ALERTS_SCREENER_DIGEST_INFO),
                    request.content,
                    op="screener_digest",
                    seen={},
                )
            except Exception:
                return httpx.Response(
                    403, json={"status": "error", "message": "The alerts refused the seal."}
                )
            received.append(message["args"])
            return httpx.Response(200, json={"status": "success", "data": {"claimed": True}})
        path = request.url.path + (f"?{request.url.query.decode()}" if request.url.query else "")
        answer = bridge.post(
            path, data=request.content, headers={"Content-Type": "application/octet-stream"}
        )
        return httpx.Response(
            answer.status_code, content=answer.data, headers={"Content-Type": "application/json"}
        )

    bp_module.emit_from_any_thread = lambda event, data, **kw: emitted.append((event, data))
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    link = Link(
        "http://openalgo", "http://alerts",
        fernet_for(PEPPER, SCREENER_INTERNAL_INFO), fernet_for(PEPPER, ALERTS_SCREENER_DIGEST_INFO),
        client=client,
    )  # fmt: skip

    async def scenario():
        try:
            assert await link.watchlist_read(424242) is None
            made = await link.watchlist_apply(
                list_id=None, create_name="Screener: Breakout",
                add=[{"key": "k", "symbol": "SBIN", "exchange": "NSE"}], remove=[], ref="r",
            )  # fmt: skip
            assert made["name"] == "Screener: Breakout" and [
                i["symbol"] for i in made["items"]
            ] == ["SBIN"]
            again = await link.watchlist_read(made["list_id"])
            assert again["items"][0]["symbol"] == "SBIN"
            with pytest.raises(LinkError) as caught:
                await link.watchlist_apply(
                    list_id=424242, create_name=None, add=[], remove=[], ref="r"
                )
            assert caught.value.status == 404
            assert (
                await link.push([{"event": "sudarshan_screener_run", "data": {"screen_id": 1}}])
                == 0
            )
            assert emitted[-1] == ("sudarshan_screener_run", {"screen_id": 1})
            answer = await link.digest({"screen": "Breakout", "run_key": "1:v1:20261028:15:4"})
            assert answer["status"] == "success" and received[0]["screen"] == "Breakout"
            wrong = Link("http://openalgo", "http://alerts", fernet_for(PEPPER, SCREENER_INTERNAL_INFO),
                         fernet_for("q" * 48, ALERTS_SCREENER_DIGEST_INFO), client=client)  # fmt: skip
            with pytest.raises(LinkError) as refused:
                await wrong.digest({"screen": "x"})
            assert refused.value.status == 403 and "refused the seal" in str(refused.value)
        finally:
            await link.aclose()

    asyncio.run(scenario())
    assert SESSION and SPEC  # the shared fixtures are what the runner test judged
