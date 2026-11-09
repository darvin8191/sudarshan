"""The target watchlist sync (SV8) against a fake internal route: a list made on first sync, adds
and removes, idempotent retries, the owner's removals and manual items, a full list, a list gone."""

from __future__ import annotations

import asyncio

import pytest

from sudarshan.screener import listsync
from sudarshan.screener import membership as mem
from sudarshan.screener import store as store_module
from sudarshan.screener.store import OWNER, ScreenerStore, Tx

NOW = 1_761_640_000_000
SID = 1


class FakeOpenAlgo:
    """OpenAlgo's lists: id -> {"name", "items": [{"id", "symbol", "exchange"}]}."""

    def __init__(self):
        self.lists: dict[int, dict] = {}
        self.next_list = 10
        self.next_item = 100
        self.calls: list[tuple] = []

    def _listing(self, list_id: int) -> dict:
        found = self.lists[list_id]
        return {
            "list_id": list_id,
            "name": found["name"],
            "items": [dict(i) for i in found["items"]],
        }

    def put(self, list_id: int, symbol: str, exchange: str) -> int:
        found = self.lists[list_id]
        for item in found["items"]:
            if (item["symbol"], item["exchange"]) == (symbol, exchange):
                return item["id"]
        self.next_item += 1
        found["items"].append({"id": self.next_item, "symbol": symbol, "exchange": exchange})
        return self.next_item

    def drop(self, list_id: int, item_id: int) -> None:
        found = self.lists[list_id]
        found["items"] = [i for i in found["items"] if i["id"] != item_id]

    async def watchlist_read(self, list_id):
        self.calls.append(("read", list_id))
        return self._listing(list_id) if list_id in self.lists else None

    async def watchlist_apply(self, *, list_id, create_name, add, remove, ref):
        self.calls.append(
            ("apply", list_id, create_name, tuple(a["symbol"] for a in add), tuple(remove), ref)
        )
        if list_id is None:
            self.next_list += 1
            list_id = self.next_list
            self.lists[list_id] = {"name": create_name, "items": []}
        for item in add:
            self.put(list_id, item["symbol"], item["exchange"])
        for item_id in remove:
            self.drop(list_id, item_id)
        return self._listing(list_id)


@pytest.fixture
def opened(tmp_path):
    made = ScreenerStore(tmp_path / "screener.db")
    made.write(OWNER, lambda tx: tx.screen_insert("Breakout", {"schema": 1}, "active", NOW))
    yield made
    made.close()
    store_module.stop()


def row(key: str, symbol: str, status="active", **extra) -> dict:
    base = mem.Row(key, symbol, "NSE", status, first_seen_at=NOW, last_seen_at=NOW)
    out = {n: getattr(base, n) for n in mem.Row.__dataclass_fields__}
    out.update(extra)
    return out


def put_rows(opened, list_id: int, *rows: dict, screen_id: int = SID) -> None:
    opened.write(OWNER, lambda tx: [tx.membership_put(screen_id, list_id, r, NOW) for r in rows])


def rows_of(opened, list_id: int) -> dict[str, dict]:
    return {r["key"]: r for r in opened.read(OWNER, lambda tx: tx.memberships(list_id=list_id))}


def run_sync(opened, link, list_id, create_name=None, ref="1:v1:20261028:15:4"):
    return asyncio.run(
        listsync.sync(
            opened, link, screen_id=SID, list_id=list_id, create_name=create_name, ref=ref, now=NOW
        )
    )


def test_a_screen_without_a_list_yet_gets_one_made_and_its_rows_move_to_it(opened):
    link = FakeOpenAlgo()
    put_rows(opened, 0, row("NSE_EQ|A", "AAA"), row("NSE_EQ|B", "BBB", status="exited"))
    result = run_sync(opened, link, 0, create_name="Screener: Breakout")
    assert result.status == "created" and result.list_id == 11 and result.added == ("NSE_EQ|A",)
    assert link.lists[11]["name"] == "Screener: Breakout"
    assert [i["symbol"] for i in link.lists[11]["items"]] == ["AAA"], "exited rows are not added"
    assert rows_of(opened, 0) == {} and rows_of(opened, 11)["NSE_EQ|A"]["in_list"] is True
    assert rows_of(opened, 11)["NSE_EQ|B"]["in_list"] is False
    assert opened.read(OWNER, lambda tx: tx.state(SID))["list_id"] == 11
    known = opened.read(OWNER, lambda tx: tx.list_items(11))
    assert (
        known["NSE_EQ|A"]["added_by_screener"] is True
        and known["NSE_EQ|A"]["openalgo_item_id"] == 101
    )
    assert run_sync(opened, link, 0).status == "nothing"


