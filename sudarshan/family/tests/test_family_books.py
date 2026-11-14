"""F5 (M2.8, M6.7): the privacy masks applied again on the way out, the portfolio client under the
family purpose, the books (when they read, what they keep, what the phone gets) and the nudges.

Design 7.2 "Privacy" and "Data plane", 7.5 targets 7, 8, 11, 12 and 13, cross-design Y5, Y6, Y11,
Y24 and Y44. The portfolio is a fake that speaks the real sealed transport on an ephemeral port;
the clock and the calendar are fakes, so the session rules are judged at exact instants.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import socket
import time
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from family_net import ask, fast_hasher, keys, running, sealed, settings
from family_planekit import (
    CHILD,
    FAMILY_OPS,
    MONDAY,
    VIEWER,
    Clock,
    FakePortfolio,
    Sock,
    Weekdays,
    book_view,
    books_answer,
    holding,
    ist,
    ok,
    position,
    signed_in,
)
from family_testkit import close
from websockets.asyncio.client import connect

from sudarshan.family import books as books_module
from sudarshan.family import privacy, viewer_api
from sudarshan.family.books import Books, Market, Memory, PlaneKeys, words_for
from sudarshan.family.contract import CATEGORIES, PRIVACY_DEFAULTS
from sudarshan.family.nudge import Nudges
from sudarshan.family.portfolio_client import NOT_SHARED, READ_OPS, PortfolioClient, Refusal
from sudarshan.family.service import FamilyService

EVERY_CHOICE = [dict(zip(CATEGORIES, bits, strict=True))
                for bits in itertools.product((True, False), repeat=4)]  # fmt: skip
#: Every figure field of the portfolio's Upstox rows and the one category that hides it alone.
#: Written out here, not read from privacy_fields.json, so a name dropped from the table turns
#: these red (design 7.5 target 7).
QTY_FIELDS = ("qty_u", "overnight_qty_u", "day_buy_qty_u", "day_sell_qty_u", "t1_qty_u",
              "avg_price_u", "buy_price_u", "sell_price_u", "value_paise", "price_u",
              "trigger_price_u", "amount_paise", "filled_qty_u", "pending_qty_u",
              "disclosed_qty_u", "collateral_qty_u", "cnc_used_qty_u")  # fmt: skip
PNL_FIELDS = ("pnl_paise", "realised_paise", "unrealised_paise")
NEVER = ("symbol", "exchange", "name", "product", "instrument_key", "last_price_u",
         "close_price_u", "multiplier", "transaction_type", "status", "side", "order_type",
         "placed_at", "exchange_at", "order_id", "trade_id", "isin")  # fmt: skip


def run(coro):
    try:
        return asyncio.run(coro)
    finally:
        close(None)


# ---- privacy: one table, applied twice (design 3; Y6, Y24) ---------------------------------------
@pytest.mark.parametrize("shown", EVERY_CHOICE)
def test_the_mask_agrees_with_the_portfolios_table_for_every_choice(shown):
    """The second pass removes exactly what the portfolio's first pass removes (one table)."""
    from sudarshan.portfolio import privacy as portfolio_privacy

    assert privacy.mask_of(privacy.hidden_categories(shown)) == portfolio_privacy.floor(shown)
    assert list(privacy.hidden_categories(shown)) == viewer_api.hidden_of(shown)
    assert set(privacy.hidden_categories(shown)) == portfolio_privacy.hidden_categories(shown)


def test_each_role_hides_what_design_3_1_says_by_default():
    assert privacy.hidden_categories(PRIVACY_DEFAULTS["viewer"]) == ("funds",)
    assert privacy.hidden_categories(PRIVACY_DEFAULTS["child"]) == CATEGORIES
    assert privacy.hidden_categories(None) == CATEGORIES  # no choices: no figures
    assert privacy.hidden_categories({"pnl": True, "qty": False, "funds": True, "orders": True}) \
        == ("pnl", "qty")  # Y24: P&L goes with quantities  # fmt: skip
    assert privacy.parts_shown(("funds",)) == ("positions", "holdings", "orders", "trades")
    assert privacy.parts_shown(CATEGORIES) == ("positions", "holdings")


#: Review H1 (FP's ``cost`` list): with P&L hidden and quantities shown, every rupee figure the P&L
#: follows from beside the quantity. Written out here, so a name dropped from the list turns red.
COST_FIELDS = ("avg_price_u", "buy_price_u", "sell_price_u", "price_u", "trigger_price_u",
               "value_paise", "invested_paise", "amount_paise", "charges_paise")  # fmt: skip


def test_pnl_hidden_with_quantities_shown_leaves_nothing_to_work_the_pnl_out_from():
    """H1: the quantity stays, and nothing it could be multiplied by to give the P&L does; the live
    price stays (it is the price, not the owner's cost)."""
    mask = privacy.mask_of(privacy.hidden_categories({"pnl": False, "qty": True, "funds": False,
                                                      "orders": True}))  # fmt: skip
    row = privacy.masked({**position(), "invested_paise": 1, "charges_paise": 2,
                          "price_u": 3, "trigger_price_u": 4, "amount_paise": 5}, mask)  # fmt: skip
    assert row["qty_u"] == 100_000 and row["last_price_u"] == 1_010_000
    assert [f for f in COST_FIELDS if f in row] == []
    assert "pnl_paise" not in row and "realised_paise" not in row

    async def go():  # and through the books' second pass, though the portfolio leaked them
        clock = Clock(ist(MONDAY, 10))
        books, _ = shelf(clock, {"positions": [position()]})
        shown = {"pnl": False, "qty": True, "funds": False, "orders": True}
        books.screen(Sock(), member(shown=shown), "positions")
        await settle(books)
        sent = books.book_of(member(shown=shown), "positions", clock())["rows"][0]
        assert sent["qty_u"] == 100_000 and [f for f in COST_FIELDS if f in sent] == []
        assert sent["side"] == "long"

    run(go())


def test_m4_the_owner_is_told_what_pnl_hidden_leaves_workable_and_the_table_says_the_same():
    """v0.12.0 review M4: with P&L hidden and quantities shown, a holding still carries its
    quantity, its live price and its previous close, so a day's change and its value are one
    multiplication away. The table no longer claims otherwise, and carries the one sentence the
    owner reads under the P&L switch. If a later change hides one of the three, this test fails
    and the sentence goes. Fails on the old code: there was no such sentence."""
    table = json.loads(privacy.FIELDS_FILE.read_text(encoding="utf-8"))
    shown = {"pnl": False, "qty": True, "funds": False, "orders": True}
    row = privacy.masked(holding(), privacy.mask_of(privacy.hidden_categories(shown)))
    day_change = row["qty_u"] * (row["last_price_u"] - row["close_price_u"])
    assert day_change != 0 and "pnl_paise" not in row and "avg_price_u" not in row
    note = privacy.PNL_HIDDEN_NOTE
    assert note == table["pnl_hidden_note"] and "day's change" in note and "quantit" in note
    assert note.endswith("Hide quantities to hide those too.")
    assert "pnl_hidden_note" in table["note"] and "does not hide a day's change" in table["note"]
    hidden = privacy.masked(holding(), privacy.mask_of(privacy.hidden_categories(
        {**shown, "qty": False})))  # fmt: skip
    assert "qty_u" not in hidden  # the remedy the sentence names does hide it


