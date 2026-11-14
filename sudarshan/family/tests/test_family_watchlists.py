"""F7 (M8.15, M20.6) and the mid-point review's M3 and L7: the owner's share ops, the lists a member
sees through OpenAlgo's internal route, how often they are read, and what leaves the screen when a
share ends.

Every server binds an ephemeral port on 127.0.0.1 (never 5585, 5000 or 5581). OpenAlgo's route is
the fake in family_planekit.py: it opens the real seal with the internal key and, as the real one
does since M3, answers only what its own note shares with the member asked about.
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet
from family_net import ask, fast_hasher, keys, running, sealed, settings
from family_planekit import (
    CHILD,
    MONDAY,
    VIEWER,
    Clock,
    FakeOpenAlgo,
    FakePortfolio,
    books_answer,
    ist,
    list_row,
    signed_in,
)
from family_testkit import close
from websockets.asyncio.client import connect

from sudarshan.autologin.handoff import NonceBook, open_sealed
from sudarshan.family import contract, watchlists
from sudarshan.family.books import PlaneKeys
from sudarshan.family.internal_client import InternalClient
from sudarshan.family.portfolio_client import Refusal
from sudarshan.family.service import FamilyService
from sudarshan.family.viewer_ws import NOT_AVAILABLE
from sudarshan.family.watchlists import Watchlists, list_ref
from sudarshan.tasks import cancel_and_wait


def run(coro):
    try:
        return asyncio.run(coro)
    finally:
        close(None)


def dead_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class World:
    """A running service with its data plane: empty books from a fake portfolio, OpenAlgo's
    internal route faked, and the feed at a port nothing listens on."""

    def __init__(self, root: Path):
        self.root, self.clock = root, Clock(ist(MONDAY, 10))

    async def __aenter__(self) -> World:
        portfolio_key, internal_key = Fernet(Fernet.generate_key()), Fernet(Fernet.generate_key())
        self.portfolio = FakePortfolio(portfolio_key, books_answer({}, self.clock))
        self.openalgo = FakeOpenAlgo(internal_key)
        purl, ourl, port = await self.portfolio.start(), await self.openalgo.start(), dead_port()
        made = settings(self.root, portfolio_url=purl, openalgo_url=ourl,
                        feed_url=f"http://127.0.0.1:{port}", feed_zmq=f"tcp://127.0.0.1:{port}")  # fmt: skip
        self.service = FamilyService(made, keys(), hasher=fast_hasher(), clock=self.clock, jobs={},
                                     data_plane=PlaneKeys(portfolio_key, internal_key))  # fmt: skip
        self._running = running(self.service)
        await self._running.__aenter__()
        self.port = self.service.listener.port
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            await self._running.__aexit__(*exc)
        finally:
            await self.openalgo.close()
            await self.portfolio.close()

    @property
    def lists(self) -> Watchlists:
        return self.service.plane.lists

    async def admin(self, op: str, **fields) -> tuple[int, dict]:
        status, _, answer = await ask(self.port, "POST", f"{contract.ADMIN_PREFIX}{op}",
                                      body=sealed(self.service.keys.admin, op, **fields))  # fmt: skip
        return status, answer

    async def share(self, list_id: int, **target) -> dict:
        """The owner shares list ``list_id`` (and OpenAlgo's note says so too, as the bridge
        writes it when the service confirms)."""
        status, answer = await self.admin("share_set", kind="watchlist", ref=str(list_id),
                                          level="view", **target)  # fmt: skip
        assert status == 200, answer
        if target.get("member_id") is not None:
            self.openalgo.shared.add(("member", target["member_id"], list_id))
        elif target.get("role") is not None:
            self.openalgo.shared.add(("role", target["role"], list_id))
        else:
            self.openalgo.shared.add(("all", None, list_id))
        return answer["data"]

    async def get(self, path: str, cookie: str) -> tuple[int, dict]:
        status, _, answer = await ask(self.port, "GET", f"/family/api{path}",
                                      headers={"Cookie": cookie})  # fmt: skip
        return status, answer


async def until(check, seconds: float = 5.0) -> None:
    async with asyncio.timeout(seconds):
        while not check():
            await asyncio.sleep(0.01)


async def next_of(sock, kind: str, seconds: float = 5.0, where=None) -> dict:
    async with asyncio.timeout(seconds):
        while True:
            message = json.loads(await sock.recv())
            if message.get("type") == kind and (where is None or where(message)):
                return message


# ---- the share ops ---------------------------------------------------------------------------------
def test_a_watchlist_shares_ref_is_openalgos_own_id_and_nothing_else():
    assert [list_ref(r) for r in ("7", "10", "2147483647")] == [7, 10, 2147483647]
    for bad in ("007", "0", "-1", " 7", "7 ", "+7", "७", "2147483648", "99999999999", "",
                "1e3", "7.0", 7, None):  # fmt: skip
        assert list_ref(bad) is None, bad


def test_the_owner_shares_changes_and_removes_through_the_admin_door(tmp_path):
    async def go():
        async with World(tmp_path) as world:
            member, _ = await signed_in(world.service, VIEWER, world.clock())
            mid = member["id"]
            share = await world.share(7, member_id=mid)
            assert {k: share[k] for k in ("kind", "ref", "member_id", "role", "level",
                                          "expires_at", "version")} == {
                "kind": "watchlist", "ref": "7", "member_id": mid, "role": None, "level": "view",
                "expires_at": None, "version": 1}  # fmt: skip
            again = await world.admin("share_set", kind="watchlist", ref="7", member_id=mid,
                                      level="restricted")  # fmt: skip
            assert (again[0], again[1]["code"]) == (409, "conflict")  # it exists: name its version
            until_ms = world.clock() + 3_600_000
            status, answer = await world.admin("share_set", kind="watchlist", ref="7",
                                               member_id=mid, level="restricted",
                                               expires_at=until_ms, version=1)  # fmt: skip
            changed = answer["data"]
            assert (changed["id"], changed["level"], changed["expires_at"], changed["version"]) == (
                share["id"], "restricted", until_ms, 2)  # fmt: skip
            part = (await world.admin("share_set", kind="book", ref="positions", role="child",
                                      level="view"))[1]["data"]  # fmt: skip
            listed = (await world.admin("share_list"))[1]["data"]
            assert [s["id"] for s in listed] == [share["id"], part["id"]]
            assert (await world.admin("share_remove", id=share["id"], version=1))[0] == 409
            removed = await world.admin("share_remove", id=share["id"], version=2)
            assert removed == (200, {"status": "success", "data": {"ok": True, "words": None}})
            assert (await world.admin("share_remove", id=share["id"], version=2))[0] == 404
            kinds = await world.service.store.read(
                lambda tx: [r["kind"] for r in tx.rows("audit", "id") if "share" in r["kind"]]
            )
            assert kinds == ["share_set", "share_set", "share_set", "share_removed"]

    run(go())


@pytest.mark.parametrize(
    ("fields", "status", "code", "field"),
    [
        ({"kind": "tab"}, 400, "invalid", "kind"),
        ({"ref": "007"}, 400, "invalid", "ref"),
        ({"ref": "positions"}, 400, "invalid", "ref"),
        ({"kind": "book", "ref": "7"}, 400, "invalid", "ref"),
        ({"level": "edit"}, 400, "invalid", "level"),
        ({"role": "viewer"}, 400, "invalid", "member_id"),  # a member and a role
        ({"member_id": None, "role": "owner"}, 400, "invalid", "role"),
        ({"member_id": True}, 400, "invalid", "member_id"),
        ({"expires_at": 1_000}, 400, "invalid", "expires_at"),  # already past
        ({"version": 1}, 409, "conflict", None),  # a new share has no version yet
        ({"member_id": 999}, 404, "not_found", None),
    ],
)  # fmt: skip
def test_a_share_the_contract_does_not_allow_is_refused_and_nothing_is_written(
    tmp_path, fields, status, code, field
):
    async def go():
        async with World(tmp_path) as world:
            member, _ = await signed_in(world.service, VIEWER, world.clock())
            args = {"kind": "watchlist", "ref": "7", "level": "view", "member_id": member["id"],
                    **fields}  # fmt: skip
            got, answer = await world.admin("share_set", **args)
            assert (got, answer["code"], answer.get("field")) == (status, code, field), answer
            assert (await world.admin("share_list"))[1]["data"] == []

    run(go())


# ---- the lists a member sees -------------------------------------------------------------------------
def test_a_member_sees_the_lists_shared_with_them_or_their_role_and_nothing_else(tmp_path):
    async def go():
        async with World(tmp_path) as world:
            a, cookie = await signed_in(world.service, VIEWER, world.clock())
            b, _ = await signed_in(world.service, VIEWER, world.clock())
            fake = world.openalgo
            fake.add(7, "Swing", [list_row(1), list_row(2)])
            fake.add(8, "Banks", [list_row(3)])
            fake.add(9, "Another member's", [list_row(4)])
            fake.add(10, "A child's", [list_row(5)])
            await world.share(7, member_id=a["id"])
            await world.share(8, role="viewer")
            await world.share(9, member_id=b["id"])
            await world.share(10, role="child")
            status, answer = await world.get("/watchlists", cookie)
            assert status == 200, answer
            assert answer["data"] == [{"id": 7, "name": "Swing", "count": 2},
                                      {"id": 8, "name": "Banks", "count": 1}]  # fmt: skip
            assert fake.calls == [{"list_ids": [7, 8], "member_id": a["id"], "role": "viewer"}]
            status, answer = await world.get("/watchlists/7", cookie)
            assert answer["data"] == {
                "id": 7, "name": "Swing", "refreshed_at": world.clock(),
                "rows": [{"symbol": "LIST1", "exchange": "NSE", "name": "List Stock 1"},
                         {"symbol": "LIST2", "exchange": "NSE", "name": "List Stock 2"}],
            }  # fmt: skip
            assert "NSE_EQ" not in json.dumps(answer)  # no instrument key, tick or lot size
            for other in ("9", "10", "11"):
                status, answer = await world.get(f"/watchlists/{other}", cookie)
                assert (status, answer["code"], answer["message"]) == (
                    403, "not_shared", "Not shared with you.")  # fmt: skip
            assert (await world.get("/watchlists/x7", cookie))[1]["code"] == "invalid"
            assert len(fake.calls) == 1 and fake.refused == []

    run(go())


def test_a_share_with_neither_a_member_nor_a_role_is_every_members(tmp_path):
    """The owner's Family page shares a list with every member as no member and no role."""

    async def go():
        async with World(tmp_path) as world:
            a, cookie_a = await signed_in(world.service, VIEWER, world.clock())
            c, cookie_c = await signed_in(world.service, CHILD, world.clock(), role="child")
            world.openalgo.add(7, "Swing", [list_row(1)])
            share = await world.share(7, member_id=None, role=None)
            assert (share["member_id"], share["role"]) == (None, None)
            for cookie in (cookie_a, cookie_c):
                assert [x["id"] for x in (await world.get("/watchlists", cookie))[1]["data"]] == [7]
            assert [(x["member_id"], x["role"]) for x in world.openalgo.calls] == [
                (a["id"], "viewer"), (c["id"], "child")]  # fmt: skip

    run(go())