def test_adds_and_removes_are_sent_once_and_a_retry_changes_nothing(opened):
    link = FakeOpenAlgo()
    link.lists[7] = {"name": "Mine", "items": []}
    put_rows(opened, 7, row("NSE_EQ|A", "AAA"), row("NSE_EQ|B", "BBB", status="missing"))
    first = run_sync(opened, link, 7)
    assert first.status == "done" and set(first.added) == {"NSE_EQ|A", "NSE_EQ|B"}
    assert link.calls[-1][0] == "apply" and link.calls[-1][1] == 7
    again = run_sync(opened, link, 7)
    assert again.added == () and again.removed == () and link.calls[-1][0] == "read", (
        "nothing to send"
    )
    # B exits: it is removed by OpenAlgo's item id; A stays
    put_rows(
        opened, 7, row("NSE_EQ|B", "BBB", status="exited", in_list=True, added_by_screener=True)
    )
    third = run_sync(opened, link, 7)
    assert third.removed == ("NSE_EQ|B",)
    assert [i["symbol"] for i in link.lists[7]["items"]] == ["AAA"]
    assert rows_of(opened, 7)["NSE_EQ|B"]["in_list"] is False
    assert run_sync(opened, link, 7).removed == (), "a remove of an item already gone is nothing"


def test_the_owners_removal_dismisses_and_his_own_items_are_never_touched(opened):
    link = FakeOpenAlgo()
    link.lists[7] = {"name": "Mine", "items": []}
    link.put(7, "ZZZ", "NSE")  # the owner's own item, before the screener ever wrote
    put_rows(opened, 7, row("NSE_EQ|A", "AAA"), row("NSE_EQ|B", "BBB"))
    run_sync(opened, link, 7)
    assert {i["symbol"] for i in link.lists[7]["items"]} == {"ZZZ", "AAA", "BBB"}
    known = opened.read(OWNER, lambda tx: tx.list_items(7))
    assert known[listsync.owner_key("ZZZ", "NSE")]["added_by_screener"] is False
    # the owner removes A by hand
    item_a = next(i["id"] for i in link.lists[7]["items"] if i["symbol"] == "AAA")
    link.drop(7, item_a)
    result = run_sync(opened, link, 7)
    assert result.dismissed == ("NSE_EQ|A",) and result.added == ()
    rows = rows_of(opened, 7)
    assert rows["NSE_EQ|A"]["status"] == "dismissed" and rows["NSE_EQ|A"]["in_list"] is False
    assert {i["symbol"] for i in link.lists[7]["items"]} == {"ZZZ", "BBB"}, "A is not put back"
    # B exits: ZZZ is the owner's and stays whatever the rows say
    put_rows(opened, 7, row("NSE_EQ|B", "BBB", status="exited", in_list=True))
    result = run_sync(opened, link, 7)
    assert result.removed == ("NSE_EQ|B",) and [i["symbol"] for i in link.lists[7]["items"]] == [
        "ZZZ"
    ]


def test_a_full_list_holds_the_rest_and_a_list_the_owner_deleted_keeps_the_rows(opened):
    link = FakeOpenAlgo()
    link.lists[7] = {"name": "Mine", "items": []}
    for n in range(mem.LIST_MAX - 1):
        link.put(7, f"OWN{n}", "NSE")
    put_rows(opened, 7, row("NSE_EQ|A", "AAA"), row("NSE_EQ|B", "BBB"))
    result = run_sync(opened, link, 7)
    assert len(result.added) == 1 and len(result.held) == 1
    assert len(link.lists[7]["items"]) == mem.LIST_MAX
    del link.lists[7]
    gone = run_sync(opened, link, 7)
    assert gone.status == "list_gone" and len(rows_of(opened, 7)) == 2


def test_two_screens_feeding_one_list_keep_an_item_while_either_wants_it(opened):
    link = FakeOpenAlgo()
    link.lists[7] = {"name": "Mine", "items": []}
    opened.write(OWNER, lambda tx: tx.screen_insert("Second", {"schema": 1}, "active", NOW))
    put_rows(opened, 7, row("NSE_EQ|A", "AAA"), screen_id=1)
    put_rows(opened, 7, row("NSE_EQ|A", "AAA"), screen_id=2)
    run_sync(opened, link, 7)
    assert [i["symbol"] for i in link.lists[7]["items"]] == ["AAA"]
    put_rows(opened, 7, row("NSE_EQ|A", "AAA", status="exited", in_list=True), screen_id=1)
    result = run_sync(opened, link, 7)
    assert result.removed == () and [i["symbol"] for i in link.lists[7]["items"]] == ["AAA"]
    put_rows(opened, 7, row("NSE_EQ|A", "AAA", status="exited", in_list=True), screen_id=2)
    assert run_sync(opened, link, 7).removed == ("NSE_EQ|A",)
    assert link.lists[7]["items"] == []


