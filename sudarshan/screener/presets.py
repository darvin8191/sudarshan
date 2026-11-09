"""The library's ready-made screens, to review and use (M9.22).

A preset is a screen written the way the phone sends it. ``use`` checks it with ``spec.check`` and
hands back the fields of a new screen that starts Paused, so the owner reads it before anything runs.
``library`` lists the cards the Library tab shows. Pure: nothing here opens a file or reads a clock.

Six presets can be judged in this release: ORB breakout, VWAP reclaim and four from the dashboards
study (the opening-range follow-through up and down, opened at the low, opened at the high). Eight
more are written out and listed with what they wait for (``waits``): history before today or a
daily store of open interest and implied volatility, both waiting for the feed's REST governor
(release review MEDIUM-6). "Delivery spike" is listed too, with the reason it cannot be used yet,
because it needs the daily delivery file that is not set up: "Volume spike" does the same on
volume.

The six presets of the dashboards study (design 3.10) port technical scans in the order the study
listed them; each one's about says, in our own words, every way it differs from the usual form of
its scan, and none carries another product's name or wording.

The three build-up presets read stock futures. Until the price feed follows those futures all day
they are checked once, after the close; ``use(key, futures_followed=True)`` gives the copy that is
checked at every 15-minute close instead. Nothing else about a preset depends on the feed.
"""

from __future__ import annotations

from dataclasses import dataclass

from sudarshan.screener import catalog as cat
from sudarshan.screener import spec

__all__ = ["PAUSED", "PRESETS", "Preset", "Unavailable", "library", "preset", "use", "where"]

#: What a screen made from a preset starts as.
PAUSED = "paused"
NOT_IN_LIBRARY = "That preset is not in the library."
NO_DELIVERY_FILE = (
    "Needs the daily delivery file, not set up yet. Volume spike does the same on volume."
)
#: Presets this release cannot judge yet (release review MEDIUM-6): they read history before today
#: or a daily store, which wait for the feed's REST governor (SV3, SV4). Each says what it waits for.
WAITS_HISTORY_15 = (
    "Waits for the history download, not set up yet: needs 151 candles of 15 minutes, and a day "
    "gives 25."
)
WAITS_HISTORY_DAYS = (
    "Waits for the history download, not set up yet: needs 20 days of candles. NIFTY 500 is "
    "also checked only after the close."
)
WAITS_OI = "Waits for yesterday's open interest, which the daily store will keep: not set up yet."
WAITS_IV = (
    "Waits for the implied volatility history, which the daily store will keep: not set up yet."
)
WAITS_SUPERTREND = (
    "Waits for the history download, not set up yet: needs 50 day candles and 49 candles of "
    "60 minutes, and a day gives 1 and 7."
)
WAITS_THREE_DAYS = (
    "Waits for the history download, not set up yet: needs 3 day candles, and a day gives 1."
)


class Unavailable(ValueError):
    """A preset that cannot be used; ``message`` is for the owner, ``key`` is the preset asked for."""

    def __init__(self, key: str, message: str):
        super().__init__(message)
        self.key = key
        self.message = message


@dataclass(frozen=True, slots=True)
class Preset:
    key: str
    name: str
    about: str
    #: The screen as the phone sends it; None for a preset that cannot be used yet.
    spec: dict | None = None
    #: Why it cannot be used yet.
    unavailable: str | None = None
    #: The run once the price feed follows the stock futures all day; None: the run never changes.
    followed_run: dict | None = None
    #: What a preset this release cannot judge yet waits for (history, a daily store); None: usable.
    waits: str | None = None


# ---- the pieces the presets are made of --------------------------------------------------------
_AFTER_CLOSE = {"on": "after_close"}
_ALSO_VALUE = {"columns": ["ltp", "change_pct", "value"]}

_BUILDUP = {"oi": "buildup", "window": {"kind": "day"}}
_OI_CHANGE = {"oi": "oi_change_pct", "window": {"kind": "day"}}
_IV_PCT = {"opt": "iv_pct", "underlying": "self"}
_AVERAGE_VOLUME = {"ind": "sma", "p": {"length": 20, "source": "volume"}}
_CLOSE, _VOLUME, _VWAP = {"field": "close"}, {"field": "volume"}, {"field": "vwap"}


