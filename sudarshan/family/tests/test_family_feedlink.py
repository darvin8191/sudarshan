"""F6 (M18.1): the family service's feed link. What it claims (the quota, books and indices first,
the Y12 line), with which token, when (a change, every 10 minutes, a restart of the feed, released 5
minutes after the last socket), the ZMQ frames it takes (frame 0 compared whole: design 7.5 target
15) and the state every phone is told.

The feed is a fake on ephemeral ports (HTTP and a ZMQ PUB): never 5580 or 5581.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from pathlib import Path

import httpx
import pytest
from family_planekit import FakeFeed, frame
from family_testkit import close

from sudarshan.family import feedlink
from sudarshan.family.feedlink import (
    FeedLink,
    Health,
    allocate,
    feed_state,
    parse_health,
    tick_data,
)
from sudarshan.family.symbol_map import INDEX_KEYS
from sudarshan.tasks import cancel_and_wait

IDX = list(INDEX_KEYS)
BOOKS = [f"NSE_EQ|SYNTHB{n:05d}" for n in range(5)]
WATCH = [f"NSE_EQ|SYNTHW{n:05d}" for n in range(10)]


def run(coro):
    try:
        return asyncio.run(coro)
    finally:
        close(None)


# ---- what is claimed ------------------------------------------------------------------------------
def test_indices_and_books_come_first_and_the_quota_binds_everything():
    """Design 7.5 target 14: the quota is enforced."""
    assert allocate(IDX + BOOKS, WATCH, frozenset(), 100, quota=10) == tuple(
        IDX + BOOKS + WATCH[:2]
    )
    assert allocate(IDX + BOOKS, WATCH, frozenset(), 100, quota=4) == tuple(IDX + BOOKS[:1])
    assert len(allocate(IDX, [f"NSE_EQ|S{n}" for n in range(500)], frozenset(), 0, 100)) == 100
    assert allocate(IDX + IDX, WATCH[:1] * 3, frozenset(), 0, 100) == tuple(IDX + WATCH[:1])


def test_a_new_list_key_is_claimed_only_while_the_socket_stays_at_or_under_450():
    """Y12.3: the socket's total plus the new keys; a key held already costs nothing new; an
    unknown total admits no new list key; books and indices are claimed whatever the total."""
    assert allocate(IDX, WATCH, frozenset(), 445, 100) == tuple(IDX + WATCH[:2])  # 445 + 3 + 2
    assert allocate(IDX, WATCH, frozenset(IDX), 445, 100) == tuple(IDX + WATCH[:5])
    held = frozenset(IDX + WATCH[:4])
    assert allocate(IDX, WATCH, held, 470, 100) == tuple(IDX + WATCH[:4])  # over: kept, no more
    assert allocate(IDX + BOOKS, WATCH, frozenset(), 470, 100) == tuple(IDX + BOOKS)
    assert allocate(IDX, WATCH, frozenset(), None, 100) == tuple(IDX)


def test_a_feed_frame_becomes_the_phones_tick():
    data = tick_data(frame(101.5, 100.0))
    assert data == {"ltp": 101.5, "change": 1.5, "change_percent": 1.5, "close": 100.0,
                    "prev_close": 100.0, "ltt": 1_763_265_600_000,
                    "timestamp": 1_763_265_600_000}  # fmt: skip
    assert tick_data({"ltp": 0}) is None and tick_data({"cp": 100}) is None
    assert tick_data({"ltp": 5, "cp": 0})["change"] is None


def test_the_state_a_phone_is_told():
    live = Health("connected", True, "a", 10, heard_at=100.0)
    assert feed_state(live, True, 101.0) == "realtime"
    assert feed_state(live, False, 101.0) == "closed"
    assert feed_state(live, True, 116.0) == "reconnecting"  # no health for 15 s
    assert feed_state(None, True, 0.0) == "reconnecting"
    for state in ("connecting", "backoff", "authorizing", "no_token"):
        assert feed_state(Health(state, False, "a", 10, 100.0), True, 101.0) == "reconnecting"
    assert feed_state(Health("closed", False, "a", 10, 100.0), True, 101.0) == "closed"
    doc = parse_health(b'{"state":"connected","subscriptions_live":true,"started_at":"s",'
                       b'"subscribed":{"total":7}}', 5.0)  # fmt: skip
    assert doc == Health("connected", True, "s", 7, 5.0) and parse_health(b"[", 1.0) is None


# ---- the link against a fake feed ------------------------------------------------------------------
class Mono:
    def __init__(self):
        self.at = 1_000.0

    def __call__(self) -> float:
        return self.at


class Rig:
    """A FeedLink on a fake feed, with what it handed on recorded."""

    def __init__(self, root: Path, fake: FakeFeed, http_url: str, zmq_url: str, http, **more):
        self.token = secrets.token_urlsafe(32)
        (root / "feed-token").write_text(self.token + "\n", encoding="ascii")
        self.wanted: tuple[list[str], list[str]] = ([], [])
        self.sockets = 1
        self.ticks: list[tuple[str, dict]] = []
        self.states: list[str] = []
        self.claimed: list[frozenset[str]] = []
        self.mono = more.pop("monotonic", time.monotonic)
        self.link = FeedLink(http=http, feed_url=http_url, zmq_url=zmq_url, etc_dir=root,
                             quota=more.pop("quota", 100), clock=lambda: int(time.time() * 1000),
                             market_open=more.pop("market_open", lambda now: True),
                             wanted=lambda: self.wanted, sockets=lambda: self.sockets,
                             on_tick=lambda k, d: self.ticks.append((k, d)),
                             on_state=lambda s, t: self.states.append(s),
                             on_claims=self.claimed.append, monotonic=self.mono)  # fmt: skip


async def rig(root: Path, **more):
    fake = FakeFeed()
    http_url, zmq_url = await fake.start()
    http = httpx.AsyncClient(trust_env=False)
    return fake, http, Rig(root, fake, http_url, zmq_url, http, **more)


def test_claims_go_with_the_familys_own_token_renew_and_follow_a_restart(tmp_path):
    async def go():
        mono = Mono()
        fake, http, r = await rig(tmp_path, monotonic=mono)
        try:
            r.wanted = (BOOKS[:2], WATCH[:1])
            await r.link.step()
            ((body, auth),) = fake.claims()
            assert auth == f"Bearer {r.token}"
            assert body == {"keys": dict.fromkeys(sorted(IDX + BOOKS[:2] + WATCH[:1]), "ltpc")}
            assert r.claimed[-1] == frozenset(IDX + BOOKS[:2] + WATCH[:1])
            mono.at += 599
            await r.link.step()
            assert len(fake.claims()) == 1  # nothing changed, not yet ten minutes
            mono.at += 1
            await r.link.step()
            assert len(fake.claims()) == 2  # renewed
            r.link._heard(parse_health(b'{"state":"connected","subscriptions_live":true,'
                                       b'"started_at":"later","subscribed":{"total":1}}', mono.at))  # fmt: skip
            await r.link.step()
            assert len(fake.claims()) == 3  # the feed restarted and forgot the claims
            r.wanted = (BOOKS[:2], [])
            await r.link.step()
            assert set(fake.claims()[-1][0]["keys"]) == set(IDX + BOOKS[:2])
        finally:
            await http.aclose()
            await fake.close()

    run(go())


def test_claims_are_released_five_minutes_after_the_last_socket(tmp_path):
    async def go():
        mono = Mono()
        fake, http, r = await rig(tmp_path, monotonic=mono)
        try:
            await r.link.step()
            r.sockets = 0
            mono.at += 299
            await r.link.step()
            assert fake.claims("DELETE") == []
            mono.at += 1
            await r.link.step()
            ((_, auth),) = fake.claims("DELETE")
            assert auth == f"Bearer {r.token}"
            assert r.link.claimed == frozenset() and r.link.allocated == ()
            assert r.claimed[-1] == frozenset()
            await r.link.step()
            assert len(fake.claims("DELETE")) == 1
        finally:
            await http.aclose()
            await fake.close()

    run(go())


def test_a_refused_claim_is_said_once_and_tried_again_after_30_s(tmp_path, caplog):
    async def go():
        mono = Mono()
        fake, http, r = await rig(tmp_path, monotonic=mono)
        try:
            fake.status["PUT"] = 409
            with caplog.at_level("WARNING", logger="sudarshan.family"):
                await r.link.step()
                await r.link.step()
            assert len(fake.claims()) == 1 and r.link.counts["refused"] == 1
            assert r.link.claimed == frozenset()
            mono.at += 30
            fake.status["PUT"] = 200
            with caplog.at_level("WARNING", logger="sudarshan.family"):
                await r.link.step()
            assert len(fake.claims()) == 2 and r.link.claimed == frozenset(IDX)
            said = [m for m in caplog.messages if "refused the family view" in m]
            assert len(said) == 1 and "409" in said[0]
        finally:
            await http.aclose()
            await fake.close()

    run(go())


def test_a_frame_is_taken_only_for_a_key_claimed_whole_never_by_its_prefix(tmp_path):
    """Design 7.5 target 15: ZMQ hands a SUB of ``NSE_INDEX|Nifty 50`` the frames of
    ``NSE_INDEX|Nifty 500`` too; none of them reaches a phone."""

    async def go():
        fake, http, r = await rig(tmp_path)
        task = asyncio.create_task(r.link.run())
        try:
            for _ in range(500):
                if r.link.allocated:
                    break
                await asyncio.sleep(0.01)
            assert "NSE_INDEX|Nifty 500" not in r.link.allocated
            for _ in range(300):
                fake.publish("NSE_INDEX|Nifty 500", frame(999.0))
                fake.publish("NSE_INDEX|Nifty 50", frame(101.0))
                await asyncio.sleep(0.01)
                if r.ticks:
                    break
            assert r.ticks and {key for key, _ in r.ticks} == {"NSE_INDEX|Nifty 50"}
            assert all(data["ltp"] == 101.0 for _, data in r.ticks)
            assert r.link.ticks["NSE_INDEX|Nifty 50"]["ltp"] == 101.0
        finally:
            await cancel_and_wait(task)
            await http.aclose()
            await fake.close()

    run(go())


def test_health_on_the_bus_becomes_the_state_and_the_close_closes_it(tmp_path):
    async def go():
        market = {"open": True}
        fake, http, r = await rig(tmp_path, market_open=lambda now: market["open"])
        task = asyncio.create_task(r.link.run())
        try:
            for _ in range(300):
                fake.publish_health()
                await asyncio.sleep(0.01)
                if r.states:
                    break
            assert r.states == ["realtime"]
            fake.health = {**fake.health, "state": "backoff", "subscriptions_live": False}
            for _ in range(300):
                fake.publish_health()
                await asyncio.sleep(0.01)
                if r.states[-1] != "realtime":
                    break
            assert r.states[-1] == "reconnecting"
            market["open"] = False
            for _ in range(300):  # the clock is looked at each second
                await asyncio.sleep(0.01)
                if r.states[-1] == "closed":
                    break
            assert r.states == ["realtime", "reconnecting", "closed"]
        finally:
            await cancel_and_wait(task)
            await http.aclose()
            await fake.close()

    run(go())


def test_snapshots_come_from_the_links_own_ticks_else_the_feed(tmp_path):
    async def go():
        fake, http, r = await rig(tmp_path)
        try:
            r.link.ticks["NSE_INDEX|Nifty 50"] = frame(101.0)
            fake.snapshots["NSE_INDEX|Nifty Bank"] = frame(55_000.0, 54_000.0)
            found = await r.link.snapshot(["NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty Bank",
                                           "NSE_EQ|NEVER"])  # fmt: skip
            assert found["NSE_INDEX|Nifty 50"]["ltp"] == 101.0
            assert found["NSE_INDEX|Nifty Bank"]["ltp"] == 55_000.0 and "NSE_EQ|NEVER" not in found
            asked = [c for c in fake.calls if c[1] == "/snapshot"]
            assert len(asked) == 1  # the link's own tick is not asked for again
        finally:
            await http.aclose()
            await fake.close()

    run(go())


def test_a_stop_that_cancels_again_while_the_link_closes_still_closes_the_bus(tmp_path,
                                                                             monkeypatch):  # fmt: skip
    """fd-audit: the service's stop cancels a job again every 0.2 s while it has not ended. A
    second cancel inside the clean-up used to skip closing the ZMQ socket, and the context's
    term() then hung the process at exit."""
    real = feedlink.cancel_and_wait

    async def slow(*tasks, **kw):
        await asyncio.sleep(0.4)  # a clean-up that takes longer than one re-cancel
        await real(*tasks, **kw)

    async def go():
        fake, http, r = await rig(tmp_path)
        monkeypatch.setattr(feedlink, "cancel_and_wait", slow)
        task = asyncio.create_task(r.link.run())
        try:
            await asyncio.sleep(0.2)
            assert r.link._sock is not None
            await cancel_and_wait(task, again_after=0.05)
            assert r.link._sock is None and r.link._ctx is None
        finally:
            await http.aclose()
            await fake.close()

    run(go())


@pytest.mark.parametrize("quota", [1, 3])
def test_the_indices_alone_fill_a_tiny_quota(quota):
    assert allocate(IDX, WATCH, frozenset(), 0, quota) == tuple(IDX[:quota])


def test_a_bus_that_cannot_be_opened_leaves_nothing_open_however_often_it_is_tried(tmp_path):
    """fd-audit: an endpoint the settings let through but ZMQ refuses (no port) raises in
    ``connect``; the data plane tries the piece again every 5 s, so each failed try must close the
    context and the socket it made."""
    from sudarshan.tests.fdwatch import FdWatch

    async def go():
        async with httpx.AsyncClient(trust_env=False) as http:
            link = FeedLink(http=http, feed_url="http://127.0.0.1:9", zmq_url="tcp://127.0.0.1",
                            etc_dir=tmp_path, quota=100, clock=lambda: 0,
                            market_open=lambda now: True, wanted=lambda: ([], []),
                            sockets=lambda: 0, on_tick=lambda k, d: None,
                            on_state=lambda s, t: None, on_claims=lambda a: None)  # fmt: skip
            watch = FdWatch()
            for _ in range(20):
                with pytest.raises(Exception):  # noqa: B017 (ZMQ's own error)
                    await link.run()
                assert link._sock is None and link._ctx is None
            await watch.aassert_clean()

    run(go())
