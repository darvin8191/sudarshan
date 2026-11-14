"""M19.5: the watch's systemd unit and timer, read the way the deploy tool and systemd read them.

The deploy tool installs every ``sudarshan-*.service`` and ``.timer`` in ``ops/systemd`` after its
own unit policy has looked at the file. Beyond that: the unit is a oneshot that only reads and
writes its own state folder, behind everything else on the server, and the timer starts it and
nothing else, every two minutes.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sudarshan.ops import events
from sudarshan.ops.watch import checks, readers, run

REPO = Path(__file__).resolve().parents[3]
UNITS = REPO / "sudarshan" / "ops" / "systemd"
DEPLOY_TOOL = REPO / "sudarshan" / "ops" / "deploy" / "sudarshan-deploy"
SERVICE = UNITS / "sudarshan-watch.service"
TIMER = UNITS / "sudarshan-watch.timer"
README = " ".join((REPO / "sudarshan" / "README.md").read_text(encoding="utf-8").split())
bash = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def directives(path: Path, key: str) -> list[str]:
    pattern = re.compile(rf"^\s*{key}\s*=\s*(.*?)\s*$")
    return [m.group(1) for line in path.read_text().splitlines() if (m := pattern.match(line))]


@bash
@pytest.mark.parametrize("unit", [SERVICE, TIMER], ids=lambda u: u.name)
def test_the_deploy_tool_accepts_the_unit(unit):
    text = DEPLOY_TOOL.read_text()
    start = text.index("unit_ok() {")
    function = text[start : text.index("\n}\n", start) + 3]
    script = 'log() { echo "$*"; }\n' + function + '\nunit_ok "$1" "$2"\n'
    done = subprocess.run(
        ["bash", "-c", script, "policy", str(unit), unit.name], capture_output=True, text=True
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_it_is_a_oneshot_of_the_design_shape():
    """P8 7.1: User=sudarshan, the ring's slice, Nice=10, CPUWeight=10, MemoryMax=128M, 60 s."""
    for key, value in {
        "Type": "oneshot",
        "User": "sudarshan",
        "Slice": "sudarshan.slice",
        "Nice": "10",
        "CPUWeight": "10",
        "MemoryMax": "128M",
        "TimeoutStartSec": "60",
        "NoNewPrivileges": "yes",
        "ProtectSystem": "strict",
        "ProtectHome": "yes",
        "PrivateTmp": "yes",
    }.items():
        assert directives(SERVICE, key) == [value], key
    assert directives(SERVICE, "ExecStart") == [
        "/opt/sudarshan/apps/openalgo/.venv/bin/python -m sudarshan.ops.watch"
    ]
    assert directives(SERVICE, "Restart") == [], "a look that failed is simply the next look"
    assert directives(SERVICE, "WantedBy") == [], "started by its timer only"


def test_it_writes_only_its_own_state_folder_and_the_program_agrees():
    assert directives(SERVICE, "StateDirectory") == ["sudarshan-watch"]
    assert directives(SERVICE, "StateDirectoryMode") == ["0700"]
    assert f"/var/lib/{directives(SERVICE, 'StateDirectory')[0]}" == run.STATE_DIR
    assert events.WATCH_SPOOL.parent == Path(run.STATE_DIR)
    assert directives(SERVICE, "ReadWritePaths") == [], "nothing else is writable"
    assert directives(SERVICE, "WorkingDirectory") == [run.APP_DIR]
    assert directives(SERVICE, "EnvironmentFile") == ["-/etc/sudarshan/watch.env"]
    assert directives(SERVICE, "ReadOnlyPaths") == ["-/etc/sudarshan"]


def test_the_unit_can_do_nothing_to_another_service():
    text = SERVICE.read_text()
    for word in ("systemctl", "ExecStartPost", "ExecStop", "OnFailure", "Wants=", "Requires=",
                 "BindsTo", "PartOf", "sudo"):  # fmt: skip
        assert word not in text, word
    names = set(re.findall(r"[a-z0-9-]+\.(?:service|timer|target)", text))
    assert names <= {"sudarshan-watch.timer", "network-online.target"}, names


def test_the_timer_starts_the_watch_every_two_minutes_and_nothing_else():
    assert directives(TIMER, "Unit") == ["sudarshan-watch.service"]
    assert directives(TIMER, "OnCalendar") == ["*:0/2:30"]
    assert checks.RUN_EVERY_MIN == 2, "the checks count minutes in runs of this timer"
    assert directives(TIMER, "WantedBy") == ["timers.target"]
    assert directives(TIMER, "Persistent") == []


def test_every_service_the_watch_asks_has_a_unit_it_can_see_enabled():
    for name in checks.SERVICES:
        unit = UNITS / f"sudarshan-{name}.service"
        assert unit.is_file(), name
        assert directives(unit, "WantedBy") == [readers.WANTS_DIR.name.removesuffix(".wants")]


def test_the_readme_says_what_the_watch_does_and_how_to_look():
    assert "## Watching the server (M19.5)" in (REPO / "sudarshan" / "README.md").read_text()
    for phrase in (
        "every 2 minutes",
        "sudo -u sudarshan .venv/bin/python -m sudarshan.ops.watch --check",
        "/var/lib/sudarshan-watch/report.json",
        "It never restarts",
        f"under {checks.DISK_WARN_GB:.0f} GB",
        f"under {checks.DISK_FAIL_GB:.0f} GB",
        f"under {checks.CERT_FAIL_HOURS:.0f} hours",
    ):
        assert phrase in README, phrase
