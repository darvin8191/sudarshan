"""M18.11: the systemd units of the nightly backup, read the way the deploy tool and systemd read them.

The deploy tool installs every ``sudarshan-*.service`` and ``.timer`` in ``ops/systemd`` after its
own unit policy has looked at the file; a unit the policy refuses never runs, and the owner is told.
Beyond that: the service is sandboxed, writes only its state folder, and is the kind of unit the
tool can restart without waiting for a whole backup; the timer fires only after the market hours the
program itself keeps out of.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sudarshan.autologin.schedule import minutes
from sudarshan.ops import backup

REPO = Path(__file__).resolve().parents[3]
UNITS = REPO / "sudarshan" / "ops" / "systemd"
DEPLOY_TOOL = REPO / "sudarshan" / "ops" / "deploy" / "sudarshan-deploy"
SERVICE = UNITS / "sudarshan-backup.service"
TIMER = UNITS / "sudarshan-backup.timer"
bash = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def directives(path: Path, key: str) -> list[str]:
    pattern = re.compile(rf"^\s*{key}\s*=\s*(.*?)\s*$")
    return [m.group(1) for line in path.read_text().splitlines() if (m := pattern.match(line))]


def policy_says(unit: Path, name: str) -> tuple[int, str]:
    """The deploy tool's unit_ok(), cut out of the script exactly as it ships, on ``unit``."""
    text = DEPLOY_TOOL.read_text()
    start = text.index("unit_ok() {")
    function = text[start : text.index("\n}\n", start) + 3]
    script = 'log() { echo "$*"; }\n' + function + '\nunit_ok "$1" "$2"\n'
    done = subprocess.run(
        ["bash", "-c", script, "policy", str(unit), name], capture_output=True, text=True
    )
    return done.returncode, done.stdout + done.stderr


# ---- the deploy tool's policy -----------------------------------------------------------------


@bash
@pytest.mark.parametrize("unit", [SERVICE, TIMER], ids=lambda u: u.name)
def test_the_deploy_tool_accepts_the_unit(unit):
    code, output = policy_says(unit, unit.name)
    assert code == 0, output


@bash
@pytest.mark.parametrize(
    ("unit", "old", "new"),
    [
        (SERVICE, "User=sudarshan", "User=root"),
        (SERVICE, "Group=sudarshan", "Group=root"),
        (SERVICE, "ExecStart=/", "ExecStart=+/"),
        (SERVICE, "NoNewPrivileges=yes", "NoNewPrivileges=no"),
        (SERVICE, "Nice=19", "Nice=-5"),
        (SERVICE, "UMask=0077", "UMask=0077\nAmbientCapabilities=CAP_DAC_READ_SEARCH"),
        (TIMER, "Unit=sudarshan-backup.service", "Unit=openalgo.service"),
    ],
)
def test_the_policy_check_is_real_for_this_unit(tmp_path, unit, old, new):
    bad = tmp_path / unit.name
    assert old in unit.read_text(), "the test must change something"
    bad.write_text(unit.read_text().replace(old, new))
    assert policy_says(bad, bad.name)[0] != 0


def test_the_names_are_ones_the_deploy_tool_takes():
    for unit in (SERVICE, TIMER):
        assert re.fullmatch(r"sudarshan-[a-z0-9-]+\.(service|timer)", unit.name)


def test_the_tool_restarts_every_service_it_installs_so_this_one_must_not_hold_it_up():
    """ring_deploy() runs ``systemctl restart`` on each service and waits for it to return: a
    oneshot unit would hold the tool for the whole of a backup, with the install lock held."""
    tool = DEPLOY_TOOL.read_text()
    assert 'systemctl restart "${n}"' in tool
    assert directives(SERVICE, "Type") == ["exec"]
    assert directives(SERVICE, "Restart") == []  # a batch job: a failure waits for its timer
    # Starting the unit again (the tool's restart after an install) is harmless: the program
    # decides for itself whether anything is to be done.
    assert directives(SERVICE, "ExecStart") == [
        "/opt/sudarshan/apps/openalgo/.venv/bin/python -m sudarshan.ops.backup"
    ]


# ---- the service ------------------------------------------------------------------------------


def test_service_shape():
    assert directives(SERVICE, "User") == ["sudarshan"]
    assert directives(SERVICE, "Group") == ["sudarshan"]
    assert directives(SERVICE, "Slice") == ["sudarshan.slice"]
    assert directives(SERVICE, "WorkingDirectory") == ["/opt/sudarshan/apps/openalgo"]
    # Optional: a missing file means the defaults.
    assert directives(SERVICE, "EnvironmentFile") == ["-/etc/sudarshan/backup.env"]
    # Started by the timer only; the deploy tool enables it, which needs no [Install] section.
    assert directives(SERVICE, "WantedBy") == []


def test_the_program_the_unit_starts_is_the_module_that_is_tested():
    assert (REPO / "sudarshan" / "ops" / "backup.py").is_file()
    assert (REPO / "sudarshan" / "ops" / "__init__.py").is_file()
    assert 'if __name__ == "__main__":' in (REPO / "sudarshan/ops/backup.py").read_text()


