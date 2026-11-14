"""Schema step 5 (phase P7, M7.27; design p7 7.1): the phase's tables and ``account.tax_mode``.

One step for the whole phase, additive. Tested as CLAUDE.md asks: on an empty file, and on a copy
of a populated store (the v0.5.0 file, taken through steps 1 to 4 with rows in every owner table)
forced back to step 4; run twice; ``--status`` before and after, writing nothing; a step that fails
part way leaves the file at step 4. The one fill writes ``tax_mode`` from each account's own kind,
only when the column is made, and never over a mode already there.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sudarshan.portfolio import migrate, migrations
from sudarshan.portfolio import store as store_module
from sudarshan.portfolio.core import kinds
from sudarshan.portfolio.store import DB_FILENAME, PortfolioStore, Scope, VersionConflict

NOW = 1_794_296_000_000
FIXTURE = Path(__file__).parent / "fixtures" / "v050_store.sql"
OWNER = Scope.owner()
PHASE = (
    "owner_setting", "tax_opening_lot", "tax_fmv", "tax_carry", "tax_day_override",
    "tax_trade_mark", "ca_event", "ca_read", "ca_decision", "dividend_line", "goal",
)  # fmt: skip
OWNED = (
    "member", "account", "tab", "tab_layout", "saved_view", "asset", "asset_alias", "activity",
    "price_point", "price_eod", "upstox_eod", "upstox_eod_holding", "import_batch",
    "tab_liability", "report_cache", "tab_share", "member_privacy",
)  # fmt: skip


@pytest.fixture(autouse=True)
def _stop():
    yield
    store_module.stop()


def rows(path, sql, args=()):
    with sqlite3.connect(path) as conn:
        return conn.execute(sql, args).fetchall()


def tables(path):
    return {r[0] for r in rows(path, "SELECT name FROM sqlite_master WHERE type = 'table'")}


def columns(path, table):
    return [r[1] for r in rows(path, f"PRAGMA table_info({table})")]


def snapshot(path, like=None):
    """Every owner row on the columns it had (``like``, an earlier snapshot), so a new column is
    allowed and a changed value is not."""
    found = {}
    for table in OWNED:
        names = like[table][0] if like else columns(path, table)
        found[table] = (names, rows(path, f"SELECT {', '.join(names)} FROM {table} ORDER BY 1"))
    return found


def status(path, capsys):
    assert migrate.main(["--status", "--path", str(path)]) == 0
    return capsys.readouterr().out.replace(str(path), "<file>")


def populated_at_step_four(tmp_path) -> Path:
    """The v0.5.0 file taken through steps 1 to 4 by a real store, with rows in every owner table,
    then put back to the shape release v0.12.0 leaves: step 4, none of step 5."""
    path = tmp_path / "s" / DB_FILENAME
    path.parent.mkdir()
    with sqlite3.connect(path) as conn:
        conn.executescript(FIXTURE.read_text())
        conn.execute("PRAGMA user_version = 1")
    PortfolioStore(path, now=NOW).close()
    store_module.stop()
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            INSERT INTO account (principal, member_id, name, kind, system, created_at, updated_at)
                VALUES ('owner', 1, 'Savings', 'bank', 0, 1, 1),
                       ('owner', 1, 'Locker', 'physical', 0, 1, 1),
                       ('owner', 1, 'Folio', 'mf_folio', 0, 1, 1);
            INSERT INTO tab_layout (tab_id, principal, spec, created_at, updated_at)
                VALUES (2, 'owner', '{"v": 1}', 1, 1);
            INSERT INTO saved_view (principal, tab_id, name, spec, created_at, updated_at)
                VALUES ('owner', 2, 'All', '{}', 1, 1);
            INSERT INTO asset_alias (principal, asset_id, kind, value, created_at, updated_at)
                VALUES ('owner', 1, 'symbol', 'OLD', 1, 1);
            INSERT INTO price_eod (asset_id, day, principal, price_u, basis, source, at,
                                   created_at, updated_at)
                VALUES (1, 20261030, 'owner', 1000000, 'close', 'feed', 1, 1, 1);
            INSERT INTO upstox_eod (day, principal, read_at, created_at, updated_at)
                VALUES (20261030, 'owner', 1, 1, 1);
            INSERT INTO upstox_eod_holding (day, instrument_key, principal, qty, created_at,
                                            updated_at)
                VALUES (20261030, 'NSE_EQ|X', 'owner', 1, 1, 1);
            INSERT INTO import_batch (principal, tab_id, format, status, created_at, updated_at)
                VALUES ('owner', 2, 'generic', 'committed', 1, 1);
            INSERT INTO tab_liability (tab_id, asset_id, principal, created_at, updated_at)
                VALUES (2, 3, 'owner', 1, 1);
            INSERT INTO report_cache (principal, kind, fy, segment, state, created_at, updated_at)
                VALUES ('owner', 'trades', 2026, 'EQ', 'ok', 1, 1);
            INSERT INTO tab_share (principal, tab_id, viewer, level, created_at, updated_at)
                VALUES ('owner', 2, '7', 'view', 1, 1);
            INSERT INTO member_privacy (principal, member, created_at, updated_at)
                VALUES ('owner', '7', 1, 1);
            """
        )
        for table in PHASE:
            conn.execute(f"DROP TABLE {table}")
        conn.execute("ALTER TABLE account DROP COLUMN tax_mode")
        conn.execute("PRAGMA user_version = 4")
    return path