def test_m4_a_table_without_the_note_is_refused(tmp_path):
    table = json.loads(privacy.FIELDS_FILE.read_text(encoding="utf-8"))
    for bad in (None, "", "no full stop", "x" * 201 + "."):
        path = tmp_path / "fields.json"
        path.write_text(json.dumps({**table, "pnl_hidden_note": bad}), encoding="utf-8")
        with pytest.raises(ValueError, match="pnl_hidden_note"):
            privacy.load_note(path)


@pytest.mark.parametrize("field", QTY_FIELDS)
def test_quantities_alone_hide_every_quantity_and_amount(field):
    assert privacy.is_hidden(field, privacy.mask_of(["qty"]))
    assert not privacy.is_hidden(field, privacy.mask_of(["funds", "orders"]))


@pytest.mark.parametrize("field", PNL_FIELDS)
def test_pnl_alone_hides_every_pnl(field):
    assert privacy.is_hidden(field, privacy.mask_of(["pnl"]))


@pytest.mark.parametrize("field", NEVER)
def test_symbols_directions_and_the_live_price_are_never_hidden(field):
    assert not privacy.is_hidden(field, privacy.mask_of(CATEGORIES))


def test_a_masked_field_is_absent_never_zero_or_null_at_any_depth():
    data = {"rows": [{"symbol": "SYNTH1", "qty_u": 0, "pnl_paise": None, "deep": {"qty_u": 5}}]}
    out = privacy.masked(data, privacy.mask_of(["qty", "pnl"]))
    assert out == {"rows": [{"symbol": "SYNTH1", "deep": {}}]}
    assert data["rows"][0]["qty_u"] == 0  # the original is left as it was
    named = frozenset({"last_price_u", "qty_u"})  # the table's never_hidden wins over a mask
    assert privacy.masked({"last_price_u": 1, "qty_u": 2}, named) == {"last_price_u": 1}


def test_the_mask_rides_in_the_seal_sorted_and_bounded():
    mask = privacy.mask_of(CATEGORIES)
    assert privacy.wire(mask) == sorted(mask) and len(mask) <= privacy.WIRE_MAX
    with pytest.raises(ValueError):
        privacy.wire(frozenset(f"f{n}" for n in range(201)))


# ---- the portfolio client: the family purpose, as the member, reads only ---------------------------
def test_the_read_ops_the_principal_and_the_sentence_are_the_portfolios():
    from sudarshan.portfolio import sharing
    from sudarshan.portfolio.core import kinds

    assert READ_OPS == tuple(kinds.FAMILY_OPS) == FAMILY_OPS
    assert kinds.FAMILY_PRINCIPAL_PREFIX == "viewer:"
    assert sharing.NOT_SHARED == NOT_SHARED


def test_reads_are_sealed_for_the_family_purpose_as_the_member_with_the_mask():
    async def go():
        fernet = Fernet(Fernet.generate_key())
        fake = FakePortfolio(fernet, lambda op, m, a: ok({"positions": book_view([], 5)}))
        url = await fake.start()
        try:
            async with httpx.AsyncClient(trust_env=False) as http:
                client = PortfolioClient(url, fernet, http)
                mask = privacy.mask_of(["qty", "pnl"])
                data = await client.call("upstox_book", 7, {"parts": ["positions"]}, mask=mask)
                assert data == {"positions": book_view([], 5)}
                assert fake.calls == [("upstox_book", 7, {"parts": ["positions"],
                                                          "mask": sorted(mask)})]  # fmt: skip
                assert client.reachable is True
                stranger = PortfolioClient(url, Fernet(Fernet.generate_key()), http)
                with pytest.raises(Refusal) as caught:
                    await stranger.call("upstox_book", 7, {"parts": ["positions"]}, mask=mask)
                assert caught.value.state == "unreachable"
                for op in ("tab_create", "share_set", "member_privacy_set", "activity_create"):
                    with pytest.raises(ValueError):
                        await client.call(op, 7, mask=mask)  # never sent
                assert len(fake.calls) == 1
        finally:
            await fake.close()

    run(go())


@pytest.mark.parametrize(
    ("status", "payload", "state", "words"),
    [
        (200, {"status": "success", "data": {"enabled": False, "message": "off"}},
         "portfolio_off", "The portfolio service is switched off on the server."),
        (403, {"status": "error", "code": "forbidden", "message": "Not shared with you."},
         "not_shared", "Not shared with you."),
        (503, {"status": "error", "code": "busy", "message": "busy"}, "unreachable",
         "The account cannot be read right now; prices still move."),
        (500, {"status": "error", "code": "failed", "message": "failed"}, "unreachable",
         "The account cannot be read right now; prices still move."),
    ],
)  # fmt: skip
def test_each_refusal_is_a_read_state_with_a_sentence(status, payload, state, words):
    async def go():
        fernet = Fernet(Fernet.generate_key())
        fake = FakePortfolio(fernet, lambda op, m, a: (status, payload))
        url = await fake.start()
        try:
            async with httpx.AsyncClient(trust_env=False) as http:
                with pytest.raises(Refusal) as caught:
                    await PortfolioClient(url, fernet, http).call("summary", 3, mask=frozenset())
                assert (caught.value.state, str(caught.value)) == (state, words)
        finally:
            await fake.close()

    run(go())


def test_a_portfolio_that_is_not_listening_is_unreachable():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    async def go():
        async with httpx.AsyncClient(trust_env=False) as http:
            client = PortfolioClient(
                f"http://127.0.0.1:{port}", Fernet(Fernet.generate_key()), http
            )
            with pytest.raises(Refusal) as caught:
                await client.call("summary", 3, mask=frozenset())
            assert caught.value.state == "unreachable" and client.reachable is False

    run(go())


