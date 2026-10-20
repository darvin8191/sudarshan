"""Golden vectors for the Upstox stream adapter's normalized payloads (SUDARSHAN M11.3).

Each frame is a real `FeedResponse` built with the generated protobuf module, serialized, and
fed through `UpstoxWebSocketClient._process_binary_message`, so the bytes take the production
path: protobuf decode, `MessageToDict` with its default options (int64 as strings, zero scalars
left out), then the adapter. Payloads are compared as canonical JSON, which also checks every
type (an int is not a float) and that nothing publishes NaN or Infinity.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import defaultdict
from typing import Any

import pytest

# The proxy package imports every broker adapter when it loads, so it has to load before any
# adapter module does, or the adapter's own import of it is circular.
import websocket_proxy  # noqa: F401
from websocket_proxy import broker_factory, connection_manager
from websocket_proxy import server as proxy_server
from websocket_proxy.base_adapter import BaseBrokerWebSocketAdapter
from websocket_proxy.connection_manager import ConnectionPool
from websocket_proxy.server import WebSocketProxy

# isort: split
from broker.upstox.streaming import MarketDataFeedV3_pb2 as pb
from broker.upstox.streaming import upstox_adapter
from broker.upstox.streaming.upstox_adapter import UpstoxWebSocketAdapter
from broker.upstox.streaming.upstox_client import UpstoxWebSocketClient

TS = 1792481400123  # the frame's currentTs: 20 Oct 2026 13:00:00.123 IST
LTT = 1792481399000  # last trade, one second earlier
DAY_START = 1792434600000  # the 1d candle's ts: 00:00 IST, the start of the day
MINUTE = 1792481340000  # the I1 candle's ts: 12:59 IST

NIFTY = ("NIFTY", "NSE_INDEX", "NSE_INDEX|Nifty 50")
BANKNIFTY = ("BANKNIFTY", "NSE_INDEX", "NSE_INDEX|Nifty Bank")
RELIANCE = ("RELIANCE", "NSE", "NSE_EQ|INE002A01018")
SBIN = ("SBIN", "NSE", "NSE_EQ|INE062A01020")
OPTION = ("NIFTY27OCT2625100CE", "NFO", "NSE_FO|45450")

RELIANCE_BOOK = [
    (50, 1423.9, 47, 1424.1),
    (35, 1423.5, 39, 1424.5),
    (42, 1423.0, 41, 1425.0),
    (28, 1422.5, 32, 1425.5),
    (33, 1422.0, 30, 1426.0),
]
OPTION_BOOK = [
    (750, 101.45, 1500, 101.55),
    (1125, 101.4, 975, 101.6),
    (600, 101.35, 450, 101.65),
    (300, 101.3, 1200, 101.7),
    (75, 101.25, 225, 101.75),
]


# --------------------------------------------------------------------------------------------
# Frames
# --------------------------------------------------------------------------------------------


def _response() -> pb.FeedResponse:
    response = pb.FeedResponse()
    response.type = pb.live_feed
    response.currentTs = TS
    return response


def _ltpc(message, ltp: float, cp: float, ltq: int = 0) -> None:
    message.ltp = ltp
    message.ltt = LTT
    message.ltq = ltq
    message.cp = cp


def _candle(ohlc, interval: str, o: float, h: float, low: float, c: float, vol: int, ts: int):
    bar = ohlc.ohlc.add()
    bar.interval = interval
    bar.open, bar.high, bar.low, bar.close = o, h, low, c
    bar.vol = vol
    bar.ts = ts


def _book(level, rows) -> None:
    for bid_q, bid_p, ask_q, ask_p in rows:
        quote = level.bidAskQuote.add()
        quote.bidQ, quote.bidP, quote.askQ, quote.askP = bid_q, bid_p, ask_q, ask_p


def index_ltpc_frame() -> pb.FeedResponse:
    response = _response()
    _ltpc(response.feeds[NIFTY[2]].ltpc, 25100.5, 25000.0)
    return response


def index_full_frame() -> pb.FeedResponse:
    response = _response()
    index = response.feeds[BANKNIFTY[2]].fullFeed.indexFF
    _ltpc(index.ltpc, 56012.35, 55800.0)
    _candle(index.marketOHLC, "1d", 55900.0, 56100.0, 55850.0, 56012.35, 0, DAY_START)
    _candle(index.marketOHLC, "I1", 56005.0, 56015.0, 56001.1, 56012.35, 0, MINUTE)
    return response


def equity_full_frame() -> pb.FeedResponse:
    response = _response()
    market = response.feeds[RELIANCE[2]].fullFeed.marketFF
    _ltpc(market.ltpc, 1424.0, 1418.0, ltq=50)
    _book(market.marketLevel, RELIANCE_BOOK)
    # vtt deliberately differs from the 1d candle's vol, to show which one `volume` is.
    _candle(market.marketOHLC, "1d", 1415.0, 1432.5, 1408.0, 1424.0, 100000, DAY_START)
    _candle(market.marketOHLC, "I1", 1423.5, 1424.5, 1423.0, 1424.0, 900, MINUTE)
    market.atp = 1419.35
    market.vtt = 100250
    market.tbq = 250000.0
    market.tsq = 310000.0
    return response


def option_full_frame() -> pb.FeedResponse:
    response = _response()
    market = response.feeds[OPTION[2]].fullFeed.marketFF
    _ltpc(market.ltpc, 101.5, 95.25, ltq=75)
    _book(market.marketLevel, OPTION_BOOK)
    greeks = market.optionGreeks
    greeks.delta, greeks.theta, greeks.gamma, greeks.vega = 0.5213, -12.3, 0.0011, 9.8
    greeks.rho = 0.0  # proto3 leaves a zero off the wire
    _candle(market.marketOHLC, "1d", 90.0, 110.0, 88.0, 101.5, 123450, DAY_START)
    _candle(market.marketOHLC, "I1", 101.0, 102.0, 100.5, 101.5, 900, MINUTE)
    market.atp = 99.87
    market.vtt = 123450
    market.oi = 4567800.0
    market.iv = 0.1432
    market.tbq = 50025.0
    market.tsq = 60075.0
    return response


def ltpc_only_frame() -> pb.FeedResponse:
    response = _response()
    _ltpc(response.feeds[SBIN[2]].ltpc, 812.4, 805.0, ltq=10)
    return response


# --------------------------------------------------------------------------------------------
# Expected payloads
# --------------------------------------------------------------------------------------------


def _base(instrument) -> dict[str, Any]:
    symbol, exchange, key = instrument
    return {"symbol": symbol, "exchange": exchange, "token": key}


def _ltp(instrument, ltp: float, cp: float, ltq: int = 0) -> dict[str, Any]:
    return _base(instrument) | {
        "ltp": ltp,
        "ltq": ltq,
        "ltt": LTT,
        "cp": cp,
        "prev_close": cp,
        "last_trade_time": LTT,
        "timestamp": TS,
    }


def _levels(rows) -> dict[str, list[dict[str, Any]]]:
    return {
        "buy": [{"price": bp, "quantity": bq, "orders": 0} for bq, bp, _aq, _ap in rows],
        "sell": [{"price": ap, "quantity": aq, "orders": 0} for _bq, _bp, aq, ap in rows],
    }


INDEX_LTP = _ltp(NIFTY, 25100.5, 25000.0)
BANKNIFTY_LTP = _ltp(BANKNIFTY, 56012.35, 55800.0)
BANKNIFTY_QUOTE = _base(BANKNIFTY) | {
    "open": 55900.0,
    "high": 56100.0,
    "low": 55850.0,
    "close": 55800.0,  # the previous close, not the 1d candle's running close
    "ltp": 56012.35,
    "last_trade_quantity": 0,
    "volume": 0,
    # An index has no average price or book totals: left out, not sent as 0.
    "prev_close": 55800.0,
    "last_trade_time": LTT,
    "timestamp": TS,
}
RELIANCE_LTP = _ltp(RELIANCE, 1424.0, 1418.0, ltq=50)
RELIANCE_QUOTE = _base(RELIANCE) | {
    "open": 1415.0,
    "high": 1432.5,
    "low": 1408.0,
    "close": 1418.0,
    "ltp": 1424.0,
    "last_trade_quantity": 50,
    "volume": 100250,  # vtt, not the 1d candle's 100000
    "average_price": 1419.35,
    "total_buy_quantity": 250000,
    "total_sell_quantity": 310000,
    "prev_close": 1418.0,
    "last_trade_time": LTT,
    "timestamp": TS,
}
RELIANCE_DEPTH = RELIANCE_QUOTE | {"depth": _levels(RELIANCE_BOOK) | {"timestamp": TS}}
OPTION_QUOTE = _base(OPTION) | {
    "open": 90.0,
    "high": 110.0,
    "low": 88.0,
    "close": 95.25,
    "ltp": 101.5,
    "last_trade_quantity": 75,
    "volume": 123450,
    "average_price": 99.87,
    "total_buy_quantity": 50025,
    "total_sell_quantity": 60075,
    "prev_close": 95.25,
    "last_trade_time": LTT,
    "oi": 4567800,
    "iv": 0.1432,
    "greeks": {"delta": 0.5213, "gamma": 0.0011, "theta": -12.3, "vega": 9.8, "rho": 0.0},
    "timestamp": TS,
}
OPTION_DEPTH = OPTION_QUOTE | {"depth": _levels(OPTION_BOOK) | {"timestamp": TS}}
SBIN_LTP = _ltp(SBIN, 812.4, 805.0, ltq=10)
SBIN_FROM_LTPC = _base(SBIN) | {
    "ltp": 812.4,
    "ltq": 10,
    "ltt": LTT,
    "cp": 805.0,
    "last_trade_quantity": 10,
    "prev_close": 805.0,
    "last_trade_time": LTT,
    "close": 805.0,
    "timestamp": TS,
}


# --------------------------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------------------------


class _NoZmq:
    """Stands in for the shared publisher, so building an adapter opens no ZeroMQ socket."""

    socket = None
    zmq_port = 0
    context = None


class _UpstoxServer:
    """Upstox's end of one market-data socket, written to by the real client.

    Applies each request as Upstox does: `sub` streams its keys in its mode (a key
    already streamed moves to it), `change_mode` moves keys already streamed, `unsub`
    stops them. `held` is what Upstox is sending: instrument key -> mode.
    """

    def __init__(self) -> None:
        self.held: dict[str, str] = {}
        self.sent: list[tuple[str, list[str], str | None]] = []

    def send(self, data: bytes, opcode: int | None = None) -> None:
        request = json.loads(data)
        method, keys = request["method"], request["data"]["instrumentKeys"]
        mode = request["data"].get("mode")
        self.sent.append((method, list(keys), mode))
        for key in keys:
            if method == "sub" or (method == "change_mode" and key in self.held):
                self.held[key] = mode
            elif method == "unsub":
                self.held.pop(key, None)

    @property
    def unsubscribed(self) -> list[list[str]]:
        return [keys for method, keys, _mode in self.sent if method == "unsub"]

    def close(self) -> None:
        pass


class _SocketThread:
    """Stands in for the client's socket thread: alive until a test ends it."""

    def __init__(self) -> None:
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive


