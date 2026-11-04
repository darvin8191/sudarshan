"""Import, undo and export through the sealed API against a temporary store (M7.22, design 4.11)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from test_portfolio_service import FERNET, post, sealed, settings_for

from sudarshan.portfolio import store as store_module
from sudarshan.portfolio.book import Book
from sudarshan.portfolio.service import Service
from sudarshan.portfolio.store import DB_FILENAME, PortfolioStore, Scope

NOW = 1_793_000_000_000  # 26 Oct 2026
HOUR = 3_600_000
OWNER = Scope.owner()
FILE = (
    "date,action,symbol,quantity,price,account\n"
    "2026-10-01,BUY,RELIANCE,10,1000,Zerodha\n"
    "2026-10-05,BUY,RELIANCE,5,1100,Zerodha\n"
    "2026-10-07,BUY,NOSUCH,5,1100,Zerodha\n"
)


@pytest.fixture(autouse=True)
def _stop_thread():
    yield
    store_module.stop()


class Rig:
    """A service with a real store in a temporary folder, its sealed API on a free port."""

    def __init__(self, tmp_path):
        self.svc = Service(settings_for(tmp_path), FERNET, jobs={})
        self.svc.store = PortfolioStore(tmp_path / "s" / DB_FILENAME, now=NOW)
        self.svc.clock = lambda: self.now
        self.now = NOW
        self.book = Book(self.svc.store)
        me = self.book.members(OWNER)[0]
        self.account = self.book.account_create(OWNER, "a-1", me["id"], "Zerodha", "demat", now=NOW)
        self.share = self.book.asset_create(
            OWNER, "s-1", "listed", now=NOW, exchange="NSE", symbol="RELIANCE",
            isin="INE002A01018", name="Reliance Industries",
        )  # fmt: skip
        self.tab = self.book.tab_create(OWNER, "t-1", "Long term", "stocks", now=NOW)

    def version(self) -> int:
        return next(t for t in self.book.tabs(OWNER) if t["id"] == self.tab["id"])["version"]

    async def call(self, op: str, **fields) -> dict:
        async with httpx.AsyncClient(trust_env=False) as client:
            answer = await client.post(
                f"http://127.0.0.1:{self.svc.api.port}/v1/{op}",
                content=sealed(op, **fields),
                timeout=20,
            )
        return answer.json()

    async def preview(self, text: str = FILE, fmt: str = "generic") -> dict:
        return await self.call(
            "import_preview", id=self.tab["id"], format=fmt, filename="t.csv", text=text
        )


def scenario_of(test):
    def wrapped(tmp_path):
        async def go():
            rig = Rig(tmp_path)
            await rig.svc.api.start(0)
            try:
                await test(rig)
            finally:
                await rig.svc.api.close()
                rig.svc.store.close()

        asyncio.run(go())

    wrapped.__name__ = test.__name__
    return wrapped


@scenario_of
async def test_preview_writes_nothing_to_the_ledger_and_keeps_no_file(rig):
    before = rig.version()
    answer = await rig.preview()
    data = answer["data"]
    assert (data["rows_total"], data["rows_new"], data["rows_dup"], data["rows_bad"]) == (
        3,
        2,
        0,
        1,
    )
    assert data["tab_version"] == before and data["expires_at"] == NOW + 24 * HOUR
    assert rig.version() == before
    assert rig.book.activities_list(OWNER, rig.tab["id"])["items"] == []
    stored = rig.svc.store.read(OWNER, lambda tx: tx.get("import_batch", data["batch_id"]))
    assert set(stored["preview"]) == {"filename", "rows"}
    assert "date,action,symbol" not in json.dumps(stored["preview"])  # normalised rows only
    assert all("key" in r for r in stored["preview"]["rows"])


@scenario_of
async def test_commit_writes_every_row_with_its_batch_and_the_same_file_is_then_all_duplicates(rig):
    shown = (await rig.preview())["data"]
    done = await rig.call("import_commit", id=shown["batch_id"], version=shown["tab_version"])
    assert done["status"] == "success"
    assert done["data"]["added"] == 2 and done["data"]["tab_version"] == shown["tab_version"] + 1
    rows = rig.book.activities_list(OWNER, rig.tab["id"])["items"]
    assert [r["source"] for r in rows] == ["import", "import"]
    assert {r["batch_id"] for r in rows} == {shown["batch_id"]}
    assert all(r["import_key"] for r in rows) and sorted(r["seq"] for r in rows) == [1, 2]
    again = (await rig.preview())["data"]
    assert (again["rows_new"], again["rows_dup"]) == (0, 2)
    refused = await rig.call("import_commit", id=again["batch_id"], version=again["tab_version"])
    assert refused["code"] == "bad_request" and "nothing new" in refused["message"]


@scenario_of
async def test_commit_after_the_tab_moved_says_so_and_writes_nothing(rig):
    shown = (await rig.preview())["data"]
    rig.book.activity_create(
        OWNER, rig.tab["id"], rig.version(),
        {"kind": "FEE", "trade_date": 20261001, "account_id": rig.account["id"], "amount_paise": 100},
        "r-1", now=NOW,
    )  # fmt: skip
    answer = await rig.call("import_commit", id=shown["batch_id"], version=shown["tab_version"])
    assert answer["code"] == "version_conflict" and "changed" in answer["message"].lower()
    assert len(rig.book.activities_list(OWNER, rig.tab["id"])["items"]) == 1


@scenario_of
async def test_a_preview_older_than_a_day_is_dropped(rig):
    shown = (await rig.preview())["data"]
    rig.now = NOW + 25 * HOUR
    answer = await rig.call("import_commit", id=shown["batch_id"], version=shown["tab_version"])
    assert answer["code"] == "not_found" and "no longer available" in answer["message"]
    kept = rig.svc.store.read(OWNER, lambda tx: tx.rows("import_batch"))
    assert kept == []


@scenario_of
async def test_a_preview_can_be_discarded_and_then_cannot_be_committed(rig):
    shown = (await rig.preview())["data"]
    gone = await rig.call("import_discard", id=shown["batch_id"])
    assert gone["data"] == {
        "batch_id": shown["batch_id"], "status": "discarded", "removed": 0, "tab_version": None,
    }  # fmt: skip
    again = await rig.call("import_commit", id=shown["batch_id"], version=shown["tab_version"])
    assert again["code"] == "not_found"


@scenario_of
async def test_undo_names_the_entry_that_depends_on_the_batch_then_succeeds_once_it_is_gone(rig):
    shown = (await rig.preview())["data"]
    done = (await rig.call("import_commit", id=shown["batch_id"], version=shown["tab_version"]))[
        "data"
    ]
    sale = rig.book.activity_create(
        OWNER, rig.tab["id"], done["tab_version"],
        {"kind": "SELL", "trade_date": 20261010, "account_id": rig.account["id"],
         "asset_id": rig.share["id"], "qty_u": 12_000_000, "price_u": 12_000_000},
        "r-2", now=NOW,
    )  # fmt: skip
    version = sale["tab_version"]
    stuck = await rig.call("import_discard", id=shown["batch_id"], version=version)
    assert stuck["code"] == "refused" and stuck["entry_id"] == sale["activity"]["id"]
    assert "sale" in stuck["message"].lower() and "Reliance" in stuck["message"]
    assert len(rig.book.activities_list(OWNER, rig.tab["id"])["items"]) == 3  # nothing was removed
    version = rig.book.activity_delete(OWNER, sale["activity"]["id"], version, now=NOW)[
        "tab_version"
    ]
    undone = await rig.call("import_discard", id=shown["batch_id"], version=version)
    assert undone["data"] == {
        "batch_id": shown["batch_id"], "status": "undone", "removed": 2, "tab_version": version + 1,
    }  # fmt: skip
    assert rig.book.activities_list(OWNER, rig.tab["id"])["items"] == []
    back = (await rig.preview())["data"]
    assert back["rows_new"] == 2  # the keys went with the rows: the file can be imported again


@scenario_of
async def test_undo_needs_the_tab_version(rig):
    shown = (await rig.preview())["data"]
    await rig.call("import_commit", id=shown["batch_id"], version=shown["tab_version"])
    answer = await rig.call("import_discard", id=shown["batch_id"])
    assert answer["code"] == "bad_request"
    stale = await rig.call("import_discard", id=shown["batch_id"], version=shown["tab_version"])
    assert stale["code"] == "version_conflict"


@scenario_of
async def test_refusals_come_in_the_owners_words(rig):
    kind = await rig.preview(fmt="pdf")
    assert kind["code"] == "bad_request" and "Choose the kind of file" in kind["message"]
    missing = await rig.call("import_preview", id=999, format="generic", filename="a", text=FILE)
    assert missing["code"] == "not_found"
    junk = await rig.preview(text="nothing,useful\n1,2\n")
    assert junk["status"] == "error" and junk["code"] == "bad_request"
    for answer in (kind, missing, junk):
        for term in ("HTTP", "Traceback", "Error", "500", "sqlite"):
            assert term not in answer["message"]


@scenario_of
async def test_export_reads_back_as_the_same_ledger(rig):
    shown = (await rig.preview())["data"]
    await rig.call("import_commit", id=shown["batch_id"], version=shown["tab_version"])
    out = (await rig.call("export_csv", id=rig.tab["id"]))["data"]
    assert out["filename"] == f"SUDARSHAN_Portfolio_{rig.tab['id']}_2026-10-26.csv"
    again = (await rig.preview(text=out["text"]))["data"]
    assert (again["rows_new"], again["rows_dup"]) == (0, 2)  # the same two trades, already here


@scenario_of
async def test_repeated_identical_rows_get_their_own_occurrence_and_both_import(rig):
    twice = (
        "date,action,symbol,quantity,price,account\n"
        "2026-10-01,BUY,RELIANCE,10,1000,Zerodha\n"
        "2026-10-01,BUY,RELIANCE,10,1000,Zerodha\n"
    )
    shown = (await rig.preview(text=twice))["data"]
    assert shown["rows_new"] == 2
    await rig.call("import_commit", id=shown["batch_id"], version=shown["tab_version"])
    keys = [r["import_key"] for r in rig.book.activities_list(OWNER, rig.tab["id"])["items"]]
    assert len(set(keys)) == 2
    assert (await rig.preview(text=twice))["data"]["rows_dup"] == 2


def test_the_ops_are_registered():
    from sudarshan.portfolio import ops

    assert {"import_preview", "import_commit", "import_discard", "export_csv"} <= set(ops.REGISTRY)
