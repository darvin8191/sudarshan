"""M7.32 (P7 lane R, R4): the risk report and the owner's settings through the sealed API."""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta

from test_portfolio_import_ops import NOW, OWNER, Rig, scenario_of  # noqa: F401  (the rig)
from test_portfolio_risk import holdings_of_rows
from test_portfolio_service import settings_for
from test_portfolio_tab_ops import clean

from sudarshan.portfolio.core import kinds, risk
from sudarshan.portfolio.core.performance import Point, periods
from sudarshan.portfolio.ops import REGISTRY

TODAY = 20261026  # the rig's day, a Monday
DEFAULTS = {**risk.DEFAULTS, "version": 0}


def on(rig, tmp_path=None):
    rig.svc.settings = dataclasses.replace(rig.svc.settings, insights=True)
    return rig


def weekdays_to(last: date, count: int) -> list[int]:
    found, day = [], last
    while len(found) < count:
        if day.weekday() < 5:
            found.append(day.year * 10000 + day.month * 100 + day.day)
        day -= timedelta(days=1)
    return found[::-1]


def manual(rig, name, request, **more):
    return rig.book.asset_create(OWNER, request, "manual", now=NOW, name=name, **more)


def hold(rig, tab, asset, rupees, day, request):
    """Buy one unit of a manual asset for ``rupees`` on ``day``."""
    version = next(t for t in rig.book.tabs(OWNER) if t["id"] == tab["id"])["version"]
    entry = {"kind": "BUY", "trade_date": day, "account_id": rig.account["id"],
             "asset_id": asset["id"], "qty_u": 1_000_000, "amount_paise": rupees * 100}  # fmt: skip
    rig.book.activity_create(OWNER, tab["id"], version, entry, request, now=NOW)


def price(rig, asset, pairs):
    def run(tx):
        for day, rupees in pairs:
            tx.set_price_point(asset["id"], day, rupees * 10_000, NOW)

    rig.svc.store.write(OWNER, run)


def test_the_three_ops_are_built_and_wait_for_the_insights_switch():
    assert {"risk", "settings_get", "settings_set"} <= set(REGISTRY)
    assert {kinds.SWITCH_OF[name] for name in ("risk", "settings_get", "settings_set")} == {
        "insights"
    }
    assert not {"risk", "settings_get", "settings_set"} & set(kinds.FAMILY_OPS)


@scenario_of
async def test_while_the_switch_is_off_each_answers_not_ready(rig):
    for name, args in (("risk", {}), ("settings_get", {}), ("settings_set", {"version": 0})):
        assert (await rig.call(name, **args))["code"] == "not_ready", name


@scenario_of
async def test_the_settings_start_at_the_owners_defaults_and_are_changed_by_version(rig):
    on(rig)
    assert (await rig.call("settings_get"))["data"] == DEFAULTS
    done = await rig.call(
        "settings_set", version=0, values={"risk_free_bp": 700, "max_holding_pct": "12.5"}
    )
    assert done["data"] == {
        **DEFAULTS, "risk_free_bp": 700, "max_holding_pct": "12.5000", "version": 2
    }  # fmt: skip
    stale = clean(await rig.call("settings_set", version=0, values={"months_target": 9}))
    assert stale["code"] == "version_conflict"
    assert (await rig.call("settings_get"))["data"]["months_target"] == 6
    more = await rig.call(
        "settings_set", version=2,
        values={"monthly_expenses_paise": 4_000_000, "months_target": 9, "max_sector_pct": 40},
    )  # fmt: skip
    assert more["data"] == {
        "risk_free_bp": 700, "max_holding_pct": "12.5000", "max_sector_pct": "40.0000",
        "monthly_expenses_paise": 4_000_000, "months_target": 9, "version": 5,
    }  # fmt: skip
    unset = await rig.call(
        "settings_set", version=5, values={"monthly_expenses_paise": None, "risk_free_bp": 650}
    )
    assert unset["data"]["monthly_expenses_paise"] is None and unset["data"]["version"] == 7
    assert list(unset["data"]) == [*kinds.SETTING_KEYS, "version"]