def _connected_client() -> UpstoxWebSocketClient:
    """The production client, its socket replaced by _UpstoxServer."""
    client = UpstoxWebSocketClient("not-a-real-token")
    client.ws = _UpstoxServer()
    client._connected = client.running = True
    client._ws_thread = _SocketThread()
    return client


def _canon(payload: dict[str, Any]) -> str:
    """Canonical JSON: key order ignored, types and values exact, NaN refused."""
    return json.dumps(payload, sort_keys=True, allow_nan=False)


@pytest.fixture
def make_adapter(monkeypatch):
    """Build real UpstoxWebSocketAdapters: no ZeroMQ, no Upstox, no database."""
    instances = BaseBrokerWebSocketAdapter._instance_count
    monkeypatch.setattr(
        upstox_adapter.SymbolMapper,
        "get_token_from_symbol",
        staticmethod(
            lambda symbol, exchange: {
                "token": _KEYS[(symbol, exchange)],
                "brexchange": _KEYS[(symbol, exchange)].split("|")[0],
            }
        ),
    )

    def build() -> UpstoxWebSocketAdapter:
        context = connection_manager._pooled_creation_context
        context.active, context.shared_publisher = True, _NoZmq()
        try:
            adapter = UpstoxWebSocketAdapter()
        finally:
            context.active, context.shared_publisher = False, None
        adapter.published = []
        adapter.publish_market_data = lambda topic, data: adapter.published.append((topic, data))
        # The production client decodes the frames, and another one, its socket
        # replaced by _UpstoxServer (adapter.upstox), sends the requests.
        adapter.decoder = UpstoxWebSocketClient("not-a-real-token")
        adapter.decoder.callbacks["on_message"] = adapter._on_market_data
        adapter.ws_client = _connected_client()
        adapter.upstox = adapter.ws_client.ws
        # Send batched subscribes at once instead of on a 0.5 s timer thread.
        adapter._start_batch_timer = adapter._process_batch_subscriptions
        adapter.connected = True
        return adapter

    yield build
    BaseBrokerWebSocketAdapter._instance_count = instances


_KEYS = {
    (symbol, exchange): key for symbol, exchange, key in (NIFTY, BANKNIFTY, RELIANCE, SBIN, OPTION)
}


def _hold(adapter: UpstoxWebSocketAdapter, instrument, mode: int) -> None:
    symbol, exchange, _key = instrument
    assert adapter.subscribe(symbol, exchange, mode)["status"] == "success"