# ---- the step ---------------------------------------------------------------------------------
def test_step_five_is_the_latest_and_its_shared_tables_belong_to_no_one():
    assert [n for n, _ in migrations.STEPS] == [1, 2, 3, 4, 5] and migrations.LATEST == 5
    assert {"ca_event", "ca_read"} <= set(migrations.SHARED_TABLES)
    text = " ".join(migrations.V5)
    for table in PHASE:
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in text, table


def test_every_owner_table_of_the_step_has_a_principal_stamps_and_a_version(tmp_path):
    path = tmp_path / "s" / DB_FILENAME
    PortfolioStore(path, now=NOW).close()
    for table in PHASE:
        have = set(columns(path, table))
        if table in migrations.SHARED_TABLES:
            assert "principal" not in have, table
            continue
        assert {"principal", "created_at", "updated_at", "version"} <= have, table
        if table != "owner_setting":  # a setting is written in place, never created twice
            assert {"id", "request_id"} <= have, table
    assert {"principal", "id"} - set(columns(path, "ca_event")) == {"principal"}


def test_an_empty_file_gets_the_step_and_status_says_so(tmp_path, capsys):
    path = tmp_path / "s" / DB_FILENAME
    assert "version: file none, code 5" in status(path, capsys) and not path.exists()
    opened = PortfolioStore(path, now=NOW)
    assert opened.applied == [1, 2, 3, 4, 5]
    opened.close()
    assert set(PHASE) <= tables(path) and "tax_mode" in columns(path, "account")
    assert rows(path, "PRAGMA user_version") == [(5,)]
    assert "version: file 5, code 5" in status(path, capsys)


# ---- a populated copy forced back to step 4 -----------------------------------------------------
def test_a_populated_copy_at_step_four_keeps_every_owner_row_and_status_writes_nothing(
    tmp_path, capsys
):
    path = populated_at_step_four(tmp_path)
    before = snapshot(path)
    assert all(found for _, found in before.values()), "the copy must hold a row in every table"
    said = status(path, capsys)
    assert "version: file 4, code 5" in said and "pending: [5]" in said
    assert snapshot(path) == before and not set(PHASE) & tables(path)  # --status wrote nothing
    assert "tax_mode" not in columns(path, "account")
    opened = PortfolioStore(path, now=NOW)
    assert opened.applied == [5] and opened.seeded == {"owner": []}
    opened.close()
    assert snapshot(path, before) == before
    assert set(PHASE) <= tables(path)
    assert rows(path, "PRAGMA user_version") == [(5,)]
    assert "version: file 5, code 5" in status(path, capsys)


def test_tax_mode_is_filled_from_each_accounts_own_kind_never_from_one_default(tmp_path):
    path = populated_at_step_four(tmp_path)
    PortfolioStore(path, now=NOW).close()
    found = dict(rows(path, "SELECT kind, tax_mode FROM account"))
    assert found == {
        "upstox": "reports", "demat": "ledger", "bank": "ledger", "physical": "ledger",
        "mf_folio": "ledger",
    }  # fmt: skip
    for kind in kinds.ACCOUNT_KINDS:  # the store's rule reads the same as the fill
        assert migrations.tax_mode_of({"kind": kind}) == ("reports" if kind == "upstox" else "ledger")
    assert migrations.tax_mode_of({"kind": "upstox", "tax_mode": "none"}) == "none"


