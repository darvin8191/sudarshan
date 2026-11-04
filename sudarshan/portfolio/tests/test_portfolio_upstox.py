"""The Upstox reader against a fake Upstox on loopback; vectors U1 to U3 (design 9.1)."""

from __future__ import annotations

import asyncio
import copy
import logging
import os
from datetime import datetime

import httpx
import pytest
from portfolio_fakes import FUNDS, HOLDINGS, POSITIONS, FakeResolver, FakeUpstox

from sudarshan.autologin.schedule import IST
from sudarshan.portfolio import upstox as up
from sudarshan.portfolio.budget import PAUSE_S, Budget
from sudarshan.portfolio.upstox import UpstoxReader, in_funds_window, matcher

TOKEN = "synthetic-token-0123456789abcdef"  # never a real token


def ist(h, m=0, s=0, day=26) -> int:
    return int(datetime(2026, 10, day, h, m, s, tzinfo=IST).timestamp() * 1000)


class Clock:
    def __init__(self, ms):
        self.ms, self.mono = ms, 1000.0

    def __call__(self):
        return self.ms

    def advance(self, seconds):
        self.ms += int(seconds * 1000)
        self.mono += seconds


@pytest.fixture
def fake():
    server = FakeUpstox()
    server.start()
    yield server
    server.stop()


@pytest.fixture
def token_file(tmp_path):
    path = tmp_path / "token"
    path.write_text(TOKEN + "\n")
    return path


def make(fake, token_file, at=ist(11, 0), **kwargs):
    clock = Clock(at)
    budget = Budget(clock=lambda: clock.mono)
    reader = UpstoxReader(token_file, fake.url, clock=clock, budget=budget, **kwargs)
    return reader, clock


def run(coro):
    return asyncio.run(coro)


async def once(reader, parts=("holdings", "positions", "mtf", "funds"), age=None):
    try:
        return await reader.book(list(parts), age)
    finally:
        await reader.aclose()


# ---- parsing ---------------------------------------------------------------------------------
def test_holdings_become_integers_at_the_parser_half_away_from_zero(fake, token_file):
    reader, _ = make(fake, token_file)
    part = run(once(reader, ["holdings"]))["holdings"]
    assert part["state"] == "ok" and part["note"] is None and part["read_at"] == ist(11)
    tcs, small = part["data"]
    assert tcs["qty_u"] == 10_000_000 and tcs["t1_qty_u"] == 2_000_000
    assert tcs["avg_price_u"] == 34_000_050  # 3400.005 rupees
    assert tcs["last_price_u"] == 35_005_500 and tcs["close_price_u"] == 34_500_000
    assert tcs["pnl_paise"] == 100_550  # 1005.495 rupees: half a paisa goes up
    assert tcs["haircut_pct"] == "0.2000" and tcs["name"] == "Tata Consultancy Services"
    assert tcs["isin"] == "INE467B01029" and tcs["product"] == "D"
    assert small["close_price_u"] is None and small["haircut_pct"] is None
    assert small["pnl_paise"] == -3000
    for row in part["data"]:
        for key, value in row.items():
            if key.endswith(("_u", "_paise")) and value is not None:
                assert value.__class__ is int, key


def test_a_loss_rounds_half_away_from_zero_too(fake, token_file):
    body = copy.deepcopy(HOLDINGS)
    body["data"][0]["pnl"] = -1005.495
    fake.bodies["holdings"] = body
    reader, _ = make(fake, token_file)
    assert run(once(reader, ["holdings"]))["holdings"]["data"][0]["pnl_paise"] == -100_550


def test_positions_keep_their_sign_and_the_multiplier(fake, token_file):
    reader, _ = make(fake, token_file)
    row = run(once(reader, ["positions"]))["positions"]["data"][0]
    assert row["qty_u"] == -65_000_000 and row["day_sell_qty_u"] == 65_000_000
    assert row["avg_price_u"] == 240_002_500 and row["multiplier"] == 1
    assert row["pnl_paise"] == 66_625 and row["value_paise"] == -155_935_000
    assert row["realised_paise"] == 0 and row["unrealised_paise"] == 66_625


