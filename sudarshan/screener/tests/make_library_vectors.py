"""Writes vectors/library.json: the fixed 1,000-bar set and the arrays of the pinned ``openalgo.ta``.

Run it from the repository root when the pin of ``openalgo`` changes, and say so in the pull request:

    uv run python sudarshan/screener/tests/make_library_vectors.py [--cross-check]

``--cross-check`` also compares the rolling extrema and the indicators TA-Lib has with TA-Lib's own
(install ``TA-Lib`` into a scratch environment first, never into the project) and records how far
apart they are after the indicators' seeds have worn off. The hand-computed cases (I1, I2 and the
few-bar cases) are written in this file by hand, not computed.

This is not a test: pytest does not collect it. It is kept next to the vectors so the way the fixed
set was made is never lost.
"""

from __future__ import annotations

import json
import os
import sys
from fractions import Fraction
from pathlib import Path

import numpy as np

from sudarshan.screener import catalog as cat
from sudarshan.screener import library
from sudarshan.screener.rolling import rolling_max, rolling_min
from sudarshan.screener.types import Candle, Series

OUT = Path(__file__).parent / "vectors" / "library.json"
BARS, SESSION_BARS, FLAT = 1000, 25, range(300, 325)
SAMPLE = [*range(120), *range(120, 900, 4), *range(900, BARS)]
PIN = {"package": "openalgo", "version": "2.0.5"}

# (id, indicator, settings). Settings not named take the catalogue's defaults.
CASES = [
    ("I4", "sma", {"length": 20}), ("I4", "sma", {"length": 3, "source": "volume"}),
    ("I4", "wma", {"length": 20}), ("I4", "vwma", {"length": 20}),
    ("I5", "ema", {"length": 20}), ("I5", "ema", {"length": 5, "source": "hlc3"}),
    ("I5", "hma", {"length": 20}), ("I5", "hma", {"length": 14}), ("I5", "hma", {"length": 9}),
    ("I5", "tema", {"length": 20}),
    ("I6", "rsi", {"length": 14}), ("I6", "rsi", {"length": 2}),
    ("I7", "macd", {}), ("I7", "macd", {"fast": 5, "slow": 10, "signal": 3}),
    ("I8", "stochastic", {}), ("I8", "stochastic", {"k": 5, "d": 2, "smooth": 2}),
    ("I8", "williams_r", {}), ("I8", "cci", {}),
    ("I9", "supertrend", {}), ("I9", "supertrend", {"atr_length": 7, "factor": "2.5"}),
    ("I9", "atr", {}), ("I9", "adx", {}), ("I9", "adx", {"length": 7}),
    ("I10", "bbands", {}), ("I10", "bbands", {"length": 10, "mult": "1.5"}),
    ("I10", "donchian", {}),
    ("I10", "keltner", {}), ("I10", "keltner", {"length": 10, "atr_length": 5, "mult": "1.5"}),
    ("I11", "mfi", {}), ("I11", "cmf", {}), ("I11", "rvol", {}), ("I11", "obv", {}),
    ("I12", "sma", {"length": 10, "source": "hl2"}), ("I12", "ema", {"length": 10, "source": "obv"}),
    ("I12", "sma", {"length": 10, "source": "oi"}),
]  # fmt: skip


def make_bars() -> dict:
    """1,000 15-minute bars in paise, 25 to a session, with a flat stretch of 25 and quiet and
    busy spells. Deterministic: the numbers it makes are stored, so this is only the record."""
    rng = np.random.default_rng(20261028)
    bars = {key: [] for key in "ohlcv"}
    price, spell, flat_price = 250_000.0, 1.0, 0
    for i in range(BARS):
        if i % 80 == 0:
            spell = rng.uniform(0.5, 1.8)
        gap = rng.normal(0, 0.003) if i % SESSION_BARS == 0 and i else 0.0
        open_ = price * (1 + gap)
        price = open_ * (1 + rng.normal(0, 0.0014 * spell))
        high = max(open_, price) * (1 + abs(rng.normal(0, 0.0006 * spell)))
        low = min(open_, price) * (1 - abs(rng.normal(0, 0.0006 * spell)))
        volume = int(rng.lognormal(8.0, 0.6) * (1.6 if i % SESSION_BARS in (0, 24) else 1.0))
        o, h, lo, c = (int(round(x / 5)) * 5 for x in (open_, high, low, price))
        h, lo = max(h, o, c), min(lo, o, c)
        if i == FLAT[0]:
            flat_price = bars["c"][-1]
        if i in FLAT:
            o = h = lo = c = flat_price
            volume = 100
        for key, value in zip("ohlcv", (o, h, lo, c, volume), strict=True):
            bars[key].append(value)
        price = float(c)
    return bars


