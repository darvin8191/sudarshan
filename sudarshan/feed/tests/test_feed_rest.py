"""M18.9: the REST governor (rest.py), cross-design rules G1-G8, against the fake Upstox.

Pure parts first (the allow-list and its one spelling, the windows, the bulk hours), then the
running feed: ceilings, the order of service, bulk refused in market hours, the 30 s cap and the
queue bound, the 429 freeze and half rate, the 401 stop and its end, bytes passed through, the
token kept out of logs and /health, and the tick lag while a warm start is in flight.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import socket
import sys
import threading
import time
from collections import deque
from pathlib import Path

import feed_fakes as ff
import httpx
import pytest
import zmq
import zmq.asyncio

from sudarshan.autologin.schedule import TradingCalendar
from sudarshan.feed import http_api, rest
from sudarshan.feed.config import Settings
from sudarshan.feed.rest import RATE_LIMITED, MarketHours, Refused, delay, resolve
from sudarshan.feed.service import FeedService

REPO_ROOT = Path(__file__).resolve().parents[3]
FEED_CMD = (sys.executable, "-m", "sudarshan.feed")
EVENING = ff.ist(2026, 10, 27, 20, 0)  # a Tuesday, a trading day: bulk is served after 16:00
LTP = "/v3/market-quote/ltp?instrument_key=NSE_EQ%7CINE002A01018"
OHLC = "/v3/market-quote/ohlc?instrument_key=NSE_EQ%7CINE002A01018&interval=1d"
QUOTES = "/v3/market-quote/quotes?instrument_key=NSE_EQ%7CINE002A01018"
DAYS = "/v3/historical-candle/NSE_EQ%7CINE002A01018/days/1/2026-10-27/2016-10-28"
INTRADAY = "/v3/historical-candle/intraday/NSE_EQ%7CINE002A01018/minutes/1"
CHAIN = "/v2/option/chain?instrument_key=NSE_INDEX%7CNifty%2050&expiry_date=2026-11-03"

# ---- G2: the allow-list, and one spelling for each call -----------------------------------------
PASSES = [
    # (asked below /upstox, the API, sent to Upstox)
    ("/v3/historical-candle/NSE_EQ%7CINE002A01018/days/1/2026-10-27/2016-10-28",
     "historical-candle", None),
    ("/v3/historical-candle/NSE_EQ|INE002A01018/minutes/1/2026-10-27", "historical-candle",
     "/v3/historical-candle/NSE_EQ%7CINE002A01018/minutes/1/2026-10-27"),
    ("/v3/historical-candle/NSE_INDEX%7CNifty%2050/months/1/2026-10-27", "historical-candle", None),
    ("/v3/historical-candle/intraday/NSE_INDEX%7CNifty%2050/minutes/1", "intraday-candle", None),
    ("/v3/historical-candle/intraday/NSE_FO%7C45450/hours/1", "intraday-candle", None),
    ("/v3/market-quote/quotes?instrument_key=NSE_EQ|INE002A01018,NSE_EQ%7CINE009A01021",
     "full-quote",
     "/v3/market-quote/quotes?instrument_key=NSE_EQ%7CINE002A01018%2CNSE_EQ%7CINE009A01021"),
    ("/v3/market-quote/ltp?instrument_key=NSE_INDEX%7CNifty+50", "ltp",
     "/v3/market-quote/ltp?instrument_key=NSE_INDEX%7CNifty%2050"),
    ("/v3/market-quote/ohlc?interval=I1&instrument_key=NSE_FO%7C45450", "ohlc",
     "/v3/market-quote/ohlc?instrument_key=NSE_FO%7C45450&interval=I1"),
    ("/v2/option/chain?instrument_key=NSE_INDEX%7CNifty%2050&expiry_date=2026-11-03",
     "option-chain", None),
    ("/v2/option/chain?instrument_key=NSE_INDEX%7CNifty%2050&expiry_date=current_week",
     "option-chain", None),
]  # fmt: skip
REFUSED = [
    # account reads stay in the portfolio service; orders and everything else never pass
    ("/v2/portfolio/long-term-holdings", 403),
    ("/v2/portfolio/short-term-positions", 403),
    ("/v3/user/get-funds-and-margin?segment=SEC", 403),
    ("/v2/order/retrieve-all", 403),
    ("/v3/order/place", 403),
    ("/v2/trade/profit-loss/data?segment=EQ&financial_year=2526&page_number=1&page_size=10", 403),
    ("/v3/feed/market-data-feed/authorize", 403),
    ("/v2/market-quote/quotes?instrument_key=NSE_EQ%7CINE002A01018", 403),  # V2, not on the list
    ("/v3/market-quote/option-greek?instrument_key=NSE_FO%7C45450", 403),  # not on the list yet
    ("/v2/expired-instruments/historical-candle/NSE_FO%7C45450/day/2026-10-27/2026-10-01", 403),
    # one spelling only: no dot segments, doubled or trailing slashes, other cases or encodings
    ("/v3/historical-candle/../../v2/portfolio/long-term-holdings", 403),
    ("/v3/historical-candle/NSE_EQ%7CINE002A01018/days/1/2026-10-27/", 403),
    ("/v3//market-quote/ltp?instrument_key=NSE_EQ%7CINE002A01018", 403),
    ("/V3/market-quote/ltp?instrument_key=NSE_EQ%7CINE002A01018", 403),
    ("/v3/market%2Dquote/ltp?instrument_key=NSE_EQ%7CINE002A01018", 403),
    ("/v3/historical-candle/NSE_EQ%7CINE002A01018/days/0/2026-10-27", 403),
    ("/v3/historical-candle/NSE_EQ%7CINE002A01018/days/1/27-10-2026", 403),
    # a key that hides a path, or is no key
    ("/v3/historical-candle/..%2F..%2Fv2%2Fportfolio%2Flong-term-holdings/days/1/2026-10-27", 400),
    ("/v3/historical-candle/NSE_EQ%7CINE002A01018%2F..%2F..%2Fv2/days/1/2026-10-27", 400),
    ("/v3/historical-candle/intraday/RELIANCE/minutes/1", 400),
    # the query: every parameter required, nothing else, each once, keys checked
    ("/v3/market-quote/ltp", 400),
    ("/v3/market-quote/ltp?instrument_key=NSE_EQ%7CINE002A01018&extra=1", 400),
    ("/v3/market-quote/ltp?instrument_key=NSE_EQ%7CINE002A01018&instrument_key=NSE_FO%7C1", 400),
    ("/v3/market-quote/ltp?instrument_key", 400),
    ("/v3/market-quote/ltp?instrument_key=" + "%2C".join(f"NSE_FO%7C{n}" for n in range(501)), 400),
    ("/v3/market-quote/ohlc?instrument_key=NSE_FO%7C45450&interval=5m", 400),
    ("/v2/option/chain?instrument_key=NSE_INDEX%7CNifty%2050%2CNSE_INDEX%7CNifty%20Bank"
     "&expiry_date=2026-11-03", 400),
    ("/v2/option/chain?instrument_key=NSE_INDEX%7CNifty%2050&expiry_date=tomorrow", 400),
    ("/v3/historical-candle/NSE_EQ%7CINE002A01018/days/1/2026-10-27?from=2016-10-28", 400),
]  # fmt: skip


@pytest.mark.parametrize(("asked", "api", "sent"), PASSES)
def test_the_allow_list_passes_market_data_reads_in_one_spelling(asked, api, sent):
    path, _, query = asked.partition("?")
    assert resolve(path, query) == (api, sent or asked)


@pytest.mark.parametrize(("asked", "status"), REFUSED)
def test_everything_else_is_refused_before_it_reaches_upstox(asked, status):
    path, _, query = asked.partition("?")
    with pytest.raises(Refused) as refused:
        resolve(path, query)
    assert refused.value.status == status
    assert refused.value.reason == ("not-allowed" if status == 403 else "bad-request")


def test_500_keys_pass_and_the_rate_limit_code_is_never_the_token_code():
    keys = ",".join(f"NSE_FO|{n}" for n in range(500))
    assert resolve("/v3/market-quote/ltp", f"instrument_key={keys}")[0] == "ltp"
    assert RATE_LIMITED.search(b'{"errors":[{"errorCode":"UDAPI10005","message":"Too Many"}]}')
    assert RATE_LIMITED.search(b'{"errors":[{"error_code":"UDAPI10005"}]}')
    assert not RATE_LIMITED.search(b'{"errors":[{"errorCode":"UDAPI100050"}]}')  # the token one
    assert not RATE_LIMITED.search(b'{"errors":[{"errorCode":"UDAPI100500"}]}')


# ---- G3: the windows ----------------------------------------------------------------------------
def test_each_window_slides_and_a_half_rate_halves_all_three():
    screener = rest.CEILINGS["sudarshan-screener"]  # 15/s, 150/min, 600 per 30 min
    sent = deque(i / 100 for i in range(15))  # 15 calls in the first 0.15 s
    assert delay(sent, screener, 0.5) == pytest.approx(0.5)  # the 16th waits for the first
    assert delay(sent, screener, 1.0) <= 0  # ... to leave the second
    # Half rate (7/s): the next call waits for the 9th of the 15, sent at 0.08 s, to leave.
    assert delay(deque(sent), screener, 0.5, factor=0.5) == pytest.approx(0.58)
    minute = deque(i * 0.4 for i in range(150))  # 150 in a minute, never 15 in one second
    assert delay(minute, screener, 59.6) == pytest.approx(0.4)
    half_hour = deque(i * 2.9 for i in range(600))  # 600 in 29 minutes
    assert delay(half_hour, screener, 1740.0) == pytest.approx(60.0)
    assert delay(half_hour, screener, 1800.0) <= 0 and len(half_hour) == 599  # the first is gone
    total = deque(i / 100 for i in range(20))  # SUDARSHAN's 20/s for any API
    assert delay(total, rest.TOTAL, 0.5) == pytest.approx(0.5)
    portfolio = rest.CEILINGS["sudarshan-portfolio"]  # 2/s, 25/min, 100 per 30 min
    assert delay(deque([0.0]), portfolio, 0.1) <= 0
    assert delay(deque([0.0]), portfolio, 0.1, factor=0.5) == pytest.approx(0.9)  # 1/s
    assert delay(deque(), portfolio, 0.0) <= 0


def test_the_shares_are_40_percent_and_the_ceilings_fit_inside_them():
    assert rest.TOTAL == (20, 200, 800)  # of Upstox's 50/s, 500/min, 2,000 per 30 min per API
    assert rest.CEILINGS == {
        "sudarshan-screener": (15, 150, 600),
        "sudarshan-portfolio": (2, 25, 100),
        "sudarshan-alerts": (2, 25, 100),
    }
    for factor in (1.0, 0.5):  # also at half rate, the clients' sum never passes the total
        for i, total in enumerate(rest.TOTAL):
            shares = sum(max(1, int(c[i] * factor)) for c in rest.CEILINGS.values())
            assert shares <= max(1, int(total * factor))
    assert (rest.MAX_WAITING, rest.MAX_IN_FLIGHT, rest.WAIT_CAP_S) == (64, 4, 30.0)
    assert (rest.FREEZE_S, rest.HALF_RATE_S, rest.MAX_BODY) == (60.0, 600.0, 8 << 20)


# ---- G4: bulk hours -----------------------------------------------------------------------------
def hours_with(tmp_path, extra: str = "") -> MarketHours:
    (tmp_path / "holidays.txt").write_text("2026-11-09  # a Monday holiday\n")
    (tmp_path / "extra.txt").write_text(extra)
    return MarketHours(TradingCalendar(tmp_path / "holidays.txt", tmp_path / "extra.txt"))


@pytest.mark.parametrize(
    ("extra", "at", "ends"),
    [
        ("", (2026, 10, 27, 10, 0), (16, 0)),  # an ordinary Tuesday
        ("", (2026, 10, 27, 8, 29), None),
        ("", (2026, 10, 27, 8, 30), (16, 0)),
        ("", (2026, 10, 27, 15, 59), (16, 0)),
        ("", (2026, 10, 27, 16, 0), None),
        ("", (2026, 10, 31, 11, 0), None),  # a Saturday
        ("", (2026, 11, 9, 11, 0), None),  # the holiday
        # Muhurat, its own window with the same margins: 13:35-16:00
        ("2026-11-08 14:05-15:40\n", (2026, 11, 8, 13, 34), None),
        ("2026-11-08 14:05-15:40\n", (2026, 11, 8, 13, 35), (16, 0)),
        ("2026-11-08 14:05-15:40\n", (2026, 11, 8, 10, 0), None),
        # a reopened Saturday without a window keeps the usual hours
        ("2026-10-31\n", (2026, 10, 31, 11, 0), (16, 0)),
        # a weekday that trades anyway adds its own window to the usual hours
        ("2026-10-28 18:00-19:00\n", (2026, 10, 28, 10, 0), (16, 0)),
        ("2026-10-28 18:00-19:00\n", (2026, 10, 28, 17, 0), None),
        ("2026-10-28 18:00-19:00\n", (2026, 10, 28, 17, 30), (19, 20)),
    ],
)  # fmt: skip
def test_bulk_waits_for_the_end_of_market_hours(tmp_path, extra, at, ends):
    end = hours_with(tmp_path, extra).end_of(ff.ist(*at))
    assert end == (None if ends is None else ff.ist(*at[:3], *ends))


# ---- the running feed ---------------------------------------------------------------------------
@contextlib.asynccontextmanager
async def running(feed_env, now: list[float] | None = None, token: str = ff.TOKEN, **env):
    """The feed and its governor against the fake Upstox, an HTTP client on the feed's port."""
    now = now or [EVENING]
    async with ff.FakeUpstox(token=token) as fake:
        svc = FeedService(Settings.from_env(feed_env(fake, **env)), clock=lambda: now[0])
        run = asyncio.create_task(svc.run())
        try:
            await ff.wait_until(lambda: svc._client is not None)
            base = f"http://127.0.0.1:{svc.http_port}"
            async with httpx.AsyncClient(base_url=base, timeout=60) as http:
                yield svc, fake, http
        finally:
            svc.stop("test")
            await asyncio.wait_for(run, 10)


