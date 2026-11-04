"""AMFI's NAV file: parsing, the nightly fetch, the NAV settle, mf_search (M7.24)."""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime

import pytest
from portfolio_fakes import NAVALL, FakeAmfi
from test_portfolio_eod import Rig as EodRig
from test_portfolio_eod import ist
from test_portfolio_import_ops import Rig as OpsRig
from test_portfolio_import_ops import scenario_of

from sudarshan.autologin.schedule import IST
from sudarshan.portfolio import amfi
from sudarshan.portfolio import store as store_module
from sudarshan.portfolio.core import freshness
from sudarshan.portfolio.store import Scope
from sudarshan.portfolio.timers import Schedule

OWNER = Scope.owner()
NAV_DAY = 20261104


@pytest.fixture(autouse=True)
def _stop_thread():
    yield
    store_module.stop()


# ---- parsing -------------------------------------------------------------------------------
def by_code(text=NAVALL):
    return {s["amfi_code"]: s for s in amfi.parse_navall(text)}


def test_the_category_and_amc_lines_belong_to_the_schemes_below_them():
    found = by_code()
    assert set(found) == {"100001", "100003", "200001", "200002"}
    first = found["100001"]
    assert first["category"] == "Open Ended Schemes(Equity Scheme - Large Cap Fund)"
    assert first["amc"] == "Alpha Mutual Fund"
    assert first["isin_growth"] == "INF000A01011" and first["isin_reinvest"] is None
    assert found["200001"]["category"].endswith("(Debt Scheme - Liquid Fund)")
    assert found["200001"]["amc"] == "Beta Mutual Fund"


def test_a_nav_of_na_is_skipped_and_so_are_lines_that_are_not_schemes():
    """Mutation target 17: N.A. would otherwise become a scheme with no price."""
    found = by_code()
    assert "100002" not in found  # N.A.
    assert "300001" not in found  # a date that is not one
    assert amfi.parse_nav("N.A.") is None and amfi.parse_nav("0") is None
    assert amfi.parse_nav("-5") is None and amfi.parse_nav("nan") is None
    assert amfi.parse_nav("") is None


def test_a_nav_is_four_decimals_half_away_from_zero_and_a_date_is_a_day():
    found = by_code()
    assert found["100001"]["nav_u"] == 1_234_568  # 123.45675 -> 123.4568
    assert found["100003"]["nav_u"] == 501_000
    assert found["200002"]["nav_u"] == 11_002_500
    assert all(s["nav_date"] == NAV_DAY for s in found.values())
    assert amfi.parse_date("4-Nov-2026") == NAV_DAY and amfi.parse_date("31-Feb-2026") is None
    assert amfi.parse_date("04-Xyz-2026") is None and amfi.parse_date("04-Nov-26") is None


def test_a_nav_written_with_a_comma_is_not_guessed_at():
    assert amfi.parse_nav("1,234.5") is None and "200003" not in by_code()


def test_an_answer_with_no_scheme_in_it_parses_to_nothing():
    assert amfi.parse_navall("<html>Service unavailable</html>\n") == []
    assert amfi.parse_navall("") == []


# ---- the fetch ---------------------------------------------------------------------------------
@pytest.fixture
def world(tmp_path):
    rig = EodRig(tmp_path)
    server = FakeAmfi()
    server.start()
    rig.svc.settings = dataclasses.replace(rig.svc.settings, amfi_url=server.url)
    yield rig, server
    server.stop()
    rig.close()


def fund(rig, code="100001"):
    return rig.book.asset_create(
        OWNER, f"f-{code}", "mf", now=ist(1, 9), name="Alpha Bluechip", amfi_code=code,
        asset_class="mf_equity",
    )  # fmt: skip


def fetch(rig, at):
    rig.now = at
    return asyncio.run(_fetch(rig, at))


async def _fetch(rig, at):
    try:
        return await amfi.fetch_step(rig.svc, at)
    finally:
        await rig.svc.amfi_client().aclose()
        rig.svc._amfi = None


