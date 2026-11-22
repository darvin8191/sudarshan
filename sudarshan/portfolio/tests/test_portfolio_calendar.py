"""The corporate-action calendar (M7.36; dividends.calendar, design p7 9.5): Upstox's events for
the instruments that matter, 90 days back and 180 on, each with its public facts and, apart from
them, what is held and what a dividend is expected to bring. Through the sealed API; no network."""

from __future__ import annotations

import dataclasses

import httpx
import pytest
from test_portfolio_ca_ops import event, history, record, scenario
from test_portfolio_closed_world import answer_misses
from test_portfolio_corpactions_reader import HELD, TRADED, UPSTOX
from test_portfolio_dividends import buy
from test_portfolio_import_ops import NOW, OWNER
from test_portfolio_purposes import FAM, JOURNAL, JRN

from sudarshan.autologin.handoff import seal
from sudarshan.portfolio import corpactions as ca
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
    record(n, 20261023, **{UPSTOX: 10})  # a share the account holds: its events are listed
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
    history(n, (20260601, "BUY", 5), (20260701, "SELL", 5), isin=TRADED)  # traded, held no more
    not_held = event(n, "dividend", 20261106, amount_u=20000, isin=TRADED)
    n.svc.store.write(OWNER, lambda tx: tx.ca_read_put(UPSTOX, NOW - 5, "ok", None))
    n.svc.store.write(OWNER, lambda tx: tx.ca_read_put(TRADED, NOW, "none", "nothing listed"))
    rows = {row["event_id"]: row for row in (await calendar(n))["calendar"]}
    # Past: 300 held the day before, Rs 5.50 a share. To come: the 250 held now, Rs 30 a share.
    assert rows[past] == {
        "event_id": past, "isin": UPSTOX, "name": None, "kind": "dividend", "ex_day": 20260814,
        "amount_u": 55000, "ratio_num": None, "ratio_den": None, "held_qty_u": 300 * U,
        "expected_paise": 1650 * RUPEE, "checked_at": NOW - 5, "decided": None,
        "holding": True, "watchlist": False,
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
async def test_an_empty_calendar_says_whether_the_list_was_read_at_all(n):
    """Review M1 and H2. Old code answered only ``listed_future: false`` and an empty list on a
    store nothing was ever read into, and the same after a night of refusals."""
    data = await calendar(n)
    assert (data["calendar"], data["listed_future"]) == ([], False)
    assert data["read_status"] == {"state": "never", "night": None, "words": ca.FIRST_READ}
    assert ca.FIRST_READ == ("SUDARSHAN reads Upstox's corporate actions for the first time "
                             "tonight after 23:45. Nothing is listed until then.")  # fmt: skip
    n.svc.store.write(
        OWNER,
        lambda tx: tx.job_put("corporate_actions", 20261025, "failed",
                              f"0 of 4 instruments read, 0 with nothing listed, 0 not read. "
                              f"{ca.WORDS['refused']}", NOW),
    )  # fmt: skip
    status = (await calendar(n))["read_status"]
    assert (status["state"], status["night"]) == ("failed", 20261025)
    assert status["words"].startswith("Upstox's list could not be read last night. Upstox did "
                                      "not allow SUDARSHAN to read its list of events")  # fmt: skip
    n.svc.store.write(OWNER, lambda tx: tx.job_put("corporate_actions", 20261026, "ok", "", NOW))
    assert (await calendar(n))["read_status"] == {"state": "ok", "night": 20261026, "words": None}
    assert answer_misses(data) == set()


@scenario
async def test_a_share_held_only_at_upstox_has_its_name(n):
    """Review M2. Old code took names from the owner's tabs only: an Upstox-only share read as
    its ISIN in the calendar, in To confirm and in the dividends."""
    from test_portfolio_ca_ops import history

    record(n, 20261023, **{UPSTOX: 250})
    event(n, "dividend", 20261110, amount_u=300000)
    assert (await calendar(n))["calendar"][0]["name"] is None  # nothing names it yet
    history(n, (20260610, "BUY", 250))  # Upstox's trade history calls it PROBE LTD
    assert (await calendar(n))["calendar"][0]["name"] == "PROBE LTD"
    # The holding's own name, as the Upstox account gives it, comes before the scrip name.
    n.reader._held_now = lambda: [{"isin": UPSTOX, "name": "Probe Industries Limited"}]
    assert (await calendar(n))["calendar"][0]["name"] == "Probe Industries Limited"


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
async def test_what_was_confirmed_is_on_the_events_row_and_is_changed_from_its_version(n):
    """v0.14.0 verification M3. No control on the phone changed a decision: a split applied
    the wrong way round, or left out of the lots when it was right, stayed so. The event's row
    in the Calendar now carries what was confirmed for the Upstox account, with its version and
    the choices To confirm offered, in the same words; ``ca_decide`` from that version changes
    it, which is also the way back for a split left out of the lots (H2)."""
    from test_portfolio_ca_ops import SPLIT_DAY, check_of, history, lots_after, waiting

    history(n, (20260610, "BUY", 100))
    record(n, 20261008, **{UPSTOX: 100})
    record(n, 20261021, **{UPSTOX: 500})
    split = event(n, "split", ex_day=SPLIT_DAY, ratio=(1, 5))
    assert (await calendar(n))["calendar"][0]["decided"] is None  # it waits under To confirm
    asked = (await waiting(n))[0]["expected"][0]
    wrong = await n.rig.call("ca_decide", id=split, scope="upstox", state="applied",
                             ratio_num=1, ratio_den=5)  # fmt: skip
    assert wrong["status"] == "success"

    async def decided() -> dict:
        return (await calendar(n))["calendar"][0]["decided"]

    first = await decided()
    version = n.read(lambda tx: tx.rows("ca_decision", event_id=split))[0]["version"]
    assert {key: first[key] for key in first if key != "expected"} == {
        "event_id": split, "version": version, "state": "applied", "ratio_num": 1,
        "ratio_den": 5, "words": "You applied this split as every 5 made 1: 100 became 20."}  # fmt: skip
    assert first["expected"] == asked  # the same choices, in To confirm's words
    turned = {"id": first["event_id"], "scope": "upstox", "state": "applied", "ratio_num": 5,
              "ratio_den": 1, "version": first["version"]}  # fmt: skip
    assert (await n.rig.call("ca_decide", **turned))["status"] == "success"
    assert lots_after(n)[0] == [(20260610, 500, 1_000_000)]
    assert (await n.rig.call("ca_decide", **turned))["status"] != "success"  # an old version
    # Left out of the lots from the row; the Check names it and points back to the row.
    now = await decided()
    left = await n.rig.call("ca_decide", id=split, scope="upstox", state="dismissed",
                            ratio_num=1, ratio_den=1, version=now["version"])  # fmt: skip
    assert left["status"] == "success"
    now = await decided()
    assert (now["state"], now["ratio_num"], now["words"]) == (
        "dismissed", None, "You left your lots as they are for this split.")  # fmt: skip
    (row,) = (await check_of(n))["rows"]
    assert row["words"].startswith(
        "PROBE LTD: Upstox holds 500 and the lots say 100. You left the split of 9 Oct 2026 out "
        "of your lots: if the company did give it, change that under Confirmed in the Calendar. "
    )
    assert row["event_id"] == split  # the Check row leads to it (v0.14.0 verification N1)
    back = await n.rig.call("ca_decide", id=split, scope="upstox", state="applied", ratio_num=5,
                            ratio_den=1, version=now["version"])  # fmt: skip
    assert back["status"] == "success" and (await check_of(n))["rows"] == []
    assert answer_misses(await calendar(n)) == set()


@scenario
async def test_every_bonus_or_split_confirmed_is_listed_whatever_its_day_and_changed_there(n):
    """v0.14.0 verification N1, its probe. A split of 1 Jun 2026, 147 days back, tapped the
    wrong way round on 26 Oct; 50 older shares are outside the records, so neither reading makes
    the lots agree with the 750 held and no tap is offered. Old code carried what was confirmed
    only on calendar rows inside the 90 days: this split had none, so nothing on the phone could
    change it, and the Check card's sentence sent the owner to a row that was not there. Every
    bonus and split confirmed for the Upstox account is now listed under Confirmed, whatever its
    day and whether Upstox still lists it, and the Check row leads there."""
    from test_portfolio_ca_ops import check_of, history

    history(n, (20260410, "BUY", 100))
    record(n, 20261021, **{UPSTOX: 750})
    split = event(n, "split", ex_day=20260601, ratio=(1, 5))
    wrong = await n.rig.call("ca_decide", id=split, scope="upstox", state="applied",
                             ratio_num=1, ratio_den=5)  # fmt: skip
    assert wrong["status"] == "success"
    assert (await calendar(n))["calendar"] == []  # outside the 90 days
    listed = (await n.rig.call("ca_list"))["data"]
    (row,) = listed["lot_check"]["rows"]
    assert (row["held_qty_u"], row["lots_qty_u"], row["fix"], row["event_id"]) == (
        750 * U, 20 * U, None, split)  # fmt: skip
    assert row["words"].endswith(
        "Neither reading of it makes the two agree: a purchase or a sale may be missing from the "
        "records. You can see the reading you confirmed, and change it, under Confirmed in the "
        "Calendar."
    )
    (kept,) = listed["confirmed"]
    assert {key: kept[key] for key in kept if key != "decided"} == {
        "event_id": split, "isin": UPSTOX, "name": "PROBE LTD", "kind": "split",
        "ex_day": 20260601, "ratio_num": 1, "ratio_den": 5, "gone_at": None}  # fmt: skip
    decided = kept["decided"]
    assert (decided["event_id"], decided["state"], decided["words"]) == (
        split, "applied", "You applied this split as every 5 made 1: 100 became 20.")  # fmt: skip
    assert decided["expected"]["held_from"] == "trades"  # no closing record reaches 31 May
    assert answer_misses(listed) == set()
    # Upstox no longer lists it: what was confirmed still counts, is listed, and is changed there.
    n.svc.store.write(OWNER, lambda tx: tx.ca_events_gone(UPSTOX, [], NOW))
    (kept,) = (await n.rig.call("ca_list"))["data"]["confirmed"]
    assert kept["gone_at"] == NOW
    turned = await n.rig.call("ca_decide", id=kept["decided"]["event_id"], scope="upstox",
                              state="applied", ratio_num=5, ratio_den=1,
                              version=kept["decided"]["version"])  # fmt: skip
    assert turned["status"] == "success"
    (row,) = (await check_of(n))["rows"]
    assert (row["lots_qty_u"], row["event_id"]) == (500 * U, split)  # the 50 are still missing
    # Nothing is listed for a tab's scope, and nothing for a bonus or split still undecided.
    assert (await n.rig.call("ca_list", scope=f"tab:{n.rig.tab['id']}"))["data"]["confirmed"] == []


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
