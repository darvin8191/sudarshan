"""The service against a fake Upstox on 127.0.0.1: fan-out, HTTP API, reconnects, refusals."""

import asyncio
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

import feed_fakes as ff
import httpx
import zmq
import zmq.asyncio

from sudarshan.feed import service
from sudarshan.feed.config import Settings
from sudarshan.feed.http_api import is_local
from sudarshan.feed.replay import read_ticks
from sudarshan.feed.service import FeedService, Throttle
from sudarshan.feed.subscriptions import Subscriptions

REPO_ROOT = Path(__file__).resolve().parents[3]
REL, INFY, NIFTY = "NSE_EQ|INE002A01018", "NSE_EQ|INE009A01021", "NSE_INDEX|Nifty 50"
FEED_CMD = (sys.executable, "-m", "sudarshan.feed")


def _summary(reqs: list[dict]) -> list[tuple]:
    return [(r["method"], r["data"].get("mode"), len(r["data"]["instrumentKeys"])) for r in reqs]


async def _stop(svc: FeedService, run: asyncio.Task) -> None:
    svc.stop("test")
    await asyncio.wait_for(run, 10)


def test_fan_out_http_api_and_clean_close(feed_env, fast, caplog):
    caplog.set_level(logging.DEBUG, logger="sudarshan.feed")

    async def scenario():
        async with ff.FakeUpstox() as fake:
            cfg = Settings.from_env(feed_env(fake))
            svc = FeedService(cfg)
            run = asyncio.create_task(svc.run())
            await ff.wait_until(lambda: svc.http is not None)
            ctx = zmq.asyncio.Context()
            sub = ctx.socket(zmq.SUB)
            sub.connect(svc.pub.endpoint)
            sub.subscribe(b"NSE_")
            try:
                # Every key is re-sent on connect, in batches (SUB_BATCH=2), as binary frames.
                await ff.wait_until(lambda: fake.requests and len(fake.requests[0]) == 3)
                assert _summary(fake.requests[0]) == [
                    ("sub", "ltpc", 2), ("sub", "ltpc", 1), ("sub", "full", 1)
                ]  # fmt: skip
                assert all(r["binary"] for r in fake.requests[0])
                await ff.wait_until(lambda: svc.health()["subscriptions_live"])
                server = fake.sockets[0]
                await server.send(ff.response({}, "market_info", {"NSE_EQ": "NORMAL_OPEN"}))
                feeds = {REL: ff.full_stock(), NIFTY: ff.ltpc(25000.5, cp=24900.0)}
                await server.send(ff.response(feeds, kind="initial_feed"))
                got = {}
                while len(got) < 2:
                    topic, payload = await asyncio.wait_for(sub.recv_multipart(), 2)
                    got[topic.decode()] = json.loads(payload)
                assert got[REL]["key"] == REL and got[REL]["bid"] == 1400.4
                assert got[REL]["ts_recv"] > 0 and got[NIFTY]["cp"] == 24900.0
                await server.send(ff.response({REL: ff.full_stock(ltp=1401.0, with_ltpc=False)}))
                _, payload = await asyncio.wait_for(sub.recv_multipart(), 2)
                assert json.loads(payload)["ltp"] == 1400.5  # no LTPC in the packet: last LTP kept

                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{svc.http_port}") as http:
                    health = (await http.get("/health")).json()
                    assert health["state"] == "connected" and health["reconnects"] == 0
                    assert health["subscribed"]["total"] == 4 and health["token_file_readable"]
                    assert health["market_status"] == {"NSE_EQ": "NORMAL_OPEN"}
                    assert health["keys_without_data"]["count"] == 2  # Nifty Bank, India VIX
                    assert health["last_message_age_s"] < 5 and health["market_window"]["open"]
                    snap = (await http.get("/snapshot", params={"keys": f"{NIFTY},{INFY}"})).json()
                    assert snap["ticks"][NIFTY]["ltp"] == 25000.5 and snap["ticks"][INFY] is None
                    every = (await http.get("/snapshot/all")).json()["ticks"]
                    assert set(every) == {REL, NIFTY}
                    body = {"mode": "full", "keys": [INFY, NIFTY]}
                    ok = (await http.post("/subscribe", json=body)).json()
                    assert (ok["new"], ok["mode_changed"], ok["applied_live"]) == (1, 1, True)
                    await ff.wait_until(lambda: len(fake.requests[0]) == 5)
                    assert [r["method"] for r in fake.requests[0][3:]] == ["sub", "change_mode"]
                    body = {"mode": "full_d30", "keys": [f"NSE_FO|{n}" for n in range(51)]}
                    over = await http.post("/subscribe", json=body)
                    assert over.status_code == 409 and "cap" in over.json()["error"]
                    bad = await http.post("/subscribe", json={"mode": "depth", "keys": [INFY]})
                    assert bad.status_code == 400
                    assert (await http.post("/subscribe", content=b"not json")).status_code == 400
                    gone = (await http.post("/unsubscribe", json={"keys": [INFY]})).json()
                    assert gone["removed"] == 1
                    await ff.wait_until(lambda: fake.requests[0][-1]["method"] == "unsub")
                    assert fake.requests[0][-1]["data"] == {"instrumentKeys": [INFY]}
                    assert (await http.get("/subscriptions")).json()["counts"]["full"] == 2
                    assert (await http.get("/nope")).status_code == 404
                    assert (await http.post("/health")).status_code == 405
                    browser = await http.get("/health", headers={"Origin": "https://example.org"})
                    assert browser.status_code == 403
            finally:
                sub.close(0)
                ctx.term()
            await _stop(svc, run)
            await ff.wait_until(lambda: fake.close_codes == [1000])  # a close frame: slot freed
            assert svc.health()["state"] == "stopped"
        state = cfg.state_dir
        assert json.loads((state / "snapshot.json").read_text())["ticks"][REL]["ltp"] == 1400.5
        restarted = Subscriptions.load(cfg.keys_file, state / "subscriptions.json").desired()
        assert restarted[NIFTY] == "full" and INFY not in restarted
        (recording,) = (state / "ticks").glob("*.jsonl.gz")
        assert len(list(read_ticks(recording))) == 3
        assert ff.TOKEN not in caplog.text and ff.CODE not in caplog.text

    asyncio.run(scenario())


