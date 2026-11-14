"""A tab as the phone reads it, its performance, and the one place a viewer's mask applies (PB10)."""

from __future__ import annotations

import json

import pytest

from sudarshan.portfolio import layout, privacy, serialize
from sudarshan.portfolio import store as store_module
from sudarshan.portfolio.book import Book
from sudarshan.portfolio.core import history, kinds
from sudarshan.portfolio.store import (
    DB_FILENAME,
    Invalid,
    NotFound,
    PortfolioStore,
    Scope,
    UnknownPrincipal,
)
from sudarshan.portfolio.views import NO_PRICE, Prices, Views

NOW = 1_793_000_000_000  # 26 Oct 2026, mid-morning in India
TODAY = 20261026
OWNER = Scope.owner()
VIEWER = Scope("viewer", None, serialize.VIEWER_MASK)


@pytest.fixture(autouse=True)
def _stop_thread():
    yield
    store_module.stop()


class World:
    def __init__(self, path):
        self.store = PortfolioStore(path, principals=("owner", "viewer"), now=NOW)
        self.book = Book(self.store)
        self.views = Views(self.store)
        me = self.book.members(OWNER)[0]
        self.zerodha = self.book.account_create(OWNER, "a-1", me["id"], "Zerodha", "demat", now=NOW)
        self.groww = self.book.account_create(OWNER, "a-2", me["id"], "Groww", "demat", now=NOW)
        self.share = self.book.asset_create(
            OWNER, "s-1", "listed", now=NOW, exchange="NSE", symbol="RELIANCE",
            isin="INE002A01018", name="Reliance Industries", sector="Energy", tags=["core"],
        )  # fmt: skip
        self.gold = self.book.asset_create(
            OWNER, "g-1", "manual", now=NOW, name="Gold coin", asset_class="gold",
            unit_label="g", first_value={"day": 20261001, "price_u": 60_000_000},
        )  # fmt: skip
        self.tab = self.book.tab_create(OWNER, "t-1", "Long term", "mixed", now=NOW)

    def version(self) -> int:
        return next(t for t in self.book.tabs(OWNER) if t["id"] == self.tab["id"])["version"]

    def add(self, rid, kind="BUY", asset=None, account=None, **more):
        entry = {
            "kind": kind, "trade_date": 20261001, "account_id": (account or self.zerodha)["id"],
            "asset_id": (asset or self.share)["id"], "qty_u": 10_000_000,
            "amount_paise": 1_000_000, **more,
        }  # fmt: skip
        return self.book.activity_create(OWNER, self.tab["id"], self.version(), entry, rid, now=NOW)

    def get(self, **args):
        return self.views.tab_view(OWNER, self.tab["id"], args, now=NOW)


@pytest.fixture
def w(tmp_path):
    made = World(tmp_path / "s" / DB_FILENAME)
    yield made
    made.store.close()


def test_a_listed_share_with_no_price_yet_says_so_and_is_counted_at_its_cost(w):
    w.add("e-1")
    view = w.get()
    (row,) = view["rows"]
    assert row["price"] == NO_PRICE and row["fresh"] == {"state": "old", "words": "No price yet"}
    assert row["lines"] == [
        {"account_id": w.zerodha["id"], "qty_u": 10_000_000, "open_qty_u": 10_000_000,
         "cost_paise": 1_000_000, "in_today_paise": 0, "out_today_paise": 0, "over_year_qty_u": 0}
    ]  # fmt: skip
    assert view["totals"]["unpriced"] == 1
    assert view["totals"]["value_paise"] == view["totals"]["cost_paise"] == 1_000_000
    assert view["totals"]["today"]["state"] == "unknown"  # never a made-up zero
    assert (view["today"], view["read_at"], view["as_of"]) == (TODAY, NOW, None)


def test_every_listed_holding_carries_the_auction_fields(w):
    w.add("e-1")
    (row,) = w.get()["rows"]
    assert {"cas", "auction_from", "auction_until", "auction_close"} <= set(row)
    assert row["cas"] is False and row["auction_from"] is None


def test_a_manual_asset_is_worth_the_owners_newest_value_and_has_no_today(w):
    w.add("e-1", asset=w.gold, qty_u=2_000_000, amount_paise=1_000_000)
    view = w.get()
    (row,) = view["rows"]
    assert row["price"]["price_u"] == 60_000_000 and row["price"]["basis"] == "manual"
    assert row["today_mode"] == "none"
    assert view["totals"]["value_paise"] == 1_200_000 and view["totals"]["unpriced"] == 0
    assert view["totals"]["unrealised_paise"] == 200_000
    assert row["fresh"]["words"].startswith("Value from 1 Oct")
    assert view["totals"]["today"]["state"] == "none"


