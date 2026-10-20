"""सुदर्शन / SUDARSHAN: the normalized Upstox tick payload (M11.3).

Pure functions over one decoded `FeedResponse` feed (`MessageToDict` with its
default options: camelCase keys, int64 as strings, zero scalars left out), plus
DayCarry, the per-key state the adapter keeps between frames. The adapter calls
these to build the LTP, Quote and Depth payloads, so upstox_adapter.py carries
only short hooks.

Field contract, in every mode where Upstox sent the value:

- `prev_close` (from `ltpc.cp`) and `last_trade_time` (from `ltpc.ltt`, epoch ms);
- Quote and Depth: `close` is the previous session's close (0.0 when unknown),
  `volume` is the volume traded today, plus `oi`, `iv` and, for options,
  `greeks` {delta, gamma, theta, vega, rho};
- Depth is a superset of Quote: the same fields beside `depth`.

Upstox sends incremental frames: one that only moves the book carries no LTPC,
no day candle and no volume. The last trade's price and time and the day's
candle and volume are carried forward to such a frame; the last trade's
quantity is not, since that trade was published already. A value Upstox did
not send is left out rather than sent as 0, except where the upstream payload
has always carried the key. No published number is NaN or Infinity: JSON has
neither, and a browser drops the whole message that carries one.
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


def finite(value: Any, default: float = 0.0) -> float:
    """float(value) when it is a finite number, else `default`.

    json_format spells a double Upstox could not compute as "NaN" or
    "Infinity", which float() accepts.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def count(value: Any) -> int:
    """A quantity as int: int64 arrives as a string, a double may be NaN; else 0."""
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
    number = finite(value)
    return int(number)


def positive_number(value: Any, kind: type) -> int | float | None:
    """`kind(value)` when it is a positive, finite number, else None.

    proto3 omits a scalar left at its default, so json_format drops a zero
    `cp`, `ltt`, `oi` or `iv` from the frame. Those fields are published only
    when present: a missing previous close or open interest must not reach a
    trader as a real 0.
    """
    number = finite(value, math.nan)
    if not number > 0:
        return None
    return int(number) if kind is int else number


def full_feed(feed_data: dict[str, Any]) -> dict[str, Any]:
    """The frame's MarketFullFeed or IndexFullFeed, or {} when it has neither."""
    wrapper = feed_data.get("fullFeed") or {}
    ff = wrapper.get("marketFF") or wrapper.get("indexFF") or {}
    return ff if isinstance(ff, dict) else {}


def day_candle(ff: dict[str, Any]) -> dict[str, Any]:
    """The day's candle (the "1d" entry of `marketOHLC`), or {} when the frame has none.

    Never another interval: the I1 entry is the current minute, and taking it
    for the day put a one-minute high and low on the screen as the day's.
    """
    candles = (ff.get("marketOHLC") or {}).get("ohlc") or []
    if not isinstance(candles, list):
        return {}
    for candle in candles:
        if isinstance(candle, dict) and candle.get("interval") == "1d":
            return candle
    return {}


class DayCarry:
    """The day's candle and volume per instrument key, carried across frames.

    A frame without the 1d candle keeps the last one, so open, high and low do
    not fall to 0. A frame without `vtt` keeps the last vtt: falling back to the
    day candle's own volume, which Upstox counts differently, made volume go
    backwards. The adapter forgets a key when its feed is released and clears
    everything on disconnect.
    """

    def __init__(self) -> None:
        self._candles: dict[str, dict[str, Any]] = {}
        self._volumes: dict[str, int] = {}

    def day(self, key: str, ff: dict[str, Any]) -> dict[str, Any]:
        """Remember what this frame says about the day; return the day as now known."""
        candle = day_candle(ff)
        if candle:
            self._candles[key] = candle
        else:
            candle = self._candles.get(key, {})
        if "vtt" in ff:
            self._volumes[key] = count(ff["vtt"])
        volume = self._volumes.get(key)
        if volume is None:
            volume = count(candle.get("vol"))
        return {
            "open": candle.get("open"),
            "high": candle.get("high"),
            "low": candle.get("low"),
            "volume": volume,
        }

    def forget(self, key: str) -> None:
        self._candles.pop(key, None)
        self._volumes.pop(key, None)

    def clear(self) -> None:
        self._candles.clear()
        self._volumes.clear()

    def __contains__(self, key: str) -> bool:
        return key in self._candles or key in self._volumes


