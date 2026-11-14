"""F11: the owner's notices in the export spool (0640, src family, never a name; a sign-in name only
when the owner turned naming on), the store's copy for the nightly backup (family-copy.db at 16:20
and 22:20: an online backup, journal_mode DELETE, 0640, whole or not at all), and the audit op the
owner's page reads (design 2.6, 4.4; cross-design Y9)."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path

import pyotp
import pytest
from family_net import ask, fast_hasher, keys, running, sealed, settings
from family_testkit import add_member, close, open_store, set_version

from sudarshan.family import backup
from sudarshan.family.accounts import Accounts, device_from
from sudarshan.family.notices import Notices
from sudarshan.family.service import FamilyService
from sudarshan.family.store import FamilyStore
from sudarshan.ops import events

PASSWORD = "a long synthetic passphrase"
PHONE = device_from("Mozilla/5.0 (Linux; Android 15; K) Chrome/142.0.0.0 Mobile")
OTHER = device_from("Mozilla/5.0 (Android 13; Mobile) Firefox/133.0")


def ist_ms(day: str, clock: str) -> int:
    """An IST wall-clock instant as epoch ms (``2026-11-16``, ``16:20``)."""
    stamp = datetime.fromisoformat(f"{day}T{clock}:00+05:30")
    return int(stamp.astimezone(UTC).timestamp() * 1000)


class Flow:
    """Accounts writing their notices to a spool of the test's own."""

    def __init__(self, root: Path, name_members: bool = False):
        self.at = ist_ms("2026-11-16", "11:00")
        self.store = open_store(root / "state")
        self.spool = root / "export" / "events.jsonl"
        self.notices = Notices(self.spool, name_members=name_members, clock=lambda: self.at)
        self.acc = Accounts(self.store, clock=lambda: self.at, hasher=fast_hasher(),
                            notify=self.notices)  # fmt: skip

    def run(self, coro):
        return asyncio.run(coro)

    def joined(self):
        card = self.run(self.acc.member_create({"display_name": "Member A", "role": "viewer"}))
        invite = self.run(self.acc.invite_create({"member_id": card["id"]}))
        secret = self.run(self.acc.join_info(invite["code"]))["totp_key"]
        code = pyotp.TOTP(secret).at(self.at // 1000)
        self.run(self.acc.join(invite["code"], "member_a", PASSWORD, code, PHONE))
        return card, secret

    def lines(self) -> list[events.Event]:
        return events.read_events(self.spool)


@pytest.fixture
def flow(tmp_path):
    made = Flow(tmp_path)
    yield made
    close(made.store)


# ---- notices --------------------------------------------------------------------------------------
def test_a_lockout_is_one_line_the_desk_can_word_and_names_no_one(flow):
    from sudarshan.alerts import notices as desk

    flow.joined()
    old = os.umask(0o077)  # the unit's UMask: the spool is still 0640 for the desk's group
    try:
        for _ in range(5):
            flow.at += 1_000
            with pytest.raises(Exception):  # noqa: B017 - the refusal itself is F3's to pin
                flow.run(flow.acc.login("member_a", "a wrong passphrase!", "000000", PHONE))
    finally:
        os.umask(old)
    lockouts = [e for e in flow.lines() if e.kind == "lockout"]
    assert len(lockouts) == 1
    line = lockouts[0]
    assert line.src == "family" and line.fact("attempts") == "5"
    assert line.fact("device") == PHONE.label and int(line.fact("until")) == (
        flow.at // 1000 + 60  # review M2: the first lock of a run is a minute
    )
    assert line.fact("handle") == "" and line.fact("name_members") == ""
    assert stat.S_IMODE(flow.spool.stat().st_mode) == 0o640
    text = flow.spool.read_text()
    assert "Member A" not in text and "member_a" not in text
    worded = desk._lockout(line, datetime.now(UTC))[0]
    assert worded.startswith("A family sign-in was locked after 5 wrong attempts (Android 15")


def test_a_new_phone_an_invite_used_and_a_removal_are_noticed(flow):
    card, secret = flow.joined()
    flow.at += 30_000
    flow.run(flow.acc.login("member_a", PASSWORD, pyotp.TOTP(secret).at(flow.at // 1000), OTHER))
    flow.run(flow.acc.member_remove({"id": card["id"], "version": 2}))
    kinds = [e.kind for e in flow.lines()]
    assert kinds == ["invite_used", "new_device", "member_removed"]
    assert [e for e in flow.lines() if e.kind == "new_device"][0].fact("device") == OTHER.label
    assert "Member A" not in flow.spool.read_text() and "member_a" not in flow.spool.read_text()


def test_the_sign_in_name_goes_in_only_when_the_owner_turned_naming_on(tmp_path):
    from sudarshan.alerts import notices as desk

    named = Flow(tmp_path, name_members=True)
    try:
        card, _ = named.joined()
        named.run(named.acc.member_remove({"id": card["id"], "version": 2}))
        removed = [e for e in named.lines() if e.kind == "member_removed"][0]
        assert removed.fact("handle") == "member_a" and removed.fact("name_members") == "1"
        assert desk._member_removed(removed, datetime.now(UTC))[0].startswith(
            "Family member member_a was removed"
        )
        assert "Member A" not in named.spool.read_text()  # the display name, never
    finally:
        close(named.store)


def test_a_notice_that_cannot_be_written_is_counted_and_never_raises(tmp_path):
    (tmp_path / "export").write_text("a file where the folder should be")
    notices = Notices(tmp_path / "export" / "events.jsonl")
    assert notices("invite_used") is False and notices.counts == {"written": 0, "failed": 1}
    with pytest.raises(ValueError):
        notices("not_a_kind")


def test_a_newer_store_is_noticed_once_however_often_the_service_starts(tmp_path):
    made = FamilyStore(tmp_path / "state" / "family.db")
    made.close()
    close(None)
    set_version(tmp_path / "state" / "family.db", 7)

    async def start_once():
        service = FamilyService(settings(tmp_path), keys(), hasher=fast_hasher())
        async with running(service):
            pass

    for _ in range(2):
        asyncio.run(start_once())
        close(None)
    newer = [e for e in events.read_events(tmp_path / "export" / "events.jsonl")
             if e.kind == "store_newer"]  # fmt: skip
    assert len(newer) == 1 and newer[0].id == "family-store_newer-v7"


def test_the_spool_is_the_one_the_desk_reads():
    from sudarshan.family.config import Settings

    assert Settings().export_dir / "events.jsonl" == events.FAMILY_SPOOL


# ---- the copy for the nightly backup (Y9) ------------------------------------------------------------
@pytest.mark.parametrize(
    ("clock", "job", "day"),
    [
        ("03:00", "copy-2220", 20261115),
        ("16:19", "copy-2220", 20261115),
        ("16:20", "copy-1620", 20261116),
        ("22:19", "copy-1620", 20261116),
        ("22:20", "copy-2220", 20261116),
        ("23:59", "copy-2220", 20261116),
    ],
)
def test_the_slot_due_is_the_latest_at_or_before_now_in_india(clock, job, day):
    assert backup.due(ist_ms("2026-11-16", clock)) == (job, day)


def test_the_copy_is_whole_private_to_the_ring_and_needs_no_side_file(tmp_path):
    opened = open_store(tmp_path / "state")
    try:
        for n in range(3):
            add_member(opened, display_name=f"Member {n}")
        folder = tmp_path / "export"
        folder.mkdir()
        folder.chmod(0o2750)
        old = os.umask(0o077)
        try:
            size = backup.make_copy(opened.path, folder)
        finally:
            os.umask(old)
    finally:
        close(opened)
    copy = folder / backup.COPY_NAME
    assert size == copy.stat().st_size > 0
    assert stat.S_IMODE(copy.stat().st_mode) == 0o640
    assert (
        copy.stat().st_gid == folder.stat().st_gid
    )  # the folder's group (sudarshan on the server)
    assert sorted(p.name for p in folder.iterdir()) == [backup.COPY_NAME]  # no -wal, no temp file
    assert copy.read_bytes()[18:20] == b"\x01\x01"  # a rollback-journal file, not WAL
    reader = sqlite3.connect(f"file:{copy}?mode=ro&immutable=1", uri=True)
    try:
        assert reader.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert reader.execute("SELECT count(*) FROM member").fetchone() == (3,)
        assert reader.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    finally:
        reader.close()


def test_a_copy_that_fails_leaves_the_last_one_and_no_temp_file(tmp_path):
    opened = open_store(tmp_path / "state")
    folder = tmp_path / "export"
    folder.mkdir()
    try:
        backup.make_copy(opened.path, folder)
        before = (folder / backup.COPY_NAME).read_bytes()
        (tmp_path / "state" / "family.db").chmod(0o000)
        if os.geteuid() != 0:  # root opens a 000 file anyway; pretend the source is gone instead
            with pytest.raises((sqlite3.Error, OSError)):
                backup.make_copy(opened.path, folder)
        with pytest.raises((sqlite3.Error, OSError)):
            backup.make_copy(tmp_path / "state" / "missing.db", folder)
        assert (folder / backup.COPY_NAME).read_bytes() == before
        assert sorted(p.name for p in folder.iterdir()) == [backup.COPY_NAME]
        with pytest.raises(FileNotFoundError):
            backup.make_copy(opened.path, tmp_path / "not-made-yet")
    finally:
        (tmp_path / "state" / "family.db").chmod(0o600)
        close(opened)


def test_each_slot_is_copied_once_a_day_and_a_missed_one_at_the_next_start(tmp_path):
    class Seat:
        def __init__(self):
            self.store = open_store(tmp_path / "state")
            self.settings = settings(tmp_path)
            self.at = ist_ms("2026-11-16", "18:00")  # a start after the 16:20 slot: caught up
            self.copy_state = {"at": None, "ok": None}

        def clock(self):
            return self.at

    seat = Seat()
    (tmp_path / "export").mkdir()
    try:
        assert asyncio.run(backup.run_due(seat)) is True
        assert asyncio.run(backup.run_due(seat)) is None  # the same slot: nothing to do
        seat.at = ist_ms("2026-11-16", "22:20")
        assert asyncio.run(backup.run_due(seat)) is True
        runs = seat.store.read_now(lambda tx: tx.rows("job_run", "day,name"))
        assert [(r["name"], r["day"], r["status"]) for r in runs] == [
            ("copy-1620", 20261116, "ok"),
            ("copy-2220", 20261116, "ok"),
        ]
        assert seat.copy_state["ok"] is True
        (tmp_path / "export" / backup.COPY_NAME).unlink()
        (tmp_path / "export").rmdir()
        seat.at = ist_ms("2026-11-17", "16:21")
        assert asyncio.run(backup.run_due(seat)) is False and seat.copy_state["ok"] is False
        assert asyncio.run(backup.run_due(seat)) is False  # tried again, not marked done
    finally:
        close(seat.store)


# ---- the audit op for the owner's page ---------------------------------------------------------------
def test_the_audit_op_pages_by_member_and_kind(tmp_path):
    async def scenario():
        service = FamilyService(settings(tmp_path), keys(), hasher=fast_hasher())
        async with running(service):
            port, admin = service.listener.port, service.keys.admin

            async def owner(op, **fields):
                status, _, answer = await ask(port, "POST", f"/v1/admin/{op}",
                                              body=sealed(admin, op, **fields))  # fmt: skip
                return status, answer

            ids = []
            for n in range(3):
                ids.append((await owner("member_create", display_name=f"M{n}",
                                        role="viewer"))[1]["data"]["id"])  # fmt: skip
                await owner("invite_create", member_id=ids[-1])
            status, answer = await owner("audit_list")
            page = answer["data"]
            assert status == 200 and [line["kind"] for line in page["lines"]][:2] == [
                "invite_created",
                "member_created",
            ]
            assert page["older"] is None and len(page["lines"]) == 6
            mine = (await owner("audit_list", member_id=ids[1]))[1]["data"]["lines"]
            assert {line["member_id"] for line in mine} == {ids[1]} and len(mine) == 2
            kind = (await owner("audit_list", kind="member_created", limit=2))[1]["data"]
            assert len(kind["lines"]) == 2 and kind["older"] == kind["lines"][-1]["id"]
            older = (await owner("audit_list", kind="member_created",
                                 before=kind["older"]))[1]["data"]  # fmt: skip
            assert len(older["lines"]) == 1 and older["older"] is None
            for bad in ({"member_id": "1"}, {"kind": "nope"}, {"limit": 0}, {"limit": 201},
                        {"before": True}):  # fmt: skip
                status, answer = await owner("audit_list", **bad)
                assert (status, answer["code"]) == (400, "invalid"), bad
                assert answer["field"] == next(iter(bad))

    asyncio.run(scenario())
    close(None)


def test_a_hundred_copies_and_notices_leave_no_descriptor(tmp_path):
    from sudarshan.tests.fdwatch import FdWatch

    opened = open_store(tmp_path / "state")
    add_member(opened)
    folder = tmp_path / "export"
    folder.mkdir()
    notices = Notices(folder / "events.jsonl")
    try:
        backup.make_copy(opened.path, folder)
        watch = FdWatch()
        for _ in range(100):
            backup.make_copy(opened.path, folder)
            notices("invite_used")
        watch.assert_clean()
        assert sorted(p.name for p in folder.iterdir()) == ["events.jsonl", backup.COPY_NAME]
    finally:
        close(opened)


def test_a_new_spool_takes_its_mode_whatever_the_umask_and_an_old_one_keeps_its_own(tmp_path):
    """``ops.events.record(..., file_mode=0o640)`` under the unit's UMask=0077 made a 0600 file the
    alerts could not read; a new file now takes the mode it asks for, and an existing file is left
    as it is."""
    spool = tmp_path / "events.jsonl"
    old = os.umask(0o077)
    try:
        assert events.record(spool, kind="invite_used", event_id="a-1", at=1e9, run="a-1",
                             src="family", file_mode=0o640)  # fmt: skip
        assert stat.S_IMODE(spool.stat().st_mode) == 0o640
        spool.chmod(0o600)
        assert events.record(spool, kind="invite_used", event_id="a-2", at=1e9, run="a-2",
                             src="family", file_mode=0o640)  # fmt: skip
        assert stat.S_IMODE(spool.stat().st_mode) == 0o600
    finally:
        os.umask(old)
    assert [e.id for e in events.read_events(spool)] == ["a-1", "a-2"]