def test_the_bars_add_up_to_the_value_and_a_filter_narrows_the_rows(w):
    w.add("e-1", asset=w.gold, qty_u=2_000_000)
    w.add("e-2", account=w.groww)
    full = w.get()
    for by in ("class", "account", "member"):
        assert (
            sum(b["value_paise"] for b in full["allocation"][by]) == full["totals"]["value_paise"]
        )
    only = w.get(accounts=[w.groww["id"]])
    assert [r["asset"]["symbol"] for r in only["rows"]] == ["RELIANCE"]
    assert only["filters"]["accounts"] == [w.groww["id"]]
    tagged = w.get(tags=["core"])
    assert [r["asset"]["symbol"] for r in tagged["rows"]] == ["RELIANCE"]
    assert w.get(classes=["gold"])["rows"][0]["asset"]["name"] == "Gold coin"


def test_as_of_replays_up_to_the_day_and_prices_at_that_days_close(w):
    w.add("e-1", asset=w.gold, qty_u=1_000_000)
    w.add("e-2", asset=w.gold, qty_u=1_000_000, trade_date=20261010)
    then = w.get(as_of=20261005)
    assert then["as_of"] == 20261005
    assert then["rows"][0]["lines"][0]["qty_u"] == 1_000_000
    assert then["totals"]["value_paise"] == 600_000  # 1 g at the value of 1 Oct
    assert then["totals"]["today"]["state"] == "none"
    before = w.get(as_of=20260930)
    assert before["rows"] == []
    share = w.add("e-3", trade_date=20261002)
    assert share["activity"]
    unknown = w.get(as_of=20261003)
    ref = next(r for r in unknown["rows"] if r["asset"]["symbol"] == "RELIANCE")
    assert ref["fresh"]["words"] == "Value on that day is not known."
    with pytest.raises(Invalid, match="already begun"):
        w.get(as_of=20261101)
    with pytest.raises(Invalid, match="valid date"):
        w.get(as_of=20261340)


def test_a_saved_view_applies_unless_the_request_gives_its_own(w):
    w.add("e-1", asset=w.gold, qty_u=2_000_000)
    w.add("e-2", account=w.groww)
    saved = w.store.write(
        OWNER,
        lambda tx: tx.insert(
            "saved_view",
            {"tab_id": w.tab["id"], "name": "Gold", "is_default": False,
             "spec": {"filters": {"classes": ["gold"]}, "sort": "value", "group": "none",
                      "columns": ["name", "value"]}},
            NOW,
        ),
    )  # fmt: skip
    assert [r["asset"]["name"] for r in w.get(view=saved["id"])["rows"]] == ["Gold coin"]
    both = w.get(view=saved["id"], classes=["equity", "gold"])
    assert len(both["rows"]) == 2
    with pytest.raises(NotFound):
        w.get(view=999)


def test_the_layout_is_the_kinds_default_until_saved(w):
    state = w.get()["layout"]
    assert state["version"] == 0 and state["spec"] == state["defaults"] == layout.defaults_for(
        "mixed"
    )
    assert set(state["spec"]["widgets"]) == set(kinds.WIDGETS)