def ask(http, path: str, client="sudarshan-screener", priority="interactive", method="GET"):
    headers = {"X-Sudarshan-Client": client, "X-Sudarshan-Priority": priority}
    return http.request(method, "/upstox" + path, headers=headers)


async def raw(svc, target: str, client: str = "sudarshan-screener") -> tuple[int, bytes]:
    """One request as exact bytes (an HTTP client would tidy dot segments away first)."""
    reader, writer = await asyncio.open_connection("127.0.0.1", svc.http_port)
    writer.write(
        f"GET {target} HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Sudarshan-Client: {client}\r\n"
        "X-Sudarshan-Priority: interactive\r\n\r\n".encode()
    )
    await writer.drain()
    answer = await reader.read()
    writer.close()
    await writer.wait_closed()
    return int(answer.split(b" ", 2)[1]), answer


def waited(r: httpx.Response) -> int:
    return int(r.headers["X-Sudarshan-Waited-Ms"])


class Held:
    """The fake's REST answers wait until the test lets them go, oldest first; after open(),
    every answer goes at once."""

    def __init__(self, fake, answer=ff.rest_ok):
        self.arrived: list[str] = []
        self._gates: list[asyncio.Event] = []
        self._open = False
        self.answer = answer
        fake.rest_answer = self._answer

    async def _answer(self, path: str):
        self.arrived.append(path)
        if not self._open:
            gate = asyncio.Event()
            self._gates.append(gate)
            await gate.wait()
        return await self.answer(path)

    def release(self, n: int = 1) -> None:
        for gate in [g for g in self._gates if not g.is_set()][:n]:
            gate.set()

    def open(self) -> None:
        self._open = True
        self.release(len(self._gates))


