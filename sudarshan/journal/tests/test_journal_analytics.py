"""The analytics op and the scope's mask (JB10): every figure from core/ over the filter the page
sends, beta against NIFTY 50 through the portfolio, and the one place a viewer's answers lose
money, prices and quantities (design 6.6; mutation target 26). All values are synthetic."""

from __future__ import annotations

import asyncio
import json

import pytest
from journal_fakes import sealed
from test_journal_api import Desk, post, served
from test_journal_capture import DAY, ist_ms, scenario
from test_journal_capture import files as files  # noqa: F401  (a fixture)
from test_journal_capture import rig as rig  # noqa: F401  (a fixture)

from sudarshan.journal import serialize
from sudarshan.journal.book import Facts, replay_tx, update_trip
from sudarshan.journal.core import metrics
from sudarshan.journal.core.money import u_to_paise
from sudarshan.journal.errors import ApiError
from sudarshan.journal.ops import REGISTRY
from sudarshan.journal.ops import common as ops_common
from sudarshan.journal.ops.analytics_ops import period_span
from sudarshan.journal.store import Scope

OWNER = Scope.owner()


def fill(trade_id, action, price, day, clock, symbol="NIFTY25NOV2625000CE", qty=65, **more):
    return {"mode": "live", "day": day, "trade_id": trade_id, "order_id": f"o-{trade_id}",
            "exchange": "NFO", "symbol": symbol, "instrument_key": "NSE_FO|50001",
            "product": "MIS", "action": action, "qty": qty, "price_u": int(price * 10_000),
            "value_paise": u_to_paise(qty * int(price * 10_000)),
            "exch_ts_ms": ist_ms(day, clock), "order_ts_ms": None, "source": "stream_read",
            **more}  # fmt: skip


def trade(made, n, day, buy, sell, clock="10:00:00", symbol=None):
    """One closed MIS trip: a buy, then a sale 20 minutes later."""
    h, m, s = (int(x) for x in clock.split(":"))
    later = f"{h:02d}:{m + 20:02d}:{s:02d}"
    rows = [fill(f"b{n}", "BUY", buy, day, clock, **({"symbol": symbol} if symbol else {})),
            fill(f"s{n}", "SELL", sell, day, later, **({"symbol": symbol} if symbol else {}))]  # fmt: skip
    made.write(OWNER, lambda tx: tx.fills_upsert(rows, 1))
    made.write(OWNER, lambda tx: replay_tx(tx, "live", {day}, 1, Facts()))


def analytics(rig, **args):
    async def go():
        token = ops_common.CALLER.set(ops_common.Caller())
        try:
            return await REGISTRY["analytics"](rig.svc, args)
        finally:
            ops_common.CALLER.reset(token)

    return scenario(rig, go)


@pytest.fixture
def book(rig):
    """Four trades: Mon 2 Nov (win), Tue 3 Nov (loss), Mon 9 Nov twice (win, flat)."""
    store = rig.svc.store
    trade(store, 1, 20261102, 100, 112.5)
    trade(store, 2, 20261103, 100, 95, clock="11:00:00")
    trade(store, 3, DAY, 100, 104, symbol="NIFTY25NOV2625100CE")
    trade(store, 4, DAY, 50, 50, clock="14:00:00", symbol="NIFTY25NOV2625200CE")
    rig.now = ist_ms(DAY, "16:30:00")
    return store


def test_the_named_periods_end_today():
    assert period_span("today", 20261111) == (20261111, 20261111)
    assert period_span("week", 20261111) == (20261109, 20261111)  # Wednesday: from Monday
    assert period_span("month", 20261111) == (20261101, 20261111)
    assert period_span("fy", 20261111) == (20260401, 20261111)
    assert period_span("fy", 20270215) == (20260401, 20270215)
    assert period_span("all", 20261111) == (None, 20261111)


def test_the_months_figures_are_cores_over_the_closed_trips(rig, book):
    got = analytics(rig)  # month by default
    trips = book.read(OWNER, lambda tx: tx.rows("round_trip", status="closed"))
    want = metrics.compute([{**t, "charges_paise": t["charges_est_paise"]} for t in trips])
    assert got["metrics"] == want and got["metrics"]["trips"] == 4
    assert [d["key"] for d in got["calendar"]["days"]] == ["20261102", "20261103", "20261109"]
    assert [w["key"] for w in got["calendar"]["weeks"]] == ["2026W45", "2026W46"]
    assert [m["key"] for m in got["calendar"]["months"]] == ["202611"]
    assert {(c["weekday"], c["hour"]) for c in got["heat"]} == {(0, 10), (1, 11), (0, 14)}
    points = got["equity"]["points"]
    assert points[0]["day"] == 20261102 and points[-1]["day"] == DAY  # trading days only
    assert 20261107 not in [p["day"] for p in points]  # a Saturday
    assert [b["name"] for b in got["by_strategy"]] == ["Manual"]
    assert got["expiry"]["not_index_expiry"]["trips"] == 4


