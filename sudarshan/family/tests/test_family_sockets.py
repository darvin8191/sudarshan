"""F6 (M18.1, M6.7) and the mid-point review's M1: the 20 sockets and their shares, and the dialect a
family phone speaks on /family/ws (authenticate, subscribe, unsubscribe, screen, market_data,
feed_state, quotes, the book message).

Every server binds an ephemeral port on 127.0.0.1 (never 5585); the feed is a fake (no ZMQ here:
test_family_feedlink.py has the bus).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from pathlib import Path

import pytest
from family_net import fast_hasher, keys, running, settings
from family_planekit import VIEWER, signed_in
from family_testkit import close
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from sudarshan.family.service import FamilyService


def run(coro):
    try:
        return asyncio.run(coro)
    finally:
        close(None)


async def attempt(port: int, cookie: str, gate: asyncio.Event | None = None):
    """One handshake (after ``gate`` opens): the open socket, or the refusal's status."""
    if gate is not None:
        await gate.wait()
    try:
        return await connect(f"ws://127.0.0.1:{port}/family/ws",
                             additional_headers={"Cookie": cookie}, open_timeout=20)  # fmt: skip
    except InvalidStatus as refused:
        return refused.response.status_code


async def settled(service, count: int, seconds: float = 5.0) -> int:
    deadline = time.monotonic() + seconds
    while len(service.registry) != count and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    return len(service.registry)


async def shut(sockets) -> None:
    for sock in sockets:
        if not isinstance(sock, int):
            with contextlib.suppress(Exception):
                await sock.close()


# ---- M1: the cap is a claim taken before the first await, and no member takes every slot -------------
def test_sixty_handshakes_at_once_from_one_session_leave_its_share_open(tmp_path: Path):
    """M1 (barrier): 60 handshakes released at once by one session. Before the fix the cap was
    read before the session check's await and the socket counted only after its handshake, so all
    60 stayed open against a cap of 20; now the session keeps its share (2) and the rest are told
    busy (503). Another member still gets in."""

    async def go():
        service = FamilyService(settings(tmp_path), keys(), hasher=fast_hasher(), jobs={})
        async with running(service):
            port, now = service.listener.port, int(time.time() * 1000)
            _, cookie_a = await signed_in(service, VIEWER, now)
            _, cookie_b = await signed_in(service, VIEWER, now)
            gate = asyncio.Event()
            tasks = [asyncio.create_task(attempt(port, cookie_a, gate)) for _ in range(60)]
            await asyncio.sleep(0.2)
            gate.set()
            got = await asyncio.gather(*tasks)
            opened = [s for s in got if not isinstance(s, int)]
            refused = [s for s in got if isinstance(s, int)]
            try:
                assert len(opened) <= 20, f"{len(opened)} sockets open against a cap of 20"
                assert len(opened) == 2 and set(refused) == {503}
                assert await settled(service, 2) == 2
                other = await attempt(port, cookie_b)
                assert not isinstance(other, int), other  # one member at their share: not busy
                await other.close()
            finally:
                await shut(got)

    run(go())


def test_the_total_of_twenty_holds_against_a_barrier_of_many_members(tmp_path: Path):
    """M1 (barrier): 30 members' handshakes released at once; every slot is claimed before its
    session check's await, so no more than 20 are ever open."""

    async def go():
        service = FamilyService(settings(tmp_path), keys(), hasher=fast_hasher(), jobs={})
        async with running(service):
            port, now = service.listener.port, int(time.time() * 1000)
            cookies = [(await signed_in(service, VIEWER, now))[1] for _ in range(30)]
            gate = asyncio.Event()
            tasks = [asyncio.create_task(attempt(port, cookie, gate)) for cookie in cookies]
            await asyncio.sleep(0.2)
            gate.set()
            got = await asyncio.gather(*tasks)
            try:
                opened = [s for s in got if not isinstance(s, int)]
                assert len(opened) == 20, f"{len(opened)} sockets open against a cap of 20"
                assert {s for s in got if isinstance(s, int)} == {503}
                assert len(service.registry) == 20 and service.registry._reserved == 0
            finally:
                await shut(got)

    run(go())


