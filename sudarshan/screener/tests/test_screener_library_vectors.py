"""screener.library and screener.rolling against golden vectors and independent references.

Three kinds of evidence, each able to fail alone:

* ``vectors/library.json``: hand cases worked on a few bars by arithmetic anyone can redo, and the
  arrays of the pinned ``openalgo.ta`` on a fixed 1,000-bar set (sampled), so a bump of the pin that
  changes a value fails here and the bump's pull request changes it on purpose.
* ``library_refs.py``: textbook and Pine Script definitions written in plain Python, one window at a
  time, which the library is held to over the whole fixed set, flat stretch included, and over
  awkward series (frozen prices, repeated bars, days without volume, huge numbers).
* the defects that made four indicators the library's own: they are asserted to still be there in
  ``openalgo.ta``, so the day a bump fixes one this file says so.
"""

from __future__ import annotations

import ast
import json
import math
import random
import time
from fractions import Fraction
from pathlib import Path

import library_refs as refs
import numpy as np
import pytest
from openalgo import ta
from openalgo.indicators import _backend

from sudarshan.screener import catalog as cat
from sudarshan.screener import library
from sudarshan.screener.rolling import rolling_max, rolling_min
from sudarshan.screener.types import Candle, Series

HERE = Path(__file__).parent
VECTORS = json.loads((HERE / "vectors" / "library.json").read_text(encoding="utf-8"))
BARS = VECTORS["bars"]
SAMPLE = VECTORS["sample"]
FLAT = range(300, 325)  # the flat stretch of the fixed set
SLOW = ("highest", "lowest", "crossover", "rising", "change")


# ---- helpers --------------------------------------------------------------------------------
def candles_of(bars: dict) -> list[Candle]:
    """Candles from lists of prices in paise; what is not given follows the close, volume is 0."""
    count = len(bars["c"])
    close = bars["c"]
    column = {key: bars.get(key, close) for key in "ohl"}
    volume = bars.get("v", [0] * count)
    return [
        Candle(
            i * 900_000,
            (i + 1) * 900_000,
            20260101 + i // 25,
            column["o"][i],
            column["h"][i],
            column["l"][i],
            close[i],
            volume[i],
        )
        for i in range(count)
    ]


def series_of(bars: dict) -> Series:
    return Series.of(15, candles_of(bars))


def settings(name: str, given: dict) -> dict:
    """What the compiled operand carries: the catalogue's defaults under the given values, decimals
    as Fractions (the vectors file holds them as text)."""
    out = {}
    for param in cat.IND[name].params:
        value = given.get(param.name, param.default)
        out[param.name] = Fraction(str(value)) if param.kind == "dec" else value
    return out


def as_nan(values) -> np.ndarray:
    return np.array([math.nan if x is None else x for x in values], dtype=np.float64)


def assert_close(got, expect, *, rel=1e-9, absolute=1e-6, label=""):
    """``expect`` may hold None for NaN. NaN must meet NaN; numbers must be near."""
    got, expect = np.asarray(got, dtype=np.float64), as_nan(expect)
    assert got.shape == expect.shape, label
    same_nan = np.isnan(got) == np.isnan(expect)
    assert same_nan.all(), f"{label}: NaN differs at {np.flatnonzero(~same_nan)[:5].tolist()}"
    ok = ~np.isnan(expect)
    gap = np.abs(got[ok] - expect[ok])
    allowed = absolute + rel * np.abs(expect[ok])
    worst = int(np.argmax(gap - allowed)) if ok.any() else 0
    assert (gap <= allowed).all(), (
        f"{label}: bar {np.flatnonzero(ok)[worst]} is {got[ok][worst]!r}, expected "
        f"{expect[ok][worst]!r}"
    )


FIXED = series_of(BARS)


# ---- hand cases and pinned arrays ----------------------------------------------------------
HAND = VECTORS["hand"]


