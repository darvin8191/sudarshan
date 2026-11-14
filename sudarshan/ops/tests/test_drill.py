"""M19.8: the restore drill (P8 7.3, 10.8). An archive built by the real backup code over a
pretend server passes; then, one at a time, a torn database, a missing vault, a vault from
another .env, a missing key in .env and a wrong record each fail with their own sentence. The
scratch folder is gone after every outcome, nothing live is touched, and nothing secret is
written anywhere."""

from __future__ import annotations

import io
import json
import sqlite3
import tarfile
from contextlib import closing
from pathlib import Path

import pytest
from backup_fakes import Server, damage, ist

from sudarshan.ops import backup, drill
from sudarshan.ops.events import read_events
from sudarshan.tests.fdwatch import FdWatch
from sudarshan.vault.store import Vault

PEPPER = "p" * 64
OTHER_PEPPER = "q" * 64
SECRET = "a-sealed-value-that-must-never-be-written-out"
ENV = (
    "APP_KEY = 'k-app-key-marker'\n"
    f"API_KEY_PEPPER = '{PEPPER}'\n"
    "DATABASE_URL = 'sqlite:///db/openalgo.db'\n"
    "BROKER_API_KEY = 'k-broker-key-marker'\n"
    "BROKER_API_SECRET = 'k-broker-secret-marker'\n"
)
SUNDAY = ist(2026, 11, 1, 11, 0)  # the first Sunday of the month, 11:00
MONDAY = ist(2026, 11, 2, 17, 0)  # a trading day, after the close


def _openalgo(path: Path, admins: int = 1, keys: int = 1, logins: int = 1) -> None:
    path.unlink(missing_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, is_admin BOOLEAN)")
        db.execute("CREATE TABLE api_keys (id INTEGER PRIMARY KEY, user_id TEXT)")
        db.execute("CREATE TABLE auth (id INTEGER PRIMARY KEY, name TEXT)")
        db.executemany("INSERT INTO users (username, is_admin) VALUES ('owner', 1)", [()] * admins)
        db.executemany("INSERT INTO api_keys (user_id) VALUES ('owner')", [()] * keys)
        db.executemany("INSERT INTO auth (name) VALUES ('owner')", [()] * logins)
        db.commit()


@pytest.fixture
def server(tmp_path):
    made = Server(tmp_path / "server")
    made.populate()
    made.close()
    for side in (made.app / "db").glob("openalgo.db-*"):
        side.unlink()
    _openalgo(made.app / "db" / "openalgo.db")
    (made.app / ".env").write_text(ENV)
    for old in (made.app / "db" / "sudarshan-vault").iterdir():
        old.unlink()
    vault = Vault(made.app / "db" / "sudarshan-vault" / "sudarshan_vault.db", pepper=PEPPER)
    vault.set_secret("upstox_totp_key", SECRET)
    vault._sessionmaker.kw["bind"].dispose()
    journal = made.var / "sudarshan-journal" / "journal.db"
    journal.parent.mkdir()
    with closing(sqlite3.connect(journal)) as db:
        db.execute("CREATE TABLE trips (id INTEGER PRIMARY KEY)")
        db.execute("PRAGMA user_version = 1")
        db.commit()
    yield made
    made.close()


def _archive(server: Server, at=None) -> Path:
    result = backup.run(server.settings(min_gap_s=0), clock=lambda: at or ist(2026, 10, 31))
    assert result.outcome == backup.OK, result
    return server.archive_dir / result.archive


def _go(server: Server, archive: Path | None = None, at=SUNDAY, **kwargs) -> drill.Outcome:
    return drill.run(server.settings(), archive, clock=lambda: at, **kwargs)


def _result(server: Server) -> dict:
    return json.loads((server.state / drill.RESULT_FILE).read_text())


