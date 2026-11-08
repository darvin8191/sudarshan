"""A loan or liability belongs to one tab, and that tab shows net worth (M7.19 V2, design 4.2 and
4.5). The migration step is additive and the earlier file keeps every row."""

from __future__ import annotations

import sqlite3

import pytest

from sudarshan.portfolio import migrations
from sudarshan.portfolio import store as store_module
from sudarshan.portfolio.book import WORDS as BOOK_WORDS
from sudarshan.portfolio.book import Book
from sudarshan.portfolio.store import (
    DB_FILENAME,
    Invalid,
    NotFound,
    PortfolioStore,
    Refused,
    Scope,
    VersionConflict,
)
from sudarshan.portfolio.views import Views

NOW = 1_793_000_000_000  # 26 Oct 2026, mid-morning in India
OWNER = Scope.owner()
LAKH_U = 100_000 * 10_000  # one lakh rupees as price_u (rupees x 10^4)


@pytest.fixture(autouse=True)
def _stop_thread():
    yield
    store_module.stop()


@pytest.fixture
def path(tmp_path):
    return tmp_path / "s" / DB_FILENAME


@pytest.fixture
def w(path):
    class World:
        def __init__(self):
            self.store = PortfolioStore(path, now=NOW)
            self.book = Book(self.store)
            self.views = Views(self.store)
            me = self.book.members(OWNER)[0]
            self.zerodha = self.book.account_create(
                OWNER, "a-1", me["id"], "Zerodha", "demat", now=NOW
            )
            self.gold = self.book.asset_create(
                OWNER, "g-1", "manual", now=NOW, name="Gold coin", asset_class="gold",
                unit_label="g", first_value={"day": 20261001, "price_u": 60_000_000},
            )  # fmt: skip
            self.tab = self.book.tab_create(OWNER, "t-1", "Home", "mixed", now=NOW)
            self.other = self.book.tab_create(OWNER, "t-2", "Long term", "stocks", now=NOW)

        def version(self, tab=None) -> int:
            tab = tab or self.tab
            return next(t for t in self.book.tabs(OWNER) if t["id"] == tab["id"])["version"]

        def hold_gold(self):
            entry = {
                "kind": "BUY", "trade_date": 20261001, "account_id": self.zerodha["id"],
                "asset_id": self.gold["id"], "qty_u": 2_000_000, "amount_paise": 1_000_000,
            }  # fmt: skip
            return self.book.activity_create(
                OWNER, self.tab["id"], self.version(), entry, "e-1", now=NOW
            )

        def loan(self, rid="l-1", name="Home loan", day=20261001, lakhs=50, tab=None, **more):
            tab = tab or self.tab
            return self.book.asset_create(
                OWNER, rid, "liability", now=NOW, name=name, tab_id=tab["id"],
                first_value={"day": day, "price_u": lakhs * LAKH_U}, **more,
            )  # fmt: skip

        def get(self, tab=None, **args):
            return self.views.tab_view(OWNER, (tab or self.tab)["id"], args, now=NOW)

    made = World()
    yield made
    made.store.close()


def raw(path, sql):
    with sqlite3.connect(path) as conn:
        return conn.execute(sql).fetchall()


# ---- the migration step --------------------------------------------------------------------------
def test_the_step_is_the_second_and_the_status_tool_knows_it(path):
    made = PortfolioStore(path, now=NOW)
    assert made.applied == [1, 2] and migrations.LATEST == 2
    assert raw(path, "PRAGMA user_version") == [(2,)]
    made.close()
    again = PortfolioStore(path, now=NOW + 1)
    assert again.applied == []
    again.close()


def test_a_populated_v1_file_gains_the_table_and_keeps_every_row(path):
    """A copy of a real v0.5.0 file (user_version 1, rows in every owner table) forced back a
    version: only step 2 runs, nothing is lost, and the owner's loan from before can be linked."""
    made = PortfolioStore(path, now=NOW)
    book = Book(made)
    me = book.members(OWNER)[0]
    account = book.account_create(OWNER, "a-1", me["id"], "Zerodha", "demat", now=NOW)
    gold = book.asset_create(
        OWNER, "g-1", "manual", now=NOW, name="Gold coin", asset_class="gold", unit_label="g",
        first_value={"day": 20261001, "price_u": 60_000_000},
    )  # fmt: skip
    tab = book.tab_create(OWNER, "t-1", "Home", "mixed", now=NOW)
    book.activity_create(
        OWNER, tab["id"], 1,
        {"kind": "BUY", "trade_date": 20261001, "account_id": account["id"],
         "asset_id": gold["id"], "qty_u": 2_000_000, "amount_paise": 1_000_000},
        "e-1", now=NOW,
    )  # fmt: skip
    made.close()
    # Force the file back to v0.5.0's schema: no link table, user_version 1 (a liability made by
    # v0.5.0's Loan entry is an asset with a balance and no tab).
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE tab_liability")
        conn.execute(
            "INSERT INTO asset (kind, name, asset_class, tags, principal, created_at, updated_at,"
            " version) VALUES ('liability', 'Car loan', 'other', '[]', 'owner', 1, 1, 1)"
        )
        conn.execute("PRAGMA user_version = 1")
    before = {
        table: raw(path, f"SELECT * FROM {table} ORDER BY 1")
        for table in ("member", "account", "tab", "asset", "activity", "price_point")
    }
    rows_before = {t: len(r) for t, r in before.items()}
    assert rows_before["asset"] == 2 and rows_before["activity"] == 1

    again = PortfolioStore(path, now=NOW + 5)
    assert again.applied == [2]
    assert raw(path, "PRAGMA user_version") == [(2,)]
    for table, rows in before.items():
        assert raw(path, f"SELECT * FROM {table} ORDER BY 1") == rows, table
    assert raw(path, "SELECT count(*) FROM tab_liability") == [(0,)]
    # The old loan shows nowhere until it is linked; setting its balance from the tab links it.
    book = Book(again)
    views = Views(again)
    loan_id = raw(path, "SELECT id FROM asset WHERE kind = 'liability'")[0][0]
    assert views.tab_view(OWNER, tab["id"], {}, now=NOW + 5)["liabilities"] == []

    def set_balance(tx):
        tx.set_price_point(loan_id, 20261001, 3 * LAKH_U, NOW + 5)
        tx.link_liability(tab["id"], loan_id, NOW + 5)

    again.write(OWNER, set_balance)
    view = views.tab_view(OWNER, tab["id"], {}, now=NOW + 5)
    assert [each["name"] for each in view["liabilities"]] == ["Car loan"]
    assert view["fixed"]["liabilities_paise"] == 300_000_00
    again.close()


