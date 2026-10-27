"""M18.11 (X8): the ring services' databases in the deploy tool's backup and rollback.

The ring keeps its state in ``/var/lib/sudarshan-<service>/`` (alerts.db, market.db, later
portfolio.db and screener.db). Before an install, with the ring stopped, the tool copies each
``*.db`` there with SQLite's online backup into ``<backup>/ring/<folder>/<name>``; a rollback puts
every copy back in its own folder, owned by the ring's user with mode 600, and removes the journal
files a stale copy would meet, all before anything starts. Nothing of the ring goes into OpenAlgo's
``db/`` folder, whose top-level ``*.db`` files the rollback restores by name, and the sealed owner
vault in ``db/`` is never restored: it holds what the owner typed since the backup (review R12).

Before a ring service starts, the tool also logs what every ``migrate`` module of that service says
with ``--status`` (best effort: no module, no line).

These tests run the whole tool on the fake server of ``test_deploy_calendar.py``, with real SQLite
files, the real ``sqlite3`` command and a migration step that can touch the files as a release's
could.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
from pathlib import Path

import pytest
from test_deploy_calendar import Box, _shim, whole_tool
from test_deploy_notices import UNIT, install_box

pytestmark = [
    pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash"),
    pytest.mark.skipif(shutil.which("sqlite3") is None, reason="needs the sqlite3 command"),
]

ALERTS, PORTFOLIO = "sudarshan-alerts", "sudarshan-portfolio"
ROWS = [1, 2, 3]


def make_db(path: Path, rows: list[int] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS t (n INTEGER)")
    con.executemany("INSERT INTO t VALUES (?)", [(n,) for n in (rows or ROWS)])
    con.commit()
    con.close()
    return path


def rows_of(path: Path) -> list[int]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [n for (n,) in con.execute("SELECT n FROM t ORDER BY rowid")]
    finally:
        con.close()


def ring_box(tmp_path: Path) -> Box:
    """An install is due. The ring has alerts.db and market.db in one folder, portfolio.db in
    another; OpenAlgo has openalgo.db and a market.db of its own (same name, other content)."""
    box = install_box(tmp_path)
    make_db(box.ringvar / ALERTS / "alerts.db", [1, 2, 3])
    make_db(box.ringvar / ALERTS / "market.db", [4, 5, 6])
    make_db(box.ringvar / PORTFOLIO / "portfolio.db", [7, 8, 9])
    make_db(box.app / "db" / "openalgo.db", [10, 11])
    make_db(box.app / "db" / "market.db", [12, 13])
    (box.state / "managed_units").write_text(f"{ALERTS}.service\n{PORTFOLIO}.service\n")
    return box


def migration_that(box: Box, script: str) -> None:
    """The release's migration step, as a shell script, before it exits with ``box.migrations``."""
    body = f'if [[ "$1" == -m ]]; then exit 0; fi\n{script}\nexit "${{FAKE_MIGRATIONS}}"'
    _shim(box.app / ".venv" / "bin", "python", body)


def touch_the_ring(box: Box, journal: bool = True) -> str:
    """A migration step that changes every database the ring and OpenAlgo have and leaves stale
    journal files by one of the ring's."""
    lines = []
    for db in (
        box.ringvar / ALERTS / "alerts.db",
        box.ringvar / ALERTS / "market.db",
        box.ringvar / PORTFOLIO / "portfolio.db",
        box.app / "db" / "openalgo.db",
        box.app / "db" / "market.db",
    ):
        lines.append(f'sqlite3 "{db}" "INSERT INTO t VALUES (999)"')
    for ext in ("-wal", "-shm", "-journal") if journal else ("-wal", "-shm"):
        lines.append(f'echo stale > "{box.ringvar / ALERTS / "alerts.db"}{ext}"')
    return "\n".join(lines)


def the_backup(box: Box) -> Path:
    (bdir,) = box.backups.iterdir()
    return bdir


# ---- the backup -------------------------------------------------------------------------------


