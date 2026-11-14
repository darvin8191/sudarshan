"""Upstox's actual charges by day into orders and trades, and the walk back (P7 J3, M14.2).
Against the fake portfolio on 127.0.0.1; every figure invented."""

from __future__ import annotations

import dataclasses

import pytest
from test_journal_book import as_fill
from test_journal_capture import files as files  # noqa: F401  (a fixture)
from test_journal_capture import ist_ms, scenario
from test_journal_capture import rig as rig  # noqa: F401  (a fixture)

from sudarshan.journal import actual
from sudarshan.journal.book import Facts, replay_tx
from sudarshan.journal.jobs import catchup, charges
from sudarshan.journal.ops import REGISTRY
from sudarshan.journal.store import Scope

OWNER = Scope.owner()
OPT = "NIFTY25NOV2625000CE"
#: Twelve trading days up to Tue 10 Nov 2026; "tonight" is Wed 11 Nov.
DAYS = [20261026, 20261027, 20261028, 20261029, 20261030, 20261102, 20261103, 20261104,
        20261105, 20261106, 20261109, 20261110]  # fmt: skip
TODAY = 20261111
PARTS = ("brokerage_paise", "gst_paise", "stt_paise", "stamp_duty_paise", "transaction_paise",
         "clearing_paise", "ipft_paise", "others_paise", "sebi_turnover_paise",
         "demat_transaction_paise")  # fmt: skip


@pytest.fixture
def on(rig):  # noqa: F811
    rig.svc.settings = dataclasses.replace(rig.svc.settings, tax=True)
    return rig


def fill(trade_id, order_id, day, action, qty, price_rs, clock, **more):
    row = {"trade_id": trade_id, "order_id": order_id, "exchange": "NFO", "symbol": OPT,
           "product": "MIS", "action": action, "qty": qty, "price_u": int(price_rs * 10_000),
           "exch_ts_ms": ist_ms(day, clock + ":00"), "day": day, **more}  # fmt: skip
    return as_fill(row)


def put(rig, fills, mode="live"):  # noqa: F811
    days = {f["day"] for f in fills}

    def write(tx):
        tx.fills_upsert(fills, rig.now)
        return replay_tx(tx, mode, days, rig.now, Facts())

    return rig.svc.store.write(OWNER, write)


def day_trade(day, n=0):
    """One option bought and sold on ``day``: two orders, one closed trade."""
    return [fill(f"b{day}{n}", f"B{day}{n}", day, "BUY", 65, 100 + n, f"1{n}:01"),
            fill(f"s{day}{n}", f"S{day}{n}", day, "SELL", 65, 110 + n, f"1{n}:20")]  # fmt: skip


def answer_for(day):
    """What the fake Upstox charged on a day: every part, different each day, and a total that
    holds 3 paise more than the parts it names."""
    k = day % 100
    parts = {"brokerage_paise": 4000, "gst_paise": 850 + k, "stt_paise": 1073 + k,
             "stamp_duty_paise": 20, "transaction_paise": 480 + k, "clearing_paise": 11,
             "ipft_paise": 3, "others_paise": 7, "sebi_turnover_paise": 2,
             "demat_transaction_paise": None}  # fmt: skip
    return {**parts, "total_paise": sum(v for v in parts.values() if v) + 3}


def charges_answers(rig, per_day=answer_for, month=None, zero=()):  # noqa: F811
    def answer(args):
        assert args["part"] == "charges" and args["fy"] == 2026
        start, end = args["from"], args["to"]
        if start != end:
            found = month(start, end) if month else dict.fromkeys((*PARTS, "total_paise"))
        elif start in zero:
            found = dict.fromkeys((*PARTS, "total_paise"), 0) | {"gst_paise": None}
        else:
            found = per_day(start)
        return {"state": "ok", "read_at": 1, "note": None, "segment": args["segment"],
                "fy": 2026, "part": "charges", "from": start, "to": end, "charges": found}  # fmt: skip

    rig.portfolio.answers["pnl_report"] = answer