def test_running_twice_changes_nothing_and_never_overwrites_a_mode_the_owner_chose(tmp_path):
    path = populated_at_step_four(tmp_path)
    PortfolioStore(path, now=NOW).close()
    store_module.stop()
    rows(path, "UPDATE account SET tax_mode = 'none' WHERE kind = 'bank'")  # the owner's choice
    rows(path, "INSERT INTO account (principal, member_id, name, kind, system, created_at, "
               "updated_at) VALUES ('owner', 1, 'Later', 'demat', 0, 2, 2)")  # fmt: skip
    after = rows(path, "SELECT id, name, kind, tax_mode, version, updated_at FROM account")
    rows(path, "PRAGMA user_version = 4")  # the step asked to run again on the same file
    again = PortfolioStore(path, now=NOW + 5)
    assert again.applied == [5]
    with again.engine.begin() as conn:  # and by hand, twice more
        migrations._v5(conn)
        migrations._v5(conn)
    again.close()
    assert rows(path, "SELECT id, name, kind, tax_mode, version, updated_at FROM account") == after
    assert dict(rows(path, "SELECT name, tax_mode FROM account"))["Later"] is None
    assert rows(path, "PRAGMA user_version") == [(5,)]


def test_a_step_that_fails_part_way_leaves_the_file_at_step_four(tmp_path, monkeypatch):
    path = populated_at_step_four(tmp_path)
    before = snapshot(path)
    broken = (*migrations.V5[:5], "CREATE TABLE goal (no closing paren")
    monkeypatch.setattr(migrations, "V5", broken)
    with pytest.raises(store_module.StoreError):
        PortfolioStore(path, now=NOW)
    store_module.stop()
    assert rows(path, "PRAGMA user_version") == [(4,)]
    assert not set(PHASE) & tables(path) and "tax_mode" not in columns(path, "account")
    assert snapshot(path) == before


def test_a_file_a_newer_release_wrote_is_refused(tmp_path):
    path = tmp_path / "s" / DB_FILENAME
    PortfolioStore(path, now=NOW).close()
    store_module.stop()
    rows(path, "PRAGMA user_version = 6")
    with pytest.raises(migrations.SchemaTooNew):
        PortfolioStore(path, now=NOW)
    assert migrate.main(["--status", "--path", str(path)]) == 2


# ---- the rules the tables keep ------------------------------------------------------------------
INSERT = {
    "tax_fmv": "INSERT INTO tax_fmv (principal, isin, fmv_u, source, created_at, updated_at) "
    "VALUES ('owner', ?, 3500000, ?, 1, 1)",
    "tax_carry": "INSERT INTO tax_carry (principal, member_id, loss_fy, bucket, paise, source, "
    "created_at, updated_at) VALUES ('owner', 1, 2024, ?, 4000000, ?, 1, 1)",
}


def test_the_tables_refuse_what_the_service_never_writes(tmp_path):
    path = tmp_path / "s" / DB_FILENAME
    PortfolioStore(path, now=NOW).close()
    with sqlite3.connect(path) as conn:
        conn.execute(INSERT["tax_fmv"], ("INE000A01011", "owner"))
        with pytest.raises(sqlite3.IntegrityError):  # one 31 Jan 2018 price per share
            conn.execute(INSERT["tax_fmv"], ("INE000A01011", "file"))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(INSERT["tax_fmv"], ("INE000A01029", "guess"))
        conn.execute(INSERT["tax_carry"], ("speculative", "owner"))
        conn.execute(INSERT["tax_carry"], ("speculative", "computed"))  # side by side
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(INSERT["tax_carry"], ("speculative", "owner"))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(INSERT["tax_carry"], ("capital", "owner"))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE account SET tax_mode = 'maybe'")
        conn.execute(
            "INSERT INTO ca_decision (principal, event_id, scope, state, created_at, updated_at) "
            "VALUES ('owner', 1, 'upstox', 'applied', 1, 1)"
        )
        with pytest.raises(sqlite3.IntegrityError):  # one decision per event and scope
            conn.execute(
                "INSERT INTO ca_decision (principal, event_id, scope, state, created_at, "
                "updated_at) VALUES ('owner', 1, 'upstox', 'dismissed', 1, 1)"
            )
        assert conn.execute("SELECT tab_ids, archived FROM goal").fetchall() == []


# ---- the store's accessors ----------------------------------------------------------------------
@pytest.fixture
def made(tmp_path):
    opened = PortfolioStore(tmp_path / "s" / DB_FILENAME, now=NOW)
    yield opened
    opened.close()