def test_a_member_holds_at_most_four_and_never_shuts_another_member_out(tmp_path: Path):
    async def go():
        service = FamilyService(settings(tmp_path), keys(), hasher=fast_hasher(), jobs={})
        async with running(service):
            port, now = service.listener.port, int(time.time() * 1000)
            member, first = await signed_in(service, VIEWER, now)
            cookies = [first]
            for _ in range(2):  # the same member signed in on two more phones
                cookies.append(await another_session(service, member["id"], now))
            held = []
            try:
                for cookie in cookies[:2]:
                    for _ in range(2):
                        held.append(await attempt(port, cookie))
                assert all(not isinstance(s, int) for s in held)  # 2 phones x 2 sockets = 4
                assert await attempt(port, cookies[2]) == 503  # a fifth: the member's share
                _, other = await signed_in(service, VIEWER, now)
                sock = await attempt(port, other)
                assert not isinstance(sock, int)
                held.append(sock)
            finally:
                await shut(held)

    run(go())


def test_a_session_at_its_share_lets_a_new_socket_in_only_in_place_of_a_dead_one(tmp_path: Path,
                                                                              monkeypatch):  # fmt: skip
    """A reload on a bad network: a socket that has said nothing (not even a pong) for the dead
    time is closed to let its session's new socket in; a live one is never closed for it (a third
    tab of a phone would otherwise close another tab, which reconnects and closes the next)."""
    from websockets.client import ClientProtocol
    from websockets.uri import parse_uri

    from sudarshan.family import ws

    monkeypatch.setattr(ws, "DEAD_AFTER_S", 0.5)

    async def silent(port: int, cookie: str):
        """A handshake whose phone then sends nothing at all (a dead network)."""
        client = ClientProtocol(parse_uri(f"ws://127.0.0.1:{port}/family/ws"))
        request = client.connect()
        request.headers["Cookie"] = cookie
        client.send_request(request)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        for chunk in client.data_to_send():
            writer.write(chunk)
        await writer.drain()
        client.receive_data(await reader.read(4096))
        assert client.events_received()[0].status_code == 101
        return reader, writer

    async def go():
        service = FamilyService(settings(tmp_path), keys(), hasher=fast_hasher(), jobs={})
        async with running(service):
            port, now = service.listener.port, int(time.time() * 1000)
            _, cookie = await signed_in(service, VIEWER, now)
            live = [await attempt(port, cookie) for _ in range(2)]
            try:
                assert await attempt(port, cookie) == 503  # two live: the third waits
                await shut(live)
                assert await settled(service, 0) == 0
                dead = [await silent(port, cookie) for _ in range(2)]
                await asyncio.sleep(0.7)  # past the dead time, short of the 60 s silence close
                fresh = await attempt(port, cookie)
                assert not isinstance(fresh, int)
                assert await settled(service, 1) == 1  # both dead ones were closed for it
                await fresh.close()
                for _, writer in dead:
                    writer.close()
            finally:
                await shut(live)

    run(go())


async def another_session(service, member_id: int, now: int) -> str:
    from sudarshan.family import sessions
    from sudarshan.family.contract import COOKIE

    def make(tx):
        device = sessions.Device("Android 14 · Chrome 129", "Android 14 · Chrome 129")
        return sessions.mint(tx, member_id, device, now)[0]

    return f"{COOKIE}={await service.store.write(make)}"


def test_every_refused_or_ended_handshake_gives_its_slot_back(tmp_path: Path):
    """A refused session check, a busy answer and a socket that ended each give the slot back:
    a hundred refusals later the cap still lets twenty in."""

    async def go():
        service = FamilyService(settings(tmp_path), keys(), hasher=fast_hasher(), jobs={})
        async with running(service):
            port, now = service.listener.port, int(time.time() * 1000)
            for _ in range(100):
                assert await attempt(port, "__Host-sudarshan_family=nobody") == 401
            cookies = [(await signed_in(service, VIEWER, now))[1] for _ in range(4)]
            held = []
            try:
                for cookie in cookies:
                    for _ in range(2):
                        held.append(await attempt(port, cookie))
                assert all(not isinstance(s, int) for s in held) and len(service.registry) == 8
                assert service.registry._reserved == 0
            finally:
                await shut(held)
            assert await settled(service, 0) == 0

    run(go())