def test_a_list_openalgo_answers_as_gone_is_not_shown(tmp_path):
    """The family service shows only what the route answers: a list OpenAlgo's note does not
    name for the member (unshared there, or a later list given the same id) is not shown."""

    async def go():
        async with World(tmp_path) as world:
            a, cookie = await signed_in(world.service, VIEWER, world.clock())
            world.openalgo.add(7, "Swing", [list_row(1)])
            world.openalgo.add(8, "Banks", [list_row(2)])
            await world.share(7, member_id=a["id"])
            await world.share(8, member_id=a["id"])
            world.openalgo.shared.discard(("member", a["id"], 8))
            assert [x["id"] for x in (await world.get("/watchlists", cookie))[1]["data"]] == [7]
            assert (await world.get("/watchlists/8", cookie))[0] == 403

    run(go())


def test_no_share_reads_nothing_and_openalgo_down_with_nothing_kept_is_said(tmp_path):
    async def go():
        async with World(tmp_path) as world:
            a, cookie = await signed_in(world.service, VIEWER, world.clock())
            assert (await world.get("/watchlists", cookie))[1]["data"] == []
            assert world.openalgo.calls == []  # nothing shared: OpenAlgo is not asked
            world.openalgo.add(7, "Swing", [list_row(1)])
            await world.share(7, member_id=a["id"])
            world.openalgo.down = True
            status, answer = await world.get("/watchlists", cookie)
            assert (status, answer["code"], answer["message"]) == (
                500, "failed", "Something went wrong on the server. Try again in a minute.")  # fmt: skip

    run(go())


