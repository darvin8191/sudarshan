"""P3 (cross-design Y5, Y41): the orders and trades parts of the Upstox reader, the owner's
reserve in the budget and the start-up probe. The fixtures are typed from Upstox's documented
field lists, never captured."""

from __future__ import annotations

import asyncio
import copy
import logging
from datetime import datetime

import pytest
from portfolio_fakes import DAY_TRADES, HOLDINGS, ORDERS, FakeUpstox

from sudarshan.autologin.schedule import IST
from sudarshan.portfolio import probe
from sudarshan.portfolio import upstox as up
from sudarshan.portfolio.budget import Budget
from sudarshan.portfolio.core import kinds
from sudarshan.portfolio.errors import ApiError
from sudarshan.portfolio.ops import upstox_ops
from sudarshan.portfolio.upstox import UpstoxReader

TOKEN = "synthetic-token-0123456789abcdef"  # never a real token
FAMILY = "viewer:7"


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


def make(fake, token_file, at=ist(11, 0), limits=None):
    clock = Clock(at)
    budget = Budget(**({"limits": limits} if limits else {}), clock=lambda: clock.mono)
    return UpstoxReader(token_file, fake.url, clock=clock, budget=budget), clock


def run(coro):
    return asyncio.run(coro)


async def once(reader, parts, age=None, principal="owner"):
    try:
        return await reader.book(list(parts), age, principal)
    finally:
        await reader.aclose()


# ---- the contract ----------------------------------------------------------------------------
def test_the_parts_list_orders_and_trades_and_the_page_keeps_its_four():
    assert kinds.UPSTOX_PARTS == ("holdings", "positions", "mtf", "funds", "orders", "trades")
    assert kinds.UPSTOX_PAGE_PARTS == ("holdings", "positions", "mtf", "funds")
    assert set(up.PATHS) == set(kinds.UPSTOX_PARTS)
    assert up.PATHS["orders"][0] == "/v2/order/retrieve-all"
    assert up.PATHS["trades"][0] == "/v2/order/trades/get-trades-for-day"


def test_the_portfolio_page_does_not_read_orders_or_trades():
    assert upstox_ops._parts({}) == list(kinds.UPSTOX_PAGE_PARTS)
    for asked in (["orders"], ["trades"], ["holdings", "orders"]):
        with pytest.raises(ApiError):
            upstox_ops._parts({"parts": asked})


# ---- parsing ---------------------------------------------------------------------------------
def test_orders_become_integers_with_the_times_in_epoch_milliseconds(fake, token_file):
    reader, _ = make(fake, token_file)
    part = run(once(reader, ["orders"]))["orders"]
    assert part["state"] == "ok" and part["read_at"] == ist(11) and part["note"] is None
    done, waiting = part["data"]
    assert done["order_id"] == "261026000000001" and done["transaction_type"] == "BUY"
    assert done["qty_u"] == 10_000_000 and done["filled_qty_u"] == 10_000_000
    assert done["price_u"] == 35_005_500 and done["avg_price_u"] == 34_999_950
    assert done["placed_at"] == ist(9, 16, 2) and done["exchange_at"] == ist(9, 16, 3)
    assert "placed_by" not in done and "guid" not in done  # the account's id never leaves
    assert waiting["status"] == "trigger pending" and waiting["trigger_price_u"] == 241_005_000
    assert waiting["pending_qty_u"] == 65_000_000 and waiting["exchange_at"] is None
    for row in part["data"]:
        for key, value in row.items():
            assert not isinstance(value, float), key


def test_trades_carry_an_exact_amount_in_paise(fake, token_file):
    reader, _ = make(fake, token_file)
    part = run(once(reader, ["trades"]))["trades"]
    first, second = part["data"]
    assert first["trade_id"] == "T-1" and first["order_id"] == "261026000000001"
    # 4 x 3499.995 = 13999.98 rupees; 6 x 3500.00 = 21000.00
    assert first["amount_paise"] == 1_399_998 and second["amount_paise"] == 2_100_000
    assert first["price_u"] == 34_999_950 and first["qty_u"] == 4_000_000
    assert part["read_at"] == ist(11)


