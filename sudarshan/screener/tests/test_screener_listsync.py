"""The target watchlist sync (SV8) against a fake internal route: a list made on first sync, adds
and removes, idempotent retries, the owner's removals and manual items, a full list, a list gone."""

from __future__ import annotations

import asyncio

import pytest

from sudarshan.screener import listsync, store as store_module
from sudarshan.screener import membership as mem
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