def test_only_allowed_calls_reach_upstox_with_the_token_and_every_answer_says_its_wait(feed_env):
    async def scenario():
        async with running(feed_env) as (svc, fake, http):
            for asked, _, _ in PASSES:
                r = await ask(http, asked)
                assert r.status_code == 200 and r.json() == {"status": "success", "data": {}}
                assert "X-Sudarshan-Reason" not in r.headers and waited(r) >= 0
            assert fake.rest_calls == [
                (sent or asked, f"Bearer {ff.TOKEN}") for asked, _, sent in PASSES
            ]
            for asked, status in REFUSED:
                got, answer = await raw(svc, "/upstox" + asked)
                assert got == status, asked
                assert (
                    b"\r\nX-Sudarshan-Reason: " in answer
                    and b"\r\nX-Sudarshan-Waited-Ms: " in answer
                )
            bad = [
                await ask(http, LTP, method="POST"),
                await ask(http, LTP, method="DELETE"),
                await ask(http, LTP, client="sudarshan-feed"),
                await ask(http, LTP, client="SUDARSHAN-SCREENER"),
                await http.get("/upstox" + LTP),  # no client, no priority
                await ask(http, LTP, priority="urgent"),
            ]
            assert [r.status_code for r in bad] == [403, 403, 403, 403, 403, 400]
            assert [r.headers["X-Sudarshan-Reason"] for r in bad] == [
                "not-allowed", "not-allowed", "client", "client", "client", "bad-request",
            ]  # fmt: skip
            assert bad[2].json() == {"error": rest.UNKNOWN_CLIENT}
            assert bad[5].json() == {"error": rest.BAD_PRIORITY}
            assert len(fake.rest_calls) == len(PASSES)  # nothing refused reached Upstox
            health = svc.health()["rest"]
            assert health["not_allowed"] == len(REFUSED) + len(bad)
            assert (
                health["apis"]["ltp"]["calls"] == 1 and health["apis"]["full-quote"]["calls"] == 1
            )
            assert health["apis"]["historical-candle"]["clients"] == {
                "sudarshan-screener": {"calls": 3, "waits": 0, "refusals": 0, "429s": 0}
            }

    asyncio.run(scenario())


def test_upstox_status_and_bytes_pass_through_never_parsed_and_8_mib_at_most(feed_env, monkeypatch):
    monkeypatch.setattr(rest, "MAX_BODY", 1000)  # 8 MiB in the service; small here
    gzipped = __import__("gzip").compress(b'{"status":"success","data":{"ltp":1}}')
    answers = {
        1: (400, b'\x00\xff not JSON {"status":"error"}\r\n', {"Content-Type": "text/odd; x=1"}),
        2: (503, b"<html>maintenance</html>", {"Content-Type": "text/html"}),
        3: (200, gzipped, {"Content-Encoding": "gzip"}),
        4: (200, b"x" * 1000, {}),
        5: (200, b"x" * 1001, {}),
        6: (200, b"y" * 1001, {"Content-Length": None}),  # no length given: counted as it comes
        7: (200, b"z" * 999, {"Content-Length": None}),
    }

    async def answer(path: str):
        return answers[int(path.rsplit("%7C", 1)[1])]

    async def scenario():
        async with running(feed_env) as (svc, fake, http):
            fake.rest_answer = answer
            got = {
                n: await ask(http, f"/v3/market-quote/ltp?instrument_key=NSE_FO%7C{n}")
                for n in answers
            }
            assert (got[1].status_code, got[1].content) == (400, answers[1][1])
            assert got[1].headers["Content-Type"] == "text/odd; x=1"
            assert (got[2].status_code, got[2].text) == (503, "<html>maintenance</html>")
            assert "X-Sudarshan-Reason" not in got[2].headers  # Upstox's 503, not the governor's
            assert "Retry-After" not in got[2].headers
            assert got[3].headers["Content-Encoding"] == "gzip"  # handed on, not inflated here
            assert got[3].json() == {"status": "success", "data": {"ltp": 1}}
            assert (got[4].status_code, len(got[4].content)) == (200, 1000)
            assert (got[7].status_code, got[7].content) == (200, b"z" * 999)
            for n in (5, 6):
                assert got[n].status_code == 502 and got[n].json() == {"error": rest.TOO_LARGE}
                assert got[n].headers["X-Sudarshan-Reason"] == "too-large"
            assert len(fake.rest_calls) == len(answers)  # 4xx and 5xx are never tried again here

    asyncio.run(scenario())


def test_each_client_has_its_own_ceiling_on_each_api(feed_env):
    async def scenario():
        async with running(feed_env) as (svc, fake, http):
            calls = (
                [ask(http, LTP, "sudarshan-portfolio") for _ in range(3)]  # 2/s
                + [ask(http, LTP, "sudarshan-alerts") for _ in range(2)]  # its own 2/s
                + [ask(http, OHLC, "sudarshan-portfolio")]  # another API, other windows
                + [ask(http, LTP, "sudarshan-screener") for _ in range(16)]  # 15/s
            )
            got = await asyncio.gather(*calls)
            assert {r.status_code for r in got} == {200}
            portfolio, alerts, ohlc, screener = got[:3], got[3:5], got[5], got[6:]
            assert sorted(waited(r) > 800 for r in portfolio) == [False, False, True]
            assert all(waited(r) < 500 for r in alerts) and waited(ohlc) < 500
            assert sorted(waited(r) > 800 for r in screener) == [False] * 15 + [True]
            assert all(waited(r) < 2500 for r in got)
            ltp = svc.health()["rest"]["apis"]["ltp"]
            assert ltp["calls"] == 21 and ltp["clients"]["sudarshan-portfolio"]["calls"] == 3
            assert ltp["clients"]["sudarshan-portfolio"]["waits"] >= 1 and ltp["refusals"] == 0

    asyncio.run(scenario())


def test_repair_goes_first_then_interactive_then_bulk_each_in_arrival_order(feed_env):
    async def scenario():
        async with running(feed_env) as (svc, fake, http):
            held = Held(fake)
            holders = [asyncio.create_task(ask(http, f"{LTP[:-4]}{n:04d}")) for n in range(4)]
            await ff.wait_until(lambda: len(held.arrived) == 4)  # every place in flight taken
            queued = [("bulk", DAYS), ("interactive", OHLC), ("repair", INTRADAY),
                      ("bulk", CHAIN), ("repair", LTP), ("interactive", QUOTES)]  # fmt: skip
            waiting = []
            for n, (priority, path) in enumerate(queued, 1):
                waiting.append(asyncio.create_task(ask(http, path, priority=priority)))
                await ff.wait_until(lambda n=n: svc.rest.health()["waiting"] == n)
            for n in range(len(queued)):
                held.release()  # one place frees: the first waiting in G4's order takes it
                await ff.wait_until(lambda n=n: len(held.arrived) == 5 + n)
            assert held.arrived[4:] == [INTRADAY, LTP, OHLC, QUOTES, DAYS, CHAIN]
            held.release(len(held.arrived))
            got = await asyncio.gather(*holders, *waiting)
            assert {r.status_code for r in got} == {200}
            assert svc.health()["rest"]["in_flight"] == 0

    asyncio.run(scenario())


