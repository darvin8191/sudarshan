"""The screener's claims at the feed and its universes follow the screens (release review HIGH-2).

The real Service, its real ops and the real feed job (``runtime.run_feed``; its ZMQ SUB connects
to a port nobody publishes on); the job's HTTP client to the feed is swapped for one that records
the claims. A screen made, resumed or edited during the day is judged on its universe at once,
the claims shrink as screens stop and are dropped when none is active, and a service that starts
switched off drops whatever claim an earlier run left. A plain stop keeps the claims for the
restart, as market alerts do; --remove-ring drops them (the deploy tests).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from screener_helpers import ms
from test_screener_runs import SPEC
from test_screener_universe import master as master_fixture  # noqa: F401  (the fixture)

from sudarshan.screener import runtime
from sudarshan.screener import store as store_module
from sudarshan.screener.ops import REGISTRY
from sudarshan.screener.service import Service
from sudarshan.screener.tests.test_screener_service import FERNET, free_port, settings_for, until

DAY = "2026-10-28"
CLAIMS = f"/claims/{runtime.CLIENT}"


@pytest.fixture
def recorded(monkeypatch):
    calls: list[tuple[str, str, int]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        body = request.read()
        keys = len(httpx.Response(200, content=body).json().get("keys", {})) if body else 0
        calls.append((request.method, request.url.path, keys))
        return httpx.Response(200, json={"claimed": keys})

    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs.pop("transport", None)
        return real(*args, transport=httpx.MockTransport(answer), **kwargs)

    monkeypatch.setattr(runtime.httpx, "AsyncClient", client)
    return calls


async def reached(predicate, why: str, timeout: float = 5.0) -> None:
    try:
        await until(predicate, timeout)
    except AssertionError:
        raise AssertionError(why) from None


def _settings(tmp_path, master, enabled=True):
    return settings_for(
        free_port(),
        enabled=enabled,
        SUDARSHAN_SCREENER_STATE_DIR=str(tmp_path),
        SUDARSHAN_SCREENER_FEED_ZMQ=f"tcp://127.0.0.1:{free_port()}",
        DATABASE_URL=f"sqlite:///{master.db_path}",
        SUDARSHAN_NSE_HOLIDAYS_FILE=str(tmp_path / "none-holidays.txt"),
        SUDARSHAN_NSE_EXTRA_DAYS_FILE=str(tmp_path / "none-extra.txt"),
    )


def test_claims_and_universes_follow_the_screens_through_the_day(
    tmp_path,
    master_fixture,  # noqa: F811
    recorded,
):
    clock = {"now": ms(DAY, "09:20")}
    settings = _settings(tmp_path, master_fixture)

    async def scenario():
        svc = Service(settings, FERNET, jobs={"feed": lambda s: runtime.run_feed(s, tick_s=0.05)})
        svc.clock = lambda: clock["now"]
        task = asyncio.create_task(svc.run())

        def last():
            return recorded[-1] if recorded else None

        try:
            await until(lambda: svc.state == "running" and "source" in svc.parts)
            source = svc.parts["source"]

            # 09:20 the owner makes a screen on the F&O stocks: claimed at once
            stocks = await REGISTRY["create"](svc, {"spec": SPEC})
            await reached(
                lambda: last() == ("PUT", CLAIMS, 2),
                "a screen made during the day is not claimed until the next day",
            )
            assert len(source.members({"kind": "fno_stocks"})) == 2

            # 10:00 he pauses it and makes one on index futures: its universe is built now, not
            # tomorrow, and the claim is the new screen's keys alone
            clock["now"] = ms(DAY, "10:00")
            await REGISTRY["update"](
                svc, {"id": stocks["id"], "version": stocks["version"], "action": "pause"}
            )
            futures = dict(SPEC, name="Index futures", universe={"kind": "index_futures"})
            made = await REGISTRY["create"](svc, {"spec": futures})
            await reached(
                lambda: source.members({"kind": "index_futures"}) != [],
                "a screen made during the day has no universe until the next day",
            )
            await reached(lambda: last() == ("PUT", CLAIMS, 2), "the new screen is not claimed")
            members = [m.symbol for m in source.members({"kind": "index_futures"})]
            assert members == ["BANKNIFTY30OCT26FUT", "NIFTY30OCT26FUT"]
            assert source.members({"kind": "fno_stocks"}) == [], "a paused screen's keys linger"

            # he pauses that one too: nothing is active, so the claim is dropped
            await REGISTRY["update"](
                svc, {"id": made["id"], "version": made["version"], "action": "pause"}
            )
            await reached(
                lambda: last() == ("DELETE", CLAIMS, 0),
                "the claims stay at the feed with every screen paused",
            )
            assert source.keys == frozenset()

            # and resumes the first: claimed again
            again = svc.store.read("owner", lambda tx: tx.screen(stocks["id"]))
            await REGISTRY["update"](
                svc, {"id": again["id"], "version": again["version"], "action": "resume"}
            )
            await reached(
                lambda: last() == ("PUT", CLAIMS, 2), "a resumed screen is not claimed again"
            )
            # at the start, with nothing active, a claim an earlier run left was dropped
            assert recorded[0] == ("DELETE", CLAIMS, 0)
        finally:
            svc.stop("test")
            await task

    asyncio.run(scenario())
    store_module.stop()


def test_a_service_switched_off_drops_the_claims_an_earlier_run_left(
    tmp_path,
    master_fixture,  # noqa: F811
    recorded,
):
    settings = _settings(tmp_path, master_fixture, enabled=False)

    async def scenario():
        svc = Service(settings, FERNET)  # the production jobs: off, only the off-time job runs
        task = asyncio.create_task(svc.run())
        try:
            await reached(
                lambda: ("DELETE", CLAIMS, 0) in recorded,
                "a screener switched off leaves its claims at the feed",
            )
            assert svc.store is None and recorded == [("DELETE", CLAIMS, 0)]
        finally:
            svc.stop("test")
            await task

    asyncio.run(scenario())


class ListLink:
    """The bridge's internal watchlist route as the service's sealed link answers it."""

    def __init__(self, items):
        self.items, self.reads = items, []

    async def watchlist_read(self, list_id):
        self.reads.append(list_id)
        if self.items is None:
            raise RuntimeError("watchlist_read: connecterror")
        return {"list_id": list_id, "name": "Mine", "items": list(self.items)}


def test_a_watchlist_screen_looks_at_the_owners_list(
    tmp_path,
    master_fixture,  # noqa: F811
    recorded,
):
    """Release review MEDIUM-7: the live source never read the owner's list, so a watchlist
    universe was always empty. The feed job reads it through the bridge's internal route."""
    clock = {"now": ms(DAY, "09:20")}
    settings = _settings(tmp_path, master_fixture)
    universe = {"kind": "watchlist", "list_id": 3}

    async def scenario():
        svc = Service(settings, FERNET, jobs={"feed": lambda s: runtime.run_feed(s, tick_s=0.05)})
        svc.clock = lambda: clock["now"]
        link = svc.parts["link"] = ListLink(
            [
                {"id": 1, "symbol": "SBIN", "exchange": "NSE"},
                {"id": 2, "symbol": "INFY", "exchange": "NSE"},
            ]
        )
        task = asyncio.create_task(svc.run())
        try:
            await until(lambda: svc.state == "running" and "source" in svc.parts)
            source = svc.parts["source"]
            await REGISTRY["create"](svc, {"spec": dict(SPEC, universe=universe)})
            await reached(
                lambda: [m.symbol for m in source.members(universe)] == ["SBIN", "INFY"],
                "a watchlist universe is always empty",
            )
            await reached(lambda: recorded[-1] == ("PUT", CLAIMS, 2), "its keys are not claimed")
            assert link.reads == [3]
        finally:
            svc.stop("test")
            await task

    asyncio.run(scenario())
    store_module.stop()
