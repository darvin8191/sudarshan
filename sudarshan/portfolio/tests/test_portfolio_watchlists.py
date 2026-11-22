"""The watchlists' shares in the corporate-action calendar (GA8.1: M7.36 for the watchlists'
symbols, P7 decision 15): read once a night through the watchlists' sealed route, never their
database; kept as one record; read from Upstox after everything the owner holds, inside the same
reads and budget; marked on the calendar beside the holdings' events; never a family member's.

OpenAlgo's route and Upstox are transports in memory; nothing touches the network. The clock is
fixed: 26 Oct 2026, the night's run at 23:50 (NIGHT), unless a test sets another."""

from __future__ import annotations

import asyncio
import functools
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from test_portfolio_ca_ops import event, history, record, scenario
from test_portfolio_closed_world import answer_misses
from test_portfolio_corpactions_reader import HELD, NIGHT, TODAY, TRADED, UPSTOX, Night
from test_portfolio_import_ops import OWNER
from test_portfolio_purposes import FAM
from test_portfolio_service import PEPPER

from sudarshan.autologin.handoff import seal
from sudarshan.portfolio import corpactions as ca
from sudarshan.portfolio import store as store_module
from sudarshan.portfolio import watchlists as wl
from sudarshan.vault import WATCHLISTS_INTERNAL_INFO, fernet_for

WATCH = fernet_for(PEPPER, WATCHLISTS_INTERNAL_INFO)
ITC, TCS = "INE154A01025", "INE467B01029"
#: A watched share whose ISIN sorts before every share of the owner's: an order by ISIN alone
#: would read it first.
EARLY = "INE000A01017"
DAY_MS = 86_400_000
IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture(autouse=True)
def _stop_thread():
    yield
    store_module.stop()


@pytest.fixture(autouse=True)
def _no_listener(monkeypatch):
    """Nothing listens on 127.0.0.1:5000 in a test: the route is a transport in memory."""
    monkeypatch.setattr(wl.loopback, "refusal", lambda url: None)


def row(symbol: str, isin: str | None = None, exchange: str = "NSE", key: str | None = None):
    """One row as the route answers it (``internal.wire``)."""
    return {"symbol": symbol, "exchange": exchange, "tick_size": "0.05", "expiry": None, "lot": 1,
            "instrument_key": key or f"{exchange}_EQ|{isin}"}  # fmt: skip


class Route:
    """OpenAlgo's sealed watchlists route in memory: ``own_members`` answers ``rows``; ``answer``
    replaces the body, ``status`` the code, and an exception is raised as the transport's."""

    def __init__(self):
        self.asked: list[tuple] = []
        self.status: int = 200
        self.answer: object = None
        self.rows()

    def rows(self, *rows) -> None:
        self.status = 200
        self.answer = {"status": "ok", "data": {"version": "7", "rows": list(rows),
                                                "left_out": [], "valid_until": None}}  # fmt: skip

    def __call__(self, request: httpx.Request) -> httpx.Response:
        opened = json.loads(WATCH.decrypt(request.content))
        self.asked.append((request.method, request.url.path, dict(request.url.params),
                           opened["op"], request.headers["content-type"]))  # fmt: skip
        if isinstance(self.answer, Exception):
            raise self.answer
        if isinstance(self.answer, bytes):
            return httpx.Response(self.status, content=self.answer)
        return httpx.Response(self.status, json=self.answer)


class Lists(Night):
    """A night whose service holds the watchlists' seal, with OpenAlgo's route in memory."""

    def __init__(self, tmp_path):
        super().__init__(tmp_path)
        self.svc.watch_fernet = WATCH
        self.route = Route()
        self.openalgo = httpx.AsyncClient(transport=httpx.MockTransport(self.route))

    async def refresh(self, now: int = NIGHT) -> str:
        return await wl.refresh(self.svc, now, self.openalgo)

    def status(self, today: int = TODAY) -> dict:
        return self.read(lambda tx: wl.status(tx, today))

    def shares(self) -> dict:
        return self.read(wl.shares)