def carried_ltpc(cached: dict[str, Any] | None) -> dict[str, Any]:
    """The last LTPC seen, for a frame that carries none.

    It keeps the last trade's price, time and previous close. It drops `ltq`,
    because that trade's quantity was published with it and a consumer adding
    up traded quantity would count it again on every book-only frame, and
    `iep`, because an indicative auction price is valid only for the tick that
    carried it.
    """
    if not cached:
        return {}
    return {key: value for key, value in cached.items() if key not in ("ltq", "iep")}


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


def ltp_fields(ltpc: dict[str, Any]) -> dict[str, Any]:
    """The LTP payload's trade keys: ltp, ltq, ltt, cp, then prev_close and last_trade_time."""
    fields: dict[str, Any] = {
        "ltp": finite(ltpc.get("ltp")),
        "ltq": count(ltpc.get("ltq")),
        "ltt": count(ltpc.get("ltt")),
        "cp": finite(ltpc.get("cp")),
    }
    fields.update(ltpc_fields(ltpc))
    return fields


def ltpc_only_fields(ltpc: dict[str, Any]) -> dict[str, Any]:
    """What a Quote or Depth payload can say when the frame had only an LTPC.

    The LTP-style keys (ltq, ltt, cp) are the ones this path has always
    published. The Quote names are added beside them. OHLC, volume and the
    book stay out, so a consumer keeps its last values instead of zeros.
    """
    fields = ltp_fields(ltpc)
    fields["last_trade_quantity"] = fields["ltq"]
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
        value = finite(message.get(name, 0), math.nan)
        values[name] = value if math.isfinite(value) else None
    return values


def quote_fields(ff: dict[str, Any], ltpc: dict[str, Any], day: dict[str, Any]) -> dict[str, Any]:
    """The Quote field set, shared by the Quote and Depth payloads.

    `close` is the PREVIOUS session's close (`ltpc.cp`), not the 1d candle's
    close. Upstox's 1d candle is today's running bar, so its close is the
    live price, and every consumer that computes change as `ltp - close`
    (useLiveQuote, the agent's live cards, the order dialog, Flow, the
    documented Quote example in docs/prompt/websockets-format.md) showed
    about 0% on Upstox. Other adapters already publish the previous close
    here (Zerodha, Fyers, Kotak, Motilal). 0.0 when Upstox sent no previous
    close, which the frontend treats as unknown.

    `day` comes from DayCarry: the day's candle and volume (vtt) as last known.
    `average_price`, `total_buy_quantity` and `total_sell_quantity` appear only
    when this frame carries them, as do `oi`, `iv` and `greeks`; `prev_close`
    and `last_trade_time` only when the LTPC does. A consumer that merges ticks
    keeps its last value instead of being sent a 0 that means "not in this
    frame". `last_trade_quantity` is 0 unless this frame reports a trade.
    """
    prev_close = positive_number(ltpc.get("cp"), float)

    fields: dict[str, Any] = {
        "open": finite(day.get("open")),
        "high": finite(day.get("high")),
        "low": finite(day.get("low")),
        "close": prev_close if prev_close is not None else 0.0,
        "ltp": finite(ltpc.get("ltp")),
        "last_trade_quantity": count(ltpc.get("ltq")),
        "volume": day.get("volume") or 0,
    }
    average_price = finite(ff.get("atp"), math.nan)
    if math.isfinite(average_price):
        fields["average_price"] = average_price
    for name, wire in (("total_buy_quantity", "tbq"), ("total_sell_quantity", "tsq")):
        if math.isfinite(finite(ff.get(wire), math.nan)):
            fields[name] = count(ff[wire])
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