# ---- the books: when they read -------------------------------------------------------------------
class FakeClient:
    """The portfolio client as the books use it, without HTTP: every read recorded."""

    def __init__(self, clock, rows=None):
        self.clock, self.rows = clock, rows or {}
        self.calls: list[tuple[str, int, dict, frozenset]] = []
        self.refuse: str | None = None
        self.reachable = True
        #: M3: the portfolio's ``shares_for_viewer`` answer; None answers as an op with no shape.
        self.shares: dict | None = None
        self.shares_down = False

    async def call(self, op, member_id, args=None, *, mask, tabs=None):
        self.calls.append((op, member_id, dict(args or {}), mask))
        if op == "shares_for_viewer" and self.shares_down:
            raise Refusal("unreachable")
        if self.refuse:
            raise Refusal(self.refuse)
        if op == "upstox_book":
            if self.shares is not None and upstox_level(self.shares) is None:
                raise Refusal("not_shared")  # as the portfolio's need_upstox_tab refuses
            return {p: book_view(self.rows.get(p, []), self.clock()) for p in args["parts"]}
        return self.shares if op == "shares_for_viewer" and self.shares is not None else {}

    def ops(self):
        return [c[0] for c in self.calls]

    def asked(self):
        return [c for c in self.calls if c[0] == "shares_for_viewer"]

    def reads(self, part=None):
        return [c for c in self.calls if c[0] == "upstox_book"
                and (part is None or c[2]["parts"] == [part])]  # fmt: skip


def shared_answer(level: str | None = "view", hides=("funds",)) -> dict:
    """The portfolio's ``shares_for_viewer`` answer: the Upstox tab at ``level`` (None: not shared
    with the member), one manual tab beside it, and the categories it hides."""
    tabs = [{"tab_id": 2, "name": "Long term", "kind": "manual", "level": "view",
             "expires_at": None}]  # fmt: skip
    if level is not None:
        tabs.insert(0, {"tab_id": 1, "name": "Upstox", "kind": "upstox", "level": level,
                        "expires_at": None})  # fmt: skip
    return {"tabs": tabs, "hides": list(hides), "member": "1"}


def upstox_level(answer: dict) -> str | None:
    return next((t["level"] for t in answer["tabs"] if t["kind"] == "upstox"), None)


class Open:
    """A market that is always open (the nudge timing tests run on the wall clock)."""

    def open(self, part, now):
        return True

    def last_end(self, part, now):
        return None


def member(n=1, shown=None):
    return {"id": n, "status": "active", "privacy": dict(VIEWER if shown is None else shown)}


def shelf(clock, rows=None, market=None):
    client = FakeClient(clock, rows)
    return Books(client, market or Market(Weekdays()), clock=clock), client


async def settle(books):
    for _ in range(200):
        if not books._flights and not books._share_flights:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("a book read never ended")


def test_nothing_is_read_while_no_screen_shows_the_part():
    """Target 12 (no screen): neither the re-sync nor a nudge reads; the nudge only marks."""

    async def go():
        clock = Clock(ist(MONDAY, 10))
        books, client = shelf(clock)
        nudges = Nudges(books, clock=clock)
        assert books.step() == 0
        assert nudges.nudged("orders", False, clock()) is False
        await asyncio.sleep(0.01)
        assert client.calls == [] and books.dirty_at["positions"] == clock()
        books.screen(Sock(), member(), "watchlist")  # a screen that shows no book part
        assert books.step() == 0 and client.calls == []

    run(go())


def test_a_screen_reads_its_parts_at_once_and_then_every_45_s_or_5_min():
    async def go():
        clock = Clock(ist(MONDAY, 10))
        books, client = shelf(clock)
        books.screen(Sock(), member(), "dashboard")  # positions; funds are hidden for a viewer
        books.screen(Sock(), member(2), "holdings")
        await settle(books)
        assert sorted((c[1], c[2]["parts"][0]) for c in client.reads()) == [
            (1, "positions"), (2, "holdings")]  # fmt: skip
        clock.at += 44_000
        assert books.step() == 0
        clock.at += 1_000
        assert books.step() == 1  # positions at 45 s
        await settle(books)
        clock.at += 254_000  # 299 s after the holdings read
        books.step()
        await settle(books)
        assert len(client.reads("holdings")) == 1
        clock.at += 1_000
        books.step()
        await settle(books)
        assert len(client.reads("holdings")) == 2

    run(go())


def test_outside_the_session_a_screen_reads_once_and_nothing_else_reads():
    """Target 12 (outside the session): after the close neither the re-sync nor a nudge reads; a
    screen that opens then reads once, and once only."""

    async def go():
        clock = Clock(ist(MONDAY, 15, 39, 30))
        books, client = shelf(clock)
        nudges = Nudges(books, clock=clock)
        sock = Sock()
        books.screen(sock, member(), "positions")
        await settle(books)
        clock.at = ist(MONDAY, 15, 40, 20)  # open across the close: read once more after it
        assert books.step() == 1
        await settle(books)
        for minutes in (2, 30, 120):
            clock.at = ist(MONDAY, 15, 40, 20) + minutes * 60_000
            assert books.step() == 0
            assert nudges.nudged("orders", False, clock()) is False
            books.screen(sock, member(), "positions")
        await asyncio.sleep(0.01)
        assert len(client.reads()) == 2
        evening = Clock(ist(MONDAY, 19))
        later, later_client = shelf(evening)
        later.screen(Sock(), member(), "positions")  # nothing kept yet: one read
        await settle(later)
        evening.at += 3_600_000
        assert later.step() == 0 and len(later_client.reads()) == 1

    run(go())


@pytest.mark.parametrize(("hour", "minute", "second", "read"),
                         [(5, 29, 59, False), (5, 30, 0, True), (0, 0, 0, False), (23, 59, 59, True)])  # fmt: skip
def test_funds_are_never_read_from_midnight_to_0530(hour, minute, second, read):
    """Target 13: the edge is 05:30:00 IST exactly."""

    async def go():
        clock = Clock(ist(MONDAY, hour, minute, second))
        books, client = shelf(clock)
        books.screen(Sock(), member(shown={**VIEWER, "funds": True}), "dashboard")
        await settle(books)
        assert bool(client.reads("funds")) is read
        view = books.book_of(member(shown={**VIEWER, "funds": True}), "funds", clock())
        if not read:
            assert view["state"] == "closed"
            assert view["words"] == "Funds are available from 5:30 AM."

    run(go())


def test_a_sandbox_nudge_reads_nothing_and_marks_nothing():
    """Target 11: the books are the real account."""

    async def go():
        clock = Clock(ist(MONDAY, 10))
        books, client = shelf(clock)
        books.screen(Sock(), member(), "positions")
        await settle(books)
        nudges = Nudges(books, clock=clock)
        clock.at += 60_000
        assert nudges.nudged("orders", True, clock()) is False
        assert nudges.nudged("holdings", True, clock()) is False
        await asyncio.sleep(0.05)
        assert len(client.reads()) == 1 and books.dirty_at == {}

    run(go())