def test_a_page_reads_the_lists_at_most_once_a_minute(tmp_path):
    """Design 7.2: the shared-list route is called at most every 60 s."""

    async def go():
        async with World(tmp_path) as world:
            a, cookie = await signed_in(world.service, VIEWER, world.clock())
            world.openalgo.add(7, "Swing", [list_row(1)])
            await world.share(7, member_id=a["id"])
            for _ in range(5):
                assert (await world.get("/watchlists", cookie))[0] == 200
                assert (await world.get("/watchlists/7", cookie))[0] == 200
                world.clock.at += 11_000  # 55 s in all
            assert len(world.openalgo.calls) == 1
            world.clock.at += 5_000  # a minute since the read
            assert (await world.get("/watchlists/7", cookie))[0] == 200
            assert len(world.openalgo.calls) == 2

    run(go())


def test_an_open_watchlist_screen_reads_once_a_minute_and_no_other_screen_reads(monkeypatch):
    monkeypatch.setattr(watchlists, "LOOK_S", 0.01)

    async def go():
        clock, reads = Clock(ist(MONDAY, 10)), []

        class Store:
            async def read(self, fn):
                return [7]

        class Client:
            async def lists(self, ids, *, member_id, role):
                reads.append(clock())
                return {"lists": [{"id": 7, "name": "Swing", "created_at": None, "rows": []}],
                        "gone": []}  # fmt: skip

        dialect = SimpleNamespace(seats={}, settle=lambda member: None)
        lists = Watchlists(Store(), Client(), SimpleNamespace(put=lambda found: list(found)),
                           clock=clock)  # fmt: skip
        lists.dialect = dialect
        seat = SimpleNamespace(member={"id": 3, "role": "viewer"}, screen="watchlist")
        dialect.seats["sock"] = seat
        task = asyncio.create_task(lists.run())
        try:
            lists.opened(seat)  # the screen opens: one read
            lists.opened(seat)  # and again at once: the read on its way is the one
            await until(lambda: len(reads) == 1)
            for _ in range(10):  # 50 s more, looked at every 5 s: nothing read
                clock.at += 5_000
                await asyncio.sleep(0.03)
            assert len(reads) == 1
            clock.at += 10_000  # a minute since the read
            await until(lambda: len(reads) == 2)
            seat.screen = "positions"
            clock.at += 120_000
            lists.opened(seat)
            await asyncio.sleep(0.1)
            assert len(reads) == 2  # no watchlist screen open: nothing read
        finally:
            await cancel_and_wait(task)
            await lists.close()

    run(go())