def _feed(adapter: UpstoxWebSocketAdapter, frame: pb.FeedResponse) -> dict[str, str]:
    """Send one frame through the client's decoder; return {topic: canonical payload}."""
    adapter.published.clear()
    adapter.decoder._process_binary_message(frame.SerializeToString())
    topics = [topic for topic, _payload in adapter.published]
    assert len(topics) == len(set(topics)), f"a topic was published twice: {topics}"
    return {topic: _canon(payload) for topic, payload in adapter.published}


# --------------------------------------------------------------------------------------------
# Golden vectors
# --------------------------------------------------------------------------------------------


def test_index_ltpc_tick_publishes_prev_close_and_trade_time(make_adapter) -> None:
    adapter = make_adapter()
    _hold(adapter, NIFTY, 1)

    assert _feed(adapter, index_ltpc_frame()) == {"NSE_INDEX_NIFTY_LTP": _canon(INDEX_LTP)}


def test_index_full_feed_ltp_and_quote(make_adapter) -> None:
    adapter = make_adapter()
    _hold(adapter, BANKNIFTY, 1)
    _hold(adapter, BANKNIFTY, 2)

    assert _feed(adapter, index_full_frame()) == {
        "NSE_INDEX_BANKNIFTY_LTP": _canon(BANKNIFTY_LTP),
        "NSE_INDEX_BANKNIFTY_QUOTE": _canon(BANKNIFTY_QUOTE),
    }


def test_equity_full_feed_ltp_quote_and_depth(make_adapter) -> None:
    adapter = make_adapter()
    for mode in (1, 2, 3):
        _hold(adapter, RELIANCE, mode)

    assert _feed(adapter, equity_full_frame()) == {
        "NSE_RELIANCE_LTP": _canon(RELIANCE_LTP),
        "NSE_RELIANCE_QUOTE": _canon(RELIANCE_QUOTE),
        "NSE_RELIANCE_DEPTH": _canon(RELIANCE_DEPTH),
    }


def test_option_full_feed_quote_and_depth_carry_oi_iv_and_greeks(make_adapter) -> None:
    adapter = make_adapter()
    _hold(adapter, OPTION, 2)
    _hold(adapter, OPTION, 3)

    assert _feed(adapter, option_full_frame()) == {
        "NFO_NIFTY27OCT2625100CE_QUOTE": _canon(OPTION_QUOTE),
        "NFO_NIFTY27OCT2625100CE_DEPTH": _canon(OPTION_DEPTH),
    }


def test_ltpc_only_tick(make_adapter) -> None:
    adapter = make_adapter()
    _hold(adapter, SBIN, 1)

    assert _feed(adapter, ltpc_only_frame()) == {"NSE_SBIN_LTP": _canon(SBIN_LTP)}


def test_ltpc_only_tick_to_quote_and_depth_publishes_what_the_ltpc_knows(make_adapter) -> None:
    """No OHLC, volume or book keys, not even an empty `depth`: a merging consumer keeps
    its last values instead of zeros and an empty book."""
    adapter = make_adapter()
    _hold(adapter, SBIN, 2)
    _hold(adapter, SBIN, 3)

    assert _feed(adapter, ltpc_only_frame()) == {
        "NSE_SBIN_QUOTE": _canon(SBIN_FROM_LTPC),
        "NSE_SBIN_DEPTH": _canon(SBIN_FROM_LTPC),
    }


def test_depth_is_a_superset_of_quote(make_adapter) -> None:
    adapter = make_adapter()
    _hold(adapter, OPTION, 2)
    _hold(adapter, OPTION, 3)
    _feed(adapter, option_full_frame())
    payloads = dict(adapter.published)

    quote = payloads["NFO_NIFTY27OCT2625100CE_QUOTE"]
    depth = payloads["NFO_NIFTY27OCT2625100CE_DEPTH"]
    assert {field: depth[field] for field in quote} == quote
    assert set(depth) - set(quote) == {"depth"}


def test_close_is_the_previous_close_and_zero_when_upstox_sent_none(make_adapter) -> None:
    adapter = make_adapter()
    _hold(adapter, RELIANCE, 2)
    frame = equity_full_frame()
    frame.feeds[RELIANCE[2]].fullFeed.marketFF.ltpc.cp = 0.0  # a contract listed today

    _feed(adapter, frame)
    ((_topic, quote),) = adapter.published

    assert quote["close"] == 0.0
    assert "prev_close" not in quote
    assert quote["open"] == 1415.0  # the day's candle is untouched


def test_a_book_only_frame_carries_the_last_trade_fields_and_day_candle(make_adapter) -> None:
    """A packet without ltpc or marketOHLC must not publish an ltp, open or close of 0. It
    reports no trade, so its traded quantity is 0, not the last trade's again."""
    adapter = make_adapter()
    _hold(adapter, RELIANCE, 3)
    _feed(adapter, equity_full_frame())

    book_only = _response()
    market = book_only.feeds[RELIANCE[2]].fullFeed.marketFF
    moved = [(bq + 5, bp, aq, ap) for bq, bp, aq, ap in RELIANCE_BOOK]
    _book(market.marketLevel, moved)
    market.atp, market.vtt, market.tbq, market.tsq = 1419.35, 100250, 250000.0, 310000.0

    assert _feed(adapter, book_only) == {
        "NSE_RELIANCE_DEPTH": _canon(
            RELIANCE_QUOTE | {"last_trade_quantity": 0, "depth": _levels(moved) | {"timestamp": TS}}
        )
    }


def test_greeks_or_iv_upstox_could_not_compute_never_publish_nan(make_adapter) -> None:
    adapter = make_adapter()
    _hold(adapter, OPTION, 2)
    frame = option_full_frame()
    market = frame.feeds[OPTION[2]].fullFeed.marketFF
    market.optionGreeks.delta = float("nan")
    market.optionGreeks.vega = float("inf")
    market.iv = float("nan")

    topics = _feed(adapter, frame)  # _canon refuses NaN and Infinity
    quote = json.loads(topics["NFO_NIFTY27OCT2625100CE_QUOTE"])

    assert quote["greeks"] == {
        "delta": None,
        "gamma": 0.0011,
        "theta": -12.3,
        "vega": None,
        "rho": 0.0,
    }
    assert "iv" not in quote


def test_an_equity_tick_has_no_oi_iv_or_greeks(make_adapter) -> None:
    adapter = make_adapter()
    _hold(adapter, RELIANCE, 2)
    _feed(adapter, equity_full_frame())
    ((_topic, quote),) = adapter.published

    assert not {"oi", "iv", "greeks"} & set(quote)


def _book_only(instrument, rows) -> pb.FeedResponse:
    """A full-feed frame that only moves the book: no LTPC, no candle, no volume."""
    response = _response()
    _book(response.feeds[instrument[2]].fullFeed.marketFF.marketLevel, rows)
    return response