def test_nudges_read_at_once_then_at_most_once_a_window_with_the_last_one_followed():
    """Y5: the first nudge reads at once; a burst reads once a window per part; the last nudge
    of a burst is always followed by a read."""

    async def go():
        clock = lambda: int(time.time() * 1000)  # noqa: E731
        books, client = shelf(clock, market=Open())
        books.screen(Sock(), member(), "positions")
        await settle(books)
        nudges = Nudges(books, clock=clock, every_ms=300)
        began = time.monotonic()
        assert nudges.nudged("orders", False, clock()) is True
        await settle(books)
        assert len(client.reads()) == 2  # the screen's read and the nudge's, at once
        for _ in range(5):
            nudges.nudged("orders", False, clock())
            await asyncio.sleep(0.02)
        assert len(client.reads()) == 2  # the burst waits for the end of the window
        await asyncio.sleep(0.45)
        await settle(books)
        assert len(client.reads()) == 3 and time.monotonic() - began >= 0.29
        await nudges.close()

    run(go())


def test_a_nudge_storm_starts_a_read_of_a_part_at_most_once_a_window():
    async def go():
        clock = lambda: int(time.time() * 1000)  # noqa: E731
        books, client = shelf(clock, market=Open())
        books.screen(Sock(), member(), "positions")
        await settle(books)
        starts: list[float] = []
        original = books.read_shown

        def counted(part):
            starts.append(time.monotonic())
            return original(part)

        books.read_shown = counted
        nudges = Nudges(books, clock=clock, every_ms=100)
        until = time.monotonic() + 1.2
        while time.monotonic() < until:
            nudges.nudged("orders", False, clock())
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.2)
        await settle(books)
        gaps = [b - a for a, b in itertools.pairwise(starts)]
        assert 8 <= len(starts) <= 15 and min(gaps) >= 0.09, (len(starts), min(gaps))
        await nudges.close()

    run(go())


def test_each_member_reads_as_themselves_and_every_socket_of_theirs_is_told():
    """Y11: after a read, only that member's sockets showing the part get the book message."""

    async def go():
        clock = Clock(ist(MONDAY, 10))
        books, client = shelf(clock, {"positions": [position()]})
        told = []
        books.after_read.append(lambda m, p, kept: told.append((m, p, kept.read_at)))
        from sudarshan.family.nudge import announce

        books.after_read.append(lambda m, p, kept: announce(books, m, p, kept.read_at))
        a, b, c = Sock(1), Sock(1), Sock(2)
        books.screen(a, member(1), "positions")
        books.screen(b, member(1), "holdings")
        books.screen(c, member(2, CHILD), "positions")
        await settle(books)
        by_member = {(call[1], call[2]["parts"][0]): call[3] for call in client.reads()}
        assert by_member[(1, "positions")] == privacy.mask_of(["funds"])
        assert by_member[(2, "positions")] == privacy.mask_of(CATEGORIES)
        both = [{"type": "book", "part": part, "read_at": clock()}
                for part in ("holdings", "positions")]  # fmt: skip
        # FD2: every socket of the member who may see the part, whatever screen it shows
        assert sorted(a.of("book"), key=str) == sorted(b.of("book"), key=str) == both
        assert c.of("book") == [{"type": "book", "part": "positions", "read_at": clock()}]
        assert (1, "positions") in books.memory and (2, "positions") in books.memory

    run(go())


# ---- the books: what the phone gets ----------------------------------------------------------------
def test_the_second_pass_masks_what_a_stubbed_portfolio_leaks():
    """Target 8: the portfolio sends a child's figures by mistake; none reaches the phone."""

    async def go():
        clock = Clock(ist(MONDAY, 10))
        books, _ = shelf(clock, {"positions": [position()], "holdings": [holding()]})
        child = member(2, CHILD)
        books.screen(Sock(2), child, "positions")
        await settle(books)
        row = books.book_of(child, "positions", clock())["rows"][0]
        assert set(row) == {"instrument_key", "exchange", "symbol", "name", "product",
                            "last_price_u", "close_price_u", "multiplier", "side"}  # fmt: skip
        books.screen(Sock(2), child, "holdings")
        await settle(books)
        held = books.book_of(child, "holdings", clock())["rows"][0]
        assert "qty_u" not in held and "pnl_paise" not in held and held["symbol"] == "SYNTH1"

    run(go())


def test_side_comes_from_the_quantity_and_is_left_out_without_it():
    async def go():
        clock = Clock(ist(MONDAY, 10))
        rows = [position(1, 100_000), position(2, -50_000), position(3, 0), position(4)]
        rows[3].pop("qty_u")  # what the portfolio's floor leaves for a member without quantities
        books, _ = shelf(clock, {"positions": rows})
        books.screen(Sock(), member(), "positions")
        await settle(books)
        got = books.book_of(member(), "positions", clock())["rows"]
        assert [r.get("side") for r in got] == ["long", "short", "closed", None]

    run(go())


def test_the_states_carry_the_design_sentences():
    at = ist(MONDAY, 10, 41, 5)
    assert (
        words_for("positions", "budget", at) == "Figures from 10:41:05. They refresh in a minute."
    )
    assert words_for("positions", "closed", ist(MONDAY, 15, 40, 12)) == (
        "Market closed · figures from 15:40:12")  # fmt: skip
    assert words_for("orders", "unreachable", at) == (
        "The account cannot be read right now; prices still move. Figures from 10:41:05."
    )
    assert words_for("positions", "portfolio_off", None) == (
        "The portfolio service is switched off on the server."
    )
    assert words_for("holdings", "not_shared", None) == "Not shared with you."
    assert words_for("positions", "ok", at) is None
    assert words_for("positions", "closed", None) == "Market closed"


def test_a_failed_read_keeps_the_old_figures_only_while_they_are_still_shared():
    async def go():
        clock = Clock(ist(MONDAY, 10))
        books, client = shelf(clock, {"positions": [position()]})
        books.screen(Sock(), member(), "positions")
        await settle(books)
        first = books.memory[(1, "positions")]
        client.refuse, clock.at = "unreachable", clock.at + 45_000
        books.step()
        await settle(books)
        kept = books.book_of(member(), "positions", clock())
        assert kept["state"] == "unreachable" and kept["read_at"] == first.read_at
        assert kept["rows"] and kept["words"].endswith("Figures from 10:00:00.")
        client.refuse, clock.at = "not_shared", clock.at + 45_000
        books.step()
        await settle(books)
        gone = books.book_of(member(), "positions", clock())
        assert (gone["state"], gone["rows"], gone["read_at"]) == ("not_shared", [], None)

    run(go())


