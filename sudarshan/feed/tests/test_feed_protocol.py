"""Protobuf decode + normalisation of synthesized Upstox FeedResponse messages."""

import json
import math

import pytest
from feed_fakes import LTT, full_stock, ltpc, ohlc_1d, response
from google.protobuf import json_format
from google.protobuf.message import DecodeError

from sudarshan.feed.protocol import decode, pb2, request
from sudarshan.feed.store import SnapshotStore

REL = "NSE_EQ|INE002A01018"
AT_1520 = 1_793_094_600_250  # 27 Oct 2026 15:20:00.250 IST, epoch ms
#: A market_info frame typed by hand in the shape Upstox's Feed V3 page documents for one (field
#: names as there, int64 as strings), as it reads once NSE_EQ's closing auction takes orders: the
#: three maps of MarketInfo (MarketDataFeedV3.proto:124-128). Never a recording.
CAS_FRAME = {
    "type": "market_info",
    "currentTs": str(AT_1520),
    "marketInfo": {
        "segmentStatus": {
            "NSE_EQ": "NORMAL_OPEN", "NSE_FO": "NORMAL_OPEN", "NSE_INDEX": "NORMAL_OPEN",
            "BSE_EQ": "NORMAL_OPEN", "BSE_FO": "NORMAL_OPEN", "BSE_INDEX": "NORMAL_OPEN",
            "MCX_FO": "NORMAL_OPEN", "NCD_FO": "NORMAL_OPEN", "BCD_FO": "NORMAL_OPEN",
        },
        "casMarketStatus": {"NSE_EQ": {"status": "CAS_LM_START", "updatedTime": str(AT_1520)}},
        "preOpenSessionStatus": {
            "NSE_EQ": {"status": "PRE_OPEN_END", "updatedTime": "1793071800000"}
        },
    },
}  # fmt: skip


def test_ltpc_stock():
    kind, ticks, status, cas = decode(response({REL: ltpc(1400.5, ltq=7, cp=1388.0)}))
    assert (kind, status, cas) == ("live_feed", {}, None)  # no market info: auction state kept
    assert ticks == {REL: {"ltp": 1400.5, "ltt": LTT, "ltq": 7, "cp": 1388.0}}
    assert isinstance(ticks[REL]["ltt"], int)  # epoch ms as an int, not the json_format string


def test_full_stock_with_depth():
    kind, ticks, _, _ = decode(response({REL: full_stock()}, kind="initial_feed"))
    tick = ticks[REL]
    assert kind == "initial_feed"
    assert (tick["ltp"], tick["ltq"], tick["cp"]) == (1400.5, 25, 1388.0)
    assert (tick["bid"], tick["bid_qty"], tick["ask"], tick["ask_qty"]) == (1400.4, 100, 1400.6, 50)
    assert tick["ohlc"] == {
        "open": 1390,
        "high": 1410,
        "low": 1385,
        "close": 1400.5,
        "vol": 123456,
        "ts": LTT,
    }
    assert (tick["atp"], tick["vtt"], tick["tbq"], tick["tsq"]) == (
        1398.25,
        4_567_890,
        12345,
        23456,
    )
    assert isinstance(tick["tbq"], int)
    assert not {"oi", "iv", "greeks"} & set(tick)  # proto3 zeros are "not sent" for an equity


def test_index_full_and_option_greeks():
    index = pb2.Feed(fullFeed=pb2.FullFeed(indexFF=pb2.IndexFullFeed(
        ltpc=pb2.LTPC(ltp=25000.5, ltt=LTT, cp=24900.0), marketOHLC=ohlc_1d(25000.5))))  # fmt: skip
    option = pb2.Feed(firstLevelWithGreeks=pb2.FirstLevelWithGreeks(
        ltpc=pb2.LTPC(ltp=120.25, ltt=LTT, ltq=75, cp=110.0),
        firstDepth=pb2.Quote(bidQ=75, bidP=120.0, askQ=150, askP=120.5),
        optionGreeks=pb2.OptionGreeks(delta=0.52, theta=-12.5, gamma=0.001, vega=8.2, rho=math.nan),
        vtt=1000, oi=250000.0, iv=0.145))  # fmt: skip
    _, ticks, _, _ = decode(response({"NSE_INDEX|Nifty 50": index, "NSE_FO|45450": option}))
    nifty, opt = ticks["NSE_INDEX|Nifty 50"], ticks["NSE_FO|45450"]
    assert (nifty["ltp"], nifty["cp"], nifty["ohlc"]["close"]) == (25000.5, 24900.0, 25000.5)
    assert not {"bid", "vtt", "greeks"} & set(nifty)
    assert opt["greeks"] == {
        "delta": 0.52,
        "theta": -12.5,
        "gamma": 0.001,
        "vega": 8.2,
        "rho": None,
    }
    assert (opt["oi"], opt["vtt"], opt["iv"]) == (250000, 1000, 0.145)
    assert (opt["bid"], opt["bid_qty"], opt["ask"], opt["ask_qty"]) == (120.0, 75, 120.5, 150)
    json.dumps(opt, allow_nan=False)  # NaN never reaches the bus


def test_market_info_and_an_unknown_enum_value():
    frame = response(
        {}, kind="market_info", segments={"NSE_EQ": "NORMAL_OPEN", "NSE_FO": "PRE_OPEN_START"}
    )
    assert decode(frame) == (
        "market_info",
        {},
        {"NSE_EQ": "NORMAL_OPEN", "NSE_FO": "PRE_OPEN_START"},
        {},  # market info without casMarketStatus: no closing auction runs
    )
    future = pb2.FeedResponse()
    future.type = 7  # a Type value newer than our .proto must not raise
    assert decode(future.SerializeToString())[0] == "7"
    with pytest.raises(DecodeError):
        decode(b"\xff\xff\xff\xff")


