"""The library's ready-made screens, to review and use (M9.22).

A preset is a screen written the way the phone sends it. ``use`` checks it with ``spec.check`` and
hands back the fields of a new screen that starts Paused, so the owner reads it before anything runs.
``library`` lists the cards the Library tab shows. Pure: nothing here opens a file or reads a clock.

Eight presets can be used. "Delivery spike" is listed too, with the reason it cannot be used yet,
because it needs the daily delivery file that is not set up: "Volume spike" does the same on volume.

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
    ),
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
            "usable": item.spec is not None,
        }
        if item.spec is None:
            card.update(where=None, spec=None, reason=item.unavailable)
        else:
            stored = _stored(item, futures_followed)
            card.update(where=where(stored), spec=stored, reason=None)
        cards.append(card)
    return tuple(cards)