# ---- v0.12.0 review M3: a kept answer is served only while its share stands ----------------------
def test_m3_after_the_close_a_removed_share_stops_showing_within_a_minute():
    """The reviewer's probe turned round. Holdings are read on a Friday evening; the owner then
    removes the Upstox tab's share. Outside the session nothing was read again until the next
    one, so the page went on answering the kept rows (the old code: 1 h, 5 h and 30 h later, with
    no call to the portfolio). Now the share is asked again at most once a minute, and the next
    page answer is "Not shared with you" with no row and nothing kept."""

    async def go():
        friday = MONDAY + timedelta(days=4)
        clock = Clock(ist(friday, 19))
        books, client = shelf(clock, {"holdings": [holding()]})
        client.shares = shared_answer()
        who = member()
        await books._fresh(who, "holdings", clock())
        before = books.book_of(who, "holdings", clock())
        assert before["state"] == "closed" and len(before["rows"]) == 1
        clock.at += 20_000
        await books._fresh(who, "holdings", clock())
        assert len(client.asked()) == 1 and len(client.reads()) == 1  # within the minute: not asked

        client.shares = shared_answer(level=None)  # the owner removes the share (or it expires)
        out = []
        for after_ms in (60_000, 3_600_000, 5 * 3_600_000, 30 * 3_600_000):
            clock.at = ist(friday, 19) + 20_000 + after_ms
            await books._fresh(who, "holdings", clock())
            out.append(books.book_of(who, "holdings", clock()))
        assert [(b["state"], b["rows"], b["read_at"], b["words"]) for b in out] == [
            ("not_shared", [], None, NOT_SHARED)] * 4  # fmt: skip
        assert books.memory[(1, "holdings")].data is None and books.keys(1) == set()
        assert books.dropped == 1

    run(go())


@pytest.mark.parametrize("changed", [shared_answer("restricted"),
                                     shared_answer(hides=("funds", "pnl", "qty"))])  # fmt: skip
def test_m3_a_share_made_restricted_or_a_tightened_privacy_drops_what_is_kept(changed):
    """The share still stands but what it shows changed (the level, or the categories the
    portfolio hides): the kept rows were read under the old terms, so they are dropped and the
    part is read again under the new ones, after the close too. Old code: no read, old rows."""

    async def go():
        clock = Clock(ist(MONDAY, 19))
        books, client = shelf(clock, {"holdings": [holding()], "positions": [position()]})
        client.shares = shared_answer()
        who = member()
        for part in ("holdings", "positions"):
            await books._fresh(who, part, clock())
        assert len(client.reads()) == 2
        client.shares, client.rows = changed, {"holdings": [], "positions": []}
        clock.at += 61_000
        await books._fresh(who, "holdings", clock())
        assert books.book_of(who, "holdings", clock())["rows"] == []
        assert books.memory[(1, "positions")] == Memory("closed")  # dropped with it, not served
        assert books.book_of(who, "positions", clock())["rows"] == []
        assert len(client.reads("holdings")) == 2 and books.dropped == 2

    run(go())


def test_m3_an_open_screen_empties_and_its_socket_is_told_without_a_page_asking():
    """A screen left open across the close: the re-sync asks what is shared once a minute, drops
    the kept rows when the share is gone, tells the member's sockets (``book``) and lets go of
    the rows' symbols. Old code: the screen kept its rows until the next session."""

    async def go():
        clock = Clock(ist(MONDAY, 19))
        books, client = shelf(clock, {"positions": [position()]})
        client.shares = shared_answer()
        sock, who = Sock(), member()
        books.open_sockets = lambda: [(sock, who)]
        told = []
        books.after_read.append(lambda member_id, part, kept: told.append((part, kept.state)))
        books.screen(sock, who, "positions")
        await settle(books)
        assert books.keys(1) == {position()["instrument_key"]} and told == [("positions", "ok")]
        assert client.ops()[:2] == ["shares_for_viewer", "upstox_book"]  # asked before the read
        books.step()
        await settle(books)
        assert len(client.asked()) == 1 and books.dropped == 0
        client.shares = shared_answer(level=None)
        clock.at += 59_000
        books.step()
        await settle(books)
        assert len(client.asked()) == 1 and books.keys(1)  # not yet a minute
        clock.at += 1_000
        books.step()
        await settle(books)
        assert told[1] == ("positions", "not_shared") and books.keys(1) == set()
        books.step()  # the re-sync reads the part again: the portfolio's own refusal is kept
        await settle(books)
        view = books.book_of(who, "positions", clock())
        assert (view["state"], view["rows"]) == ("not_shared", [])
        client.shares = shared_answer()  # shared again: the open screen fills at its next look
        clock.at += 60_000
        books.step()
        await settle(books)
        assert len(books.book_of(who, "positions", clock())["rows"]) == 1

    run(go())


def test_m3_while_the_portfolio_does_not_answer_what_is_kept_stays_and_is_asked_again():
    async def go():
        clock = Clock(ist(MONDAY, 19))
        books, client = shelf(clock, {"holdings": [holding()]})
        client.shares = shared_answer()
        who = member()
        await books._fresh(who, "holdings", clock())
        client.shares_down = True
        clock.at += 61_000
        await books._fresh(who, "holdings", clock())
        assert len(books.book_of(who, "holdings", clock())["rows"]) == 1 and books.dropped == 0
        client.shares_down, client.shares = False, shared_answer(level=None)
        clock.at += 61_000
        await books._fresh(who, "holdings", clock())
        assert books.book_of(who, "holdings", clock())["state"] == "not_shared"
        assert len(client.asked()) == 3

    run(go())