@pytest.mark.parametrize("case", HAND, ids=[f"{c['id']}-{c['name']}" for c in HAND])
def test_hand_case(case):
    """Worked by hand on a few bars: the arithmetic is in the case's note."""
    out = library.compute(case["name"], settings(case["name"], case["p"]), series_of(case["bars"]))
    assert set(out) == set(case["out"])
    for key, expect in case["out"].items():
        assert_close(out[key], expect, label=f"{case['id']} {key}")


def test_ema_five_is_ready_at_fifteen_bars():
    """I2: the first value seeds the EMA, so it exists from the first bar; it counts as ready only
    after three times its length, once the seed has worn off."""
    assert cat.IND["ema"].warmup({"length": 5}, "value") == 15
    value = library.compute("ema", settings("ema", {"length": 5}), FIXED)["value"]
    assert not np.isnan(value[0])


CASES = VECTORS["cases"]
CASE_IDS = [f"{c['id']}-{c['name']}-{i}" for i, c in enumerate(CASES)]


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_pinned_arrays(case):
    """The arrays of openalgo.ta 2.0.5 (and the library's own four) on the fixed set."""
    out = library.compute(case["name"], settings(case["name"], case["p"]), FIXED)
    assert list(out) == list(case["out"])
    for key, expect in case["out"].items():
        got = out[key]
        valid = np.flatnonzero(~np.isnan(got))
        assert (int(valid[0]) if valid.size else None) == case["first"][key], f"{key} first"
        assert_close(got[SAMPLE], expect, label=f"{case['name']} {case['p']} {key}")


def test_the_pin_is_the_version_the_vectors_were_made_with():
    from importlib.metadata import version

    assert VECTORS["pin"] == {"package": "openalgo", "version": version("openalgo")}


# ---- rolling extrema -------------------------------------------------------------------------
def test_rolling_pinned():
    pinned = VECTORS["rolling"]
    values = np.array(pinned["values"], dtype=np.float64)
    assert_close(rolling_max(values, pinned["window"]), pinned["max"], absolute=0)
    assert_close(rolling_min(values, pinned["window"]), pinned["min"], absolute=0)


def brute(values: np.ndarray, window: int, pick) -> np.ndarray:
    out = np.full(values.size, np.nan)
    for i in range(window - 1, values.size):
        chunk = values[i - window + 1 : i + 1]
        out[i] = np.nan if np.isnan(chunk).any() else pick(chunk)
    return out


@pytest.mark.parametrize("size", [0, 1, 2, 7, 20, 21, 63, 64, 65, 200])
@pytest.mark.parametrize("window", [1, 2, 3, 5, 7, 20, 64, 100, 300])
def test_rolling_matches_brute_force(size, window):
    rng = np.random.default_rng(size * 1000 + window)
    values = rng.integers(-50, 50, size).astype(np.float64)
    for got, expect in (
        (rolling_max(values, window), brute(values, window, np.max)),
        (rolling_min(values, window), brute(values, window, np.min)),
    ):
        assert got.shape == (size,)
        assert_close(got, expect, absolute=0, label=f"size {size} window {window}")


@pytest.mark.parametrize("window", [1, 3, 5, 20])
def test_rolling_gives_nan_while_a_nan_is_inside_and_recovers(window):
    rng = np.random.default_rng(window)
    values = rng.integers(0, 100, 120).astype(np.float64)
    values[[10, 11, 70]] = np.nan
    assert_close(rolling_max(values, window), brute(values, window, np.max), absolute=0)
    assert_close(rolling_min(values, window), brute(values, window, np.min), absolute=0)


def test_rolling_works_on_the_rises_and_falls_a_window_cannot_hide():
    """A single spike must leave the window exactly ``window`` bars after it came in."""
    values = np.zeros(50)
    values[10] = 9.0
    values[30] = -9.0
    top, bottom = rolling_max(values, 5), rolling_min(values, 5)
    assert top[10] == top[14] == 9.0 and top[15] == 0.0 and top[9] == 0.0
    assert bottom[30] == bottom[34] == -9.0 and bottom[35] == 0.0 and bottom[29] == 0.0


