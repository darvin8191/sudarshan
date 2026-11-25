"""A 401 on a read other than holdings and positions is settled before its words are chosen
(M13.12, v0.18.0 verification V2).

Such a 401 may mean this token may not make that read, or that Upstox refuses the token itself,
first met on this read: the Funds view reads funds alone, and used to say "the rest of the Upstox
tab is not affected" while the System page said accepted. Now a holdings or positions read of the
same token file answered since the refused read was sent settles it; otherwise one holdings read
goes, single flight and through the budget. Never more than that one call, none while a refusal
is already settled, and none at all while Upstox answers. Upstox is a transport in memory; nothing
here leaves the machine.
"""

from __future__ import annotations

import asyncio
import re

import httpx
import pytest
from test_portfolio_orders_trades import TOKEN, Clock, ist
from test_portfolio_token_refusals import BODIES, PATHS

from sudarshan.portfolio import reports
from sudarshan.portfolio import upstox as up
from sudarshan.portfolio.budget import Budget

AT_1115 = " SUDARSHAN asks Upstox again after 11:15 on Mon 26 Oct."
AT_0800 = " SUDARSHAN asks Upstox again after 08:00 on Tue 27 Oct."
FOUR = ("holdings", "positions", "mtf", "funds")


class Upstox:
    """Upstox in memory: each read answered 200 unless ``status`` says otherwise, and each call
    logged with the time it was made. ``gate`` holds a read's answer until its event is set."""

    def __init__(self, clock: Clock):
        self.clock, self.status, self.calls = clock, {}, []
        self.gate: dict[str, asyncio.Event] = {}

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        read = PATHS[request.url.path]
        self.calls.append((read, self.clock()))
        if read in self.gate:
            await self.gate[read].wait()
        status = self.status.get(read, 200)
        if status == 200:
            return httpx.Response(200, json=BODIES[read])
        return httpx.Response(status, json={"status": "error", "errors": []})

    def asked(self, read: str) -> list[int]:
        return [at for name, at in self.calls if name == read]


class World:
    def __init__(self, tmp_path, at=ist(11)):
        token = tmp_path / "token"
        token.write_text(TOKEN + "\n")
        self.clock = Clock(at)
        self.upstox = Upstox(self.clock)
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.upstox))
        self.reader = up.UpstoxReader(
            token, "http://upstox.test", clock=self.clock, client=self.client,
            budget=Budget(clock=lambda: self.clock.mono),
        )  # fmt: skip
        self.line: list[str] = []  # what the token check (the System page) was told, in turn
        self.reader.on_verdict = lambda state, at, sig: self.line.append(state)

    async def view(self, *parts: str) -> dict:
        return await self.reader.book(list(parts), 0)


def go(tmp_path, steps, at=ist(11)):
    async def main():
        world = World(tmp_path, at)
        try:
            return world, await steps(world)
        finally:
            await world.client.aclose()

    return asyncio.run(main())


def test_the_funds_view_first_to_meet_a_refused_token_says_so_and_the_line_fails(tmp_path):
    """The verification's probe: the token stops being accepted while the owner is on the Funds
    view. Funds says the token's words, not that the rest of the tab is fine; the System page
    says failed; the Holdings view and the next funds read ask nothing more."""

    async def steps(world):
        world.upstox.status.update(dict.fromkeys(FOUR, 401))
        funds = (await world.view("funds"))["funds"]
        first = [name for name, _ in world.upstox.calls]
        world.clock.advance(45)
        again = (await world.view("funds"))["funds"]
        holdings = (await world.view("holdings"))["holdings"]
        return funds, first, again, holdings

    world, (funds, first, again, holdings) = go(tmp_path, steps)
    assert funds["note"] == again["note"] == holdings["note"] == up.WORDS["token_refused"]
    assert first == [
        "funds",
        "holdings",
    ]  # the funds read, and the one holdings read it let through
    assert len(world.upstox.calls) == 2 and world.line == ["failed"]
    assert world.reader._paused == {}