def test_a_frame_without_a_trade_publishes_no_traded_quantity(make_adapter) -> None:
    """The last trade's price and time are carried to a book-only frame; its quantity is
    not, or a consumer adding up traded quantity counts that trade again on every frame."""
    adapter = make_adapter()
    _hold(adapter, OPTION, 1)
    _hold(adapter, OPTION, 2)  # the key is streamed in `full`
    _feed(adapter, option_full_frame())

    _feed(adapter, _book_only(OPTION, OPTION_BOOK))
    payloads = dict(adapter.published)
    ltp = payloads["NFO_NIFTY27OCT2625100CE_LTP"]
    quote = payloads["NFO_NIFTY27OCT2625100CE_QUOTE"]

    assert (ltp["ltp"], ltp["ltt"], ltp["last_trade_time"], ltp["cp"]) == (101.5, LTT, LTT, 95.25)
    assert ltp["ltq"] == 0
    assert (quote["ltp"], quote["last_trade_quantity"]) == (101.5, 0)

    _feed(adapter, option_full_frame())  # the next trade reports its own quantity
    assert dict(adapter.published)["NFO_NIFTY27OCT2625100CE_LTP"]["ltq"] == 75


def test_values_a_frame_does_not_carry_are_left_out_or_carried(make_adapter) -> None:
    """Average price and the book totals are left out, not sent as 0, so a merging consumer
    keeps its last values. Volume and the day's candle are carried from the last frame."""
    adapter = make_adapter()
    _hold(adapter, OPTION, 2)
    _feed(adapter, option_full_frame())

    _feed(adapter, _book_only(OPTION, OPTION_BOOK))
    ((_topic, quote),) = adapter.published

    assert not {"average_price", "total_buy_quantity", "total_sell_quantity"} & set(quote)
    assert quote["volume"] == 123450
    assert (quote["open"], quote["high"], quote["low"]) == (90.0, 110.0, 88.0)


def test_volume_never_falls_back_below_the_last_vtt(make_adapter) -> None:
    """A frame with the day candle but no vtt keeps the last vtt. The candle's own volume,
    which Upstox counts differently, used to take over and step volume backwards."""
    adapter = make_adapter()
    _hold(adapter, RELIANCE, 2)
    _feed(adapter, equity_full_frame())  # vtt 100250; the 1d candle says 100000

    later = _response()
    market = later.feeds[RELIANCE[2]].fullFeed.marketFF
    _ltpc(market.ltpc, 1424.5, 1418.0, ltq=25)
    _candle(market.marketOHLC, "1d", 1415.0, 1432.5, 1408.0, 1424.5, 100000, DAY_START)
    _feed(adapter, later)
    ((_topic, quote),) = adapter.published

    assert quote["volume"] == 100250


def test_only_the_day_candle_gives_open_high_and_low(make_adapter) -> None:
    """A frame whose marketOHLC holds only the minute candle keeps the day's values. The
    minute bar's high and low are never shown, or carried, as the day's."""
    adapter = make_adapter()
    _hold(adapter, OPTION, 2)
    minute_only = _response()
    market = minute_only.feeds[OPTION[2]].fullFeed.marketFF
    _ltpc(market.ltpc, 101.6, 95.25, ltq=75)
    _candle(market.marketOHLC, "I1", 101.0, 101.7, 100.9, 101.6, 50, MINUTE)

    _feed(adapter, minute_only)  # nothing is known about the day yet
    ((_topic, first),) = adapter.published
    assert (first["open"], first["high"], first["low"], first["volume"]) == (0.0, 0.0, 0.0, 0)

    _feed(adapter, option_full_frame())  # the day candle arrives
    _feed(adapter, minute_only)
    ((_topic, later),) = adapter.published
    assert (later["open"], later["high"], later["low"]) == (90.0, 110.0, 88.0)


def test_no_published_number_is_nan_or_infinity(make_adapter) -> None:
    """A price, average price, book level or auction price Upstox could not compute is sent
    as 0, left out or skipped. JSON has no NaN, and a browser drops the whole tick."""
    adapter = make_adapter()
    _hold(adapter, RELIANCE, 1)
    _hold(adapter, RELIANCE, 3)
    frame = equity_full_frame()
    market = frame.feeds[RELIANCE[2]].fullFeed.marketFF
    market.ltpc.ltp = float("inf")
    market.ltpc.iep.value = float("nan")
    market.atp = float("nan")
    market.rp = float("inf")
    market.marketOHLC.ohlc[0].high = float("nan")  # the 1d candle
    market.marketLevel.bidAskQuote[0].bidP = float("inf")

    topics = _feed(adapter, frame)  # _canon refuses NaN and Infinity
    ltp = json.loads(topics["NSE_RELIANCE_LTP"])
    depth = json.loads(topics["NSE_RELIANCE_DEPTH"])

    assert ltp["ltp"] == 0.0 and depth["ltp"] == 0.0
    assert "indicative_equilibrium_price" not in ltp | depth
    assert not {"average_price", "reference_price"} & set(depth)
    assert (depth["open"], depth["high"]) == (1415.0, 0.0)
    assert [level["price"] for level in depth["depth"]["buy"]] == [
        1423.5,
        1423.0,
        1422.5,
        1422.0,
        0.0,
    ]


# --------------------------------------------------------------------------------------------
# Mode coverage through the proxy's connection pool
# --------------------------------------------------------------------------------------------


def _pool(adapter: UpstoxWebSocketAdapter) -> ConnectionPool:
    """A ConnectionPool around one adapter, built without a ZeroMQ publisher."""
    pool = ConnectionPool.__new__(ConnectionPool)
    pool.lock = threading.RLock()
    pool.logger = logging.getLogger("test.sudarshan.pool")
    pool.broker_name, pool.user_id = "upstox", "owner"
    pool.adapters, pool.adapter_symbol_counts = [adapter], [0]
    pool.max_symbols, pool.max_connections = 1000, 2
    pool.subscription_map, pool.subscription_depths = {}, {}
    pool.peak_total_symbols, pool.peak_connections_used, pool.peak_symbol_counts = 0, 0, []
    pool.initialized = pool.connected = True
    return pool


class _ClientSocket:
    """A browser's WebSocket as the proxy sees it."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, text: str) -> None:
        self.sent.append(json.loads(text))


class _OneShotSub:
    """The proxy's ZeroMQ SUB, replaying given frames, then stopping the listener."""

    def __init__(self, proxy: WebSocketProxy, frames: list[tuple[str, dict[str, Any]]]):
        self.proxy = proxy
        self.frames = [[topic.encode(), json.dumps(payload).encode()] for topic, payload in frames]

    async def recv_multipart(self):
        if self.frames:
            return self.frames.pop(0)
        self.proxy.running = False
        raise TimeoutError


