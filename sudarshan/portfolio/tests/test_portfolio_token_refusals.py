"""Which reads a 401 pauses (M13.12, v0.17.0 verification V1).

A 401 on holdings or positions is Upstox refusing the Analytics Token itself: every read pauses
and the token line fails. A 401 on any other read may only mean this token may not make that read:
that read alone pauses, in its own words, and everything else keeps reading. The vectors
(``vectors/token_refusals.json``, written out in ``make_token_refusals.py``) give each of the
twelve kinds of read; the scenario below runs the journal's refused day trades through the sealed
API and checks the Upstox tab, the reports, Performance and the closing record. Upstox is a
transport in memory or a fake on loopback; nothing here leaves the machine.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import re
from pathlib import Path

import httpx
import pytest
from portfolio_fakes import (
    CHARGES,
    DAY_TRADES,
    FUNDS,
    HOLDINGS,
    ORDER_HISTORY,
    ORDERS,
    PNL,
    PNL_META,
    POSITIONS,
    TRADES,
)
from test_portfolio_import_ops import NOW, OWNER
from test_portfolio_orders_trades import TOKEN, Clock, ist
from test_portfolio_purposes import fake as fake  # noqa: F401  (the fake Upstox fixture)
from test_portfolio_purposes import scenario
from test_portfolio_upstox_parity import RECORDS, TCS, _fill, _trade

from sudarshan.portfolio import corpactions as ca
from sudarshan.portfolio import eod, probe, reports
from sudarshan.portfolio import upstox as up
from sudarshan.portfolio.budget import Budget
from sudarshan.portfolio.core.days import day_of_instant
from sudarshan.portfolio.core.kinds import UPSTOX_PARTS

FILE = json.loads(
    (Path(__file__).parent / "vectors" / "token_refusals.json").read_text(encoding="utf-8")
)
WORDS = FILE["words"]
ISIN = "INE002A01018"
#: Upstox's read paths, by the budget's name of each read.
PATHS = {
    "/v2/portfolio/long-term-holdings": "holdings",
    "/v2/portfolio/short-term-positions": "positions",
    "/v3/portfolio/mtf-positions": "mtf",
    "/v3/user/get-funds-and-margin": "funds",
    "/v2/order/retrieve-all": "orders",
    "/v2/order/trades/get-trades-for-day": "day_trades",
    "/v2/charges/historical-trades": "trades",
    "/v2/trade/profit-loss/metadata": "pnl_meta",
    "/v2/trade/profit-loss/data": "pnl",
    "/v2/trade/profit-loss/charges": "charges",
    "/v2/order/history": "order_history",
}
EMPTY = {"status": "success", "data": []}
BODIES = {
    "holdings": HOLDINGS, "positions": POSITIONS, "mtf": EMPTY, "funds": FUNDS, "orders": ORDERS,
    "day_trades": DAY_TRADES, "trades": TRADES, "pnl_meta": PNL_META, "pnl": PNL,
    "charges": CHARGES, "order_history": ORDER_HISTORY, "corporate_actions": EMPTY,
}  # fmt: skip
#: The reader's part of each read on the live book (the day's trades are its part ``trades``).
PART_OF = {"orders": "orders", "day_trades": "trades"}
TAB = ("holdings", "positions", "mtf", "funds", "orders", "day_trades")


class World:
    """One reader of each kind (the live book, the reports, corporate actions) on an Upstox in
    memory that answers the read ``refused`` with a 401 and every other one as it should."""

    def __init__(self, tmp_path: Path, refused: str):
        self.token = tmp_path / "token"
        self.token.write_text(TOKEN + "\n")
        self.refused, self.asked = refused, []
        self.clock = Clock(ist(11))
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._answer))
        self.reader = up.UpstoxReader(
            self.token, "http://upstox.test", clock=self.clock,
            budget=Budget(clock=lambda: self.clock.mono), client=self.client,
        )  # fmt: skip
        self.line: list[str] = []  # what the token check was told, in turn
        self.reader.on_verdict = lambda state, at, sig: self.line.append(state)
        self.reports = reports.ReportReader(None, self.reader)
        self.corp = ca.CorpActions(
            None, self.reader, budget=Budget(ca.LIMITS, lambda: self.clock.mono)
        )

    def _answer(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        read = "corporate_actions" if path.endswith("/corporate-actions") else PATHS[path]
        self.asked.append(read)
        if read == self.refused:
            return httpx.Response(401, json={"status": "error", "errors": []})
        return httpx.Response(200, json=BODIES[read])

    async def read(self, kind: str) -> tuple[bool, str | None]:
        """(whether Upstox was asked, the sentence shown; None when the read was answered)."""
        self.clock.advance(3)  # past the 2 s a failed read is reused and the per-second bucket
        before = len(self.asked)
        if kind in TAB:
            part = PART_OF.get(kind, kind)
            view = (await self.reader.book([part], 0))[part]
            said = None if view["state"] == "ok" else view["note"]
        elif kind == "corporate_actions":
            outcome, _ = await self.corp._call(ISIN)
            said = None if outcome == "ok" else ca.WORDS[outcome]
        else:
            outcome, _ = await self.reports._call(kind, {})
            said = None if outcome == "ok" else reports.WORDS[outcome]
        return len(self.asked) > before, said


@pytest.mark.parametrize("case", FILE["cases"], ids=lambda c: c["id"])
def test_a_401_pauses_what_the_vectors_say_in_the_words_they_say(tmp_path, case):
    async def go():
        world = World(tmp_path, case["refused"])
        try:
            first = await world.read(case["refused"])
            then = {kind: await world.read(kind) for kind in FILE["reads"]}
            return world, first, then
        finally:
            await world.client.aclose()

    world, first, then = asyncio.run(go())
    assert first == (True, WORDS[case["first"]])
    for kind, want in case["then"].items():
        expected = {"asks": (True, None), "asks_again": (True, WORDS[case["first"]])}
        assert then[kind] == expected.get(want, (False, WORDS.get(want))), kind
    assert world.line[-1] == case["token_line"]


def test_the_vectors_cover_every_read_the_token_makes():
    live = {up.API_OF.get(part, part) for part in UPSTOX_PARTS}
    assert set(FILE["reads"]) == live | set(reports.PATHS) | {ca.API}
    assert [c["refused"] for c in FILE["cases"]] == FILE["reads"]
    assert up.JUDGES == ("holdings", "positions")


def test_a_read_refused_alone_never_says_the_token_was_refused_or_suggests_another():
    alone = [
        *(up.WORDS[f"not_allowed_{part}"] for part in ("mtf", "funds", "orders", "trades")),
        *(reports.WORDS[key] for key in reports.WORDS if "not_allowed" in key),
        ca.WORDS["not_allowed"],
    ]
    assert len(alone) == 10
    for sentence in alone:
        assert sentence.startswith("Upstox did not let SUDARSHAN's read-only token read "), sentence
        assert "not affected" in sentence, sentence
        assert not re.search(r"accept|creat|revok|renew|replace|new token|\b[45]\d\d\b", sentence)


# ---- a refused token says one thing in one refresh (v0.17.0 review M1) --------------------------
FOUR = ("holdings", "positions", "mtf", "funds")
TAIL = {"long-term-holdings": "holdings", "short-term-positions": "positions",
        "mtf-positions": "mtf", "get-funds-and-margin": "funds"}  # fmt: skip


@pytest.mark.parametrize("order", list(itertools.permutations(FOUR)), ids="-".join)
def test_a_refused_token_says_one_thing_in_one_refresh_whatever_order_upstox_answers(
    tmp_path, order
):
    """The token stops being accepted and the tab's next refresh gets 401 on all four reads,
    sent at once and answered in ``order`` (each answer waits until the one before it has been
    taken: an event, not a timer). Holdings, positions, MTF and funds all say the token was not
    accepted, in that refresh and the next; no part keeps a pause of its own and the token line
    is failed. The reviewer's two probes are holdings-positions-mtf-funds and its reverse."""

    async def go():
        token = tmp_path / "token"
        token.write_text(TOKEN + "\n")
        clock = Clock(ist(11))
        taken = {name: asyncio.Event() for name in FOUR}
        sent, all_sent = [], asyncio.Event()

        async def answer(request: httpx.Request) -> httpx.Response:
            part = TAIL[request.url.path.rsplit("/", 1)[-1]]
            sent.append(part)
            if len(sent) == len(FOUR):
                all_sent.set()
            await all_sent.wait()  # all four are in flight before any answer lands
            turn = order.index(part)
            if turn:
                await taken[order[turn - 1]].wait()
            return httpx.Response(401, json={"status": "error", "errors": []})

        client = httpx.AsyncClient(transport=httpx.MockTransport(answer))
        reader = up.UpstoxReader(token, "http://upstox.test", clock=clock, client=client,
                                 budget=Budget(clock=lambda: clock.mono))  # fmt: skip
        line, landed = [], []
        reader.on_verdict = lambda state, at, sig: line.append(state)
        refused = reader.refused

        def took(api, sig):
            why = refused(api, sig)
            landed.append(api)
            taken[api].set()
            return why

        reader.refused = took
        try:
            first = await reader.book(list(FOUR), 0)
            clock.advance(3)
            then = await reader.book(list(FOUR), 0)
        finally:
            await client.aclose()
        return first, then, landed, line, reader, sent

    first, then, landed, line, reader, sent = asyncio.run(go())
    assert sorted(sent) == sorted(FOUR) and landed == list(order)
    for views in (first, then):
        assert {part: view["note"] for part, view in views.items()} == dict.fromkeys(
            FOUR, up.WORDS["token_refused"]
        )
        assert {view["state"] for view in views.values()} == {"unavailable"}
    assert reader._paused == {} and line == ["failed", "failed"]