def test_no_orders_is_an_empty_list_and_a_malformed_row_is_left_out(fake, token_file, caplog):
    fake.bodies["day_orders"] = {"status": "success", "data": None}
    reader, _ = make(fake, token_file)
    assert run(once(reader, ["orders"]))["orders"]["data"] == []
    broken = copy.deepcopy(ORDERS)
    del broken["data"][0]["order_id"]
    fake.bodies["day_orders"] = broken
    reader, _ = make(fake, token_file)
    with caplog.at_level(logging.WARNING):
        data = run(once(reader, ["orders"]))["orders"]["data"]
    assert [r["order_id"] for r in data] == ["261026000000002"]
    assert "1 rows of the orders answer were not readable" in caplog.text


def test_an_answer_that_is_not_the_shape_is_unreadable_never_zeros(fake, token_file):
    fake.bodies["day_trades"] = {"status": "success", "data": {"not": "a list"}}
    reader, _ = make(fake, token_file)
    part = run(once(reader, ["trades"]))["trades"]
    assert part["state"] == "unavailable" and part["data"] is None


# ---- single flight, memory and one bucket each ------------------------------------------------
def test_a_family_read_and_an_owner_read_within_five_seconds_cost_one_call(fake, token_file):
    reader, clock = make(fake, token_file)

    async def go():
        first, second = await asyncio.gather(
            reader.book(["trades"], None, FAMILY), reader.book(["trades"], None, "owner")
        )
        clock.advance(4)
        third = await reader.book(["trades"], None, "sudarshan-journal")
        await reader.aclose()
        return first, second, third

    first, second, third = run(go())
    assert fake.count("day_trades") == 1
    assert first == second == third


def test_each_part_has_its_own_bucket(fake, token_file):
    reader, _ = make(fake, token_file)

    async def go():
        for _ in range(3):  # 2 a second: the third of a burst finds the bucket empty
            await reader.book(["orders"], 0)
        return await reader.book(["trades", "positions"], 0)

    book = run(go())
    assert book["trades"]["state"] == "ok" and book["positions"]["state"] == "ok"
    assert fake.count("day_orders") == 2
    assert reader.budget._of("orders") is not reader.budget._of("day_trades")


# ---- Y5: the owner's reserve -----------------------------------------------------------------
def test_a_principal_other_than_the_owner_leaves_the_reserve():
    clock = [0.0]
    budget = Budget(limits=((50, 1.0), (30, 60.0), (200, 1800.0)), clock=lambda: clock[0])
    spent = 0
    while budget.take("orders", FAMILY):
        spent += 1
        assert spent < 500
    assert spent == 21  # the minute bucket: 30 tokens; a read at 10 or more goes, so 9 are left
    assert budget.take("orders")  # the owner still reads
    clock[0] += 60  # the minute refills; the 30-minute bucket is lower now
    assert budget._of("orders")[2].tokens < 200


def test_the_thirty_minute_bucket_keeps_its_hundred():
    clock = [0.0]
    budget = Budget(limits=((500, 1.0), (500, 60.0), (200, 1800.0)), clock=lambda: clock[0])
    family = 0
    while budget.take("trades", "sudarshan-journal"):
        family += 1
        assert family < 500
    assert family == 101  # tokens fall to 99: below 100 the next is refused
    assert budget._of("trades")[2].tokens == pytest.approx(99.0, abs=0.1)
    assert budget.take("trades")  # the owner may take the rest
    assert budget.can_take("trades", "owner") and not budget.can_take("trades", FAMILY)


def test_the_boundary_is_at_least_not_more_than():
    clock = [0.0]
    budget = Budget(limits=((50, 1.0), (500, 60.0), (200, 1800.0)), clock=lambda: clock[0])
    bucket = budget._of("orders")[2]
    bucket.tokens = 100.0
    assert budget.can_take("orders", FAMILY)
    bucket.tokens = 99.0
    assert not budget.can_take("orders", FAMILY)
    minute = budget._of("orders")[1]
    bucket.tokens, minute.tokens = 150.0, 10.0
    assert budget.can_take("orders", FAMILY)
    minute.tokens = 9.0
    assert not budget.can_take("orders", FAMILY)
    assert budget.can_take("orders", "owner")


