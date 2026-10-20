"""Protobuf decode + normalisation of synthesized Upstox FeedResponse messages."""

import json
import math

import pytest
from feed_fakes import LTT, full_stock, ltpc, ohlc_1d, response
from google.protobuf.message import DecodeError

from sudarshan.feed.protocol import decode, pb2, request
from sudarshan.feed.store import SnapshotStore

REL = "NSE_EQ|INE002A01018"


def test_ltpc_stock():
    kind, ticks, status = decode(response({REL: ltpc(1400.5, ltq=7, cp=1388.0)}))
    assert (kind, status) == ("live_feed", {})
    assert ticks == {REL: {"ltp": 1400.5, "ltt": LTT, "ltq": 7, "cp": 1388.0}}
    assert isinstance(ticks[REL]["ltt"], int)  # epoch ms as an int, not the json_format string


def test_full_stock_with_depth():
    kind, ticks, _ = decode(response({REL: full_stock()}, kind="initial_feed"))
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
    _, ticks, _ = decode(response({"NSE_INDEX|Nifty 50": index, "NSE_FO|45450": option}))
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
    )
    future = pb2.FeedResponse()
    future.type = 7  # a Type value newer than our .proto must not raise
    assert decode(future.SerializeToString())[0] == "7"
    with pytest.raises(DecodeError):
        decode(b"\xff\xff\xff\xff")


def test_incremental_packets_merge_and_initial_feed_replaces():
    store = SnapshotStore(None)
    _, first, _ = decode(response({REL: full_stock()}, kind="initial_feed"))
    store.update(REL, first[REL], 1, replace=True)
    _, depth_only, _ = decode(response({REL: full_stock(ltp=1401.0, with_ltpc=False)}))
    merged = store.update(REL, depth_only[REL], 2)
    assert (merged["ltp"], merged["ohlc"]["close"], merged["ts_recv"]) == (1400.5, 1401.0, 2)
    _, fresh, _ = decode(response({REL: ltpc(1402.0)}, kind="initial_feed"))
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