def night(rig, today, recent):  # noqa: F811
    rig.now = ist_ms(today, "22:00:00")
    before = len(rig.portfolio.calls)
    line = scenario(rig, lambda: charges.actual_charges(rig.svc, recent, rig.now))
    return line, [c[1] for c in rig.portfolio.calls[before:] if c[0] == "pnl_report"]


def trips(rig):  # noqa: F811
    return rig.svc.store.read(OWNER, lambda tx: tx.rows("round_trip", order="opened_ms,id"))


def orders(rig, **equal):  # noqa: F811
    return rig.svc.store.read(
        OWNER, lambda tx: tx.rows("charges_order", order="day,order_id", **equal)
    )


def rows(rig, **equal):  # noqa: F811
    return rig.svc.store.read(
        OWNER, lambda tx: tx.rows("upstox_charges", order="period_from,period_to", **equal)
    )


# ---- the walk back -----------------------------------------------------------------------------
def test_twelve_days_are_walked_back_in_two_nights_and_every_trade_adds_up(on):
    """The design's replay: 12 days of fills, a fake portfolio answering day charges. After the
    runs every trade has an actual, and the orders and the trades of each day add up to Upstox's
    total for that day, part by part, to the paisa."""
    put(on, [f for d in DAYS for f in day_trade(d)] + day_trade(20261110, 1))
    charges_answers(on, month=lambda a, b: {"total_paise": 90_000, "brokerage_paise": 40_000})
    assert all(t["charges_actual_paise"] is None for t in trips(on))

    line, asked = night(on, TODAY, [20261110, 20261109, 20261106])
    # The 3 last trading days, October's month row (due since 3 Nov), then 5 earlier days.
    assert [(a["from"], a["to"]) for a in asked] == [
        (20261106, 20261106), (20261109, 20261109), (20261110, 20261110), (20261001, 20261031),
        (20261105, 20261105), (20261104, 20261104), (20261103, 20261103), (20261102, 20261102),
        (20261030, 20261030),
    ]  # fmt: skip
    assert {a["segment"] for a in asked} == {"FO"}
    assert line == ("charges: 8 days and 1 months answered by Upstox, 0 not ready yet, "
                    "9 trades now carry actual charges, 4 earlier days still to ask")  # fmt: skip

    line, asked = night(on, 20261112, [20261111, 20261110, 20261109])
    assert [a["from"] for a in asked] == [20261029, 20261028, 20261027, 20261026]
    assert "4 days and 0 months answered" in line and "still to ask" not in line
    line, asked = night(on, 20261113, [20261112, 20261111, 20261110])
    assert asked == [] and line == "charges: nothing to ask Upstox for"

    found = trips(on)
    assert len(found) == 13 and all(t["charges_actual_paise"] is not None for t in found)
    for day in DAYS:
        want = answer_for(day)
        kept = orders(on, day=day)
        assert sum(o["total_paise"] for o in kept) == want["total_paise"]
        assert {o["basis"] for o in kept} == {"day"}
        day_trips = [t for t in found if t["open_day"] == day]
        assert sum(t["charges_actual_paise"] for t in day_trips) == want["total_paise"]
        for name, part in (("brokerage_paise", "brokerage"), ("stt_paise", "stt"),
                           ("gst_paise", "gst"), ("transaction_paise", "transaction")):  # fmt: skip
            assert sum(t["charges_actual_parts"]["parts"][part] for t in day_trips) == want[name]
        # What the total holds beyond the named parts is shared too, never dropped.
        assert sum(t["charges_actual_parts"]["parts"]["unlisted"] for t in day_trips) == 3
        for t in day_trips:
            assert t["net_paise"] == t["gross_paise"] - t["charges_actual_paise"]
            assert t["charges_actual_basis"] == "day" and t["version"] == 1
            assert t["charges_actual_parts"]["partial"] is False
            assert t["charges_actual_parts"]["missing"] == ["dp"]  # no delivery sale: not partial
    assert len(rows(on, basis="day")) == 12 and len(rows(on, basis="month")) == 1