def test_a_nudge_storm_with_a_family_screen_open_leaves_every_owner_resync_answered(
    fake, token_file
):
    """Five minutes of nudges, harder than the design says (a family read every second, 300 in
    all) with the owner's own 45 s re-sync; without the reserve the bucket is empty by the end."""
    reader, clock = make(fake, token_file)
    owner_states = []

    async def go():
        for second in range(300):
            clock.advance(1)
            await reader.book(["positions"], 0, FAMILY)
            if second % 45 == 44:
                owner_states.append((await reader.book(["positions"], 0, "owner"))["positions"])
        await reader.aclose()

    run(go())
    assert len(owner_states) >= 6
    assert all(p["state"] == "ok" and p["note"] is None for p in owner_states), [
        p["state"] for p in owner_states
    ]
    assert fake.count("positions") <= 200  # the whole 30 minute cap is never passed


def test_a_read_the_reserve_refuses_does_not_tell_the_owner_it_failed(fake, token_file):
    reader, clock = make(fake, token_file)
    reader.budget._of("day_trades")[2].tokens = 50.0  # below the reserve: the family is held back

    async def go():
        held = (await reader.book(["trades"], 0, FAMILY))["trades"]
        owner = (await reader.book(["trades"], 0, "owner"))["trades"]  # no 2 s failed reuse
        await reader.aclose()
        return held, owner

    held, owner = run(go())
    assert held["state"] == "unavailable" and held["data"] is None
    assert "keeping its Upstox reads for the account owner" in held["note"]
    assert owner["state"] == "ok" and len(owner["data"]) == 2
    assert fake.count("day_trades") == 1


def test_a_family_read_the_per_second_bucket_refuses_never_marks_the_owners_figures(
    fake, token_file
):
    """M4: the per-second bucket is the owner's too. A family read that finds it empty is held
    back; it never sets the shared memory stale ("busy") for the owner's next two seconds."""
    reader, clock = make(fake, token_file)

    async def go():
        await reader.book(["holdings"], 0)  # the owner read it at 11:00:00
        clock.advance(6)  # older than the five seconds of memory
        second = reader.budget._of("holdings")[0]
        second.tokens, second.at = 0.0, clock.mono  # this second's two reads are spent
        held = (await reader.book(["holdings"], 5, FAMILY))["holdings"]
        second.tokens = 2.0  # the next second
        owner = (await reader.book(["holdings"], 5, "owner"))["holdings"]
        await reader.aclose()
        return held, owner

    held, owner = run(go())
    assert held["state"] == "stale" and held["read_at"] == ist(11)
    assert "keeping its Upstox reads for the account owner" in held["note"]
    assert owner["state"] == "ok" and owner["note"] is None and owner["read_at"] == ist(11, 0, 6)
    assert fake.count("holdings") == 2


def test_a_held_read_answers_the_figures_it_has_as_old(fake, token_file):
    reader, clock = make(fake, token_file)

    async def go():
        await reader.book(["orders"], 0)  # the owner read it
        clock.advance(30)
        reader.budget._of("orders")[2].tokens = 10.0
        held = (await reader.book(["orders"], 5, FAMILY))["orders"]
        await reader.aclose()
        return held

    held = run(go())
    assert held["state"] == "stale" and len(held["data"]) == 2 and held["read_at"] == ist(11)
    assert "Figures from 11:00:00" in held["note"]


# ---- Y41: the probe --------------------------------------------------------------------------
def test_the_probe_logs_three_statuses_and_nothing_from_a_body(fake, token_file, caplog):
    fake.status["day_orders"] = 403
    fake.raw["day_orders"] = b'{"errors":[{"message":"SECRET-BODY-TEXT"}]}'
    fake.status["order_history"] = 400
    reader, _ = make(fake, token_file)
    with caplog.at_level(logging.INFO, logger="sudarshan.portfolio.probe"):
        found = run(_probe(reader))
    assert found == {"orders": 403, "trades": 200, "order_history": 400}
    assert (
        "Analytics Token probe: retrieve-all 403, get-trades-for-day 200, order history 400"
        in caplog.text
    )
    assert "SECRET-BODY-TEXT" not in caplog.text and TOKEN not in caplog.text
    assert fake.queries[-1][1] == {"order_id": "0"}