def test_incremental_packets_merge_and_initial_feed_replaces():
    store = SnapshotStore(None)
    _, first, _, _ = decode(response({REL: full_stock()}, kind="initial_feed"))
    store.update(REL, first[REL], 1, replace=True)
    _, depth_only, _, _ = decode(response({REL: full_stock(ltp=1401.0, with_ltpc=False)}))
    merged = store.update(REL, depth_only[REL], 2)
    assert (merged["ltp"], merged["ohlc"]["close"], merged["ts_recv"]) == (1400.5, 1401.0, 2)
    _, fresh, _, _ = decode(response({REL: ltpc(1402.0)}, kind="initial_feed"))
    assert store.update(REL, fresh[REL], 3, replace=True) == {
        "key": REL, "ltp": 1402.0, "ltt": LTT, "ltq": 0, "cp": 0.0, "ts_recv": 3,
    }  # fmt: skip


def test_subscription_requests():
    sub = json.loads(request("sub", ["NSE_INDEX|Nifty 50"], "ltpc"))
    assert set(sub) == {"guid", "method", "data"} and len(sub["guid"]) == 20
    assert (sub["method"], sub["data"]) == (
        "sub",
        {"instrumentKeys": ["NSE_INDEX|Nifty 50"], "mode": "ltpc"},
    )
    assert json.loads(request("unsub", [REL], "full"))["data"] == {"instrumentKeys": [REL]}


def test_closing_auction_status_from_a_frame_shaped_like_upstoxs():
    frame = json_format.ParseDict(CAS_FRAME, pb2.FeedResponse()).SerializeToString()
    kind, ticks, status, cas = decode(frame)
    assert (kind, ticks) == ("market_info", {})
    assert status["NSE_EQ"] == "NORMAL_OPEN" and len(status) == 9  # unchanged by the new map
    assert cas == {"NSE_EQ": {"status": "CAS_LM_START", "updated_ms": AT_1520}}
    assert isinstance(cas["NSE_EQ"]["updated_ms"], int)  # epoch ms, not the JSON string
    json.dumps(cas, allow_nan=False)
    # A phase newer than Upstox's page (it is a string on the wire) and no time given: kept as is.
    later = response({}, "market_info", {"NSE_EQ": "NORMAL_OPEN"}, cas={"NSE_EQ": ("CAS_X", 0)})
    assert decode(later)[3] == {"NSE_EQ": {"status": "CAS_X", "updated_ms": None}}


def _candle(interval: str, close: float, ts: int):
    return pb2.OHLC(interval=interval, open=close - 2, high=close + 1, low=close - 3, close=close,
                    vol=4200, ts=ts)  # fmt: skip


def test_the_one_minute_candle_of_a_stock_and_an_index_and_a_packet_without_one():
    minute, day = 1_793_094_240_000, 1_793_039_400_000  # 15:14 and midnight, 27 Oct 2026 IST
    stock = full_stock()
    stock.fullFeed.marketFF.marketOHLC.CopyFrom(
        pb2.MarketOHLC(ohlc=[_candle("1d", 1400.5, day), _candle("I1", 1400.0, minute)])
    )
    index = pb2.Feed(fullFeed=pb2.FullFeed(indexFF=pb2.IndexFullFeed(
        ltpc=pb2.LTPC(ltp=25000.5, ltt=LTT, cp=24900.0),
        marketOHLC=pb2.MarketOHLC(ohlc=[_candle("I1", 25000.0, minute), _candle("1d", 25000.5, day)]),
    )))  # fmt: skip
    _, ticks, _, _ = decode(response({REL: stock, "NSE_INDEX|Nifty 50": index}, "initial_feed"))
    one_minute = {"open": 1398.0, "high": 1401.0, "low": 1397.0, "close": 1400.0, "vol": 4200,
                  "ts": minute}  # fmt: skip
    assert ticks[REL]["ohlc_1m"] == one_minute and ticks[REL]["ohlc"]["ts"] == day
    nifty = ticks["NSE_INDEX|Nifty 50"]
    assert (nifty["ohlc_1m"]["close"], nifty["ohlc_1m"]["ts"], nifty["ohlc"]["close"]) == (
        25000.0, minute, 25000.5,
    )  # fmt: skip
    # The store keeps the last minute's candle when a packet carries only the day's.
    store = SnapshotStore(None)
    store.update(REL, ticks[REL], 1, replace=True)
    day_only = full_stock(ltp=1401.0, with_ltpc=False)
    day_only.fullFeed.marketFF.marketOHLC.CopyFrom(
        pb2.MarketOHLC(ohlc=[_candle("1d", 1401.0, day)])
    )
    _, later, _, _ = decode(response({REL: day_only}))
    assert "ohlc_1m" not in later[REL]
    merged = store.update(REL, later[REL], 2)
    assert merged["ohlc_1m"] == one_minute and merged["ohlc"]["close"] == 1401.0
    # The next minute's candle replaces it whole.
    nxt = full_stock(with_ltpc=False)
    nxt.fullFeed.marketFF.marketOHLC.CopyFrom(
        pb2.MarketOHLC(ohlc=[_candle("I1", 1401.5, minute + 60_000)])
    )
    _, third, _, _ = decode(response({REL: nxt}))
    assert store.update(REL, third[REL], 3)["ohlc_1m"]["ts"] == minute + 60_000