def _number(text: str) -> dict:
    return {"num": text}


def _times(factor: str) -> dict:
    """Twenty-candle average volume times ``factor``."""
    return {"math": "mul", "args": [_AVERAGE_VOLUME, _number(factor)]}


def _state(left: dict, name: str) -> dict:
    return {"left": left, "op": "is", "args": {"name": name}}


def _compare(left: dict, op: str, right: dict) -> dict:
    return {"left": left, "op": op, "right": right}


# ---- the dashboards study's presets (design 3.10) ------------------------------------------------
_CHANGE = {"field": "change_pct", "tf": "D"}
_DAY_OPEN, _DAY_HIGH, _DAY_LOW = {"field": "day_open"}, {"field": "day_high"}, {"field": "day_low"}


def _line(cid: str, left: dict, op: str, right: dict, on: bool = True) -> dict:
    return {"id": cid, "on": on, "left": left, "op": op, "right": right}


def _first_5(name: str) -> dict:
    """The high or low of the day's first 5-min candle."""
    return {"field": name, "tf": 5, "at": "first_today"}


#: The opening range: the day's first 5-min candle's high less its low.
_RANGE = {"math": "sub", "args": [_first_5("high"), _first_5("low")]}
_DAY = {"tf": "D"}
_SUPERTREND = {"ind": "supertrend", "p": {"atr_length": 7, "factor": "3"}}


def _study(key: str, name: str, about: str, universe_run: tuple, where: list, order: str,
           waits: str | None = None) -> Preset:  # fmt: skip
    universe, run = universe_run
    return Preset(
        key=key,
        name=name,
        about=about,
        spec={
            "name": name,
            "universe": universe,
            "run": run,
            "where": {"all": where},
            "rank": {"by": _CHANGE, "order": order},
            "alerts": _ALSO_VALUE,
        },
        waits=waits,
    )


