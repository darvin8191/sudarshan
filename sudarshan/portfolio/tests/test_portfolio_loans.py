"""A loan or liability belongs to one tab, and that tab shows net worth (M7.19 V2, design 4.2 and
4.5). The migration step is additive and the earlier file keeps every row."""

from __future__ import annotations

import sqlite3
from pathlib import Path

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
            if "version" not in more:
                more["version"] = self.version(tab)
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
    assert made.applied[:2] == [1, 2] and migrations.LATEST >= 2
    assert raw(path, "PRAGMA user_version") == [(migrations.LATEST,)]
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
    assert again.applied[0] == 2 and again.applied == list(range(2, migrations.LATEST + 1))
    assert raw(path, "PRAGMA user_version") == [(migrations.LATEST,)]
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
    said = capsys.readouterr().out.replace(str(path), "<file>")  # the temp path may hold any digit
    later = list(range(2, migrations.LATEST + 1))  # the steps stacked branches add come after
    assert f"version: file 1, code {migrations.LATEST}" in said and f"pending: {later}" in said
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
        w.loan(tab={"id": 999}, version=1)
    upstox = next(t for t in w.book.tabs(OWNER) if t["kind"] == "upstox")
    with pytest.raises(Refused) as caught:
        w.loan(tab=upstox, version=upstox["version"])
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


# ---- loans entered in v0.5.0 (before a loan had a tab) -------------------------------------------
FIXTURE = Path(__file__).parent / "fixtures" / "v050_store.sql"
LATER = NOW + 15 * 86_400_000  # 10 Nov 2026: the day after v0.5.0 is published, and its loans' days


@pytest.fixture
def v050(tmp_path):
    """A portfolio.db written by the v0.5.0 code itself (2dc208a): the Upstox tab, a tab "Home"
    with two buys, and two loans made by that release's Loan entry, which carries no tab."""
    path = tmp_path / "s" / DB_FILENAME
    path.parent.mkdir()
    with sqlite3.connect(path) as conn:
        conn.executescript(FIXTURE.read_text())
        conn.execute("PRAGMA user_version = 1")
    assert raw(path, "SELECT name FROM sqlite_master WHERE name = 'tab_liability'") == []
    made = PortfolioStore(path, now=LATER)
    assert made.applied == list(range(2, migrations.LATEST + 1))
    book, views = Book(made), Views(made)
    home = next(t for t in book.tabs(OWNER) if t["name"] == "Home")

    class Opened:
        store, path_, tab = made, path, home

        @staticmethod
        def version(tab=home):
            return next(t for t in book.tabs(OWNER) if t["id"] == tab["id"])["version"]

        @staticmethod
        def view(tab=home, **args):
            return views.tab_view(OWNER, tab["id"], args, now=LATER)

        @staticmethod
        def loan_id(name):
            return raw(path, f"SELECT id FROM asset WHERE name = '{name}'")[0][0]

        book_ = book

    yield Opened
    made.close()


def test_a_loan_from_v050_is_listed_as_on_no_tab_and_can_be_moved_to_one(v050):
    """MEDIUM-4. The row says nothing of a tab, so the migration cannot backfill it; the Summary
    lists it ('Loans not on a tab') and the owner moves it, from the tab's own version."""
    view = v050.view()
    assert view["liabilities"] == [] and view["fixed"]["liabilities_paise"] == 0
    loose = {each["name"]: each for each in view["loose_loans"]}
    assert set(loose) == {"Home loan", "Car loan"}
    assert loose["Home loan"]["outstanding_paise"] == 50 * 100_000 * 100
    assert loose["Home loan"]["state"] == "owing"
    upstox = next(t for t in v050.book_.tabs(OWNER) if t["kind"] == "upstox")
    assert v050.view(upstox)["loose_loans"] == []  # nothing can be moved to the Upstox tab

    before = v050.version()
    v050.book_.loan_move(OWNER, v050.loan_id("Home loan"), v050.tab["id"], before, now=LATER)
    assert v050.version() == before + 1
    moved = v050.view()
    assert [each["name"] for each in moved["liabilities"]] == ["Home loan"]
    assert moved["fixed"]["liabilities_paise"] == 50 * 100_000 * 100
    assert [each["name"] for each in moved["loose_loans"]] == ["Car loan"]


