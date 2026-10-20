"""सुदर्शन / SUDARSHAN: the normalized Upstox tick payload (M11.3).

Pure functions over one decoded `FeedResponse` feed (`MessageToDict` with its
default options: camelCase keys, int64 as strings, zero scalars left out). The
adapter keeps the per-key carry-forward state and calls these to build the
LTP, Quote and Depth payloads, so upstox_adapter.py carries only short hooks.

Field contract, in every mode where Upstox sent the value:

- `prev_close` (from `ltpc.cp`) and `last_trade_time` (from `ltpc.ltt`, epoch ms);
- Quote and Depth: `close` is the previous session's close (0.0 when unknown),
  `volume` is the volume traded today, plus `oi`, `iv` and, for options,
  `greeks` {delta, gamma, theta, vega, rho};
- Depth is a superset of Quote: the same fields beside `depth`.

A missing value is left out rather than sent as 0, except where the upstream
payload has always carried the key.
"""

from __future__ import annotations

import math
import time
from typing import Any

GREEK_NAMES = ("delta", "gamma", "theta", "vega", "rho")


def tick_timestamp(value: Any) -> int:
    """The frame's currentTs as integer epoch milliseconds, or now if absent.

    currentTs is an int64, which json_format renders as a string.
    """
    try:
        ts = int(value)
    except (TypeError, ValueError):
        ts = 0
    return ts if ts > 0 else int(time.time() * 1000)


def positive_number(value: Any, kind: type) -> int | float | None:
    """`kind(value)` when it is a positive, finite number, else None.

    proto3 omits a scalar left at its default, so json_format drops a zero
    `cp`, `ltt`, `oi` or `iv` from the frame. Those fields are published only
    when present: a missing previous close or open interest must not reach a
    trader as a real 0. Accepts the string form json_format gives int64, and
    refuses "NaN" and "Infinity" (how json_format spells a computed value
    Upstox could not work out): JSON has neither, and a browser drops the
    whole tick that carries one.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return int(number) if kind is int else number


def daily_candle(feed_data: dict[str, Any]) -> dict[str, Any]:
    """The day's candle in this frame, or {} when it has none.

    The 1d entry of `marketOHLC`; the first entry when there is no 1d one,
    which is the fallback the Quote extractor has always used.
    """
    full_feed = feed_data.get("fullFeed") or {}
    ff = full_feed.get("marketFF") or full_feed.get("indexFF") or {}
    ohlc_list = (ff.get("marketOHLC") or {}).get("ohlc") or []
    if not isinstance(ohlc_list, list):
        return {}
    daily = next(
        (o for o in ohlc_list if isinstance(o, dict) and o.get("interval") == "1d"),
        ohlc_list[0] if ohlc_list else {},
    )
    return daily if isinstance(daily, dict) else {}


def ltpc_fields(ltpc: dict[str, Any]) -> dict[str, Any]:
    """`prev_close` (from cp) and `last_trade_time` (from ltt, epoch ms)."""
    fields: dict[str, Any] = {}
    prev_close = positive_number(ltpc.get("cp"), float)
    if prev_close is not None:
        fields["prev_close"] = prev_close
    last_trade_time = positive_number(ltpc.get("ltt"), int)
    if last_trade_time is not None:
        fields["last_trade_time"] = last_trade_time
    return fields


def ltpc_only_fields(ltpc: dict[str, Any]) -> dict[str, Any]:
    """What a Quote or Depth payload can say when the frame had only an LTPC.

    The LTP-style keys (ltq, ltt, cp) are the ones this path has always
    published. The Quote names are added beside them. OHLC, volume and the
    book stay out, so a consumer keeps its last values instead of zeros.
    """
    fields: dict[str, Any] = {
        "ltp": float(ltpc.get("ltp", 0) or 0),
        "ltq": int(ltpc.get("ltq", 0) or 0),
        "ltt": int(ltpc.get("ltt", 0) or 0),
        "cp": float(ltpc.get("cp", 0) or 0),
        "last_trade_quantity": int(ltpc.get("ltq", 0) or 0),
    }
    fields.update(ltpc_fields(ltpc))
    if "prev_close" in fields:
        fields["close"] = fields["prev_close"]
    return fields


def greeks(ff: dict[str, Any]) -> dict[str, float | None] | None:
    """{delta, gamma, theta, vega, rho} when Upstox sent optionGreeks, else None.

    Inside a present OptionGreeks message an absent field is a proto3 zero
    (rho often is), so it is filled with 0.0. A greek Upstox could not compute
    (NaN, for an illiquid contract) is None, published as null, as
    sudarshan-feed does. An empty message, which carries no greek at all, is
    treated as absent.
    """
    message = ff.get("optionGreeks")
    if not isinstance(message, dict) or not message:
        return None
    values: dict[str, float | None] = {}
    for name in GREEK_NAMES:
        try:
            value = float(message.get(name, 0) or 0)
        except (TypeError, ValueError):
            value = math.nan
        values[name] = value if math.isfinite(value) else None
    return values


def quote_fields(
    ff: dict[str, Any],
    ltpc: dict[str, Any],
    daily: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The Quote field set, shared by the Quote and Depth payloads.

    `close` is the PREVIOUS session's close (`ltpc.cp`), not the 1d candle's
    close. Upstox's 1d candle is today's running bar, so its close is the
    live price, and every consumer that computes change as `ltp - close`
    (useLiveQuote, the agent's live cards, the order dialog, Flow, the
    documented Quote example in docs/prompt/websockets-format.md) showed
    about 0% on Upstox. Other adapters already publish the previous close
    here (Zerodha, Fyers, Kotak, Motilal). 0.0 when Upstox sent no previous
    close, which the frontend treats as unknown.

    `volume` is `vtt` (volume traded today), falling back to the 1d candle's
    volume when vtt is absent. `oi`, `iv`, `greeks`, `prev_close` and
    `last_trade_time` appear only when Upstox sent them. `daily` is the day's
    candle, this frame's or the last one seen for the key.
    """
    if daily is None:
        daily = daily_candle({"fullFeed": {"marketFF": ff}})
    ohlc = daily

    if "vtt" in ff:
        volume = int(ff["vtt"])
    else:
        volume = int(ohlc.get("vol", 0)) if ohlc else 0

    prev_close = positive_number(ltpc.get("cp"), float)

    fields: dict[str, Any] = {
        "open": float(ohlc.get("open", 0)),
        "high": float(ohlc.get("high", 0)),
        "low": float(ohlc.get("low", 0)),
        "close": prev_close if prev_close is not None else 0.0,
        "ltp": float(ltpc.get("ltp", 0)),
        "last_trade_quantity": int(ltpc.get("ltq", 0)),
        "volume": volume,
        "average_price": float(ff.get("atp", 0)),
        "total_buy_quantity": int(ff.get("tbq", 0)),
        "total_sell_quantity": int(ff.get("tsq", 0)),
    }
    fields.update(ltpc_fields(ltpc))

    oi = positive_number(ff.get("oi"), int)
    if oi is not None:
        fields["oi"] = oi
    iv = positive_number(ff.get("iv"), float)
    if iv is not None:
        fields["iv"] = iv
    option_greeks = greeks(ff)
    if option_greeks is not None:
        fields["greeks"] = option_greeks

    return fields