class _MarketDataServiceStub:
    def process_market_data(self, _data: dict[str, Any]) -> bool:
        return True


def _proxy_with_clients(subscribers: dict[int, tuple[str, str, int]]) -> WebSocketProxy:
    """A proxy built without __init__ (no ports), with one browser client per entry."""
    proxy = WebSocketProxy.__new__(WebSocketProxy)
    proxy.running = True
    proxy.clients = {client_id: _ClientSocket() for client_id in subscribers}
    proxy.user_mapping = dict.fromkeys(subscribers, "owner")
    proxy.user_broker_mapping = {"owner": "upstox"}
    proxy.broker_adapters = {}
    proxy.subscriptions = {client_id: set() for client_id in subscribers}
    proxy.subscription_index = defaultdict(set)
    for client_id, key in subscribers.items():
        proxy.subscription_index[key].add(client_id)
    proxy.order_subscribers = defaultdict(set)
    proxy.last_message_time = {}
    proxy.last_tick_time = {}
    proxy._last_stale_warn = {}
    proxy._stale_tick_warn_seconds = 120
    proxy._last_stale_check = proxy._last_cleanup_time = 0.0
    proxy._stale_check_interval = 30
    proxy._cleanup_interval = 300
    proxy._throttle_entry_max_age = 60
    proxy._messages_processed = 0
    return proxy


async def _deliver(proxy: WebSocketProxy, frames: list[tuple[str, dict[str, Any]]]) -> None:
    proxy.socket = _OneShotSub(proxy, frames)
    await proxy.zmq_listener()
    for _ in range(20):  # let any per-client sender drain
        await asyncio.sleep(0)


def test_a_quote_subscriber_covered_by_depth_receives_volume_ohlc_and_oi(
    make_adapter, monkeypatch
) -> None:
    adapter = make_adapter()
    pool = _pool(adapter)
    symbol, exchange, key = OPTION
    assert pool.subscribe(symbol, exchange, 3)["status"] == "success"
    covered = pool.subscribe(symbol, exchange, 2)
    assert covered["status"] == "success" and "covered" in covered["message"]
    assert list(adapter.subscriptions) == [f"{symbol}_{exchange}_3"]  # Upstox sees Depth only
    assert adapter.upstox.held == {key: "full"}

    _feed(adapter, option_full_frame())
    assert [topic for topic, _payload in adapter.published] == ["NFO_NIFTY27OCT2625100CE_DEPTH"]

    monkeypatch.setattr(proxy_server, "get_market_data_service", _MarketDataServiceStub)
    proxy = _proxy_with_clients({7: (symbol, exchange, 2)})
    asyncio.run(_deliver(proxy, adapter.published))

    (message,) = proxy.clients[7].sent
    assert message["type"] == "market_data" and message["mode"] == 2
    data = message["data"]
    assert data["volume"] == 123450
    assert (data["open"], data["high"], data["low"], data["close"]) == (90.0, 110.0, 88.0, 95.25)
    assert data["oi"] == 4567800
    assert data["ltp"] == 101.5


def _option_ltpc_frame() -> pb.FeedResponse:
    """What Upstox sends for OPTION while it streams the key in `ltpc`."""
    response = _response()
    _ltpc(response.feeds[OPTION[2]].ltpc, 101.5, 95.25, ltq=75)
    return response


@pytest.mark.parametrize(("lower", "higher"), [(2, 3), (1, 2), (1, 3)])
def test_downgrade_keeps_the_upstox_key_for_the_remaining_mode(
    make_adapter, lower: int, higher: int
) -> None:
    """Closing the higher mode must not unsubscribe the key the lower mode still uses. Once
    only LTP is left, the key goes back to `ltpc`, which is all LTP needs."""
    adapter = make_adapter()
    pool = _pool(adapter)
    symbol, exchange, key = OPTION
    assert pool.subscribe(symbol, exchange, lower)["status"] == "success"
    assert pool.subscribe(symbol, exchange, higher)["status"] == "success"
    assert adapter.upstox.held == {key: "full"}

    downgraded = pool.unsubscribe(symbol, exchange, higher)

    assert downgraded["status"] == "success"
    assert adapter.upstox.unsubscribed == []
    upstox_mode = "ltpc" if lower == 1 else "full"
    assert adapter.upstox.held == {key: upstox_mode}
    assert adapter.ws_client.mode_of(key) == upstox_mode
    assert sum(len(keys) for keys in adapter.ws_client._keys_by_mode.values()) == 1
    assert list(adapter.subscriptions) == [f"{symbol}_{exchange}_{lower}"]

    # The remaining subscriber keeps receiving ticks.
    frame = _option_ltpc_frame() if lower == 1 else option_full_frame()
    topics = _feed(adapter, frame)
    suffix = {1: "LTP", 2: "QUOTE"}[lower]
    assert list(topics) == [f"NFO_NIFTY27OCT2625100CE_{suffix}"]
    assert json.loads(topics[f"NFO_NIFTY27OCT2625100CE_{suffix}"])["ltp"] == 101.5


@pytest.mark.parametrize(("low", "high"), [(1, 2), (1, 3), (2, 3)])
def test_the_key_leaves_upstox_when_every_mode_is_released(
    make_adapter, low: int, high: int
) -> None:
    """The lower mode's owner leaves first, then the higher one's. The pool hands each mode
    back to the adapter, so no forgotten record keeps the key streaming with no owner."""
    adapter = make_adapter()
    pool = _pool(adapter)
    symbol, exchange, key = OPTION
    assert pool.subscribe(symbol, exchange, low)["status"] == "success"
    assert pool.subscribe(symbol, exchange, high)["status"] == "success"

    assert pool.unsubscribe(symbol, exchange, low)["status"] == "success"
    assert list(adapter.subscriptions) == [f"{symbol}_{exchange}_{high}"]
    assert adapter.upstox.held == {key: "full"}  # still needed by `high`

    assert pool.unsubscribe(symbol, exchange, high)["status"] == "success"
    assert pool.subscription_map == {} and adapter.subscriptions == {}
    assert adapter.upstox.held == {}
    assert adapter.upstox.unsubscribed == [[key]]


@pytest.mark.parametrize(("covered", "upstox_mode"), [(2, "full"), (1, "ltpc")])
def test_a_downgrade_never_drops_the_key_in_between(
    make_adapter, covered: int, upstox_mode: str
) -> None:
    """Depth subscribed first, a lower mode covered by it, then Depth released. The lower
    mode is taken before Depth goes, so the key never leaves Upstox and nothing is sent
    again; with only LTP left it is switched to `ltpc` in place."""
    adapter = make_adapter()
    pool = _pool(adapter)
    symbol, exchange, key = OPTION
    assert pool.subscribe(symbol, exchange, 3)["status"] == "success"
    assert pool.subscribe(symbol, exchange, covered)["status"] == "success"
    adapter.upstox.sent.clear()

    assert pool.unsubscribe(symbol, exchange, 3)["status"] == "success"

    switched = [("change_mode", [key], "ltpc")] if upstox_mode == "ltpc" else []
    assert adapter.upstox.sent == switched
    assert adapter.upstox.held == {key: upstox_mode}
    assert list(adapter.subscriptions) == [f"{symbol}_{exchange}_{covered}"]