@scenario_of
async def test_a_setting_that_cannot_be_is_refused_in_the_owners_words_and_nothing_is_kept(rig):
    on(rig)
    for values, said in (
        ({"risk_free_bp": 5001}, "between 0% and 50%"),
        ({"risk_free_bp": -1}, "between 0% and 50%"),
        ({"risk_free_bp": 6.5}, "between 0% and 50%"),
        ({"risk_free_bp": True}, "between 0% and 50%"),
        ({"max_holding_pct": "0"}, "one holding"),
        ({"max_holding_pct": "100.0001"}, "one holding"),
        ({"max_holding_pct": "12.34567"}, "one holding"),
        ({"max_holding_pct": 12.5}, "one holding"),
        ({"max_sector_pct": "lots"}, "one sector"),
        ({"max_sector_pct": "1/0"}, "one sector"),
        ({"monthly_expenses_paise": 0}, "Monthly expenses"),
        ({"monthly_expenses_paise": "40000"}, "Monthly expenses"),
        ({"months_target": 0}, "between 1 and 120"),
        ({"months_target": 121}, "between 1 and 120"),
        ({"capital": 1}, "not a setting"),
        ({"risk_free_bp": 700, "months_target": 0}, "between 1 and 120"),
        ({}, "Nothing was changed"),
        (None, "Nothing was changed"),
    ):
        refused = clean(await rig.call("settings_set", version=0, values=values))
        assert refused["code"] == "bad_request" and said in refused["message"], values
    assert (await rig.call("settings_get"))["data"] == DEFAULTS


@scenario_of
async def test_a_new_tab_has_no_figure_yet_and_every_tile_says_why(rig):
    on(rig)
    data = (await rig.call("risk", tab=rig.tab["id"]))["data"]
    assert data["tab_id"] == rig.tab["id"] and data["as_of_day"] == TODAY
    assert data["basis"] == "Daily history starts once every holding here has a closing price."
    figures = data["risk"]
    wait = "Shown after 60 trading days of closing values: 60 more to go."
    assert figures["days"] == 0 and figures["drawdown"]["note"] == wait
    for name in ("volatility", "sharpe", "sortino", "calmar"):
        assert figures[name] == {"value": None, "note": wait}
    assert figures["concentration"] == {
        "holdings": [], "sectors": [], "untagged_pct": None, "flags": []
    }  # fmt: skip
    assert figures["overlap"] == []
    assert figures["emergency"] == {
        "months": None, "target_months": 6, "note": "Set your monthly expenses to check this"
    }  # fmt: skip
    assert list(data) == ["tab_id", "as_of_day", "basis", "risk", "live"]
    assert data["live"]["rows"] == []
    assert list(figures) == ["days", "drawdown", "volatility", "sharpe", "sortino", "calmar",
                             "concentration", "overlap", "emergency"]  # fmt: skip
    gone = clean(await rig.call("risk", tab=999))
    assert gone["code"] == "not_found"


@scenario_of
async def test_the_upstox_tab_has_no_risk_report(rig):
    on(rig)
    upstox = next(t for t in rig.book.tabs(OWNER) if t["kind"] == "upstox")
    assert clean(await rig.call("risk", tab=upstox["id"]))["code"] == "not_found"


def snapshot_again(live) -> dict:
    """The concentration worked out again from the rows the answer carries, by the rule the phone
    copies (``holdings_of_rows``; vectors ``rows`` of risk.json)."""
    return risk.concentration(
        holdings_of_rows(live["rows"]), live["max_holding_pct"], live["max_sector_pct"]
    )


def swing(count):
    """Rupee values of one unit over ``count`` days: up 2, down 3, up 5, down 2, up 3, on 1,000."""
    values, value = [], 1000
    for at in range(count):
        value += (20, -30, 50, -20, 30)[at % 5]
        values.append(value)
    return values