_FNO = {"kind": "fno_stocks"}
_STUDY: tuple[Preset, ...] = (
    _study(
        "orb_follow_up", "Opening-range follow-through, up",
        "F&O stocks whose 5-min close is more than one opening range above the high of the day's "
        "first 5-min candle; the range is that candle's high less its low. The first check that "
        "can match is 09:25. Differs from the usual form of this scan: F&O stocks, the stocks "
        "followed tick by tick, in place of NIFTY 200; each closed 5-min candle is judged, not the "
        "one still forming, so a match never vanishes inside its candle; ranked by the day's % "
        "change against yesterday's close, largest first; shows the LTP, % change and the line's "
        "value. A stock first followed after 09:15 reads Not checked, since its first candle was "
        "not seen.",
        (_FNO, {"on": "close", "tf": 5}),
        [_line("c1", {"field": "close", "tf": 5}, "gt",
               {"math": "add", "args": [_first_5("high"), _RANGE]})],
        "desc",
    ),
    _study(
        "orb_follow_down", "Opening-range follow-through, down",
        "F&O stocks whose 5-min close is more than one opening range below the low of the day's "
        "first 5-min candle; the range is that candle's high less its low. The first check that "
        "can match is 09:25. Differs from the usual form of this scan: F&O stocks in place of "
        "NIFTY 200; each closed 5-min candle is judged, not the one still forming; ranked by the "
        "day's % change with the largest fall first, where the usual form lists the largest rise "
        "first; shows the LTP, % change and the line's value. A stock first followed after 09:15 "
        "reads Not checked, since its first candle was not seen.",
        (_FNO, {"on": "close", "tf": 5}),
        [_line("c1", {"field": "close", "tf": 5}, "lt",
               {"math": "sub", "args": [_first_5("low"), _RANGE]})],
        "asc",
    ),
    _study(
        "open_at_low", "Opened at the low, strong start",
        "F&O stocks that opened at their low of the day so far and above yesterday's close, whose "
        "latest 3-min candle opened at its low and closed higher. The day's open, high and low are "
        "the exchange's, the pre-open price as the open. Differs from the usual form of this scan: "
        "F&O stocks in place of NIFTY 200; the 3-min lines judge each closed candle, not the one "
        "still forming; two lines, yesterday's close above its 200-day average and a 15-day "
        "average volume above 10 lakh, are written in and switched off until the history "
        "download; the body test, (close - open) / (close - low) of 0.7 or more, is written as "
        "close above open, which finds the same stocks while the 3-min open is its low and stops "
        "meaning the same if that line is switched off; ranked by the day's % change, largest "
        "first.",
        (_FNO, {"on": "close", "tf": 3}),
        [
            _line("c1", _DAY_OPEN, "eq", _DAY_LOW),
            _line("c2", _DAY_OPEN, "gt", {"field": "prev_close"}),
            _line("c3", {"field": "open", "tf": 3}, "eq", {"field": "low", "tf": 3}),
            _line("c4", {"field": "close", "tf": 3}, "gt", {"field": "open", "tf": 3}),
            _line("c5", {"field": "close", **_DAY, "at": {"ago": 1}}, "gt",
                  {"ind": "sma", "p": {"length": 200, "source": "close"}, **_DAY,
                   "at": {"ago": 1}}, on=False),
            _line("c6", {"ind": "sma", "p": {"length": 15, "source": "volume"}, **_DAY}, "gt",
                  _number("1000000"), on=False),
        ],
        "desc",
    ),
    _study(
        "open_at_high", "Opened at the high, weak start",
        "F&O stocks that opened at their high of the day so far, with more than 5 lakh shares "
        "traded today and a day low above 100 rupees. Checked on the forming day candle at each "
        "15-minute close; the day's open, high, low and volume are the exchange's, the pre-open "
        "included. Differs from the usual form of this scan: F&O stocks during the day in place "
        "of NIFTY 200, whose copy checked after the close comes with that list; ranked by the "
        "day's % change with the largest fall first, where the usual form lists the largest rise "
        "first.",
        (_FNO, {"on": "close", "tf": 15}),
        [
            _line("c1", _DAY_OPEN, "eq", _DAY_HIGH),
            _line("c2", {"field": "volume", **_DAY}, "gt", _number("500000")),
            _line("c3", _DAY_LOW, "gt", _number("100")),
        ],
        "asc",
    ),
    _study(
        "supertrend_turn", "Supertrend turn with volume, hourly confirmed",
        "F&O stocks whose day close crosses above Supertrend 7, 3 (yesterday at or below it), on "
        "volume above the 7-day average, with the 60-min close above its own Supertrend 7, 3. "
        "Differs from the usual form of this scan: F&O stocks in place of every listed stock; no "
        "company-value filter (the usual form asks for more than 1,500 crore), as none is "
        "expected to remove an F&O stock and SUDARSHAN has no source of company values yet; "
        "Supertrend averages the true range Wilder's way, the common charting rule, and a scan "
        "that averages it otherwise can differ slightly; ranked by the day's % change, largest "
        "first.",
        (_FNO, {"on": "close", "tf": 15}),
        [
            _line("c1", {"field": "close", **_DAY, "at": {"ago": 1}}, "lte",
                  {**_SUPERTREND, **_DAY, "at": {"ago": 1}}),
            _line("c2", {"field": "close", **_DAY}, "gt", {**_SUPERTREND, **_DAY}),
            _line("c3", {"field": "volume", **_DAY}, "gt",
                  {"ind": "sma", "p": {"length": 7, "source": "volume"}, **_DAY}),
            _line("c4", {"field": "close", "tf": 60}, "gt", {**_SUPERTREND, "tf": 60}),
        ],
        "desc",
        WAITS_SUPERTREND,
    ),
    _study(
        "two_day_reversal", "Two-day reversal from an opening low",
        "F&O stocks that opened at the day's low, below the lows of the two days before, and now "
        "trade above the highs of both. Checked on the forming day candle at each 15-minute "
        "close; the day's open and low are the exchange's. Differs from the usual form of this "
        "scan: F&O stocks in place of every listed stock, whose stand-in, a NIFTY 500 copy "
        "checked after the close, comes with that list; no company-value filter (the usual form "
        "asks for more than 200 crore), as none is expected to remove an F&O stock and SUDARSHAN "
        "has no source of company values yet; ranked by the day's % change, largest first.",
        (_FNO, {"on": "close", "tf": 15}),
        [
            _line("c1", _DAY_OPEN, "eq", _DAY_LOW),
            _line("c2", _DAY_OPEN, "lt", {"field": "prev_day_low"}),
            _line("c3", _DAY_OPEN, "lt", {"field": "low", **_DAY, "at": {"ago": 2}}),
            _line("c4", {"field": "close", **_DAY}, "gt", {"field": "prev_day_high"}),
            _line("c5", {"field": "close", **_DAY}, "gt", {"field": "high", **_DAY, "at": {"ago": 2}}),
        ],
        "desc",
        WAITS_THREE_DAYS,
    ),
)  # fmt: skip