def test_a_refused_downgrade_leaves_the_higher_mode_owned(make_adapter, monkeypatch) -> None:
    adapter = make_adapter()
    pool = _pool(adapter)
    symbol, exchange, key = OPTION
    assert pool.subscribe(symbol, exchange, 3)["status"] == "success"
    assert pool.subscribe(symbol, exchange, 1)["status"] == "success"
    refused = {"status": "error", "message": "refused"}
    monkeypatch.setattr(adapter, "subscribe", lambda *_args, **_kwargs: refused)

    result = pool.unsubscribe(symbol, exchange, 3)

    assert (result["status"], result["code"]) == ("error", "DOWNGRADE_FAILED")
    assert set(pool.subscription_map) == {(symbol, exchange, 1), (symbol, exchange, 3)}
    assert list(adapter.subscriptions) == [f"{symbol}_{exchange}_3"]
    assert adapter.upstox.held == {key: "full"}


class _OneTokenAdapter:
    """A broker adapter without RELEASE_EVERY_MODE, holding one token per symbol: a release
    of any mode drops it, whatever mode another subscriber still needs."""

    def __init__(self) -> None:
        self.subscriptions: dict[str, Any] = {}
        self.calls: list[tuple[str, int]] = []
        self.at_broker: dict[tuple[str, str], int] = {}

    def subscribe(self, symbol: str, exchange: str, mode: int, _depth: int = 5) -> dict:
        self.calls.append(("subscribe", mode))
        self.at_broker[(symbol, exchange)] = mode
        return {"status": "success"}

    def unsubscribe(self, symbol: str, exchange: str, mode: int) -> dict:
        self.calls.append(("unsubscribe", mode))
        self.at_broker.pop((symbol, exchange), None)
        return {"status": "success"}


def test_a_broker_without_the_flag_keeps_the_pools_old_paths() -> None:
    """Only an adapter that declares RELEASE_EVERY_MODE is handed a lower mode's release or
    a lower-first downgrade. Another broker's token, which a Depth subscriber still uses,
    must not be dropped when the LTP subscriber leaves."""
    adapter = _OneTokenAdapter()
    pool = _pool(adapter)
    assert pool.subscribe("RELIANCE", "NSE", 1)["status"] == "success"
    assert pool.subscribe("RELIANCE", "NSE", 3)["status"] == "success"
    adapter.calls.clear()

    assert pool.unsubscribe("RELIANCE", "NSE", 1)["status"] == "success"
    assert adapter.calls == []
    assert adapter.at_broker == {("RELIANCE", "NSE"): 3}

    assert pool.subscribe("RELIANCE", "NSE", 2)["status"] == "success"  # covered by Depth
    adapter.calls.clear()
    assert pool.unsubscribe("RELIANCE", "NSE", 3)["status"] == "success"
    assert adapter.calls == [("unsubscribe", 3), ("subscribe", 2)]  # upstream's order
    assert adapter.at_broker == {("RELIANCE", "NSE"): 2}


def test_a_refused_release_after_the_lower_mode_was_taken_leaves_both_owned(
    make_adapter, monkeypatch
) -> None:
    """Depth is released after LTP was taken for the downgrade, and the adapter refuses: Depth
    goes back into the pool with its depth, and the adapter's records stay within the pool's."""
    adapter = make_adapter()
    pool = _pool(adapter)
    symbol, exchange, key = OPTION
    assert pool.subscribe(symbol, exchange, 3, 20)["status"] == "success"
    assert pool.subscribe(symbol, exchange, 1)["status"] == "success"  # covered
    release = adapter.unsubscribe
    refused = {"status": "error", "message": "refused"}
    monkeypatch.setattr(
        adapter, "unsubscribe", lambda s, e, m: refused if m == 3 else release(s, e, m)
    )

    result = pool.unsubscribe(symbol, exchange, 3)

    assert (result["status"], result["code"]) == ("error", "DOWNGRADE_FAILED")
    assert result["phase"] == "release_high_mode"
    assert set(pool.subscription_map) == {(symbol, exchange, 1), (symbol, exchange, 3)}
    assert pool.subscription_depths[(symbol, exchange, 3)] == 20
    assert sorted(adapter.subscriptions) == [f"{symbol}_{exchange}_1", f"{symbol}_{exchange}_3"]
    assert adapter.upstox.held == {key: "full"}


def test_a_refused_release_of_a_lower_mode_leaves_it_owned(make_adapter, monkeypatch) -> None:
    adapter = make_adapter()
    pool = _pool(adapter)
    symbol, exchange, key = OPTION
    assert pool.subscribe(symbol, exchange, 1)["status"] == "success"
    assert pool.subscribe(symbol, exchange, 3)["status"] == "success"
    refused = {"status": "error", "message": "refused"}
    monkeypatch.setattr(adapter, "unsubscribe", lambda *_args: refused)

    result = pool.unsubscribe(symbol, exchange, 1)

    assert result == refused
    assert set(pool.subscription_map) == {(symbol, exchange, 1), (symbol, exchange, 3)}
    assert pool.subscription_depths[(symbol, exchange, 1)] == 5
    assert adapter.upstox.held == {key: "full"}


def test_a_lower_mode_beside_a_higher_one_never_switches_the_key_down(make_adapter) -> None:
    """The adapter used directly, without the pool: LTP subscribed while Depth holds the key
    is sent in the mode the key needs, `full`, so nothing is sent at all."""
    adapter = make_adapter()
    _hold(adapter, OPTION, 3)
    _hold(adapter, OPTION, 1)

    assert adapter.upstox.sent == [("sub", [OPTION[2]], "full")]
    assert adapter.upstox.held == {OPTION[2]: "full"}


def test_releasing_a_mode_never_switches_a_key_up(make_adapter) -> None:
    """LTP is streamed in `ltpc` while a Depth subscribe still waits in the batch. Releasing
    LTP leaves a record needing `full`, but switching keys is only ever downward: the
    batch's subscribe takes the key up when it goes out."""
    adapter = make_adapter()
    adapter._start_batch_timer = lambda: None  # hold the batch, as the 0.5 s timer does
    _hold(adapter, OPTION, 1)
    adapter._process_batch_subscriptions()
    _hold(adapter, OPTION, 3)

    assert adapter.unsubscribe(OPTION[0], OPTION[1], 1)["status"] == "success"

    assert adapter.upstox.sent == [("sub", [OPTION[2]], "ltpc")]
    adapter._process_batch_subscriptions()
    assert adapter.upstox.held == {OPTION[2]: "full"}