def test_an_archive_the_real_backup_made_passes(server):
    archive = _archive(server)
    before = {p: p.stat().st_mtime_ns for p in server.app.rglob("*") if p.is_file()}
    found = _go(server)
    assert (found.outcome, found.item, found.archive) == (drill.PASSED, "", archive.name)
    assert found.databases >= 5 and found.exit_code == 0
    assert not (server.state / drill.SCRATCH).exists(), "the scratch folder is gone"
    assert {p: p.stat().st_mtime_ns for p in server.app.rglob("*") if p.is_file()} == before
    result = _result(server)
    assert result["outcome"] == "passed" and result["archive"] == archive.name
    assert result["at"] == "2026-11-01T11:00:00+05:30" and result["words"] == ""
    (told,) = [e for e in read_events(server.settings().spool) if e.kind.startswith("drill")]
    assert (told.kind, told.src, told.fact("archive")) == ("drill_passed", "backup", archive.name)


def _repacked(archive: Path, change) -> Path:
    """The archive with ``change(name, data, manifest)`` applied to each member (None drops it),
    under a record made to agree: what a faithful backup of a broken server would hold."""
    import hashlib

    members, manifest = {}, None
    with tarfile.open(archive, "r:gz") as tar:
        for item in tar:
            if item.isreg():
                data = tar.extractfile(item).read()
                if item.name == backup.MANIFEST:
                    manifest = json.loads(data)
                else:
                    members[item.name] = data
    kept = {}
    for name, data in members.items():
        data = change(name, data)
        if data is not None:
            kept[name] = data
    manifest["members"] = [
        {**m, "size": len(kept[m["name"]]), "sha256": hashlib.sha256(kept[m["name"]]).hexdigest()}
        for m in manifest["members"]
        if m["name"] in kept
    ]
    out = archive.with_name("sudarshan-backup-20261031-170000.tar.gz")
    with tarfile.open(out, "w:gz") as tar:
        for name, data in {**kept, backup.MANIFEST: json.dumps(manifest).encode()}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return out


def _fails(server: Server, archive: Path, item: str, name: str = "") -> None:
    found = _go(server, archive)
    assert (found.outcome, found.item, found.exit_code) == (drill.FAILED, item, 1), found
    assert name in found.name
    assert not (server.state / drill.SCRATCH).exists(), "the scratch folder is gone"
    result = _result(server)
    assert result["outcome"] == "failed" and result["words"] == drill.WORDS[item]
    told = [e for e in read_events(server.settings().spool) if e.kind == "drill_failed"]
    assert told and told[-1].fact("item") == item


def test_a_torn_database_fails_and_names_it(server, tmp_path):
    def tear(name, data):
        if name != "db/logs.db":
            return data
        scratch = tmp_path / "torn.db"
        scratch.write_bytes(data)
        with closing(sqlite3.connect(scratch)) as db:
            db.executemany("INSERT INTO t (v) VALUES (?)", [("x" * 500,)] * 200)
            db.commit()
        damage(scratch)
        return scratch.read_bytes()

    _fails(server, _repacked(_archive(server), tear), "database", "logs.db")


def test_a_missing_vault_fails(server):
    drop = lambda name, data: None if name.startswith("db/sudarshan-vault/") else data  # noqa: E731
    _fails(server, _repacked(_archive(server), drop), "vault")


def test_a_vault_from_another_env_fails(server):
    swap = lambda name, data: (  # noqa: E731
        data.replace(PEPPER.encode(), OTHER_PEPPER.encode()) if name == drill.ENV_MEMBER else data
    )
    _fails(server, _repacked(_archive(server), swap), "vault_key")


def test_a_key_missing_from_the_env_fails_and_names_the_key_only(server):
    cut = lambda name, data: (  # noqa: E731
        data.replace(b"BROKER_API_SECRET", b"SOMETHING_ELSE") if name == drill.ENV_MEMBER else data
    )
    _fails(server, _repacked(_archive(server), cut), "env", "BROKER_API_SECRET")
    assert "k-broker" not in (server.state / drill.RESULT_FILE).read_text()