# ---- F6: the dialect ----------------------------------------------------------------------------------
from cryptography.fernet import Fernet  # noqa: E402
from family_planekit import (  # noqa: E402
    CHILD,
    MONDAY,
    Clock,
    FakeFeed,
    FakePortfolio,
    Sock,
    book_view,
    frame,
    ist,
    ok,
    position,
)

from sudarshan.family import sessions as sessions_module  # noqa: E402
from sudarshan.family.books import Memory, PlaneKeys  # noqa: E402
from sudarshan.family.symbol_map import Listing  # noqa: E402
from sudarshan.family.viewer_ws import CAPPED, NOT_AVAILABLE, TOO_MANY  # noqa: E402

NIFTY = {"symbol": "NIFTY", "exchange": "NSE_INDEX"}


class World:
    """A running service with its data plane on a fake portfolio and a fake feed."""

    def __init__(self, root: Path, rows_of=None, **more):
        self.root, self.rows_of, self.more = root, rows_of or (lambda member_id: {}), more
        self.clock = Clock(ist(MONDAY, 10))

    async def __aenter__(self):
        fernet = Fernet(Fernet.generate_key())

        def answer(op, member_id, args):
            rows = self.rows_of(member_id)
            if op != "upstox_book":
                return ok({})
            return ok({p: book_view(rows.get(p, []), self.clock()) for p in args["parts"]})

        self.portfolio = FakePortfolio(fernet, answer)
        self.feed = FakeFeed()
        purl = await self.portfolio.start()
        furl, zurl = await self.feed.start()
        made = settings(self.root, portfolio_url=purl, feed_url=furl, feed_zmq=zurl, **self.more)
        self.service = FamilyService(made, keys(), hasher=fast_hasher(), clock=self.clock,
                                     jobs={}, data_plane=PlaneKeys(fernet))  # fmt: skip
        self._running = running(self.service)
        await self._running.__aenter__()
        self.port = self.service.listener.port
        return self

    async def __aexit__(self, *exc):
        try:
            await self._running.__aexit__(*exc)
        finally:
            await self.feed.close()
            await self.portfolio.close()

    @property
    def plane(self):
        return self.service.plane

    async def phone(self, privacy=VIEWER, role="viewer", cookie=None):
        if cookie is None:
            member, cookie = await signed_in(self.service, privacy, self.clock(), role=role)
        else:
            member = None
        sock = await connect(f"ws://127.0.0.1:{self.port}/family/ws",
                             additional_headers={"Cookie": cookie})  # fmt: skip
        await sock.send(json.dumps({"action": "authenticate", "family": True}))
        assert await next_of(sock, "auth") == {"type": "auth", "status": "success"}
        return member, cookie, sock


async def next_of(sock, kind: str, seconds: float = 5.0, where=None) -> dict:
    """The next message of ``kind`` (that ``where`` accepts), others passed over."""
    async with asyncio.timeout(seconds):
        while True:
            message = json.loads(await sock.recv())
            if message.get("type") == kind and (where is None or where(message)):
                return message


async def gather(sock, seconds: float) -> list[dict]:
    """Every message received within ``seconds``."""
    out = []
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(seconds):
            while True:
                out.append(json.loads(await sock.recv()))
    return out


def known(world: World, n: int, member_id: int) -> list[dict]:
    """``n`` synthetic listings in the symbol map, kept as the member's positions (followable)."""
    rows = [position(i) for i in range(1, n + 1)]
    world.plane.symbols.put(Listing(r["symbol"], r["exchange"], r["instrument_key"], r["name"],
                                    refreshed_at=world.clock()) for r in rows)  # fmt: skip
    for part in ("positions", "holdings"):
        data = rows if part == "positions" else []
        world.plane.books.memory[(member_id, part)] = Memory(
            "ok", data, world.clock(), world.clock()
        )
    return [{"symbol": r["symbol"], "exchange": r["exchange"]} for r in rows]