def test_the_filters_narrow_the_trips(rig, book):
    assert analytics(rig, period="today")["metrics"]["trips"] == 2
    assert analytics(rig, period="week")["metrics"]["trips"] == 2
    assert analytics(rig, **{"from": 20261102, "to": 20261103})["metrics"]["trips"] == 2
    assert analytics(rig, period="all")["metrics"]["trips"] == 4
    first = book.read(OWNER, lambda tx: tx.rows("round_trip", close_day=20261102))[0]
    fomo = book.read(OWNER, lambda tx: tx.rows("tag", name="FOMO"))[0]
    general = book.read(OWNER, lambda tx: tx.rows("playbook"))[0]
    book.write(OWNER, lambda tx: update_trip(tx, first["id"], 1, {
        "strategy_override": "Opening drive", "mistakes": [fomo["id"]],
        "setup_id": general["id"]}, 1))  # fmt: skip
    assert analytics(rig, strategy="opening DRIVE")["metrics"]["trips"] == 1
    assert analytics(rig, tag=fomo["id"])["metrics"]["trips"] == 1
    assert analytics(rig, playbook=general["id"])["metrics"]["trips"] == 1
    tagged = analytics(rig)
    assert [b["name"] for b in tagged["by_tag"]] == ["FOMO"]
    assert [b["name"] for b in tagged["by_playbook"]] == ["General"]
    book.write(OWNER, lambda tx: tx.touch("round_trip", first["id"], {"index_expiry_day": 1}, 1))
    assert analytics(rig, expiry="expiry")["metrics"]["trips"] == 1
    assert analytics(rig, expiry="non_expiry")["metrics"]["trips"] == 3
    assert analytics(rig, mode="sandbox")["metrics"]["trips"] == 0
    with pytest.raises(ApiError):
        analytics(rig, period="decade")
    with pytest.raises(ApiError) as caught:
        analytics(rig, **{"from": 20261110, "to": 20261102})
    assert caught.value.message == "The period starts after it ends."


def test_beta_needs_the_capital_twenty_days_and_the_index_closes(rig, book):
    beta = analytics(rig)["beta"]
    assert beta["state"] == "no_capital"
    book.write(OWNER, lambda tx: tx.settings_save(
        {**{k: v for k, v in tx.settings().items() if k != "version"},
         "capital_base_paise": 50_000_000}, 1, 1))  # fmt: skip
    beta = analytics(rig)["beta"]  # six trading days since the first trade: not asked yet
    assert beta["state"] == "short" and beta["days"] == 6 and beta["need"] == 20
    assert beta["note"] == "shown after 20 trading days (from 27 Nov 2026)"
    assert "benchmark_closes" not in [c[0] for c in rig.portfolio.calls]
    rig.now = ist_ms(20261201, "16:30:00")  # 22 trading days since 2 Nov
    days = [20261030] + [d for d in range(20261102, 20261131) if d % 100 <= 30] + [20261201]
    weekdays = [d for d in days if __import__("datetime").date(
        d // 10000, d // 100 % 100, d % 100).weekday() < 5]  # fmt: skip
    rig.portfolio.answers["benchmark_closes"] = {
        "state": "ok", "index": "nifty50",
        "closes": [{"day": d, "close_u": 2_500_000_000 + (n % 3) * 7_000_000}
                   for n, d in enumerate(weekdays)],
    }  # fmt: skip
    beta = analytics(rig, period="all")["beta"]
    assert beta["state"] == "ok" and beta["days"] == 22 and beta["beta"] is not None
    rig.portfolio.down = True
    assert analytics(rig, period="all")["beta"] is None  # no closes: no beta, no guess


def test_the_viewers_mask_takes_every_paise_price_and_quantity_out(rig, book):
    """Mutation target 26: trips, reports and day closes as a viewer sees them."""
    trip = book.read(OWNER, lambda tx: tx.rows("round_trip", status="closed"))[0]

    async def detail():
        token = ops_common.CALLER.set(ops_common.Caller())
        try:
            return await REGISTRY["trip_get"](rig.svc, {"id": trip["id"]})
        finally:
            ops_common.CALLER.reset(token)

    owner = scenario(rig, detail)
    report = {"id": 1, "kind": "eod", "period_key": "20261109", "text": "Net +₹812.50",
              "status": "sent", "reason": None, "file_name": None}  # fmt: skip
    close = {"day": DAY, "realised_paise": 1, "unrealised_paise": 2, "charges_est_paise": 3,
             "trades_closed": 4, "wins": 2, "losses": 1, "flats": 1,
             "positions_overnight": [{"symbol": "ACME", "product": "CNC", "qty": 10}],
             "kill_switch": "off", "reconcile_note": "The close check agreed."}  # fmt: skip
    seen = serialize.apply({"trip": owner, "report": report, "close": close,
                            "analytics": analytics(rig)}, serialize.viewer())  # fmt: skip
    flat = json.dumps(seen)
    for key in _keys(seen):
        assert serialize.category(key) is None, key
    assert "812" not in flat and '"text"' not in flat
    assert seen["trip"]["symbol"] == trip["symbol"] and seen["trip"]["side"] == "long"
    assert seen["trip"]["status"] == "closed" and seen["trip"]["strategy"] == "Manual"
    assert seen["close"]["trades_closed"] == 4 and seen["close"]["wins"] == 2
    assert seen["close"]["positions_overnight"] == [{"symbol": "ACME", "product": "CNC"}]
    assert seen["close"]["reconcile_note"] == "The close check agreed."
    assert seen["analytics"]["metrics"]["trips"] == 4
    assert serialize.apply(owner, OWNER) is owner  # the owner's scope masks nothing


def _keys(value):
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from _keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _keys(inner)


def test_the_family_purposes_answer_leaves_through_the_mask():
    async def go():
        async def counts(ctx, args):
            return {"day": 1, "trades": 2, "won": 1, "lost": 1, "net_paise": 99}

        server, base = await served(Desk(), {"family_day": counts})
        try:
            answer = await post(base, "family_day", sealed("family_day", "family", day=1))
        finally:
            await server.close()
        return answer.json()

    body = asyncio.run(go())
    assert body["data"] == {"day": 1, "trades": 2, "won": 1, "lost": 1}