def test_a_reconnect_replays_each_key_once_in_the_mode_it_needs(make_adapter) -> None:
    adapter = make_adapter()
    _hold(adapter, OPTION, 3)
    _hold(adapter, OPTION, 1)
    _hold(adapter, SBIN, 1)
    fresh = _UpstoxServer()  # Upstox forgets every key when a socket closes
    adapter.ws_client.ws = adapter.upstox = fresh
    adapter.ws_client._keys_by_mode.clear()  # as the client's _on_ws_open does

    adapter._on_connect()

    assert sorted(fresh.sent) == [("sub", [SBIN[2]], "ltpc"), ("sub", [OPTION[2]], "full")]
    assert fresh.held == {OPTION[2]: "full", SBIN[2]: "ltpc"}


def test_a_key_released_before_its_subscribe_went_out_is_not_sent(make_adapter) -> None:
    adapter = make_adapter()
    adapter._start_batch_timer = lambda: None  # hold the batch, as the 0.5 s timer does
    _hold(adapter, SBIN, 1)
    assert adapter.unsubscribe("SBIN", "NSE", 1)["status"] == "success"

    adapter._process_batch_subscriptions()

    assert ("sub", [SBIN[2]], "ltpc") not in adapter.upstox.sent
    assert adapter.upstox.held == {}


def test_the_client_streams_each_key_in_one_mode_and_says_so() -> None:
    """The per-mode sets follow a key between modes, so the caps count it once."""
    client = _connected_client()
    upstox = client.ws
    key = OPTION[2]

    assert client.subscribe([key], "ltpc") and client.mode_of(key) == "ltpc"
    assert client.subscribe([key], "full") and client.mode_of(key) == "full"
    assert client._keys_by_mode == {"full": {key}}  # counted once, in one mode
    assert client.change_mode([key], "ltpc") and client.mode_of(key) == "ltpc"
    assert client.unsubscribe([key]) and client.mode_of(key) is None

    assert upstox.sent == [
        ("sub", [key], "ltpc"),
        ("sub", [key], "full"),
        ("change_mode", [key], "ltpc"),
        ("unsub", [key], None),
    ]
    client._connected = False
    assert not client.change_mode([key], "full")  # never sent on a closed socket


def test_the_last_mode_leaving_releases_the_key_and_its_carry_forward(make_adapter) -> None:
    adapter = make_adapter()
    pool = _pool(adapter)
    symbol, exchange, key = OPTION
    assert pool.subscribe(symbol, exchange, 2)["status"] == "success"
    _feed(adapter, option_full_frame())
    assert key in adapter._last_ltpc and key in adapter._day_carry

    assert pool.unsubscribe(symbol, exchange, 2)["status"] == "success"

    assert adapter.upstox.unsubscribed == [[key]]
    assert adapter.upstox.held == {}
    assert key not in adapter._last_ltpc and key not in adapter._day_carry


# --------------------------------------------------------------------------------------------
# The broker linger over the real pool and adapter
# --------------------------------------------------------------------------------------------


class _HeldSleep:
    """The linger's sleep, held until the test lets it run out."""

    def __init__(self) -> None:
        self.gates: list[asyncio.Event] = []

    async def __call__(self, _seconds: float) -> None:
        gate = asyncio.Event()
        self.gates.append(gate)
        await gate.wait()


def _lingering_proxy(monkeypatch, wrapper) -> WebSocketProxy:
    """A proxy built without __init__, whose broker adapter is `wrapper`, lingering 300 s."""
    monkeypatch.setattr(proxy_server, "verify_api_key", lambda _key: "owner")
    monkeypatch.setattr(proxy_server, "get_broker_name", lambda _key: "upstox")
    monkeypatch.setattr(proxy_server, "create_broker_adapter", lambda _broker: wrapper)
    proxy = WebSocketProxy.__new__(WebSocketProxy)
    proxy.clients, proxy.subscriptions, proxy.user_mapping = {}, {}, {}
    proxy.user_broker_mapping, proxy.broker_adapters = {}, {}
    proxy.subscription_index = defaultdict(set)
    proxy.order_subscribers = defaultdict(set)
    proxy.broker_linger_seconds = 300.0
    proxy._linger_sleep = _HeldSleep()
    return proxy


def _pooled(adapter: UpstoxWebSocketAdapter):
    wrapper = broker_factory._PooledAdapterWrapper(UpstoxWebSocketAdapter, "upstox")
    wrapper._pool, wrapper._user_id = _pool(adapter), "owner"
    return wrapper


async def _screen(proxy: WebSocketProxy, client_id: int, mode: str) -> None:
    """A browser signs in and subscribes OPTION in `mode`."""
    proxy.clients[client_id] = _ClientSocket()
    proxy.subscriptions[client_id] = set()
    await proxy.authenticate_client(client_id, {"api_key": "k"})
    symbol, exchange, _key = OPTION
    request = {"symbols": [{"symbol": symbol, "exchange": exchange}], "mode": mode, "depth": 5}
    await proxy.subscribe_client(client_id, request)


def test_the_linger_keeps_a_socket_only_once_upstox_streams_nothing(
    make_adapter, monkeypatch
) -> None:
    """A positions screen (LTP) and a chart (Depth) on one option both close. Every record
    is handed back, Upstox stops the key, and only then is the idle socket kept."""
    adapter = make_adapter()
    wrapper = _pooled(adapter)
    proxy = _lingering_proxy(monkeypatch, wrapper)

    async def scenario() -> None:
        await _screen(proxy, 1, "LTP")
        await _screen(proxy, 2, "Depth")
        assert adapter.upstox.held == {OPTION[2]: "full"}
        await proxy.cleanup_client(1)
        await proxy.cleanup_client(2)
        for _ in range(5):
            await asyncio.sleep(0)

        assert "owner" in proxy._broker_lingers
        assert wrapper.subscriptions == {} and adapter.subscriptions == {}
        assert adapter.upstox.held == {}
        proxy._cancel_all_broker_lingers()

    asyncio.run(scenario())


def test_a_record_the_pool_lost_track_of_ends_the_connection_at_once(
    make_adapter, monkeypatch
) -> None:
    """The pool's map is empty but the adapter still streams a key: the socket is not
    provably idle, so it is closed at once instead of kept."""
    adapter = make_adapter()
    wrapper = _pooled(adapter)
    proxy = _lingering_proxy(monkeypatch, wrapper)

    async def scenario() -> None:
        await _screen(proxy, 1, "Quote")
        _hold(adapter, SBIN, 1)  # a record no pool entry owns, streaming at Upstox
        await proxy.cleanup_client(1)

        assert proxy._lingers() == {}
        assert "owner" not in proxy.broker_adapters
        assert adapter.subscriptions == {}  # the pool's disconnect took the adapter down

    asyncio.run(scenario())