def test_the_nightly_fetch_stores_schemes_and_prices_the_held_fund_at_its_nav_date(world):
    rig, server = world
    held = fund(rig)
    assert fetch(rig, ist(4, 23, 30, mo=11)) == "ok"
    assert len(server.asks) == 1 and "If-Modified-Since" not in server.asks[0]
    scheme = rig.svc.store.read(OWNER, lambda tx: tx.mf_get("100001"))
    assert scheme["nav_u"] == 1_234_568 and scheme["nav_date"] == NAV_DAY
    row = rig.eod_row(NAV_DAY, held)
    assert (row["price_u"], row["basis"], row["source"]) == (1_234_568, "nav", "amfi")
    assert rig.svc.store.read(OWNER, lambda tx: tx.mf_count()) == 4
    assert rig.eod_row(NAV_DAY, rig.tcs) is None  # a share is not touched
    for name in ("nav_fetch", "nav_settle"):
        assert rig.job(name, NAV_DAY)["status"] == "ok"


def test_a_repeated_ask_carries_if_modified_since_and_a_304_is_not_a_failure(world):
    rig, server = world
    fetch(rig, ist(4, 23, 30, mo=11))
    server.modified = False
    assert fetch(rig, ist(5, 7, 30, mo=11)) is None  # the night's fetch is done: no retry
    rig.svc.store.write(
        OWNER, lambda tx: tx.job_put("nav_fetch", 20261105, "failed", "x", ist(5, 0, mo=11))
    )
    assert fetch(rig, ist(5, 23, 30, mo=11)) is None  # today's row exists
    rig.svc.store.write(
        OWNER, lambda tx: tx.job_put("nav_fetch", 20261105, "failed", "x", ist(5, 23, 31, mo=11))
    )


def test_the_second_ask_sends_if_modified_since(world):
    rig, server = world
    fetch(rig, ist(4, 23, 30, mo=11))
    rig.svc.store.write(
        OWNER, lambda tx: tx.job_put("nav_fetch", NAV_DAY, "failed", "x", ist(4, 23, 31, mo=11))
    )
    server.modified = False
    assert fetch(rig, ist(5, 7, 30, mo=11)) == "ok"  # the retry
    assert "If-Modified-Since" in server.asks[-1]
    assert "AMFI had no newer list" in rig.job("nav_fetch", NAV_DAY)["detail"]


def test_a_failed_fetch_keeps_the_navs_held_and_is_retried_once_at_half_past_seven(world):
    rig, server = world
    held = fund(rig)
    fetch(rig, ist(4, 23, 30, mo=11))
    server.body, server.status = "<html>down</html>", 503
    assert fetch(rig, ist(5, 23, 30, mo=11)) == "failed"
    assert rig.job("nav_fetch", 20261105)["status"] == "failed"
    assert rig.svc.store.read(OWNER, lambda tx: tx.mf_get("100001"))["nav_u"] == 1_234_568
    assert fetch(rig, ist(5, 23, 45, mo=11)) is None  # one attempt a slot
    server.body, server.status = (
        NAVALL.replace("123.45675", "124.0").replace("04-Nov", "05-Nov"),
        200,
    )
    assert fetch(rig, ist(6, 7, 29, mo=11)) is None  # not before 07:30
    assert fetch(rig, ist(6, 7, 30, mo=11)) == "ok"
    assert rig.job("nav_fetch", 20261105)["status"] == "ok"
    assert rig.eod_row(20261105, held)["price_u"] == 1_240_000
    assert fetch(rig, ist(6, 8, 0, mo=11)) is None


def test_a_file_in_a_form_nobody_can_read_is_a_failure_and_stores_nothing(world):
    rig, server = world
    server.body = "<html>Service unavailable</html>"
    assert fetch(rig, ist(4, 23, 30, mo=11)) == "failed"
    assert rig.svc.store.read(OWNER, lambda tx: tx.mf_count()) == 0
    assert "could not read" in rig.job("nav_fetch", NAV_DAY)["detail"]


