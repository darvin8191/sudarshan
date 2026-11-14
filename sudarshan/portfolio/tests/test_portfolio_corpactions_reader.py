"""The nightly read of Upstox's corporate actions (M7.36; corpactions.py): which instruments, the
weekly rotation, its own budget, the hours it keeps, and that an answer it cannot read changes
nothing. Upstox is a transport in memory answering rows typed from its documented fields
(corpactions.json); nothing here touches the network."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from test_portfolio_import_ops import NOW, OWNER, Rig
from test_portfolio_upstox import TOKEN

from sudarshan.portfolio import corpactions as ca
from sudarshan.portfolio import store as store_module
from sudarshan.portfolio.budget import Budget
from sudarshan.portfolio.core import corpactions as core
from sudarshan.portfolio.core.days import day_of_instant
from sudarshan.portfolio.upstox import UpstoxReader

FILE = json.loads((Path(__file__).parent / "vectors" / "corpactions.json").read_text("utf-8"))
SAMPLE = FILE["rows"][0]["raw"]
BONUS = FILE["rows"][1]["raw"]
HELD, TRADED, UPSTOX = "INE002A01018", "INE000B01011", "INE000C01012"
DAY_MS = 86_400_000
#: 26 Oct 2026 13:03 IST is NOW; the night's run begins at 23:45 IST.
NIGHT = NOW - (13 * 60 + 3) * 60_000 + (23 * 60 + 50) * 60_000
TODAY = day_of_instant(NOW)


@pytest.fixture(autouse=True)
def _stop_thread():
    yield
    store_module.stop()


class Upstox:
    """Upstox in memory: ``answers[isin]`` is the body, a status, or absent (a 404)."""

    def __init__(self):
        self.answers: dict[str, object] = {}
        self.asked: list[str] = []
        self.headers: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        parts = request.url.path.split("/")
        assert parts[:3] == ["", "v2", "fundamentals"] and parts[4] == "corporate-actions"
        self.asked.append(parts[3])
        self.headers.append(dict(request.headers))
        answer = self.answers.get(parts[3], 404)
        if isinstance(answer, int):
            return httpx.Response(answer, json={"status": "error", "errors": []})
        if isinstance(answer, bytes):
            return httpx.Response(200, content=answer)
        return httpx.Response(200, json={"status": "success", "data": answer})


class Night:
    """A service with a store, an Upstox in memory and a reader that never really sleeps."""

    def __init__(self, tmp_path, **more):
        self.rig = Rig(tmp_path)
        self.svc, self.upstox = self.rig.svc, Upstox()
        token = tmp_path / "token"
        token.write_text(TOKEN)
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.upstox))
        self.svc.upstox = UpstoxReader(
            token, "http://upstox.test", clock=self.svc.clock, client=self.client
        )
        self.slept: list[float] = []
        self.mono = 0.0
        more.setdefault("budget", Budget(ca.LIMITS, lambda: self.mono))
        self.reader = ca.CorpActions(self.svc, self.svc.upstox, sleep=self._sleep,
                                     mono=lambda: self.mono, **more)  # fmt: skip
        self.svc._corpactions = self.reader

    async def read_one(self, isin: str, now: int) -> str:
        """One read, a pace after the last one (the budget's second has passed)."""
        self.mono += ca.PACE_S
        return await self.reader.read_one(isin, now)

    async def read_now(self, isin: str, now: int) -> str:
        self.mono += ca.PACE_S
        return await self.reader.read_now(isin, now)

    async def _sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.mono += seconds

    def hold(self, isin: str = HELD) -> None:
        """An entry for the rig's share, so the share is one a custom tab holds."""
        self.rig.book.activity_create(
            OWNER, self.rig.tab["id"], self.rig.version(),
            {"kind": "BUY", "trade_date": 20261001, "account_id": self.rig.account["id"],
             "asset_id": self.rig.share["id"], "qty_u": 10_000_000, "price_u": 10_000_000},
            "e-1", now=NOW,
        )  # fmt: skip

    def read(self, fn):
        return self.svc.store.read(OWNER, fn)

    def events(self, **kw):
        return self.read(lambda tx: tx.ca_events(**kw))

    def reads(self):
        return {row["isin"]: row for row in self.read(lambda tx: tx.ca_reads())}


def night(test):
    def wrapped(tmp_path):
        async def go():
            n = Night(tmp_path)
            try:
                await test(n)
            finally:
                await n.client.aclose()
                n.svc.store.close()

        asyncio.run(go())

    wrapped.__name__ = test.__name__
    return wrapped


# ---- which instruments -------------------------------------------------------------------------
@night
async def test_the_instruments_are_what_is_held_and_what_was_traded(n):
    n.hold()
    n.rig.book.asset_create(OWNER, "s-2", "listed", now=NOW, exchange="NSE", symbol="IDLE",
                            isin="INE000D01013", name="Never bought")  # fmt: skip

    def seed(tx):
        tx.upstox_eod_put(TODAY, {"read_at": NOW}, [
            {"instrument_key": f"NSE_EQ|{UPSTOX}", "qty": 5_000_000},
            {"instrument_key": "NSE_INDEX|Nifty 50", "qty": 1},
        ], NOW)  # fmt: skip
        rows = [{"isin": TRADED, "segment": "EQ"}, {"isin": None}, {"isin": "not-an-isin"}]
        tx.report_put("trades", 2026, "EQ", "ok", NOW, None, {"rows": rows}, NOW)

    n.svc.store.write(OWNER, seed)
    assert n.read(ca.known_isins) == {HELD, UPSTOX, TRADED}
    held_now = [{"isin": "INE000E01014"}, {"isin": None}]
    assert n.read(lambda tx: ca.known_isins(tx, held_now)) == {HELD, UPSTOX, TRADED, "INE000E01014"}


def test_an_isin_is_read_out_of_an_instrument_key():
    assert ca.isin_of_key("NSE_EQ|INE002A01018") == "INE002A01018"
    for other in ("NSE_INDEX|Nifty 50", "NSE_FO|54321", "", None, 5):
        assert ca.isin_of_key(other) is None


# ---- the rotation (pure) -----------------------------------------------------------------------
def test_tonight_takes_the_never_read_then_the_near_then_the_week_old():
    days = {"A": 20261019, "B": 20261020, "C": 20261025, "D": None, "E": 20261026, "F": 20261025}
    order = core.tonight(list("FEDCBA"), days, {"C", "E"}, 20261026, 10)
    # D was never read; C has an event near and was not read today (E was); A is a week old;
    # B is six days old and F one day old, so they wait.
    assert order == ["D", "C", "A"]
    assert core.tonight(list("ABCDEF"), days, {"C", "E"}, 20261026, 2) == ["D", "C"]
    assert core.tonight(list("AB"), {"A": 20261018, "B": 20261019}, set(), 20261026, 9) == [
        "A",
        "B",
    ]
    assert core.tonight(["A"], {}, set(), 20261026, 0) == []


# ---- one night ---------------------------------------------------------------------------------
@night
async def test_a_night_reads_each_instrument_once_and_keeps_what_it_lists(n):
    n.hold()
    n.upstox.answers[HELD] = [SAMPLE, BONUS, {"name": "Demerger", "expiry_date": "1 Jan 2026"}]
    assert await n.reader.run(NIGHT) == ("ok", "1 of 1 instruments read, 0 with nothing listed, "
                                               "0 not read")  # fmt: skip
    assert n.upstox.asked == [HELD]
    assert n.upstox.headers[0]["authorization"] == f"Bearer {TOKEN}"
    kept = n.events(gone=True)
    assert [(e["kind"], e["ex_day"], e["amount_u"], e["ratio_num"], e["ratio_den"]) for e in kept] \
        == [("dividend", 20250814, 55000, None, None), ("bonus", 20250910, None, 1, 1)]  # fmt: skip
    assert kept[0]["details"][3] == {"name": "Dividend type", "value": "Final"}
    read = n.reads()[HELD]
    assert (read["state"], read["read_at"]) == ("ok", NOW)
    assert read["note"] == "1 rows could not be read and were left out."
    n.rig.now = NOW + 60_000
    assert (await n.reader.run(NIGHT))[1].startswith("0 of 0")  # read today: not asked again
    assert n.upstox.asked == [HELD]


@night
async def test_the_same_event_reworded_stays_one_event_and_a_dropped_one_is_kept_as_gone(n):
    n.hold()
    n.upstox.answers[HELD] = [SAMPLE, BONUS]
    await n.read_one(HELD, NOW)
    first = {e["kind"]: e["id"] for e in n.events()}
    reworded = {**SAMPLE, "event_details": [{"name": "Dividend Type", "value": "FINAL"}]}
    n.upstox.answers[HELD] = [reworded]
    await n.read_one(HELD, NOW + DAY_MS)
    live = n.events()
    assert [(e["id"], e["read_at"]) for e in live] == [(first["dividend"], NOW + DAY_MS)]
    gone = [e for e in n.events(gone=True) if e["gone_at"] is not None]
    assert [(e["id"], e["gone_at"]) for e in gone] == [(first["bonus"], NOW + DAY_MS)]
    n.upstox.answers[HELD] = [SAMPLE, BONUS]  # listed again: the same row comes back
    await n.read_one(HELD, NOW + 2 * DAY_MS)
    assert {e["kind"]: e["id"] for e in n.events()} == first


@pytest.mark.parametrize("answer", [b"<html>", b'{"status": "success"}', b"[]", 503])
def test_an_answer_that_cannot_be_read_changes_nothing(tmp_path, answer):
    @night
    async def scenario(n):
        n.upstox.answers[HELD] = [SAMPLE]
        await n.read_one(HELD, NOW)
        n.upstox.answers[HELD] = answer
        outcome = await n.read_one(HELD, NOW + DAY_MS)
        assert outcome == ("silent" if answer == 503 else "unreadable")
        assert [e["gone_at"] for e in n.events(gone=True)] == [None]
        assert n.reads()[HELD]["read_at"] == NOW  # still due tomorrow

    scenario(tmp_path)


@night
async def test_an_instrument_upstox_does_not_know_is_asked_again_in_a_week_not_every_night(n):
    n.hold()
    assert await n.read_one(HELD, NOW) == "none"
    read = n.reads()[HELD]
    assert (read["state"], read["read_at"], read["note"]) == ("none", NOW, ca.WORDS["none"])
    assert await n.reader.plan(NOW + 6 * DAY_MS) == []
    assert await n.reader.plan(NOW + 7 * DAY_MS) == [HELD]


@night
async def test_a_path_is_never_built_from_text_that_is_no_isin(n):
    assert await n.read_one("../../order", NOW) == "none"
    assert n.upstox.asked == []


# ---- the budget and the pauses ------------------------------------------------------------------
def test_the_budget_is_its_own_a_tenth_of_upstoxs_per_api_figures():
    assert ca.LIMITS == ((1, 1.0), (20, 60.0), (200, 1800.0))
    assert 60 / ca.PACE_S < 20 and ca.MAX_READS < 200


@night
async def test_reads_are_paced_and_spend_from_the_readers_own_budget(n):
    isins = [f"INE000{letter}01010" for letter in "FGH"]
    for isin in isins:
        n.upstox.answers[isin] = []
    n.reader._held_now = lambda: [{"isin": isin} for isin in isins]
    assert n.reader.budget is not n.svc.upstox.budget
    status, note = await n.reader.run(NIGHT)
    assert (status, n.upstox.asked) == ("ok", isins)
    assert n.slept == [ca.PACE_S, ca.PACE_S]
    assert n.svc.upstox.budget._buckets == {}  # the live book's budget was not touched


def test_an_empty_budget_stops_the_run_and_sends_nothing(tmp_path):
    async def go():
        clock = [0.0]
        n = Night(tmp_path, budget=Budget(((1, 1.0), (1, 60.0), (200, 1800.0)), lambda: clock[0]))
        try:
            n.reader._held_now = lambda: [{"isin": HELD}, {"isin": TRADED}]
            n.upstox.answers.update({HELD: [], TRADED: []})
            status, note = await n.reader.run(NIGHT)
            assert status == "ok" and note.endswith(ca.WORDS["busy"])
            assert n.upstox.asked == [TRADED]  # the first by ISIN; the second found no token
        finally:
            await n.client.aclose()
            n.svc.store.close()

    asyncio.run(go())


@night
async def test_a_429_pauses_this_api_for_a_minute_and_ends_the_run(n):
    n.reader._held_now = lambda: [{"isin": HELD}, {"isin": TRADED}]
    n.upstox.answers.update({TRADED: 429, HELD: []})  # read in the order of their ISINs
    status, note = await n.reader.run(NIGHT)
    assert status == "failed" and note.endswith(ca.WORDS["slow"])
    assert n.upstox.asked == [TRADED] and n.reader.budget.paused_for(ca.API) > 0
    assert await n.read_one(HELD, NOW) == "slow"
    assert n.upstox.asked == [TRADED]


@night
async def test_a_refused_token_stops_every_read_until_the_token_file_changes(n):
    n.upstox.answers[HELD] = 401
    assert await n.read_one(HELD, NOW) == "token_refused"
    assert n.svc.upstox._refused is True  # the live book pauses with it
    assert await n.read_one(HELD, NOW) == "token_refused"
    assert n.upstox.asked == [HELD]


@night
async def test_three_silent_answers_in_a_row_end_the_run(n):
    isins = [f"INE000{letter}01010" for letter in "FGHJ"]
    n.reader._held_now = lambda: [{"isin": isin} for isin in isins]
    n.upstox.answers.update(dict.fromkeys(isins, 503))
    status, note = await n.reader.run(NIGHT)
    assert status == "failed" and note.endswith(ca.WORDS["silent"])
    assert len(n.upstox.asked) == ca.SILENT_STOP


# ---- the hours and the job ----------------------------------------------------------------------
def test_the_run_belongs_to_the_night_and_never_to_market_hours():
    midnight = NOW - (13 * 60 + 3) * 60_000

    def at(hour: int, minute: int) -> int | None:
        return ca.due_day(midnight + (hour * 60 + minute) * 60_000)

    assert at(23, 44) is None and at(23, 45) == TODAY and at(23, 59) == TODAY
    assert at(0, 0) == at(7, 59) == 20261025  # the night that began the day before
    for hour in range(8, 23):
        assert at(hour, 0) is None and at(hour, 30) is None


@night
async def test_the_job_runs_once_a_night_and_never_by_day(n):
    n.hold()
    n.upstox.answers[HELD] = [SAMPLE]
    n.rig.now = NOW
    assert await ca.corporate_actions_step(n.svc, NOW) is None  # 13:03: not its hours
    assert n.upstox.asked == []
    n.rig.now = NIGHT
    assert await ca.corporate_actions_step(n.svc, NIGHT) == "ok"
    row = n.read(lambda tx: tx.job_get("corporate_actions", TODAY))
    assert row["status"] == "ok" and row["detail"].startswith("1 of 1 instruments read")
    assert await ca.corporate_actions_step(n.svc, NIGHT + 60_000) is None
    assert await ca.corporate_actions_step(n.svc, NIGHT + 2 * 3_600_000) is None  # 01:50
    assert n.upstox.asked == [HELD]


def test_the_job_is_registered_under_the_contracts_name_and_its_switch(monkeypatch):
    from sudarshan.portfolio import service, timers
    from sudarshan.portfolio.core import kinds

    monkeypatch.setattr(service, "JOBS", {})
    timers.register_all()
    assert ca.API in kinds.JOB_NAMES and ca.API in kinds.SWITCHED_JOBS["insights"]
    assert getattr(service.JOBS[ca.API], "switch", None) == "insights"


# ---- on demand ------------------------------------------------------------------------------------
@night
async def test_a_read_on_demand_is_for_an_instrument_that_matters_and_once_a_minute(n):
    n.hold()
    n.upstox.answers[HELD] = [SAMPLE]
    assert await n.read_now("INE999Z01019", NOW) == "unknown"
    assert n.upstox.asked == []
    assert await n.read_now(HELD, NOW) == "ok"
    assert await n.read_now(HELD, NOW + 59_000) == "wait"
    assert await n.read_now(HELD, NOW + 60_000) == "ok"
    assert n.upstox.asked == [HELD, HELD]