def test_funds_become_groups_of_labelled_paise_lines(fake, token_file):
    reader, _ = make(fake, token_file)
    funds = run(once(reader, ["funds"]))["funds"]["data"]
    assert funds["available_paise"] == 12_500_050
    groups = {g["key"]: g for g in funds["groups"]}
    assert set(groups) == {
        "cash_available_to_trade", "pledge_available_to_trade",
        "cash_unavailable_to_trade", "pledge_unavailable_to_trade",
    }  # fmt: skip
    cash = groups["cash_available_to_trade"]
    assert cash["label"] == "Cash available to trade" and cash["total_paise"] == 10_000_050
    lines = {ln["key"]: ln for ln in cash["lines"]}
    assert lines["cash.opening_balance"] == {
        "key": "cash.opening_balance", "label": "Opening balance", "paise": 10_000_050
    }  # fmt: skip
    assert lines["margin_used.loss.realised"]["paise"] == 0
    assert "total" not in lines  # the group's own total is not repeated as a line
    unsettled = {ln["key"]: ln["paise"] for ln in groups["cash_unavailable_to_trade"]["lines"]}
    assert unsettled == {
        "unsettled_profit.todays_profit": 1050, "unsettled_profit.previous_days": 0
    }  # fmt: skip


def test_the_token_goes_in_the_header_and_the_funds_call_names_its_version(fake, token_file):
    reader, _ = make(fake, token_file)
    run(once(reader))
    sent = dict(fake.requests)
    assert sent["holdings"]["Authorization"] == f"Bearer {TOKEN}"
    assert sent["funds"]["Api-Version"] == "3.0" and "Api-Version" not in sent["holdings"]


def test_an_answer_that_is_not_the_part_is_unreadable_and_figures_stay(fake, token_file, caplog):
    reader, clock = make(fake, token_file)

    async def go():
        first = await reader.book(["holdings"])
        fake.bodies["holdings"] = {"status": "success", "data": {"oops": 1}}
        clock.advance(10)
        with caplog.at_level(logging.ERROR):
            second = await reader.book(["holdings"])
        await reader.aclose()
        return first, second

    first, second = run(go())
    held = second["holdings"]
    assert held["state"] == "stale" and held["data"] == first["holdings"]["data"]
    assert "could not read" in held["note"] and "Figures from 11:00:00" in held["note"]


# ---- single flight and memory ------------------------------------------------------------------
def test_phones_share_one_read_and_five_seconds_of_memory(fake, token_file):
    fake.delay = 0.2
    reader, clock = make(fake, token_file)

    async def go():
        await asyncio.gather(*(reader.book(["positions"]) for _ in range(5)))
        assert fake.count("positions") == 1  # single flight
        clock.advance(4.9)
        await reader.book(["positions"])
        assert fake.count("positions") == 1  # from memory
        clock.advance(0.2)
        await reader.book(["positions"])
        assert fake.count("positions") == 2  # older than five seconds
        await reader.book(["positions"], 0)  # max_age 0 reads now
        await reader.aclose()

    run(go())
    assert fake.count("positions") == 3


def test_max_age_lets_a_page_ask_for_fresher_figures(fake, token_file):
    reader, clock = make(fake, token_file)

    async def go():
        await reader.book(["positions"])
        clock.advance(2.5)
        await reader.book(["positions"], 2)
        await reader.aclose()

    run(go())
    assert fake.count("positions") == 2


# ---- U1: the funds window ------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("clock_at", "read"),
    [
        (ist(23, 59, 59), True),
        (ist(0, 0, 0, day=27), False),
        (ist(5, 29, 59, day=27), False),
        (ist(5, 30, 0, day=27), True),
    ],
)
def test_u1_funds_are_read_outside_midnight_to_half_past_five(fake, token_file, clock_at, read):
    """Mutation target 18: the window edges."""
    reader, _ = make(fake, token_file, at=clock_at)
    funds = run(once(reader, ["funds"]))["funds"]
    assert (fake.count("funds") == 1) is read
    assert in_funds_window(clock_at) is (not read)
    if read:
        assert funds["state"] == "ok"
    else:
        assert funds["state"] == "unavailable" and funds["data"] is None
        assert funds["note"] == "Funds are available from 5:30 AM."