def test_reconnects_with_a_fresh_authorize_and_resubscribes(feed_env, fast):
    async def scenario():
        async with ff.FakeUpstox() as fake:
            svc = FeedService(Settings.from_env(feed_env(fake)))
            run = asyncio.create_task(svc.run())
            await ff.wait_until(lambda: len(fake.requests) == 1 and len(fake.requests[0]) == 3)
            fake.sockets[0].transport.abort()  # dropped without a close frame
            await ff.wait_until(lambda: len(fake.requests) == 2 and len(fake.requests[1]) == 3)
            health, drop = svc.health(), svc.health()["last_disconnect"]
            assert fake.authorize_calls == 2 and health["reconnects"] == 1
            assert (drop["by"], drop["code"]) == ("dropped", 1006) and drop["socket_age_s"] >= 0
            await _stop(svc, run)
            await ff.wait_until(lambda: fake.close_codes == [1006, 1000])

    asyncio.run(scenario())


def test_refused_upgrade_names_the_connection_limit_then_recovers(feed_env, fast):
    async def scenario():
        async with ff.FakeUpstox() as fake:
            fake.upgrade_status = 403
            svc = FeedService(Settings.from_env(feed_env(fake)))
            run = asyncio.create_task(svc.run())
            await ff.wait_until(lambda: svc.health()["state"] == "backoff")
            health = svc.health()
            assert health["last_error"]["kind"] == "upgrade_refused"
            assert "HTTP 403" in health["last_error"]["message"]
            assert "connection limit" in health["last_error"]["hint"]
            assert health["next_retry_in_s"] > 0.1  # REFUSED_FLOOR_S (0.3 s here, 60 s live)
            fake.upgrade_status = None
            await ff.wait_until(lambda: svc.health()["state"] == "connected")
            await _stop(svc, run)

    asyncio.run(scenario())


def test_rejected_token_backs_off_long_and_retries_when_the_file_changes(feed_env, fast, caplog):
    caplog.set_level(logging.DEBUG, logger="sudarshan.feed")

    async def scenario():
        async with ff.FakeUpstox(token="the-current-token-9876543210") as fake:
            env = feed_env(fake)  # the token file holds ff.TOKEN, which this fake refuses
            svc = FeedService(Settings.from_env(env))
            run = asyncio.create_task(svc.run())
            await ff.wait_until(lambda: svc.health()["state"] == "token_rejected")
            health = svc.health()
            assert health["last_error"]["kind"] == "token_rejected"
            assert health["next_retry_in_s"] > 20  # TOKEN_REJECTED_S (30 s here, 10 min live)
            assert "token rejected" in caplog.text and "UDAPI100050" in caplog.text
            Path(env["SUDARSHAN_UPSTOX_TOKEN_FILE"]).write_text(fake.token + "\n")
            await ff.wait_until(lambda: svc.health()["state"] == "connected")
            assert fake.authorize_calls == 2
            exposed = json.dumps(svc.health())
            await _stop(svc, run)
        for secret in (ff.TOKEN, fake.token, ff.CODE):
            assert secret not in caplog.text and secret not in exposed

    asyncio.run(scenario())


