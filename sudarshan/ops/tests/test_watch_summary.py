"""M19.5, M19.10, M19.13: the server diary and the weekly summary (P8 7.1): every notice kept
once, 8 years of them; one summary on Sunday evening with the week's figures; and the words."""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from pathlib import Path

from backup_fakes import Archive, Server, ist

from sudarshan.alerts.notices import compose
from sudarshan.autologin.messages import BRAND
from sudarshan.ops import backup, events
from sudarshan.ops.watch import checks, diary, readers, run, summary
from sudarshan.tests.fdwatch import FdWatch

SUNDAY_1755 = datetime(2026, 11, 15, 12, 25, tzinfo=UTC).timestamp()  # 17:55 in India
YEAR_END = datetime(2026, 12, 31, 18, 29, tzinfo=UTC).timestamp()  # 23:59 on 31 Dec in India


def _event(spool: Path, ident: str, at: float, kind: str = "installed", src: str = "deploy"):
    assert events.record(spool, kind=kind, event_id=ident, at=at, run=ident, src=src, tag="v1.2.3")


def test_every_notice_is_kept_once_in_its_years_file(tmp_path):
    state, deploy, family = tmp_path / "state", tmp_path / "d.jsonl", tmp_path / "f.jsonl"
    _event(deploy, "a", YEAR_END)
    _event(deploy, "b", YEAR_END + 120)  # 00:01 on 1 Jan 2027 in India
    _event(family, "c", YEAR_END - 60, kind="lockout", src="family")
    spools = [deploy, family, tmp_path / "missing.jsonl"]
    assert diary.keep(state, YEAR_END, spools) == 3
    assert diary.keep(state, YEAR_END, spools) == 0, "nothing is written twice"
    _event(deploy, "d", YEAR_END + 180)
    assert diary.keep(state, YEAR_END, spools) == 1
    old = [json.loads(line) for line in (state / "log" / "2026.jsonl").read_text().splitlines()]
    new = [json.loads(line) for line in (state / "log" / "2027.jsonl").read_text().splitlines()]
    assert [e["id"] for e in old] == ["c", "a"] and [e["id"] for e in new] == ["b", "d"]
    assert old[1] == {"id": "a", "src": "deploy", "kind": "installed", "at": int(YEAR_END),
                      "run": "a", "tag": "v1.2.3"}  # fmt: skip
    assert stat.S_IMODE((state / "log").stat().st_mode) == 0o700
    assert stat.S_IMODE((state / "log" / "2026.jsonl").stat().st_mode) == 0o600


def test_a_lost_memory_writes_a_line_again_but_never_misses_one(tmp_path):
    state, spool = tmp_path / "state", tmp_path / "d.jsonl"
    _event(spool, "a", YEAR_END)
    diary.keep(state, YEAR_END, [spool])
    (state / "log" / diary.SEEN_FILE).write_text("{broken")
    assert diary.keep(state, YEAR_END, [spool]) == 1
    assert len((state / "log" / "2026.jsonl").read_text().splitlines()) == 2
    written, seen = diary.append_new(state, [f"x{n}" for n in range(diary.SEEN_MAX + 50)], [spool])
    assert written == 1 and len(seen) == diary.SEEN_MAX and seen[-1] == "a"


def test_eight_years_are_kept_and_only_year_files_are_removed(tmp_path):
    folder = tmp_path / "log"
    folder.mkdir()
    for name in ("2017.jsonl", "2018.jsonl", "2026.jsonl", "notes.txt", "2001.jsonl.bak"):
        (folder / name).write_text("x\n")
    assert diary.prune(tmp_path, 2026) == ["2017.jsonl"]
    assert sorted(p.name for p in folder.iterdir()) == [
        "2001.jsonl.bak", "2018.jsonl", "2026.jsonl", "notes.txt",
    ]  # fmt: skip
    assert diary.prune(tmp_path / "nowhere", 2026) == []


def test_the_nightly_backup_takes_the_diary(tmp_path):
    server = Server(tmp_path / "server")
    server.populate()
    server.close()
    log = server.var / "sudarshan-watch" / "log"
    log.mkdir(parents=True)
    (log / "2026.jsonl").write_text('{"id":"a"}\n')
    (server.var / "sudarshan-watch" / "state.json").write_text("{}")
    result = backup.run(server.settings(), clock=lambda: ist(2026, 10, 31))
    assert result.outcome == backup.OK
    files = Archive(server.archive_dir / result.archive).files
    assert "ring/sudarshan-watch/log/2026.jsonl" in files
    assert "ring/sudarshan-watch/state.json" not in files


