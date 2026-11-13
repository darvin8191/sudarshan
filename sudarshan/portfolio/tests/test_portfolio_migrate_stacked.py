"""The stacked steps together: step 2 (a loan's tab), step 3 (the reports cache) and step 4 (tab
shares and member privacy). A store written by the v0.5.0 code (version 1) and one at the old
step 2 both reach 4, keep every owner row, and --status says so before and after."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sudarshan.portfolio import migrate, migrations
from sudarshan.portfolio import store as store_module
from sudarshan.portfolio.store import DB_FILENAME, PortfolioStore

NOW = 1_794_296_000_000  # 10 Nov 2026
FIXTURE = Path(__file__).parent / "fixtures" / "v050_store.sql"
OWNED = ("member", "account", "tab", "asset", "activity", "price_point")


@pytest.fixture(autouse=True)
def _stop():
    yield
    store_module.stop()


def rows(path, sql):
    with sqlite3.connect(path) as conn:
        return conn.execute(sql).fetchall()


def snapshot(path):
    return {t: rows(path, f"SELECT * FROM {t} ORDER BY 1") for t in OWNED}


def status(path, capsys):
    assert migrate.main(["--status", "--path", str(path)]) == 0
    return capsys.readouterr().out.replace(str(path), "<file>")


def test_the_latest_step_is_four_and_step_two_is_still_the_loan_table():
    assert migrations.LATEST == 4
    assert [n for n, _ in migrations.STEPS] == [1, 2, 3, 4]


def test_a_v050_store_goes_from_1_to_4_and_status_says_so(tmp_path, capsys):
    path = tmp_path / "s" / DB_FILENAME
    path.parent.mkdir()
    with sqlite3.connect(path) as conn:
        conn.executescript(FIXTURE.read_text())
        conn.execute("PRAGMA user_version = 1")
    before = snapshot(path)
    said = status(path, capsys)
    assert "version: file 1, code 4" in said and "pending: [2, 3, 4]" in said
    assert snapshot(path) == before  # --status wrote nothing
    opened = PortfolioStore(path, now=NOW)
    assert opened.applied == [2, 3, 4]
    opened.close()
    assert snapshot(path) == before
    assert rows(path, "PRAGMA user_version") == [(4,)]
    names = {r[0] for r in rows(path, "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"tab_liability", "report_cache", "tab_share", "member_privacy"} <= names
    assert "version: file 4, code 4" in status(path, capsys)


def test_a_store_at_the_old_step_two_goes_from_2_to_4_and_keeps_its_loan_links(tmp_path, capsys):
    path = tmp_path / "s" / DB_FILENAME
    made = PortfolioStore(path, now=NOW)
    made.close()
    store_module.stop()
    with sqlite3.connect(path) as conn:  # the shape 45d4b3a left: step 2 applied, no step 3
        conn.execute("DROP TABLE report_cache")
        conn.execute(
            "INSERT INTO tab_liability (tab_id, asset_id, principal, created_at, updated_at)"
            " VALUES (7, 9, 'owner', 1, 1)"
        )
        conn.execute("PRAGMA user_version = 2")
    said = status(path, capsys)
    assert "version: file 2, code 4" in said and "pending: [3, 4]" in said
    again = PortfolioStore(path, now=NOW)
    assert again.applied == [3, 4]
    again.close()
    assert rows(path, "SELECT tab_id, asset_id FROM tab_liability") == [(7, 9)]
    assert rows(path, "PRAGMA user_version") == [(4,)]
    assert "version: file 4, code 4" in status(path, capsys)