def test_authenticate_subscribe_quote_then_ticks_every_100_ms_with_the_feed_state(tmp_path):
    async def go():
        async with World(tmp_path) as world:
            world.feed.snapshots["NSE_INDEX|Nifty 50"] = frame(24_000.5, 23_900.0)
            _, _, sock = await world.phone()
            assert (await next_of(sock, "feed_state"))["state"] in ("reconnecting", "realtime")
            await sock.send(json.dumps({"action": "subscribe", "symbols": [NIFTY], "mode": 1}))
            assert (await next_of(sock, "subscribe"))["status"] == "success"
            quotes = await next_of(sock, "quotes")
            assert quotes["quotes"] == [{"symbol": "NIFTY", "exchange": "NSE_INDEX",
                                         "name": "NIFTY 50", "ltp": 24_000.5, "change": 100.5,
                                         "change_percent": 0.4205, "close": 23_900.0,
                                         "at": 1_763_265_600_000}]  # fmt: skip
            assert quotes["capped"] == [] and "message" not in quotes
            for _ in range(300):  # the claim reached the feed: the bus follows the key now
                if any("NSE_INDEX|Nifty 50" in (b or {}).get("keys", {})
                       for b, _ in world.feed.claims()):  # fmt: skip
                    break
                await asyncio.sleep(0.01)
            for _ in range(300):
                world.feed.publish_health()
                world.feed.publish("NSE_INDEX|Nifty 50", frame(24_001.0, 23_900.0))
                await asyncio.sleep(0.01)
                if world.plane.feed.counts["ticks"]:
                    break
            began = time.monotonic()
            for n in range(100):  # 100 ticks in about a second
                world.feed.publish("NSE_INDEX|Nifty 50", frame(24_100.0 + n, 23_900.0))
                await asyncio.sleep(0.01)
            seen = [m for m in await gather(sock, 0.5) if m.get("type") == "market_data"]
            took = time.monotonic() - began
            assert seen and seen[-1]["data"]["ltp"] == 24_199.0
            assert seen[-1]["symbol"] == "NIFTY" and seen[-1]["data"]["prev_close"] == 23_900.0
            assert len(seen) <= took / 0.1 + 3, (len(seen), took)  # one per 100 ms at most
            await sock.close()

    run(go())


def test_an_unknown_symbol_is_refused_in_words_and_a_socket_follows_at_most_150(tmp_path):
    async def go():
        async with World(tmp_path) as world:
            member, _, sock = await world.phone()
            await sock.send(json.dumps({"action": "subscribe", "mode": 1, "symbols": [
                {"symbol": "NOSUCH", "exchange": "NSE"}]}))  # fmt: skip
            refused = await next_of(sock, "subscribe")
            assert refused == {"type": "subscribe", "status": "error", "message": NOT_AVAILABLE,
                               "symbols": [{"symbol": "NOSUCH", "exchange": "NSE"}]}  # fmt: skip
            wanted = known(world, 151, member["id"])
            await sock.send(json.dumps({"action": "subscribe", "symbols": wanted, "mode": 1}))
            over = await next_of(sock, "subscribe", where=lambda m: m["status"] == "error")
            assert over["message"] == TOO_MANY and over["symbols"] == wanted[150:]
            taken = await next_of(sock, "subscribe", where=lambda m: m["status"] == "success")
            assert len(taken["symbols"]) == 150
            await sock.close()

    run(go())


def test_symbols_past_the_feeds_cap_are_named_with_their_last_price(tmp_path):
    """Design 7.5 target 14: with the claims' quota at 5 (the indices take 3), 8 of 10 followed
    symbols are past it; the phone is told which, with the label, and their last prices."""

    async def go():
        async with World(tmp_path, max_claims=5) as world:
            member, _, sock = await world.phone()
            wanted = known(world, 10, member["id"])
            for i in range(1, 11):
                world.feed.snapshots[f"NSE_EQ|SYNTH{i:07d}"] = frame(100.0 + i, 100.0)
            await sock.send(json.dumps({"action": "subscribe", "symbols": wanted, "mode": 1}))
            capped = await next_of(sock, "quotes", where=lambda m: m["capped"])
            assert capped["message"] == CAPPED
            assert len(capped["capped"]) == 8
            assert {q["symbol"] for q in capped["quotes"]} == {
                c["symbol"] for c in capped["capped"]
            }
            assert all(q["ltp"] is not None and q["at"] for q in capped["quotes"])
            put = world.feed.claims()[-1][0]["keys"]
            assert len(put) == 5 and {k for k in put if k.startswith("NSE_INDEX")} == {
                "NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty Bank", "NSE_INDEX|India VIX"}  # fmt: skip
            await sock.send(json.dumps({"action": "unsubscribe", "symbols": wanted[:6], "mode": 1}))
            later = await next_of(sock, "quotes", where=lambda m: len(m["capped"]) < 8)
            assert len(later["capped"]) == 2
            await sock.close()

    run(go())