def test_an_ended_share_leaves_the_screen_at_once_even_while_openalgo_cannot_be_read(tmp_path):
    async def go():
        async with World(tmp_path) as world:
            a, cookie = await signed_in(world.service, VIEWER, world.clock())
            world.openalgo.add(7, "Swing", [list_row(1)])
            world.openalgo.add(8, "Banks", [list_row(2)])
            world.openalgo.add(9, "Brief", [list_row(3)])
            s7 = await world.share(7, member_id=a["id"])
            await world.share(8, role="viewer")
            status, answer = await world.admin(
                "share_set", kind="watchlist", ref="9", member_id=a["id"], level="view",
                expires_at=world.clock() + 30_000)  # fmt: skip
            world.openalgo.shared.add(("member", a["id"], 9))
            ids = [x["id"] for x in (await world.get("/watchlists", cookie))[1]["data"]]
            assert ids == [7, 8, 9]
            world.openalgo.down = True
            assert (await world.admin("share_remove", id=s7["id"], version=1))[0] == 200
            status, answer = await world.get("/watchlists", cookie)
            assert status == 200 and [x["id"] for x in answer["data"]] == [8, 9]  # cut, not read
            assert (await world.get("/watchlists/7", cookie))[1]["code"] == "not_shared"
            world.clock.at += 61_000  # the brief share has ended too
            assert [x["id"] for x in (await world.get("/watchlists", cookie))[1]["data"]] == [8]

    run(go())