def test_moving_a_loan_is_guarded_by_the_tabs_version_and_goes_to_one_tab_only(v050):
    loan = v050.loan_id("Home loan")
    with pytest.raises(VersionConflict):
        v050.book_.loan_move(OWNER, loan, v050.tab["id"], v050.version() + 3, now=LATER)
    assert raw(v050.path_, "SELECT count(*) FROM tab_liability") == [(0,)]
    v050.book_.loan_move(OWNER, loan, v050.tab["id"], v050.version(), now=LATER)
    other = v050.book_.tab_create(OWNER, "t-x", "Long term", "stocks", now=LATER)
    with pytest.raises(Refused) as caught:  # it is on Home now
        v050.book_.loan_move(OWNER, loan, other["id"], other["version"], now=LATER)
    assert "on the Home portfolio" in str(caught.value)
    assert raw(v050.path_, "SELECT tab_id FROM tab_liability") == [(v050.tab["id"],)]
    with pytest.raises(Refused):  # a share is not a loan
        v050.book_.loan_move(OWNER, 1, other["id"], other["version"], now=LATER)
    upstox = next(t for t in v050.book_.tabs(OWNER) if t["kind"] == "upstox")
    with pytest.raises(Refused) as caught:
        v050.book_.loan_move(
            OWNER, v050.loan_id("Car loan"), upstox["id"], upstox["version"], now=LATER
        )
    assert str(caught.value) == BOOK_WORDS["loan_tab"]
    v050.book_.tab_update(OWNER, other["id"], other["version"], {"archived": True}, now=LATER)
    archived = next(t for t in v050.book_.tabs(OWNER) if t["id"] == other["id"])
    with pytest.raises(Refused) as caught:
        v050.book_.loan_move(
            OWNER, v050.loan_id("Car loan"), other["id"], archived["version"], now=LATER
        )
    assert str(caught.value) == BOOK_WORDS["archived"]
    with pytest.raises(NotFound):
        v050.book_.loan_move(OWNER, 999, other["id"], archived["version"], now=LATER)


def test_adding_a_loan_again_says_where_the_one_with_that_name_is(v050):
    """The sentence names the place: no tab yet (and what to do), or the tab it is on."""

    def again(name="Home loan", rid="l-again"):
        return v050.book_.asset_create(
            OWNER, rid, "liability", now=LATER, name=name, tab_id=v050.tab["id"],
            version=v050.version(), first_value={"day": 20261105, "price_u": LAKH_U},
        )  # fmt: skip

    with pytest.raises(Refused) as caught:
        again("home LOAN")  # the name is matched ignoring case
    said = str(caught.value)
    assert "Home loan" in said and "on no portfolio yet" in said and "Move to a tab" in said
    assert "already have an asset" not in said
    v050.book_.loan_move(
        OWNER, v050.loan_id("Home loan"), v050.tab["id"], v050.version(), now=LATER
    )
    with pytest.raises(Refused) as caught:
        again()
    assert str(caught.value) == (
        "You already have a loan called Home loan, on the Home portfolio. Update it there."
    )
    with pytest.raises(Refused) as caught:  # a manual asset keeps the plain sentence
        again("Gold coin")
    assert str(caught.value) == "You already have an asset called Gold coin."


def test_the_v050_file_migrates_once_and_a_second_open_changes_nothing(v050):
    before = {
        t: raw(v050.path_, f"SELECT * FROM {t} ORDER BY 1") for t in ("asset", "tab", "price_point")
    }
    v050.store.close()
    again = PortfolioStore(v050.path_, now=LATER + 1)
    assert again.applied == []
    again.close()
    assert before == {t: raw(v050.path_, f"SELECT * FROM {t} ORDER BY 1") for t in before}