# ---- release review HIGH-5: a retried sync never repeats what an earlier try did ---------------------
class FlakyOpenAlgo(FakeOpenAlgo):
    """Fails the first change after a list is made (its adds), once: a timeout or a 5xx."""

    def __init__(self):
        super().__init__()
        self.failed = False

    async def watchlist_apply(self, *, list_id, create_name, add, remove, ref):
        if list_id is not None and add and not self.failed:
            self.failed = True
            raise RuntimeError("watchlist: readtimeout")
        return await super().watchlist_apply(
            list_id=list_id, create_name=create_name, add=add, remove=remove, ref=ref
        )


def test_a_retry_after_the_list_was_made_does_not_make_it_again(tmp_path):
    from test_screener_runs import NOW as RUN_NOW
    from test_screener_runs import SPEC, due_for

    from sudarshan.screener import outbox, runs, spec
    from sudarshan.screener.runs import Judged, Member
    from sudarshan.screener.types import HIT, Outcome

    opened = ScreenerStore(tmp_path / "screener.db")
    try:
        target = {"list_id": None, "create_name": "Screener: Above hundred"}
        stored = spec.check(dict(SPEC, target=target))
        screen = opened.write(OWNER, lambda tx: tx.screen_insert("S", stored, "active", RUN_NOW))
        members = [
            Member("NSE_EQ|A", "AAA", "NSE", "NSE_EQ"),
            Member("NSE_EQ|B", "BBB", "NSE", "NSE_EQ"),
        ]
        judged = [Judged(m, Outcome(HIT, (), {}, 100), (12000, 11000)) for m in members]
        due = due_for(screen, 1)
        opened.write(OWNER, lambda tx: runs.record(
            tx, tx.screen(screen["id"]), tx.state(screen["id"]), due, judged, RUN_NOW))  # fmt: skip
        link = FlakyOpenAlgo()
        clock = {"now": RUN_NOW}
        sender = outbox.Sender(opened, link, lambda: clock["now"])
        for _ in range(4):  # the first try, then the retries as their backoff comes due
            asyncio.run(sender.tick())
            clock["now"] += 61_000
        state = opened.read(OWNER, lambda tx: tx.state(screen["id"]))
        rows = opened.read(OWNER, lambda tx: tx.memberships(screen_id=screen["id"]))
        assert len(link.lists) == 1, "every retry of the sync makes another 'Screener: ...' list"
        (made,) = link.lists
        assert state["list_id"] == made and {r["list_id"] for r in rows} == {made}
        assert sorted(i["symbol"] for i in link.lists[made]["items"]) == ["AAA", "BBB"]
        creates = [c for c in link.calls if c[0] == "apply" and c[1] is None]
        assert [c[5] for c in creates] == [listsync.create_ref(screen["id"])]
    finally:
        opened.close()
        store_module.stop()


class LostAnswer(FakeOpenAlgo):
    """Applies the first change and then loses its answer (the connection dropped after OpenAlgo
    had written it), once."""

    def __init__(self):
        super().__init__()
        self.lost = False

    async def watchlist_apply(self, *, list_id, create_name, add, remove, ref):
        answer = await super().watchlist_apply(
            list_id=list_id, create_name=create_name, add=add, remove=remove, ref=ref
        )
        if add and not self.lost:
            self.lost = True
            raise RuntimeError("watchlist: remoteprotocolerror")
        return answer


def test_an_add_that_landed_without_its_answer_stays_the_screeners(opened):
    link = LostAnswer()
    link.lists[7] = {"name": "Mine", "items": []}
    link.put(7, "ZZZ", "NSE")  # the owner's own item
    put_rows(opened, 7, row("NSE_EQ|A", "AAA"), row("NSE_EQ|B", "BBB"))
    with pytest.raises(RuntimeError):
        run_sync(opened, link, 7)
    run_sync(opened, link, 7)  # the outbox's retry: the adds are in the list already
    rows = rows_of(opened, 7)
    assert rows["NSE_EQ|A"]["added_by_screener"] is True, (
        "the screener's add is taken for the owner's"
    )
    assert rows["NSE_EQ|A"]["in_list"] is True and rows["NSE_EQ|B"]["in_list"] is True
    # both exit: the screener takes out what it put in, and only that
    put_rows(
        opened, 7,
        row("NSE_EQ|A", "AAA", status="exited", in_list=True),
        row("NSE_EQ|B", "BBB", status="exited", in_list=True),
    )  # fmt: skip
    run_sync(opened, link, 7)
    assert [i["symbol"] for i in link.lists[7]["items"]] == ["ZZZ"], (
        "the screener's items stay for good"
    )