def test_no_more_than_twenty_questions_a_night_and_only_the_years_upstox_keeps():
    traded = [(d, e) for d in DAYS for e in ("NSE", "NFO", "MCX", "CDS", "BSE")]
    traded += [(20240105, "NFO"), (20260401, "XYZ")]  # FY 2023-24: out of reach; no such segment
    queue, left = charges.plan_night(traded, [], [20261110, 20261109, 20261106], TODAY)
    assert len(queue) == charges.MAX_CALLS
    assert queue[:12] == [(s, d, d) for d in (20261106, 20261109, 20261110)
                          for s in ("CD", "COM", "EQ", "FO")]  # fmt: skip
    assert queue[12:16] == [(s, 20261001, 20261031) for s in ("CD", "COM", "EQ", "FO")]
    assert left == 4  # 9 older days, 5 of them tonight (of which the cap lets one through)
    assert all(start >= 20261001 for _, start, _ in queue)


def test_a_month_is_asked_from_the_third_of_the_next():
    assert not charges.month_due(20261001, 20261102)
    assert charges.month_due(20261001, 20261103) and charges.month_due(20261001, 20270115)
    assert not charges.month_due(20261201, 20270102) and charges.month_due(20261201, 20270103)
    traded = [(20261030, "NFO")]
    assert charges.plan_night(traded, [], [], 20261102)[0] == [("FO", 20261030, 20261030)]
    # Today's own charges are never asked for tonight: Upstox's report runs days behind.
    assert charges.plan_night([(20261102, "NFO")], [], [20261102], 20261102)[0] == []
    waiting = {"segment": "FO", "period_from": 20261001, "period_to": 20261031,
               "asked": actual.GIVE_UP}  # fmt: skip
    day = {"segment": "FO", "period_from": 20261030, "period_to": 20261030, "total_paise": 5}
    # A month not answered after the nights allowed is asked again on the 3rd only.
    assert charges.plan_night(traded, [waiting, day], [], 20261203)[0] == [
        ("FO", 20261001, 20261031)
    ]
    assert charges.plan_night(traded, [waiting, day], [], 20261204)[0] == []


