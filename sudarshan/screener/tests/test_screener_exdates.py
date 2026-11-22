"""The portfolio's ex-dates in the screener (M9.13, gap audit GA8.3; exdates.py).

The screener reads them from the portfolio's sealed API under its own purpose, once a day, and the
"days to ex-date" line counts the sessions to them on the exchange's calendar. Every instant is a
fixed IST date and every portfolio is the test's own (no network); the seal is opened with the
key alone, so no clock is read for it either.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import date, datetime

import httpx
import pytest
from screener_helpers import ms
from test_screener_feed_runtime import SBIN, SPEC, frame, health
from test_screener_feed_runtime import world as world  # noqa: F401  (the fixture)
from test_screener_universe import master as master_fixture  # noqa: F401  (the fixture)

from sudarshan.alerts.market.feedlink import HEALTH
from sudarshan.autologin.schedule import IST
from sudarshan.screener import check, config, engine, events, spec
from sudarshan.screener.exdates import ExDatesLink, Facts, Unread, parse, run_ex_dates
from sudarshan.screener.runs import Member
from sudarshan.screener.types import HIT, MISS, NO_EVENTS, NOT_CHECKED, Why
from sudarshan.tasks import cancel_and_wait
from sudarshan.vault import PORTFOLIO_SCREENER_API_INFO, fernet_for

KEY = fernet_for(os.environ["API_KEY_PEPPER"], PORTFOLIO_SCREENER_API_INFO)
URL = "http://127.0.0.1:5583"
OURS = 4242
SBIN_ISIN, ITC_ISIN = "INE062A01020", "INE154A01025"
ANSWER = {
    "as_of": 1_793_050_000_000,
    "shares": [
        {"isin": SBIN_ISIN, "events": [["bonus", 20261112], ["dividend", 20261031]]},
        {"isin": ITC_ISIN, "events": []},
    ],
}
HEAD = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid\n"


def at(day: date, hm: str) -> int:
    hour, minute = map(int, hm.split(":"))
    return int(datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST).timestamp() * 1000)


def table(tmp_path, uid: int | None) -> tuple:
    """/proc/net/tcp as the port check reads it: port 5583 held by ``uid``, or by nobody."""
    path = tmp_path / f"tcp-{uid}"
    line = (
        f"   0: 0100007F:{5583:04X} 00000000:0000 0A 00000000:00000000 00:00000000 00000000"
        f"  {uid}        0 1 1 0000000000000000 100 0 0 10 0\n"
    )
    path.write_text(HEAD + (line if uid is not None else ""))
    return (path,)


class Portfolio:
    """The portfolio's API as the screener meets it; each request opened with the screener's key."""

    def __init__(self, status: int = 200, body=None):
        self.status = status
        self.body = {"status": "success", "data": ANSWER} if body is None else body
        self.asked: list[tuple[str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.asked.append((request.url.path, json.loads(KEY.decrypt(request.content))))
        if isinstance(self.body, str):
            return httpx.Response(self.status, text=self.body)
        return httpx.Response(self.status, json=self.body)


def link_to(tmp_path, portfolio, holder: int | None = OURS) -> ExDatesLink:
    client = httpx.AsyncClient(transport=httpx.MockTransport(portfolio))
    return ExDatesLink(URL, KEY, client=client, tables=table(tmp_path, holder), uid=lambda: OURS)


def read(link: ExDatesLink):
    return asyncio.run(link.read())


# ---- the read --------------------------------------------------------------------------------------
def test_the_read_is_sealed_for_the_screener_purpose_and_gives_each_shares_events(tmp_path):
    portfolio = Portfolio()
    facts = read(link_to(tmp_path, portfolio))
    assert facts == Facts(
        {SBIN_ISIN: (("dividend", 20261031), ("bonus", 20261112)), ITC_ISIN: ()}, ANSWER["as_of"]
    )
    [(path, message)] = portfolio.asked
    assert path == "/v1/ex_dates"
    assert (message["op"], message["principal"], message["args"]) == (
        "ex_dates", "sudarshan-screener", {},
    )  # fmt: skip


@pytest.mark.parametrize(
    "status, body, kind",
    [
        (501, {"status": "error", "code": "not_ready"}, "not_ready"),  # insights switched off
        (403, {"status": "error", "code": "forbidden"}, "refused"),  # another pepper
        (403, {"status": "error", "code": "principal"}, "refused"),
        (500, {"status": "error", "code": "failed"}, "failed"),
        (200, {"status": "success", "data": {"enabled": False, "message": "off"}}, "off"),
        (200, "<html>", "failed"),
        (200, {"status": "success", "data": {"shares": "all"}}, "failed"),
    ],
)
def test_a_read_that_did_not_happen_says_why(tmp_path, status, body, kind):
    with pytest.raises(Unread) as failed:
        read(link_to(tmp_path, Portfolio(status, body)))
    assert failed.value.kind == kind


def test_nothing_is_asked_of_a_port_nobody_or_another_account_holds(tmp_path):
    for holder, kind in ((None, "down"), (1000, "held")):
        portfolio = Portfolio()
        with pytest.raises(Unread) as failed:
            read(link_to(tmp_path, portfolio, holder))
        assert failed.value.kind == kind and portfolio.asked == []


def test_a_connection_that_fails_is_down(tmp_path):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    link = ExDatesLink(URL, KEY, client=httpx.AsyncClient(transport=httpx.MockTransport(refuse)),
                       tables=table(tmp_path, OURS), uid=lambda: OURS)  # fmt: skip
    with pytest.raises(Unread) as failed:
        read(link)
    assert failed.value.kind == "down"


def test_a_share_with_an_event_that_cannot_be_read_is_not_known_rather_than_partly():
    facts = parse({"as_of": True, "shares": [
        {"isin": SBIN_ISIN, "events": [["dividend", 20261031], ["meeting", 20261105]]},
        {"isin": ITC_ISIN, "events": [["split", 20261104], ["bonus", "20261105"]]},
        {"isin": "INE000X01011", "events": [["rights", 20261120]]},
        {"isin": 7, "events": []},
        "not a share",
    ]})  # fmt: skip
    assert facts == Facts({"INE000X01011": (("rights", 20261120),)}, None)


# ---- once a day ------------------------------------------------------------------------------------
TUESDAY, WEDNESDAY, THURSDAY = date(2027, 3, 30), date(2027, 3, 31), date(2027, 4, 1)


def test_the_read_is_made_at_start_and_after_08_05_each_day_over_the_years_end(tmp_path):
    portfolio = Portfolio()
    link = link_to(tmp_path, portfolio)
    asks = []
    for when in ((WEDNESDAY, "03:00"), (WEDNESDAY, "03:01"), (WEDNESDAY, "08:04"),
                 (WEDNESDAY, "08:05"), (WEDNESDAY, "15:30"), (WEDNESDAY, "23:59"),
                 (THURSDAY, "00:00"), (THURSDAY, "08:04"), (THURSDAY, "08:05"),
                 (THURSDAY, "08:06")):  # fmt: skip
        asyncio.run(link.refresh(at(*when)))
        asks.append(len(portfolio.asked))
    assert asks == [1, 1, 1, 2, 2, 2, 2, 2, 3, 3]
    assert link.status() == {"state": "ok", "as_of": ANSWER["as_of"], "shares": 2}


def test_a_failed_read_is_tried_again_ten_minutes_on_and_keeps_what_was_read(tmp_path):
    portfolio = Portfolio(500, {"status": "error", "code": "failed"})
    link = link_to(tmp_path, portfolio)
    asyncio.run(link.refresh(at(TUESDAY, "09:00")))
    asyncio.run(link.refresh(at(TUESDAY, "09:09")))
    assert len(portfolio.asked) == 1 and link.state == "failed" and link.facts is None
    assert link.of_key(SBIN, at(TUESDAY, "09:09")) is None  # nothing read: not known
    portfolio.status, portfolio.body = 200, {"status": "success", "data": ANSWER}
    asyncio.run(link.refresh(at(TUESDAY, "09:10")))
    assert len(portfolio.asked) == 2 and link.state == "ok"
    portfolio.status, portfolio.body = 500, {"status": "error", "code": "failed"}
    asyncio.run(link.refresh(at(WEDNESDAY, "08:05")))  # the next day's read fails
    assert link.state == "failed"
    assert link.of_key(SBIN, at(WEDNESDAY, "09:15")) == (("dividend", 20261031),
                                                         ("bonus", 20261112))  # fmt: skip


def test_the_last_read_counts_for_three_days_and_only_for_shares():
    link = ExDatesLink(URL, KEY)
    link.facts, link.read_at = parse(ANSWER), at(TUESDAY, "09:10")
    assert link.of_key("NSE_EQ|" + ITC_ISIN, at(TUESDAY, "09:15")) == ()  # read: none listed
    assert link.of_key("BSE_EQ|" + ITC_ISIN, at(TUESDAY, "09:15")) == ()
    assert link.of_key("NSE_FO|49081", at(TUESDAY, "09:15")) is None  # a contract: no ISIN
    assert link.of_key("NSE_INDEX|Nifty 50", at(TUESDAY, "09:15")) is None
    assert link.of_key("NSE_EQ|INE000Z01011", at(TUESDAY, "09:15")) is None  # not read
    three_days = link.read_at + 3 * 86_400_000
    assert link.of_key(SBIN, three_days) is not None and link.of_key(SBIN, three_days + 1) is None


def test_the_job_reads_while_the_service_holds_a_key(tmp_path):
    class Service:
        parts: dict = {}
        clock = staticmethod(lambda: at(TUESDAY, "09:00"))

    asyncio.run(run_ex_dates(Service()))  # no key: returns at once
    portfolio = Portfolio()
    Service.parts = {"exdates": link_to(tmp_path, portfolio)}

    async def go():
        task = asyncio.create_task(run_ex_dates(Service(), tick_s=0.01))
        while not portfolio.asked:
            await asyncio.sleep(0.01)
        await cancel_and_wait(task)

    asyncio.run(go())
    assert len(portfolio.asked) == 1  # the same day: read once


def test_the_service_reads_the_portfolio_on_this_server_only(tmp_path):
    from sudarshan.screener.service import JOBS, Service

    settings = config.Settings.from_env({"SUDARSHAN_SCREENER": "on"})
    assert settings.portfolio_url == URL
    with pytest.raises(config.ConfigError):
        config.Settings.from_env({"SUDARSHAN_SCREENER_PORTFOLIO_URL": "http://10.0.0.5:5583"})
    svc = Service(settings, KEY)
    svc.pepper = os.environ["API_KEY_PEPPER"]
    svc._link()
    assert svc.parts["exdates"].url == URL and "ex_dates" in JOBS


# ---- a live run counts the sessions to them --------------------------------------------------------
def trade(source, clock) -> None:
    """SBIN trades every 30 s from 09:15 to 09:30 on a healthy feed, sealed as the service does."""
    at = ms("2026-10-28", "09:15:00")
    source.on_frame(HEALTH, health(at), at)
    for minute in range(16):
        for second in (5, 35):
            at = ms("2026-10-28", "09:15:00") + minute * 60_000 + second * 1000
            source.on_frame(HEALTH, health(at - 1000), at - 1000)
            source.on_frame(SBIN, frame(SBIN, at, 600.0, 10 * (2 * minute + 1)), at)
        clock["now"] = at + 30_000
        source.on_clock(clock["now"])


class Shut:
    """Weekdays, less Thursday 12 Nov 2026 (a holiday made for the test)."""

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day != date(2026, 11, 12)

    def windows(self):
        return {}


def test_a_live_run_counts_sessions_to_the_portfolios_ex_dates(world):  # noqa: F811
    store, source, clock = world
    source.calendar = Shut()
    source.new_day(clock["now"])  # Wednesday 28 Oct 2026
    link = ExDatesLink(URL, KEY)
    link.facts, link.read_at = parse(ANSWER), clock["now"]
    source.ex_dates = link
    share = Member(SBIN, "SBIN", "NSE", "NSE_EQ", False)
    trade(source, clock)  # it trades from 09:15: the 15-minute run at 09:30 is due for it
    boundary = ms("2026-10-28", "09:30")

    def judged(kind: str, most: int):
        plan = plan_of({"id": "c1", "left": {"event": "ex_days", "kind": kind}, "op": "lte",
                        "right": {"num": str(most)}})  # fmt: skip
        return engine.judge(plan, source.subject(share, plan, boundary),
                            source.context(boundary, 20261028))  # fmt: skip

    assert source.subject(share, plan_of(SPEC["where"]), boundary).ex_dates == (
        ("dividend", 20261031), ("bonus", 20261112),
    )  # fmt: skip
    # Saturday 31 Oct goes ex at Monday's session, three after Wednesday's
    dividend = judged("dividend", 3)
    assert (dividend.kind, dividend.values["c1"]["l"]) == (HIT, "3")
    # Thursday 12 Nov is a holiday: it goes ex at Friday 13 Nov, the holiday not counted
    bonus = judged("bonus", 11)
    assert (bonus.kind, bonus.values["c1"]["l"]) == (HIT, "11")
    assert judged("bonus", 10).kind == MISS
    assert judged("split", 400).kind == MISS  # read, none listed: never within any screen's days
    source.ex_dates = None  # nothing from the portfolio: "Not checked", never a miss
    unread = judged("any", 3)
    assert (unread.kind, unread.why) == (NOT_CHECKED, (Why(NO_EVENTS, ("ex_dates",)),))


# ---- the replay check --------------------------------------------------------------------------------
def plan_of(where: dict, rank=None):
    screen = {**SPEC, "universe": {"kind": "watchlist", "list_id": 7}, "where": where}
    return spec.compile_plan(spec.check({**screen, **({"rank": rank} if rank else {})}))


def test_a_plan_that_counts_days_to_an_ex_date_is_known_as_one():
    ex = {"id": "c2", "left": {"event": "ex_days"}, "op": "lte", "right": {"num": "3"}}
    expiry = {"id": "c3", "left": {"event": "expiry_days"}, "op": "eq", "right": {"num": "0"}}
    price = SPEC["where"]
    assert events.reads_ex_dates(plan_of(ex)) is True
    assert events.reads_ex_dates(plan_of({"all": [price, {"not": ex}]})) is True
    assert events.reads_ex_dates(plan_of(expiry)) is False
    assert events.reads_ex_dates(plan_of({"all": [price, {**ex, "on": False}]})) is False
    assert events.reads_ex_dates(plan_of(price)) is False


def test_a_replay_difference_on_a_screen_counting_ex_dates_is_explained():
    key = "5:v1:20261028:15:2"
    line = check.RunLine(key, "Ex-date soon", ms("2026-10-28", "09:45"), 0, (), (), 2, False, "",
                         False)  # fmt: skip
    live = [{"run_key": key, "status": "done", "matched": 2, "entered": 0, "exited": 0,
             "marks": {}}]  # fmt: skip
    explained, unexplained = check.compare([line], live, frozenset({5}))
    assert unexplained == [] and explained[0].endswith(f"({check.EX_DATES})")
    explained, unexplained = check.compare([line], live, frozenset({6}))  # another screen's
    assert explained == [] and "service 2 matched" in unexplained[0]