def series_of(bars: dict) -> Series:
    candles = []
    for i in range(len(bars["c"])):
        start = i * 900_000
        candles.append(
            Candle(
                start,
                start + 900_000,
                20260101 + i // SESSION_BARS,
                bars["o"][i],
                bars["h"][i],
                bars["l"][i],
                bars["c"][i],
                bars["v"][i],
            )  # fmt: skip
        )
    return Series.of(15, candles)


def settings(name: str, given: dict) -> dict:
    """The full settings of a case, as the compiled operand carries them (Fractions for decimals)."""
    out = {}
    for param in cat.IND[name].params:
        value = given.get(param.name, param.default)
        out[param.name] = Fraction(value) if param.kind == "dec" else value
    return out


def stored(params: dict) -> dict:
    """Settings as the vectors file holds them: decimals as text."""
    out = {}
    for key, value in params.items():
        if isinstance(value, Fraction):
            value = str(value.numerator) if value.denominator == 1 else str(float(value))
        out[key] = value
    return out


def sampled(array: np.ndarray) -> list:
    return [None if np.isnan(array[i]) else round(float(array[i]), 6) for i in SAMPLE]


def first_valid(array: np.ndarray) -> int | None:
    valid = np.flatnonzero(~np.isnan(array))
    return int(valid[0]) if valid.size else None


def dump(node, depth: int = 0) -> str:
    """JSON with objects spread over lines and lists kept on one line."""
    pad = "  " * depth
    if isinstance(node, dict):
        if not node:
            return "{}"
        items = [f"{pad}  {json.dumps(k)}: {dump(v, depth + 1)}" for k, v in node.items()]
        return "{\n" + ",\n".join(items) + f"\n{pad}}}"
    if isinstance(node, list) and node and all(isinstance(x, dict) for x in node):
        return "[\n" + ",\n".join(f"{pad}  {dump(x, depth + 1)}" for x in node) + f"\n{pad}]"
    return json.dumps(node, separators=(", ", ": "))