# ---- a day Upstox does not answer --------------------------------------------------------------
def test_an_all_zero_answer_while_fills_exist_is_not_ready_and_the_month_answers_for_it(on):
    """Mutation 14: an all-zero row taken as the day's actual would give every trade charges of
    0.00. It is asked again for 7 nights, then October's month row, less the days that did
    answer, is shared among the orders of the days left."""
    fri, thu = 20261030, 20261029
    put(on, day_trade(fri) + day_trade(thu) + day_trade(thu, 1))
    month = {"total_paise": 20_000, "brokerage_paise": 12_000, "gst_paise": 2_160,
             "stt_paise": 5_000, "stamp_duty_paise": None, "transaction_paise": 840}  # fmt: skip
    charges_answers(on, zero={thu}, month=lambda a, b: month)
    line, asked = night(on, 20261102, [fri, thu, 20261028])
    assert "1 days and 0 months answered by Upstox, 1 not ready yet, 1 trades now" in line
    (kept,) = rows(on, period_from=thu)
    assert (kept["asked"], kept["total_paise"], kept["gst_paise"]) == (1, 0, None)
    assert orders(on, day=thu) == []
    waiting = [t for t in trips(on) if t["open_day"] == thu]
    assert all(t["charges_actual_paise"] is None for t in waiting)
    assert all(t["net_paise"] == t["gross_paise"] - t["charges_est_paise"] for t in waiting)

    # Nights 2 to 7: the day is asked each night; the month from 3 Nov on, and it is kept, but
    # it does not answer for the day while the day is still being asked.
    for n, today in enumerate((20261103, 20261104, 20261105, 20261106, 20261109, 20261110), 2):
        line, asked = night(on, today, [])
        assert (thu, thu) in [(a["from"], a["to"]) for a in asked]
        assert rows(on, period_from=thu)[0]["asked"] == n
        if n < actual.GIVE_UP:
            assert orders(on, day=thu) == []
    assert [(r["period_from"], r["period_to"]) for r in rows(on, basis="month")] == [
        (20261001, 20261031)
    ]
    # The seventh night gave the day up: the month's row less Friday's answer is Thursday's.
    friday = answer_for(fri)
    kept = orders(on, day=thu)
    assert {o["basis"] for o in kept} == {"month"} and len(kept) == 4
    rest = 20_000 - friday["total_paise"]
    assert sum(o["total_paise"] for o in kept) == rest
    thursday = [t for t in trips(on) if t["open_day"] == thu]
    assert sum(t["charges_actual_paise"] for t in thursday) == rest
    assert {t["charges_actual_basis"] for t in thursday} == {"month"}
    for part, want in (("brokerage", 12_000 - 4_000), ("stt", 5_000 - friday["stt_paise"]),
                       ("gst", 2_160 - friday["gst_paise"])):  # fmt: skip
        assert sum(t["charges_actual_parts"]["parts"][part] for t in thursday) == want
    # Stamp duty is a part the month left empty: skipped, named, and the trades say partial.
    assert all("stamp" not in t["charges_actual_parts"]["parts"] for t in thursday)
    assert all(t["charges_actual_parts"]["partial"] for t in thursday)
    assert all("stamp" in t["charges_actual_parts"]["missing"] for t in thursday)
    (friday_trip,) = [t for t in trips(on) if t["open_day"] == fri]
    assert friday_trip["charges_actual_basis"] == "day"
    # Given up and answered for: never asked again.
    line, asked = night(on, 20261111, [])
    assert asked == []


def test_a_row_is_ready_only_when_it_says_something():
    assert not actual.ready(None)
    assert not actual.ready({"total_paise": 0, "brokerage_paise": None, "stt_paise": 0})
    assert actual.ready({"total_paise": None, "stt_paise": 5})
    assert actual.parts_of({"total_paise": 10, "brokerage_paise": 4, "stt_paise": None}) == {
        **dict.fromkeys(actual.COLUMN_OF), "brokerage": 4, "unlisted": 6,
    }  # fmt: skip
    assert "unlisted" not in actual.parts_of({"total_paise": 4, "brokerage_paise": 4})
    assert "unlisted" not in actual.parts_of({"total_paise": None, "brokerage_paise": 4})


# ---- a trade has an actual only when every order of it has one ---------------------------------
def test_a_carried_trade_waits_for_both_its_days_and_a_reversal_shares_its_order(on):
    mon, tue = 20261109, 20261110
    carried = [
        fill("c1", "C1", mon, "BUY", 65, 100, "10:00", product="NRML"),
        fill("c2", "C2", tue, "SELL", 130, 120, "11:00", product="NRML"),  # closes, opens a short
        fill("c3", "C3", tue, "BUY", 65, 115, "12:00", product="NRML"),
    ]
    put(on, carried)
    charges_answers(on)
    on.now = ist_ms(TODAY, "22:00:00")

    def one_day(tx):
        tx.upstox_charges_put(
            charges.charges_row(answer_for(mon), "FO", mon, mon, 1, on.now), on.now)  # fmt: skip
        ids = actual.spread_day(tx, mon, "FO", "plus", on.now)
        return actual.apply_trips(tx, actual.trips_of_orders(tx, ids), on.now)

    assert on.svc.store.write(OWNER, one_day) == 0  # Monday alone: the long's sale has no share
    long, short = trips(on)
    assert long["charges_actual_paise"] is None and (long["side"], short["side"]) == (
        "long", "short")  # fmt: skip
    line, _ = night(on, TODAY, [tue, mon])
    assert "2 trades now carry actual charges" in line
    long, short = trips(on)
    by_order = {o["order_id"]: o for o in orders(on)}
    # Monday's whole figure is the long's entry order; Tuesday's sale of 130 is split 65:65.
    sale = by_order["C2"]["parts"]["parts"]
    assert long["charges_actual_parts"]["parts"]["stt"] + short["charges_actual_parts"]["parts"][
        "stt"] == sale["stt"] + by_order["C1"]["parts"]["parts"]["stt"] + by_order["C3"]["parts"][
        "parts"]["stt"]  # fmt: skip
    assert long["charges_actual_paise"] + short["charges_actual_paise"] == (
        answer_for(mon)["total_paise"] + answer_for(tue)["total_paise"])  # fmt: skip
    assert (
        abs(long["charges_actual_parts"]["parts"]["brokerage"] - by_order["C1"]["total_paise"])
        < answer_for(mon)["total_paise"]
    )
    half = sale["stt"] // 2
    assert long["charges_actual_parts"]["parts"]["stt"] - by_order["C1"]["parts"]["parts"][
        "stt"] in (half, half + 1)  # fmt: skip