def test_funds_refused_alone_costs_one_holdings_read_and_its_tries_none(tmp_path):
    """The token is accepted and may not read funds. The Funds view reads every 45 seconds from
    Monday 11:00 to Tuesday 08:01: Upstox is asked for funds at 11:00, at the 11:15 try and at
    Tuesday's 08:00 (v0.17.0 review M2, unchanged), and for holdings once, at 11:00, to settle the
    first refusal's words. The tries are refusals of a read known to be refused alone: none lets
    a holdings read through."""

    async def steps(world):
        world.upstox.status["funds"] = 401
        views = {}
        while world.clock() <= ist(8, 1, day=27):
            views[world.clock()] = (await world.view("funds"))["funds"]
            world.clock.advance(45)
        return views

    world, views = go(tmp_path, steps)
    assert world.upstox.asked("funds") == [ist(11), ist(11, 15), ist(8, 0, day=27)]
    assert world.upstox.asked("holdings") == [ist(11)] and world.line == ["ok"]
    not_funds = up.WORDS["not_allowed_funds"]
    assert views[ist(11)]["note"] == views[ist(11, 14, 15)]["note"] == not_funds + AT_1115
    assert views[ist(11, 15)]["note"] == views[ist(23)]["note"] == not_funds + AT_0800


@pytest.mark.parametrize("first", ["mtf", "positions"])
def test_the_positions_view_settles_its_own_mtf_refusal_with_no_call(tmp_path, first):
    """Nothing extra while the token is accepted: on the Positions view MTF is refused and
    positions answered in the same refresh, in either order (an event, not a timer, holds the
    second answer), and no holdings read is made."""

    async def steps(world):
        world.upstox.status["mtf"] = 401
        reader, held = world.reader, asyncio.Event()
        if first == "mtf":  # positions answers once MTF's refusal is being settled
            judge = reader.judge

            async def judging(*args):
                held.set()
                return await judge(*args)

            reader.judge = judging
            world.upstox.gate["positions"] = held
        else:  # MTF's refusal lands once positions' answer is taken
            accept = reader._accept

            def accepted(*args):
                accept(*args)
                held.set()

            reader._accept = accepted
            world.upstox.gate["mtf"] = held
        return await world.view("positions", "mtf")

    world, views = go(tmp_path, steps)
    assert sorted(name for name, _ in world.upstox.calls) == ["mtf", "positions"]
    assert views["mtf"]["note"] == up.WORDS["not_allowed_mtf"] + AT_1115
    assert views["positions"]["state"] == "ok" and world.line == ["ok"]


@pytest.mark.parametrize("token", ["accepted", "refused"])
def test_two_refusals_in_one_refresh_let_one_holdings_read_through(tmp_path, token):
    """MTF and funds refused in one refresh with neither holdings nor positions in it: one
    holdings read settles both (single flight), and both say the same thing."""

    async def steps(world):
        refused = ("mtf", "funds") if token == "accepted" else FOUR
        world.upstox.status.update(dict.fromkeys(refused, 401))
        return await world.view("mtf", "funds")

    world, views = go(tmp_path, steps)
    assert world.upstox.asked("holdings") == [ist(11)] and world.upstox.asked("positions") == []
    if token == "accepted":
        assert views["mtf"]["note"] == up.WORDS["not_allowed_mtf"] + AT_1115
        assert views["funds"]["note"] == up.WORDS["not_allowed_funds"] + AT_1115
        assert world.line == ["ok"]
    else:
        assert {view["note"] for view in views.values()} == {up.WORDS["token_refused"]}
        assert world.line == ["failed"] and world.reader._paused == {}