def test_a_symbol_waits_for_the_members_books_then_follows_or_is_refused(tmp_path):
    """After a restart a phone subscribes again before it reads its books: what it may see is
    followed once they are read; what it may not is refused, never followed."""

    def rows_of(member_id: int) -> dict:
        return {"positions": [position(1)]} if member_id == 1 else {"positions": [position(9)]}

    async def go():
        async with World(tmp_path, rows_of=rows_of) as world:
            mine, other = position(1), position(9)
            world.plane.symbols.put(Listing(r["symbol"], r["exchange"], r["instrument_key"],
                                            r["name"], refreshed_at=1) for r in (mine, other))  # fmt: skip
            member, _, sock = await world.phone()
            assert member["id"] == 1
            ask = [{"symbol": "SYNTH1", "exchange": "NSE"}, {"symbol": "SYNTH9", "exchange": "NSE"}]
            await sock.send(json.dumps({"action": "subscribe", "symbols": ask, "mode": 1}))
            taken = await next_of(sock, "subscribe", where=lambda m: m["status"] == "success")
            assert taken["symbols"] == [ask[0]]
            refused = await next_of(sock, "subscribe", where=lambda m: m["status"] == "error")
            assert refused["symbols"] == [ask[1]] and refused["message"] == NOT_AVAILABLE
            seat = next(iter(world.plane.dialect.seats.values()))
            assert set(seat.follows) == {("SYNTH1", "NSE")} and not seat.waiting
            await sock.close()

    run(go())


class _Answered:
    """A source (the books, the shared lists) for the M2 tests: ``keys`` are the member's to see,
    and ``has`` says whether it has an answer for them yet (``ensure`` gives one)."""

    def __init__(self, has: bool, keys: tuple[str, ...] = ()):
        self.has, self.mine, self.ensured = has, set(keys), 0

    def keys(self, member_id):
        return set(self.mine) if self.has else set()

    def known(self, member_id):
        return self.has

    async def ensure(self, member):
        self.ensured += 1
        self.has = True

    def screen(self, *args):
        return 0

    def closed(self, sock):
        return None

    def book_keys(self):
        return set()


def _m2_dialect(has: bool):
    """A dialect whose map holds SYNTH1 (the member's own) and SYNTH9 (in the map through another
    member's books: not this member's), and nothing called NOSUCH."""
    from types import SimpleNamespace

    from sudarshan.family.symbol_map import SymbolMap
    from sudarshan.family.viewer_ws import Dialect

    symbols = SymbolMap()
    symbols.put(Listing(r["symbol"], r["exchange"], r["instrument_key"], r["name"], refreshed_at=1)
                for r in (position(1), position(9)))  # fmt: skip

    async def judge(hashed):
        return SimpleNamespace(member={"id": 7, "role": "child", "privacy": dict(CHILD)})

    books = _Answered(has, (position(1)["instrument_key"],))
    dialect = Dialect(judge=judge, symbols=symbols, books=books)
    dialect.sources.append(books)
    return dialect, books


M2_ASK = [{"symbol": "SYNTH9", "exchange": "NSE"}, {"symbol": "NOSUCH", "exchange": "NSE"},
          {"symbol": "SYNTH1", "exchange": "NSE"}]  # fmt: skip


def _errors(sock: Sock) -> list[dict]:
    return [m for m in sock.of("subscribe") if m["status"] == "error"]