# ---- every loan write goes through the tab's version ---------------------------------------------
def test_a_loan_cannot_be_made_without_the_tabs_version(w):
    with pytest.raises(Invalid):
        w.book.asset_create(
            OWNER, "l-nv", "liability", now=NOW, name="Home loan", tab_id=w.tab["id"],
            first_value={"day": 20261001, "price_u": LAKH_U},
        )  # fmt: skip
    assert raw(w.store.path, "SELECT count(*) FROM tab_liability") == [(0,)]


def test_a_balance_is_set_from_the_loans_own_tab_at_its_version_and_moves_it(w):
    loan = w.loan(lakhs=50)
    before = w.version()
    with pytest.raises(VersionConflict):
        w.book.loan_balance_set(
            OWNER, loan["id"], w.tab["id"], before + 2, 20261020, LAKH_U, now=NOW
        )
    w.book.loan_balance_set(OWNER, loan["id"], w.tab["id"], before, 20261020, 48 * LAKH_U, now=NOW)
    assert w.version() == before + 1
    assert w.get()["liabilities"][0]["outstanding_paise"] == 48 * 100_000 * 100
    # Another tab cannot take the loan by setting its balance: it counts on one tab only.
    other_version = w.version(w.other)
    with pytest.raises(Refused) as caught:
        w.book.loan_balance_set(
            OWNER, loan["id"], w.other["id"], other_version, 20261021, 47 * LAKH_U, now=NOW
        )
    assert "on the Home portfolio" in str(caught.value)
    assert w.version(w.other) == other_version
    assert raw(w.store.path, "SELECT tab_id FROM tab_liability") == [(w.tab["id"],)]
    assert w.get(tab=w.other)["liabilities"] == []
    with pytest.raises(Refused):
        w.book.loan_balance_set(OWNER, w.gold["id"], w.tab["id"], w.version(), 20261020, 1, now=NOW)


# ---- net worth under a filter ---------------------------------------------------------------------
def test_loans_and_net_worth_are_for_the_whole_tab_not_for_a_filtered_part(w):
    bank = w.book.account_create(
        OWNER, "a-2", w.book.members(OWNER)[0]["id"], "Bank", "bank", now=NOW
    )
    w.hold_gold()
    w.loan(lakhs=1)
    whole = w.get()
    assert whole["fixed"]["liabilities_paise"] == 100_000 * 100 and whole["loans_hidden"] is False
    for narrowing in ({"accounts": [bank["id"]]}, {"classes": ["gold"]}, {"tags": ["x"]},
                      {"held": "over"}):  # fmt: skip
        part = w.get(**narrowing)
        assert part["liabilities"] == [] and part["fixed"]["liabilities_paise"] == 0, narrowing
        assert part["loans_hidden"] is True and part["loose_loans"] == [], narrowing
    # A day in the past is not a part of the tab: the loan's balance on that day stands.
    past = w.get(as_of=20261010)
    assert past["fixed"]["liabilities_paise"] == 100_000 * 100 and past["loans_hidden"] is False
    assert w.get(tab=w.other, accounts=[bank["id"]])["loans_hidden"] is False  # nothing to hide


# ---- a loan entered at 0, or dated later ----------------------------------------------------------
def test_a_loan_entered_at_zero_or_dated_later_is_listed_with_words_for_what_it_means(w):
    zero = w.loan(rid="l-0", lakhs=0, name="Spare loan")
    later = w.loan(rid="l-9", day=20261231, lakhs=20, name="Car loan")
    view = w.get()
    shown = {each["asset_id"]: each for each in view["liabilities"]}
    assert set(shown) == {zero["id"], later["id"]}
    assert shown[zero["id"]]["state"] == "zero"
    assert (
        shown[zero["id"]]["words"]
        == "Entered at 0, so it lowers nothing. Update the balance once you owe something."
    )
    assert shown[later["id"]]["state"] == "later" and shown[later["id"]]["day"] == 20261231
    assert shown[later["id"]]["words"] == (
        "Its balance starts on 31 Dec. It lowers net worth from that day, not before."
    )
    assert view["fixed"]["liabilities_paise"] == 0  # neither lowers net worth today
    assert w.get(as_of=20260930)["liabilities"] == []  # on a past day it did not exist yet
    # A loan that was owed and is paid off still leaves the list (state zero is only for 0 first).
    owed = w.loan(rid="l-1", lakhs=5, name="Home loan")
    w.book.loan_balance_set(OWNER, owed["id"], w.tab["id"], w.version(), 20261020, 0, now=NOW)
    assert owed["id"] not in {x["asset_id"] for x in w.get()["liabilities"]}


