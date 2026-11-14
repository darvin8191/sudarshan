"""M19.8: the restore drill's unit and timer, read the way the deploy tool and systemd read
them. The unit reads the backup's folder and writes only there, has no network, and can do
nothing to another service; the timer starts it on the first Sunday of the month."""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

from sudarshan.ops import backup, drill

REPO = Path(__file__).resolve().parents[3]
UNITS = REPO / "sudarshan" / "ops" / "systemd"
DEPLOY_TOOL = REPO / "sudarshan" / "ops" / "deploy" / "sudarshan-deploy"
SERVICE = UNITS / "sudarshan-drill.service"
TIMER = UNITS / "sudarshan-drill.timer"
RAW_README = (REPO / "sudarshan" / "README.md").read_text(encoding="utf-8")
README = " ".join(RAW_README.split())
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


def test_it_runs_as_the_backup_does_in_the_backups_folder():
    for key, value in {
        "Type": "exec",  # the deploy tool restarts it and must not wait for a whole drill
        "User": "sudarshan",
        "Slice": "sudarshan.slice",
        "Nice": "19",
        "CPUWeight": "10",
        "MemoryMax": "512M",
        "NoNewPrivileges": "yes",
        "ProtectSystem": "strict",
        "ProtectHome": "yes",
        "PrivateTmp": "yes",
        "StateDirectory": "sudarshan-backup",
        "StateDirectoryMode": "0700",
    }.items():
        assert directives(SERVICE, key) == [value], key
    backup_unit = UNITS / "sudarshan-backup.service"
    for key in ("StateDirectory", "MemoryMax", "MemoryHigh", "Nice", "CPUWeight", "IOWeight"):
        assert directives(SERVICE, key) == directives(backup_unit, key), key
    assert f"/var/lib/{directives(SERVICE, 'StateDirectory')[0]}" == backup.STATE_DIR
    assert directives(SERVICE, "ExecStart") == [
        "/opt/sudarshan/apps/openalgo/.venv/bin/python -m sudarshan.ops.drill"
    ]
    assert directives(SERVICE, "EnvironmentFile") == ["-/etc/sudarshan/backup.env"]
    assert directives(SERVICE, "ReadWritePaths") == [], "nothing but its state folder is writable"
    assert directives(SERVICE, "Restart") == [] and directives(SERVICE, "WantedBy") == []


def test_it_has_no_network_and_can_do_nothing_to_another_service():
    assert directives(SERVICE, "RestrictAddressFamilies") == ["AF_UNIX"]
    text = SERVICE.read_text()
    for word in ("systemctl", "ExecStartPost", "ExecStop", "OnFailure", "Wants=", "Requires=",
                 "BindsTo", "PartOf", "sudo"):  # fmt: skip
        assert word not in text, word
    names = set(re.findall(r"[a-z0-9-]+\.(?:service|timer|target)", text))
    assert names <= {"sudarshan-drill.timer"}, names
    source = (REPO / "sudarshan" / "ops" / "drill.py").read_text()
    for word in ("subprocess", "socket", "urllib.request", "http.client", "systemctl", "trinetra"):
        assert word not in source.lower(), f"drill.py names {word}"


def test_the_timer_is_the_first_sunday_of_the_month_at_eleven():
    assert directives(TIMER, "Unit") == ["sudarshan-drill.service"]
    assert directives(TIMER, "OnCalendar") == ["Sun *-*-01..07 11:00:00 Asia/Kolkata"]
    assert directives(TIMER, "WantedBy") == ["timers.target"]
    assert directives(TIMER, "Persistent") == ["true"]
    for year, month in ((2026, 11), (2026, 12), (2027, 2), (2027, 8)):
        sundays = [date(year, month, day) for day in range(1, 8)
                   if date(year, month, day).weekday() == 6]  # fmt: skip
        assert len(sundays) == 1 and (sundays[0] - timedelta(days=7)).month != month


def test_the_readme_says_what_the_drill_proves_and_how_to_run_it():
    assert "### The restore drill (M19.8)" in RAW_README
    for phrase in (
        "first Sunday of each month at 11:00",
        "sudo -u sudarshan .venv/bin/python -m sudarshan.ops.drill",
        "It restores nothing over live files",
        "it does not prove that a new server boots",
        "once a quarter",
        "over 100 days",
    ):
        assert phrase in README, phrase
    for item, words in drill.WORDS.items():
        assert words.endswith("."), item