@scenario_of
async def test_sixty_one_closing_values_give_the_figures_of_the_core(rig):
    on(rig)
    gold = manual(rig, "Gold bar", "g-1", asset_class="gold", sector="Metals")
    days = weekdays_to(date(2026, 10, 26), 61)
    values = [1000, *swing(60)]
    hold(rig, rig.tab, gold, 1000, days[0], "b-1")
    price(rig, gold, zip(days, values, strict=True))
    data = (await rig.call("risk", tab=rig.tab["id"]))["data"]
    # The same series by hand: one point a day, the purchase on the first.
    points = [Point(days[0], 100_000, in_paise=100_000)] + [
        Point(day, value * 100) for day, value in zip(days[1:], values[1:], strict=True)
    ]
    want = risk.series_figures(periods(points, 0), None, 650)
    assert want["days"] == 60 and want["volatility"]["value"] is not None
    got = data["risk"]
    assert {name: got[name] for name in want} == want
    assert got["sharpe"]["note"] == "Not yet: needs a year of daily history."
    assert got["drawdown"]["pct"] == "2.9412"  # 1,020 to 990: 30 / 1,020
    assert (got["drawdown"]["peak_day"], got["drawdown"]["trough_day"]) == (days[1], days[2])
    assert data["as_of_day"] == TODAY and data["basis"] == "On closing values up to 26 Oct 2026."
    assert got["concentration"]["holdings"] == [
        {"label": "Gold bar", "weight_pct": "100.0000", "over_limit": True}
    ]
    assert got["concentration"]["sectors"] == [
        {"label": "Metals", "weight_pct": "100.0000", "over_limit": True}
    ]
    assert got["concentration"]["untagged_pct"] == "0.0000"
    assert got["concentration"]["flags"] == [
        "Gold bar is 100% of what is held here, above your 15% limit.",
        "The sector Metals is 100% of what is held here, above your 35% limit.",
    ]
    # What the bars were worked out from comes with them, for the phone to move with ticks.
    live = data["live"]
    assert list(live) == ["read_at", "max_holding_pct", "max_sector_pct", "rows"]
    assert (live["read_at"], live["max_holding_pct"], live["max_sector_pct"]) == (
        NOW, "15.0000", "35.0000",
    )  # fmt: skip
    assert [row["asset"]["name"] for row in live["rows"]] == ["Gold bar"]
    assert snapshot_again(live) == got["concentration"]
    # One day fewer, and nothing of the series is shown.
    await rig.call(
        "settings_set", version=0, values={"max_holding_pct": 100, "max_sector_pct": 100}
    )
    eased = (await rig.call("risk", tab=rig.tab["id"]))["data"]
    assert eased["live"]["max_holding_pct"] == eased["live"]["max_sector_pct"] == "100.0000"
    eased = eased["risk"]["concentration"]
    assert eased["flags"] == [] and not eased["holdings"][0]["over_limit"]


@scenario_of
async def test_fifty_nine_returns_are_not_enough(rig):
    on(rig)
    gold = manual(rig, "Gold bar", "g-1", asset_class="gold")
    days = weekdays_to(date(2026, 10, 26), 60)
    hold(rig, rig.tab, gold, 1000, days[0], "b-1")
    price(rig, gold, zip(days, [1000, *swing(59)], strict=True))
    got = (await rig.call("risk", tab=rig.tab["id"]))["data"]["risk"]
    assert got["days"] == 59 and got["volatility"]["value"] is None
    assert got["drawdown"]["pct"] is None
    assert got["volatility"]["note"].endswith("1 more to go.")
    assert got["concentration"]["flags"][-1] == "Untagged: 100%; tag them to check sectors."


