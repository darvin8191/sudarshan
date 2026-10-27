"""The library indicators: ``openalgo.ta`` behind one call, the way the engine reads them.

``compute(name, params, series)`` returns every output of an indicator over a whole series as
float64 arrays of the series' length, keyed by the output names in ``catalog.IND[name].outs``.

What the pinned ``openalgo.ta`` (2.0.5) computes is the definition, except in four places where it
was found wrong or misleading and this module does the sum itself (each is pinned by a vector and
by an independent reference in the tests):

- ``mfi``: the library keeps running sums of fractional money flows. After a fall followed by a flat
  price the sums never return to zero and the ratio of two leftovers gave values such as -950. Here
  the flows are whole numbers (three times the typical price times the volume), summed exactly.
  A window in which the typical price never moved has no index (NaN), as in Pine Script.
- ``cci``: the library measures each bar against a float average that is a hair off in a run of
  identical bars, and gave 66.67 where the answer is 0. Here the mean deviation is summed exactly.
- ``vwma``, ``cmf`` and ``rvol``: a window in which nothing traded has no volume weights, no money
  flow and no average to compare with. The library answers the close, 0 and 1.0; here it is NaN, so
  a rule such as "relative volume between 0.8 and 1.2" never hits a stock that did not trade.

Everything else follows the library, whose conventions were checked against textbook and Pine
Script definitions written independently in the tests: EMA seeded with the first value, RSI and ATR
smoothed the Wilder way from a simple average, Bollinger Bands on the population deviation, slow
stochastic, Supertrend with direction -1 in an up trend and +1 in a down trend, ADX returned as
(+DI, -DI, ADX), HMA with the square root rounded down. A bump of the pin that changes a value
fails ``vectors/library.json``, and the bump's pull request changes it on purpose.

Rules this module keeps:

- ``highest``, ``lowest``, ``crossover``, ``rising`` and ``change`` of ``openalgo.ta`` are never
  called. They run as Python loops (1 to 4 ms for 1,000 bars, a thousand times the rest), so the
  screener has its own (``rolling``, ``price``, ``engine``). A test reads the screener's modules and
  fails if their names appear in code.
- A value is ready when the series holds the indicator's warm-up count (``catalog``), never because
  it is not NaN. A NaN that remains (a flat series, no volume) is still "cannot be computed".
- A series shorter than the library accepts gives NaN, not its ``ValueError``.

Pure: no I/O, no clock.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

import numpy as np
from openalgo import ta

from sudarshan.screener import catalog as cat
from sudarshan.screener.types import Series


def _floats(column: np.ndarray) -> np.ndarray:
    return column.astype(np.float64)


def source_array(series: Series, source: str) -> np.ndarray:
    """The chosen price, volume, open interest or on-balance volume as float64 (paise units)."""
    if source == "close":
        return _floats(series.c)
    if source == "open":
        return _floats(series.o)
    if source == "high":
        return _floats(series.h)
    if source == "low":
        return _floats(series.l)
    if source == "hl2":
        return (_floats(series.h) + _floats(series.l)) / 2
    if source == "hlc3":
        return (_floats(series.h) + _floats(series.l) + _floats(series.c)) / 3
    if source == "ohlc4":
        return (_floats(series.o) + _floats(series.h) + _floats(series.l) + _floats(series.c)) / 4
    if source == "volume":
        return _floats(series.v)
    if source == "oi":
        return np.where(series.oi >= 0, series.oi, np.nan)
    if source == "obv":
        return _obv(series)
    raise ValueError(f"unknown source: {source!r}")


def _obv(series: Series) -> np.ndarray:
    if len(series) == 0:
        return np.empty(0)
    return np.asarray(ta.obv(_floats(series.c), _floats(series.v)), dtype=np.float64)


def _hlc(series: Series) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return _floats(series.h), _floats(series.l), _floats(series.c)


def _arrays(*arrays) -> tuple[np.ndarray, ...]:
    return tuple(np.asarray(array, dtype=np.float64) for array in arrays)


def _value(array) -> dict[str, np.ndarray]:
    return {"value": np.asarray(array, dtype=np.float64)}


# ---- one function per indicator: (series, params) -> outputs ------------------------------------
def _sma(series: Series, p: Mapping) -> dict:
    return _value(ta.sma(source_array(series, p["source"]), p["length"]))


def _wma(series: Series, p: Mapping) -> dict:
    return _value(ta.wma(source_array(series, p["source"]), p["length"]))


def _ema(series: Series, p: Mapping) -> dict:
    return _value(ta.ema(source_array(series, p["source"]), p["length"]))


def _volume_sums(series: Series, length: int) -> np.ndarray:
    """Total volume of the last ``length`` bars at every bar; whole numbers, so exact. The first
    ``length - 1`` entries are not a full window and hold -1."""
    total = np.cumsum(series.v)
    sums = total.copy()
    sums[length:] -= total[:-length]
    sums[: length - 1] = -1
    return sums


def _vwma(series: Series, p: Mapping) -> dict:
    value = np.asarray(
        ta.vwma(source_array(series, p["source"]), _floats(series.v), p["length"]), dtype=np.float64
    )
    value[_volume_sums(series, p["length"]) == 0] = np.nan  # nothing traded: no weights
    return {"value": value}


def _hma(series: Series, p: Mapping) -> dict:
    return _value(ta.hma(_floats(series.c), p["length"]))


def _tema(series: Series, p: Mapping) -> dict:
    return _value(ta.tema(_floats(series.c), p["length"]))


def _rsi(series: Series, p: Mapping) -> dict:
    return _value(ta.rsi(_floats(series.c), p["length"]))


def _macd(series: Series, p: Mapping) -> dict:
    line, signal, hist = _arrays(*ta.macd(_floats(series.c), p["fast"], p["slow"], p["signal"]))
    return {"macd": line, "signal": signal, "hist": hist}


def _stochastic(series: Series, p: Mapping) -> dict:
    high, low, close = _hlc(series)
    k, d = _arrays(*ta.stochastic(high, low, close, p["k"], p["smooth"], p["d"]))
    return {"k": k, "d": d}


def _supertrend(series: Series, p: Mapping) -> dict:
    high, low, close = _hlc(series)
    value, direction = _arrays(
        *ta.supertrend(high, low, close, p["atr_length"], float(p["factor"]))
    )
    return {"value": value, "direction": direction}


def _bbands(series: Series, p: Mapping) -> dict:
    close = _floats(series.c)
    upper, middle, lower = _arrays(*ta.bbands(close, p["length"], float(p["mult"])))
    with np.errstate(divide="ignore", invalid="ignore"):
        spread = upper - lower
        pct_b = np.where(spread == 0, np.nan, (close - lower) / spread)
        width = np.where(middle == 0, np.nan, spread / middle)
    return {"upper": upper, "middle": middle, "lower": lower, "pct_b": pct_b, "width": width}


def _adx(series: Series, p: Mapping) -> dict:
    plus_di, minus_di, adx = _arrays(*ta.adx(*_hlc(series), p["length"]))
    return {"adx": adx, "plus_di": plus_di, "minus_di": minus_di}


def _atr(series: Series, p: Mapping) -> dict:
    return _value(ta.atr(*_hlc(series), p["length"]))


def _cci(series: Series, p: Mapping) -> dict:
    """Commodity channel index with the mean deviation summed exactly.

    ``ta.cci`` measures each bar against the average of the last ``length`` typical prices as a
    float; a run of identical bars leaves that average a hair off the price, and a deviation of
    1e-11 over 0.015 times 1e-11 gave values such as 66.67 where the answer is 0. Three times the
    typical price is a whole number, and with S its sum over the window, the index is
    ``(n * T - S) * n / (0.015 * sum(|n * T_j - S|))``, all whole numbers until the last division.
    A window of identical typical prices has no deviation and gives 0, as in TA-Lib.
    """
    length, size = p["length"], len(series)
    out = np.full(size, np.nan)
    if size < length:
        return {"value": out}
    typical = series.h + series.l + series.c  # three times the typical price, in paise
    windows = np.lib.stride_tricks.sliding_window_view(typical, length)
    total = windows.sum(axis=1)
    spread = np.abs(length * windows - total[:, None]).sum(axis=1)
    lead = (length * typical[length - 1 :] - total) * length
    with np.errstate(divide="ignore", invalid="ignore"):
        out[length - 1 :] = np.where(spread == 0, 0.0, lead / (0.015 * spread))
    return {"value": out}


def _mfi(series: Series, p: Mapping) -> dict:
    """Money flow index over the last ``length`` changes of the typical price, summed exactly.

    ``ta.mfi`` keeps a running sum of fractional money flows; after a fall followed by a flat price
    the sums never return to zero, and the ratio of two leftovers gave values such as -950. Prices
    and volumes are whole numbers here, so three times the typical price times the volume is one
    too: whole-number running totals are exact and a window is a difference of two of them. A window
    in which the typical price never moved has no index (NaN), as in Pine Script.
    """
    length, size = p["length"], len(series)
    out = np.full(size, np.nan)
    if size <= length:
        return {"value": out}
    typical, volume = series.h + series.l + series.c, series.v  # three times the typical price
    if int(typical.max()) * int(volume.max()) * size >= 1 << 62:  # beyond any real market
        typical, volume = typical.astype(object), volume.astype(object)  # exact, only slower
    flow = typical * volume
    up = np.where(typical[1:] > typical[:-1], flow[1:], 0)
    down = np.where(typical[1:] < typical[:-1], flow[1:], 0)
    rising = np.concatenate(([0], np.cumsum(up)))  # rising[i] is the flow of the rises up to bar i
    falling = np.concatenate(([0], np.cumsum(down)))
    pos = (rising[length:] - rising[:-length]).astype(np.float64)
    neg = (falling[length:] - falling[:-length]).astype(np.float64)
    total = pos + neg
    with np.errstate(divide="ignore", invalid="ignore"):
        out[length:] = np.where(total == 0, np.nan, 100.0 * pos / total)
    return {"value": out}


def _cmf(series: Series, p: Mapping) -> dict:
    value = np.asarray(ta.cmf(*_hlc(series), _floats(series.v), p["length"]), dtype=np.float64)
    value[_volume_sums(series, p["length"]) == 0] = np.nan  # nothing traded: no money flow
    return {"value": value}


def _williams_r(series: Series, p: Mapping) -> dict:
    return _value(ta.williams_r(*_hlc(series), p["length"]))


def _donchian(series: Series, p: Mapping) -> dict:
    upper, middle, lower = _arrays(*ta.donchian(_floats(series.h), _floats(series.l), p["length"]))
    return {"upper": upper, "lower": lower, "middle": middle}


def _keltner(series: Series, p: Mapping) -> dict:
    upper, middle, lower = _arrays(
        *ta.keltner(*_hlc(series), p["length"], p["atr_length"], float(p["mult"]))
    )
    return {"upper": upper, "middle": middle, "lower": lower}


def _rvol(series: Series, p: Mapping) -> dict:
    """Volume over its average of the last ``length`` bars, the bar itself included. A window with
    no volume at all has no average, so no ratio (``ta.rvol`` answers 1.0 there)."""
    length, size = p["length"], len(series)
    out = np.full(size, np.nan)
    if size < length:
        return {"value": out}
    sums = _volume_sums(series, length)[length - 1 :]
    with np.errstate(divide="ignore", invalid="ignore"):
        out[length - 1 :] = np.where(sums == 0, np.nan, length * series.v[length - 1 :] / sums)
    return {"value": out}


def _obv_indicator(series: Series, p: Mapping) -> dict:
    return _value(_obv(series))


_RUN: dict[str, Callable[[Series, Mapping], dict[str, np.ndarray]]] = {
    "sma": _sma, "wma": _wma, "vwma": _vwma, "ema": _ema, "hma": _hma, "tema": _tema,
    "rsi": _rsi, "macd": _macd, "stochastic": _stochastic, "supertrend": _supertrend,
    "bbands": _bbands, "adx": _adx, "atr": _atr, "cci": _cci, "mfi": _mfi, "cmf": _cmf,
    "williams_r": _williams_r, "donchian": _donchian, "keltner": _keltner, "rvol": _rvol,
    "obv": _obv_indicator,
}  # fmt: skip

#: The fewest bars the library accepts for each indicator; fewer and it raises, so we answer NaN.
_NEEDS: dict[str, Callable[[Mapping], int]] = {
    "sma": lambda p: p["length"], "wma": lambda p: p["length"], "vwma": lambda p: p["length"],
    "ema": lambda p: p["length"], "hma": lambda p: p["length"], "tema": lambda p: p["length"],
    "rsi": lambda p: p["length"], "macd": lambda p: p["slow"], "stochastic": lambda p: p["k"],
    "supertrend": lambda p: p["atr_length"], "bbands": lambda p: p["length"],
    "adx": lambda p: p["length"], "atr": lambda p: p["length"], "cci": lambda p: p["length"],
    "mfi": lambda p: p["length"], "cmf": lambda p: p["length"],
    "williams_r": lambda p: p["length"], "donchian": lambda p: p["length"],
    "keltner": lambda p: max(p["length"], p["atr_length"]), "rvol": lambda p: p["length"],
    "obv": lambda p: 1,
}  # fmt: skip


def names() -> tuple[str, ...]:
    """The indicators this module computes: the catalogue's library family."""
    return tuple(_RUN)


def compute(name: str, params: Mapping, series: Series) -> dict[str, np.ndarray]:
    """Every output of indicator ``name`` over ``series``; NaN where it cannot be computed.

    ``params`` is the compiled operand's settings as a mapping: ints, Fractions for decimals and
    text for choices, exactly as ``catalog`` defines them.
    """
    spec = cat.IND[name]
    if spec.family != "library":
        raise KeyError(f"{name} is not a library indicator")
    size = len(series)
    if size < _NEEDS[name](params):
        return {out: np.full(size, np.nan) for out in spec.outs}
    outputs = _RUN[name](series, params)
    return {out: outputs[out] for out in spec.outs}