def hand_cases() -> list:
    """Worked by hand, on a few bars, so a convention is checked by arithmetic anyone can redo."""
    return [
        {"id": "I1", "note": "sma(3) of 1 to 5", "name": "sma", "p": {"length": 3},
         "bars": {"c": [1, 2, 3, 4, 5]}, "out": {"value": [None, None, 2, 3, 4]}},
        {"id": "I2", "note": "ema(2) of 10, 20, 30 starts at the first value: alpha 2/3; the "
                              "screener calls ema(5) ready only at 15 bars",
         "name": "ema", "p": {"length": 2}, "bars": {"c": [10, 20, 30]},
         "out": {"value": [10, 16.666667, 25.555556]}},
        {"id": "I4h", "note": "wma(3): weights 1, 2, 3", "name": "wma", "p": {"length": 3},
         "bars": {"c": [1, 2, 3, 4, 5]},
         "out": {"value": [None, None, 2.333333, 3.333333, 4.333333]}},
        {"id": "I6h", "note": "rsi(2) of 10, 11, 10, 12, 13: Wilder smoothing from a simple average",
         "name": "rsi", "p": {"length": 2}, "bars": {"c": [10, 11, 10, 12, 13]},
         "out": {"value": [None, None, 50, 83.333333, 90]}},
        {"id": "I9h", "note": "atr(2): the first bar's true range is high minus low, Wilder after",
         "name": "atr", "p": {"length": 2},
         "bars": {"h": [11, 12, 13, 12], "l": [9, 10, 10, 11], "c": [10, 11, 12, 11]},
         "out": {"value": [None, 2, 2.5, 1.75]}},
        {"id": "I10h", "note": "bbands(3, 2): population deviation sqrt(2/3) of 1, 2, 3",
         "name": "bbands", "p": {"length": 3, "mult": "2"}, "bars": {"c": [1, 2, 3, 4]},
         "out": {"upper": [None, None, 3.632993, 4.632993], "middle": [None, None, 2, 3],
                 "lower": [None, None, 0.367007, 1.367007],
                 "pct_b": [None, None, 0.806186, 0.806186],
                 "width": [None, None, 1.632993, 1.088662]}},
        {"id": "I10d", "note": "donchian(3)", "name": "donchian", "p": {"length": 3},
         "bars": {"h": [5, 7, 6, 9], "l": [3, 4, 2, 6], "c": [4, 6, 3, 8]},
         "out": {"upper": [None, None, 7, 9], "lower": [None, None, 2, 2],
                 "middle": [None, None, 4.5, 5.5]}},
        {"id": "I8w", "note": "williams_r(3)", "name": "williams_r", "p": {"length": 3},
         "bars": {"h": [5, 7, 6, 9], "l": [3, 4, 2, 6], "c": [4, 6, 3, 8]},
         "out": {"value": [None, None, -80, -14.285714]}},
        {"id": "I8c", "note": "cci(3): typical price, mean deviation, 0.015", "name": "cci",
         "p": {"length": 3}, "bars": {"h": [5, 7, 6, 9], "l": [3, 4, 2, 6], "c": [4, 6, 3, 8]},
         "out": {"value": [None, None, -63.636364, 100]}},
        {"id": "I11o", "note": "obv starts at 0", "name": "obv", "p": {},
         "bars": {"c": [10, 11, 11, 9], "v": [100, 50, 70, 30]},
         "out": {"value": [0, 50, 50, 20]}},
        {"id": "I5t", "note": "tema(2) of 10 to 50: each average starts from the first value of "
                              "its input and drops its first result; exact fractions 9910/243 and "
                              "36730/729",
         "name": "tema", "p": {"length": 2}, "bars": {"c": [10, 20, 30, 40, 50]},
         "out": {"value": [None, None, None, 40.781893, 50.384088]}},
        {"id": "I11m", "note": "mfi(2) on typical price 10, 12, 11, 11, 11, 13 with volume 100 to "
                               "600: rises 2400 (bar 1) and 7800 (bar 5), fall 3300 (bar 2); "
                               "42.105263 = 100*2400/5700; a window that never moved has no index",
         "name": "mfi", "p": {"length": 2},
         "bars": {"c": [10, 12, 11, 11, 11, 13], "v": [100, 200, 300, 400, 500, 600]},
         "out": {"value": [None, None, 42.105263, 0, None, 100]}},
        {"id": "I11c", "note": "cmf(2): multipliers +1, -1, 0, +1, +1 on volume 100, 300, 200, 0, 0; "
                               "-200/400, -300/500, 0/200; a window without volume has none",
         "name": "cmf", "p": {"length": 2},
         "bars": {"h": [12, 12, 12, 12, 12], "l": [10, 10, 10, 10, 10],
                  "c": [12, 10, 11, 12, 12], "v": [100, 300, 200, 0, 0]},
         "out": {"value": [None, -0.5, -0.6, 0, None]}},
        {"id": "I11r", "note": "rvol(2) of volume 10, 30, 0, 0: 30/20, 0/15, then no average",
         "name": "rvol", "p": {"length": 2}, "bars": {"c": [1, 1, 1, 1], "v": [10, 30, 0, 0]},
         "out": {"value": [None, 1.5, 0, None]}},
        {"id": "I4v", "note": "vwma(3): (100*10 + 102*20 + 0)/30, then 102 alone, then no volume",
         "name": "vwma", "p": {"length": 3},
         "bars": {"c": [100, 102, 101, 105, 103], "v": [10, 20, 0, 0, 0]},
         "out": {"value": [None, None, 101.333333, 102, None]}},
        {"id": "I8f", "note": "cci(3) of identical bars whose typical price (31/3) is not a "
                              "whole number: no deviation, 0",
         "name": "cci", "p": {"length": 3},
         "bars": {"h": [11] * 5, "l": [10] * 5, "c": [10] * 5},
         "out": {"value": [None, None, 0, 0, 0]}},
    ]  # fmt: skip


