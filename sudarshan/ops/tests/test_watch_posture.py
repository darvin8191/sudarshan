"""The watch's slower readings (M19.5, P8 7.1): the posture read once an hour, AI connections
and Telegram links counted from OpenAlgo's tables, the backup's folder, the drill's result; and
over runs, news told once and what is only shown never told."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from sudarshan.ops import events
from sudarshan.ops.watch import checks, posture, run
from sudarshan.tests.fdwatch import FdWatch

NOW = datetime(2026, 11, 16, 4, 30, tzinfo=UTC).timestamp()  # Monday 10:00 in India
SHIPPED = (
    "OPENALGO_MCP_READ_ONLY = '1'\nMCP_OAUTH_WRITE_SCOPE_ENABLED = 'False'\n"
    "MCP_OAUTH_REQUIRE_APPROVAL = 'True'\nFLASK_DEBUG = 'False'\n"
)
HEAD = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid\n"


def _app(tmp_path: Path, env: str = SHIPPED) -> tuple[Path, Path]:
    app = tmp_path / "app"
    (app / "db" / "sudarshan-vault").mkdir(parents=True, mode=0o700)
    (app / "db" / "sudarshan-vault").chmod(0o700)
    (app / ".env").write_text(env)
    (app / ".env").chmod(0o600)
    return app, app / ".env"


def test_the_locks_and_debug_are_read_from_openalgos_settings():
    shipped = {"OPENALGO_MCP_READ_ONLY": "1", "MCP_OAUTH_WRITE_SCOPE_ENABLED": "False",
               "MCP_OAUTH_REQUIRE_APPROVAL": "True"}  # fmt: skip
    assert posture.mcp(shipped) == {"switched_on": False, "locked": True, "approval": True}
    assert posture.mcp({"MCP_HTTP_ENABLED": "True"}) == {
        "switched_on": True, "locked": False, "approval": False,
    }  # fmt: skip
    assert posture.debug({"FLASK_DEBUG": "True"}) and not posture.debug({})


def test_a_private_place_open_to_others_is_named(tmp_path):
    app, _ = _app(tmp_path)
    etc = tmp_path / "etc"
    etc.mkdir(mode=0o750)
    etc.chmod(0o750)
    assert posture.loose(app, etc) == []
    etc.chmod(0o755)
    (app / ".env").chmod(0o644)
    secret = app / "db" / "sudarshan-vault" / "sudarshan_vault.db"
    secret.write_bytes(b"x")
    secret.chmod(0o640)
    assert posture.loose(app, etc) == ["env", "settings", "vault"]
    assert posture.loose(tmp_path / "nowhere", tmp_path / "none") is None


def test_only_the_rings_ports_are_looked_for_in_the_kernels_table(tmp_path):
    v4, v6 = tmp_path / "tcp", tmp_path / "tcp6"
    v4.write_text(
        HEAD
        + "   0: 0100007F:15CC 00000000:0000 0A 0 0 0\n"  # 5580 on 127.0.0.1
        + "   1: 00000000:15D0 00000000:0000 0A 0 0 0\n"  # 5584 on every address
        + "   2: 00000000:1F90 00000000:0000 0A 0 0 0\n"  # 8080: not the ring's, never named
        + "   3: 0A000005:15CE 0A000009:D431 01 0 0 0\n"  # 5582, a connection, not a listener
        + "garbage\n"
    )
    v6.write_text(
        HEAD
        + "   0: 00000000000000000000000001000000:15CF 0:0000 0A 0 0 0\n"  # 5583 on ::1
        + "   1: 00000000000000000000000000000000:15CD 0:0000 0A 0 0 0\n"  # 5581 on ::
    )
    assert posture.open_ports((v4, v6)) == ["5581", "5584"]
    assert posture.open_ports((tmp_path / "none",)) is None
    v4.write_text(HEAD)
    assert posture.open_ports((v4,)) == []


def test_updates_waiting_are_counted_from_the_systems_own_notice(tmp_path):
    notice = tmp_path / "updates-available"
    notice.write_text(
        "\n12 updates can be applied immediately.\n"
        "3 of these updates are standard security updates.\n"
        "To see these additional updates run: apt list --upgradable\n"
    )
    assert posture.updates(notice) == {"all": 12, "security": 3}
    notice.write_text("\n1 update can be applied immediately.\n")
    assert posture.updates(notice) == {"all": 1, "security": 0}
    notice.write_text("\n")
    assert posture.updates(notice) == {"all": 0, "security": 0}
    assert posture.updates(tmp_path / "none") is None


def test_the_posture_is_read_once_an_hour(tmp_path):
    app, env = _app(tmp_path)
    first = posture.read(NOW, app, env)
    assert first["at"] == NOW and first["mcp"]["locked"] and first["debug"] is False
    env.write_text("FLASK_DEBUG = 'True'\n")
    assert posture.read(NOW + 3000, app, env, first) == first, "inside the hour: not read again"
    later = posture.read(NOW + 3700, app, env, first)
    assert later["at"] == NOW + 3700 and later["debug"] is True and not later["mcp"]["locked"]
    assert posture.read(NOW, app, env, {"at": "x"})["at"] == NOW


def _openalgo(app: Path, *, oauth: bool = True, telegram: bool = True) -> None:
    with closing(sqlite3.connect(app / "db" / "openalgo.db")) as db:
        if oauth:
            db.execute("CREATE TABLE oauth_clients (client_id TEXT, approved BOOLEAN, revoked_at)")
            db.execute("CREATE TABLE oauth_refresh_tokens (family_id TEXT, revoked_at, expires_at)")
            db.executemany(
                "INSERT INTO oauth_clients VALUES (?, ?, ?)",
                [("a", 1, None), ("b", 0, None), ("c", 1, "2026-11-01 00:00:00")],
            )
            db.executemany(
                "INSERT INTO oauth_refresh_tokens VALUES (?, ?, ?)",
                [
                    ("f1", "2026-11-10 00:00:00", "2099-01-01 00:00:00"),  # rotated away
                    ("f1", None, "2099-01-01 00:00:00"),
                    ("f2", None, "2099-01-01 00:00:00"),
                    ("f3", None, "2020-01-01 00:00:00"),  # run out
                    ("f4", "2026-11-10 00:00:00", "2099-01-01 00:00:00"),  # revoked
                ],
            )
        if telegram:
            db.execute("CREATE TABLE telegram_users (telegram_id INTEGER, is_active BOOLEAN)")
            db.executemany("INSERT INTO telegram_users VALUES (?, ?)", [(1, 1), (2, 0)])
        db.commit()


def test_connections_and_links_are_counted_read_only(tmp_path):
    app, env = _app(tmp_path)
    assert posture.counts(app, env) is None, "no database: unread"
    _openalgo(app)
    assert posture.counts(app, env) == {"clients": 1, "families": 2, "links": 1}
    assert not list((app / "db").glob("*-wal")) and not list((app / "db").glob("*-shm"))


def test_a_table_that_was_never_made_counts_as_none(tmp_path):
    app, env = _app(tmp_path)
    _openalgo(app, oauth=False)
    assert posture.counts(app, env) == {"clients": 0, "families": 0, "links": 1}


def test_the_backups_folder_and_the_drills_result(tmp_path):
    folder = tmp_path / "sudarshan-backup"
    assert posture.backup(NOW, folder) == {"age_h": None}
    assert posture.drill(folder) == {}
    (folder / "archive").mkdir(parents=True)
    names = {
        "sudarshan-backup-20260404-163000.tar.gz": 3 << 20,  # the 2025-26 year's archive
        "sudarshan-backup-20261114-163000.tar.gz": 5 << 20,
        "sudarshan-backup-20261115-163000.tar.gz": 6 << 20,  # 17.5 h before NOW
        "sudarshan-backup-20261115-223000-partial.tar.gz": 1 << 20,
        "notes.txt": 10,
    }
    for name, size in names.items():
        with open(folder / "archive" / name, "wb") as handle:
            handle.truncate(size)
    (folder / "status.json").write_text(json.dumps({"offsite": "ok", "outcome": "ok"}))
    seen = posture.backup(NOW, folder)
    assert seen == {"age_h": 17.5, "size_mb": 6, "offsite": "ok", "fy": "2025-26", "fy_mb": 3}
    # Review L4: a year's archive still owed its offsite copy is read from the job's own word.
    for owed, want in (
        ({"fy": "2025-26", "archive": "sudarshan-backup-20260404-163000.tar.gz"}, "2025-26"),
        (None, None), ("2025-26", None), ({"fy": "<b>"}, None), ({"fy": 2025}, None),
    ):  # fmt: skip
        (folder / "status.json").write_text(json.dumps({"offsite": "ok", "fy_owed": owed}))
        assert posture.backup(NOW, folder).get("fy_owed") == want, owed
    (folder / "drill.json").write_text(
        json.dumps({"at": "2026-11-01T11:00:05+05:30", "outcome": "passed", "item": ""})
    )
    found = posture.drill(folder)
    assert (
        found["ok"] is True
        and found["at"] == datetime.fromisoformat("2026-11-01T11:00:05+05:30").timestamp()
    )
    (folder / "drill.json").write_text(json.dumps({"at": "2026-11-01T11:00:05+05:30",
                                                   "outcome": "failed", "item": "vault"}))  # fmt: skip
    assert posture.drill(folder)["ok"] is False
    (folder / "drill.json").write_text("{not json")
    assert posture.drill(folder) == {}


def test_historifys_size(tmp_path):
    app, _ = _app(tmp_path)
    assert posture.historify_gb(app) == 0.0
    with open(app / "db" / "historify.duckdb", "wb") as handle:
        handle.truncate(2_500_000_000)
    assert posture.historify_gb(app) == 2.5


def _runs(tmp_path: Path, readings_by_run) -> run.Settings:
    settings = run.Settings(tmp_path / "state", tmp_path / "app", tmp_path / "app" / ".env")
    at = NOW
    for readings in readings_by_run:
        at += 120
        seen = {**readings, "now": at}
        assert run.run_once(
            settings, clock=lambda at=at: at, gather=lambda *a, s=seen: s, installing=bool, env={}
        ) == 0  # fmt: skip
    return settings


def _counts(clients=0, families=0, links=1) -> dict:
    return {"counts": {"clients": clients, "families": families, "links": links}}


def test_a_new_connection_and_a_new_link_are_each_told_once(tmp_path):
    settings = _runs(
        tmp_path,
        [_counts(), _counts(), _counts(1, 1), _counts(1, 1), _counts(1, 1, 2), _counts(1, 0, 1)],
    )
    told = events.read_events(settings.spool)
    assert [(e.kind, e.fact("check"), e.fact("what")) for e in told] == [
        ("watch_new", "assistant", "connection"),
        ("watch_new", "telegram_links", "linked"),
        ("watch_new", "telegram_links", "unlinked"),
    ]
    assert told[0].fact("clients") == "1" and len({e.id for e in told}) == 3


def test_what_is_only_shown_is_in_the_report_and_never_an_event(tmp_path):
    quiet = {
        "posture": {"reboot": True, "updates": {"all": 4, "security": 2}},
        "backup": {"age_h": 50.0},
        "drill": {},
    }
    settings = _runs(tmp_path, [quiet, quiet])
    assert events.read_events(settings.spool) == []
    levels = {c["name"]: c for c in json.loads(settings.report_file.read_text())["checks"]}
    for name in ("posture_reboot", "posture_updates", "backup", "drill"):
        assert levels[name]["level"] == "warning", name
    assert levels["drill"]["facts"] == {"cause": "never"}
    assert json.loads(settings.state_file.read_text())["posture"] == quiet["posture"]


def test_a_drill_grown_old_and_a_posture_fault_are_told(tmp_path):
    fresh = {"drill": {"at": NOW - 86400, "ok": True}, "posture": {"debug": False}}
    old = {"drill": {"at": NOW - 102 * 86400, "ok": True}, "posture": {"debug": True}}
    settings = _runs(tmp_path, [fresh, old, old, fresh])
    told = [(e.kind, e.fact("check")) for e in events.read_events(settings.spool)]
    assert sorted(told) == [
        ("watch_fine", "posture_debug"),
        ("watch_warning", "drill"),
        ("watch_warning", "posture_debug"),
    ], "the drill's own pass is the drill's to tell"


def test_the_readers_leave_no_descriptor_open(tmp_path):
    app, env = _app(tmp_path)
    _openalgo(app)
    table = tmp_path / "tcp"
    table.write_text(HEAD)
    posture.counts(app, env)
    watch = FdWatch()
    for _ in range(40):
        assert posture.counts(app, env) and posture.read(NOW, app, env)
        posture.open_ports((table,)), posture.loose(app), posture.backup(NOW, tmp_path)
        posture.drill(tmp_path), posture.historify_gb(app)
    watch.assert_clean()
    assert checks.SHOWN_ONLY >= {"openalgo", "backup"} and os.path.isdir(app)