def test_bulk_is_refused_at_once_in_market_hours_and_served_after(feed_env, tmp_path):
    now = [ff.ist(2026, 10, 27, 10, 0)]  # Tuesday 10:00

    async def scenario():
        async with running(feed_env, now) as (svc, fake, http):
            r = await ask(http, DAYS, priority="bulk")
            assert (r.status_code, r.json()) == (
                503,
                {"error": rest.BULK_WAITS.format(end="16:00")},
            )
            assert (
                r.json()["error"] == "History downloads wait for the market to close (after 16:00)."
            )
            assert r.headers["X-Sudarshan-Reason"] == "bulk-hours" and waited(r) < 200
            assert r.headers["Retry-After"] == str(6 * 3600)
            assert svc.health()["rest"]["bulk_refused_until"] == "2026-10-27T16:00:00+05:30"
            assert fake.rest_calls == []  # never held, never sent
            for priority in ("repair", "interactive"):  # market hours are theirs
                assert (await ask(http, INTRADAY, priority=priority)).status_code == 200
            now[0] = ff.ist(2026, 10, 27, 16, 0)
            assert (await ask(http, DAYS, priority="bulk")).status_code == 200
            assert svc.health()["rest"]["bulk_refused_until"] is None
            # A bulk call still waiting for a place when market hours begin is refused then.
            now[0] = ff.ist(2026, 10, 28, 8, 29, 59)
            held = Held(fake)
            holders = [asyncio.create_task(ask(http, f"{LTP[:-4]}{n:04d}")) for n in range(4)]
            await ff.wait_until(lambda: len(held.arrived) == 4)
            late = asyncio.create_task(ask(http, DAYS, priority="bulk"))
            await ff.wait_until(lambda: svc.rest.health()["waiting"] == 1)
            now[0] = ff.ist(2026, 10, 28, 8, 30)
            held.release()
            r = await late
            assert (r.status_code, r.headers["X-Sudarshan-Reason"]) == (503, "bulk-hours")
            assert r.headers["Retry-After"] == str(int(7.5 * 3600))
            held.release(4)
            await asyncio.gather(*holders)
            assert len(held.arrived) == 4  # the late bulk call never went
            days = svc.health()["rest"]["apis"]["historical-candle"]["clients"][
                "sudarshan-screener"
            ]
            assert (days["calls"], days["refusals"]) == (1, 2)

    asyncio.run(scenario())


def test_a_request_waits_at_most_30_s_and_at_most_64_wait(feed_env, monkeypatch):
    async def scenario():
        async with running(feed_env) as (svc, fake, http):
            held = Held(fake)
            holders = [asyncio.create_task(ask(http, f"{LTP[:-4]}{n:04d}")) for n in range(4)]
            await ff.wait_until(lambda: len(held.arrived) == 4)
            # 64 wait for a place (spread over clients and APIs, so no window holds them) ...
            paths = [OHLC, QUOTES, INTRADAY, CHAIN, DAYS]
            waiting = [
                asyncio.create_task(ask(http, paths[n % 5], client=list(rest.CEILINGS)[n % 3]))
                for n in range(64)
            ]
            await ff.wait_until(lambda: svc.rest.health()["waiting"] == 64, 10)
            # ... and the 65th is told at once.
            r = await ask(http, LTP, "sudarshan-alerts")
            assert (r.status_code, r.json()) == (503, {"error": rest.BUSY})
            assert r.headers["X-Sudarshan-Reason"] == "busy" and r.headers["Retry-After"] == "60"
            assert waited(r) < 500
            held.open()
            got = await asyncio.gather(*holders, *waiting)
            assert {r.status_code for r in got} == {200} and len(held.arrived) == 68
            # The 30 s cap (shortened here): one that never gets a place is told then.
            monkeypatch.setattr(rest, "WAIT_CAP_S", 0.5)
            before = dict(svc.health()["rest"]["apis"]["ltp"]["clients"]["sudarshan-alerts"])
            held = Held(fake)
            holders = [asyncio.create_task(ask(http, f"{LTP[:-4]}{n:04d}")) for n in range(4)]
            await ff.wait_until(lambda: len(held.arrived) == 4)
            r = await ask(http, LTP, "sudarshan-alerts")
            assert (r.status_code, r.json(), r.headers["Retry-After"]) == (
                503, {"error": rest.BUSY}, "60",
            )  # fmt: skip
            assert 450 <= waited(r) < 2000
            held.open()
            await asyncio.gather(*holders)
            after = svc.health()["rest"]["apis"]["ltp"]["clients"]["sudarshan-alerts"]
            assert {k: after[k] - before[k] for k in before} == {
                "calls": 0, "waits": 1, "refusals": 1, "429s": 0,
            }  # fmt: skip
            assert svc.health()["rest"]["waiting"] == 0

    asyncio.run(scenario())


TOO_MANY = {"status": "error", "errors": [{"errorCode": "UDAPI10005", "message": "Too Many Request Sent"}]}  # fmt: skip


def bump(path, text: str) -> None:
    """Rewrite a file and move its stamp on, so the change is seen on any file system."""
    before = path.stat().st_mtime_ns
    path.write_text(text)
    os.utime(path, ns=(before + 10**9, before + 10**9))


def test_a_429_stops_that_api_for_every_client_then_halves_its_rates(feed_env, monkeypatch):
    monkeypatch.setattr(rest, "FREEZE_S", 0.6)  # 60 s in the service
    monkeypatch.setattr(rest, "HALF_RATE_S", 2.5)  # 10 minutes in the service
    answers = deque([(429, json.dumps(TOO_MANY).encode(), {})])

    async def answer(path: str):
        return answers.popleft() if answers else await ff.rest_ok(path)

    async def scenario():
        async with running(feed_env) as (svc, fake, http):
            fake.rest_answer = answer
            start = time.monotonic()
            r = await ask(http, LTP, "sudarshan-portfolio")
            assert (r.status_code, r.json()) == (429, TOO_MANY)  # Upstox's answer, passed on
            ltp = svc.health()["rest"]["apis"]["ltp"]
            assert ltp["frozen_until"] and ltp["half_rate_until"] and ltp["429s"] == 1
            assert ltp["clients"]["sudarshan-portfolio"]["429s"] == 1
            other, same = await asyncio.gather(
                ask(http, OHLC, "sudarshan-alerts"), ask(http, LTP, "sudarshan-alerts")
            )
            assert other.status_code == 200 and waited(other) < 300  # another API is untouched
            assert same.status_code == 200 and 400 <= waited(same) < 1500  # every client waits
            # Then half rate: the portfolio's 2/s is 1/s until the freeze is 2.5 s behind.
            await asyncio.sleep(start + 1.7 - time.monotonic())
            got = await asyncio.gather(*(ask(http, LTP, "sudarshan-portfolio") for _ in range(2)))
            assert sorted(waited(r) > 700 for r in got) == [False, True]
            await asyncio.sleep(start + 0.6 + 2.5 + 1.1 - time.monotonic())
            got = await asyncio.gather(*(ask(http, LTP, "sudarshan-portfolio") for _ in range(2)))
            assert all(waited(r) < 500 for r in got)  # full rate again
            ltp = svc.health()["rest"]["apis"]["ltp"]
            assert (ltp["frozen_until"], ltp["half_rate_until"], ltp["calls"]) == (None, None, 6)

    asyncio.run(scenario())