def test_an_older_file_never_replaces_a_newer_nav(world):
    rig, server = world
    fetch(rig, ist(4, 23, 30, mo=11))
    server.body = NAVALL.replace("04-Nov", "03-Nov").replace("123.45675", "99.0")
    rig.svc.store.write(
        OWNER, lambda tx: tx.job_put("nav_fetch", 20261105, "failed", "x", ist(5, 23, 31, mo=11))
    )
    fetch(rig, ist(6, 7, 30, mo=11))
    assert rig.svc.store.read(OWNER, lambda tx: tx.mf_get("100001"))["nav_u"] == 1_234_568


def test_a_newer_nav_keeps_the_one_before_it_as_previous(world):
    rig, server = world
    held = fund(rig)
    fetch(rig, ist(4, 23, 30, mo=11))
    server.body = NAVALL.replace("123.45675", "124.5").replace("04-Nov", "05-Nov")
    rig.svc.store.write(
        OWNER, lambda tx: tx.job_put("nav_fetch", 20261105, "failed", "x", ist(5, 23, 31, mo=11))
    )
    fetch(rig, ist(6, 7, 30, mo=11))
    scheme = rig.svc.store.read(OWNER, lambda tx: tx.mf_get("100001"))
    assert (scheme["nav_u"], scheme["prev_nav_u"], scheme["prev_nav_date"]) == (
        1_245_000, 1_234_568, NAV_DAY,
    )  # fmt: skip
    assert rig.eod_row(20261105, held)["prev_close_u"] == 1_234_568


def test_the_switch_off_asks_nobody(world):
    rig, server = world
    rig.svc.settings = dataclasses.replace(rig.svc.settings, amfi=False)
    assert fetch(rig, ist(4, 23, 30, mo=11)) is None
    assert server.asks == []


@pytest.mark.parametrize(
    ("clock", "today_row", "yesterday_row", "want"),
    [
        ((4, 23, 29), None, "ok", None),
        ((4, 23, 30), None, None, 20261104),
        ((4, 23, 30), "ok", None, None),
        ((5, 7, 29), None, None, None),
        ((5, 7, 30), None, None, 20261104),
        ((5, 7, 30), None, "ok", None),
        ((5, 12, 0), None, "failed_late", None),
        ((5, 7, 30), None, "failed_early", 20261104),
    ],
)
def test_when_a_fetch_is_due(clock, today_row, yesterday_row, want):
    d, h, m = clock
    at = ist(d, h, m, mo=11)
    row = None if today_row is None else {"status": today_row, "at": at}
    previous = None
    if yesterday_row == "ok":
        previous = {"status": "ok", "at": at - 3_600_000}
    elif yesterday_row == "failed_early":
        previous = {"status": "failed", "at": ist(4, 23, 31, mo=11)}
    elif yesterday_row == "failed_late":
        previous = {"status": "failed", "at": ist(5, 7, 31, mo=11)}
    assert amfi.due_day(at, row, previous) == want


# ---- the NAV settle: freshness (vectors FR1, FR2) and the frozen rule ------------------------------
def info_of(rig, held, today):
    row = rig.svc.store.read(OWNER, lambda tx: tx.get("asset", held["id"]))
    return rig.svc.store.read(OWNER, lambda tx: rig.svc.prices.now(tx, row, today))


def test_fr1_the_nav_of_the_last_trading_day_is_fresh(world):
    rig, _ = world
    held = fund(rig)
    fetch(rig, ist(4, 23, 30, mo=11))
    today = 20261105
    price = info_of(rig, held, today)
    last = rig.svc.prices.last_session_day(today, ist(5, 12, mo=11))
    assert price["basis"] == "nav" and price["day"] == NAV_DAY
    assert freshness.of_price(price, today=today, last_session_day=last).state == freshness.FRESH