@pytest.mark.parametrize("why", ["a fault", "an empty bucket"])
def test_a_refusal_the_holdings_read_could_not_settle_says_nothing_about_the_rest(tmp_path, why):
    """The holdings read meets a fault, or the budget has nothing left for it: funds says it was
    refused, and nothing about the rest of the tab; the token line is told nothing. Its try at
    11:15 is settled afresh (one holdings read, answered), and says the rest is not affected."""

    async def steps(world):
        world.upstox.status.update(funds=401, holdings=503)
        take = world.reader.budget.take
        if why == "an empty bucket":
            world.upstox.status.pop("holdings")
            world.reader.budget.take = lambda api, principal="owner": (
                api != "holdings" and take(api, principal)
            )
        first = (await world.view("funds"))["funds"]
        world.upstox.status.pop("holdings", None)
        world.reader.budget.take = take
        world.clock.advance(15 * 60)
        return first, (await world.view("funds"))["funds"]

    world, (first, then) = go(tmp_path, steps)
    assert first["note"] == up.WORDS["unsure_funds"] + AT_1115
    assert "affected" not in first["note"]
    tried = [ist(11)] if why == "a fault" else []
    assert world.upstox.asked("holdings") == [*tried, ist(11, 15)]
    assert then["note"] == up.WORDS["not_allowed_funds"] + AT_0800
    assert world.line == ["ok"]  # the 503 is not an answer about the token; the 11:15 read is


def test_a_later_holdings_answer_settles_a_refusal_left_unsure(tmp_path):
    """Left unsure, funds says the rest is not affected once the tab's own holdings read is
    answered (no call of its own), until its try."""

    async def steps(world):
        world.upstox.status.update(funds=401, holdings=503)
        first = (await world.view("funds"))["funds"]
        world.upstox.status.pop("holdings")
        world.clock.advance(60)
        await world.view("holdings")
        world.clock.advance(45)
        return first, (await world.view("funds"))["funds"]

    world, (first, then) = go(tmp_path, steps)
    assert first["note"] == up.WORDS["unsure_funds"] + AT_1115
    assert then["note"] == up.WORDS["not_allowed_funds"] + AT_1115
    assert world.upstox.asked("funds") == [ist(11)] and len(world.upstox.asked("holdings")) == 2


@pytest.mark.parametrize("token", ["accepted", "refused"])
def test_a_report_read_first_to_meet_a_refusal_is_settled_the_same_way(tmp_path, token):
    """The trade history (the History view, Read again, the nightly read) meets the 401 first:
    the same one holdings read settles it, and the report's words follow."""

    async def steps(world):
        refused = ("trades",) if token == "accepted" else ("trades", "holdings", "positions")
        world.upstox.status.update(dict.fromkeys(refused, 401))
        outcome, _ = await reports.ReportReader(None, world.reader)._call("trades", {})
        return outcome

    world, outcome = go(tmp_path, steps)
    assert world.upstox.asked("holdings") == [ist(11)]
    if token == "accepted":
        assert outcome == "trades_not_allowed" and world.line == ["ok"]
    else:
        assert outcome == "token_refused" and world.line == ["failed"]
        assert world.reader._paused == {}


def test_nothing_is_asked_more_while_upstox_answers(tmp_path):
    """The quiet path: an hour of the tab's reads with every answer 200 asks Upstox exactly what
    the tab reads, and no holdings read more."""

    async def steps(world):
        for _ in range(80):
            await world.view("funds")
            await world.view("positions", "mtf")
            world.clock.advance(45)
        return None

    world, _ = go(tmp_path, steps)
    counts = {read: len(world.upstox.asked(read)) for read in FOUR}
    assert counts == {"holdings": 0, "positions": 80, "mtf": 80, "funds": 80}


def test_words_for_a_refusal_not_settled_claim_nothing_and_suggest_no_new_token():
    unsure = [up.WORDS[f"unsure_{part}"] for part in ("mtf", "funds", "orders", "trades")]
    unsure += [reports.WORDS[key] for key in reports.WORDS if "unsure" in key]
    assert len(unsure) == 9
    for sentence in unsure:
        assert sentence.startswith("Upstox did not let SUDARSHAN's read-only token read "), sentence
        assert "affected" not in sentence and sentence.endswith("."), sentence
        assert not re.search(r"accept|creat|revok|renew|replace|new token|\b[45]\d\d\b", sentence)