def test_status_lists_step_two_for_a_v1_file(path, capsys):
    from sudarshan.portfolio import migrate

    made = PortfolioStore(path, now=NOW)
    made.close()
    raw(path, "PRAGMA user_version = 1")
    assert migrate.main(["--status", "--path", str(path)]) == 0
    said = capsys.readouterr().out
    assert "1" in said and "2" in said
    assert raw(path, "PRAGMA user_version") == [(1,)]  # --status changed nothing


# ---- the book --------------------------------------------------------------------------------------
def test_a_loan_needs_a_tab_and_a_first_balance(w):
    with pytest.raises(Invalid) as caught:
        w.book.asset_create(OWNER, "l-x", "liability", now=NOW, name="Home loan")
    assert str(caught.value) == BOOK_WORDS["loan_tab"]
    with pytest.raises(Invalid) as caught:
        w.book.asset_create(
            OWNER, "l-y", "liability", now=NOW, name="Home loan", tab_id=w.tab["id"]
        )
    assert str(caught.value) == BOOK_WORDS["loan_first"]
    with pytest.raises(NotFound):
        w.loan(tab={"id": 999})
    upstox = next(t for t in w.book.tabs(OWNER) if t["kind"] == "upstox")
    with pytest.raises(Refused) as caught:
        w.loan(tab=upstox)
    assert str(caught.value) == BOOK_WORDS["loan_tab"]


def test_a_loan_moves_its_tabs_version_only_from_the_one_it_was_decided_on(w):
    before = w.version()
    with pytest.raises(VersionConflict):
        w.loan(version=before + 7)
    assert w.version() == before
    w.loan(version=before)
    assert w.version() == before + 1
    # A retry of the same request finds the row made, and moves nothing again.
    w.loan(version=before)
    assert w.version() == before + 1


def test_a_loan_is_linked_to_its_tab_once(w):
    loan = w.loan()
    again = w.loan()  # the same request id
    assert again["id"] == loan["id"]
    assert raw(w.store.path, "SELECT tab_id, asset_id FROM tab_liability") == [
        (w.tab["id"], loan["id"])
    ]


# ---- the view --------------------------------------------------------------------------------------
def test_the_tab_shows_its_loans_and_net_worth_and_no_return_figure_moves(w):
    w.hold_gold()
    plain = w.get()
    w.loan(lakhs=50)
    view = w.get()
    (loan,) = view["liabilities"]
    assert loan["name"] == "Home loan" and loan["outstanding_paise"] == 50 * 100_000 * 100
    assert loan["day"] == 20261001
    assert loan["fresh"]["words"].startswith("Balance from 1 Oct")
    assert view["fixed"]["liabilities_paise"] == 50 * 100_000 * 100
    assert view["totals"] == plain["totals"]  # value, gain and return are untouched
    assert {k: v for k, v in view["fixed"].items() if k != "liabilities_paise"} == {
        k: v for k, v in plain["fixed"].items() if k != "liabilities_paise"
    }
    assert view["rows"] == plain["rows"]  # a loan is never a holding row
    assert w.get(tab=w.other)["liabilities"] == []  # another tab does not see it


def test_the_newest_balance_on_or_before_the_day_stands_and_a_paid_off_loan_is_gone(w):
    loan = w.loan(lakhs=50, day=20261001)

    def later(tx):
        tx.set_price_point(loan["id"], 20261015, 48 * LAKH_U, NOW)

    w.store.write(OWNER, later)
    assert w.get()["liabilities"][0]["outstanding_paise"] == 48 * 100_000 * 100
    assert w.get(as_of=20261010)["liabilities"][0]["outstanding_paise"] == 50 * 100_000 * 100
    assert w.get(as_of=20260930)["liabilities"] == []  # it did not exist then

    def paid(tx):
        tx.set_price_point(loan["id"], 20261020, 0, NOW)

    w.store.write(OWNER, paid)
    view = w.get()
    assert view["liabilities"] == [] and view["fixed"]["liabilities_paise"] == 0


def test_an_old_balance_is_marked_old_by_the_tabs_own_limit(w):
    w.loan(day=20260801)  # 86 days before today
    (loan,) = w.get()["liabilities"]
    assert loan["fresh"]["state"] == "old" and "86 days old" in loan["fresh"]["words"]


def test_deleting_the_tab_drops_the_link_and_keeps_the_loan_asset(w):
    loan = w.loan()
    w.book.tab_update(OWNER, w.tab["id"], w.version(), {"archived": True}, now=NOW)
    w.book.tab_delete(OWNER, w.tab["id"], w.version(), "Home", now=NOW)
    assert raw(w.store.path, "SELECT count(*) FROM tab_liability") == [(0,)]
    assert raw(
        w.store.path, "SELECT name FROM asset WHERE id = ?".replace("?", str(loan["id"]))
    ) == [("Home loan",)]