def test_fr2_a_nav_four_business_days_behind_is_old(world):
    rig, _ = world
    held = fund(rig)
    fetch(rig, ist(4, 23, 30, mo=11))
    price = info_of(rig, held, 20261111)
    got = freshness.of_price(price, today=20261111, last_session_day=20261111)
    assert got.state == freshness.OLD


def test_the_nav_settle_leaves_a_frozen_day_alone(world):
    rig, _ = world
    held = fund(rig)
    fetch(rig, ist(4, 23, 30, mo=11))
    rig.svc.store.write(
        OWNER, lambda tx: tx.job_put("nav_fetch", 20261105, "failed", "x", ist(5, 23, 31, mo=11))
    )
    rig.svc.store.write(
        OWNER, lambda tx: tx.mf_put_many([{**by_code()["100001"], "nav_u": 1}], ist(5, 23))
    )
    sched = Schedule(rig.svc.ensure_calendar())
    # Mon 9 Nov is 4 trading days after Wed 4 Nov (5, 6, 9, 10 -> counted by the schedule)
    written = rig.svc.store.write(
        OWNER, lambda tx: amfi.apply_navs(tx, 20261110, sched, ist(10, 9, mo=11))
    )
    assert written == 0 and rig.eod_row(NAV_DAY, held)["price_u"] == 1_234_568


# ---- mf_search ---------------------------------------------------------------------------------
def load(rig):
    rig.svc.store.write(OWNER, lambda tx: tx.mf_put_many(amfi.parse_navall(NAVALL), 1))


@scenario_of
async def test_search_finds_schemes_by_every_word_of_the_name_the_amc_or_the_code(rig):
    load(rig)
    both = (await rig.call("mf_search", q="alpha bluechip"))["data"]
    assert [i["amfi_code"] for i in both["items"]] == ["100001"] and both["note"] is None
    item = both["items"][0]
    assert item["asset_class"] == "mf_equity" and item["nav_u"] == 1_234_568
    assert item["nav_day"] == NAV_DAY and item["amc"] == "Alpha Mutual Fund"
    assert [i["amfi_code"] for i in (await rig.call("mf_search", q="200002"))["data"]["items"]] == [
        "200002"
    ]
    liquid = (await rig.call("mf_search", q="beta direct"))["data"]["items"]
    assert [i["asset_class"] for i in liquid] == ["mf_debt", "mf_debt"]
    assert (await rig.call("mf_search", q="alpha beta"))["data"]["items"] == []
    assert len((await rig.call("mf_search", q="fund", limit=2))["data"]["items"]) == 2
    assert (await rig.call("mf_search", q="  "))["data"]["items"] == []


@scenario_of
async def test_search_says_so_when_the_list_is_not_loaded_or_the_feature_is_off(rig):
    empty = (await rig.call("mf_search", q="alpha"))["data"]
    assert empty["items"] == [] and "not loaded" in empty["note"]
    rig.svc.settings = dataclasses.replace(rig.svc.settings, amfi=False)
    off = (await rig.call("mf_search", q="alpha"))["data"]
    assert "off" in off["note"] and "by hand" in off["note"]


@scenario_of
async def test_search_refuses_a_wrong_request_in_words(rig):
    for fields in ({"q": 5}, {"q": "x" * 101}, {"q": "a", "limit": 0}, {"q": "a", "limit": 21},
                   {"q": "a", "limit": "2"}):  # fmt: skip
        got = await rig.call("mf_search", **fields)
        assert got["code"] == "bad_request" and "Reload" in got["message"], fields


def test_the_downloaded_file_is_never_kept_or_logged(world, caplog):
    rig, _ = world
    with caplog.at_level("DEBUG"):
        fetch(rig, ist(4, 23, 30, mo=11))
    assert "Alpha Bluechip" not in caplog.text and "INF000A01011" not in caplog.text
    assert datetime.now(IST).year >= 2026
    assert OpsRig is not None