@pytest.mark.parametrize("window", [0, -1, 2.0, 3.5, "3", None, True, np.int64(3)])
def test_rolling_refuses_a_window_that_is_not_a_whole_number_of_values(window):
    with pytest.raises(ValueError, match="whole number"):
        rolling_max([1.0, 2.0, 3.0], window)
    with pytest.raises(ValueError, match="whole number"):
        rolling_min([1.0, 2.0, 3.0], window)


def test_rolling_refuses_more_than_one_series():
    with pytest.raises(ValueError, match="one series"):
        rolling_max(np.zeros((3, 3)), 2)


def test_rolling_does_not_change_its_input():
    values = np.array([3.0, 1.0, 2.0, 5.0, 4.0])
    before = values.copy()
    rolling_max(values, 2)
    rolling_min(values, 2)
    assert (values == before).all()


# ---- the library against independent references --------------------------------------------
def source_values(bars: dict, source: str) -> list:
    c = bars["c"]
    o, h, low = (bars.get(key, c) for key in "ohl")
    v = bars.get("v", [0] * len(c))
    return {
        "close": c, "open": o, "high": h, "low": low, "volume": v,
        "hl2": [(x + y) / 2 for x, y in zip(h, low, strict=True)],
        "hlc3": [(x + y + z) / 3 for x, y, z in zip(h, low, c, strict=True)],
        "ohlc4": [(w + x + y + z) / 4 for w, x, y, z in zip(o, h, low, c, strict=True)],
        "obv": refs.obv(c, v),
        "oi": [None] * len(c),  # open interest is not in these bars: every value is missing
    }[source]  # fmt: skip


def reference(name: str, p: dict, bars: dict) -> dict:
    """What an independent definition says each output of ``name`` is, None where it has none."""
    c = bars["c"]
    h, low, v = bars.get("h", c), bars.get("l", c), bars.get("v", [0] * len(c))
    n = p.get("length")
    if name in ("sma", "wma", "ema", "vwma"):
        a = source_values(bars, p["source"])
        if any(x is None for x in a):
            return {"value": [None] * len(a)}
        run = {
            "sma": lambda: refs.sma(a, n), "wma": lambda: refs.wma(a, n),
            "ema": lambda: refs.ema(a, n), "vwma": lambda: refs.vwma(a, v, n),
        }  # fmt: skip
        return {"value": run[name]()}
    if name == "hma":
        return {"value": refs.hma(c, n)}
    if name == "tema":
        return {"value": refs.tema(c, n)}
    if name == "rsi":
        return {"value": refs.rsi(c, n)}
    if name == "macd":
        line, signal, hist = refs.macd(c, p["fast"], p["slow"], p["signal"])
        return {"macd": line, "signal": signal, "hist": hist}
    if name == "stochastic":
        k, d = refs.stochastic(h, low, c, p["k"], p["smooth"], p["d"])
        return {"k": k, "d": d}
    if name == "supertrend":
        value, direction = refs.supertrend(h, low, c, p["atr_length"], float(p["factor"]))
        return {"value": value, "direction": direction}
    if name == "bbands":
        upper, middle, lower = refs.bbands(c, n, float(p["mult"]))
        pct_b, width = [], []
        for up, mid, down, close in zip(upper, middle, lower, c, strict=True):
            flat = up is None or up == down  # no spread: where the price sits in it is undefined
            pct_b.append(None if flat else (close - down) / (up - down))
            width.append(None if up is None or mid == 0 else (up - down) / mid)
        return {"upper": upper, "middle": middle, "lower": lower, "pct_b": pct_b, "width": width}
    if name == "adx":
        plus, minus, adx = refs.adx(h, low, c, n)
        return {"adx": adx, "plus_di": plus, "minus_di": minus}
    if name == "atr":
        return {"value": refs.atr(h, low, c, n)}
    if name == "cci":
        return {"value": refs.cci(h, low, c, n)}
    if name == "mfi":
        return {"value": refs.mfi(h, low, c, v, n)}
    if name == "cmf":
        return {"value": refs.cmf(h, low, c, v, n)}
    if name == "williams_r":
        return {"value": refs.williams_r(h, low, c, n)}
    if name == "donchian":
        upper, lower, middle = refs.donchian(h, low, n)
        return {"upper": upper, "lower": lower, "middle": middle}
    if name == "keltner":
        upper, middle, lower = refs.keltner(h, low, c, n, p["atr_length"], float(p["mult"]))
        return {"upper": upper, "middle": middle, "lower": lower}
    if name == "rvol":
        return {"value": refs.rvol(v, n)}
    if name == "obv":
        return {"value": refs.obv(c, v)}
    raise AssertionError(name)