def _buildup(name: str, state: str, op: str, percent: str, order: str, about: str) -> Preset:
    """A build-up of the day: its state, and open interest moved by at least ``percent``."""
    return Preset(
        key=state,
        name=name,
        about=about,
        spec={
            "name": name,
            "universe": {"kind": "fno_futures"},
            "run": _AFTER_CLOSE,
            "where": {"all": [_state(_BUILDUP, state), _compare(_OI_CHANGE, op, _number(percent))]},
            "rank": {"by": _OI_CHANGE, "order": order},
            "alerts": _ALSO_VALUE,
        },
        followed_run={"on": "close", "tf": 15},
        waits=WAITS_OI,
    )


PRESETS: tuple[Preset, ...] = (
    _buildup(
        "Long build-up", "long_buildup", "gte", "2", "desc",
        "Stock futures with the price up and open interest up 2% or more since yesterday's close.",
    ),
    _buildup(
        "Short build-up", "short_buildup", "gte", "2", "desc",
        "Stock futures with the price down and open interest up 2% or more since yesterday's close.",
    ),
    _buildup(
        "Short covering", "short_covering", "lte", "-2", "asc",
        "Stock futures with the price up and open interest down 2% or more since yesterday's close.",
    ),
    Preset(
        key="iv_extremes",
        name="IV extremes",
        about=(
            "NIFTY 50 and NIFTY BANK when implied volatility is at its 80th percentile or higher, "
            "or at its 20th or lower. India VIX itself is not checked."
        ),
        spec={
            "name": "IV extremes",
            "universe": {"kind": "indices"},
            "run": _AFTER_CLOSE,
            "where": {
                "any": [
                    _compare(_IV_PCT, "gte", _number("80")),
                    _compare(_IV_PCT, "lte", _number("20")),
                ]
            },
            "rank": {"by": _IV_PCT, "order": "desc"},
            "alerts": _ALSO_VALUE,
        },
        waits=WAITS_IV,
    ),
    Preset(
        key="orb_breakout",
        name="ORB breakout",
        about=(
            "Stocks whose 5-min close crosses above the high of the first 15 minutes, on volume "
            "above 1.5 times its 20-candle average. Nothing shows before 09:30, when that range "
            "is complete."
        ),
        spec={
            "name": "ORB breakout",
            "universe": {"kind": "fno_stocks"},
            "run": {"on": "close", "tf": 5},
            "where": {
                "all": [
                    _compare(_CLOSE, "crosses_above", {"ind": "orb_high", "p": {"minutes": 15}}),
                    _compare(_VOLUME, "gt", _times("1.5")),
                ]
            },
        },
    ),
    Preset(
        key="ema_cross_15",
        name="15-min EMA cross",
        about=(
            "Stocks where the 15-min EMA 20 crosses above EMA 50 while RSI 14 is above 50 and the "
            "price is above VWAP."
        ),
        spec={
            "name": "15-min EMA cross",
            "universe": {"kind": "fno_stocks"},
            "run": {"on": "close", "tf": 15},
            "where": {
                "all": [
                    _compare(
                        {"ind": "ema", "p": {"length": 20}},
                        "crosses_above",
                        {"ind": "ema", "p": {"length": 50}},
                    ),
                    _compare({"ind": "rsi", "p": {"length": 14}}, "gt", _number("50")),
                    _compare(_CLOSE, "gt", _VWAP),
                ]
            },
        },
        waits=WAITS_HISTORY_15,
    ),
    Preset(
        key="vwap_reclaim",
        name="VWAP reclaim",
        about=(
            "Stocks whose 5-min close crosses above VWAP on volume above its 20-candle average."
        ),
        spec={
            "name": "VWAP reclaim",
            "universe": {"kind": "fno_stocks"},
            "run": {"on": "close", "tf": 5},
            "where": {
                "all": [
                    _compare(_CLOSE, "crosses_above", _VWAP),
                    _compare(_VOLUME, "gt", _AVERAGE_VOLUME),
                ]
            },
        },
    ),
    Preset(
        key="delivery_spike",
        name="Delivery spike",
        about="Stocks where the share of shares taken for delivery jumps well above its usual level.",
        unavailable=NO_DELIVERY_FILE,
    ),
    Preset(
        key="volume_spike",
        name="Volume spike",
        about=(
            "NIFTY 500 stocks whose volume for the day is at least twice its 20-day average and "
            "that closed above the previous close. Checked after the close."
        ),
        spec={
            "name": "Volume spike",
            "universe": {"kind": "index_list", "id": "NIFTY500"},
            "run": _AFTER_CLOSE,
            "where": {
                "all": [
                    _compare(_VOLUME, "gte", _times("2")),
                    _compare(_CLOSE, "gt", {"field": "prev_close"}),
                ]
            },
            "rank": {"by": {"ind": "rvol", "p": {"length": 20}}, "order": "desc"},
            "alerts": _ALSO_VALUE,
        },
        waits=WAITS_HISTORY_DAYS,
    ),
    *_STUDY,
)  # fmt: skip