def test_socket_is_held_only_inside_the_market_window(feed_env, fast):
    now = [ff.ist(2026, 10, 21, 15, 49, 58)]  # a Wednesday, two seconds before 15:50

    async def scenario():
        async with ff.FakeUpstox() as fake:
            env = feed_env(fake, SUDARSHAN_FEED_ALWAYS_ON="0")
            svc = FeedService(Settings.from_env(env), clock=lambda: now[0])
            run = asyncio.create_task(svc.run())
            await ff.wait_until(lambda: svc.health()["state"] == "connected")
            now[0] = ff.ist(2026, 10, 21, 15, 50, 1)
            await ff.wait_until(lambda: svc.health()["state"] == "closed")
            await ff.wait_until(lambda: fake.close_codes == [1000])
            health = svc.health()
            assert health["detail"] == "after the market window"
            assert health["market_window"]["next_open"] == "2026-10-22T08:58:00+05:30"
            assert health["last_disconnect"]["by"] == "market window closed"
            await _stop(svc, run)
            assert fake.authorize_calls == 1

    asyncio.run(scenario())


def test_sigterm_closes_cleanly_and_saves_state(feed_env, tmp_path):
    http_port, zmq_port = ff.free_port(), ff.free_port()
    ports = {"SUDARSHAN_FEED_HTTP_PORT": str(http_port), "SUDARSHAN_FEED_ZMQ_PORT": str(zmq_port)}
    env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}

    async def ticks_seen(http: httpx.AsyncClient) -> int:
        return (await http.get(f"http://127.0.0.1:{http_port}/health")).json()["stats"]["ticks"]

    async def scenario():
        async with ff.FakeUpstox() as fake:
            run_env = env | feed_env(fake) | ports
            with open(tmp_path / "feed.log", "wb") as log:  # a file, never a pipe
                proc = await asyncio.create_subprocess_exec(
                    *FEED_CMD, cwd=REPO_ROOT, env=run_env, stdout=log, stderr=log
                )
            try:
                await ff.wait_until(lambda: fake.requests and len(fake.requests[0]) == 2, 20)
                frame = ff.response({NIFTY: ff.ltpc(25000.0)}, kind="initial_feed")
                await fake.sockets[0].send(frame)
                async with httpx.AsyncClient() as http:
                    deadline = time.monotonic() + 10
                    while await ticks_seen(http) < 1:
                        assert time.monotonic() < deadline, "the tick never reached the service"
                        await asyncio.sleep(0.05)
                proc.send_signal(signal.SIGTERM)
                assert await asyncio.wait_for(proc.wait(), 15) == 0
            finally:
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
            await ff.wait_until(lambda: fake.close_codes == [1000])
        log_text = (tmp_path / "feed.log").read_text()
        assert "closing the feed socket: service stopping" in log_text
        assert ff.TOKEN not in log_text and ff.CODE not in log_text
        snapshot = json.loads((tmp_path / "state" / "snapshot.json").read_text())
        assert snapshot["ticks"][NIFTY]["ltp"] == 25000.0

    asyncio.run(scenario())


def test_retry_delays(feed_env):
    svc = FeedService(Settings.from_env(feed_env()))
    for failures in range(1, 14):
        ceiling = min(300.0, 2.0 * 2 ** (failures - 1))
        assert ceiling / 2 <= svc._retry_delay("dropped", failures) <= ceiling
    assert svc._retry_delay("upgrade_refused", 1) >= 60.0  # wait out a stale socket
    svc._token_refusals = 1
    assert svc._retry_delay("token_rejected", 1) == 600.0
    svc._token_refusals = 9
    assert svc._retry_delay("token_rejected", 1) == 3600.0


def test_error_logs_are_rate_limited(caplog):
    throttle = Throttle(every=60)
    for _ in range(3):
        throttle(logging.ERROR, "kind", "upstream refused")
    assert caplog.text.count("upstream refused") == 1
    throttle._seen["kind"] = (time.monotonic() - 61, 2)
    throttle(logging.ERROR, "kind", "upstream refused")
    assert "upstream refused (2 similar suppressed)" in caplog.text