def test_the_summary_is_due_on_sunday_evening_once_a_week():
    assert not summary.due(SUNDAY_1755, None)
    assert summary.due(SUNDAY_1755 + 300, None)
    week = summary.week_of(SUNDAY_1755 + 300)
    assert week == "2026-W46" and not summary.due(SUNDAY_1755 + 600, week)
    assert not summary.due(SUNDAY_1755 + 86400, None), "Monday: next Sunday's, not a late one"
    assert summary.due(SUNDAY_1755 + 7 * 86400 + 300, week)


def test_the_weeks_use_is_counted_from_what_each_run_reads():
    usage = None
    ticks = [0, 0]
    for busy_share in [10] * 96 + [90] * 4:  # 96 quiet runs, 4 busy ones
        ticks = [ticks[0] + busy_share, ticks[1] + 100]
        usage = summary.sample(
            usage, {"cpu": list(ticks), "mem_available_mb": 3000, "mem_total_mb": 12000}
        )
    assert sum(usage["cpu"]) == 99, "the first run has nothing to compare with"
    assert summary.under_for_95(usage["cpu"]) == 15 and summary.under_for_95(usage["mem"]) == 80
    assert summary.under_for_95([0] * summary.BUCKETS) is None
    odd = summary.sample({"cpu": "x", "cpu_prev": [5, 1]}, {"cpu": None, "mem_total_mb": 0})
    assert sum(odd["cpu"]) == 0 and sum(odd["mem"]) == 0 and odd["cpu_prev"] is None
    back = summary.sample({"cpu_prev": [500, 1000]}, {"cpu": [10, 20]})  # counters after a boot
    assert sum(back["cpu"]) == 0 and back["cpu_prev"] == [10, 20]


def test_the_kernels_counters_are_read(tmp_path):
    table = tmp_path / "stat"
    table.write_text("cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 1 2 3 4 5 6 7 8 9 10\nintr 5\n")
    assert readers.cpu(table) == [150, 1000]
    assert readers.cpu(tmp_path / "none") is None
    info = tmp_path / "meminfo"
    info.write_text("MemTotal:       12288000 kB\nMemAvailable:    6144000 kB\n")
    assert readers.mem_total_mb(info) == 12000 and readers.mem_available_mb(info) == 6000


def test_sizes_are_measured_for_sudarshans_and_openalgos_folders_only(tmp_path):
    app, var = tmp_path / "app", tmp_path / "var"
    for path, megabytes in {
        app / "db" / "openalgo.db": 3,
        app / "db" / "historify.duckdb": 5,
        app / "log" / "app.log": 2,
        var / "sudarshan-screener" / "bars" / "x.bin": 4,
        var / "sudarshan-journal" / "journal.db": 1,
        var / "sudarshan-backup" / "archive" / "a.tar.gz": 7,
        var / "other-system" / "big.bin": 50,
    }.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as handle:
            handle.truncate(megabytes << 20)
    (var / "sudarshan-link").symlink_to(var / "other-system")
    assert summary.sizes(app, f"{var}/sudarshan-") == {
        "db": 3, "hist": 5, "log": 2, "ring": 5, "bak": 7,
    }  # fmt: skip
    assert summary.folder_mb(tmp_path / "nowhere") == 0
    assert summary.folder_mb(var, [2]) is None, "a walk past its budget says so"
    assert summary.sizes(tmp_path / "none", str(tmp_path / "none" / "x-")) == {
        "db": 0, "log": 0, "hist": 0,
    }  # fmt: skip


def _run(settings, at, readings):
    return run.run_once(
        settings, clock=lambda: at, gather=lambda *a: {**readings, "now": at}, installing=bool,
        env={}, measure=lambda app: {"db": 1300, "log": 40, "hist": 800, "ring": 2300, "bak": 4100},
    )  # fmt: skip