def lists(test):
    def wrapped(tmp_path):
        async def go():
            n = Lists(tmp_path)
            try:
                await test(n)
            finally:
                await n.openalgo.aclose()
                await n.client.aclose()
                n.svc.store.close()

        asyncio.run(go())

    wrapped.__name__ = test.__name__
    return wrapped


def watch(n, *shares, at: int = NIGHT) -> None:
    """The lists as a night read them: ``(isin, symbol)`` each, on NSE."""
    found = [{"isin": isin, "symbol": symbol, "exchange": "NSE"} for isin, symbol in shares]
    n.svc.store.write(OWNER, lambda tx: wl.keep(tx, "ok", found, at))


# ---- the read: the sealed route, shares only, one record ---------------------------------------
@lists
async def test_the_night_reads_the_lists_through_the_sealed_route_and_keeps_their_shares(n):
    n.route.rows(
        row("ITC", ITC, "BSE"), row("ITC", ITC), row("TCS", TCS),
        row("NIFTY", key="NSE_INDEX|Nifty 50", exchange="NSE_INDEX"),
        row("NIFTY24NOV26FUT", key="NSE_FO|49081", exchange="NFO"),
    )  # fmt: skip
    assert n.status() == {"state": "never", "read_at": None, "share_count": 0, "words": wl.FIRST}
    said = await n.refresh()
    assert n.route.asked == [("POST", "/sudarshan/watchlists/internal/read", {"op": "own_members"},
                              "own_members", "application/octet-stream")]  # fmt: skip
    assert n.shares() == {
        ITC: {"isin": ITC, "symbol": "ITC", "exchange": "NSE"},  # one share, under NSE's symbol
        TCS: {"isin": TCS, "symbol": "TCS", "exchange": "NSE"},
    }
    assert n.status() == {"state": "ok", "read_at": NIGHT, "share_count": 2, "words": None}
    assert said == "watchlists: 2 shares"  # counts only in the job log, never a symbol


def test_before_the_first_read_the_calendar_says_when_it_comes():
    assert wl.FIRST == (
        "SUDARSHAN reads the shares on your watchlists for the first time tonight after 23:45. "
        "Their dividends, bonuses and splits show here after that."
    )


@pytest.mark.parametrize(
    "answer, status",
    [
        (httpx.ConnectError("refused"), 200),  # OpenAlgo is not answering (a deploy, a crash)
        ({"status": "error", "code": "failed", "message": "Try again in a minute."}, 500),
        ({"status": "error", "message": "Forbidden"}, 403),  # a seal OpenAlgo did not take
        (b"<html>a page</html>", 200),
        ({"status": "ok", "data": {"version": "7"}}, 200),  # no rows: not the route's shape
    ],
)
def test_a_night_that_cannot_read_the_lists_keeps_the_last_shares_and_says_so(
    tmp_path, answer, status
):
    @lists
    async def run(n):
        n.route.rows(row("ITC", ITC))
        await n.refresh()
        n.route.answer, n.route.status = answer, status
        assert await n.refresh(NIGHT + DAY_MS) == "watchlists: not read, last kept"
        assert set(n.shares()) == {ITC}
        status_now = n.status(TODAY + 2)  # the morning after the 27th's failed night
        assert (status_now["state"], status_now["read_at"]) == ("failed", NIGHT)
        assert status_now["words"] == (
            "SUDARSHAN could not read your watchlists last night. The shares shown are from its "
            "last read. It tries again tonight after 23:45."
        )
        # Seen the same evening, after the run: tonight, and tomorrow night again.
        assert n.status(TODAY + 1)["words"].startswith(
            "SUDARSHAN could not read your watchlists tonight."
        )

    run(tmp_path)


@lists
async def test_a_first_night_that_fails_says_no_share_is_shown_yet(n):
    n.route.answer, n.route.status = {"status": "error", "message": "Forbidden"}, 403
    await n.refresh()
    assert n.shares() == {}
    assert n.status(TODAY + 1) == {
        "state": "failed", "read_at": None, "share_count": 0,
        "words": "SUDARSHAN could not read your watchlists last night. None of their shares is "
                 "shown yet. It tries again tonight after 23:45.",
    }  # fmt: skip