@whole_tool
def test_an_install_copies_every_ring_database_under_ring_and_never_beside_openalgos(tmp_path):
    box = ring_box(tmp_path)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    bdir = the_backup(box)
    for folder, name, rows in (
        (ALERTS, "alerts.db", [1, 2, 3]),
        (ALERTS, "market.db", [4, 5, 6]),
        (PORTFOLIO, "portfolio.db", [7, 8, 9]),
    ):
        copy = bdir / "ring" / folder / name
        assert rows_of(copy) == rows  # a real database, with its rows
        assert (
            stat.S_IMODE(copy.stat().st_mode) & 0o077 == 0
        )  # private, like the rest of the backup
        assert f"ring: backed up {folder}/{name}\n" in done.stdout
    # the top level holds OpenAlgo's databases only: the rollback copies every one of them into db/
    assert sorted(p.name for p in bdir.glob("*.db")) == ["market.db", "openalgo.db"]
    assert rows_of(bdir / "market.db") == [12, 13]  # OpenAlgo's, not the ring's market.db
    # the live files are as they were, with nothing left beside them
    assert rows_of(box.ringvar / ALERTS / "market.db") == [4, 5, 6]
    assert sorted(p.name for p in (box.ringvar / ALERTS).iterdir()) == ["alerts.db", "market.db"]


@whole_tool
def test_the_ring_is_stopped_before_its_databases_are_copied(tmp_path):
    """A copy made while a service still writes would be older than its last words."""
    box = ring_box(tmp_path)
    real = shutil.which("sqlite3") or "sqlite3"
    _shim(
        box.bin,
        "sqlite3",
        '[[ "$*" == */ring/* ]] && cp "${FAKE_WEB}/systemctl" "${FAKE_WEB}/systemctl_at_copy"\n'
        f'exec {real} "$@"',
    )
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    seen = (box.web / "systemctl_at_copy").read_text().splitlines()
    assert f"stop {ALERTS}.service" in seen and f"stop {PORTFOLIO}.service" in seen
    assert f"start {ALERTS}.service" not in seen and f"restart {ALERTS}.service" not in seen


@whole_tool
def test_a_ring_database_still_in_its_write_ahead_log_is_copied_whole(tmp_path):
    """A raw copy of the file would lose the rows the log holds; the online backup does not."""
    box = ring_box(tmp_path)
    live = box.ringvar / ALERTS / "alerts.db"
    con = sqlite3.connect(live)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute("INSERT INTO t VALUES (100)")
    con.commit()
    try:
        assert (box.ringvar / ALERTS / "alerts.db-wal").stat().st_size > 0
        done = box.run()
        assert done.returncode == 0, done.stdout + done.stderr
        assert rows_of(the_backup(box) / "ring" / ALERTS / "alerts.db") == [1, 2, 3, 100]
    finally:
        con.close()


@whole_tool
def test_no_ring_state_means_no_ring_folder_in_the_backup(tmp_path):
    box = install_box(tmp_path)
    make_db(box.app / "db" / "openalgo.db")
    (box.ringvar / ALERTS).mkdir()  # a folder with no database
    (box.ringvar / ALERTS / "events.jsonl").write_text("{}\n")
    (box.ringvar / "elsewhere").mkdir()  # not the ring's name
    make_db(box.ringvar / "elsewhere" / "other.db")
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert not (the_backup(box) / "ring").exists() and "ring: backed up" not in done.stdout


@whole_tool
def test_a_database_that_cannot_be_copied_is_logged_and_the_install_goes_on(tmp_path):
    box = ring_box(tmp_path)
    (box.ringvar / ALERTS / "broken.db").write_bytes(b"this is not a database" * 100)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert box.deployed() == "v1.0.1"
    assert f"WARNING: ring: could not back up {ALERTS}/broken.db" in done.stdout
    ring = the_backup(box) / "ring" / ALERTS
    assert not (ring / "broken.db").exists()  # nothing half-written is left to be put back
    assert rows_of(ring / "alerts.db") == [1, 2, 3]  # and the others were copied


@whole_tool
def test_a_command_failing_inside_the_backup_never_rolls_the_install_back(tmp_path):
    """The tool rolls back on any failed command; the ring's copy is a convenience and must not."""
    box = ring_box(tmp_path)
    real = shutil.which("basename") or "basename"
    _shim(  # one name cannot be worked out: a failure that no condition around it would catch
        box.bin,
        "basename",
        f'for a in "$@"; do [[ "$a" == *portfolio.db ]] && exit 1; done\nexec {real} "$@"',
    )
    done = box.run()
    assert done.returncode == 0 and box.deployed() == "v1.0.1", done.stdout + done.stderr
    assert "rolling back" not in done.stdout
    ring = the_backup(box) / "ring"
    assert sorted(p.name for p in ring.rglob("*.db")) == ["alerts.db", "market.db"]
    assert f"ring: backed up {ALERTS}/alerts.db\n" in done.stdout  # the rest was copied