async def _probe(reader):
    try:
        return await probe.probe(reader)
    finally:
        await reader.aclose()


def test_the_probe_without_a_token_says_so_and_asks_nothing(fake, tmp_path, caplog):
    reader, _ = make(fake, tmp_path / "missing")
    with caplog.at_level(logging.INFO, logger="sudarshan.portfolio.probe"):
        found = run(_probe(reader))
    assert set(found.values()) == {"no token"} and fake.count() == 0


class ProbeService:
    """What the probe's job and the token check read of the service: its state folder, its
    clock, the reader (told to report its holdings and positions answers, as the service does)
    and the check's last result."""

    def __init__(self, folder, reader, clock):
        self.settings = type("Settings", (), {"state_dir": folder})()
        self.clock, self._reader = clock, reader
        self.token_check = None
        reader.on_verdict = lambda state, at, sig: probe.heard(self, state, at, sig)

    def ensure_upstox(self):
        return self._reader


def test_the_probe_asks_upstox_at_most_once_a_day(fake, token_file, tmp_path, caplog):
    """L7: three Upstox calls at the first start of a day, none at a restart the same day."""
    reader, clock = make(fake, token_file)
    service = ProbeService(tmp_path, reader, clock)

    async def go():
        found = [await probe.at_start(service, 0), await probe.at_start(service, 0)]
        clock.advance(24 * 3600)  # the next day
        found.append(await probe.at_start(service, 0))
        await reader.aclose()
        return found

    with caplog.at_level(logging.INFO, logger="sudarshan.portfolio.probe"):
        first, again, next_day = run(go())
    assert first == {"orders": 200, "trades": 200, "order_history": 200} and again is None
    assert next_day == first
    assert (fake.count("day_orders"), fake.count("day_trades"), fake.count("order_history")) == (
        2,
        2,
        2,
    )
    assert caplog.text.count("Analytics Token probe:") == 2
    assert (tmp_path / probe.DAY_FILE).read_text().strip() == "20261027"
    # v0.16.0 review H1: what the probe found is a line in the log, never the token check.
    assert service.token_check is None and not (tmp_path / probe.RESULT_FILE).exists()


def test_a_probe_upstox_did_not_answer_is_tried_again_at_the_next_start(fake, tmp_path):
    folder = tmp_path / "state"
    folder.mkdir()
    reader, clock = make(fake, tmp_path / "missing-token")
    service = ProbeService(folder, reader, clock)

    async def go():
        first = await probe.at_start(service, 0)
        (tmp_path / "missing-token").write_text(TOKEN + "\n")  # the token arrives
        second = await probe.at_start(service, 0)
        await reader.aclose()
        return first, second

    first, second = run(go())
    assert set(first.values()) == {"no token"}
    assert second == {"orders": 200, "trades": 200, "order_history": 200}
    assert fake.count("day_orders") == 1
    assert probe.view(service.token_check, tmp_path / "missing-token") == {
        "state": "unknown", "at": None,
    }  # fmt: skip


def test_a_refused_token_in_the_probe_does_not_pause_the_tabs_reads(fake, token_file):
    for name in ("day_orders", "day_trades", "order_history"):
        fake.status[name] = 401
    reader, _ = make(fake, token_file)

    async def go():
        await probe.probe(reader)
        book = await reader.book(["positions"], 0)
        await reader.aclose()
        return book

    assert run(go())["positions"]["state"] == "ok"


# ---- the token check (M13.12, M19.5; v0.16.0 review H1) ------------------------------------------
def _checked(service, token_file):
    return probe.view(service.token_check, token_file)