@lists
async def test_watchlists_v2_switched_off_empties_the_shares_and_says_so(n):
    n.route.rows(row("ITC", ITC))
    await n.refresh()
    n.route.status = 503
    n.route.answer = {"status": "error", "code": "off", "message": "Watchlists v2 is off."}
    assert await n.refresh(NIGHT + DAY_MS) == "watchlists: switched off"
    assert n.shares() == {}
    assert n.status(TODAY + 1)["state"] == "off"
    assert (
        n.status(TODAY + 1)["words"]
        == wl.OFF
        == (
            "Watchlists v2 is switched off on this server, so the calendar shows the events of what "
            "you hold only."
        )
    )


@lists
async def test_a_port_held_by_another_program_is_never_sent_the_seal(n):
    held = "port 5000 of this server is held by another user's program"
    wl.loopback.refusal = lambda url: held  # the fixture puts the stand-in back afterwards
    n.route.rows(row("ITC", ITC))
    assert await n.refresh() == "watchlists: not read, last kept"
    assert n.route.asked == [] and n.status()["state"] == "failed"


@lists
async def test_without_the_seal_nothing_is_read_or_written(n):
    n.svc.watch_fernet = None
    assert await n.refresh() == "watchlists not read: no key"
    assert n.route.asked == [] and n.status()["state"] == "never"


# ---- Upstox's reads: after the owner's shares, in the same reads and budget ---------------------
@lists
async def test_the_watchlists_shares_are_read_after_the_owners_in_the_reads_left(n):
    n.hold()  # HELD, in a custom tab
    n.svc.store.write(OWNER, lambda tx: tx.upstox_eod_put(
        TODAY, {"read_at": NIGHT, "holdings_value_paise": 50_000},
        [{"instrument_key": f"NSE_EQ|{UPSTOX}", "qty": 5_000_000}], NIGHT,
    ))  # fmt: skip
    watch(n, (TCS, "TCS"), (EARLY, "ABC"), (HELD, "RELIANCE"))  # HELD is the owner's already
    ca.MAX_READS, real = 3, ca.MAX_READS
    try:
        assert await n.reader.plan(NIGHT) == [UPSTOX, HELD, EARLY]
        ca.MAX_READS = 10
        assert await n.reader.plan(NIGHT) == [UPSTOX, HELD, EARLY, TCS]
    finally:
        ca.MAX_READS = real


@lists
async def test_a_watchlist_share_that_failed_before_comes_after_those_never_read(n):
    watch(n, (ITC, "ITC"), (TCS, "TCS"))
    n.svc.store.write(OWNER, lambda tx: tx.ca_read_put(ITC, NIGHT - DAY_MS, "failed", "x"))
    assert await n.reader.plan(NIGHT) == [TCS, ITC]


@lists
async def test_a_whole_night_spends_no_read_more_than_the_reads_it_had(n):
    n.hold()
    watch(n, (ITC, "ITC"), (TCS, "TCS"), (EARLY, "ABC"))
    for isin in (HELD, EARLY, ITC):
        n.upstox.answers[isin] = []  # nothing listed
    ca.MAX_READS, real = 3, ca.MAX_READS
    try:
        status, note = await n.reader.run(NIGHT)
    finally:
        ca.MAX_READS = real
    assert n.upstox.asked == [HELD, EARLY, ITC]  # the owner's share first; one read each, no more
    assert status == "ok" and note.startswith("0 of 3 instruments read, 3 with nothing listed")


@lists
async def test_the_nightly_step_reads_the_lists_before_its_rotation(n):
    n.route.rows(row("ITC", ITC))
    n.upstox.answers[ITC] = []
    real = wl.refresh
    wl.refresh = functools.partial(real, client=n.openalgo)
    try:
        assert await ca.corporate_actions_step(n.svc, NIGHT) == "ok"
    finally:
        wl.refresh = real
    assert n.upstox.asked == [ITC]
    done = n.read(lambda tx: tx.job_get(ca.API, TODAY))
    assert "watchlists: 1 shares" in done["detail"] and "ITC" not in done["detail"]


