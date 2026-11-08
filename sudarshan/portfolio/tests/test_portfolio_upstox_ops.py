"""upstox_book and upstox_history through the sealed API, against a fake Upstox."""

from __future__ import annotations

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