def test_a_wrong_record_fails_before_anything_is_unpacked(server):
    archive = _archive(server)
    whole = archive.read_bytes()
    broken = archive.with_name("sudarshan-backup-20261031-180000.tar.gz")
    broken.write_bytes(whole[: len(whole) // 2])
    _fails(server, broken, "archive")
    edited = _repacked(archive, lambda name, data: data)
    with tarfile.open(edited, "r:gz") as tar:
        items = {i.name: tar.extractfile(i).read() for i in tar if i.isreg()}
    items["db/sandbox.db"] = items["db/sandbox.db"] + b"x"
    with tarfile.open(edited, "w:gz") as tar:
        for name, data in items.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    _fails(server, edited, "archive")


def test_openalgos_database_must_hold_one_admin_a_key_and_a_login(server):
    cases = (
        ((2, 1, 1), "admin"),
        ((0, 1, 1), "admin"),
        ((1, 0, 1), "api_key"),
        ((1, 1, 0), "broker"),
    )
    for day, (rows, name) in enumerate(cases, start=20):
        _openalgo(server.app / "db" / "openalgo.db", *rows)
        _fails(server, _archive(server, ist(2026, 10, day)), "openalgo", name)


def test_a_store_newer_than_this_release_fails(server):
    journal = server.var / "sudarshan-journal" / "journal.db"
    with closing(sqlite3.connect(journal)) as db:
        db.execute("PRAGMA user_version = 99999")
        db.commit()
    _fails(server, _archive(server), "version", "journal.db")


def test_a_member_that_would_leave_the_folder_is_refused(server, tmp_path):
    archive = _archive(server)
    for odd in ("../outside.txt", "/abs.txt"):
        assert not drill._safe(odd)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    with pytest.raises(drill._Fail):
        drill.unpack(evil, scratch)
    assert drill._safe("db/openalgo.db") and archive.is_file()


def test_no_room_and_no_archive_are_said_as_such(server):
    found = _go(server)
    assert (found.outcome, found.item) == (drill.FAILED, "none")
    archive = _archive(server)
    found = _go(server, free_bytes=lambda path: 10)
    assert (found.outcome, found.item, found.archive) == (drill.FAILED, "space", archive.name)
    assert not (server.state / drill.SCRATCH).exists()


def test_the_timers_run_waits_for_a_day_without_trading_and_never_meets_the_market(server):
    _archive(server)
    assert (_go(server, at=MONDAY).outcome, _go(server, at=MONDAY).item) == (
        drill.SKIPPED, "trading_day",
    )  # fmt: skip
    assert not (server.state / drill.RESULT_FILE).exists(), "a run that steps aside records nothing"
    by_hand = _go(server, server.archive_dir / server.archives()[0], at=MONDAY)
    assert by_hand.outcome == drill.PASSED, "the owner's own drill runs after the close"
    in_hours = _go(server, server.archive_dir / server.archives()[0], at=ist(2026, 11, 2, 11, 0))
    assert (in_hours.outcome, in_hours.item) == (drill.SKIPPED, "market")
    server.extra.write_text("2026-11-01\n")  # a Sunday with a special session
    assert _go(server, at=SUNDAY).outcome == drill.SKIPPED


def test_nothing_secret_is_written_or_printed(server, capsys, monkeypatch):
    _archive(server)
    real = drill.run
    monkeypatch.setattr(drill, "run", lambda settings, archive=None: real(
        settings, archive, clock=lambda: SUNDAY))  # fmt: skip
    assert drill.main([], env=server.env()) == 0
    printed = capsys.readouterr()
    assert printed.out.startswith("restore drill: passed (sudarshan-backup-")
    written = (server.state / drill.RESULT_FILE).read_text()
    spool = server.settings().spool.read_text()
    for marker in (SECRET, PEPPER, "k-app-key-marker", "k-broker-key-marker"):
        for text in (printed.out, printed.err, written, spool):
            assert marker not in text
    assert drill.main([str(server.root / "nothing.tar.gz")], env=server.env()) == 2


def test_the_drill_leaves_no_descriptor_open(server):
    archive = _archive(server)
    _go(server, archive)
    watch = FdWatch()
    for _ in range(5):
        assert _go(server, archive).outcome == drill.PASSED
    watch.assert_clean()
