"""Schema step 4 (M7.37; Y6, Y28): tab_share and member_privacy. Empty file, and a populated copy of
a real store forced back to step 3; idempotent; --status reports before and after; no owner row
is touched."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sudarshan.portfolio import migrate, migrations
from sudarshan.portfolio import store as store_module
from sudarshan.portfolio.store import DB_FILENAME, PortfolioStore

NOW = 1_794_296_000_000
FIXTURE = Path(__file__).parent / "fixtures" / "v050_store.sql"
OWNED = ("member", "account", "tab", "asset", "activity", "price_point", "tab_liability")


@pytest.fixture(autouse=True)
def _stop():
    yield
    store_module.stop()


def rows(path, sql):
    with sqlite3.connect(path) as conn:
        return conn.execute(sql).fetchall()


def snapshot(path):
    return {t: rows(path, f"SELECT * FROM {t} ORDER BY 1") for t in OWNED}


def tables(path):
    return {r[0] for r in rows(path, "SELECT name FROM sqlite_master WHERE type = 'table'")}


def status(path, capsys):
    assert migrate.main(["--status", "--path", str(path)]) == 0
    return capsys.readouterr().out


def test_step_four_is_the_latest_and_adds_two_tables_and_nothing_else():
    assert [n for n, _ in migrations.STEPS][-1] == migrations.LATEST == 4
    assert set(migrations.V4) and all(
        "tab_share" in s or "member_privacy" in s for s in migrations.V4
    )


def test_an_empty_file_gets_the_two_tables_with_their_rules(tmp_path, capsys):
    path = tmp_path / "s" / DB_FILENAME
    assert (
        "file none, code 4" in status(path, capsys) and not path.exists()
    )  # --status makes nothing
    opened = PortfolioStore(path, now=NOW)
    opened.close()
    assert {"tab_share", "member_privacy"} <= tables(path)
    assert rows(path, "PRAGMA user_version") == [(4,)]
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO tab_share (principal, tab_id, viewer, level, created_at, updated_at)"
                     " VALUES ('owner', 1, '7', 'view', 1, 1)")  # fmt: skip
        with pytest.raises(sqlite3.IntegrityError):  # one share per tab and viewer
            conn.execute("INSERT INTO tab_share (principal, tab_id, viewer, level, created_at, updated_at)"
                         " VALUES ('owner', 1, '7', 'restricted', 1, 1)")  # fmt: skip
        with pytest.raises(sqlite3.IntegrityError):  # a level the service never writes
            conn.execute("INSERT INTO tab_share (principal, tab_id, viewer, level, created_at, updated_at)"
                         " VALUES ('owner', 1, '8', 'admin', 1, 1)")  # fmt: skip
        conn.execute("INSERT INTO member_privacy (principal, member, created_at, updated_at)"
                     " VALUES ('owner', '7', 1, 1)")  # fmt: skip
        assert conn.execute(
            "SELECT show_pnl, show_qty, show_funds, show_orders FROM member_privacy"
        ).fetchall() == [(0, 0, 0, 0)]  # nothing is shown until the owner says so
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO member_privacy (principal, member, created_at, updated_at)"
                         " VALUES ('owner', '7', 1, 1)")  # fmt: skip


def test_the_step_is_idempotent_and_a_second_open_does_nothing(tmp_path):
    path = tmp_path / "s" / DB_FILENAME
    PortfolioStore(path, now=NOW).close()
    store_module.stop()
    again = PortfolioStore(path, now=NOW)
    assert again.applied == []
    with (
        again.engine.begin() as conn
    ):  # the step run by hand again changes nothing and raises nothing
        migrations._v4(conn)
        migrations._v4(conn)
    again.close()
    assert rows(path, "PRAGMA user_version") == [(4,)]


def test_a_populated_copy_forced_back_to_step_three_keeps_every_owner_row(tmp_path, capsys):
    path = tmp_path / "s" / DB_FILENAME
    path.parent.mkdir()
    with sqlite3.connect(path) as conn:
        conn.executescript(FIXTURE.read_text())
        conn.execute("PRAGMA user_version = 1")
    first = PortfolioStore(path, now=NOW)  # a real store at the latest step, with the data in it
    first.close()
    store_module.stop()
    with sqlite3.connect(path) as conn:  # the shape the previous release left: step 3, no step 4
        conn.execute("DROP TABLE tab_share")
        conn.execute("DROP TABLE member_privacy")
        conn.execute("PRAGMA user_version = 3")
    before = snapshot(path)
    assert before["tab"] and before["activity"], "the fixture must carry owner data"
    said = status(path, capsys)
    assert "version: file 3, code 4" in said and "pending: [4]" in said
    assert snapshot(path) == before  # --status wrote nothing
    assert not ({"tab_share", "member_privacy"} & tables(path))
    opened = PortfolioStore(path, now=NOW)
    assert opened.applied == [4]
    opened.close()
    assert snapshot(path) == before
    assert {"tab_share", "member_privacy"} <= tables(path)
    assert rows(path, "SELECT count(*) FROM tab_share") == [(0,)]
    assert rows(path, "PRAGMA user_version") == [(4,)]
    assert "version: file 4, code 4" in status(path, capsys)


def test_a_file_a_newer_release_wrote_is_refused(tmp_path, capsys):
    path = tmp_path / "s" / DB_FILENAME
    PortfolioStore(path, now=NOW).close()
    store_module.stop()
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version = 5")
    with pytest.raises(migrations.SchemaTooNew):
        PortfolioStore(path, now=NOW)
    assert migrate.main(["--status", "--path", str(path)]) == 2