def test_the_window_shows_the_last_figures_with_their_time_and_never_zeros(fake, token_file):
    reader, clock = make(fake, token_file, at=ist(23, 58))

    async def go():
        await reader.book(["funds"])
        clock.advance(120)  # 00:00:00 the next day
        shown = await reader.book(["funds"])
        await reader.aclose()
        return shown["funds"]

    shown = run(go())
    assert fake.count("funds") == 1
    assert shown["state"] == "stale" and shown["data"]["available_paise"] == 12_500_050
    assert shown["note"] == (
        "Upstox does not give funds between midnight and 5:30 AM. "
        "Showing the figures read at 11:58 PM."
    )


def test_a_423_at_another_hour_is_the_same_answer(fake, token_file):
    fake.status["funds"] = 423
    reader, _ = make(fake, token_file, at=ist(11, 0))
    funds = run(once(reader, ["funds"]))["funds"]
    assert funds["state"] == "unavailable" and funds["note"] == "Funds are available from 5:30 AM."
    assert fake.count("funds") == 1


def test_the_window_only_holds_back_funds(fake, token_file):
    reader, _ = make(fake, token_file, at=ist(2, 0))
    book = run(once(reader))
    assert book["holdings"]["state"] == "ok" and book["funds"]["state"] == "unavailable"
    assert fake.count("holdings") == 1 and fake.count("funds") == 0


# ---- U3: 429 ----------------------------------------------------------------------------------------
def test_u3_a_429_pauses_that_api_for_sixty_seconds_and_no_other(fake, token_file):
    """Mutation target 19: the length of the pause."""
    assert PAUSE_S == 60.0
    reader, clock = make(fake, token_file)

    async def go():
        await reader.book(["positions", "holdings"])
        fake.status["positions"] = 429
        clock.advance(10)
        slow = await reader.book(["positions"])
        assert fake.count("positions") == 2
        fake.status["positions"] = 200
        clock.advance(59)  # 59 seconds after the 429: still resting, nothing is sent
        resting = await reader.book(["positions"])
        assert fake.count("positions") == 2
        other = await reader.book(["holdings"])
        assert fake.count("holdings") == 2  # another API is not paused
        clock.advance(2.5)
        back = await reader.book(["positions"])
        await reader.aclose()
        return slow, resting, other, back

    slow, resting, other, back = run(go())
    assert slow["positions"]["state"] == "stale"
    assert slow["positions"]["note"] == (
        "Upstox asked SUDARSHAN to slow down. Figures from 11:00:00. They refresh in a minute."
    )
    assert resting["positions"]["state"] == "stale" and other["holdings"]["state"] == "ok"
    assert back["positions"]["state"] == "ok" and fake.count("positions") == 3


def test_upstoxs_own_rate_code_pauses_too(fake, token_file):
    fake.status["holdings"] = 400
    fake.raw["holdings"] = b'{"status":"error","errors":[{"errorCode":"UDAPI10005"}]}'
    reader, clock = make(fake, token_file)

    async def go():
        first = await reader.book(["holdings"])
        clock.advance(20)
        await reader.book(["holdings"])
        await reader.aclose()
        return first

    first = run(go())
    assert "slow down" in first["holdings"]["note"] and fake.count("holdings") == 1


# ---- 401, silence, the token ----------------------------------------------------------------------
def test_a_401_pauses_reads_until_the_token_file_changes(fake, token_file, caplog):
    fake.status["holdings"] = 401
    reader, clock = make(fake, token_file)

    async def go():
        with caplog.at_level(logging.DEBUG):
            first = await reader.book(["holdings"])
            clock.advance(30)
            await reader.book(["holdings", "positions"])
            assert fake.count() == 1  # nothing sent: the token is refused
            token_file.write_text(TOKEN + "-new\n")
            os.utime(token_file, ns=(1, 1))
            fake.status["holdings"] = 200
            clock.advance(30)
            again = await reader.book(["holdings"])
        await reader.aclose()
        return first, again

    first, again = run(go())
    assert first["holdings"]["note"].startswith("Upstox did not accept SUDARSHAN's read-only token")
    assert "do not create a new one" in first["holdings"]["note"]
    assert again["holdings"]["state"] == "ok"
    assert TOKEN not in caplog.text and "Bearer" not in caplog.text