def test_m2_a_symbol_another_member_holds_is_refused_with_an_unknown_one_in_one_message():
    """v0.12.0 review M2: the map is one for the whole service, so an answer that told "not in
    the map" from "in the map, not yours" named the owner's holdings to a member who was not
    shared them. With the member's books read, both are refused at once, in one message, in the
    order asked. Fails on the old code: only NOSUCH was refused at once, and SYNTH9 waited."""

    async def go():
        dialect, _ = _m2_dialect(has=True)
        sock = Sock()
        await dialect.on_message(sock, json.dumps({"action": "subscribe", "symbols": M2_ASK}))
        at_once = list(sock.sent)
        await asyncio.sleep(0.05)
        seat = dialect.seats[sock]
        await dialect.close()
        return at_once, _errors(sock), seat

    at_once, errors, seat = asyncio.run(go())
    assert [m for m in at_once if m.get("status") == "error"] == [
        {"type": "subscribe", "status": "error", "message": NOT_AVAILABLE,
         "symbols": M2_ASK[:2]}]  # fmt: skip
    assert errors == [m for m in at_once if m.get("status") == "error"]  # and nothing later
    assert set(seat.follows) == {("SYNTH1", "NSE")} and not seat.waiting


def test_m2_before_the_books_are_read_both_wait_and_are_refused_together():
    """The same two symbols while the member's books have no answer yet: neither is refused at
    once (the old code refused NOSUCH here and now, SYNTH9 later), both wait, and the read that
    lands refuses both in one message while the member's own symbol is followed."""

    async def go():
        dialect, books = _m2_dialect(has=False)
        sock = Sock()
        await dialect.on_message(sock, json.dumps({"action": "subscribe", "symbols": M2_ASK}))
        at_once, waiting = list(sock.sent), dict(dialect.seats[sock].waiting)
        await asyncio.sleep(0.05)  # the ensure read lands and rechecks
        seat = dialect.seats[sock]
        await dialect.close()
        return at_once, waiting, _errors(sock), seat, books.ensured

    at_once, waiting, errors, seat, ensured = asyncio.run(go())
    assert at_once == [] and set(waiting) == {("SYNTH9", "NSE"), ("NOSUCH", "NSE"),
                                              ("SYNTH1", "NSE")}  # fmt: skip
    assert ensured == 1
    assert errors == [{"type": "subscribe", "status": "error", "message": NOT_AVAILABLE,
                       "symbols": M2_ASK[:2]}]  # fmt: skip
    assert set(seat.follows) == {("SYNTH1", "NSE")} and not seat.waiting


def test_m2_a_symbol_the_map_gains_while_it_waits_is_followed_when_it_is_the_members():
    """A symbol asked for before the read that brings it to the map (a list's row) is followed
    once that read lands, as one the map already held would be."""

    async def go():
        dialect, books = _m2_dialect(has=False)
        books.mine.add("NSE_EQ|LATER")
        sock = Sock()
        await dialect.on_message(sock, json.dumps({"action": "subscribe", "symbols": [
            {"symbol": "LATER", "exchange": "NSE"}]}))  # fmt: skip
        dialect.symbols.put([Listing("LATER", "NSE", "NSE_EQ|LATER", "Later", refreshed_at=1)])
        await asyncio.sleep(0.05)
        seat = dialect.seats[sock]
        await dialect.close()
        return seat, _errors(sock)

    seat, errors = asyncio.run(go())
    assert seat.follows == {("LATER", "NSE"): "NSE_EQ|LATER"} and not errors


def test_the_book_message_reaches_every_socket_of_a_member_who_may_see_the_part(tmp_path):
    """FD2: a member's every open socket is told when a part they may see was read, not only the
    one that follows prices or shows that screen; a child, whose orders are hidden, is not."""

    async def go():
        async with World(tmp_path, rows_of=lambda m: {"trades": [], "positions": []}) as world:
            member, cookie, trades = await world.phone()
            _, _, idle = await world.phone(cookie=cookie)
            _, _, child = await world.phone(CHILD, role="child")
            await child.send(json.dumps({"action": "screen", "name": "trades"}))
            await trades.send(json.dumps({"action": "screen", "name": "trades"}))
            told = await next_of(trades, "book")
            assert told["part"] == "trades"
            assert (await next_of(idle, "book")) == told
            assert [m for m in await gather(child, 0.5) if m.get("type") == "book"] == []
            assert [c[0] for c in world.portfolio.calls] == ["upstox_book"]  # the child: no read
            for sock in (trades, idle, child):
                await sock.close()

    run(go())