def test_one_summary_goes_out_on_sunday_evening_with_the_weeks_figures(tmp_path):
    settings = run.Settings(tmp_path / "state", tmp_path / "app", tmp_path / "app" / ".env")
    readings = {
        "disk": {"free_gb": 41.2, "inodes_free_pct": 80.0},
        "posture": {"reboot": True, "updates": {"all": 12, "security": 3}},
        "backup": {"age_h": 1.6},
        "drill": {"at": SUNDAY_1755 - 14 * 86400, "ok": True},
        "mem_available_mb": 4000,
        "mem_total_mb": 12000,
    }
    at, ticks = SUNDAY_1755 - 240, 0
    for _ in range(6):  # 17:53 to 18:03
        at, ticks = at + 120, ticks + 30
        assert _run(settings, at, {**readings, "cpu": [ticks, ticks * 4]}) == 0
    told = [e for e in events.read_events(settings.spool) if e.kind == "watch_summary"]
    assert len(told) == 1, "once, at the first run from 18:00"
    facts = told[0].facts
    assert facts["warning"] == "posture_reboot,posture_updates" and facts["failing"] == ""
    assert (facts["disk_free_gb"], facts["updates_security"], facts["reboot"]) == (
        "41.2",
        "3",
        "yes",
    )
    assert (facts["drill_cause"], facts["drill_days"]) == ("passed", "14")
    assert facts["sizes"] == "bak=4100,db=1300,hist=800,log=40,ring=2300"
    assert (facts["cpu95"], facts["mem95"]) == ("30", "70")
    state = json.loads(settings.state_file.read_text())
    assert state["summary_week"] == "2026-W46" and sum(state["usage"]["cpu"]) == 1
    assert compose(told, datetime.fromtimestamp(at, UTC)).splitlines() == [
        BRAND,
        "Weekly server summary.",
        "Needs you: a restart the system asks for (to look at), security updates waiting "
        "(to look at). The System page has each.",
        f"Checks fine: {facts['fine']}, {facts['unread']} could not be read.",
        "Disk: 41.2 GB free. OpenAlgo's databases 1.3 GB, its logs 40 MB, Historify 800 MB, "
        "SUDARSHAN's own stores 2.2 GB, the backups 4.0 GB.",
        "Newest backup: 1 hours old. Restore test: passed 14 days ago.",
        "System updates waiting: 12, 3 of them security. They are yours to apply by hand on a "
        "non-trading day. The system asks for a restart.",
        "This week the server's processor stayed under 30% and its memory under 70% for 95% of "
        "the time.",
    ]
    diary_lines = (settings.state_dir / "log" / "2026.jsonl").read_text().splitlines()
    assert [json.loads(line)["kind"] for line in diary_lines] == ["watch_summary"]


def test_a_quiet_week_says_nothing_needs_you():
    quiet = events.Event("s", "watch_summary", SUNDAY_1755, "s", "watch",
                         {"failing": "", "warning": "", "fine": "25", "unread": "0"})  # fmt: skip
    assert compose([quiet], datetime.fromtimestamp(SUNDAY_1755, UTC)).splitlines() == [
        BRAND, "Weekly server summary.", "Nothing needs you.", "Checks fine: 25.",
    ]  # fmt: skip
    odd = events.Event("s", "watch_summary", SUNDAY_1755, "s", "watch",
                       {"failing": "<b>", "sizes": "db=x", "fine": "many", "cpu95": "9"})  # fmt: skip
    assert compose([odd], datetime.fromtimestamp(SUNDAY_1755, UTC)).splitlines()[1:] == [
        "Weekly server summary.", "Nothing needs you.",
    ]  # fmt: skip
    assert set(checks.SHOWN_ONLY) <= {r.name for r in checks.run_checks({}, {})}


def test_the_diary_and_the_summary_leave_no_descriptor_open(tmp_path):
    spool = tmp_path / "d.jsonl"
    _event(spool, "a", YEAR_END)
    diary.keep(tmp_path / "state", YEAR_END, [spool])
    watch = FdWatch()
    for number in range(30):
        _event(spool, f"e{number}", YEAR_END + number)
        diary.keep(tmp_path / "state", YEAR_END, [spool])
        summary.sizes(tmp_path, f"{tmp_path}/s-"), readers.cpu(), readers.mem_total_mb()
    watch.assert_clean()