def test_only_local_peers_are_served():
    assert is_local(("127.0.0.1", 40000)) and is_local(("::1", 40000, 0, 0))
    assert not is_local(("10.1.2.3", 40000)) and not is_local(None)


def test_closing_auction_status_in_health_one_line_per_change(feed_env, fast, monkeypatch, caplog):
    """M18.1 (X6h): Upstox's casMarketStatus as cas_status, on the health topic at once, and in
    the journal: the first market info on every socket (an empty map too) and every change."""
    caplog.set_level(logging.INFO, logger="sudarshan.feed")
    monkeypatch.setattr(service, "HEALTH_EVERY_S", 3600.0)  # so only a change publishes health
    at_1515, at_1520 = 1_793_094_300_120, 1_793_094_600_250  # 27 Oct 2026 IST, epoch ms
    open_ = {"NSE_EQ": "NORMAL_OPEN"}

    def info(cas: dict | None, segments=open_) -> bytes:
        return ff.response({}, "market_info", segments, cas)

    def phase(status: str, at: int) -> dict:
        return {"status": status, "updated_ms": at}

    async def health_showing(sub, cas_status: dict) -> None:
        while True:  # well inside the 2 s: the hourly publish never comes here
            topic, payload = await asyncio.wait_for(sub.recv_multipart(), 2)
            if topic == b"__health__" and json.loads(payload)["cas_status"] == cas_status:
                return

    async def scenario():
        async with ff.FakeUpstox() as fake:
            svc = FeedService(Settings.from_env(feed_env(fake)))
            run = asyncio.create_task(svc.run())
            await ff.wait_until(lambda: svc.health()["subscriptions_live"])
            assert svc.health()["cas_status"] == {}
            ctx = zmq.asyncio.Context()
            sub = ctx.socket(zmq.SUB)
            sub.connect(svc.pub.endpoint)
            sub.subscribe(b"__health__")
            await asyncio.sleep(0.3)  # a SUB misses what is sent before it has joined
            try:
                server = fake.sockets[0]
                both = {"NSE_EQ": ("CTS_CLOSE", at_1515), "BSE_EQ": ("CTS_CLOSE", at_1515)}
                await server.send(info(both))
                await health_showing(sub, {s: phase("CTS_CLOSE", at_1515) for s in both})
                await server.send(info(both))  # the same state again: no line
                await server.send(info(both | {"NSE_EQ": ("CAS_LM_START", at_1520)}))
                await health_showing(sub, {"NSE_EQ": phase("CAS_LM_START", at_1520),
                                           "BSE_EQ": phase("CTS_CLOSE", at_1515)})  # fmt: skip
                later = {"NSE_EQ": ("CAS_LM_START", at_1520), "BSE_EQ": ("CTS_CLOSE", at_1520)}
                await server.send(info(later))  # only BSE_EQ's time moves
                await health_showing(sub, {s: phase(p, at_1520) for s, (p, _) in later.items()})
                await server.send(info(None, {"NSE_EQ": "NORMAL_CLOSE"}))
                await health_showing(sub, {})  # Upstox lists no auction any more
                server.transport.abort()  # the next socket's first market info is told too
                await ff.wait_until(lambda: len(fake.requests) == 2 and len(fake.requests[1]) == 3)
                await fake.sockets[1].send(info(None))
                await fake.sockets[1].send(ff.response({NIFTY: ff.ltpc(25000.0)}))  # no info
                await ff.wait_until(lambda: svc.health()["stats"]["ticks"] == 1)
                health = svc.health()
            finally:
                sub.close(0)
                ctx.term()
            await _stop(svc, run)
        assert health["cas_status"] == {} and health["market_status"] == {"NSE_EQ": "NORMAL_OPEN"}
        told = [r for r in caplog.records if r.getMessage().startswith("closing auction status")]
        at = "2026-10-27T15:15:00+05:30"
        assert [(r.levelname, r.getMessage()) for r in told] == [
            ("INFO", f"closing auction status on connect: BSE_EQ CTS_CLOSE at {at}, NSE_EQ "
                     f"CTS_CLOSE at {at}"),
            ("INFO", "closing auction status: NSE_EQ CAS_LM_START at 2026-10-27T15:20:00+05:30"),
            ("INFO", "closing auction status: BSE_EQ CTS_CLOSE at 2026-10-27T15:20:00+05:30"),
            ("INFO", "closing auction status: BSE_EQ none, NSE_EQ none"),
            ("INFO", "closing auction status on connect: none"),
        ]  # fmt: skip

    asyncio.run(scenario())
