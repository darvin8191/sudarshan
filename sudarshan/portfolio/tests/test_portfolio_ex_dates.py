"""The screener purpose of the sealed API and its one op, ``ex_dates`` (M9.13, gap audit GA8.3).

The screener's "days to ex-date" line counts from the corporate actions the nightly read keeps;
it never asks Upstox. Through the sealed API on a temporary store, on the fixed day Wednesday 31
March 2027 (the last day of a financial year): no network, no wall-clock date.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import logging
from datetime import datetime

import httpx
import pytest
from test_portfolio_import_ops import FERNET, OWNER, Rig
from test_portfolio_purposes import FAM, JOURNAL, JRN
from test_portfolio_service import PEPPER

from sudarshan.autologin.handoff import seal
from sudarshan.autologin.schedule import IST
from sudarshan.portfolio import corpactions as ca
from sudarshan.portfolio import store as store_module
from sudarshan.portfolio.core import kinds
from sudarshan.vault import PORTFOLIO_SCREENER_API_INFO, fernet_for

SCR = fernet_for(PEPPER, PORTFOLIO_SCREENER_API_INFO)
SCREENER = "sudarshan-screener"
DAY_MS = 86_400_000
NOW = int(datetime(2027, 3, 31, 12, 0, tzinfo=IST).timestamp() * 1000)
READ, WATCHED, FAILED, STALE, NEVER = (
    "INE000A01011", "INE000B01011", "INE000C01011", "INE000D01011", "INE000E01011",
)  # fmt: skip


@pytest.fixture(autouse=True)
def _stop_thread():
    yield
    store_module.stop()


def scenario(test):
    def wrapped(tmp_path, caplog):
        async def go():
            rig = Rig(tmp_path)
            rig.now = NOW
            rig.svc.settings = dataclasses.replace(rig.svc.settings, insights=True)
            rig.svc.api.purposes += [("family", FAM), ("journal", JRN), ("screener", SCR)]
            await rig.svc.api.start(0)
            try:
                more = (caplog,) if "caplog" in inspect.signature(test).parameters else ()
                await test(rig, *more)
            finally:
                await rig.svc.api.close()
                rig.svc.store.close()

        asyncio.run(go())

    wrapped.__name__ = test.__name__
    return wrapped


async def ask(rig, fernet, principal, op, **args):
    async with httpx.AsyncClient(trust_env=False) as client:
        answer = await client.post(
            f"http://127.0.0.1:{rig.svc.api.port}/v1/{op}",
            content=seal(fernet, op, principal=principal, args=args), timeout=20,
        )  # fmt: skip
    return answer.status_code, answer.json()


def nightly(rig) -> None:
    """What the nightly read left: a share read yesterday with its events (one gone since, one
    past the week back, one past the span on), a watched share read 15 days ago listing none, a
    share that only failed, one last read 16 days ago, and an event of a share never read."""

    def write(tx):
        def put(isin, kind, ex_day):
            row = {"isin": isin, "kind": kind, "ex_day": ex_day, "details": []}
            row["raw_hash"] = ca.event_hash(row)
            return tx.ca_event_put(row, NOW - DAY_MS)[0]["raw_hash"]

        kept = [put(READ, "bonus", 20270327), put(READ, "dividend", 20270401)]
        kept += [put(READ, "dividend", 20270320), put(READ, "split", 20280601)]
        put(READ, "rights", 20270405)
        tx.ca_events_gone(READ, kept, NOW - DAY_MS)  # Upstox no longer lists the rights issue
        put(STALE, "dividend", 20270402)
        put(NEVER, "dividend", 20270402)
        tx.ca_read_put(READ, NOW - DAY_MS, "ok", None)
        tx.ca_read_put(WATCHED, NOW - 15 * DAY_MS, "none", ca.WORDS["none"])
        tx.ca_read_put(FAILED, NOW - DAY_MS, "failed", ca.WORDS["silent"])
        tx.ca_read_put(STALE, NOW - 16 * DAY_MS, "ok", None)

    rig.svc.store.write(OWNER, write)


# ---- what the screener is given ------------------------------------------------------------------
@scenario
async def test_the_screener_is_given_each_cleanly_read_shares_ex_dates_and_nothing_else(rig):
    nightly(rig)
    status, body = await ask(rig, SCR, SCREENER, "ex_dates")
    assert status == 200, body
    assert body["data"] == {
        "as_of": NOW - DAY_MS,
        "shares": [
            # Saturday 27 March is within the week back (it takes effect at Monday's session);
            # Thursday 1 April is in the next financial year; 20 March and June 2028 are out.
            {"isin": READ, "events": [["bonus", 20270327], ["dividend", 20270401]]},
            {"isin": WATCHED, "events": []},  # read 15 days ago, nothing listed: none to come
        ],
    }  # the share that only failed, the one read 16 days ago and the one never read: not known


@scenario
async def test_nothing_read_is_an_empty_answer_not_an_error(rig):
    status, body = await ask(rig, SCR, SCREENER, "ex_dates")
    assert (status, body["data"]) == (200, {"as_of": None, "shares": []})


@scenario
async def test_the_answer_carries_no_account_figure(rig):
    nightly(rig)
    data = (await ask(rig, SCR, SCREENER, "ex_dates"))[1]["data"]
    assert set(data) == {"as_of", "shares"}
    assert {key for share in data["shares"] for key in share} == {"isin", "events"}
    assert {event[0] for share in data["shares"] for event in share["events"]} <= set(
        kinds.CA_KINDS
    )


def test_the_span_is_the_screeners():
    from sudarshan.screener import events

    assert ca.EX_DATES_AHEAD_DAYS == events.REACH_DAYS
    assert ca.EX_DATES_FRESH_DAYS == 15


# ---- who may ask ---------------------------------------------------------------------------------
@scenario
async def test_the_screener_purpose_opens_ex_dates_only_and_a_write_is_403_and_logged(rig, caplog):
    assert kinds.SCREENER_OPS == ("ex_dates",)
    with caplog.at_level(logging.WARNING, logger="sudarshan.portfolio"):
        for name in (n for n in kinds.SERVICE_OP_NAMES if n not in kinds.SCREENER_OPS):
            assert (await ask(rig, SCR, SCREENER, name, id=1))[0] == 403, name
    assert "refused the op tab_create under the screener purpose" in caplog.text
    assert (await ask(rig, SCR, "owner", "ex_dates"))[0] == 403  # the purpose's own principal
    assert (await ask(rig, SCR, JOURNAL, "ex_dates"))[0] == 403
    assert len(rig.book.tabs(OWNER)) == 2


@scenario
async def test_no_other_purpose_opens_ex_dates(rig):
    """The owner's bridge, a family member and the journal are each refused: it is not on their
    lists, as the screener's key opens none of theirs."""
    assert not set(kinds.SCREENER_OPS) & {*kinds.OP_NAMES, *kinds.FAMILY_OPS, *kinds.JOURNAL_OPS}
    for fernet, who in ((FERNET, "owner"), (FAM, "viewer:7"), (JRN, JOURNAL)):
        status, body = await ask(rig, fernet, who, "ex_dates")
        assert (status, body["status"]) == (403, "error"), who


@scenario
async def test_it_waits_for_the_insights_switch(rig):
    rig.svc.settings = dataclasses.replace(rig.svc.settings, insights=False)
    status, body = await ask(rig, SCR, SCREENER, "ex_dates")
    assert (status, body["code"]) == (501, "not_ready")
    assert kinds.SWITCH_OF["ex_dates"] == "insights"


def test_the_service_holds_the_screeners_key(tmp_path):
    from test_portfolio_service import settings_for

    from sudarshan.portfolio.service import Service

    svc = Service(settings_for(tmp_path), FERNET, jobs={}, screener_fernet=SCR)
    assert ("screener", SCR) in svc.api.purposes