def test_a_phone_follows_a_shared_lists_rows_and_lets_go_when_the_share_ends(tmp_path):
    async def go():
        async with World(tmp_path) as world:
            a, cookie = await signed_in(world.service, VIEWER, world.clock())
            world.openalgo.add(7, "Swing", [list_row(1)])
            s7 = await world.share(7, member_id=a["id"])
            sock = await connect(f"ws://127.0.0.1:{world.port}/family/ws",
                                 additional_headers={"Cookie": cookie})  # fmt: skip
            try:
                await sock.send(json.dumps({"action": "authenticate", "family": True}))
                await next_of(sock, "auth")
                await sock.send(json.dumps({"action": "screen", "name": "watchlist"}))
                await until(lambda: world.lists.known(a["id"]))  # the screen read the lists
                row = {"symbol": "LIST1", "exchange": "NSE"}
                await sock.send(json.dumps({"action": "subscribe", "symbols": [row], "mode": 1}))
                taken = await next_of(sock, "subscribe")
                assert (taken["status"], taken["symbols"]) == ("success", [row])
                assert "NSE_EQ|LIST0000001" in world.service.plane.dialect.by_key
                world.openalgo.shared.clear()
                assert (await world.admin("share_remove", id=s7["id"], version=1))[0] == 200
                gone = await next_of(sock, "subscribe", where=lambda m: m["status"] == "error")
                assert gone == {"type": "subscribe", "status": "error", "message": NOT_AVAILABLE,
                                "symbols": [row]}  # fmt: skip
                assert "NSE_EQ|LIST0000001" not in world.service.plane.dialect.by_key
            finally:
                await sock.close()

    run(go())


def test_more_than_fifty_shares_ask_for_the_first_fifty(tmp_path):
    async def go():
        async with World(tmp_path) as world:
            a, cookie = await signed_in(world.service, VIEWER, world.clock())
            for list_id in range(1, 53):
                world.openalgo.add(list_id, f"List {list_id}", [])
                await world.share(list_id, member_id=a["id"])
            assert len((await world.get("/watchlists", cookie))[1]["data"]) == 50
            (call,) = world.openalgo.calls
            assert call["list_ids"] == list(range(1, 51))

    run(go())


