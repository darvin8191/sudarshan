"""Risk and X-ray, and the owner's settings for it (phase P7, lane R: M7.32).

``risk`` answers ``RiskReport`` (types.ts) for one custom tab, or for every custom tab together
when no tab is named; ``settings_get`` and ``settings_set`` read and write the five settings the
figures use (``kinds.SETTING_KEYS``). All three wait for ``SUDARSHAN_PORTFOLIO_INSIGHTS``
(``ops.switched``) and are the owner's alone: none is a family op.

This module translates and never decides: the rows of the store become the arguments of
``core/risk.py``, and what it answers is what is sent. The series figures rest on closing values
(the same daily series the Performance view draws); the concentration bars, the overlap and the
emergency fund on what each holding is worth now.
"""

from __future__ import annotations

from collections.abc import Mapping
from fractions import Fraction
from typing import Any

from ..core import history, performance, risk, valuation, words
from ..core.days import day_of_instant
from ..core.filters import Filters
from ..core.kinds import HOLDING_KINDS, MAX_PAISE, SETTING_KEYS
from ..core.money import format_pct
from ..store import Invalid, NotFound, Tx, VersionConflict
from . import op
from .common import answer, need, parts, scope_now, warmed, whole

#: What a setting may be, in the owner's words when it is not.
RATE_MAX_BP = 5000
MONTHS_MAX = 120
SAYS = {
    "unknown": "That is not a setting of this page. Reload the page.",
    "risk_free_bp": "The risk-free rate must be between 0% and 50% a year.",
    "max_holding_pct": "The limit for one holding must be above 0% and at most 100%.",
    "max_sector_pct": "The limit for one sector must be above 0% and at most 100%.",
    "monthly_expenses_paise": "Monthly expenses must be an amount above zero, or left empty.",
    "months_target": "The emergency fund target must be between 1 and 120 months.",
    "empty": "Nothing was changed. Change a setting, then save.",
}


def _int(value: Any, low: int, high: int) -> bool:
    return value.__class__ is int and low <= value <= high


def _limit(value: Any) -> str | None:
    """A limit as the text of four decimals it is kept as, from a number or a text; None when it
    is not above 0 and at most 100, or has more than four decimals."""
    if value.__class__ is int:
        value = str(value)
    if value.__class__ is not str:
        return None
    try:
        scaled = Fraction(value.strip()) * 10_000
    except (ValueError, ZeroDivisionError):
        return None
    if scaled.denominator != 1 or not 0 < scaled <= 1_000_000:
        return None
    return format_pct(int(scaled))


def checked(values: Any) -> dict[str, Any]:
    """The settings a request carries, each as it is kept; the owner's sentence for the first
    that cannot be."""
    if not isinstance(values, Mapping) or not values:
        raise Invalid(SAYS["empty"])
    found: dict[str, Any] = {}
    for name, value in values.items():
        if name not in SETTING_KEYS:
            raise Invalid(SAYS["unknown"])
        if name == "risk_free_bp":
            ok = _int(value, 0, RATE_MAX_BP)
        elif name == "months_target":
            ok = _int(value, 1, MONTHS_MAX)
        elif name == "monthly_expenses_paise":
            ok = value is None or _int(value, 1, MAX_PAISE)
        else:
            value = _limit(value)
            ok = value is not None
        if not ok:
            raise Invalid(SAYS[name])
        found[name] = value
    return found


def settings_of(tx: Tx) -> dict[str, Any]:
    """``OwnerSettings`` (types.ts): each setting or its default, and one version for the five
    (the sum of the rows' own, so that any change to any of them moves it on)."""
    out: dict[str, Any] = dict(risk.DEFAULTS)
    version = 0
    for name in SETTING_KEYS:
        row = tx.setting_get(name)
        if row is not None:
            out[name] = row["value"]
            version += row["version"]
    return {**out, "version": version}


def settings_put(tx: Tx, values: Mapping[str, Any], version: int, now: int) -> dict[str, Any]:
    """Write the settings named, only from the version the five were read at."""
    if settings_of(tx)["version"] != version:
        raise VersionConflict("version_conflict_item")
    for name, value in values.items():
        row = tx.setting_get(name)
        tx.setting_put(name, value, 0 if row is None else row["version"], now)
    return settings_of(tx)


@op("settings_get")
async def settings_get(ctx, message):
    store, scope = need(ctx), scope_now()
    return await answer(lambda: store.read(scope, settings_of))