def test_performance_carries_every_part_and_its_series_is_kept_per_tab_version(w, monkeypatch):
    w.add("e-1", asset=w.gold, qty_u=2_000_000)
    first = w.views.performance(OWNER, w.tab["id"], now=NOW)
    assert first["valuation_day"] == TODAY and first["value_paise"] == 1_200_000
    assert first["money_in_paise"] == 1_000_000 and first["net_put_in_paise"] == 1_000_000
    assert first["total_gain_paise"] == 200_000 and first["absolute_return_pct"] == "20.0000"
    assert first["xirr"]["state"] in ("later", "shown", "none") and first["twr"]["start_day"]
    assert [b["benchmark"] for b in first["benchmarks"]] == list(kinds.BENCHMARKS)
    assert first["series"][0] == {
        "day": 20261001,
        "value_paise": 1_200_000,
        "net_put_in_paise": 1_000_000,
    }
    assert {"by_fy", "income", "series_words"} <= set(first)
    json.dumps(first)  # nothing but JSON
    calls = []
    real = history.daily
    monkeypatch.setattr(history, "daily", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert w.views.performance(OWNER, w.tab["id"], now=NOW) == first
    assert calls == []  # the same tab version and prices: from the cache
    w.add("e-2", asset=w.gold, qty_u=1_000_000, trade_date=20261010)
    changed = w.views.performance(OWNER, w.tab["id"], now=NOW)
    assert calls == [1] and changed["money_in_paise"] == 2_000_000
    w.store.write(OWNER, lambda tx: tx.set_price_point(w.gold["id"], 20261020, 70_000_000, NOW + 1))
    again = w.views.performance(OWNER, w.tab["id"], now=NOW)
    assert calls == [1, 1] and again["value_paise"] > changed["value_paise"]


def test_a_tab_with_no_entries_has_no_series_and_says_why(w):
    report = w.views.performance(OWNER, w.tab["id"], now=NOW)
    assert report["series"] == [] and report["first_flow_day"] is None
    assert report["value_paise"] == 0


def test_state_and_summary_list_the_owners_tabs_with_live_inputs_for_the_pinned_ones(w):
    w.add("e-1", asset=w.gold, qty_u=2_000_000)
    pinned = w.book.tab_create(OWNER, "t-2", "Pinned", "stocks", now=NOW, pinned=True)
    state = w.views.state(OWNER, now=NOW, version="9.9")
    assert state["service_version"] == "9.9" and state["today"] == TODAY
    assert state["schema_version"] > 0 and state["amfi"] == {"on": False, "nav_day": None}
    assert {t["id"] for t in state["tabs"]} >= {w.tab["id"], pinned["id"]}
    summary = w.views.summary(OWNER, now=NOW)
    ids = [e["tab"]["id"] for e in summary["entries"]]
    assert pinned["id"] in ids and w.tab["id"] not in ids
    mine = next(e for e in summary["entries"] if e["tab"]["id"] == pinned["id"])
    assert set(mine["live"]) == {"rows", "fixed", "totals"} and mine["upstox"] is None


# ---- the principal filter (mutation target 21) and the viewer's mask (design 6.5) ----------------
def test_another_principal_sees_none_of_the_owners_tab(w):
    w.add("e-1", asset=w.gold, qty_u=2_000_000)
    with pytest.raises(NotFound):
        w.views.tab_view(VIEWER, w.tab["id"], {}, now=NOW)
    with pytest.raises(NotFound):
        w.views.performance(VIEWER, w.tab["id"], now=NOW)
    theirs = w.views.state(VIEWER, now=NOW, version="x")
    assert [t["kind"] for t in theirs["tabs"]] == ["upstox"]  # only the tab seeded for them
    assert w.tab["id"] not in {t["id"] for t in theirs["tabs"]}
    assert len(theirs["accounts"]) == 1 and w.zerodha["id"] not in {
        a["id"] for a in theirs["accounts"]
    }
    assert [e["tab"]["kind"] for e in w.views.summary(VIEWER, now=NOW)["entries"]] == ["upstox"]
    with pytest.raises(UnknownPrincipal):
        w.views.state(Scope("stranger"), now=NOW, version="x")


def _walk(data, found):
    if isinstance(data, dict):
        for key, value in data.items():
            found.add(key)
            if privacy.kept_whole(key, value):
                continue  # how old a price is: a state and a sentence, hidden by no category
            _walk(value, found)
    elif isinstance(data, list):
        for item in data:
            _walk(item, found)
    return found


def test_a_viewer_sees_symbols_and_direction_but_no_money(w):
    w.add("e-1", asset=w.gold, qty_u=2_000_000)
    w.add("e-2")
    full = w.get()
    page = w.book.activities_list(OWNER, w.tab["id"])
    shown = serialize.out(
        VIEWER, {"view": full, "activities": [serialize.activity(a) for a in page["items"]]}
    )
    keys = _walk(shown, set())
    secret = [k for k in keys if serialize.hidden(k, serialize.VIEWER_MASK)]
    assert secret == []
    # and the age of each price reaches a viewer who is shown no money (v0.12.0)
    assert [row["fresh"] for row in shown["view"]["rows"]] == [row["fresh"] for row in full["rows"]]
    assert all(set(row["fresh"]) == {"state", "words"} for row in full["rows"])
    symbols = [r["asset"]["symbol"] for r in shown["view"]["rows"]]
    assert "RELIANCE" in symbols
    assert {a["kind"] for a in shown["activities"]} == {"BUY"}
    for banned in ("qty_u", "price_u", "amount_paise", "value_paise", "lines", "totals", "fixed"):
        assert banned not in keys
    assert _walk(serialize.out(OWNER, full), set()) >= {"qty_u", "totals", "value_paise"}


def test_the_owner_scope_has_no_mask_and_the_answer_is_untouched(w):
    data = {"a_paise": 1, "nested": [{"qty_u": 2}]}
    assert serialize.out(OWNER, data) is data
    assert OWNER.mask == frozenset()


def test_the_default_prices_invent_nothing(w):
    def probe(tx):
        return Prices().now(tx, {"id": w.share["id"], "kind": "listed"}, TODAY)

    assert w.store.read(OWNER, probe) == NO_PRICE