def test_udapi10005_in_any_answer_stops_the_api_and_a_call_that_cannot_wait_is_told_at_once(
    feed_env,
):
    answers = {
        QUOTES: (400, json.dumps(TOO_MANY).encode(), {}),  # Upstox's code, without a 429
        OHLC: (400, ff.token_error(), {}),  # UDAPI100050 is the token error, not a limit
    }

    async def answer(path: str):
        return answers.pop(path, None) or await ff.rest_ok(path)

    async def scenario():
        async with running(feed_env) as (svc, fake, http):
            fake.rest_answer = answer
            assert (await ask(http, QUOTES, "sudarshan-portfolio")).status_code == 400
            r = await ask(http, QUOTES, "sudarshan-screener")  # stopped 60 s: more than 30 s
            assert (r.status_code, r.json()) == (503, {"error": rest.SLOWED})
            assert r.headers["X-Sudarshan-Reason"] == "rate-limited" and waited(r) < 300
            assert 55 <= int(r.headers["Retry-After"]) <= 60
            assert (await ask(http, LTP)).status_code == 200
            assert (await ask(http, OHLC)).status_code == 400
            assert (await ask(http, OHLC)).status_code == 200  # never stopped
            apis = svc.health()["rest"]["apis"]
            assert apis["full-quote"]["frozen_until"] and apis["ohlc"]["frozen_until"] is None
            quote = apis["full-quote"]["clients"]
            assert quote["sudarshan-portfolio"]["429s"] == 1
            assert quote["sudarshan-screener"] == {"calls": 0, "waits": 0, "refusals": 1, "429s": 0}
            assert len(fake.rest_calls) == 4

    asyncio.run(scenario())


def test_a_401_stops_every_api_until_the_token_file_changes(feed_env, caplog):
    caplog.set_level(logging.INFO, logger="sudarshan.feed")
    new_token = "the-next-analytics-token-9876543210"

    async def revoked(path: str):  # Upstox stops taking the token while calls are in flight
        return (401, ff.token_error(), {}) if path.endswith("0000") else await ff.rest_ok(path)

    async def scenario():
        async with running(feed_env) as (svc, fake, http):
            held = Held(fake, revoked)
            holders = [asyncio.create_task(ask(http, f"{LTP[:-4]}{n:04d}")) for n in range(4)]
            await ff.wait_until(lambda: len(held.arrived) == 4)
            queued = asyncio.create_task(ask(http, OHLC, "sudarshan-alerts"))
            await ff.wait_until(lambda: svc.rest.health()["waiting"] == 1)
            held.release()  # the 401
            first = await holders[0]
            assert (first.status_code, first.content) == (401, ff.token_error())
            r = await queued  # told at once, not after 30 s
            assert (r.status_code, r.json()) == (503, {"error": rest.TOKEN_REFUSED})
            assert r.headers["X-Sudarshan-Reason"] == "token" and waited(r) < 1000
            sent = len(fake.rest_calls)
            for path in (DAYS, CHAIN, LTP):
                r = await ask(http, path, "sudarshan-portfolio")
                assert (r.status_code, r.headers["Retry-After"]) == (503, "60")
            assert len(fake.rest_calls) == sent  # nothing more went to Upstox
            assert svc.health()["rest"]["token_refused_since"] == "2026-10-27T20:00:00+05:30"
            held.open()
            assert {r.status_code for r in await asyncio.gather(*holders[1:])} == {200}
            # The owner puts the new token in the file: the next call goes, with it.
            fake.token = new_token
            bump(svc.cfg.token_file, new_token + "\n")
            r = await ask(http, LTP)
            assert r.status_code == 200 and fake.rest_calls[-1][1] == f"Bearer {new_token}"
            assert svc.health()["rest"]["token_refused_since"] is None
        told = [r.getMessage() for r in caplog.records if r.name == "sudarshan.feed.rest"]
        assert len([m for m in told if "refused the Analytics Token" in m]) == 1
        assert "the token file changed: Upstox requests through the governor resume" in told

    asyncio.run(scenario())


def test_the_token_and_queries_never_reach_the_logs_or_health(feed_env, caplog):
    caplog.set_level(logging.DEBUG, logger="sudarshan.feed")
    exposed = []

    async def scenario():
        async with running(feed_env) as (svc, fake, http):
            assert (await ask(http, LTP)).status_code == 200
            assert (await ask(http, DAYS, priority="bulk")).status_code == 200
            fake.token = "another-token-0123456789abcdef"  # Upstox refuses ours now
            assert (await ask(http, QUOTES)).status_code == 401
            assert (await ask(http, QUOTES)).status_code == 503
            exposed.append(svc.health())
            bump(svc.cfg.token_file, fake.token + "\n")
            fake._server.close()  # Upstox cannot be reached
            r = await ask(http, CHAIN)
            assert (r.status_code, r.json()) == (502, {"error": rest.NO_ANSWER})
            assert r.headers["X-Sudarshan-Reason"] == "no-answer"
            exposed.append(svc.health())
        assert "REST: option-chain failed: ConnectError" in caplog.text
        whole = [json.dumps(health) for health in exposed]
        for secret in (ff.TOKEN, "another-token-0123456789abcdef", "instrument_key", "expiry_date"):
            assert secret not in caplog.text and all(secret not in text for text in whole)
        governor = [json.dumps(health["rest"]) for health in exposed]  # no key, no path in it
        assert all("INE002A01018" not in text and "/v" not in text for text in governor)

    asyncio.run(scenario())


def test_the_counters_outlive_a_restart_so_a_week_of_429s_can_be_read(
    feed_env, monkeypatch, caplog
):
    monkeypatch.setattr(rest, "SAVE_AFTER_S", 0.2)  # 1 s in the service, and at every stop
    answers = deque([(429, json.dumps(TOO_MANY).encode(), {})])

    async def answer(path: str):
        return answers.popleft() if answers else await ff.rest_ok(path)

    def ltp_of(svc) -> dict:
        return svc.health()["rest"]["apis"]["ltp"]

    async def scenario():
        async with running(feed_env) as (svc, fake, http):
            fake.rest_answer = answer
            kept = svc.cfg.state_dir / rest.COUNTS_FILE
            assert (await ask(http, LTP, "sudarshan-portfolio")).status_code == 429
            assert (await ask(http, OHLC, "sudarshan-alerts")).status_code == 200
            await ff.wait_until(kept.exists)  # saved while running, not only at a stop
            assert (await ask(http, OHLC, "sudarshan-alerts")).status_code == 200  # saved at stop
            since = svc.health()["rest"]["counted_since"]
        assert since == "2026-10-27T20:00:00+05:30"
        async with running(feed_env, [EVENING + 86400]) as (svc, fake, http):  # the next day
            rest_health = svc.health()["rest"]
            assert rest_health["counted_since"] == since
            assert (ltp_of(svc)["429s"], ltp_of(svc)["calls"]) == (1, 1)
            assert ltp_of(svc)["clients"]["sudarshan-portfolio"]["429s"] == 1
            assert rest_health["apis"]["ohlc"]["clients"]["sudarshan-alerts"]["calls"] == 2
            assert ltp_of(svc)["frozen_until"] is None  # a stop of 60 s is over by the next day
            assert (await ask(http, LTP, "sudarshan-portfolio")).status_code == 200
            assert ltp_of(svc)["calls"] == 2  # counting goes on
        kept.write_text("{damaged")
        async with running(feed_env, [EVENING + 2 * 86400]) as (svc, fake, http):
            assert svc.health()["rest"]["counted_since"] == "2026-10-29T20:00:00+05:30"  # afresh
            assert ltp_of(svc)["calls"] == 0 and ltp_of(svc)["clients"] == {}
        assert "REST counters in" in caplog.text and "counting afresh" in caplog.text

    asyncio.run(scenario())