def test_the_lists_and_shares_leave_no_descriptor_and_a_stop_leaves_no_task(tmp_path):
    """fd-audit (F7): a hundred reads of the lists and share changes leave the descriptor table as
    it was; the stop ends every task, the lists' own included."""
    from sudarshan.tests.fdwatch import FdWatch

    async def go():
        async with World(tmp_path) as world:
            a, cookie = await signed_in(world.service, VIEWER, world.clock())
            world.openalgo.add(7, "Swing", [list_row(1)])
            share = await world.share(7, member_id=a["id"])
            await world.get("/watchlists", cookie)  # warm up
            watch = FdWatch()
            for _ in range(100):
                world.clock.at += 61_000
                assert (await world.get("/watchlists", cookie))[0] == 200
                status, answer = await world.admin("share_set", kind="watchlist", ref="7",
                                                   member_id=a["id"], level="view",
                                                   version=share["version"])  # fmt: skip
                share = answer["data"]
            await watch.aassert_clean()
            assert len(world.openalgo.calls) >= 100
            assert world.lists._tasks == set() or all(t.done() for t in world.lists._tasks)
        others = [t.get_name() for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        assert others == []

    run(go())


# ---- the internal client ---------------------------------------------------------------------------
def client_with(handler, clock=lambda: 0.0) -> tuple[InternalClient, httpx.AsyncClient, Fernet]:
    fernet = Fernet(Fernet.generate_key())
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return InternalClient("http://127.0.0.1:1", fernet, http, clock), http, fernet


def test_the_client_seals_the_member_and_keeps_only_what_is_asked_and_well_formed():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        data = {"lists": [
            {"id": 7, "name": "Swing", "created_at": "2026-11-02 09:15:00",
             "rows": [list_row(1), {"symbol": "", "exchange": "NSE"}, "junk"]},
            {"id": 99, "name": "Not asked for", "created_at": None, "rows": []},
            {"id": "8", "name": "Bad id", "rows": []},
        ], "gone": [8]}  # fmt: skip
        return httpx.Response(200, json={"status": "success", "data": data})

    async def go():
        client, http, fernet = client_with(handler)
        try:
            found = await client.lists([7, 8, 7], member_id=4, role="child")
        finally:
            await http.aclose()
        assert [lst["id"] for lst in found["lists"]] == [7] and found["gone"] == [8]
        assert found["lists"][0]["rows"] == [list_row(1)]
        (request,) = seen
        assert request.url.path == contract.INTERNAL_PATH
        message = open_sealed(fernet, request.content, op="watchlists", seen=NonceBook(8))
        assert (message["principal"], message["list_ids"], message["member_id"],
                message["role"]) == ("sudarshan-family", [7, 8], 4, "child")  # fmt: skip
        with pytest.raises(ValueError):
            await client.lists([], member_id=4, role="child")
        with pytest.raises(ValueError):
            await client.lists([1], member_id=4, role="owner")

    run(go())


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(500, json={"status": "error", "code": "failed", "message": "Not now."}),
        httpx.Response(403, json={"status": "error", "code": "forbidden", "message": "No."}),
        httpx.Response(200, content=b"<html>not json</html>"),
        httpx.Response(200, json={"status": "success", "data": None}),
        "down",
    ],
)  # fmt: skip
def test_a_failed_read_is_one_refusal_said_once_in_ten_minutes(answer, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        if answer == "down":
            raise httpx.ConnectError("refused", request=request)
        return answer

    async def go():
        now = [0.0]
        client, http, _ = client_with(handler, clock=lambda: now[0])
        try:
            with caplog.at_level("WARNING", logger="sudarshan.family"):
                for step in (0.0, 300.0, 599.0, 601.0):
                    now[0] = step
                    with pytest.raises(Refusal) as refused:
                        await client.lists([7], member_id=4, role="viewer")
                    assert refused.value.state == "unreachable"
        finally:
            await http.aclose()
        assert len([r for r in caplog.records if r.name == "sudarshan.family"]) == 2
        assert client.reachable is (False if answer == "down" else answer.status_code < 500)

    run(go())


# ---- review L7: one list of forwarded headers ------------------------------------------------------
#: Every header nginx can set on a proxied request (OpenAlgo's internal route refuses these five).
NGINX_HEADERS = ("X-Forwarded-For", "X-Real-IP", "Forwarded", "X-Forwarded-Proto",
                 "X-Forwarded-Host")  # fmt: skip


def test_the_contract_lists_every_header_nginx_sets():
    assert sorted(contract.FORWARDED_HEADERS) == sorted(NGINX_HEADERS)


@pytest.mark.parametrize("header", NGINX_HEADERS)
def test_every_header_nginx_sets_is_refused_at_the_sealed_doors_and_health(tmp_path, header):
    async def go():
        service = FamilyService(settings(tmp_path), keys(), hasher=fast_hasher(), jobs={})
        async with running(service):
            port, more = service.listener.port, {header: "203.0.113.7"}
            body = sealed(service.keys.admin, "member_list")
            status, _, answer = await ask(port, "POST", "/v1/admin/member_list", body=body,
                                          headers=more)  # fmt: skip
            assert (status, answer["code"]) == (404, "not_found")
            assert (await ask(port, "GET", "/health", headers=more))[0] == 404
            assert (await ask(port, "GET", "/health"))[0] == 200

    run(go())
