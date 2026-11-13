"""upstox_book and upstox_history through the sealed API, against a fake Upstox."""

from __future__ import annotations

import asyncio

import pytest
from portfolio_fakes import FakeUpstox
from sqlalchemy import insert
from test_portfolio_import_ops import NOW, OWNER, Rig, scenario_of
from test_portfolio_upstox import TOKEN

from sudarshan.portfolio.upstox import UpstoxReader

DAY = 20261023


@pytest.fixture
def fake():
    server = FakeUpstox()
    server.start()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def _wire(fake, tmp_path, monkeypatch):
    token = tmp_path / "token"
    token.write_text(TOKEN)
    monkeypatch.setattr(Rig, "__init_upstox__", lambda self: None, raising=False)
    orig = Rig.__init__

    def init(self, path):
        orig(self, path)
        self.svc.upstox = UpstoxReader(token, fake.url, clock=lambda: self.now)

    monkeypatch.setattr(Rig, "__init__", init)


@scenario_of
async def test_the_book_answers_the_parts_asked_for_in_order_and_only_those(rig):
    answer = await rig.call("upstox_book", parts=["funds", "holdings"])
    data = answer["data"]
    assert list(data) == ["holdings", "funds"]
    assert data["holdings"]["state"] == "ok" and data["holdings"]["read_at"] == NOW
    assert data["holdings"]["data"][0]["qty_u"] == 10_000_000
    assert data["funds"]["data"]["available_paise"] == 12_500_050
    assert TOKEN not in str(answer)


@scenario_of
async def test_the_summary_carries_the_upstox_holdings_for_the_dashboard_card(rig):
    """M3.11: the card's Upstox row comes with the summary, from the reader's memory, so the
    phone reads once and prices every row from the same ticks."""
    data = (await rig.call("summary"))["data"]
    upstox = next(e for e in data["entries"] if e["tab"]["system"])
    assert upstox["live"] is None
    assert upstox["upstox"]["state"] == "ok" and upstox["upstox"]["read_at"] == NOW
    assert upstox["upstox"]["holdings"][0]["qty_u"] == 10_000_000
    assert upstox["upstox"]["note"] is None
    assert all(e["upstox"] is None for e in data["entries"] if not e["tab"]["system"])


def pin(rig):
    """Pin the rig's tab, so that the summary (the dashboard card) carries it."""
    rig.book.tab_update(OWNER, rig.tab["id"], rig.version(), {"pinned": True}, now=rig.now)


