"""F1: family.db: its schema in steps, --status that changes nothing, the append-only audit, the
private file, the one writer thread, versions, secrets at rest and the outbox rule (Y44)."""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import socket
import sqlite3
import stat
import threading
import time
from pathlib import Path

import pytest
from family_testkit import add_member, close, open_store, raw, secrets, set_version, store_key_text
from sqlalchemy.pool import NullPool

from sudarshan.family import audit, migrate, migrations
from sudarshan.family.errors import NotFound, SchemaTooNew, StoreError, VersionConflict
from sudarshan.family.store import FamilyStore, Secrets, tables_of
from sudarshan.tests.fdwatch import FdWatch

TABLES = {
    "member", "credential", "invite", "viewer_session", "push_subscription", "telegram_link",
    "share", "symbol_map", "outbox", "audit", "job_run",
}  # fmt: skip


@pytest.fixture
def folder(tmp_path):
    return tmp_path / "state"


@pytest.fixture
def opened(folder):
    made = open_store(folder)
    yield made
    close(made)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def status_of(path: Path, *extra: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = migrate.main(["--path", str(path), *extra])
    return code, out.getvalue()


# ---- migrations (design 4.3) -------------------------------------------------------------------
def test_a_new_file_gets_every_table_at_version_1_in_wal(opened):
    def look(tx):
        triggers = {
            r[0]
            for r in tx.conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        mode = tx.conn.exec_driver_sql("PRAGMA journal_mode").scalar()
        return set(tables_of(tx.conn)), tx.user_version(), triggers, mode

    tables, version, triggers, mode = opened.read_now(look)
    assert tables == TABLES and version == migrations.LATEST == 1
    assert triggers == {"audit_no_update", "audit_no_delete"}
    assert mode == "wal" and opened.applied == [1]


def test_opening_again_runs_no_step(folder, opened):
    again = FamilyStore(folder / "family.db", secrets())
    try:
        assert again.applied == []
    finally:
        again.close()


def test_a_populated_copy_forced_back_to_the_old_version_keeps_every_row(folder, opened):
    member = add_member(opened)
    opened.write_now(lambda tx: audit.record(tx, at=5, actor="owner", kind="member_created",
                                             member_id=member["id"]))  # fmt: skip
    close(opened)
    set_version(folder / "family.db", 0)  # what the file of the release before V1 would say
    again = FamilyStore(folder / "family.db", secrets())
    try:
        assert again.applied == [1]
        rows = again.read_now(lambda tx: (tx.count("member"), tx.count("audit"), tx.user_version()))
        assert rows == (1, 1, 1)
    finally:
        close(again)


def test_a_newer_file_is_refused_in_the_owners_words_and_left_as_it_was(folder, opened):
    add_member(opened)
    close(opened)
    path = folder / "family.db"
    set_version(path, 99)
    before = digest(path)
    with pytest.raises(SchemaTooNew) as caught:
        FamilyStore(path, secrets())
    assert str(caught.value) == (
        "The family data was written by a newer release. Install that release again; nothing "
        "was changed."
    )
    assert caught.value.code == "store_newer" and (caught.value.found, caught.value.known) == (
        99,
        1,
    )
    assert digest(path) == before
    code, said = status_of(path, "--status")
    assert code == 2 and "version: file 99, code 1" in said and "newer release" in said


def test_status_with_no_file_makes_none(folder):
    code, said = status_of(folder / "family.db", "--status")
    assert code == 0 and "version: file none, code 1" in said and "pending: [1]" in said
    assert not folder.exists()


def test_status_opens_read_only_and_changes_nothing(folder, opened):
    add_member(opened)
    close(opened)
    path = folder / "family.db"
    before = (sorted(p.name for p in folder.iterdir()), digest(path))
    code, said = status_of(path, "--status")
    assert code == 0 and "version: file 1, code 1" in said and "pending: none" in said
    assert (sorted(p.name for p in folder.iterdir()), digest(path)) == before


def test_status_runs_by_module_name_as_the_deploy_runs_it(folder, opened):
    import subprocess
    import sys

    close(opened)
    done = subprocess.run(
        [sys.executable, "-m", "sudarshan.family.migrate", "--status"],
        capture_output=True, text=True, timeout=60, check=False,
        env={**os.environ, "STATE_DIRECTORY": str(folder)},
        cwd=Path(__file__).resolve().parents[3],
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert f"file:    {folder / 'family.db'}" in done.stdout


def test_migrate_refuses_while_the_service_holds_its_port(folder):
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        code, said = status_of(folder / "family.db", "--port", str(held.getsockname()[1]))
    assert code == 2 and "The family service is running" in said
    assert not (folder / "family.db").exists()


def test_migrate_applies_when_the_service_is_down(folder):
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    code, said = status_of(folder / "family.db", "--port", str(port))
    close(None)
    assert code == 0 and "applied: [1]" in said


# ---- the audit is append-only (M2.12; mutation target 22) ----------------------------------------
def test_the_audit_refuses_update_and_delete_through_the_store_and_plain_sqlite(folder, opened):
    for n in range(3):
        opened.write_now(lambda tx, n=n: audit.record(tx, at=n, actor="system", kind="view"))
    with pytest.raises(StoreError):
        opened.write_now(lambda tx: tx.update("audit", {"id": 1}, {"kind": "login_ok"}))
    with pytest.raises(StoreError):
        opened.write_now(lambda tx: tx.delete("audit", id=2))
    con = raw(folder / "family.db")
    try:
        for sql in ("UPDATE audit SET actor = 'owner'", "DELETE FROM audit"):
            with pytest.raises(sqlite3.DatabaseError, match="audit is append-only"):
                con.execute(sql)
    finally:
        con.close()
    assert opened.read_now(lambda tx: tx.count("audit")) == 3


def test_audit_pages_are_newest_first_by_member_and_kind_with_older(opened):
    kinds = ("login_ok", "view", "login_failed")

    def fill(tx):
        for n in range(450):
            audit.record(tx, at=n, actor=audit.member_actor(1 + n % 2), member_id=1 + n % 2,
                         kind=kinds[n % 3], session="ab" * 32, ip_hash="cd" * 32)  # fmt: skip

    opened.write_now(fill)
    first = opened.read_now(lambda tx: audit.page(tx))
    assert len(first["lines"]) == 200 and first["lines"][0]["id"] == 450
    assert first["older"] == first["lines"][-1]["id"] == 251
    line = first["lines"][0]
    assert line["device"] == "ab" * 8 and line["place"] == "cd" * 4
    assert set(line) == {"id", "at", "actor", "member_id", "kind", "device", "place", "detail"}
    second = opened.read_now(lambda tx: audit.page(tx, before=first["older"]))
    third = opened.read_now(lambda tx: audit.page(tx, before=second["older"]))
    assert len(third["lines"]) == 50 and third["older"] is None
    mine = opened.read_now(lambda tx: audit.page(tx, member_id=2, kind="view", limit=1000))
    assert mine["lines"] and all(
        line["member_id"] == 2 and line["kind"] == "view" for line in mine["lines"]
    )
    assert len(mine["lines"]) == 75


def test_audit_detail_is_plain_short_and_the_kind_is_the_contracts(opened):
    def write(tx):
        audit.record(
            tx,
            at=1,
            actor="owner",
            kind="member_updated",
            detail={"fields": ["language"], "long": "x" * 500, "odd": object()},
        )
        audit.record(tx, at=2, actor="owner", kind="member_updated",
                     detail={str(n): "y" * 199 for n in range(40)})  # fmt: skip

    opened.write_now(write)
    lines = opened.read_now(lambda tx: audit.page(tx))["lines"]
    assert lines[0]["detail"] == {"cut": True}
    assert len(lines[1]["detail"]["long"]) == 200 and isinstance(lines[1]["detail"]["odd"], str)
    with pytest.raises(ValueError):
        opened.write_now(lambda tx: audit.record(tx, at=1, actor="owner", kind="no_such_kind"))


# ---- files, threads and writers -------------------------------------------------------------------
def test_the_folder_and_the_file_are_private_whatever_the_umask(folder):
    old = os.umask(0o022)
    try:
        made = open_store(folder)
        add_member(made)
    finally:
        os.umask(old)
    try:
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700
        for name in ("family.db", "family.db-wal", "family.db-shm"):
            if (folder / name).exists():
                assert stat.S_IMODE((folder / name).stat().st_mode) == 0o600, name
    finally:
        close(made)


def test_every_call_runs_on_the_one_store_thread(opened):
    seen = []

    def look(tx):
        seen.append((threading.current_thread().name, threading.get_ident()))
        return tx.count("member")

    opened.read_now(look)
    opened.write_now(look)
    found = []
    workers = [threading.Thread(target=lambda: found.append(opened.read_now(look))) for _ in "ab"]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)
    assert len(seen) == 4 and len({ident for _, ident in seen}) == 1
    assert seen[0][0].startswith("family-store")


def test_two_writers_at_their_first_write_both_land(opened):
    gate = threading.Barrier(2)
    errors = []

    def one(label):
        try:
            gate.wait(5)
            add_member(opened, display_name=f"Member {label}")
        except Exception as exc:  # noqa: BLE001 - the assertion below names it
            errors.append(exc)

    workers = [threading.Thread(target=one, args=(label,)) for label in "BC"]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)
    assert errors == []
    assert opened.read_now(lambda tx: tx.count("member")) == 2


def test_a_write_waits_for_another_program_holding_the_write_lock(folder, opened):
    """BEGIN IMMEDIATE queues behind another writer (the migrate command, a copy) and lands."""
    con = raw(folder / "family.db")
    con.execute("BEGIN IMMEDIATE")
    release = threading.Timer(0.4, lambda: con.execute("COMMIT"))
    release.start()
    try:
        began = time.monotonic()
        add_member(opened)
        assert time.monotonic() - began >= 0.3
    finally:
        release.join()
        con.close()
    assert opened.read_now(lambda tx: tx.count("member")) == 1


def test_the_engine_has_no_pool_and_leaves_no_descriptor(folder):
    watch = FdWatch()
    made = open_store(folder)
    assert isinstance(made.engine.pool, NullPool)
    add_member(made)
    made.read_now(lambda tx: tx.count("member"))
    close(made)
    watch.assert_clean()


# ---- versions and secrets ---------------------------------------------------------------------------
def test_a_change_names_the_version_it_was_decided_on(opened):
    member = add_member(opened)
    changed = opened.write_now(lambda tx: tx.member_change(member["id"], 1, {"language": "gu"}, 9))
    assert (changed["version"], changed["language"], changed["updated_at"]) == (2, "gu", 9)
    for stale in (1, None, "2", True):
        with pytest.raises(VersionConflict):
            opened.write_now(lambda tx, v=stale: tx.member_change(member["id"], v, {}, 10))
    with pytest.raises(NotFound) as gone:
        opened.write_now(lambda tx: tx.member_change(member["id"] + 7, 1, {}, 10))
    assert "no longer there" in str(gone.value)
    assert opened.read_now(lambda tx: tx.member(member["id"]))["version"] == 2


def test_a_retried_create_cannot_make_a_second_row(opened):
    add_member(opened, request_id="r-1")
    with pytest.raises(StoreError):
        add_member(opened, request_id="r-1")
    assert opened.read_now(lambda tx: tx.count("member")) == 1


def test_secrets_at_rest_are_sealed_with_the_store_key(folder):
    keys = secrets()
    made = open_store(folder, keys)
    member = add_member(made)
    secret = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"  # a well-known example, not a member's
    made.write_now(
        lambda tx: tx.insert(
            "credential",
            {"member_id": member["id"], "password_hash": "x", "totp_secret_enc": keys.seal(secret),
             "password_changed_at": 1, "updated_at": 1},
        )
    )  # fmt: skip
    sealed = made.read_now(lambda tx: tx.row("credential", member_id=member["id"]))
    close(made)
    assert keys.open(sealed["totp_secret_enc"]) == secret
    for name in ("family.db", "family.db-wal"):
        if (folder / name).exists():
            assert secret.encode() not in (folder / name).read_bytes()
    with pytest.raises(StoreError):
        secrets().open(sealed["totp_secret_enc"])


def test_the_store_key_is_read_as_root_writes_it():
    text = store_key_text()
    keys = Secrets.from_text(text)
    assert keys.place("203.0.113.9") == Secrets.from_text(text).place("203.0.113.9")
    assert keys.place("203.0.113.9") != keys.place("203.0.113.10")
    assert keys.place("203.0.113.9") != Secrets.from_text(store_key_text()).place("203.0.113.9")
    for bad in ("", "abc", "A" * 44 + "\n", "-" * 43 + "=", text.replace("=", "")[:20]):
        with pytest.raises(ValueError):
            Secrets.from_text(bad)


def test_an_outbox_row_keeps_no_payload_once_sent(opened):
    """Cross-design Y44: the masked text can hold the owner's figures; the schema refuses to keep it
    past the send."""
    row = {"member_id": 1, "channel": "push", "category": "digest", "payload": '{"body":"x"}',
           "urgency": "low", "ttl_s": 600, "due_at": 1, "created_at": 1}  # fmt: skip
    new = opened.write_now(lambda tx: tx.insert("outbox", row))
    with pytest.raises(StoreError):
        opened.write_now(lambda tx: tx.update("outbox", {"id": new}, {"sent_at": 2}))
    opened.write_now(
        lambda tx: tx.update("outbox", {"id": new}, {"sent_at": 2, "payload": None, "result": "ok"})
    )
    assert opened.read_now(lambda tx: tx.row("outbox", id=new))["payload"] is None
    with pytest.raises(StoreError):
        opened.write_now(lambda tx: tx.insert("outbox", {**row, "sent_at": 3}))


def test_a_file_that_cannot_be_opened_is_said_in_words(tmp_path):
    folder = tmp_path / "state"
    folder.mkdir()
    (folder / "family.db").write_bytes(b"this is not a database at all" * 100)
    with pytest.raises(StoreError) as caught:
        FamilyStore(folder / "family.db", secrets())
    close(None)
    assert "cannot be read on the server" in str(caught.value) and caught.value.cause