# ---- the calendar: marked beside the holdings, and the owner's alone -----------------------------
async def calendar(n, **args) -> dict:
    answer = await n.rig.call("calendar", **args)
    assert answer["status"] == "success", answer
    return answer["data"]


@scenario
async def test_the_calendar_marks_the_watchlists_shares_beside_the_holdings(n):
    record(n, 20261023, **{UPSTOX: 250})
    history(n, (20260601, "BUY", 5), (20260701, "SELL", 5), isin=TRADED)
    watch(n, (ITC, "ITC"), (UPSTOX, "PROBE"))
    held = event(n, "dividend", 20261110, amount_u=30000)
    watched = event(n, "dividend", 20261112, amount_u=52500, isin=ITC)
    gone = event(n, "dividend", 20261113, amount_u=10000, isin=TCS)  # neither any more
    traded = event(n, "bonus", 20261114, ratio=(1, 1), isin=TRADED)
    data = await calendar(n)
    rows = {r["event_id"]: r for r in data["calendar"]}
    assert list(rows) == [held, watched, traded] and gone not in rows
    assert (rows[held]["holding"], rows[held]["watchlist"], rows[held]["held_qty_u"]) == (
        True, True, 250_000_000)  # fmt: skip
    assert {k: rows[watched][k] for k in ("name", "holding", "watchlist", "held_qty_u",
                                          "expected_paise", "amount_u")} == {
        "name": "ITC", "holding": False, "watchlist": True, "held_qty_u": None,
        "expected_paise": None, "amount_u": 52500,
    }  # fmt: skip
    assert (rows[traded]["holding"], rows[traded]["watchlist"]) == (True, False)
    assert data["watchlists"] == {"state": "ok", "read_at": NIGHT, "share_count": 2,
                                  "words": None}  # fmt: skip
    assert answer_misses(data) == set()


@scenario
async def test_a_share_taken_off_every_watchlist_leaves_the_calendar(n):
    watch(n, (ITC, "ITC"))
    itc = event(n, "dividend", 20261112, amount_u=52500, isin=ITC)
    assert [r["event_id"] for r in (await calendar(n))["calendar"]] == [itc]
    watch(n, (TCS, "TCS"), at=NIGHT + DAY_MS)
    assert (await calendar(n))["calendar"] == []
    assert n.read(lambda tx: tx.ca_events(isins=[ITC]))  # Upstox's event itself is kept


@scenario
async def test_the_family_purpose_is_refused_the_calendar_and_its_watchlist_rows(n):
    watch(n, (ITC, "ITC"))
    event(n, "dividend", 20261112, amount_u=52500, isin=ITC)
    async with httpx.AsyncClient(trust_env=False) as client:
        answer = await client.post(
            f"http://127.0.0.1:{n.svc.api.port}/v1/calendar",
            content=seal(FAM, "calendar", principal="viewer:7", args={}), timeout=20,
        )  # fmt: skip
    assert answer.status_code == 403 and "ITC" not in answer.text and ITC not in answer.text


@scenario
async def test_the_calendar_over_31_march_lists_each_day_as_upstox_gives_it(n):
    """The financial year turns between two ex-dates; a Saturday's stays on its Saturday (the
    calendar shows Upstox's day; the screener counts the trading days to it)."""
    n.rig.now = int(datetime(2027, 3, 31, 12, 0, tzinfo=IST).timestamp() * 1000)
    tried = int(datetime(2027, 3, 31, 23, 50, tzinfo=IST).timestamp() * 1000)
    watch(n, (ITC, "ITC"), at=n.rig.now - DAY_MS)
    days = (20270331, 20270401, 20270403)  # Wednesday, Thursday, a Saturday
    made = [event(n, "dividend", day, amount_u=10000, isin=ITC) for day in days]
    data = await calendar(n)
    assert (data["from"], data["to"]) == (20261231, 20270927)
    assert [(r["event_id"], r["ex_day"]) for r in data["calendar"]] == list(
        zip(made, days, strict=True)
    )
    n.svc.store.write(OWNER, lambda tx: wl.keep(tx, "failed", [], tried))
    assert n.read(lambda tx: wl.status(tx, 20270401))["words"].startswith(
        "SUDARSHAN could not read your watchlists last night."
    )