def main() -> None:
    bars = make_bars()
    series = series_of(bars)
    cases = []
    for case_id, name, given in CASES:
        params = settings(name, given)
        out = library.compute(name, params, series)
        cases.append({
            "id": case_id, "name": name, "p": stored(params),
            "first": {k: first_valid(v) for k, v in out.items()},
            "out": {k: sampled(v) for k, v in out.items()},
        })  # fmt: skip
    values = np.array(bars["c"], dtype=np.float64)
    window = 20
    document = {
        "about": "The library's golden vectors. 'hand' cases are worked by hand on a few bars. "
                 "'bars' is the fixed 1,000-bar 15-minute set, in paise, with a flat stretch at "
                 "bars 300 to 324. 'cases' pin the arrays of the pinned openalgo.ta at the "
                 "'sample' bar indices (null is NaN); 'first' is the first bar with a value. "
                 "'rolling' pins the rolling maximum and minimum of the closes. mfi, cci, vwma, cmf "
                 "and rvol are the screener's own sums (library.py says why), the rest are the "
                 "library's. Made by tests/make_library_vectors.py.",
        "pin": PIN,
        "sample": SAMPLE,
        "hand": hand_cases(),
        "rolling": {
            "window": window, "values": bars["c"],
            "max": [None if np.isnan(x) else int(x) for x in rolling_max(values, window)],
            "min": [None if np.isnan(x) else int(x) for x in rolling_min(values, window)],
        },
        "bars": bars,
        "cases": cases,
    }  # fmt: skip
    if "--cross-check" in sys.argv:
        document["cross_check"] = cross_check(bars, series)
    OUT.write_text(dump(document) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


def cross_check(bars: dict, series: Series) -> dict:
    """How far the library is from TA-Lib after each indicator's seed has worn off."""
    site = os.environ.get("TALIB_SITE")  # a scratch environment's site-packages, never ours
    if site:
        sys.path.append(site)
    import talib  # noqa: PLC0415 (only here, only when asked)

    f = lambda key: np.array(bars[key], dtype=np.float64)  # noqa: E731
    h, lo, c, v = f("h"), f("l"), f("c"), f("v")

    def gap(ours, theirs, skip, flat=True):
        """Largest difference after ``skip`` bars; ``flat`` leaves out the windows that touch the
        flat stretch, where a zero range is a convention each library chooses for itself."""
        both = ~np.isnan(ours) & ~np.isnan(theirs)
        both[:skip] = False
        if not flat:
            both[FLAT[0] : FLAT[-1] + 40] = False
        return float(np.max(np.abs(ours[both] - theirs[both]))) if both.any() else None

    def run(name, **given):
        return library.compute(name, settings(name, given), series)

    result = {"talib": talib.__version__}
    result["max_20"] = gap(rolling_max(c, 20), talib.MAX(c, 20), 0)
    result["min_20"] = gap(rolling_min(c, 20), talib.MIN(c, 20), 0)
    result["max_7_high"] = gap(rolling_max(h, 7), talib.MAX(h, 7), 0)
    result["sma_20"] = gap(run("sma")["value"], talib.SMA(c, 20), 0)
    result["wma_20"] = gap(run("wma")["value"], talib.WMA(c, 20), 0)
    result["ema_20_after_200"] = gap(run("ema")["value"], talib.EMA(c, 20), 200)
    result["tema_20_after_600"] = gap(run("tema")["value"], talib.TEMA(c, 20), 600)
    result["rsi_14"] = gap(run("rsi")["value"], talib.RSI(c, 14), 0)
    macd = run("macd")
    t_macd, t_sig, t_hist = talib.MACD(c, 12, 26, 9)
    result["macd_after_300"] = gap(macd["macd"], t_macd, 300)
    result["macd_signal_after_300"] = gap(macd["signal"], t_sig, 300)
    result["macd_hist_after_300"] = gap(macd["hist"], t_hist, 300)
    stoch = run("stochastic")
    t_k, t_d = talib.STOCH(h, lo, c, 14, 3, 0, 3, 0)
    result["stoch_k"] = gap(stoch["k"], t_k, 0, flat=False)
    result["stoch_d"] = gap(stoch["d"], t_d, 0, flat=False)
    bb = run("bbands")
    t_u, t_m, t_l = talib.BBANDS(c, 20, 2, 2, 0)
    result["bb_upper"], result["bb_lower"] = gap(bb["upper"], t_u, 0), gap(bb["lower"], t_l, 0)
    adx = run("adx")
    result["adx_after_300"] = gap(adx["adx"], talib.ADX(h, lo, c, 14), 300)
    result["plus_di_after_300"] = gap(adx["plus_di"], talib.PLUS_DI(h, lo, c, 14), 300)
    result["minus_di_after_300"] = gap(adx["minus_di"], talib.MINUS_DI(h, lo, c, 14), 300)
    result["atr_after_300"] = gap(run("atr")["value"], talib.ATR(h, lo, c, 14), 300)
    result["cci_20"] = gap(run("cci")["value"], talib.CCI(h, lo, c, 20), 0)
    mfi, t_mfi = run("mfi")["value"], talib.MFI(h, lo, c, v, 14)
    result["mfi_14"] = gap(mfi, t_mfi, 0, flat=False)
    willr, t_willr = run("williams_r")["value"], talib.WILLR(h, lo, c, 14)
    result["williams_r_14"] = gap(willr, t_willr, 0, flat=False)
    at = FLAT[-1]

    def pair(ours_, theirs_):
        return [None if np.isnan(x) else float(x) for x in (ours_[at], theirs_[at])]

    result["flat_stretch_at_bar_324"] = {
        "stochastic_k": pair(stoch["k"], t_k),
        "williams_r": pair(willr, t_willr),
        "mfi": pair(mfi, t_mfi),
    }  # [ours, TA-Lib]: a zero range is a convention each library chooses itself
    don = run("donchian")
    result["donchian_upper"] = gap(don["upper"], talib.MAX(h, 20), 0)
    result["donchian_lower"] = gap(don["lower"], talib.MIN(lo, 20), 0)
    ours, theirs = run("obv")["value"], talib.OBV(c, v)
    result["obv_after_offset"] = gap(ours - ours[0], theirs - theirs[0], 0)
    return result


if __name__ == "__main__":
    main()
