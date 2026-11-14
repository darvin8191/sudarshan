"""v0.5.2: NSE's freeze limits from 2 November 2026 hold through every deploy.

sudarshan-deploy runs upgrade/migrate_all.py on every install. Until v0.5.2 that list carried
migrate_qty_freeze_oct_2026.py, which treated NSE's 1,800 for NIFTY (and 900 for BANKNIFTY) as
stale and raised them to 3,510 and 1,440 on each run: the exchange's limits never stuck, not even
when typed in by hand on the Freeze Quantities page, and orders above them are rejected.

Each case seeds a copy of a database the way an install was seeded (the app's own loader, from
the October CSV), changes it as the owner may have, runs every freeze migration the registry
lists exactly as migrate_all.py does (its own process, from the project root), twice, as two
deploys would, and reads the rows back.
"""

from __future__ import annotations

import csv
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parents[3]
UPGRADE = REPO / "upgrade"
SEED_CSV = REPO / "data" / "qtyfreeze.csv"
DEPLOY_TOOL = REPO / "sudarshan" / "ops" / "deploy" / "sudarshan-deploy"

#: NSE/FAOP/77189, in force from 2 November 2026.
NOVEMBER = {
    "NIFTY": 1800,
    "BANKNIFTY": 900,
    "FINNIFTY": 1800,
    "MIDCPNIFTY": 2800,
    "NIFTYNXT50": 600,
    "NIFTYFPI": 8500,
}
#: What v0.5.1's seed carried and every v0.5.1 deploy wrote back (NSE/FAOP/76693, 5 October).
OCTOBER = {
    "NIFTY": 3510,
    "BANKNIFTY": 1440,
    "FINNIFTY": 3240,
    "MIDCPNIFTY": 5760,
    "NIFTYNXT50": 1125,
    "NIFTYFPI": 53900,
}

#: The app's own loader, as the first start of an install runs it.
SEED = """
import sys
from database import qty_freeze_db as qf
qf.init_db()
sys.exit(0 if qf.load_freeze_qty_from_csv(sys.argv[1], "NFO") else 1)
"""
#: The app's startup step for this table: seed it from data/qtyfreeze.csv only when empty.
STARTUP = """
from database import qty_freeze_db as qf
qf.ensure_qty_freeze_tables_exists()
"""


def _env(db: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db.as_posix()}"
    return env


def _python(db: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args], cwd=REPO, env=_env(db), capture_output=True, text=True, timeout=120
    )


def _registered() -> list[str]:
    # On Windows migrate_all rewraps stdout at import, which closes pytest's capture.
    with patch.object(sys, "platform", "test"):
        from upgrade import migrate_all

    return [script for script, _description in migrate_all.MIGRATIONS]


def _freeze_scripts() -> list[str]:
    return [name for name in _registered() if "freeze" in name]


def _deploy(db: Path) -> str:
    """One deploy's migrate_all.py, for this table: every freeze script it lists, in order, each
    in its own process from the project root (migrate_all.run_migration)."""
    out = []
    for script in _freeze_scripts():
        done = _python(db, str(UPGRADE / script))
        assert done.returncode == 0, done.stdout + done.stderr
        out.append(done.stdout)
    return "".join(out)


def _october_csv(tmp_path: Path) -> Path:
    """The seed v0.5.1 shipped: today's file with the six index rows at their October values."""
    with SEED_CSV.open(newline="") as handle:
        rows = list(csv.reader(handle))
    for row in rows[1:]:
        symbol = row[1].strip()
        if symbol in OCTOBER:
            row[2] = str(OCTOBER[symbol])
    path = tmp_path / "qtyfreeze-october.csv"
    with path.open("w", newline="") as handle:
        csv.writer(handle).writerows(rows)
    return path


def _install(tmp_path: Path) -> Path:
    """An install on v0.5.1: its database seeded from the October CSV by the app itself."""
    db = tmp_path / "openalgo.db"
    done = _python(db, "-c", SEED, str(_october_csv(tmp_path)))
    assert done.returncode == 0, done.stdout + done.stderr
    return db