def test_it_writes_only_its_state_directory_and_may_read_what_it_backs_up():
    assert directives(SERVICE, "ProtectSystem") == ["strict"]
    assert directives(SERVICE, "ProtectHome") == ["yes"]
    assert directives(SERVICE, "StateDirectory") == [Path(backup.STATE_DIR).name]
    assert directives(SERVICE, "StateDirectoryMode") == ["0700"]
    assert directives(SERVICE, "ReadOnlyPaths") == ["-/etc/sudarshan"]
    # SQLite opens a database it only reads with the right to make its -shm file beside it, so
    # the folders it reads from are writable: OpenAlgo's db/ (the vault's folder is inside it)
    # and the ring's state folders in /var/lib. Nothing else.
    assert directives(SERVICE, "ReadWritePaths") == ["-/opt/sudarshan/apps/openalgo/db -/var/lib"]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("NoNewPrivileges", "yes"),
        ("PrivateTmp", "yes"),
        ("PrivateDevices", "yes"),
        ("ProtectKernelTunables", "yes"),
        ("ProtectKernelModules", "yes"),
        ("ProtectKernelLogs", "yes"),
        ("ProtectControlGroups", "yes"),
        ("ProtectClock", "yes"),
        ("ProtectProc", "invisible"),
        ("RestrictNamespaces", "yes"),
        ("RestrictSUIDSGID", "yes"),
        ("RestrictRealtime", "yes"),
        ("LockPersonality", "yes"),
        ("SystemCallFilter", "@system-service"),
        ("SystemCallErrorNumber", "EPERM"),
        ("SystemCallArchitectures", "native"),
        ("UMask", "0077"),  # every archive and every file it makes is the owner's alone
        ("LimitCORE", "0"),  # a crash must not write the databases to a core file
        # Only the offsite command needs the network, and only when the owner sets one.
        ("RestrictAddressFamilies", "AF_UNIX AF_INET AF_INET6"),
    ],
)
def test_service_hardening(key, value):
    assert directives(SERVICE, key) == [value]


def test_it_stays_behind_the_market_and_cannot_run_away():
    assert directives(SERVICE, "Nice") == ["19"]
    assert directives(SERVICE, "CPUWeight") == ["10"]
    assert directives(SERVICE, "IOWeight") == ["10"]
    assert directives(SERVICE, "MemoryMax") == ["512M"]
    (runtime,) = directives(SERVICE, "RuntimeMaxSec")
    settings = backup.Settings.from_env({})
    # The longest legitimate night: waiting out an install, the archive, then the upload.
    assert int(runtime) > settings.deploy_wait_s + settings.offsite_timeout_s + 1800
    (stop,) = directives(SERVICE, "TimeoutStopSec")
    assert 10 <= int(stop) <= 60  # SIGTERM is answered at once; a hung stop is not waited out


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="needs systemd-analyze")
def test_systemd_reads_both_units_without_complaint(tmp_path):
    for unit in (SERVICE, TIMER):
        shutil.copy(unit, tmp_path / unit.name)
    done = subprocess.run(
        ["systemd-analyze", "verify", str(tmp_path / SERVICE.name), str(tmp_path / TIMER.name)],
        capture_output=True,
        text=True,
    )
    # The server's own interpreter under /opt/sudarshan is absent on a CI runner; that says
    # nothing about the unit, so only that complaint is set aside.
    absent_on_this_machine = re.compile(
        r"Command /opt/sudarshan/\S+ is not executable: No such file or directory"
    )
    complaints = [
        line
        for line in (done.stdout + done.stderr).splitlines()
        if "sudarshan-backup" in line and not absent_on_this_machine.search(line)
    ]
    assert complaints == []


# ---- the timer --------------------------------------------------------------------------------


def calendar_times() -> list[str]:
    times = []
    for expression in directives(TIMER, "OnCalendar"):
        match = re.fullmatch(r"\*-\*-\* (\d\d:\d\d):00 Asia/Kolkata", expression)
        assert match, f"{expression!r} is not an every-day Indian-time wake-up"
        times.append(match[1])
    return times


def test_the_timer_wakes_the_job_only_after_the_market_hours_it_keeps_out_of():
    times = calendar_times()
    assert times == ["16:30", "22:30"]
    start, end = (minutes(t) for t in backup.BLOCKED)
    for at in times:
        assert not start <= minutes(at) < end, at
    # AccuracySec lets systemd delay a wake-up (never advance it) by up to that long.
    (accuracy,) = directives(TIMER, "AccuracySec")
    assert accuracy in ("1min", "1m", "60s")
    assert minutes(times[0]) - end >= 30  # and the first is half an hour clear of the close


def test_the_second_wake_up_is_a_second_chance_not_a_second_backup():
    """22:30 comes less than the program's minimum gap after 16:30: if the first made the
    night's archive, the second finds it fresh and does nothing."""
    first, second = (minutes(t) for t in calendar_times())
    assert (second - first) * 60 < backup.Settings.from_env({}).min_gap_s


def test_the_timer_starts_this_service_and_catches_up_a_night_the_server_was_down():
    assert directives(TIMER, "Unit") == [SERVICE.name]
    assert directives(TIMER, "Persistent") == ["true"]
    assert directives(TIMER, "WantedBy") == ["timers.target"]


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="needs systemd-analyze")
def test_systemd_reads_the_timer_calendar_as_every_day_at_those_hours_in_india():
    for expression in directives(TIMER, "OnCalendar"):
        done = subprocess.run(
            ["systemd-analyze", "calendar", "--iterations=8", expression],
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, done.stderr
        stamps = re.findall(
            r"(?:Next elapse|Iteration #\d+): (\w{3}) (\d{4}-\d\d-\d\d) (\d\d:\d\d)", done.stdout
        )
        assert len(stamps) == 8
        assert {hhmm for _, _, hhmm in stamps} == {expression.split()[1][:5]}
        assert len({day for _, day, _ in stamps}) == 8  # a different day each time: every day
        assert {weekday for weekday, _, _ in stamps} >= {"Sat", "Sun"}  # weekends too