#: Where a bounded indicator must stay whatever the market does: (low, high), None for no bound.
BOUNDS = {
    ("rsi", "value"): (0, 100), ("stochastic", "k"): (0, 100), ("stochastic", "d"): (0, 100),
    ("williams_r", "value"): (-100, 0), ("mfi", "value"): (0, 100), ("adx", "adx"): (0, 100),
    ("adx", "plus_di"): (0, 100), ("adx", "minus_di"): (0, 100), ("cmf", "value"): (-1, 1),
    ("atr", "value"): (0, None), ("rvol", "value"): (0, None),
}  # fmt: skip


def check_against_references(name: str, p: dict, bars: dict, series: Series, label: str):
    out = library.compute(name, p, series)
    expect = reference(name, p, bars)
    for key, got in out.items():
        finite = got[~np.isnan(got)]
        assert np.isfinite(finite).all(), f"{label} {name}.{key} holds an infinity"
        low, high = BOUNDS.get((name, key), (None, None))
        if low is not None:
            assert (finite >= low - 1e-9).all(), f"{label} {name}.{key} fell below {low}"
        if high is not None:
            assert (finite <= high + 1e-9).all(), f"{label} {name}.{key} rose above {high}"
        assert_close(got, expect[key], rel=1e-9, absolute=1e-7, label=f"{label} {name}.{key}")


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_every_pinned_case_agrees_with_its_reference_over_the_whole_set(case):
    """All 1,000 bars, the flat stretch included, not only the sampled ones."""
    p = settings(case["name"], case["p"])
    check_against_references(case["name"], p, BARS, FIXED, "fixed set")


def test_the_money_flow_index_stays_inside_zero_to_one_hundred_on_the_flat_stretch():
    """The defect that made ``mfi`` the library's own: -950 after a fall and then a flat price."""
    value = library.compute("mfi", settings("mfi", {}), FIXED)["value"]
    window = value[FLAT[0] : FLAT[-1] + 1]
    assert np.isnan(window[-1])  # 14 flat bars before: no movement, no index
    assert ((value[~np.isnan(value)] >= 0) & (value[~np.isnan(value)] <= 100)).all()