def test_feed_alive_tells_a_stopped_feed_from_a_reconnecting_one(make_adapter) -> None:
    adapter = make_adapter()
    client = adapter.ws_client
    assert adapter.feed_alive

    client._connected = False  # reconnecting: the socket thread is still trying
    assert adapter.feed_alive
    client._ws_thread.alive = False  # it gave up after its last attempt
    assert not adapter.feed_alive
    client._ws_thread.alive = True
    client._authorize_refusals = 1  # Upstox refuses the stored login (after 03:30)
    assert not adapter.feed_alive
    client._authorize_refusals = 0
    client.running = False  # stopped
    assert not adapter.feed_alive


class _FreshAdapter:
    """What create_broker_adapter builds once a stale adapter has been evicted."""

    def __init__(self) -> None:
        self.subscriptions: dict[str, Any] = {}
        self.connected = True

    def initialize(self, *_args, **_kwargs) -> dict[str, Any]:
        return {"status": "success"}

    def connect(self) -> dict[str, Any]:
        return {"status": "success"}

    def subscribe(self, symbol: str, exchange: str, mode: int, _depth: int) -> dict[str, Any]:
        self.subscriptions[f"{symbol}_{exchange}_{mode}"] = {"mode": mode}
        return {"status": "success"}

    def is_auth_error(self, _message: str) -> bool:
        return False


@pytest.mark.parametrize("stopped", [None, "gave up", "login refused"])
def test_a_screen_coming_back_reuses_a_lingering_socket_only_while_its_feed_lives(
    make_adapter, monkeypatch, stopped: str | None
) -> None:
    """A lingering adapter whose Upstox client gave up, or whose login Upstox refuses, would
    accept subscriptions and never send a tick. It is rebuilt with the current login."""
    adapter = make_adapter()
    wrapper = _pooled(adapter)
    proxy = _lingering_proxy(monkeypatch, wrapper)
    fresh = _FreshAdapter()

    async def scenario() -> None:
        await _screen(proxy, 1, "LTP")
        await proxy.cleanup_client(1)
        for _ in range(5):
            await asyncio.sleep(0)
        assert "owner" in proxy._broker_lingers
        if stopped == "gave up":
            adapter.ws_client._ws_thread.alive = False
        elif stopped == "login refused":
            adapter.ws_client._authorize_refusals = 1
        monkeypatch.setattr(proxy_server, "create_broker_adapter", lambda _broker: fresh)

        await _screen(proxy, 2, "LTP")

        assert proxy._lingers() == {}
        if stopped is None:
            assert proxy.broker_adapters["owner"] is wrapper
            assert adapter.upstox.held == {OPTION[2]: "ltpc"}
        else:
            assert proxy.broker_adapters["owner"] is fresh
            assert not adapter.ws_client.running  # the old socket was closed

    asyncio.run(scenario())


def test_an_unsubscribe_naming_its_mode_releases_it_at_upstox(make_adapter, monkeypatch) -> None:
    """The proxy reads an unsubscribe without a mode as Quote, so the frame MarketDataManager
    sent before M2 released nothing it held. The frame it sends now names the mode."""
    adapter = make_adapter()
    proxy = _lingering_proxy(monkeypatch, _pooled(adapter))
    symbol, exchange, key = SBIN

    async def scenario() -> None:
        proxy.clients[1] = browser = _ClientSocket()
        proxy.subscriptions[1] = set()
        await proxy.authenticate_client(1, {"api_key": "k"})
        held = {"symbol": symbol, "exchange": exchange}
        await proxy.subscribe_client(1, {"symbols": [held], "mode": "LTP"})

        await proxy.unsubscribe_client(1, {"action": "unsubscribe", "symbols": [held]})
        assert browser.sent[-1]["successful"][0]["mode"] == "Quote"  # not what it held
        assert adapter.upstox.held == {key: "ltpc"}

        request = {"action": "unsubscribe", "symbols": [held], "mode": "LTP"}
        await proxy.unsubscribe_client(1, request)
        assert browser.sent[-1]["successful"][0]["mode"] == "LTP"
        assert (symbol, exchange, 1) not in proxy.subscription_index
        assert adapter.upstox.held == {}

    asyncio.run(scenario())


# --------------------------------------------------------------------------------------------
# A lagging client and the values a newer frame leaves out
# --------------------------------------------------------------------------------------------


class _GatedPhone:
    """A phone on a slow link: its first send waits until the test lets it through."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.gate = asyncio.Event()
        self.transport = self

    async def send(self, frame: str) -> None:
        if not self.sent:
            await self.gate.wait()
        self.sent.append(json.loads(frame))

    def abort(self) -> None:
        pass


def test_a_lagging_client_still_gets_what_only_the_merged_tick_carried(
    make_adapter, monkeypatch
) -> None:
    """A trade frame (open interest, iv, greeks, average price) waits behind a slow send, and
    a book-only frame of the same stream, which leaves those out, merges into it. The client
    gets one Quote message: the newer frame's values and the older one's open interest and
    greeks, never old open interest left on screen as current."""
    adapter = make_adapter()
    _hold(adapter, OPTION, 2)
    _hold(adapter, SBIN, 1)  # another stream, whose tick occupies the sender
    published = []
    for frame in (ltpc_only_frame(), option_full_frame(), _book_only(OPTION, OPTION_BOOK)):
        adapter.published.clear()
        adapter.decoder._process_binary_message(frame.SerializeToString())
        published.extend(adapter.published)
    trade, book_only = published[1][1], published[2][1]
    assert "oi" in trade and "oi" not in book_only

    monkeypatch.setattr(proxy_server, "get_market_data_service", _MarketDataServiceStub)
    proxy = _proxy_with_clients({7: (OPTION[0], OPTION[1], 2)})
    proxy.subscription_index[(SBIN[0], SBIN[1], 1)].add(7)

    async def scenario() -> list[dict[str, Any]]:
        phone = _GatedPhone()
        proxy.clients[7] = phone
        proxy._open_outbox(7, phone)
        await _deliver(proxy, published)  # SBIN in flight, the trade waiting, then book-only
        phone.gate.set()
        for _ in range(20):
            await asyncio.sleep(0)
        proxy._close_outbox(7)
        return phone.sent

    sent = asyncio.run(scenario())
    (quote,) = [message["data"] for message in sent if message["symbol"] == OPTION[0]]
    assert (quote["oi"], quote["iv"], quote["average_price"]) == (4567800, 0.1432, 99.87)
    assert quote["greeks"] == trade["greeks"]
    assert quote["last_trade_quantity"] == 0  # the newer frame reports no new trade
    assert (quote["ltp"], quote["volume"]) == (101.5, 123450)