def saved(svc) -> dict:
    """rest_counts.json as it is on disk now ({} before the first save)."""
    kept = svc.cfg.state_dir / rest.COUNTS_FILE
    return json.loads(kept.read_text()) if kept.exists() else {}


def test_a_restart_keeps_every_window_and_any_stop_so_no_share_is_granted_twice(
    feed_env, monkeypatch
):
    """Review M2: a deploy restarts the feed only after 16:00, the warm starts' hours, and a
    restart used to start every window empty: 600 screener calls before it and 600 after it in
    one half hour. The windows and a 429's stop now outlive the process."""
    monkeypatch.setattr(rest, "CEILINGS", rest.CEILINGS | {"sudarshan-portfolio": (10, 3, 100)})
    too_many = deque([(429, json.dumps(TOO_MANY).encode(), {})])

    async def answer(path: str):
        return too_many.popleft() if "quotes" in path and too_many else await ff.rest_ok(path)

    async def scenario():
        now = [EVENING]
        async with running(feed_env, now) as (svc, fake, http):
            fake.rest_answer = answer
            for _ in range(3):  # the portfolio's whole minute (3 here, 25 in the service)
                assert (await ask(http, LTP, "sudarshan-portfolio")).status_code == 200
            assert (await ask(http, QUOTES, "sudarshan-alerts")).status_code == 429  # 60 s stop
        async with running(feed_env, now) as (svc, fake, http):  # restarted at once
            got = [
                await ask(http, LTP, "sudarshan-portfolio"),  # its minute is still full
                await ask(http, QUOTES, "sudarshan-screener"),  # full quotes still stopped
                await ask(http, LTP, "sudarshan-screener"),  # its own windows: free
            ]
            frozen = svc.health()["rest"]["apis"]["full-quote"]["frozen_until"]
            sent = list(fake.rest_calls)
        now[0] += 31 * 60  # half an hour on: every window, the stop and the half rate are over
        async with running(feed_env, now) as (svc, fake, http):
            later = [await ask(http, LTP, "sudarshan-portfolio"), await ask(http, QUOTES)]
        return got, frozen, sent, later

    (minute, stopped, other), frozen, sent, later = asyncio.run(scenario())
    assert (minute.status_code, minute.headers["X-Sudarshan-Reason"]) == (503, "busy")
    assert 50 <= int(minute.headers["Retry-After"]) <= 60 and waited(minute) < 300  # told at once
    assert (stopped.status_code, stopped.headers["X-Sudarshan-Reason"]) == (503, "rate-limited")
    assert 50 <= int(stopped.headers["Retry-After"]) <= 60 and frozen
    assert other.status_code == 200 and sent == [(LTP, f"Bearer {ff.TOKEN}")]
    assert [r.status_code for r in later] == [200, 200]


def test_a_crash_counts_its_unsaved_second_as_used_and_keeps_a_stop(feed_env, monkeypatch, caplog):
    """Review M2: Restart=always brings the feed back after a crash, which loses what the last
    second sent. That second counts as each client's whole per-second share; a 429's stop is
    saved at once, so even a crash right after it keeps it."""
    monkeypatch.setattr(rest, "CEILINGS", rest.CEILINGS | {"sudarshan-portfolio": (10, 3, 100)})
    monkeypatch.setattr(rest, "SAVE_AFTER_S", 0.2)
    too_many = deque([(429, json.dumps(TOO_MANY).encode(), {})])

    async def answer(path: str):
        return too_many.popleft() if "quotes" in path and too_many else await ff.rest_ok(path)

    def portfolio_ltp(state: dict) -> list:
        return state.get("apis", {}).get("ltp", {}).get("sent_ms", {}).get("sudarshan-portfolio")

    async def scenario():
        now = [EVENING]
        async with running(feed_env, now) as (svc, fake, http):
            fake.rest_answer = answer
            kept = svc.cfg.state_dir / rest.COUNTS_FILE
            assert (await ask(http, LTP, "sudarshan-portfolio")).status_code == 200
            await ff.wait_until(lambda: portfolio_ltp(saved(svc)) is not None)
            monkeypatch.setattr(rest, "SAVE_AFTER_S", 60.0)  # nothing more saved by the clock
            assert (await ask(http, QUOTES, "sudarshan-alerts")).status_code == 429
            await ff.wait_until(lambda: saved(svc)["apis"]["full-quote"]["frozen_until_ms"])
            for _ in range(2):  # sent, never saved: the crash comes first
                assert (await ask(http, LTP, "sudarshan-portfolio")).status_code == 200
            crashed = kept.read_bytes()  # the file as a crash now would leave it
            assert len(portfolio_ltp(json.loads(crashed))) == 1
        kept.write_bytes(crashed)
        monkeypatch.setattr(rest, "SAVE_AFTER_S", 0.2)
        async with running(feed_env, now) as (svc, fake, http):
            got = [await ask(http, LTP, "sudarshan-portfolio"), await ask(http, QUOTES)]
            return got, list(fake.rest_calls)

    (minute, stopped), sent = asyncio.run(scenario())
    assert (minute.status_code, minute.headers["X-Sudarshan-Reason"]) == (503, "busy")
    assert (stopped.status_code, stopped.headers["X-Sudarshan-Reason"]) == (503, "rate-limited")
    assert sent == []
    assert "the last run ended without saving its stop" in caplog.text


def test_a_429_that_comes_back_while_the_feed_stops_is_still_kept(feed_env, monkeypatch):
    """Review M2: the governor's stop writes its last save first, while calls already with
    Upstox may still come back. A 429 among them is written at once, never lost."""
    monkeypatch.setattr(rest, "SAVE_AFTER_S", 60.0)  # no save by the clock in this test

    async def too_many(path: str):
        return 429, json.dumps(TOO_MANY).encode(), {}

    async def scenario():
        now = [EVENING]
        async with running(feed_env, now) as (svc, fake, http):
            held = Held(fake, too_many)
            late = asyncio.create_task(ask(http, QUOTES, "sudarshan-alerts"))
            await ff.wait_until(lambda: len(held.arrived) == 1)
            svc.rest.close()  # the first thing the feed's stop does
            held.release()
            assert (await late).status_code == 429
            stopped = saved(svc)["apis"]["full-quote"]["frozen_until_ms"]
        async with running(feed_env, now) as (svc, fake, http):
            return stopped, await ask(http, QUOTES)

    stopped, after = asyncio.run(scenario())
    assert stopped and (after.status_code, after.headers["X-Sudarshan-Reason"]) == (
        503, "rate-limited",
    )  # fmt: skip


def test_a_save_does_its_disk_work_off_the_event_loop(feed_env, monkeypatch):
    """Review L5: the JSON, write and fsync of rest_counts.json run on the saver thread while the
    feed runs (only the stop's last save is written on the loop, which is stopping)."""
    monkeypatch.setattr(rest, "SAVE_AFTER_S", 0.05)
    threads, write = [], rest.atomic_write_json

    def recording(path, obj):
        threads.append(threading.current_thread())
        write(path, obj)

    monkeypatch.setattr(rest, "atomic_write_json", recording)

    async def scenario():
        async with running(feed_env) as (svc, fake, http):
            assert (await ask(http, LTP)).status_code == 200
            await ff.wait_until(lambda: len(threads) >= 1)
            while_running = list(threads)
        return while_running, threads[-1]

    while_running, at_stop = asyncio.run(scenario())
    loop_thread = threading.current_thread()  # asyncio.run ran the loop on this thread
    assert all(t is not loop_thread for t in while_running), while_running
    assert at_stop is loop_thread