def test_a_session_that_ended_is_closed_at_its_next_message_with_4401(tmp_path):
    async def go():
        async with World(tmp_path) as world:
            _, cookie, sock = await world.phone()
            hashed = sessions_module.id_hash(cookie.split("=", 1)[1])
            await world.service.store.write(
                lambda tx: sessions_module.revoke(tx, hashed, world.clock(), "logout")
            )
            await sock.send(json.dumps({"action": "subscribe", "symbols": [NIFTY]}))
            with pytest.raises(ConnectionClosed):
                await gather(sock, 3) and await sock.recv()
            assert (sock.close_code, sock.close_reason) == (4401, "Signed out on this device.")

    run(go())


def test_a_phone_that_floods_is_closed(tmp_path):
    async def go():
        async with World(tmp_path) as world:
            _, _, sock = await world.phone()
            with contextlib.suppress(ConnectionClosed):
                for _ in range(45):
                    await sock.send(json.dumps({"action": "screen", "name": "watchlist"}))
                await gather(sock, 3)
            await sock.wait_closed()
            assert sock.close_code == 1008

    run(go())


def test_the_market_route_gives_the_feed_state_and_the_indices_last_prices(tmp_path):
    from family_net import ask

    async def go():
        async with World(tmp_path) as world:
            world.feed.snapshots["NSE_INDEX|India VIX"] = frame(12.5, 13.0)
            _, cookie = await signed_in(world.service, VIEWER, world.clock())
            status, _, answer = await ask(world.port, "GET", "/family/api/market",
                                          headers={"Cookie": cookie})  # fmt: skip
            assert status == 200, answer
            market = answer["data"]
            assert market["feed"] in ("reconnecting", "realtime") and market["since"]
            assert [q["name"] for q in market["indices"]] == ["NIFTY 50", "NIFTY BANK", "INDIA VIX"]
            vix = market["indices"][2]
            assert (vix["ltp"], vix["change"], vix["at"]) == (12.5, -0.5, 1_763_265_600_000)
            assert market["indices"][0]["ltp"] is None  # no frame: null, never 0

    run(go())


def test_a_slow_phone_loses_frames_past_200_never_memory(tmp_path):
    """The bounded queue (ws.py): a socket whose phone does not take what is sent keeps 200
    messages and drops the rest; the service never waits for it."""
    from websockets.protocol import State

    from sudarshan.family import ws

    class Writer:
        def write(self, data):
            pass

    sock = ws.ViewerSocket(None, Writer(), session="ab" * 32, member_id=1, dialect=None,
                           registry=ws.Registry())  # fmt: skip
    sock.protocol.state = State.OPEN
    sent = [sock.send({"type": "market_data", "n": n}) for n in range(250)]
    assert sent.count(True) == ws.QUEUE == 200 and sock.dropped == 50 and sock.queue.qsize() == 200


def test_a_hundred_phones_come_and_go_and_leave_no_descriptor_seat_or_task(tmp_path):
    """fd-audit: a hundred rounds of a socket opened, signed in, following an index and closed,
    with the feed's bus connected, leave the descriptor table as it was and no seat behind; the
    stop then ends every task and closes the bus and the HTTP client."""
    from sudarshan.tests.fdwatch import FdWatch

    async def go():
        async with World(tmp_path) as world:
            _, cookie = await signed_in(world.service, VIEWER, world.clock())
            _, _, warm = await world.phone(cookie=cookie)
            await warm.close()
            await settled(world.service, 0)
            watch = FdWatch()
            for _ in range(100):
                _, _, sock = await world.phone(cookie=cookie)
                await sock.send(json.dumps({"action": "subscribe", "symbols": [NIFTY]}))
                await next_of(sock, "quotes")
                await sock.close()
            assert await settled(world.service, 0) == 0
            for _ in range(200):
                if not world.plane.dialect.seats:
                    break
                await asyncio.sleep(0.01)
            dialect = world.plane.dialect
            assert (dialect.seats, dialect.by_key, dialect._heard, dialect._pending) == (
                {},
                {},
                {},
                {},
            )
            await watch.aassert_clean()
            feed, http = world.plane.feed, world.plane.http
        others = [t.get_name() for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        assert others == [] and feed._sock is None and feed._ctx is None and http.is_closed

    run(go())