# ---- a replay keeps the actual; a late fill is shared again ------------------------------------
def test_a_replay_keeps_the_actual_and_a_late_fill_is_shared_from_the_kept_row(on):
    mon = 20261109
    put(on, day_trade(mon))
    charges_answers(on)
    night(on, 20261110, [mon])
    (trip,) = trips(on)
    total = answer_for(mon)["total_paise"]
    assert trip["charges_actual_paise"] == total and trip["net_paise"] == 65_000 - total
    calls = len(on.portfolio.calls)

    put(on, day_trade(mon))  # the same day replayed (a close check, a nudge)
    (again,) = trips(on)
    assert (again["id"], again["charges_actual_paise"], again["net_paise"]) == (
        trip["id"], total, 65_000 - total)  # fmt: skip
    assert again["charges_est_paise"] == trip["charges_est_paise"] != total

    put(on, day_trade(mon, 1))  # a fill the close check brings later: two more orders
    first, late = trips(on)
    assert len(orders(on, day=mon)) == 4 and sum(
        o["total_paise"] for o in orders(on, day=mon)) == total  # fmt: skip
    assert first["charges_actual_paise"] + late["charges_actual_paise"] == total
    assert late["charges_actual_paise"] > 0 and first["charges_actual_paise"] < total
    assert late["net_paise"] == late["gross_paise"] - late["charges_actual_paise"]
    assert len(on.portfolio.calls) == calls  # from the kept row: Upstox is not asked again


def test_a_trade_whose_order_loses_its_share_goes_back_to_the_estimate(on):
    mon = 20261109
    put(on, day_trade(mon))
    charges_answers(on)
    night(on, 20261110, [mon])

    def drop(tx):
        tx.charges_orders_put("live", mon, "FO", [], on.now)
        return actual.apply_trips(tx, [t["id"] for t in tx.rows("round_trip")], on.now)

    assert on.svc.store.write(OWNER, drop) == 0
    (trip,) = trips(on)
    assert trip["charges_actual_paise"] is None and trip["charges_actual_parts"] is None
    assert trip["charges_actual_basis"] is None
    assert trip["net_paise"] == trip["gross_paise"] - trip["charges_est_paise"]


def test_the_sandbox_journal_never_carries_actual_charges(on):
    mon = 20261109
    put(on, [{**f, "mode": "sandbox"} for f in day_trade(mon)], mode="sandbox")
    charges_answers(on)
    line, asked = night(on, 20261110, [mon])
    assert asked == [] and line == "charges: nothing to ask Upstox for"
    assert trips(on)[0]["charges_actual_paise"] is None