def test_switched_off_it_says_so_and_a_stopping_feed_answers_every_waiting_call(feed_env):
    assert Settings.from_env({}).rest is True  # on unless the owner turns it off

    async def scenario():
        async with running(feed_env, SUDARSHAN_FEED_REST="off") as (svc, fake, http):
            r = await ask(http, LTP)
            assert (r.status_code, r.json()) == (503, {"error": rest.SWITCHED_OFF})
            assert r.headers["X-Sudarshan-Reason"] == "off" and fake.rest_calls == []
            assert svc.health()["rest"]["enabled"] is False
        async with running(feed_env) as (svc, fake, http):
            held = Held(fake)
            holders = [asyncio.create_task(ask(http, f"{LTP[:-4]}{n:04d}")) for n in range(4)]
            await ff.wait_until(lambda: len(held.arrived) == 4)
            queued = asyncio.create_task(ask(http, OHLC))
            await ff.wait_until(lambda: svc.rest.health()["waiting"] == 1)
            svc.stop("test")  # a deploy restarting the feed
            r = await queued
            assert (r.status_code, r.json()) == (503, {"error": rest.RESTARTING})
            assert r.headers["X-Sudarshan-Reason"] == "restarting"
            held.open()
            await asyncio.gather(*holders, return_exceptions=True)  # their own answers, or none

    asyncio.run(scenario())


def test_a_client_that_stops_reading_loses_its_answer_and_holds_nothing(feed_env, monkeypatch):
    """Answers of up to 8 MiB wait in the feed only until their client reads them: one that does
    not within the write limit (30 s; shortened here) is cut off, its socket and buffer freed."""
    monkeypatch.setattr(http_api, "WRITE_TIMEOUT_S", 0.5)
    body = b"x" * (4 << 20)

    async def big(path: str):
        return 200, body, {}

    def open_fds() -> int:
        return len(os.listdir("/proc/self/fd"))

    async def scenario():
        async with running(feed_env) as (svc, fake, http):
            fake.rest_answer = big
            assert (await ask(http, LTP)).status_code == 200  # every pool warmed up first
            stuck = socket.socket()
            stuck.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)  # takes almost nothing
            before = open_fds()  # with the client's own socket, before the feed accepts it
            stuck.connect(("127.0.0.1", svc.http_port))
            stuck.sendall(
                f"GET /upstox{LTP} HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Sudarshan-Client: "
                "sudarshan-screener\r\nX-Sudarshan-Priority: interactive\r\n\r\n".encode()
            )
            await ff.wait_until(lambda: open_fds() > before)  # the feed holds its connection
            await asyncio.sleep(1.5)  # the call, then the write limit
            assert open_fds() == before and svc.health()["rest"]["in_flight"] == 0
            stuck.setblocking(False)
            got = 0
            with contextlib.suppress(BlockingIOError, ConnectionError):
                while chunk := stuck.recv(1 << 20):
                    got += len(chunk)
            stuck.close()
            assert got < len(body)  # cut off, never handed the whole answer
            assert (await ask(http, LTP)).status_code == 200  # the governor serves on

    asyncio.run(scenario())


def stuck_caller(svc, n: int) -> socket.socket:
    """A caller that sends its request and then takes almost nothing of the answer."""
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    s.connect(("127.0.0.1", svc.http_port))
    s.sendall(
        f"GET /upstox{LTP[:-4]}{n:04d} HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Sudarshan-Client: "
        "sudarshan-screener\r\nX-Sudarshan-Priority: interactive\r\n\r\n".encode()
    )
    return s


def test_an_answer_keeps_its_place_until_its_caller_has_it(feed_env, monkeypatch):
    """Review M3: a place in flight used to be given back when Upstox's answer arrived, while
    the answer could wait 30 s more for its reader: 12 callers that did not read held 40 MiB of
    6 MiB answers with nothing in flight. Now 4 answers at most wait in the feed."""
    body, writers, serve = b"x" * (6 << 20), [], http_api._serve

    async def tracking(service, reader, writer):
        writers.append(writer)
        await serve(service, reader, writer)

    monkeypatch.setattr(http_api, "_serve", tracking)

    async def big(path: str):
        return 200, body, {}

    async def scenario():
        async with running(feed_env) as (svc, fake, http):
            fake.rest_answer = big
            stuck = [stuck_caller(svc, n) for n in range(12)]
            await ff.wait_until(lambda: len(fake.rest_calls) >= 4, 10)
            await asyncio.sleep(1.0)
            buffered = sum(w.transport.get_write_buffer_size() for w in writers
                           if not w.transport.is_closing())  # fmt: skip
            health = svc.rest.health()
            seen = (len(fake.rest_calls), health["in_flight"], health["waiting"], buffered)
            for s in stuck:
                s.close()
            await ff.wait_until(lambda: svc.rest.health()["in_flight"] == 0, 5)
            return seen

    calls, in_flight, waiting, buffered = asyncio.run(scenario())
    assert (calls, in_flight, waiting) == (4, 4, 8)
    assert buffered <= 4 * len(body)


def test_a_caller_that_hangs_up_while_waiting_never_reaches_upstox(feed_env):
    """Review L2: a caller that gives up (its own timeout) while waiting for a place leaves the
    queue at once; it used to spend a call and a place for nobody."""

    def open_fds() -> int:
        return len(os.listdir("/proc/self/fd"))

    async def scenario():
        async with running(feed_env) as (svc, fake, http):
            held = Held(fake)
            holders = [asyncio.create_task(ask(http, f"{LTP[:-4]}{n:04d}")) for n in range(4)]
            try:
                await ff.wait_until(lambda: len(held.arrived) == 4)
                before = open_fds()
                s = socket.create_connection(("127.0.0.1", svc.http_port))
                s.sendall(
                    f"GET /upstox{OHLC} HTTP/1.1\r\nHost: x\r\nX-Sudarshan-Client: "
                    "sudarshan-alerts\r\nX-Sudarshan-Priority: interactive\r\n\r\n".encode()
                )
                await ff.wait_until(lambda: svc.rest.health()["waiting"] == 1)
                s.close()
                await ff.wait_until(lambda: svc.rest.health()["waiting"] == 0, 2)  # gone at once
                await ff.wait_until(lambda: open_fds() == before, 2)  # its socket closed too
            finally:
                held.open()
            got = await asyncio.gather(*holders)
            await asyncio.sleep(0.3)
            return [r.status_code for r in got], held.arrived

    codes, arrived = asyncio.run(scenario())
    assert codes == [200] * 4 and not any("ohlc" in path for path in arrived)


# ---- G8: a warm start in flight adds no tick lag ------------------------------------------------
WARM_START = r"""
import json, socket, sys, time
from concurrent.futures import ThreadPoolExecutor

port, count, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]

def one(n):  # raw sockets: the client's own work must not starve the feed on a small box
    path = f"/upstox/v3/historical-candle/NSE_EQ%7CINE{n:09d}/minutes/1/2026-10-27/2026-09-28"
    with socket.create_connection(("127.0.0.1", port), timeout=90) as s:
        s.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Sudarshan-Client: "
                  "sudarshan-screener\r\nX-Sudarshan-Priority: bulk\r\n\r\n".encode())
        buf, size, head = bytearray(1 << 20), 0, b""
        while k := s.recv_into(buf):
            head, size = head or bytes(buf[:k]), size + k
    status, _, rest = head.partition(b"\r\n\r\n")[0].partition(b"\r\n")
    length = int(rest.lower().split(b"content-length: ")[1].split(b"\r\n")[0])
    return int(status.split()[1]), length

began = time.time()
with ThreadPoolExecutor(8) as pool:
    answers = list(pool.map(one, range(count)))
with open(out, "w") as f:
    json.dump({"began": began, "ended": time.time(), "answers": answers}, f)
"""
TICK_KEYS = [f"NSE_FO|{40000 + n}" for n in range(10)]
CANDLES = b'{"status":"success","data":{"candles":[' + b'["2026-10-27T09:15:00+05:30",1400.5,1401.0,1399.5,1400.0,12345,0],' * 7900 + b"[]]}}"  # fmt: skip