class SealedOpenAlgo(FakeOpenAlgo):
    """Refuses a change whose sealed request would pass the internal routes' limit, as the
    service's Link does before it sends (link.Link._internal_call)."""

    def __init__(self):
        super().__init__()
        self.sizes: list[int] = []

    async def watchlist_apply(self, *, list_id, create_name, add, remove, ref):
        from sudarshan.autologin.handoff import seal
        from sudarshan.screener import contract
        from sudarshan.screener.link import LinkError
        from sudarshan.vault import SCREENER_INTERNAL_INFO, fernet_for

        args = {"list_id": list_id, "create_name": create_name, "add": list(add),
                "remove": list(remove), "ref": ref}  # fmt: skip
        blob = seal(fernet_for("f" * 64, SCREENER_INTERNAL_INFO), "watchlist_apply",
                    principal="owner", args=args)  # fmt: skip
        self.sizes.append(len(blob))
        if len(blob) > contract.INTERNAL_MAX_BYTES:
            raise LinkError(413, "watchlist_apply: the request is over the limit")
        return await super().watchlist_apply(
            list_id=list_id, create_name=create_name, add=add, remove=remove, ref=ref
        )


def test_a_sync_bigger_than_one_seal_goes_in_parts_each_under_the_limit(opened):
    from sudarshan.screener import contract

    link = SealedOpenAlgo()
    link.lists[7] = {"name": "Mine", "items": []}
    many = [row(f"NSE_EQ|INE{n:06d}A01018", f"SYMBOL{n:04d}") for n in range(mem.LIST_MAX)]
    put_rows(opened, 7, *many)
    result = run_sync(opened, link, 7)
    assert len(result.added) == mem.LIST_MAX
    assert len(link.lists[7]["items"]) == mem.LIST_MAX, "a sync over the seal is refused every try"
    assert len(link.sizes) >= 2 and max(link.sizes) <= contract.INTERNAL_MAX_BYTES
    known = opened.read(OWNER, lambda tx: tx.list_items(7))
    assert all(known[r["key"]]["added_by_screener"] for r in many)
    assert all(r["in_list"] for r in rows_of(opened, 7).values())
    # every part is applied once: the next sync has nothing to send
    sent = len(link.sizes)
    assert run_sync(opened, link, 7).added == () and len(link.sizes) == sent


# ---- release review HIGH-6: delete with "Also remove the instruments it added" ------------------------
@pytest.fixture
def service(tmp_path):
    from test_screener_runs import NOW as RUN_NOW

    from sudarshan.screener.service import Service
    from sudarshan.screener.tests.test_screener_service import FERNET, settings_for

    svc = Service(settings_for(1, SUDARSHAN_SCREENER_STATE_DIR=str(tmp_path)), FERNET, jobs={})
    svc.store = ScreenerStore(tmp_path / "screener.db")
    svc.clock = lambda: RUN_NOW
    yield svc
    svc.store.close()
    store_module.stop()


def _screen(service, name: str) -> dict:
    from test_screener_runs import NOW as RUN_NOW
    from test_screener_runs import SPEC

    from sudarshan.screener import spec

    return service.store.write(
        OWNER, lambda tx: tx.screen_insert(name, spec.check(SPEC), "active", RUN_NOW)
    )


def _delete(service, screen_id: int, remove_added: bool) -> dict:
    from sudarshan.screener.ops import REGISTRY

    return asyncio.run(REGISTRY["delete"](service, {"id": screen_id, "remove_added": remove_added}))


def _queued_sync(service, link, now: int) -> None:
    pending = service.store.read(OWNER, lambda tx: tx.outbox_due(now + 10**6, 10))
    sync = next(p for p in pending if p["kind"] == "list_sync")
    asyncio.run(
        listsync.sync(service.store, link, screen_id=sync["payload"]["screen_id"],
                      list_id=sync["payload"]["list_id"], create_name=None, ref=sync["ref"],
                      now=now + 1000)
    )  # fmt: skip