# ---- v0.12.0 verification N6, N7: while the portfolio gives no answer about the share ------------
def test_n6_a_page_answers_within_a_few_seconds_while_the_portfolio_hangs(monkeypatch):
    """N6: the share question and the read each waited the client's 12 s, so the summary (two
    parts) could take about 36 s against nginx's 30. A page now waits ``SHARES_WAIT_S`` for the
    share answer, reads nothing new while it has not come, and says the shares could not be
    checked over what it kept: never as current. Fails on the old code: the page waited for the
    question (here, for as long as it hung) and answered the kept rows as "closed"."""

    async def go():
        monkeypatch.setattr(books_module, "SHARES_WAIT_S", 0.05)
        clock = Clock(ist(MONDAY, 19))
        books, client = shelf(clock, {"positions": [position()], "funds": [{"x": 1}]})
        client.shares = shared_answer(hides=())
        who = member(shown={"pnl": True, "qty": True, "funds": True, "orders": True})
        await books._fresh(who, "positions", clock())
        assert books.book_of(who, "positions", clock())["state"] == "closed"
        gate, answer = asyncio.Event(), client.call

        async def hangs(op, member_id, args=None, *, mask, tabs=None):
            client.calls.append((op, member_id, dict(args or {}), mask))
            await gate.wait()  # the portfolio took the connection and says nothing
            raise Refusal("unreachable")

        client.call = hangs
        clock.at += 61_000
        started = asyncio.get_running_loop().time()
        seat = type("Seat", (), {"member": who})()
        done = await asyncio.wait_for(books.route_summary(None, {}, seat), 2.0)
        assert asyncio.get_running_loop().time() - started < 1.0
        body = done.payload["data"]
        assert body["state"] == "unreachable"
        assert body["words"] == "What is shared with you could not be checked just now. " \
            "Figures from 19:00:00."  # fmt: skip
        book = books.book_of(who, "positions", clock())
        assert (book["state"], len(book["rows"])) == ("unreachable", 1) and book["words"] == body[
            "words"
        ]
        assert len(client.asked()) == 2 and len(client.reads()) == 1  # one question, no new read
        gate.set()
        await settle(books)
        client.call = answer  # it answers again: the next page is as before
        clock.at += 61_000
        await books._fresh(who, "positions", clock())
        again = books.book_of(who, "positions", clock())
        assert (again["state"], len(again["rows"])) == ("closed", 1)
        assert again["words"] == "Market closed \u00b7 figures from 19:00:00"

    run(go())


def test_n7_kept_figures_are_put_away_after_ten_minutes_without_an_answer_and_say_why():
    """N7: while the portfolio gave no answer about the share (a seal key that no longer matches,
    the portfolio switched off or down), an open screen kept its figures until the service
    restarted. After ``SHARES_KEEP_MS`` without an answer they are put away, the socket is told,
    the symbols are let go, the sentence says why, and nothing is read or kept again until the
    portfolio answers. Fails on the old code: the rows were still served hours later."""

    async def go():
        clock = Clock(ist(MONDAY, 19))
        books, client = shelf(clock, {"holdings": [holding()]})
        client.shares = shared_answer()
        sock, who = Sock(), member()
        books.open_sockets = lambda: [(sock, who)]
        told = []
        books.after_read.append(lambda member_id, part, kept: told.append((part, kept.state)))
        books.screen(sock, who, "holdings")
        await settle(books)
        assert books.keys(1) and told == [("holdings", "ok")]
        client.shares_down = True
        for _ in range(10):  # nine minutes after the first unanswered question: kept, not checked
            clock.at += 61_000
            books.step()
            await settle(books)
        held = books.book_of(who, "holdings", clock())
        assert (held["state"], len(held["rows"])) == ("unreachable", 1)
        assert held["words"].startswith("What is shared with you could not be checked just now.")
        assert books.dropped == 0
        clock.at += 61_000  # past the tenth minute
        books.step()
        await settle(books)
        gone = books.book_of(who, "holdings", clock())
        assert (gone["state"], gone["rows"], gone["read_at"]) == ("unreachable", [], None)
        assert gone["words"] == (
            "What is shared with you could not be checked for ten minutes, so the figures were "
            "put away. They come back when the server answers."
        )
        assert books.memory[(1, "holdings")].data is None and books.keys(1) == set()
        assert told[-1] == ("holdings", "unchecked") and books.dropped == 1
        reads = len(client.reads())
        for _ in range(5):  # hours later: still nothing, no read of the account, said once
            clock.at += 3_600_000
            books.step()
            await settle(books)
            await books._fresh(who, "holdings", clock())
        assert len(client.reads()) == reads and told.count(("holdings", "unchecked")) == 1
        assert books.book_of(who, "holdings", clock())["rows"] == []
        client.shares_down = False  # the portfolio answers again: the open screen fills
        clock.at += 61_000
        books.step()
        await settle(books)
        books.step()
        await settle(books)
        back = books.book_of(who, "holdings", clock())
        assert len(back["rows"]) == 1 and back["state"] in ("ok", "closed")

    run(go())


def test_n7_the_bound_is_kept_at_the_door_too_and_a_removed_share_still_wins():
    """Between the tenth minute and the next question the kept rows are still in memory: the
    answer leaves them out already. And an answer that the share is gone is "Not shared with
    you", not the put-away sentence."""

    async def go():
        clock = Clock(ist(MONDAY, 19))
        books, client = shelf(clock, {"holdings": [holding()]})
        client.shares = shared_answer()
        who = member()
        await books._fresh(who, "holdings", clock())
        client.shares_down = True
        clock.at += 61_000
        await books._fresh(who, "holdings", clock())  # the first question without an answer
        clock.at += books_module.SHARES_KEEP_MS - 1
        assert len(books.book_of(who, "holdings", clock())["rows"]) == 1
        clock.at += 1
        assert books.memory[(1, "holdings")].data is not None
        assert books.book_of(who, "holdings", clock())["rows"] == []
        client.shares_down, client.shares = False, shared_answer(level=None)
        await books._fresh(who, "holdings", clock())
        gone = books.book_of(who, "holdings", clock())
        assert (gone["state"], gone["words"], gone["rows"]) == ("not_shared", NOT_SHARED, [])

    run(go())


def test_m3_one_question_at_a_time_and_what_is_remembered_is_bounded():
    """fd-audit: two pages asking together make one question; a member with nothing kept and no
    socket is forgotten after six hours; ``close`` ends a question still on its way."""

    async def go():
        clock = Clock(ist(MONDAY, 19))
        books, client = shelf(clock, {"holdings": [holding()]})
        client.shares = shared_answer()
        who = member()
        await asyncio.gather(books.confirm(who), books.confirm(who), books.confirm(who))
        assert len(client.asked()) == 1 and not books._share_flights
        clock.at += books_module.KEEP_MS
        books.step()
        assert books.shared == {}
        gate = asyncio.Event()

        async def slow(*args, **kwargs):
            await gate.wait()

        client.call = slow
        task = books.confirm_start(who)
        await asyncio.sleep(0)
        await books.close()
        assert task.cancelled() and not books._share_flights

    run(go())


def test_m3_the_sign_reads_only_the_upstox_tabs_share_and_what_is_hidden():
    sign_of = books_module.sign_of
    assert sign_of(shared_answer()) == ("view", ("funds",))
    assert sign_of(shared_answer("restricted", ("qty", "pnl"))) == ("restricted", ("pnl", "qty"))
    assert sign_of(shared_answer(level=None)) == (None, ("funds",))
    assert sign_of({"tabs": []}) == (None, ())
    for not_the_op in ({}, None, {"tabs": None}, [], {"enabled": True}):
        assert sign_of(not_the_op) is None  # nothing can be told: nothing is dropped