def _rows(db: Path) -> dict[tuple[str, str], int]:
    with closing(sqlite3.connect(db)) as conn:
        query = "SELECT exchange, symbol, freeze_qty FROM qty_freeze"
        return {(exchange, symbol): qty for exchange, symbol, qty in conn.execute(query)}


def _indices(db: Path) -> dict[str, int]:
    rows = _rows(db)
    return {symbol: rows[("NFO", symbol)] for symbol in NOVEMBER if ("NFO", symbol) in rows}


def _set_by_hand(db: Path, values: dict[str, int]) -> None:
    """What the Freeze Quantities page writes when the owner edits a row."""
    with closing(sqlite3.connect(db)) as conn, conn:
        for symbol, qty in values.items():
            conn.execute(
                "UPDATE qty_freeze SET freeze_qty = ? WHERE exchange = 'NFO' AND symbol = ?",
                (qty, symbol),
            )


def test_an_install_on_the_october_limits_comes_down_and_stays_down(tmp_path):
    db = _install(tmp_path)
    seeded = _rows(db)
    assert _indices(db) == OCTOBER  # the state the server is in today

    first = _deploy(db)
    assert _indices(db) == NOVEMBER
    assert "3510 -> 1800" in first and "1440 -> 900" in first

    second = _deploy(db)
    assert _indices(db) == NOVEMBER, "a second deploy raised the limits again"
    assert "[OK] Nothing to change." in second

    after = _rows(db)
    assert len(after) == len(seeded)  # nothing added, nothing deleted
    assert {key: qty for key, qty in after.items() if key[1] not in NOVEMBER} == {
        key: qty for key, qty in seeded.items() if key[1] not in NOVEMBER
    }


@pytest.mark.parametrize(
    "by_hand",
    [
        # NSE's own figures, typed in on the Freeze Quantities page: every v0.5.1 deploy undid
        # this, raising NIFTY back to 3,510 and BANKNIFTY to 1,440.
        {"NIFTY": 1800, "BANKNIFTY": 900},
        # Whole lots of his own choosing (27 x 65 and 29 x 30), a value nobody shipped or published.
        {"NIFTY": 1755, "BANKNIFTY": 870},
    ],
    ids=["the published limits", "values of his own"],
)
def test_limits_the_owner_set_by_hand_survive_every_deploy(tmp_path, by_hand):
    db = _install(tmp_path)
    _set_by_hand(db, by_hand)

    for _deploy_number in (1, 2):
        out = _deploy(db)
        assert _indices(db) == {**NOVEMBER, **by_hand}

    if by_hand["NIFTY"] != NOVEMBER["NIFTY"]:
        assert "set by an admin, left alone (NSE limit is 1800)" in out


def test_a_new_install_is_left_to_the_seed_and_the_seed_carries_the_new_limits(tmp_path):
    db = tmp_path / "openalgo.db"
    _deploy(db)  # before the app's first start: no database file yet
    assert not db.exists(), "the migration created a database the app had not made"

    done = _python(db, "-c", STARTUP)
    assert done.returncode == 0, done.stdout + done.stderr
    assert _indices(db) == NOVEMBER

    _deploy(db)
    assert _indices(db) == NOVEMBER


def test_no_script_a_deploy_runs_raises_the_limits_again():
    """migrate_all.py runs every listed script on every deploy, in order: an older revision left in
    the list would rewrite the rows each time, and an install stopped between the two would keep
    limits the exchange rejects. Only the November script may touch the table."""
    assert "migrate_all.py" in DEPLOY_TOOL.read_text()
    assert _freeze_scripts() == ["migrate_qty_freeze_nov_2026.py"]
    assert not (UPGRADE / "migrate_qty_freeze_oct_2026.py").exists()
    writers = [
        name
        for name in _registered()
        if (UPGRADE / name).exists() and "qty_freeze" in (UPGRADE / name).read_text()
    ]
    assert writers == ["migrate_qty_freeze_nov_2026.py"]