def test_remove_added_takes_the_screeners_items_out_of_the_list(service):
    from test_screener_runs import NOW as RUN_NOW

    store = service.store
    screen = _screen(service, "Above hundred")
    link = FakeOpenAlgo()
    link.lists[7] = {"name": "Mine", "items": []}
    link.put(7, "ZZZ", "NSE")  # the owner's own item
    put_rows(
        service.store, 7, row("NSE_EQ|A", "AAA"), row("NSE_EQ|B", "BBB"), screen_id=screen["id"]
    )
    asyncio.run(
        listsync.sync(store, link, screen_id=screen["id"], list_id=7, create_name=None, ref="r1",
                      now=RUN_NOW)
    )  # fmt: skip
    assert {i["symbol"] for i in link.lists[7]["items"]} == {"ZZZ", "AAA", "BBB"}

    answer = _delete(service, screen["id"], True)
    _queued_sync(service, link, RUN_NOW)
    items = sorted(i["symbol"] for i in link.lists[7]["items"])
    assert items == ["ZZZ"], "the two instruments the screener added are still in the list"
    assert answer == {"deleted": screen["id"], "removed_from_list": 2}
    kept = store.read(OWNER, lambda tx: tx.memberships(screen_id=screen["id"]))
    assert {r["key"]: r["status"] for r in kept} == {"NSE_EQ|A": "exited", "NSE_EQ|B": "exited"}, (
        "a deleted screen's membership rows are kept for the history (design 4.2)"
    )
    known = store.read(OWNER, lambda tx: tx.list_items(7))
    assert set(known) == {listsync.owner_key("ZZZ", "NSE")}


def test_delete_counts_only_what_it_takes_out_and_keeps_what_another_screen_wants(service):
    from test_screener_runs import NOW as RUN_NOW

    store = service.store
    first, second = _screen(service, "First"), _screen(service, "Second")
    link = FakeOpenAlgo()
    link.lists[7] = {"name": "Mine", "items": []}
    put_rows(store, 7, row("NSE_EQ|A", "AAA"), row("NSE_EQ|B", "BBB"), screen_id=first["id"])
    put_rows(store, 7, row("NSE_EQ|A", "AAA"), screen_id=second["id"])
    put_rows(store, 7, row("NSE_EQ|C", "CCC"), screen_id=first["id"])
    asyncio.run(
        listsync.sync(store, link, screen_id=first["id"], list_id=7, create_name=None, ref="r1",
                      now=RUN_NOW)
    )  # fmt: skip
    # C's add has not reached the list yet when the owner deletes: it is not in it to take out
    put_rows(store, 7, row("NSE_EQ|C", "CCC", in_list=False), screen_id=first["id"])
    link.drop(7, next(i["id"] for i in link.lists[7]["items"] if i["symbol"] == "CCC"))
    answer = _delete(service, first["id"], True)
    assert answer["removed_from_list"] == 1, "AAA stays for the other screen; CCC was never in it"
    _queued_sync(service, link, RUN_NOW)
    assert sorted(i["symbol"] for i in link.lists[7]["items"]) == ["AAA"]


def test_delete_without_remove_keeps_the_items_and_hides_the_screens_chips(service):
    from test_screener_runs import NOW as RUN_NOW

    from sudarshan.screener.ops import REGISTRY

    store = service.store
    first, second = _screen(service, "First"), _screen(service, "Second")
    link = FakeOpenAlgo()
    link.lists[7] = {"name": "Mine", "items": []}
    put_rows(store, 7, row("NSE_EQ|A", "AAA"), screen_id=first["id"])
    put_rows(store, 7, row("NSE_EQ|B", "BBB"), screen_id=second["id"])
    asyncio.run(
        listsync.sync(store, link, screen_id=first["id"], list_id=7, create_name=None, ref="r1",
                      now=RUN_NOW)
    )  # fmt: skip
    assert _delete(service, first["id"], False) == {"deleted": first["id"], "removed_from_list": 0}
    kept = store.read(OWNER, lambda tx: tx.memberships(screen_id=first["id"]))
    assert [(r["key"], r["status"]) for r in kept] == [("NSE_EQ|A", "active")], (
        "a deleted screen's membership rows are kept for the history (design 4.2)"
    )
    # the other screen's next sync of the same list leaves the kept item alone
    asyncio.run(
        listsync.sync(store, link, screen_id=second["id"], list_id=7, create_name=None, ref="r2",
                      now=RUN_NOW + 1000)
    )  # fmt: skip
    assert sorted(i["symbol"] for i in link.lists[7]["items"]) == ["AAA", "BBB"]
    rows = asyncio.run(REGISTRY["membership"](service, {"list": 7}))["rows"]
    assert [r["symbol"] for r in rows] == ["BBB"], "a deleted screen's rows still carry its chips"
