"""The corporate-action calendar (M7.36; dividends.calendar, design p7 9.5): Upstox's events for
the instruments that matter, 90 days back and 180 on, each with its public facts and, apart from
them, what is held and what a dividend is expected to bring. Through the sealed API; no network."""

from __future__ import annotations

import dataclasses

import httpx
import pytest
from test_portfolio_ca_ops import event, record, scenario
from test_portfolio_closed_world import answer_misses
from test_portfolio_corpactions_reader import HELD, TRADED, UPSTOX
from test_portfolio_dividends import buy
from test_portfolio_import_ops import NOW, OWNER
from test_portfolio_purposes import FAM, JOURNAL, JRN

from sudarshan.autologin.handoff import seal
from sudarshan.portfolio import store as store_module
from sudarshan.portfolio.core import kinds

U = 1_000_000
TODAY = 20261026
RUPEE = 100


@pytest.fixture(autouse=True)
def _stop_thread():
    yield
    store_module.stop()


async def calendar(n, **args):
    answer = await n.rig.call("calendar", **args)
    assert answer["status"] == "success", answer
    return answer["data"]


@scenario
async def test_the_calendar_reaches_ninety_days_back_and_a_hundred_and_eighty_on(n):
    assert (kinds.CALENDAR_PAST_DAYS, kinds.CALENDAR_NEXT_DAYS) == (90, 180)
    # 26 Oct 2026 less 90 days is 28 Jul 2026; plus 180 days is 24 Apr 2027.
    days = (20260727, 20260728, 20261026, 20270424, 20270425)
    for day in days:
        event(n, "dividend", day, amount_u=10000)
    data = await calendar(n)
    assert (data["from"], data["to"]) == (20260728, 20270424)
    assert [row["ex_day"] for row in data["calendar"]] == list(days[1:4])
    asked = await calendar(n, **{"from": 20270425, "to": 20270430})
    assert [row["ex_day"] for row in asked["calendar"]] == [20270425]
    for bad in ({"from": 20261301}, {"from": 20261101, "to": 20261001}):
        assert (await n.rig.call("calendar", **bad))["code"] == "bad_request"


@scenario
async def test_a_row_is_the_public_facts_and_apart_from_them_what_is_held(n):
    record(n, 20260813, **{UPSTOX: 300})
    record(n, 20261023, **{UPSTOX: 250})  # the last closing record: what is held now
    buy(n, 20260601, 100, "e-1")  # the rig's share, in a custom tab
    past = event(n, "dividend", 20260814, amount_u=55000)
    to_come = event(n, "dividend", 20261110, amount_u=300000)
    bonus = event(n, "bonus", 20261120, ratio=(1, 2))
    in_tab = event(n, "dividend", 20261105, amount_u=20000, isin=HELD)
    not_held = event(n, "dividend", 20261106, amount_u=20000, isin=TRADED)
    n.svc.store.write(OWNER, lambda tx: tx.ca_read_put(UPSTOX, NOW - 5, "ok", None))
    n.svc.store.write(OWNER, lambda tx: tx.ca_read_put(TRADED, NOW, "none", "nothing listed"))
    rows = {row["event_id"]: row for row in (await calendar(n))["calendar"]}
    # Past: 300 held the day before, Rs 5.50 a share. To come: the 250 held now, Rs 30 a share.
    assert rows[past] == {
        "event_id": past, "isin": UPSTOX, "name": None, "kind": "dividend", "ex_day": 20260814,
        "amount_u": 55000, "ratio_num": None, "ratio_den": None, "held_qty_u": 300 * U,
        "expected_paise": 1650 * RUPEE, "checked_at": NOW - 5,
    }  # fmt: skip
    assert (rows[to_come]["held_qty_u"], rows[to_come]["expected_paise"]) == (250 * U, 7500 * RUPEE)
    assert (rows[bonus]["ratio_num"], rows[bonus]["ratio_den"], rows[bonus]["held_qty_u"],
            rows[bonus]["expected_paise"]) == (1, 2, 250 * U, None)  # fmt: skip
    assert (rows[in_tab]["name"], rows[in_tab]["held_qty_u"], rows[in_tab]["expected_paise"]) == (
        "Reliance Industries", 100 * U, 200 * RUPEE)  # fmt: skip
    assert (rows[not_held]["held_qty_u"], rows[not_held]["expected_paise"],
            rows[not_held]["checked_at"]) == (None, None, None)  # fmt: skip
    assert list(rows) == [past, in_tab, not_held, to_come, bonus]  # by ex-date
    rights = event(n, "rights", 20261125, ratio=(3, 10), amount_u=1202500)  # a price, not a payout
    row = (await calendar(n))["calendar"][-1]
    assert (row["event_id"], row["amount_u"], row["expected_paise"]) == (rights, 1202500, None)
    assert answer_misses({"calendar": list(rows.values())}) == set()
    assert n.read(lambda tx: tx.rows("dividend_line")) == []  # the calendar makes no line


@scenario
async def test_what_is_held_in_the_upstox_account_and_in_a_tab_adds_up(n):
    record(n, 20261023, **{HELD: 40})
    buy(n, 20260601, 100, "e-1")
    event(n, "dividend", 20261105, amount_u=20000, isin=HELD)
    (row,) = (await calendar(n))["calendar"]
    assert (row["held_qty_u"], row["expected_paise"]) == (140 * U, 280 * RUPEE)


@scenario
async def test_it_says_when_upstox_lists_nothing_to_come(n):
    event(n, "dividend", 20260814, amount_u=55000)
    gone = event(n, "dividend", 20261110, amount_u=300000)
    assert (await calendar(n))["listed_future"] is True
    n.svc.store.write(OWNER, lambda tx: tx.ca_events_gone(UPSTOX, [], NOW))
    data = await calendar(n)
    assert (
        data["listed_future"] is False and data["calendar"] == []
    )  # a withdrawn event is not shown
    event(n, "split", 20280101, ratio=(1, 5), isin=TRADED)  # outside the range asked, still to come
    data = await calendar(n)
    assert data["listed_future"] is True and data["calendar"] == [] and gone


@scenario
async def test_no_family_member_and_no_other_service_sees_the_calendar(n):
    assert "calendar" not in {*kinds.FAMILY_OPS, *kinds.JOURNAL_OPS, *kinds.SHARING_OPS}
    async with httpx.AsyncClient(trust_env=False) as client:
        for fernet, who in ((FAM, "viewer:7"), (JRN, JOURNAL)):
            answer = await client.post(
                f"http://127.0.0.1:{n.svc.api.port}/v1/calendar",
                content=seal(fernet, "calendar", principal=who, args={}), timeout=20,
            )  # fmt: skip
            assert answer.status_code == 403, who
    n.svc.settings = dataclasses.replace(n.svc.settings, insights=False)
    assert (await n.rig.call("calendar"))["code"] == "not_ready"