def test_a_missing_token_says_so_and_asks_nothing(fake, tmp_path):
    reader, _ = make(fake, tmp_path / "none")
    held = run(once(reader, ["holdings"]))["holdings"]
    assert held["state"] == "unavailable" and "cannot find its Upstox token" in held["note"]
    assert fake.count() == 0


@pytest.mark.parametrize("status", [500, 502, 503])
def test_a_server_fault_keeps_the_last_figures(fake, token_file, status):
    reader, clock = make(fake, token_file)

    async def go():
        await reader.book(["mtf", "holdings"])
        fake.status["holdings"] = status
        clock.advance(10)
        shown = await reader.book(["holdings"])
        await reader.aclose()
        return shown

    shown = run(go())["holdings"]
    assert shown["state"] == "stale" and len(shown["data"]) == 2
    assert shown["note"] == "Upstox did not answer. Figures from 11:00:00."


def test_a_slow_upstox_is_not_waited_for_forever(fake, token_file):
    fake.delay = 1.0
    short = httpx.AsyncClient(timeout=0.2)
    reader, _ = make(fake, token_file, client=short)
    held = run(reader.book(["holdings"]))["holdings"]
    assert held["state"] == "unavailable" and held["note"] == "Upstox did not answer."
    run(short.aclose())


def test_a_failed_read_is_not_repeated_within_two_seconds(fake, token_file):
    fake.status["holdings"] = 503
    reader, clock = make(fake, token_file)

    async def go():
        await reader.book(["holdings"])
        clock.advance(1.5)
        await reader.book(["holdings"])
        clock.advance(1.0)
        await reader.book(["holdings"])
        await reader.aclose()

    run(go())
    assert fake.count("holdings") == 2


def test_the_service_budget_holds_a_part_back_and_says_so(fake, token_file):
    reader, clock = make(fake, token_file)

    async def go():
        shown = []
        for _ in range(3):  # two a second: the third in the same second waits
            shown.append((await reader.book(["positions"], 0))["positions"])
            clock.advance(0.01)
        await reader.aclose()
        return shown

    first, second, third = run(go())
    assert fake.count("positions") == 2
    assert first["state"] == second["state"] == "ok"
    assert third["state"] == "stale" and third["data"] == second["data"]
    assert third["note"] == (
        "SUDARSHAN is reading Upstox as often as it allows. "
        "Figures from 11:00:00. They refresh in a moment."
    )


# ---- the master contract and the auction fields -------------------------------------------------------
class Prices:
    def auction_for(self, symbol, exchange, now):
        return (True, 1, 2, 3) if symbol == "TCS" else (False, None, None, None)


def test_rows_get_their_symbol_exchange_and_auction_fields(fake, token_file):
    reader, _ = make(fake, token_file, decorate=matcher(FakeResolver(), Prices()))
    tcs, small = run(once(reader, ["holdings"]))["holdings"]["data"]
    assert (tcs["exchange"], tcs["symbol"]) == ("NSE", "TCS")
    assert (tcs["cas"], tcs["auction_from"], tcs["auction_until"], tcs["auction_close"]) == (
        True, 1, 2, 3,
    )  # fmt: skip
    assert (small["exchange"], small["symbol"], small["cas"]) == (None, None, False)


def test_a_master_contract_that_fails_leaves_the_figures_alone(fake, token_file):
    def broken(rows, now):
        raise RuntimeError("no master contract")

    reader, _ = make(fake, token_file, decorate=broken)
    held = run(once(reader, ["holdings"]))["holdings"]
    assert held["state"] == "ok" and held["data"][0]["qty_u"] == 10_000_000


def test_the_reader_wording_has_no_codes_or_protocol_terms():
    for words in up.WORDS.values():
        assert not any(w in words for w in ("401", "429", "423", "HTTP", "5xx", "token file"))