@scenario_of
async def test_overlap_the_emergency_fund_and_every_tab_together(rig):
    on(rig)
    gold = manual(rig, "Gold bar", "g-1", asset_class="gold", sector="Metals")
    land = manual(rig, "Plot", "g-2", asset_class="real_estate")
    deposit = manual(rig, "Bank deposit", "g-3", asset_class="cash")
    wallet = manual(rig, "Savings account", "g-4", asset_class="bonds")
    other = rig.book.tab_create(OWNER, "t-2", "Second", "mixed", now=NOW)
    cash = rig.book.tab_create(OWNER, "t-3", "Fixed deposits", "cash", now=NOW)
    shelved = rig.book.tab_create(OWNER, "t-4", "Old", "mixed", now=NOW)
    day = 20261020
    # Long term: gold 6,000 + plot 4,000.  Second: gold 1,000 + deposit 3,000.
    # Fixed deposits (a cash tab): a holding of another class, 2,000, counts whole.
    for at, (tab, asset, rupees) in enumerate(
        ((rig.tab, gold, 6000), (rig.tab, land, 4000), (other, gold, 1000),
         (other, deposit, 3000), (cash, wallet, 2000), (shelved, deposit, 9000))
    ):  # fmt: skip
        hold(rig, tab, asset, rupees, day, f"b-{at}")
    for asset, rupees in ((gold, 1), (land, 4000), (deposit, 3000), (wallet, 2000)):
        price(rig, asset, [(day, rupees)])
    price(rig, gold, [(day, 6000)])
    old = next(t for t in rig.book.tabs(OWNER) if t["id"] == shelved["id"])
    rig.book.tab_update(OWNER, shelved["id"], old["version"], {"archived": True}, now=NOW)
    await rig.call("settings_set", version=0, values={"monthly_expenses_paise": 200_000})

    got = (await rig.call("risk", tab=rig.tab["id"]))["data"]["risk"]
    # Gold is worth 6,000 a unit in both tabs: 60% here, and 6,000 of 9,000 = 66.67% there.
    assert got["overlap"] == [
        {"tab_id": other["id"], "weight_pct": "60.0000", "names": ["Gold bar"]},
        {"tab_id": cash["id"], "weight_pct": "0.0000", "names": []},
    ]
    # Liquid: the deposit in Second (3,000) and all of the cash tab (2,000) = 5,000; 2,000 a
    # month: 2.5 months. The archived tab's deposit is not counted.
    assert got["emergency"] == {
        "months": "2.5000", "target_months": 6,
        "note": "Covers 2.50 months of your expenses; your target is 6.",
    }  # fmt: skip
    # Every tab together: gold 6,000 + 6,000, plot 4,000, deposit 3,000, savings 2,000 = 21,000.
    both = (await rig.call("risk"))["data"]
    assert both["tab_id"] is None and both["risk"]["overlap"] == []
    assert both["risk"]["emergency"] == got["emergency"]
    bars = both["risk"]["concentration"]["holdings"]
    assert [(bar["label"], bar["weight_pct"]) for bar in bars] == [
        ("Gold bar", "57.1429"), ("Plot", "19.0476"), ("Bank deposit", "14.2857"),
        ("Savings account", "9.5238"),
    ]  # fmt: skip
    assert [bar["over_limit"] for bar in bars] == [True, True, False, False]
    # The rows of every tab asked for come with it: gold twice (one holding), the archived
    # tab's nothing; worked out again from them, the bars are the same.
    names = sorted(row["asset"]["name"] for row in both["live"]["rows"])
    assert names == ["Bank deposit", "Gold bar", "Gold bar", "Plot", "Savings account"]
    assert snapshot_again(both["live"]) == both["risk"]["concentration"]
    one = (await rig.call("risk", tab=rig.tab["id"]))["data"]
    assert sorted(row["asset"]["name"] for row in one["live"]["rows"]) == ["Gold bar", "Plot"]
    assert snapshot_again(one["live"]) == one["risk"]["concentration"]


@scenario_of
async def test_past_a_year_the_ratios_show_and_follow_the_owners_risk_free_rate(rig):
    on(rig)
    gold = manual(rig, "Gold bar", "g-1", asset_class="gold")
    days = weekdays_to(date(2026, 10, 26), 60)
    hold(rig, rig.tab, gold, 1000, 20250901, "b-1")
    price(rig, gold, [(20250901, 1000), *zip(days, swing(60), strict=True)])
    first = (await rig.call("risk", tab=rig.tab["id"]))["data"]["risk"]
    assert first["days"] > 250 and first["sharpe"]["note"] is None
    await rig.call("settings_set", version=0, values={"risk_free_bp": 0})
    second = (await rig.call("risk", tab=rig.tab["id"]))["data"]["risk"]
    assert float(second["sharpe"]["value"]) > float(first["sharpe"]["value"])
    assert float(second["sortino"]["value"]) > float(first["sortino"]["value"])
    for name in ("days", "drawdown", "volatility", "calmar"):
        assert second[name] == first[name]
    # Annualised TWR over the volatility, with nothing taken off: the Performance view's figure.
    shown = (await rig.call("performance", id=rig.tab["id"]))["data"]["twr"]["annualised_pct"]
    ratio = float(shown) / float(second["volatility"]["value"])
    assert abs(float(second["sharpe"]["value"]) - ratio) < 0.0002


@scenario_of
async def test_no_key_of_these_answers_is_unknown_to_the_mask_table(rig):
    """The closed-world rule (design p7 section 10) on the real answers: every key is hidden
    with P&L or quantities, never hidden, or a reviewed structural key."""
    from test_portfolio_closed_world import answer_misses

    on(rig)
    gold = manual(rig, "Gold bar", "g-1", asset_class="gold", sector="Metals")
    hold(rig, rig.tab, gold, 1000, 20261020, "b-1")
    price(rig, gold, [(20261020, 1000)])
    rig.book.tab_create(OWNER, "t-2", "Second", "mixed", now=NOW)
    for name, args in (("risk", {"tab": rig.tab["id"]}), ("risk", {}), ("settings_get", {})):
        assert answer_misses((await rig.call(name, **args))["data"]) == set(), name