@whole_tool
def test_names_and_links_that_could_carry_an_attack_are_never_followed(tmp_path):
    box = ring_box(tmp_path)
    folder = box.ringvar / ALERTS
    pwned = Path.cwd() / "PWNED_BY_A_DATABASE_NAME"  # where a command run by the tool would write
    pwned.unlink(missing_ok=True)
    # a name that would end the quoted backup command and start another
    evil = folder / f"x'; .shell touch {pwned.name}; '.db"
    make_db(evil)
    odd = [folder / "has space.db", folder / "new\nline.db"]
    for path in odd:
        make_db(path)
    shutil.copy(box.app / "db" / "openalgo.db", tmp_path / "secret.db")
    (folder / "link.db").symlink_to(tmp_path / "secret.db")  # another database, reached by a link
    (box.ringvar / "sudarshan-linked").symlink_to(tmp_path)  # a ring folder that is a link
    try:
        done = box.run()
        assert not pwned.exists()
    finally:
        pwned.unlink(missing_ok=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert sorted(p.name for p in (the_backup(box) / "ring").rglob("*.db")) == [
        "alerts.db", "market.db", "portfolio.db",
    ]  # fmt: skip
    assert done.stdout.count("unusual name - not backed up") == 3


# ---- the rollback -----------------------------------------------------------------------------


@whole_tool
def test_a_rollback_puts_each_ring_database_back_in_its_own_folder(tmp_path):
    box = ring_box(tmp_path)
    box.migrations = "1"
    migration_that(box, touch_the_ring(box))
    done = box.run()
    assert done.returncode == 1 and "rolling back" in done.stdout, done.stdout + done.stderr
    assert box.deployed() == "v1.0.0"
    for folder, name, rows in (
        (ALERTS, "alerts.db", [1, 2, 3]),
        (ALERTS, "market.db", [4, 5, 6]),
        (PORTFOLIO, "portfolio.db", [7, 8, 9]),
    ):
        live = box.ringvar / folder / name
        assert rows_of(live) == rows, name  # as they were before the install
        assert stat.S_IMODE(live.stat().st_mode) == 0o600
        assert f"ring: put back {folder}/{name}\n" in done.stdout
    # the stale journal files are gone and nothing of the restore is left
    assert sorted(p.name for p in (box.ringvar / ALERTS).iterdir()) == ["alerts.db", "market.db"]
    assert sorted(p.name for p in (box.ringvar / PORTFOLIO).iterdir()) == ["portfolio.db"]
    # the ring's user owns them (the fake chown only records it: that user is not on this machine)
    asked = (box.web / "chown").read_text().splitlines()
    assert f"sudarshan:sudarshan {box.ringvar / ALERTS / 'alerts.db'}.restoring" in asked


@whole_tool
def test_a_restored_database_is_private_whatever_mode_its_copy_had(tmp_path):
    """The copy is made private with the rest of the backup, but the restored file does not rest on
    that: a ring database holds alerts and market state for one user only."""
    box = ring_box(tmp_path)
    box.migrations = "1"
    migration_that(
        box,
        touch_the_ring(box)
        + f'\nchmod 664 "{box.backups}"/*/ring/*/*.db',  # a copy others can read
    )
    done = box.run()
    assert done.returncode == 1 and "rolling back" in done.stdout, done.stdout + done.stderr
    copies = list(the_backup(box).glob("ring/*/*.db"))
    assert len(copies) == 3 and all(stat.S_IMODE(p.stat().st_mode) == 0o664 for p in copies)
    for folder, name in ((ALERTS, "alerts.db"), (ALERTS, "market.db"), (PORTFOLIO, "portfolio.db")):
        assert stat.S_IMODE((box.ringvar / folder / name).stat().st_mode) == 0o600, name


@whole_tool
def test_a_rollback_puts_the_ring_back_before_the_ring_starts(tmp_path):
    box = ring_box(tmp_path)
    box.migrations = "1"
    migration_that(box, touch_the_ring(box))
    db = box.ringvar / ALERTS / "alerts.db"
    _shim(
        box.bin,
        "systemctl",
        'echo "$*" >> "${FAKE_WEB}/systemctl"\n'
        'if [[ "$1" == is-active ]]; then echo active; fi\n'
        'if [[ "$1 $2" == "start sudarshan-alerts.service" ]]; then\n'
        f'    sqlite3 "{db}" "SELECT group_concat(n) FROM t" >> "${{FAKE_WEB}}/seen_at_start"\n'
        "fi",
    )
    done = box.run()
    assert done.returncode == 1, done.stdout + done.stderr
    assert (box.web / "seen_at_start").read_text().split() == ["1,2,3"]  # restored by then
    started = (box.web / "systemctl").read_text().splitlines()
    assert "start sudarshan-alerts.service" in started
    assert "start sudarshan-portfolio.service" in started


@whole_tool
def test_a_rollback_never_puts_a_ring_database_into_openalgos_db_folder(tmp_path):
    box = ring_box(tmp_path)
    box.migrations = "1"
    migration_that(box, touch_the_ring(box))
    before = sorted(p.name for p in (box.app / "db").iterdir())
    done = box.run()
    assert done.returncode == 1, done.stdout + done.stderr
    assert sorted(p.name for p in (box.app / "db").iterdir()) == before == [
        "market.db", "openalgo.db",
    ]  # fmt: skip
    assert rows_of(box.app / "db" / "market.db") == [12, 13]  # its own, from the top-level copy
    assert rows_of(box.app / "db" / "openalgo.db") == [10, 11]
    assert rows_of(box.ringvar / ALERTS / "market.db") == [4, 5, 6]  # the ring's, from ring/


@whole_tool
def test_a_rollback_never_restores_the_vault_and_leaves_it_as_it_is(tmp_path):
    """What the owner typed since the backup would be lost by a restore (review R12)."""
    box = ring_box(tmp_path)
    vault = box.app / "db" / "sudarshan-vault"
    vault.mkdir(mode=0o700)
    sealed = vault / "sealed.bin"
    sealed.write_bytes(b"sealed-1")
    sealed.chmod(0o600)
    make_db(vault / "keys.db", [41, 42])  # a database inside the vault folder
    box.migrations = "1"
    migration_that(
        box,
        touch_the_ring(box) + f'\nprintf sealed-2 > "{sealed}"\nsqlite3 "{vault / "keys.db"}" '
        '"INSERT INTO t VALUES (43)"',
    )
    done = box.run()
    assert done.returncode == 1 and "rolling back" in done.stdout, done.stdout + done.stderr
    assert sealed.read_bytes() == b"sealed-2"  # the newer secret survived the rollback
    assert rows_of(vault / "keys.db") == [41, 42, 43]
    assert (
        stat.S_IMODE(vault.stat().st_mode) == 0o700 and stat.S_IMODE(sealed.stat().st_mode) == 0o600
    )
    bdir = the_backup(box)
    assert not [
        p for p in bdir.rglob("*") if "vault" in p.name or p.name in ("sealed.bin", "keys.db")
    ]


@whole_tool
def test_a_ring_folder_that_is_gone_costs_its_database_and_nothing_else(tmp_path):
    box = ring_box(tmp_path)
    box.migrations = "1"
    migration_that(box, f'rm -rf "{box.ringvar / PORTFOLIO}"\n{touch_the_ring(box)} || true')
    done = box.run()
    assert done.returncode == 1 and "rolling back" in done.stdout, done.stdout + done.stderr
    assert f"ring: {PORTFOLIO} is gone - portfolio.db not put back" in done.stdout
    assert not (box.ringvar / PORTFOLIO).exists()  # not made up by a root-run tool
    assert rows_of(box.ringvar / ALERTS / "alerts.db") == [1, 2, 3]  # the rest went back


@whole_tool
def test_a_database_that_cannot_be_put_back_is_logged_and_the_rollback_finishes(tmp_path):
    box = ring_box(tmp_path)
    box.migrations = "1"
    migration_that(box, touch_the_ring(box, journal=False))
    # the ring's user cannot be given the copy: nothing is replaced, and the rest of the rollback runs
    _shim(
        box.bin,
        "chown",
        'echo "$*" >> "${FAKE_WEB}/chown"\n'
        'if [[ "$1" == sudarshan:sudarshan ]]; then exit 1; fi\nexit 0',
    )
    done = box.run()
    assert done.returncode == 1 and "rollback done" in done.stdout, done.stdout + done.stderr
    assert f"WARNING: ring: could not put back {ALERTS}/alerts.db" in done.stdout
    assert rows_of(box.ringvar / ALERTS / "alerts.db") == [1, 2, 3, 999]  # left as it was found
    assert not list(box.ringvar.rglob("*.restoring"))
    assert (box.ringvar / ALERTS / "alerts.db-wal").exists()  # and its journal files untouched
    assert rows_of(box.app / "db" / "openalgo.db") == [10, 11]  # OpenAlgo's own went back


@whole_tool
def test_a_rollback_with_no_ring_state_is_the_rollback_it_was(tmp_path):
    box = install_box(tmp_path)
    make_db(box.app / "db" / "openalgo.db", [10, 11])
    box.migrations = "1"
    migration_that(box, f'sqlite3 "{box.app / "db" / "openalgo.db"}" "INSERT INTO t VALUES (999)"')
    done = box.run()
    assert done.returncode == 1 and "rollback done" in done.stdout, done.stdout + done.stderr
    assert rows_of(box.app / "db" / "openalgo.db") == [10, 11]
    assert "ring: put back" not in done.stdout and not (the_backup(box) / "ring").exists()


# ---- the migration status before a ring service starts ----------------------------------------


def serve_units(box: Box, tag: str, units: dict[str, str]) -> None:
    listing = [{"type": "file", "name": name} for name in units]
    api = f"https://api.github.com/repos/{box.OWNER_REPO}/contents/sudarshan/ops/systemd?ref={tag}"
    box.serve(api, json.dumps(listing).encode())
    raw = f"https://raw.githubusercontent.com/{box.OWNER_REPO}/{tag}/sudarshan/ops/systemd/"
    for name, body in units.items():
        box.serve(raw + name, body.encode())


def status_box(tmp_path: Path) -> Box:
    """v1.0.1 installs and brings three ring services; alerts has two migrate modules, portfolio
    one and feed none. Each status run says what the shim below says and records when it ran."""
    box = ring_box(tmp_path)
    for service, modules in (
        ("alerts", ["migrate.py", "market/migrate.py", "evil name/migrate.py"]),
        ("portfolio", ["migrate.py"]),
        ("feed", ["service.py"]),
    ):
        for module in modules:
            path = box.app / "sudarshan" / service / module
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")
    (box.app / "sudarshan" / "alerts" / "linked").mkdir()
    (tmp_path / "real_migrate.py").write_text("")
    (box.app / "sudarshan" / "alerts" / "linked" / "migrate.py").symlink_to(
        tmp_path / "real_migrate.py"
    )
    _shim(
        box.app / ".venv" / "bin",
        "python",
        'if [[ "$1" == -m ]]; then\n'
        '    echo "$2 $3 $(pwd)" >> "${FAKE_WEB}/python_m"\n'
        '    cp "${FAKE_WEB}/systemctl" "${FAKE_WEB}/systemctl_at_$2"\n'
        '    echo "stores: 2, pending: 1"; echo "second line"\n'
        '    exit "$(cat "${FAKE_WEB}/status_rc" 2>/dev/null || echo 0)"\n'
        'fi\nexit "${FAKE_MIGRATIONS}"',
    )
    _shim(box.bin, "timeout", 'echo "$1" >> "${FAKE_WEB}/timeout"\nshift\nexec "$@"')
    service = UNIT.format(user="sudarshan")
    names = ("sudarshan-alerts", "sudarshan-portfolio", "sudarshan-feed")
    serve_units(box, "v1.0.1", {f"{name}.service": service for name in names})
    return box


@whole_tool
def test_each_services_migration_status_is_logged_before_it_starts(tmp_path):
    box = status_box(tmp_path)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    out = done.stdout
    assert "ring: sudarshan.alerts.migrate --status: stores: 2, pending: 1\n" in out
    assert "ring: sudarshan.alerts.migrate --status: second line\n" in out
    assert "ring: sudarshan.alerts.market.migrate --status: stores: 2, pending: 1\n" in out
    assert "ring: sudarshan.portfolio.migrate --status: stores: 2, pending: 1\n" in out
    assert "sudarshan.feed" not in out and "evil name" not in out and "linked" not in out
    ran = (box.web / "python_m").read_text().splitlines()
    assert sorted(ran) == sorted(
        f"{module} --status {box.app}"
        for module in (
            "sudarshan.alerts.migrate",
            "sudarshan.alerts.market.migrate",
            "sudarshan.portfolio.migrate",
        )
    )  # as the tool's own code tree, from the app folder
    assert set((box.web / "timeout").read_text().split()) == {"60"}  # none can hold the deploy up
    # as the ring's own user, never as root: a migrate module is the release's code, not the tool's
    asked = (box.web / "sudo").read_text().splitlines()
    for module in (
        "sudarshan.alerts.migrate",
        "sudarshan.alerts.market.migrate",
        "sudarshan.portfolio.migrate",
    ):
        assert f"-u sudarshan -H -- {box.app}/.venv/bin/python -m {module} --status" in asked
    # before the service starts: the systemctl log at that moment has no restart of it yet
    for module, unit in (
        ("sudarshan.alerts.migrate", "sudarshan-alerts"),
        ("sudarshan.alerts.market.migrate", "sudarshan-alerts"),
        ("sudarshan.portfolio.migrate", "sudarshan-portfolio"),
    ):
        seen = (box.web / f"systemctl_at_{module}").read_text()
        assert f"restart {unit}.service" not in seen
    assert "restart sudarshan-alerts.service" in (box.web / "systemctl").read_text()


@whole_tool
def test_a_status_that_fails_or_says_too_much_is_cut_short_and_the_install_goes_on(tmp_path):
    box = status_box(tmp_path)
    (box.web / "status_rc").write_text("3")
    long_line = "x" * 600
    _shim(
        box.app / ".venv" / "bin",
        "python",
        'if [[ "$1" == -m ]]; then\n'
        f'    for i in $(seq 1 40); do echo "line $i"; done; echo "{long_line}"\n'
        '    exit "$(cat "${FAKE_WEB}/status_rc")"\nfi\nexit "${FAKE_MIGRATIONS}"',
    )
    done = box.run()
    assert done.returncode == 0 and box.deployed() == "v1.0.1", done.stdout + done.stderr
    assert (
        "WARNING: ring: sudarshan.alerts.migrate --status did not finish cleanly (exit 3)"
        in done.stdout
    )
    lines = [ln for ln in done.stdout.splitlines() if "sudarshan.portfolio.migrate --status:" in ln]
    assert len(lines) == 30 and lines[0].endswith("line 1") and lines[-1].endswith("line 30")
    assert "xxxx" not in done.stdout  # the long line came after the 30th


@whole_tool
def test_a_status_line_is_cut_at_300_characters(tmp_path):
    box = status_box(tmp_path)
    _shim(
        box.app / ".venv" / "bin",
        "python",
        f'if [[ "$1" == -m ]]; then echo "{"y" * 600}"; exit 0; fi\nexit "${{FAKE_MIGRATIONS}}"',
    )
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    longest = max(len(ln) for ln in done.stdout.splitlines() if "--status:" in ln)
    assert longest < 400 and "y" * 300 in done.stdout and "y" * 301 not in done.stdout


@whole_tool
def test_a_rollback_logs_the_status_before_it_starts_a_ring_service(tmp_path):
    box = status_box(tmp_path)
    box.migrations = "1"
    done = box.run()
    assert done.returncode == 1 and "rolling back" in done.stdout, done.stdout + done.stderr
    assert "ring: sudarshan.alerts.migrate --status: stores: 2, pending: 1\n" in done.stdout
    seen = (box.web / "systemctl_at_sudarshan.alerts.migrate").read_text()
    assert "start openalgo" in seen and "start sudarshan-alerts.service" not in seen


@whole_tool
def test_no_status_is_asked_for_a_service_that_has_no_module_or_for_a_timer(tmp_path):
    box = ring_box(tmp_path)  # no sudarshan/<service>/migrate.py anywhere
    serve_units(
        box,
        "v1.0.1",
        {
            "sudarshan-alerts.service": UNIT.format(user="sudarshan"),
            "sudarshan-alerts.timer": "[Timer]\nOnCalendar=daily\n",
        },
    )
    _shim(box.app / ".venv" / "bin", "python", 'echo "$*" >> "${FAKE_WEB}/python_args"\nexit 0')
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert "--status" not in done.stdout
    assert "-m" not in (box.web / "python_args").read_text().split()


# ---- the tool as a whole ----------------------------------------------------------------------


def test_the_ring_steps_are_in_the_tool_and_its_state_base_is_set_in_one_place():
    from test_deploy_calendar import DEPLOY_TOOL

    text = DEPLOY_TOOL.read_text()
    for name in ("ring_db_backup", "ring_db_restore", "ring_status", "ring_name_ok"):
        assert f"{name}()" in text, name
    assert '"${bdir}/ring/${folder}' in text  # under ring/, never at the top of the backup folder
    assert text.count('RING_VAR="/var/lib"') == 1
    assert "/var/lib/sudarshan-" not in text  # every use goes through RING_VAR