# ---- awkward series --------------------------------------------------------------------------
def awkward(kind: str, seed: int, count: int = 300) -> dict:
    """Prices in paise on a 5 paise tick, shaped to break running sums and zero denominators."""
    rng = random.Random(seed)
    base = 250_000
    c = [base]
    for i in range(1, count):
        step = rng.choice([-10, -5, 0, 5, 10]) * rng.choice([1, 1, 3])
        if kind == "frozen_runs":
            step = step if (i // 30) % 2 else 0
        elif kind == "circuit":
            step = 5 if i < count // 3 else 0
        elif kind == "gaps" and i % 50 == 0:
            step = rng.choice([-1, 1]) * base // 5
        c.append(max(5, c[-1] + step))
    c = [int(round(x / 5)) * 5 for x in c]
    spread = [rng.randint(0, 3) * 5 for _ in c]
    o = [c[0]] + c[:-1]
    h = [max(a, b) + s for a, b, s in zip(o, c, spread, strict=True)]
    low = [max(5, min(a, b) - s) for a, b, s in zip(o, c, spread, strict=True)]
    v = [rng.choice([0, 100, 1000, 5000]) for _ in c]
    if kind in ("frozen_runs", "circuit"):
        o, h, low = list(c), list(c), list(c)
    if kind == "repeated_bars":  # the same bar, with a typical price that is not a whole number
        for i in range(120, 190):
            o[i], h[i], low[i], c[i], v[i] = o[119], h[119], low[119], c[119], v[119]
    if kind in ("no_volume", "no_volume_and_frozen"):
        for i in range(100, 180):
            v[i] = 0
        if kind == "no_volume_and_frozen":
            for i in range(120, 160):
                o[i] = h[i] = low[i] = c[i] = c[119]
    if kind == "huge":  # a lakh a share and a billion shares: beyond the 64-bit sums of money flow
        o, h, low, c = ([x * 40 for x in column] for column in (o, h, low, c))
        v = [x * 200_000 for x in v]
    return {"o": o, "h": h, "l": low, "c": c, "v": v}


KINDS = ["random", "frozen_runs", "circuit", "repeated_bars", "no_volume",
         "no_volume_and_frozen", "huge", "gaps"]  # fmt: skip

#: Settings beyond the defaults, so short and odd lengths are held to the references too.
EXTRA = [
    ("hma", {"length": 14}), ("rsi", {"length": 2}), ("mfi", {"length": 3}),
    ("stochastic", {"k": 5, "d": 2, "smooth": 2}), ("macd", {"fast": 5, "slow": 10, "signal": 3}),
    ("supertrend", {"atr_length": 7, "factor": "2.5"}), ("adx", {"length": 7}),
    ("bbands", {"length": 10, "mult": "1.5"}), ("cci", {"length": 5}), ("cmf", {"length": 3}),
    ("keltner", {"length": 10, "atr_length": 5, "mult": "1.5"}), ("rvol", {"length": 3}),
]  # fmt: skip


@pytest.mark.parametrize("kind", KINDS)
def test_library_agrees_with_the_references_on_awkward_series(kind):
    bars = awkward(kind, seed=KINDS.index(kind) + 7)
    series = series_of(bars)
    for name in sorted(library.names()):
        check_against_references(name, settings(name, {}), bars, series, kind)
    for name, given in EXTRA:
        check_against_references(name, settings(name, given), bars, series, kind)


# ---- shape, readiness, sources ---------------------------------------------------------------
def test_the_library_computes_exactly_the_catalogues_library_indicators():
    wanted = {name for name, spec in cat.IND.items() if spec.family == "library"}
    assert set(library.names()) == wanted
    assert len(wanted) == 21


@pytest.mark.parametrize("name", sorted(library.names()))
def test_every_output_the_catalogue_names_comes_back_as_float64_of_the_series_length(name):
    out = library.compute(name, settings(name, {}), FIXED)
    assert list(out) == list(cat.IND[name].outs)
    for key, array in out.items():
        assert array.dtype == np.float64 and array.shape == (len(FIXED),), key


@pytest.mark.parametrize("name", sorted(library.names()))
def test_a_series_too_short_for_the_library_gives_nan_not_an_error(name):
    """The library raises ValueError for fewer bars than its length; the engine must not see it,
    at any length of series from none to well past the indicator's own."""
    params = settings(name, {})
    spec = cat.IND[name]
    needs = library._NEEDS[name](params)
    for size in range(0, 61):
        short = series_of({key: BARS[key][:size] for key in "ohlcv"})
        out = library.compute(name, params, short)
        assert list(out) == list(spec.outs)
        for key, array in out.items():
            assert array.dtype == np.float64 and array.shape == (size,), (name, key, size)
            if size < needs:
                assert np.isnan(array).all(), (name, key, size)


@pytest.mark.parametrize("name", sorted(library.names()))
def test_a_value_exists_from_the_warmup_bar_on(name):
    """The engine calls a value ready by counting bars (catalog.warmup), never by looking for NaN,
    so on an ordinary market the library must have one by then and keep one."""
    params = settings(name, {})
    quiet = series_of({key: BARS[key][: FLAT[0]] for key in "ohlcv"})  # before the flat stretch
    out = library.compute(name, params, quiet)
    for key, array in out.items():
        warm = cat.IND[name].warmup(params, key)
        assert 1 <= warm <= len(quiet), (name, key, warm)
        assert not np.isnan(array[warm - 1 :]).any(), f"{name}.{key} has no value at bar {warm}"


@pytest.mark.parametrize(
    ("source", "expect"),
    [
        ("close", lambda b: b["c"]), ("open", lambda b: b["o"]), ("high", lambda b: b["h"]),
        ("low", lambda b: b["l"]), ("volume", lambda b: b["v"]),
        ("hl2", lambda b: [(h + low) / 2 for h, low in zip(b["h"], b["l"], strict=True)]),
        ("hlc3", lambda b: [(h + low + c) / 3 for h, low, c in zip(b["h"], b["l"], b["c"], strict=True)]),
        ("ohlc4", lambda b: [(o + h + low + c) / 4 for o, h, low, c in zip(b["o"], b["h"], b["l"], b["c"], strict=True)]),
        ("obv", lambda b: refs.obv(b["c"], b["v"])),
    ],
)  # fmt: skip
def test_source_arrays(source, expect):
    got = library.source_array(FIXED, source)
    assert got.dtype == np.float64
    assert_close(got, expect(BARS), rel=1e-12, absolute=1e-9, label=source)


def test_open_interest_source_is_missing_until_a_bar_has_it():
    candles = candles_of({"c": [10, 11, 12], "v": [1, 1, 1]})
    candles[1] = candles[1]._replace(oi=500)
    candles[2] = candles[2]._replace(oi=0)
    got = library.source_array(Series.of(15, candles), "oi")
    assert np.isnan(got[0]) and got[1] == 500.0 and got[2] == 0.0  # no oi is not zero oi


def test_an_unknown_source_is_refused():
    with pytest.raises(ValueError, match="source"):
        library.source_array(FIXED, "vwap")


def test_a_price_indicator_is_not_the_librarys_to_compute():
    price_only = [name for name, spec in cat.IND.items() if spec.family != "library"]
    assert price_only  # the screener's own studies exist in the catalogue
    with pytest.raises(KeyError):
        library.compute(price_only[0], {}, FIXED)


# ---- the four indicators the library does itself --------------------------------------------
def test_the_defects_are_still_in_the_pinned_library():
    """Why ``mfi``, ``cci``, ``vwma``, ``cmf`` and ``rvol`` are computed here. Each assertion
    holds for openalgo 2.0.5; when a bump fails one, that indicator may go back to ``ta``."""
    close = np.array(BARS["c"], dtype=np.float64)
    high, low = np.array(BARS["h"], dtype=np.float64), np.array(BARS["l"], dtype=np.float64)
    volume = np.array(BARS["v"], dtype=np.float64)
    mfi = ta.mfi(high, low, close, volume, 14)
    assert (mfi[~np.isnan(mfi)] > 100).any() or (mfi[~np.isnan(mfi)] < 0).any(), "mfi is sound"

    repeated = awkward("repeated_bars", seed=KINDS.index("repeated_bars") + 7)
    h, lo, c = (np.array(repeated[key], dtype=np.float64) for key in "hlc")
    cci = ta.cci(h, lo, c, 20)
    assert np.nanmax(np.abs(cci[140:190])) > 1, "cci answers 0 for identical bars now"

    prices = np.array([100.0, 102.0, 101.0, 105.0, 103.0, 104.0])
    nothing = np.array([10.0, 20.0, 0.0, 0.0, 0.0, 0.0])
    assert list(ta.vwma(prices, nothing, 3)[4:]) == [103.0, 104.0], "vwma has no fallback now"
    assert list(ta.cmf(prices + 1, prices - 1, prices, nothing, 3)[4:]) == [0.0, 0.0]
    assert list(ta.rvol(nothing, 3)[4:]) == [1.0, 1.0], "rvol is NaN without volume now"


def test_the_librarys_own_versions_say_nothing_where_nothing_traded():
    flat_days = {"c": [100, 102, 101, 105, 103, 104], "v": [10, 20, 0, 0, 0, 0]}
    series = series_of(flat_days)
    assert np.isnan(
        library.compute("vwma", settings("vwma", {"length": 3}), series)["value"][4:]
    ).all()
    assert np.isnan(
        library.compute("cmf", settings("cmf", {"length": 3}), series)["value"][4:]
    ).all()
    assert np.isnan(
        library.compute("rvol", settings("rvol", {"length": 3}), series)["value"][4:]
    ).all()


def test_money_flow_sums_stay_exact_beyond_sixty_four_bits():
    """A lakh a share and a billion shares a bar for 300 bars is more money than int64 holds."""
    bars = awkward("huge", seed=KINDS.index("huge") + 7)
    peak = max(h + low + c for h, low, c in zip(bars["h"], bars["l"], bars["c"], strict=True))
    assert peak * max(bars["v"]) * len(bars["c"]) >= 1 << 62  # the case really is that large
    check_against_references("mfi", settings("mfi", {}), bars, series_of(bars), "huge")


# ---- the slow functions are never called -----------------------------------------------------
def screener_modules() -> list[Path]:
    return sorted(p for p in HERE.parent.glob("*.py") if p.name != "__init__.py")


def test_the_five_slow_functions_of_openalgo_ta_are_never_called():
    """They run as Python loops, 0.3 to 4 ms for 1,000 bars against 5 to 70 microseconds for the
    rest. The screener has its own: rolling (highest, lowest), price and engine (the others)."""
    found = []
    for path in screener_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in SLOW:
                text = ast.unparse(node.value)
                if text == "ta" or text.endswith(".ta"):
                    found.append(f"{path.name}:{node.lineno} {text}.{node.attr}")
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "openalgo":
                found += [
                    f"{path.name}:{node.lineno} import {a.name}"
                    for a in node.names
                    if a.name in SLOW
                ]
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "getattr":
                args = [a.value for a in node.args if isinstance(a, ast.Constant)]
                found += [f"{path.name}:{node.lineno} getattr {a}" for a in args if a in SLOW]
    assert not found, found


def test_the_ast_check_would_see_a_slow_call():
    """The check above is only worth something if it fails on what it forbids."""
    tree = ast.parse("from openalgo import ta\nx = ta.highest(c, 20)\ny = ta.crossover(a, b)\n")
    hits = [
        n.attr for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and n.attr in SLOW and ast.unparse(n.value) == "ta"
    ]  # fmt: skip
    assert hits == ["highest", "crossover"]


# ---- speed -------------------------------------------------------------------------------------
def best_microseconds(call, runs: int = 30) -> float:
    taken = []
    for _ in range(runs):
        began = time.perf_counter()
        call()
        taken.append(time.perf_counter() - began)
    return min(taken) * 1e6


def test_the_rust_core_is_in_use():
    """Without it every indicator is a Python loop and a run of 500 symbols takes seconds."""
    assert _backend.HAVE_RUST


@pytest.mark.parametrize("name", sorted(library.names()))
def test_an_indicator_over_a_thousand_bars_takes_well_under_a_millisecond(name):
    """Measured on 2 shared cores: 9 to 155 microseconds. A pure Python fallback takes 1 to 4 ms,
    so the bound is a few times the slowest, with the best of 30 runs to ignore a busy machine."""
    params = settings(name, {})
    assert best_microseconds(lambda: library.compute(name, params, FIXED)) < 800


def test_rolling_extrema_beat_the_librarys_python_loops_by_a_wide_margin():
    close = np.array(BARS["c"], dtype=np.float64)
    ours = best_microseconds(lambda: rolling_max(close, 20))
    theirs = best_microseconds(lambda: ta.highest(close, 20), runs=10)
    assert ours < 400
    assert theirs > 3 * ours, "ta.highest is no longer slow: rolling.py could be replaced"
