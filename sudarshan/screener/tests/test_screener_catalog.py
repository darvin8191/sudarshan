"""catalog.py: the one table the checker, the engine, the words and the builder all read."""

from __future__ import annotations

import math
import re
from fractions import Fraction

import pytest

from sudarshan.screener import catalog as cat
from sudarshan.screener.types import DAY, MINUTE_TFS, TIMEFRAMES, UNITS, WEEK

# What the screener keeps, worked out from first principles (design 3.2): the history is loaded once
# as 30 calendar days of 1-minute bars (21 trading days) and 90 days of 60-minute bars (63 trading
# days); the session is 09:15 to 15:30, 375 minutes, and a short last candle still counts.
SESSION_MINUTES, CAP = 375, 1600
DAYS_30, DAYS_90 = 21, 63


def per_day(tf: int) -> int:
    return -(-SESSION_MINUTES // tf)


@pytest.mark.parametrize("tf", [1, 3, 5, 15, 30])
def test_minute_candles_are_built_from_30_days_of_1_minute_bars(tf):
    assert cat.KEPT[tf] == min(CAP, DAYS_30 * per_day(tf))


def test_60_minute_candles_come_from_90_days_of_hourly_bars():
    assert cat.KEPT[60] == DAYS_90 * per_day(60) == 441


def test_the_day_and_week_history():
    assert cat.KEPT[DAY] == CAP
    assert cat.KEPT[WEEK] == 320
    assert set(cat.KEPT) == set(TIMEFRAMES)


def test_every_timeframe_has_its_words():
    assert set(cat.TF_WORDS) == set(TIMEFRAMES)
    assert cat.candles_of(15) == "15-min candles"
    assert cat.candles_of(DAY) == "day candles"
    assert cat.candles_of(WEEK) == "week candles"


# The design's table (3.5), for the default settings and for a few that are not, written out by hand.
WARMUPS = [
    ("sma", {"length": 20}, 20), ("sma", {"length": 5}, 5),
    ("wma", {"length": 20}, 20), ("vwma", {"length": 20}, 20),
    ("ema", {"length": 20}, 60), ("ema", {"length": 5}, 15),
    ("hma", {"length": 20}, 25), ("hma", {"length": 16}, 20), ("hma", {"length": 9}, 12),
    ("hma", {"length": 10}, 14),
    ("tema", {"length": 20}, 180), ("rsi", {"length": 14}, 98), ("rsi", {"length": 2}, 14),
    ("macd", {"fast": 12, "slow": 26, "signal": 9}, 105),
    ("macd", {"fast": 5, "slow": 10, "signal": 3}, 39),
    ("stochastic", {"k": 14, "d": 3, "smooth": 3}, 20),
    ("supertrend", {"atr_length": 10, "factor": Fraction(3)}, 70),
    ("supertrend", {"atr_length": 7, "factor": Fraction(2)}, 49),
    ("bbands", {"length": 20, "mult": Fraction(2)}, 20),
    ("adx", {"length": 14}, 140), ("atr", {"length": 14}, 98),
    ("cci", {"length": 20}, 21), ("mfi", {"length": 14}, 15), ("cmf", {"length": 20}, 21),
    ("williams_r", {"length": 14}, 15), ("donchian", {"length": 20}, 20),
    ("keltner", {"length": 20, "atr_length": 10, "mult": Fraction(2)}, 70),
    ("keltner", {"length": 30, "atr_length": 5, "mult": Fraction(2)}, 90),
    ("rvol", {"length": 20}, 20), ("obv", {}, 1),
    ("highest", {"length": 20}, 20), ("lowest", {"length": 7}, 7),
    ("orb_high", {"minutes": 15}, 1), ("pivot", {"kind": "classic"}, 2),
    ("rs", {"index": "NIFTY50", "n": 20}, 21),
]  # fmt: skip


@pytest.mark.parametrize("name, params, expected", WARMUPS)
def test_warm_up_counts_are_the_designs(name, params, expected):
    assert cat.IND[name].warmup(params, cat.IND[name].default_out) == expected


def test_every_table_row_is_complete():
    for name, spec in cat.IND.items():
        assert spec.family in ("library", "price"), name
        assert spec.default_out in spec.outs, name
        assert set(spec.units) == set(spec.outs), name
        assert all(unit in ("source", *UNITS) for unit in spec.units.values()), name
        assert spec.label and spec.label[0].isupper(), name
        assert len({p.name for p in spec.params}) == len(spec.params), name
        if spec.tfs is not None:
            assert set(spec.tfs) <= set(TIMEFRAMES), name


def test_every_default_setting_passes_its_own_bounds():
    for name, spec in cat.IND.items():
        for param in spec.params:
            where = f"{name}.{param.name}"
            if param.kind == "int":
                assert type(param.default) is int, where
                assert param.low <= param.default <= param.high, where
            elif param.kind == "dec":
                assert (
                    isinstance(param.default, Fraction) and param.low <= param.default <= param.high
                )
            else:
                assert param.default in param.choices, where
                assert len(set(param.choices)) == len(param.choices), where


def test_every_indicator_is_ready_with_default_settings_on_every_minute_timeframe():
    """A screen made of defaults must always fit the history the screener keeps."""
    for name, spec in cat.IND.items():
        defaults = {p.name: p.default for p in spec.params}
        need = spec.warmup(defaults, spec.default_out) + 1  # one more for a cross
        for tf in MINUTE_TFS:
            assert need <= cat.KEPT[tf], (name, tf)


def test_the_library_indicators_are_the_ones_that_count_toward_the_limit():
    library = {name for name, spec in cat.IND.items() if spec.family == "library"}
    assert library == {
        "sma", "wma", "vwma", "ema", "hma", "tema", "rsi", "macd", "stochastic", "supertrend",
        "bbands", "adx", "atr", "cci", "mfi", "cmf", "williams_r", "donchian", "keltner", "rvol",
        "obv",
    }  # fmt: skip
    own = {name for name, spec in cat.IND.items() if spec.family == "price"}
    assert own == {"highest", "lowest", "orb_high", "orb_low", "pivot", "rs", "pattern"}


def test_units_follow_the_source_of_an_average():
    sma = cat.IND["sma"]
    assert sma.unit({"source": "close"}, "value") == "paise"
    assert sma.unit({"source": "hlc3"}, "value") == "paise"
    assert sma.unit({"source": "volume"}, "value") == "qty"
    assert sma.unit({"source": "oi"}, "value") == "qty"
    assert sma.unit({"source": "obv"}, "value") == "qty"
    assert cat.IND["rsi"].unit({"length": 14}, "value") == "num"
    assert cat.IND["obv"].unit({}, "value") == "qty"
    assert cat.IND["rs"].unit({"index": "NIFTY50", "n": 20}, "value") == "pct"
    assert cat.IND["pattern"].unit({}, "state") == "state"


def test_the_titles_read_the_way_a_trader_says_them():
    assert cat.IND["ema"].title({"length": 20}) == "EMA 20"
    assert cat.IND["macd"].title({"fast": 12, "slow": 26, "signal": 9}) == "MACD 12, 26, 9"
    assert (
        cat.IND["supertrend"].title({"atr_length": 10, "factor": Fraction(3)}) == "Supertrend 10, 3"
    )
    assert cat.IND["supertrend"].title({"atr_length": 10, "factor": Fraction(5, 2)}) == (
        "Supertrend 10, 2.5"
    )
    assert cat.IND["obv"].title({}) == "OBV"
    assert cat.IND["pivot"].title({"kind": "cpr"}) == "Pivot (cpr)"
    assert cat.IND["williams_r"].title({"length": 14}) == "Williams %R 14"


def test_pivot_lines():
    assert cat.PIVOT_OUTS["classic"] == ("P", "R1", "R2", "R3", "S1", "S2", "S3")
    assert cat.PIVOT_OUTS["cpr"] == ("P", "TC", "BC")
    assert set(cat.IND["pivot"].outs) == {*cat.PIVOT_OUTS["classic"], "TC", "BC"}


def test_patterns_and_the_candles_each_needs():
    assert set(cat.PATTERN_NEEDS) == set(cat.PATTERNS)
    assert cat.PATTERN_NEEDS == {
        "bullish_engulfing": 2, "bearish_engulfing": 2, "doji": 1, "hammer": 1,
        "shooting_star": 1, "inside_bar": 2, "nr7": 7,
    }  # fmt: skip
    assert cat.BUILDUP_STATES == (
        "long_buildup",
        "short_buildup",
        "short_covering",
        "long_unwinding",
        "none",
    )


def test_fields_and_levels():
    assert set(cat.RUNNING_LEVELS) <= set(cat.FIELDS)
    assert set(cat.DAY_LEVELS) <= set(cat.FIELDS)
    assert all(not cat.FIELDS[name].candle for name in cat.DAY_LEVELS)
    assert all(spec.unit in UNITS for spec in cat.FIELDS.values())
    assert cat.FIELDS["close"].unit == "paise" and cat.FIELDS["volume"].unit == "qty"
    assert cat.FIELDS["change_pct"].unit == "pct" and cat.FIELDS["turnover"].unit == "rupees"
    assert set(cat.PRICE_SOURCES) < set(cat.SOURCES)


def test_operators():
    assert set(cat.OPS) == {
        "gt", "gte", "lt", "lte", "eq", "between", "within_pct", "crosses_above", "crosses_below",
        "rising", "falling", "is",
    }  # fmt: skip
    assert {name for name, spec in cat.OPS.items() if spec.crossing} == {
        "crosses_above",
        "crosses_below",
    }
    assert cat.OPS["between"].args == ("low", "high")
    assert cat.OPS["rising"].args == ("n",) and cat.OPS["is"].args == ("name",)
    assert all(spec.arity in (cat.BINARY, cat.UNARY) for spec in cat.OPS.values())
    assert len({spec.words for spec in cat.OPS.values()}) == len(cat.OPS)


def test_universes_and_lists():
    assert cat.UNIVERSES == (
        "fno_stocks", "fno_futures", "index_futures", "indices", "options", "index_list",
        "watchlist",
    )  # fmt: skip
    followed = {key for key, (_, tick_by_tick) in cat.INDEX_LISTS.items() if tick_by_tick}
    assert followed == {"NIFTY50", "NIFTYBANK"}
    assert cat.INDEX_LISTS["NIFTY500"][0] == "NIFTY 500"
    assert cat.OI_UNIVERSES == {"fno_futures", "index_futures", "options", "watchlist"}


def test_option_measures():
    assert cat.OPT["pcr"].unit == "num" and cat.OPT["max_pain"].unit == "paise"
    assert cat.OPT["iv_change"].timed and not cat.OPT["pcr"].timed
    assert not cat.OPT["vix_rank"].underlying and cat.OPT["iv_rank"].underlying


def test_no_word_a_trader_reads_is_a_machine_word():
    """The platform never says arm, armed or arming where a trader reads (CLAUDE.md)."""
    machine = re.compile(r"\b(arm|armed|arming|paper|virtual)\b", re.IGNORECASE)
    words = [spec.label for spec in cat.IND.values()]
    words += [spec.words for spec in cat.OPS.values()] + [spec.words for spec in cat.OPT.values()]
    words += [spec.words for spec in cat.FIELDS.values()]
    for text in words:
        assert text and not machine.search(text), text


def test_limits():
    assert cat.NAME_MAX == 60 and cat.LIST_NAME_MAX == 64
    assert cat.SCHEMA == 1
    assert cat.MAX_RISING == 20
    assert cat.DEFAULT_GRACE_RUNS == 2 and cat.DEFAULT_MAX_ADDS == 10 and cat.DEFAULT_ALERT_CAP == 3
    assert cat.RUN_TFS == MINUTE_TFS


def test_hma_warm_up_is_length_plus_the_rounded_up_square_root():
    for length in range(1, 400):
        assert cat.IND["hma"].warmup({"length": length}, "value") == length + math.ceil(
            math.sqrt(length)
        )