# ---- the switch, the catch-up, the sheet -------------------------------------------------------
def test_switched_off_nothing_is_asked_and_the_catch_up_keeps_its_year_row(rig):  # noqa: F811
    mon = 20261109
    put(rig, day_trade(mon))
    charges_answers(rig)
    line, asked = night(rig, 20261110, [mon])
    assert line == charges.OFF and asked == []
    assert trips(rig)[0]["charges_actual_paise"] is None


def test_the_catch_up_runs_the_charges_step_once_a_day(on):
    mon = 20261109
    put(on, day_trade(mon))
    on.svc.store.write(OWNER, lambda tx: tx.day_close_put(
        "live", mon, {"reconciled_at": 1, "reconcile_note": "checked"}, 1))  # fmt: skip
    on.answer_trades([])
    on.portfolio.answers["holdings"] = {"state": "ok", "read_at": 1, "note": None, "data": []}
    on.portfolio.answers["historical_trades"] = {"state": "ok", "read_at": 1, "note": None,
                                                 "total_pages": 1, "rows": []}  # fmt: skip

    def answer(args):
        if args["part"] == "data":
            return {"state": "ok", "read_at": 1, "rows": [], "page_size": 5000}
        assert "from" in args, "the year-so-far charges are not read while the switch is on"
        return {"state": "ok", "read_at": 1, "charges": answer_for(args["from"])}

    on.portfolio.answers["pnl_report"] = answer
    on.now = ist_ms(20261110, "22:00:00")
    line = scenario(on, lambda: catchup.catch_up(on.svc, 20261110, on.now, False))
    assert "charges: 1 days and 0 months answered by Upstox" in line
    assert trips(on)[0]["charges_actual_paise"] == answer_for(mon)["total_paise"]
    assert [r["basis"] for r in rows(on)] == ["day"]
    asked = len(on.portfolio.calls)
    scenario(on, lambda: catchup.catch_up(on.svc, 20261110, on.now, False))
    assert [c[0] for c in on.portfolio.calls[asked:]].count("pnl_report") == 0


def test_a_portfolio_that_stops_answering_keeps_what_was_read(on):
    put(on, day_trade(20261109) + day_trade(20261110))
    seen = []

    def answer(args):
        seen.append(args["from"])
        if len(seen) > 1:
            return (503, {"status": "error", "code": "unavailable"})
        return {"state": "ok", "read_at": 1, "charges": answer_for(args["from"])}

    on.portfolio.answers["pnl_report"] = answer
    line, _ = night(on, TODAY, [20261110, 20261109])
    assert "1 days and 0 months answered" in line and line.endswith("1 not read")
    assert [t["charges_actual_paise"] is None for t in trips(on)] == [False, True]


def test_the_trade_sheet_lists_the_parts_upstox_charged(on):
    mon = 20261109
    put(on, day_trade(mon))
    charges_answers(on)
    (trip,) = trips(on)

    async def sheet():
        return await REGISTRY["trip_get"](on.svc, {"id": trip["id"]})

    before = scenario(on, sheet)
    assert before["charges_actual"] is None and before["charges_actual_basis"] is None
    night(on, 20261110, [mon])
    got = scenario(on, sheet)
    want = answer_for(mon)
    assert (
        got["charges_actual_paise"] == want["total_paise"] and got["charges_actual_basis"] == "day"
    )
    shown = got["charges_actual"]
    assert shown["total_paise"] == want["total_paise"] == sum(p["paise"] for p in shown["parts"])
    assert [(p["part"], p["label"]) for p in shown["parts"]][:3] == [
        ("brokerage", "Brokerage"), ("gst", "GST"), ("stt", "STT"),
    ]  # fmt: skip
    assert shown["parts"][-1] == {"part": "unlisted", "label": "Not itemised by Upstox",
                                  "paise": 3}  # fmt: skip
    assert shown["partial"] is False and shown["missing"] == ["Depository charge"]
    assert got["charges"]["parts"]["total_paise"] == trip["charges_est_paise"]  # still shown
    assert got["net_paise"] == got["gross_paise"] - want["total_paise"]