class StuckReader:
    """A reader whose holdings read waits until the test lets it go (a slow Upstox), then answers
    as the real one does, or raises (a reader that is broken)."""

    def __init__(self, real, *, fail=False):
        self.real, self.fail, self.calls, self.go = real, fail, 0, asyncio.Event()

    async def book(self, parts, max_age_s=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("the reader broke")
        await self.go.wait()
        return await self.real.book(parts, max_age_s)


@scenario_of
async def test_a_slow_upstox_leaves_the_card_its_tabs_and_a_row_that_is_still_reading(rig):
    """MEDIUM-1: the summary answers inside the bridge's time. The owner's tabs are there, the
    Upstox row says it is still being read (in words about Upstox, never about the service), and a
    later summary, once the read is done, has the figures."""
    from sudarshan.portfolio.ops import tab_ops

    pin(rig)
    tab_ops.UPSTOX_WAIT_S = 0.05
    stuck = StuckReader(rig.svc.upstox)
    rig.svc.upstox = stuck
    try:
        await slow_summary(rig, stuck)
    finally:
        tab_ops.UPSTOX_WAIT_S = 1.0
        stuck.go.set()


async def slow_summary(rig, stuck):
    from sudarshan.portfolio.ops import tab_ops

    first = (await rig.call("summary"))["data"]
    names = [e["tab"]["name"] for e in first["entries"]]
    assert "Upstox" in names and len(names) >= 2  # the tabs are in
    upstox = next(e for e in first["entries"] if e["tab"]["system"])["upstox"]
    assert upstox["state"] == "reading" and upstox["holdings"] == [] and upstox["read_at"] is None
    assert upstox["note"] == tab_ops.UPSTOX_READING
    assert "not answering" not in upstox["note"] and "service" not in upstox["note"].lower()
    # A second ask while the read is out joins it: one read, not two.
    await rig.call("summary")
    assert stuck.calls == 1
    stuck.go.set()
    await asyncio.sleep(0.05)
    later = (await rig.call("summary"))["data"]
    done = next(e for e in later["entries"] if e["tab"]["system"])["upstox"]
    assert done["state"] == "ok" and done["holdings"][0]["qty_u"] == 10_000_000


@scenario_of
async def test_a_reader_that_breaks_gives_the_row_its_sentence_and_the_card_its_tabs(rig):
    """MEDIUM-1, the failure path: the row says Upstox could not be read; the tabs stay."""
    from sudarshan.portfolio.ops import tab_ops

    pin(rig)
    rig.svc.upstox = StuckReader(rig.svc.upstox, fail=True)
    data = (await rig.call("summary"))["data"]
    assert len(data["entries"]) >= 2
    upstox = next(e for e in data["entries"] if e["tab"]["system"])["upstox"]
    assert upstox == {
        "state": "unavailable",
        "read_at": None,
        "note": tab_ops.UPSTOX_NOT_READ,
        "holdings": [],
    }
    assert "not answering" not in upstox["note"]


@scenario_of
async def test_no_parts_means_all_four_and_max_age_is_read_as_seconds(rig):
    first = (await rig.call("upstox_book"))["data"]
    assert list(first) == ["holdings", "positions", "mtf", "funds"]
    await rig.call("upstox_book", max_age=60)
    assert rig.svc.upstox.client is not None
    from_memory = (await rig.call("upstox_book", parts=["mtf"], max_age=60))["data"]
    assert from_memory["mtf"]["data"] == []


@scenario_of
async def test_a_request_with_a_wrong_part_or_age_is_refused_in_words(rig):
    for fields in ({"parts": ["nope"]}, {"parts": []}, {"parts": "holdings"}, {"max_age": -1},
                   {"max_age": "5"}, {"max_age": True}):  # fmt: skip
        answer = await rig.call("upstox_book", **fields)
        assert answer["code"] == "bad_request", fields
        assert "Reload the page" in answer["message"]


def eod(rig, day, read_at, value, cost, pnl):
    def run(tx):
        tx.conn.execute(
            insert(tx._t("upstox_eod")).values(
                day=day,
                principal=tx.who,
                read_at=read_at,
                holdings_value_paise=value,
                holdings_cost_paise=cost,
                positions_pnl_paise=pnl,
                funds=None,
                created_at=read_at,
                updated_at=read_at,
            )
        )

    rig.svc.store.write(OWNER, run)


@scenario_of
async def test_history_is_the_closing_records_newest_last_with_late_and_provisional(rig):
    empty = (await rig.call("upstox_history"))["data"]
    assert empty["days"] == [] and "15:55" in empty["note"]
    at_1555 = NOW - 3 * 86_400_000 + 10_300_000  # Fri 23 Oct 2026, 15:55 IST
    eod(rig, DAY, at_1555, 1_000_000, 900_000, 500)
    eod(rig, 20261022, at_1555 - 86_400_000 + 3600_000 * 1, 900_000, 800_000, -500)  # 16:55: late
    eod(rig, 20261021, at_1555 - 2 * 86_400_000, 800_000, 800_000, 0)
    rig.now = NOW  # Mon 26 Oct 13:03: after the 09:20 settle of Mon
    days = (await rig.call("upstox_history"))["data"]["days"]
    assert [d["day"] for d in days] == [20261021, 20261022, DAY]
    friday = days[-1]
    assert friday["holdings_value_paise"] == 1_000_000 and friday["positions_pnl_paise"] == 500
    assert friday["late"] is False and friday["provisional"] is False
    assert days[1]["late"] is True
    rig.now = at_1555 + 3_600_000  # Friday 16:55: the next settle is Monday 09:20
    assert (await rig.call("upstox_history"))["data"]["days"][-1]["provisional"] is True
    limited = (await rig.call("upstox_history", limit=2))["data"]["days"]
    assert [d["day"] for d in limited] == [20261022, DAY]
    assert (await rig.call("upstox_history", limit=0))["code"] == "bad_request"


def ist(day: str, hhmm: str) -> int:
    """Epoch ms of ``hhmm`` IST on the ISO date ``day``."""
    from datetime import date

    from sudarshan.alerts.market.sessions import epoch_ms

    hours, minutes = map(int, hhmm.split(":"))
    return epoch_ms(date.fromisoformat(day), hours * 60 + minutes)


def clock_of(epoch_ms: int) -> str:
    from sudarshan.portfolio.upstox import minute_of_day

    minute = minute_of_day(epoch_ms)
    return f"{minute // 60:02d}:{minute % 60:02d}"


@scenario_of
async def test_the_history_names_the_times_of_the_day_in_question(rig):
    """Either side of 7 Dec 2026 (F&O to 15:45): the first record is taken at 15:55 before and
    16:00 after; each day's record is due at its own time, and late 45 minutes after its close
    (16:25 on 4 Dec, 16:30 on 8 and 10 Dec, a NIFTY and a SENSEX expiry)."""

    async def note() -> str:
        return (await rig.call("upstox_history"))["data"]["note"]

    rig.now = ist("2026-12-04", "10:00")
    assert await note() == "No closing record has been taken yet. The first is taken at 15:55."
    rig.now = ist("2026-12-04", "15:56")  # Friday's has passed: Monday 7 Dec's is next
    assert await note() == "No closing record has been taken yet. The first is taken at 16:00."
    rig.now = ist("2026-12-10", "10:00")
    assert (await note()).endswith("taken at 16:00.")
    eod(rig, 20261204, ist("2026-12-04", "16:26"), 1_000_000, 900_000, 0)  # after 16:25: late
    eod(rig, 20261203, ist("2026-12-03", "16:25"), 1_000_000, 900_000, 0)  # 16:25: on time
    eod(rig, 20261208, ist("2026-12-08", "16:31"), 1_000_000, 900_000, 0)  # after 16:30: late
    eod(rig, 20261210, ist("2026-12-10", "16:28"), 1_000_000, 900_000, 0)  # on time
    rig.now = ist("2026-12-11", "12:00")
    data = (await rig.call("upstox_history"))["data"]
    shown = {d["day"]: d for d in data["days"]}
    assert data["note"] is None
    assert {day: d["late"] for day, d in shown.items()} == {
        20261203: False, 20261204: True, 20261208: True, 20261210: False
    }  # fmt: skip
    assert {day: clock_of(d["due_at"]) for day, d in shown.items()} == {
        20261203: "15:55", 20261204: "15:55", 20261208: "16:00", 20261210: "16:00"
    }  # fmt: skip