def _reads(reader, *batches):
    """Each batch of parts read in turn, every one asking Upstox (max age 0)."""

    async def go():
        try:
            return [await reader.book(list(parts), 0) for parts in batches]
        finally:
            await reader.aclose()

    return run(go())


def _start(service, reader, times=1):
    async def go():
        try:
            return [await probe.at_start(service, 0) for _ in range(times)]
        finally:
            await reader.aclose()

    return run(go())


def test_order_endpoints_that_refuse_the_token_never_make_the_check_failed(
    fake, token_file, tmp_path, caplog
):
    """The review's case: Upstox answers 401 at all three order endpoints (say this token may
    not read them) while the holdings read as ever. The probe logs its three numbers and
    decides nothing; the check is the holdings read's: accepted. No alarm, no failing line."""
    for name in ("day_orders", "day_trades", "order_history"):
        fake.status[name] = 401
    reader, clock = make(fake, token_file)
    service = ProbeService(tmp_path, reader, clock)

    async def go():
        try:
            found = await probe.at_start(service, 0)
            return found, await reader.book(["holdings"], 0)
        finally:
            await reader.aclose()

    with caplog.at_level(logging.INFO, logger="sudarshan.portfolio.probe"):
        found, book = run(go())
    assert found == {"orders": 401, "trades": 401, "order_history": 401}
    assert "retrieve-all 401, get-trades-for-day 401, order history 401" in caplog.text
    assert book["holdings"]["state"] == "ok"
    assert _checked(service, token_file) == {"state": "ok", "at": ist(11) // 1000}


@pytest.mark.parametrize("part", up.JUDGES)
def test_a_holdings_or_positions_read_upstox_refused_is_failed(fake, token_file, tmp_path, part):
    assert up.JUDGES == ("holdings", "positions")
    fake.status[part] = 401
    reader, clock = make(fake, token_file)
    service = ProbeService(tmp_path, reader, clock)
    (book,) = _reads(reader, [part])
    assert book[part]["note"] == up.WORDS["token_refused"]
    assert _checked(service, token_file) == {"state": "failed", "at": ist(11) // 1000}


@pytest.mark.parametrize(
    ("part", "path"),
    [("mtf", "mtf"), ("funds", "funds"), ("orders", "day_orders"), ("trades", "day_trades")],
)
def test_no_other_read_decides_the_check(fake, token_file, tmp_path, part, path):
    fake.status[path] = 401
    reader, clock = make(fake, token_file)
    service = ProbeService(tmp_path, reader, clock)
    (book,) = _reads(reader, [part])
    assert book[part]["state"] == "unavailable"
    assert service.token_check is None and _checked(service, token_file)["state"] == "unknown"
    assert not (tmp_path / probe.RESULT_FILE).exists()


def test_a_read_refused_alone_pauses_that_read_and_nothing_else(fake, token_file, tmp_path):
    """v0.17.0 verification V1. The day's trades refused (the journal's 22:00 read, say): that
    read pauses, in words that do not say the token was refused, and every other part still
    asks: the Upstox tab's four, the day's orders. The check hears holdings' own answer."""
    fake.status["day_trades"] = 401
    reader, clock = make(fake, token_file)
    service = ProbeService(tmp_path, reader, clock)
    every = [*kinds.UPSTOX_PAGE_PARTS, "orders"]
    trades, tab, again = _reads(reader, ["trades"], every, ["trades"])
    assert trades["trades"]["note"] == up.WORDS["not_allowed_trades"]
    assert "did not accept" not in trades["trades"]["note"]
    assert {part: tab[part]["state"] for part in every} == dict.fromkeys(every, "ok")
    assert again["trades"]["note"] == up.WORDS["not_allowed_trades"]
    assert fake.count("day_trades") == 1  # the refused read asks nothing more
    asked = [fake.count(p) for p in ("holdings", "positions", "mtf", "funds", "day_orders")]
    assert asked == [1, 1, 1, 1, 1]
    assert _checked(service, token_file) == {"state": "ok", "at": ist(11) // 1000}


def test_a_dead_token_another_read_saw_first_is_failed_at_the_next_holdings_read(
    fake, token_file, tmp_path
):
    for path in ("day_trades", "holdings", "positions"):
        fake.status[path] = 401
    reader, clock = make(fake, token_file)
    service = ProbeService(tmp_path, reader, clock)

    async def go():
        try:
            await reader.book(["trades"], 0)
            assert service.token_check is None  # the trades refusal decides nothing
            await reader.book(["holdings"], 0)
            asked = fake.count()
            clock.advance(60)
            return asked, await reader.book(["holdings", "positions"], 0)
        finally:
            await reader.aclose()

    asked, again = run(go())
    assert asked == fake.count() == 2  # once refused itself, holdings and positions pause too
    assert again["positions"]["note"] == up.WORDS["token_refused"]
    assert _checked(service, token_file) == {"state": "failed", "at": ist(11) // 1000}


@pytest.mark.parametrize("refused_first", [True, False], ids=["401 lands first", "200 first"])
def test_holdings_answered_and_positions_refused_in_one_batch_leave_the_line_failing(
    token_file, tmp_path, refused_first
):
    """v0.17.0 verification V3: the tab reads both at once and the token is ended between the
    two requests. Whichever answer lands last, an "ok" never replaces the "failed" of the same
    token file: the line fails while the tab says the token was not accepted."""
    import httpx

    clock = Clock(ist(11))
    order: list[str] = []
    landed = {"holdings": asyncio.Event(), "positions": asyncio.Event()}

    async def answer(request):
        part = "holdings" if request.url.path.endswith("long-term-holdings") else "positions"
        other = landed["positions" if part == "holdings" else "holdings"]
        if (part == "holdings") == refused_first:
            await other.wait()  # this one is answered after the other has been taken
        order.append(part)
        if part == "holdings":
            return httpx.Response(200, json=HOLDINGS)
        return httpx.Response(401, json={"status": "error", "errors": []})

    async def go():
        client = httpx.AsyncClient(transport=httpx.MockTransport(answer))
        reader = UpstoxReader(token_file, "http://upstox.test", clock=clock, client=client,
                              budget=Budget(clock=lambda: clock.mono))  # fmt: skip
        service = ProbeService(tmp_path, reader, clock)
        told = reader.on_verdict

        def heard(state, at, sig):
            told(state, at, sig)
            landed["holdings" if state == "ok" else "positions"].set()

        reader.on_verdict = heard
        try:
            book = await reader.book(["holdings", "positions"], 0)
        finally:
            await client.aclose()
        return service, book

    service, book = run(go())
    assert order == (["positions", "holdings"] if refused_first else ["holdings", "positions"])
    assert book["holdings"]["state"] == "ok"  # what Upstox did answer is shown
    assert book["positions"]["note"] == up.WORDS["token_refused"]
    assert _checked(service, token_file) == {"state": "failed", "at": ist(11) // 1000}


def test_the_check_asks_upstox_nothing_of_its_own(fake, token_file, tmp_path):
    """GA5.1's rule holds: no call is made for the check. The tab's four parts cost four calls,
    and a restart the same day shows the kept result and asks nothing more."""
    reader, clock = make(fake, token_file)
    ProbeService(tmp_path, reader, clock)
    _reads(reader, kinds.UPSTOX_PAGE_PARTS)
    assert fake.count() == 4
    probe.note_probed(tmp_path, 20261026)  # today's probe has run
    again_reader, later = make(fake, token_file, at=ist(15, 30))
    again = ProbeService(tmp_path, again_reader, later)
    assert _start(again, again_reader) == [None]
    assert fake.count() == 4
    assert _checked(again, token_file) == {"state": "ok", "at": ist(11) // 1000}


def test_the_check_is_kept_as_a_state_and_a_time_and_never_the_token(fake, token_file, tmp_path):
    reader, clock = make(fake, token_file)
    ProbeService(tmp_path, reader, clock)
    _reads(reader, ["holdings"])
    path = tmp_path / probe.RESULT_FILE
    assert TOKEN not in path.read_text() and "200" not in path.read_text()
    assert set(probe.read_result(tmp_path)) == {"at", "state", "sig"}
    assert path.stat().st_mode & 0o777 == 0o600


def test_the_result_is_written_when_it_says_something_new_and_once_a_day(
    fake, token_file, tmp_path
):
    reader, clock = make(fake, token_file)
    service = ProbeService(tmp_path, reader, clock)

    async def go():
        seen = []
        try:
            for advance, part in ((0, "holdings"), (600, "holdings"), (86_400, "positions")):
                clock.advance(advance)
                await reader.book([part], 0)
                seen.append((probe.read_result(tmp_path)["at"], service.token_check["at"]))
            fake.status["holdings"] = 401
            clock.advance(60)
            await reader.book(["holdings"], 0)
            seen.append((probe.read_result(tmp_path)["at"], service.token_check["at"]))
            return seen, probe.read_result(tmp_path)["state"]
        finally:
            await reader.aclose()

    seen, state = run(go())
    first, later, next_day, refused = (ist(11), ist(11, 10), ist(11, 10, day=27),
                                       ist(11, 11, day=27))  # fmt: skip
    assert seen == [
        (first // 1000, first // 1000),
        (first // 1000, later // 1000),  # shown at once, the file left as it was that day
        (next_day // 1000, next_day // 1000),
        (refused // 1000, refused // 1000),
    ]
    assert state == "failed"


def test_a_token_file_changed_after_the_read_is_not_the_token_that_was_judged(
    fake, token_file, tmp_path
):
    """A refused token the owner then replaces must not stay "failed", and a new token is not
    "ok" on the old one's word: unknown until the next holdings or positions read, which asks."""
    fake.status["holdings"] = 401
    reader, clock = make(fake, token_file)
    service = ProbeService(tmp_path, reader, clock)

    async def go():
        try:
            await reader.book(["holdings"], 0)
            failed = _checked(service, token_file)
            token_file.write_text(TOKEN + "-renewed\n")
            changed = _checked(service, token_file)
            fake.status["holdings"] = 200
            clock.advance(60)
            await reader.book(["holdings"], 0)
            return failed, changed, _checked(service, token_file)
        finally:
            await reader.aclose()

    failed, changed, renewed = run(go())
    assert failed == {"state": "failed", "at": ist(11) // 1000}
    assert changed == {"state": "unknown", "at": ist(11) // 1000, "changed": True}
    assert renewed == {"state": "ok", "at": ist(11, 1) // 1000}
    token_file.unlink()
    assert _checked(service, token_file)["state"] == "unknown"


@pytest.mark.parametrize(
    "kept",
    ["", "{", '{"at":17', '{"at":1,"state":"fine","sig":[1,2]}', '{"at":"x","state":"ok"}',
     '{"at":5,"state":"ok","sig":[1]}', "[]", '{"at":true,"state":"ok","sig":null}'],
)  # fmt: skip
def test_a_result_file_cut_off_or_misshapen_is_no_result(tmp_path, kept):
    (tmp_path / probe.RESULT_FILE).write_text(kept)
    assert probe.read_result(tmp_path) is None


def test_the_service_health_carries_the_check_its_reader_heard(tmp_path, token_file, fake):
    from types import SimpleNamespace

    from sudarshan.portfolio import config
    from sudarshan.portfolio.service import Service

    settings = config.Settings(
        enabled=False, state_dir=tmp_path, token_file=token_file, upstox_url=fake.url
    )
    service = Service(settings, None, jobs={})
    service.clock = lambda: ist(11)
    service.prices = SimpleNamespace()  # no price book: the rows are matched to nothing
    shown = lambda: service.health()["portfolio_state"]["analytics_token"]  # noqa: E731
    assert shown() == {"state": "unknown", "at": None}
    reader = service.ensure_upstox()

    async def go():
        try:
            await reader.book(["holdings"], 0)
        finally:
            await reader.aclose()

    run(go())
    assert shown() == {"state": "ok", "at": ist(11) // 1000}
    assert probe.read_result(tmp_path)["state"] == "ok"
    assert TOKEN not in str(service.health())