def test_a_401_after_the_token_was_refused_for_the_same_file_is_that_refusal(tmp_path):
    """A report or a part read sent before the holdings refusal and answered after it says the
    token's words and keeps no pause of its own; a 401 to a read sent with another token file is
    still that read's alone."""
    token = tmp_path / "token"
    token.write_text(TOKEN + "\n")
    reader = up.UpstoxReader(token, "http://upstox.test", clock=Clock(ist(11)))
    sig = up._signature(token)
    assert reader.refused("funds", sig) == "read" and set(reader._paused) == {"funds"}
    assert reader.refused("positions", sig) == "token"
    for api in ("mtf", "funds", "orders", "day_trades", "trades", "pnl", "order_history"):
        assert reader.refused(api, sig) == "token", api
    assert reader._paused == {}
    other = (sig[0] + 1, sig[1])
    assert reader.refused("funds", other) == "read" and reader._paused == {"funds": other}


# ---- the journal's day trades refused, through the sealed API ----------------------------------
@scenario
async def test_a_refusal_of_the_days_trades_alone_leaves_everything_else_working(rig, fake):
    """The premise of the verification's V1: Upstox answers 401 when this token reads the day's
    trades (the journal reads them on each fill, at the close check and at 22:00). The journal is
    told so in words; holdings, positions, funds and MTF, the reports and Performance all keep
    reading, the closing record gets its funds, and the token line says accepted."""
    rig.svc.upstox.on_verdict = lambda state, at, sig: probe.heard(rig.svc, state, at, sig)
    fake.status["day_trades"] = 401
    status, body = await rig.journal("trades_day")
    day = body["data"]
    assert status == 200 and day["state"] == "unavailable" and day["data"] is None
    assert day["note"] == up.WORDS["not_allowed_trades"]

    tab = await rig.owner("upstox_book")
    assert {part: tab[part]["state"] for part in tab} == dict.fromkeys(
        ("holdings", "positions", "mtf", "funds"), "ok"
    )
    assert tab["funds"]["data"]["available_paise"] == 12_500_050
    again = (await rig.journal("trades_day"))[1]["data"]
    assert again["note"] == up.WORDS["not_allowed_trades"] and fake.count("day_trades") == 1

    history = await rig.owner("report_trades", fy=2026, segment="EQ")
    assert history["state"] == "ok" and history["note"] is None and fake.count("trades") == 1
    pnl = await rig.owner("report_pnl", fy=2026, segment="EQ")
    assert pnl["state"] == "ok" and fake.count("pnl_meta") == 1

    _fill(rig, [_trade(20251023, TCS, "BUY", 10, 30000)], RECORDS, years=(2025, 2024))
    performance = await rig.owner("upstox_performance")
    assert performance["state"] == "ok" and performance["note"] is None
    assert performance["data"]["value_paise"] == 3_300_000

    today = day_of_instant(NOW)
    assert await eod.record_upstox(rig.svc, today, NOW) == "ok"
    kept = rig.svc.store.read(OWNER, lambda tx: tx.rows("upstox_eod", "day"))
    record = next(row for row in kept if row["day"] == today)
    assert json.loads(record["funds"])["available_paise"] == 12_500_050
    assert record["holdings_value_paise"] is not None
    assert probe.view(rig.svc.token_check, rig.svc.upstox.token_file)["state"] == "ok"
