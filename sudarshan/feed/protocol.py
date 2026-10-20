"""Upstox Market Data Feed V3 wire format.

Out: subscription requests are JSON sent as BINARY frames (Upstox ignores text frames).
In: protobuf ``FeedResponse`` messages. The generated module is the one OpenAlgo's Upstox adapter
ships (``broker/upstox/streaming/MarketDataFeedV3_pb2.py``); it is loaded by file path so this
service shares the single .proto source of truth without importing any OpenAlgo package.

Normalised tick fields (absent until Upstox has sent them):
    key, ltp, ltt (epoch ms), ltq, cp (previous close)   LTPC group
    bid, bid_qty, ask, ask_qty                           best level (full / option_greeks)
    ohlc {open, high, low, close, vol, ts}               the "1d" candle
    greeks {delta, theta, gamma, vega, rho}, iv          options
    atp, vtt (volume traded today), oi, tbq, tsq         full mode
    ts_recv (epoch ms)                                   added by the service

Presence rule: the message-typed groups above (LTPC, quote, greeks, OHLC) carry presence on the
wire, so a group that is present replaces its fields whole. The bare proto3 scalars (atp, vtt, oi,
iv, tbq, tsq) cannot tell "not sent" from 0, so a 0 there is treated as "not sent" and the store
keeps the previous value.
"""

from __future__ import annotations

import importlib.util
import json
import math
import uuid
from pathlib import Path

PB2_FILE = Path(__file__).resolve().parents[2] / "broker/upstox/streaming/MarketDataFeedV3_pb2.py"


def _load_pb2():
    spec = importlib.util.spec_from_file_location("sudarshan_feed_v3_pb2", PB2_FILE)
    if spec is None or spec.loader is None:
        raise ImportError(f"Upstox Feed V3 protobuf module not found at {PB2_FILE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pb2 = _load_pb2()

_FULL_SCALARS = ("atp", "vtt", "oi", "iv", "tbq", "tsq")
_GREEKS_SCALARS = ("vtt", "oi", "iv")
_INTEGRAL = {"vtt", "oi", "tbq", "tsq"}  # quantities; oi/tbq/tsq are doubles on the wire


def request(method: str, keys: list[str], mode: str | None = None) -> bytes:
    """One sub / change_mode / unsub request, encoded for a binary frame."""
    data: dict = {"instrumentKeys": keys}
    if mode and method != "unsub":
        data["mode"] = mode
    return json.dumps({"guid": uuid.uuid4().hex[:20], "method": method, "data": data}).encode()


def _enum_name(enum, value: int) -> str:
    try:
        return enum.Name(value)
    except ValueError:  # a value newer than our copy of the .proto
        return str(value)


def decode(buf: bytes) -> tuple[str, dict[str, dict], dict[str, str]]:
    """Parse one FeedResponse into (type, {key: partial tick}, {segment: market status})."""
    resp = pb2.FeedResponse()
    resp.ParseFromString(buf)
    status = {}
    if resp.HasField("marketInfo"):
        status = {
            seg: _enum_name(pb2.MarketStatus, value)
            for seg, value in resp.marketInfo.segmentStatus.items()
        }
    ticks = {key: normalize(feed) for key, feed in resp.feeds.items()}
    return _enum_name(pb2.Type, resp.type), ticks, status


def normalize(feed) -> dict:
    """Flatten one ``Feed`` message into the normalised (partial) tick dict."""
    out: dict = {}
    kind = feed.WhichOneof("FeedUnion")
    if kind == "ltpc":
        _ltpc(out, feed.ltpc)
    elif kind == "fullFeed":
        branch = feed.fullFeed.WhichOneof("FullFeedUnion")  # marketFF | indexFF
        if branch is None:
            return out
        ff = getattr(feed.fullFeed, branch)
        if ff.HasField("ltpc"):
            _ltpc(out, ff.ltpc)
        if ff.HasField("marketOHLC"):
            _ohlc(out, ff.marketOHLC)
        if branch == "marketFF":
            if ff.HasField("marketLevel"):
                _quote(out, ff.marketLevel.bidAskQuote)
            if ff.HasField("optionGreeks"):
                _greeks(out, ff.optionGreeks)
            _scalars(out, ff, _FULL_SCALARS)
    elif kind == "firstLevelWithGreeks":
        fl = feed.firstLevelWithGreeks
        if fl.HasField("ltpc"):
            _ltpc(out, fl.ltpc)
        if fl.HasField("firstDepth"):
            _quote(out, [fl.firstDepth])
        if fl.HasField("optionGreeks"):
            _greeks(out, fl.optionGreeks)
        _scalars(out, fl, _GREEKS_SCALARS)
    return out


def _ltpc(out: dict, m) -> None:
    out.update(ltp=m.ltp, ltt=m.ltt, ltq=m.ltq, cp=m.cp)


def _quote(out: dict, levels) -> None:
    # Best bid = highest bid price, best ask = lowest ask price (not trusting list order).
    bid = max((q for q in levels if q.bidP > 0), key=lambda q: q.bidP, default=None)
    ask = min((q for q in levels if q.askP > 0), key=lambda q: q.askP, default=None)
    out.update(
        bid=bid.bidP if bid else 0.0,
        bid_qty=bid.bidQ if bid else 0,
        ask=ask.askP if ask else 0.0,
        ask_qty=ask.askQ if ask else 0,
    )


def _ohlc(out: dict, m) -> None:
    for c in m.ohlc:
        if c.interval == "1d":
            out["ohlc"] = {
                "open": c.open, "high": c.high, "low": c.low, "close": c.close,
                "vol": c.vol, "ts": c.ts,
            }  # fmt: skip
            return


def _greeks(out: dict, m) -> None:
    # Computed values can be NaN for illiquid contracts; JSON has no NaN, so publish null.
    names = ("delta", "theta", "gamma", "vega", "rho")
    out["greeks"] = {n: v if math.isfinite(v := getattr(m, n)) else None for n in names}


def _scalars(out: dict, m, names: tuple[str, ...]) -> None:
    for name in names:
        value = getattr(m, name)
        if value and math.isfinite(value):
            out[name] = int(value) if name in _INTEGRAL else value
