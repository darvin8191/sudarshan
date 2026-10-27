"""Test helpers: FeedResponse builders and a fake Upstox (authorize endpoint + feed socket)."""

from __future__ import annotations

import asyncio
import json
import socket
import time
from datetime import datetime

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from sudarshan.feed.protocol import pb2
from sudarshan.feed.window import IST

TOKEN = "test-analytics-token-0123456789abcdef"  # never a real token
CODE = "one-time-code-42"  # stands in for the credential in the authorized URI
LTT = 1_761_022_800_123


def ist(y: int, mo: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> float:
    return datetime(y, mo, d, h, mi, s, tzinfo=IST).timestamp()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


def response(
    feeds: dict, kind: str = "live_feed", segments: dict | None = None, cas: dict | None = None
) -> bytes:
    """``cas``: {segment: (closing-auction phase, epoch ms it began)} in the market info."""
    resp = pb2.FeedResponse(type=pb2.Type.Value(kind), currentTs=LTT)
    for key, feed in feeds.items():
        resp.feeds[key].CopyFrom(feed)
    for segment, status in (segments or {}).items():
        resp.marketInfo.segmentStatus[segment] = pb2.MarketStatus.Value(status)
    for segment, (phase, since_ms) in (cas or {}).items():
        resp.marketInfo.casMarketStatus[segment].CopyFrom(
            pb2.StatusInfo(status=phase, updatedTime=since_ms)
        )
    return resp.SerializeToString()


def ltpc(ltp: float, ltq: int = 0, cp: float = 0.0):
    return pb2.Feed(ltpc=pb2.LTPC(ltp=ltp, ltt=LTT, ltq=ltq, cp=cp))


def ohlc_1d(close: float):
    return pb2.MarketOHLC(ohlc=[
        pb2.OHLC(interval="I1", open=1, high=1, low=1, close=1, vol=1, ts=1),
        pb2.OHLC(interval="1d", open=1390, high=1410, low=1385, close=close, vol=123456, ts=LTT),
    ])  # fmt: skip


def full_stock(ltp: float = 1400.5, with_ltpc: bool = True):
    # Depth deliberately worst level first: the best bid/ask must not depend on list order.
    levels = [
        pb2.Quote(bidQ=100 + i, bidP=1400.4 - i, askQ=50 + i, askP=1400.6 + i) for i in range(5)
    ]
    ff = pb2.MarketFullFeed(
        marketLevel=pb2.MarketLevel(bidAskQuote=levels[::-1]), marketOHLC=ohlc_1d(ltp),
        atp=1398.25, vtt=4_567_890, tbq=12345.0, tsq=23456.0,
    )  # fmt: skip
    if with_ltpc:
        ff.ltpc.CopyFrom(pb2.LTPC(ltp=ltp, ltt=LTT, ltq=25, cp=1388.0))
    return pb2.Feed(
        fullFeed=pb2.FullFeed(marketFF=ff), requestMode=pb2.RequestMode.Value("full_d5")
    )


class FakeUpstox:
    """One local websockets server playing both Upstox endpoints.

    GET /v3/feed/market-data-feed/authorize answers like Upstox (401 unless the bearer token
    matches); /feed is the market-data socket, refused with ``upgrade_status`` when set.
    """

    def __init__(self, token: str = TOKEN):
        self.token = token
        self.upgrade_status: int | None = None
        self.authorize_calls = 0
        self.sockets: list = []  # server side of each accepted socket
        self.requests: list[list[dict]] = []  # JSON requests received, per socket
        self.close_codes: list = []  # close code the server saw, per socket

    async def __aenter__(self) -> FakeUpstox:
        self._server = await serve(self._handler, "127.0.0.1", 0, process_request=self._process)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        self._server.close()
        await self._server.wait_closed()

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _process(self, connection, request):
        if request.path.startswith("/v3/feed/market-data-feed/authorize"):
            self.authorize_calls += 1
            if request.headers.get("Authorization") != f"Bearer {self.token}":
                error = {"errorCode": "UDAPI100050", "message": "Invalid token used to access API"}
                return connection.respond(401, json.dumps({"status": "error", "errors": [error]}))
            uri = f"ws://127.0.0.1:{self.port}/feed?requestId=r{self.authorize_calls}&code={CODE}"
            return connection.respond(200, json.dumps({"data": {"authorized_redirect_uri": uri}}))
        if self.upgrade_status:
            return connection.respond(self.upgrade_status, "Forbidden")
        return None  # go on with the WebSocket handshake

    async def _handler(self, ws) -> None:
        received: list[dict] = []
        self.sockets.append(ws)
        self.requests.append(received)
        try:
            async for message in ws:
                received.append(json.loads(message) | {"binary": isinstance(message, bytes)})
        except ConnectionClosed:
            pass
        self.close_codes.append(ws.close_code)