@op("settings_set")
async def settings_set(ctx, message):
    store, scope, now = need(ctx), scope_now(), ctx.clock()
    version = whole(message, "version")

    def run():
        values = checked(message.get("values"))
        return store.write(scope, lambda tx: settings_put(tx, values, version, now))

    return await answer(run)


# ---- the report ------------------------------------------------------------------------------


def _custom(tx: Tx) -> list[dict[str, Any]]:
    """The owner's own tabs that are in use: not the Upstox tab, not an archived one."""
    return [
        tab
        for tab in tx.rows("tab", "position,id")
        if not (tab["system"] or tab["kind"] == "upstox" or tab["archived"])
    ]


def _holdings(live) -> list[risk.Holding]:
    """What a tab holds now, one line per asset: its value at the price, or its cost without one
    (as the holdings list and the allocation bars count it). The asset is the instrument: the
    same asset in another tab is the same holding."""
    found = []
    for aid in live.rows_by:
        asset = live.assets[aid]
        if asset["kind"] in HOLDING_KINDS:
            value = valuation.row_figures(live.core[aid]).value_paise
            found.append(risk.Holding(str(aid), asset["name"], value, live.facts[aid].sector))
    return found


def _liquid(tabs: list[dict[str, Any]], lives: Mapping[int, Any]) -> int:
    """Cash-like value: all of a tab of the kind FD and cash, and elsewhere the assets of the
    class cash."""
    total = 0
    for tab in tabs:
        live = lives[tab["id"]]
        for aid in live.rows_by:
            if tab["kind"] == "cash" or live.facts[aid].asset_class == "cash":
                total += max(0, valuation.row_figures(live.core[aid]).value_paise)
    return total


def _daily(views, tx: Tx, tab: Mapping[str, Any], live, today: int) -> history.Daily:
    """The tab's daily series, from the cache the Performance view fills (same key)."""
    key = (tab["id"], tab["version"], today, views.prices.stamp(tx))
    days = views.cache.get(key)
    if days is None:
        ledger = live.ledger
        used = [live.assets[a] for a in sorted({a for (_, a) in ledger.positions if a is not None})]
        used = [a for a in used if a["kind"] in HOLDING_KINDS]
        quotes = {aid: history.Quotes.of(q) for aid, q in views.prices.closes(tx, used).items()}
        days = history.daily(ledger, quotes, today)
        views.cache.put(key, days)
    return days


def report(views, tx: Tx, tab_id: int | None, now: int) -> dict[str, Any]:
    """``RiskReport`` (types.ts) of the tab ``tab_id``, or of every custom tab as one."""
    today = day_of_instant(now)
    tabs = _custom(tx)
    if tab_id is not None and tab_id not in {tab["id"] for tab in tabs}:
        raise NotFound()
    settings = settings_of(tx)
    lives = {tab["id"]: views._live(tx, tab, Filters(), today, now) for tab in tabs}
    asked = [tab for tab in tabs if tab_id is None or tab["id"] == tab_id]
    series = [_daily(views, tx, tab, lives[tab["id"]], today) for tab in asked]
    points, opening = risk.together([days.points for days in series])
    periods = performance.periods(points, opening)
    figures = risk.series_figures(
        periods,
        performance.twr(points, opening).annualised_pct,
        settings["risk_free_bp"],
        views.prices.holidays(),
    )
    mine = [each for tab in asked for each in _holdings(lives[tab["id"]])]
    figures["concentration"] = risk.concentration(
        mine, settings["max_holding_pct"], settings["max_sector_pct"]
    )
    figures["overlap"] = (
        []
        if tab_id is None
        else [
            risk.overlap(tab["id"], mine, _holdings(lives[tab["id"]]))
            for tab in tabs
            if tab["id"] != tab_id
        ]
    )
    figures["emergency"] = risk.emergency(
        _liquid(tabs, lives), settings["monthly_expenses_paise"], settings["months_target"]
    )
    last = periods[-1].day if periods else None
    return {
        "tab_id": tab_id,
        "as_of_day": last or today,
        "basis": words.risk_basis(last) if last else words.SERIES_WAITING,
        "risk": figures,
    }


@op("risk")
async def risk_report(ctx, message):
    await warmed(ctx)
    _, views = parts(ctx)
    now, scope = ctx.clock(), scope_now()
    tab_id = None if message.get("tab") is None else whole(message, "tab")
    return await answer(lambda: views.store.read(scope, lambda tx: report(views, tx, tab_id, now)))