def test_a_part_the_members_privacy_withholds_is_never_asked_for():
    async def go():
        clock = Clock(ist(MONDAY, 10))
        books, client = shelf(clock)
        books.screen(Sock(2), member(2, CHILD), "orders")
        await settle(books)
        assert client.calls == []
        view = books.book_of(member(2, CHILD), "orders", clock())
        assert (view["state"], view["words"], view["rows"]) == ("not_shared", NOT_SHARED, [])
        assert view["hidden"] == list(CATEGORIES)

    run(go())


def test_what_no_screen_shows_is_forgotten_after_six_hours():
    """fd-audit (memory): member ids only grow as members come and go, so nothing is kept for
    ever; what an open screen shows is kept however old."""

    async def go():
        clock = Clock(ist(MONDAY, 10))
        books, _ = shelf(clock)
        books.screen(Sock(), member(1), "positions")
        books.memory[(9, "holdings")] = Memory("ok", [], clock(), clock())
        books.memory[(1, "positions")] = Memory("ok", [], clock(), clock())
        clock.at += books_module.KEEP_MS - 1
        books.step()
        await settle(books)
        assert (9, "holdings") in books.memory
        clock.at += 1
        books.step()
        await settle(books)
        assert (9, "holdings") not in books.memory and (1, "positions") in books.memory

    run(go())


def test_the_page_reads_on_its_own_only_past_the_resync_or_a_nudge_window():
    async def go():
        clock = Clock(ist(MONDAY, 10))
        books, client = shelf(clock)
        assert books.due(1, "positions", clock(), "page") is True
        books.memory[(1, "positions")] = Memory("ok", [], clock(), clock())
        clock.at += 30_000
        assert books.due(1, "positions", clock(), "page") is False
        books.mark_dirty("positions", clock())
        assert books.due(1, "positions", clock(), "page") is True  # 30 s since: past 10 s
        books.memory[(1, "positions")] = Memory("ok", [], clock(), clock())
        books.mark_dirty("positions", clock() + 1)
        clock.at += 9_000
        assert books.due(1, "positions", clock(), "page") is False  # inside the nudge window
        assert books.due(1, "positions", clock(), "screen") is True  # a screen opening: 5 s

    run(go())


# ---- the service: the door, the nudge, /health and family.db -------------------------------------
CANARY = 987_654_321  # a figure that must never be written to family.db


