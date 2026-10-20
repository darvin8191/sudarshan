"""The systemd units: accepted by the deploy tool's own policy, and in step with the schedule."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sudarshan.autologin import launcher, worker
from sudarshan.autologin.schedule import SCHEDULE
from sudarshan.vault.store import VAULT_DIR, default_vault_path

REPO = Path(__file__).resolve().parents[3]
UNITS = REPO / "sudarshan" / "ops" / "systemd"
DEPLOY_TOOL = REPO / "sudarshan" / "ops" / "deploy" / "sudarshan-deploy"
SERVICE = UNITS / "sudarshan-autologin.service"
TIMER = UNITS / "sudarshan-autologin.timer"


def _directives(path: Path, key: str) -> list[str]:
    pattern = re.compile(rf"^\s*{key}\s*=\s*(.*?)\s*$")
    return [m.group(1) for line in path.read_text().splitlines() if (m := pattern.match(line))]


def _unit_ok_function() -> str:
    """The deploy tool's unit_ok(), cut out of the script exactly as it ships."""
    text = DEPLOY_TOOL.read_text()
    start = text.index("unit_ok() {")
    end = text.index("\n}\n", start) + 3
    return text[start:end]


def _policy_says(unit: Path, name: str) -> tuple[int, str]:
    script = 'log() { echo "$*"; }\n' + _unit_ok_function() + '\nunit_ok "$1" "$2"\n'
    done = subprocess.run(
        ["bash", "-c", script, "policy", str(unit), name], capture_output=True, text=True
    )
    return done.returncode, done.stdout + done.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize("unit", [SERVICE, TIMER], ids=lambda p: p.name)
def test_the_deploy_tool_accepts_the_units(unit):
    code, output = _policy_says(unit, unit.name)
    assert code == 0, output


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_the_policy_check_is_real(tmp_path):
    """A unit breaking the policy is refused by the same extracted function (not a vacuous pass)."""
    bad = tmp_path / "sudarshan-autologin.service"
    bad.write_text(SERVICE.read_text().replace("User=sudarshan", "User=root"))
    assert _policy_says(bad, bad.name)[0] != 0
    bad.write_text(SERVICE.read_text().replace("ExecStart=/", "ExecStart=+/"))
    assert _policy_says(bad, bad.name)[0] != 0


def test_service_shape():
    assert _directives(SERVICE, "Type") == ["oneshot"]
    assert _directives(SERVICE, "User") == ["sudarshan"]
    assert _directives(SERVICE, "Group") == ["sudarshan"]
    assert _directives(SERVICE, "NoNewPrivileges") == ["yes"]
    assert _directives(SERVICE, "WorkingDirectory") == ["/opt/sudarshan/apps/openalgo"]
    assert _directives(SERVICE, "ExecStart") == [
        "/opt/sudarshan/apps/openalgo/.venv/bin/python -m sudarshan.autologin"
    ]
    assert _directives(SERVICE, "SuccessExitStatus") == ["75"]
    assert "[Install]" not in SERVICE.read_text()  # only the timer starts it


def test_service_may_write_the_vault_and_nothing_else(monkeypatch):
    monkeypatch.delenv("SUDARSHAN_VAULT_DB", raising=False)  # the shipped default, not a test's
    assert _directives(SERVICE, "ProtectSystem") == ["strict"]
    assert _directives(SERVICE, "ProtectHome") == ["yes"]
    (writable,) = _directives(SERVICE, "ReadWritePaths")
    app_dir = _directives(SERVICE, "WorkingDirectory")[0]
    assert writable == f"-{app_dir}/db/{VAULT_DIR}"
    assert default_vault_path().parent.name == VAULT_DIR  # where the code keeps it


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("PrivateTmp", "yes"),
        ("PrivateDevices", "yes"),
        ("ProtectKernelTunables", "yes"),
        ("ProtectKernelModules", "yes"),
        ("ProtectKernelLogs", "yes"),
        ("ProtectControlGroups", "yes"),
        ("ProtectClock", "yes"),
        ("RestrictNamespaces", "yes"),
        ("RestrictAddressFamilies", "AF_UNIX AF_INET AF_INET6"),
        ("RestrictSUIDSGID", "yes"),
        ("LockPersonality", "yes"),
        ("SystemCallFilter", "@system-service"),
        ("SystemCallErrorNumber", "EPERM"),
        ("UMask", "0077"),
    ],
)
def test_service_hardening(key, value):
    assert _directives(SERVICE, key) == [value]


def test_service_waits_for_openalgo_but_never_starts_it():
    """After= orders a boot; Wants= would start OpenAlgo while a deploy has it stopped."""
    after = " ".join(_directives(SERVICE, "After")).split()
    assert "openalgo.service" in after
    for key in ("Wants", "Requires", "BindsTo", "Requisite", "PartOf"):
        assert "openalgo.service" not in " ".join(_directives(SERVICE, key))


def _seconds(value: str) -> float:
    match = re.fullmatch(r"(\d+)(s|min)?", value)
    assert match, value
    return float(match.group(1)) * (60 if match.group(2) == "min" else 1)


def test_time_limits_outlast_the_slowest_run():
    """Every limit is longer than the run it bounds, computed from the timeouts in code."""
    scheduled, test = worker.run_budget_s("scheduled"), worker.run_budget_s("test")
    (start,) = map(_seconds, _directives(SERVICE, "TimeoutStartSec"))
    (stop,) = map(_seconds, _directives(SERVICE, "TimeoutStopSec"))
    assert start >= scheduled + 30  # plus interpreter start-up and vault writes
    assert launcher.TEST_TIMEOUT_S >= test + 15
    lease = worker.LEASE_TTL.total_seconds()
    assert lease > start + stop and lease > launcher.TEST_TIMEOUT_S + launcher.KILL_GRACE_S
    # The page waits for longer than the server lets the child run.
    api = (REPO / "frontend" / "src" / "api" / "autologin.ts").read_text()
    (page_ms,) = re.findall(r"timeout: ([0-9_]+)", api)
    assert (
        int(page_ms.replace("_", "")) / 1000 > launcher.TEST_TIMEOUT_S + launcher.KILL_GRACE_S + 5
    )


def test_timer_matches_the_schedule_in_code():
    calendars = _directives(TIMER, "OnCalendar")
    # Every day: the worker skips closed days itself, and a weekend session can be an extra day.
    assert calendars == [f"*-*-* {t}:00 Asia/Kolkata" for t in SCHEDULE]
    assert _directives(TIMER, "Unit") == ["sudarshan-autologin.service"]
    assert _directives(TIMER, "Persistent") == ["true"]
    assert _directives(TIMER, "WantedBy") == ["timers.target"]


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="needs systemd-analyze")
def test_systemd_reads_the_units_without_complaint(tmp_path):
    for unit in (SERVICE, TIMER):
        shutil.copy(unit, tmp_path / unit.name)
    done = subprocess.run(
        ["systemd-analyze", "verify", str(tmp_path / SERVICE.name), str(tmp_path / TIMER.name)],
        capture_output=True,
        text=True,
    )
    complaints = [
        line for line in (done.stdout + done.stderr).splitlines() if "sudarshan-autologin" in line
    ]
    assert complaints == []
    for calendar in _directives(TIMER, "OnCalendar"):
        out = subprocess.run(
            ["systemd-analyze", "calendar", calendar], capture_output=True, text=True
        ).stdout
        assert "Next elapse:" in out and calendar.split()[-2] in out  # the time of day