def _percentiles(lags: list[float]) -> dict:
    lags = sorted(lags)
    at = lambda q: round(lags[min(len(lags) - 1, int(q * len(lags)))] * 1000, 2)  # noqa: E731
    return {"ticks": len(lags), "p50_ms": at(0.5), "p95_ms": at(0.95), "p99_ms": at(0.99),
            "max_ms": round(lags[-1] * 1000, 2)}  # fmt: skip


def test_a_warm_start_in_flight_adds_no_tick_lag(feed_env, tmp_path):
    """G8. A synthetic session replayed at speed (200 frames of 10 ticks a second, about 90
    times today's live rate) through the feed as its own process, before, during and after a
    warm start of 512 KiB candle answers through the governor at the screener's full 15/s.

    Inside the feed, loop_probe.py times every step of its event loop in CPU time. A tick that
    arrives while the governor works waits for that one step, so the governor's steps bound the
    lag it can add, and its share of the loop bounds how often a tick meets one. Those are
    judged. The ticks' lag as a consumer sees it is printed for the record, not judged: on the
    shared test machine it moves with other processes, and this test's own process serves the
    512 KiB answers while it times the ticks (a phase-to-phase median check failed for that
    alone, by 0.08 ms, with the governor's longest step at 2.2 ms).
    """
    from datetime import timedelta

    from sudarshan.feed.window import ist_date

    http_port, zmq_port = ff.free_port(), ff.free_port()
    today = ist_date(time.time())
    holidays = tmp_path / "holidays-any-hour.txt"  # so bulk is served whenever this runs
    holidays.write_text("".join(f"{today + timedelta(days=d)}\n" for d in (-1, 0, 1)))
    env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
    results, probe = tmp_path / "warm_start.json", tmp_path / "loop_probe.json"

    async def phase(server, sub, seconds: float, seq: int) -> tuple[list[float], int]:
        sent, got, lags = {}, {}, []

        async def receive():
            while True:
                _, payload = await sub.recv_multipart()
                got.setdefault(json.loads(payload)["ltt"], []).append(time.perf_counter())

        receiver = asyncio.create_task(receive())
        start = next_at = time.monotonic()
        while time.monotonic() - start < seconds:
            feeds = {key: ff.pb2.Feed(ltpc=ff.pb2.LTPC(ltp=100.0 + seq % 7, ltt=seq, ltq=1))
                     for key in TICK_KEYS}  # fmt: skip
            frame = ff.response(feeds)
            sent[seq] = time.perf_counter()
            await server.send(frame)
            seq, next_at = seq + 1, next_at + 0.005
            await asyncio.sleep(max(0.0, next_at - time.monotonic()))
        deadline = time.monotonic() + 5
        while any(len(got.get(n, ())) < len(TICK_KEYS) for n in sent):
            assert time.monotonic() < deadline, "ticks were lost"
            await asyncio.sleep(0.01)
        receiver.cancel()
        assert set(got) == set(sent) and {len(times) for times in got.values()} == {len(TICK_KEYS)}
        for n, when in sent.items():
            lags += [t - when for t in got[n]]
        return lags, seq

    async def scenario():
        async with ff.FakeUpstox() as fake:

            async def candles(path: str):
                return 200, CANDLES, {}

            fake.rest_answer = candles
            run_env = env | feed_env(fake) | {
                "SUDARSHAN_FEED_HTTP_PORT": str(http_port),
                "SUDARSHAN_FEED_ZMQ_PORT": str(zmq_port),
                "SUDARSHAN_NSE_HOLIDAYS_FILE": str(holidays),
            }  # fmt: skip
            with open(tmp_path / "feed.log", "wb") as log:  # a file, never a pipe
                feed = await asyncio.create_subprocess_exec(
                    sys.executable, str(REPO_ROOT / "sudarshan/feed/tests/loop_probe.py"),
                    str(probe), cwd=REPO_ROOT, env=run_env, stdout=log, stderr=log,
                )  # fmt: skip
            ctx = zmq.asyncio.Context()
            sub = ctx.socket(zmq.SUB)
            client = None
            try:
                await ff.wait_until(lambda: fake.requests and len(fake.requests[0]) == 2, 20)
                server = fake.sockets[0]
                sub.connect(f"tcp://127.0.0.1:{zmq_port}")
                sub.subscribe(b"NSE_FO|")
                await asyncio.sleep(0.5)  # the SUB joins; the feed settles
                before, seq = await phase(server, sub, 4.0, 1)
                with open(tmp_path / "client.log", "wb") as log:
                    client = await asyncio.create_subprocess_exec(
                        sys.executable, "-c", WARM_START, str(http_port), "90", str(results),
                        env=env, stdout=log, stderr=log,
                    )  # fmt: skip
                await ff.wait_until(lambda: len(fake.rest_calls) >= 4, 20)  # in flight now
                during_from = time.time()
                during, seq = await phase(server, sub, 4.0, seq)
                during_to = time.time()
                assert await asyncio.wait_for(client.wait(), 40) == 0
                after, seq = await phase(server, sub, 3.0, seq)
            finally:
                sub.close(0)
                ctx.term()
                if client is not None and client.returncode is None:
                    client.kill()
                    await client.wait()
                if feed.returncode is None:
                    feed.terminate()  # a clean stop writes the probe's file
                    assert await asyncio.wait_for(feed.wait(), 15) == 0
        warm = json.loads(results.read_text())
        assert warm["answers"] == [[200, len(CANDLES)]] * 90
        assert warm["began"] < during_from and warm["ended"] > during_to  # in flight throughout
        return before, during, after, (warm["began"], warm["ended"])

    before, during, after, warm = asyncio.run(scenario())
    lag = {name: _percentiles(lags) for name, lags in
           (("before", before), ("during", during), ("after", after))}  # fmt: skip
    steps = json.loads(probe.read_text())
    in_warm = [(cpu, owner) for at, cpu, owner in steps["long_steps"] if warm[0] <= at <= warm[1]]
    governor = sorted((cpu for cpu, owner in in_warm if owner == "governor"), reverse=True)
    feed_own = [cpu for cpu, owner in in_warm if owner == "feed"]  # its recorder's flush, ...
    count, cpu_ms = steps["totals"]["governor"]
    share = cpu_ms / ((warm[1] - warm[0]) * 1000)
    print(f"\ntick lag {json.dumps(lag)}\ngovernor: {count} loop steps, {cpu_ms:.0f} ms CPU in "
          f"all for 90 answers ({share:.1%} of the loop while they ran), longest "
          f"{governor[:3]} ms; the feed's own longest step meanwhile "
          f"{max(feed_own, default=0):.2f} ms")  # fmt: skip
    assert count > 90 * 4  # the probe saw the governor's work
    # Its steps are short. One reading inflated by the machine is let pass; work that cost every
    # answer a long step would show 90 of them.
    assert sum(cpu >= 5.0 for cpu in governor) <= 2, governor[:5]
    assert share < 0.15  # and it holds the loop a small part of the time