def dead_port() -> int:
    """A port nothing listens on: the feed link's calls there are refused at once (never the real
    5580 or 5581)."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def plane_service(root: Path, url: str, fernet: Fernet, clock: Clock, **more) -> FamilyService:
    port = dead_port()
    more = {"feed_url": f"http://127.0.0.1:{port}", "feed_zmq": f"tcp://127.0.0.1:{port}"} | more
    return FamilyService(settings(root, portfolio_url=url, **more), keys(), hasher=fast_hasher(),
                         clock=clock, jobs={}, data_plane=PlaneKeys(fernet))  # fmt: skip


async def until_type(sock, kind: str, seconds: float = 5.0) -> dict:
    """The next message of ``kind`` a real socket receives (others are passed over)."""
    async with asyncio.timeout(seconds):
        while True:
            message = json.loads(await sock.recv())
            if message.get("type") == kind:
                return message


def test_the_door_serves_masked_books_and_a_nudge_tells_the_open_screen(tmp_path):
    """Through the running service: a child's positions leave masked though the portfolio leaked
    them; orders are not asked for; a sealed nudge reads and tells the open screen (Y11); /health
    has counts only; family.db holds no figure (Y44)."""

    async def go():
        clock, fernet = Clock(ist(MONDAY, 10)), Fernet(Fernet.generate_key())
        rows = {"positions": [position(pnl_paise=CANARY)]}
        fake = FakePortfolio(fernet, books_answer(rows, clock))
        url = await fake.start()
        service = plane_service(tmp_path, url, fernet, clock)
        try:
            async with running(service):
                port = service.listener.port
                child, cookie = await signed_in(service, CHILD, clock(), role="child")
                status, _, answer = await ask(port, "GET", "/family/api/books/positions",
                                              headers={"Cookie": cookie})  # fmt: skip
                assert status == 200, answer
                book = answer["data"]
                assert (book["part"], book["state"], book["words"]) == ("positions", "ok", None)
                assert book["hidden"] == list(CATEGORIES) and book["read_at"] == clock()
                assert "qty_u" not in book["rows"][0] and "pnl_paise" not in book["rows"][0]
                reads = [c for c in fake.calls if c[0] == "upstox_book"]
                assert reads[0][1] == child["id"] and reads[0][2]["parts"] == ["positions"]
                assert fake.ops() == ["shares_for_viewer", "upstox_book"]  # M3: asked first
                status, _, answer = await ask(port, "GET", "/family/api/books/orders",
                                              headers={"Cookie": cookie})  # fmt: skip
                assert answer["data"]["state"] == "not_shared" and len(fake.calls) == 2
                sock = await connect(f"ws://127.0.0.1:{port}/family/ws",
                                     additional_headers={"Cookie": cookie})  # fmt: skip
                await sock.send(json.dumps({"action": "authenticate", "family": True}))
                await sock.send(json.dumps({"action": "screen", "name": "positions"}))
                await until_type(sock, "feed_state")
                for _ in range(300):  # the screen is the socket's next message: wait for it
                    if service.plane.books.screens:
                        break
                    await asyncio.sleep(0.01)
                clock.at += 20_000
                nudge = sealed(service.keys.nudge, "nudge", principal="sudarshan-alerts",
                               kind="orders", sandbox=False, at=clock())  # fmt: skip
                began = time.monotonic()
                status, _, answer = await ask(port, "POST", "/v1/nudge", body=nudge)
                assert (status, answer["data"]) == (200, {"taken": True})
                told = await until_type(sock, "book")  # Y11: at once, well inside 3 s
                assert told == {"type": "book", "part": "positions", "read_at": clock()}
                assert time.monotonic() - began < 3
                await sock.close()
                status, _, answer = await ask(port, "GET", "/family/api/books/positions",
                                              headers={"Cookie": cookie})  # fmt: skip
                assert answer["data"]["read_at"] == clock()
                assert fake.ops().count("upstox_book") == 2  # kept
                status, _, health = await ask(port, "GET", "/health")
                data = health["family_state"]["data"]
                assert data["books"] == {"reads": 2, "screens": 0, "kept": 1, "dropped": 0}
                assert data["portfolio"] == "answering" and data["nudges"]["taken"] == 1
                assert "SYNTH" not in json.dumps(health) and "Member" not in json.dumps(health)
            stored = b"".join(p.read_bytes() for p in (tmp_path / "state").glob("family.db*"))
            assert str(CANARY).encode() not in stored
        finally:
            await fake.close()

    run(go())


def test_the_summary_and_the_shared_portfolio_through_the_door(tmp_path):
    async def go():
        clock, fernet = Clock(ist(MONDAY, 10)), Fernet(Fernet.generate_key())
        rows = {"positions": [position(1, 100_000), position(2, -100_000), position(3, 0)]}

        def answer(op, member_id, args):
            if op == "shares_for_viewer":
                return ok({"tabs": [{"tab_id": 4, "name": "Long term", "kind": "custom",
                                     "level": "view", "expires_at": None}],
                           "hides": [], "member": str(member_id)})  # fmt: skip
            if op == "summary":
                return ok({"entries": [{"tab": {"id": 4}, "live": {"totals": {"qty_u": 9}}}]})
            if op == "tab_get":
                return ok({"totals": {"value_paise": 5}, "rows": [{"asset": {"symbol": "X"}}]})
            return books_answer(rows, clock)(op, member_id, args)

        fake = FakePortfolio(fernet, answer)
        url = await fake.start()
        service = plane_service(tmp_path, url, fernet, clock)
        try:
            async with running(service):
                port = service.listener.port
                _, cookie = await signed_in(service, VIEWER, clock())
                head = {"Cookie": cookie}
                summary = (await ask(port, "GET", "/family/api/summary", headers=head))[2]["data"]
                assert summary["positions"] == {"open": 2, "long": 1, "short": 1}
                assert summary["pnl"] == {"pnl_paise": 30_000, "realised_paise": 0,
                                          "unrealised_paise": 30_000}  # fmt: skip
                assert summary["funds"] is None and summary["hidden"] == ["funds"]
                folio = (await ask(port, "GET", "/family/api/portfolio", headers=head))[2]["data"]
                assert folio["tabs"] == [{"id": 4, "name": "Long term", "kind": "custom",
                                          "level": "view", "expires_at": None}]  # fmt: skip
                assert folio["summary"]["entries"][0]["live"]["totals"] == {"qty_u": 9}
                tab = (await ask(port, "GET", "/family/api/portfolio/tabs/4", headers=head))[2]
                assert tab["data"]["view"]["totals"] == {"value_paise": 5}
                other = (await ask(port, "GET", "/family/api/portfolio/tabs/5", headers=head))[2]
                assert other["data"]["state"] == "not_shared" and other["data"]["view"] is None
                assert "tab_get" not in [op for op, _, a in fake.calls if a.get("id") == 5]
        finally:
            await fake.close()

    run(go())


def test_without_its_keys_the_service_has_no_data_plane_and_its_doors_say_not_ready(tmp_path):
    async def go():
        service = FamilyService(settings(tmp_path), keys(), hasher=fast_hasher(), jobs={})
        async with running(service):
            assert service.plane is None and service.nudged("orders", False, 1) is False
            _, cookie = await signed_in(service, VIEWER, int(time.time() * 1000))
            status, _, answer = await ask(service.listener.port, "GET",
                                          "/family/api/books/positions",
                                          headers={"Cookie": cookie})  # fmt: skip
            assert answer["code"] == "not_ready"

    run(go())


def test_reads_and_nudges_leave_no_descriptor_and_a_stop_leaves_no_task(tmp_path):
    """fd-audit: a hundred rounds of a read through the door and a nudge leave the descriptor
    table as it was; the stop ends every task of the data plane and closes its client."""
    from sudarshan.tests.fdwatch import FdWatch

    async def go():
        clock, fernet = Clock(ist(MONDAY, 10)), Fernet(Fernet.generate_key())
        fake = FakePortfolio(fernet, books_answer({"positions": [position()]}, clock))
        url = await fake.start()
        service = plane_service(tmp_path, url, fernet, clock)
        nudge = lambda: sealed(service.keys.nudge, "nudge", principal="sudarshan-alerts",  # noqa: E731
                               kind="orders", sandbox=False, at=clock())  # fmt: skip
        try:
            async with running(service):
                port = service.listener.port
                child, cookie = await signed_in(service, CHILD, clock(), role="child")
                service.plane.books.screen(Sock(child["id"]), child, "positions")
                head = {"Cookie": cookie}
                await ask(port, "GET", "/family/api/books/positions", headers=head)  # warm up
                await settle(service.plane.books)
                watch = FdWatch()
                for _ in range(100):
                    clock.at += 50_000  # past the re-sync and the nudge window: every call reads
                    status, _, _ = await ask(port, "GET", "/family/api/books/positions",
                                             headers=head)  # fmt: skip
                    assert status == 200
                    assert (await ask(port, "POST", "/v1/nudge", body=nudge()))[0] == 200
                    await settle(service.plane.books)
                await watch.aassert_clean()
                assert len(fake.calls) >= 200
            others = [t.get_name() for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            assert others == [] and service.plane.http.is_closed
        finally:
            await fake.close()

    run(go())


#: The data plane's modules (F5 to F7); each lane adds its own.
PLANE_MODULES = [
    "privacy.py",
    "portfolio_client.py",
    "books.py",
    "nudge.py",
    "feedlink.py",
    "viewer_ws.py",
    "symbol_map.py",
    "watchlists.py",
    "internal_client.py",
]


def test_no_log_call_of_the_data_plane_passes_a_name_a_code_or_a_token():
    """Design 2.8 and 7.2: no log call passes a variable named handle, name, chat_id, endpoint,
    code, token, password or totp (the checker is the family service's own, test_family_logs)."""
    from test_family_logs import FAMILY, offences

    sources = {module: (FAMILY / module).read_text(encoding="utf-8") for module in PLANE_MODULES}
    assert any("log.warning(" in text or "log.exception(" in text for text in sources.values())
    for module, source in sources.items():
        assert offences(source, module) == []
    assert offences("log.warning('x %s', member.code)\n", "planted") == ["planted:1: code"]


def test_the_plane_keys_are_the_provision_steps_files(tmp_path):
    from sudarshan.family.config import ConfigError

    etc = tmp_path / "etc"
    etc.mkdir()
    made = settings(tmp_path)
    with pytest.raises(ConfigError):
        PlaneKeys.load(made)
    key, internal = Fernet.generate_key(), Fernet.generate_key()
    (etc / "seal-portfolio-family.key").write_bytes(key + b"\n")
    with pytest.raises(ConfigError):  # F7: the internal route's key is the provision step's too
        PlaneKeys.load(made)
    (etc / "seal-internal.key").write_bytes(internal + b"\n")
    loaded = PlaneKeys.load(made)
    assert loaded.portfolio.decrypt(Fernet(key).encrypt(b"x")) == b"x"
    assert loaded.internal.decrypt(Fernet(internal).encrypt(b"y")) == b"y"