def test_a_setting_is_written_only_from_the_version_it_was_read_at(made):
    first = made.write(OWNER, lambda tx: tx.setting_put("tax:1", {"turnover": "abs_pnl"}, 0, NOW))
    assert (first["value"], first["version"]) == ({"turnover": "abs_pnl"}, 1)
    with pytest.raises(VersionConflict):  # one set already: a new one must name it
        made.write(OWNER, lambda tx: tx.setting_put("tax:1", {}, 0, NOW))
    second = made.write(OWNER, lambda tx: tx.setting_put("tax:1", {"turnover": "x"}, 1, NOW + 1))
    assert (second["value"], second["version"]) == ({"turnover": "x"}, 2)
    with pytest.raises(VersionConflict):
        made.write(OWNER, lambda tx: tx.setting_put("tax:1", {}, 1, NOW + 2))
    assert made.read(OWNER, lambda tx: tx.setting_get("risk_free_bp")) is None
    assert "principal" not in second


def test_a_taxpayers_inputs_and_accounts_come_back_by_table(made):
    def write(tx):
        tx.insert("tax_opening_lot", {
            "member_id": 1, "account_id": 1, "isin": "INE000A01011", "acquired_day": 20160310,
            "qty_u": 100_000_000, "cost_paise": 2_000_000, "source": "owner",
        }, NOW, "r-1")  # fmt: skip
        tx.insert("tax_carry", {"member_id": 2, "loss_fy": 2024, "bucket": "ltcl",
                                "paise": 1, "source": "owner"}, NOW)  # fmt: skip

    made.write(OWNER, write)
    found = made.read(OWNER, lambda tx: tx.tax_inputs(1))
    assert set(found) == set(store_module.TAX_MEMBER_TABLES)
    assert [lot["isin"] for lot in found["tax_opening_lot"]] == ["INE000A01011"]
    assert found["tax_carry"] == []  # member 2's loss is member 2's
    assert made.read(OWNER, lambda tx: tx.by_request("tax_opening_lot", "r-1"))["version"] == 1
    accounts = made.read(OWNER, lambda tx: tx.tax_accounts(1))
    assert [(a["kind"], a["tax_mode"]) for a in accounts] == [("upstox", "reports")]


def test_a_corporate_action_is_kept_once_marked_gone_and_listed_again(made):
    event = {"isin": "INE000A01011", "kind": "bonus", "ex_day": 20250910, "ratio_num": 1,
             "ratio_den": 1, "details": [{"name": "Announcement date", "value": "1 Aug 2025"}],
             "raw_hash": "h1"}  # fmt: skip
    row, new = made.write(OWNER, lambda tx: tx.ca_event_put(event, NOW))
    assert new and row["details"][0]["name"] == "Announcement date" and row["gone_at"] is None
    again, new = made.write(OWNER, lambda tx: tx.ca_event_put(event, NOW + 1))
    assert not new and again["id"] == row["id"] and again["read_at"] == NOW + 1
    assert made.write(OWNER, lambda tx: tx.ca_events_gone("INE000A01011", ["h2"], NOW + 2)) == 1
    assert made.read(OWNER, lambda tx: tx.ca_events()) == []
    gone = made.read(OWNER, lambda tx: tx.ca_events(gone=True))
    assert [e["gone_at"] for e in gone] == [NOW + 2]  # kept, never deleted
    back, _ = made.write(OWNER, lambda tx: tx.ca_event_put(event, NOW + 3))
    assert back["gone_at"] is None
    assert made.read(OWNER, lambda tx: tx.ca_events(["INE000A01029"])) == []
    assert made.read(OWNER, lambda tx: tx.ca_events(from_day=20250911)) == []
    made.write(OWNER, lambda tx: tx.ca_read_put("INE000A01011", NOW, "ok", None))
    made.write(OWNER, lambda tx: tx.ca_read_put("INE000A01011", NOW + 9, "ok", None))
    assert made.read(OWNER, lambda tx: tx.ca_reads()) == [
        {"isin": "INE000A01011", "read_at": NOW + 9, "state": "ok", "note": None}
    ]


def test_a_member_scope_never_reaches_the_phase_tables(made):
    narrow = Scope("owner", frozenset({1}))
    for table in ("tax_opening_lot", "goal", "dividend_line", "owner_setting", "ca_decision"):
        with pytest.raises(store_module.OutOfScope):
            made.read(narrow, lambda tx, table=table: tx.rows(table))
    with pytest.raises(store_module.OutOfScope):
        made.read(narrow, lambda tx: tx.ca_events())