# ---- a store already at the old step 2 (45d4b3a) --------------------------------------------------
def test_a_store_at_the_old_step_two_keeps_its_links_and_status_reads_it_correctly(w, path, capsys):
    """Step 2 is the only step this branch adds (step 3 belongs to the reports branch): its
    schema is unchanged since 45d4b3a, so a file that ran it opens with nothing to apply and every
    link kept, and a file whose step 2 was cut short (version 1, table present) re-runs it
    cleanly."""
    from sudarshan.portfolio import migrate

    loan = w.loan()
    w.store.close()
    store_module.stop()
    assert migrate.main(["--status", "--path", str(path)]) == 0
    said = capsys.readouterr().out.replace(str(path), "<file>")
    top = migrations.LATEST
    assert f"version: file {top}, code {top}" in said and "pending: none" in said
    again = PortfolioStore(path, now=NOW + 1)
    assert again.applied == []
    again.close()
    store_module.stop()
    raw(path, "PRAGMA user_version = 1")  # step 2 cut short after its table was made
    assert migrate.main(["--status", "--path", str(path)]) == 0
    assert f"pending: {list(range(2, top + 1))}" in capsys.readouterr().out
    rerun = PortfolioStore(path, now=NOW + 2)
    assert rerun.applied == list(range(2, top + 1))
    rerun.close()
    assert raw(path, "SELECT tab_id, asset_id FROM tab_liability") == [(w.tab["id"], loan["id"])]
    assert raw(path, "PRAGMA user_version") == [(top,)]


# ---- boundaries the mutants found ----------------------------------------------------------------
def test_a_balance_dated_today_counts_today(w):
    w.loan(day=20261026, lakhs=7)  # NOW is 26 Oct 2026: the day itself is on or before it
    assert w.get()["fixed"]["liabilities_paise"] == 7 * 100_000 * 100
    assert w.get(as_of=20261026)["fixed"]["liabilities_paise"] == 7 * 100_000 * 100
    assert w.get(as_of=20261025)["liabilities"] == []


def test_a_loan_is_refused_on_an_archived_tab(w):
    w.book.tab_update(OWNER, w.tab["id"], w.version(), {"archived": True}, now=NOW)
    with pytest.raises(Refused) as caught:
        w.loan()
    assert str(caught.value) == BOOK_WORDS["archived"]
    assert raw(w.store.path, "SELECT count(*) FROM tab_liability") == [(0,)]


def test_a_nav_typed_by_hand_is_worded_as_a_nav_not_as_a_value(w):
    fund = w.book.asset_create(OWNER, "f-1", "mf", now=NOW, amfi_code="120503", name="Fund")
    w.book.activity_create(
        OWNER, w.tab["id"], w.version(),
        {"kind": "BUY", "trade_date": 20261001, "account_id": w.zerodha["id"],
         "asset_id": fund["id"], "qty_u": 10_000_000, "amount_paise": 1_000_000},
        "e-f", now=NOW,
    )  # fmt: skip
    w.store.write(OWNER, lambda tx: tx.set_price_point(fund["id"], 20261020, 123_4567, NOW))
    (row,) = w.get()["rows"]
    assert row["fresh"]["words"].startswith("NAV from 20 Oct")