_BY_KEY = {item.key: item for item in PRESETS}
_GROUPS = {
    "fno_stocks": "F&O stocks",
    "fno_futures": "F&O stock futures",
    "index_futures": "Index futures",
    "indices": "Indices",
}


def preset(key: str) -> Preset:
    """The preset called ``key``, or ``Unavailable``."""
    item = _BY_KEY.get(key) if key.__class__ is str else None
    if item is None:
        raise Unavailable(str(key), NOT_IN_LIBRARY)
    return item


def where(stored: dict) -> str:
    """Where and when a checked screen looks: 'F&O stocks · 15-min', 'NIFTY 500 · after the close'."""
    universe, run = stored["universe"], stored["run"]
    kind = universe["kind"]
    if kind == "index_list":
        group = cat.INDEX_LISTS[universe["id"]][0]
    elif kind == "options":
        group = f"{universe['underlying']} option strikes"
    elif kind == "watchlist":
        group = "A watchlist"
    else:
        group = _GROUPS[kind]
    when = "after the close" if run["on"] == "after_close" else cat.TF_WORDS[run["tf"]]
    return f"{group} · {when}"


def _stored(item: Preset, futures_followed: bool) -> dict:
    fields = dict(item.spec)
    if futures_followed and item.followed_run is not None:
        fields["run"] = item.followed_run
    return spec.check(fields)


def use(key: str, *, futures_followed: bool = False) -> dict:
    """The fields of a new screen made from a preset: its name, its checked screen, Paused."""
    item = preset(key)
    if item.spec is None:
        raise Unavailable(item.key, item.unavailable)
    stored = _stored(item, futures_followed)
    return {"name": stored["name"], "spec": stored, "status": PAUSED}


def library(*, futures_followed: bool = False) -> tuple[dict, ...]:
    """The cards of the Library tab, in the order shown."""
    cards = []
    for item in PRESETS:
        card = {
            "key": item.key,
            "name": item.name,
            "about": item.about,
            "usable": item.spec is not None and item.waits is None,
        }
        if item.spec is None:
            card.update(where=None, spec=None, reason=item.unavailable)
        else:
            stored = _stored(item, futures_followed)
            card.update(where=where(stored), spec=stored, reason=item.waits)
        cards.append(card)
    return tuple(cards)
