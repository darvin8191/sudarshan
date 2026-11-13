"""F2: the unit sudarshan-family.service, read the way the deploy tool reads it (its own unit_ok, cut
out of the tool exactly as it ships; the tool itself is not changed) and the way systemd reads it."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sudarshan.family import config, contract, store

REPO = Path(__file__).resolve().parents[3]
UNIT = REPO / "sudarshan" / "ops" / "systemd" / "sudarshan-family.service"
SLICE = REPO / "sudarshan" / "ops" / "systemd" / "sudarshan.slice"
DEPLOY_TOOL = REPO / "sudarshan" / "ops" / "deploy" / "sudarshan-deploy"
bash = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def directives(text: str, key: str) -> list[str]:
    pattern = re.compile(rf"^\s*{key}\s*=\s*(.*?)\s*$")
    return [m.group(1) for line in text.splitlines() if (m := pattern.match(line))]


def unit_ok(unit: Path, name: str) -> tuple[int, str]:
    """The deploy tool's unit_ok(), cut out of the script as it ships, on ``unit``."""
    text = DEPLOY_TOOL.read_text()
    start = text.index("unit_ok() {")
    function = text[start : text.index("\n}\n", start) + 3]
    script = 'set -Eeuo pipefail\nlog() { echo "$*"; }\n' + function + '\nunit_ok "$1" "$2"\n'
    done = subprocess.run(
        ["bash", "-c", script, "policy", str(unit), name], capture_output=True, text=True
    )
    return done.returncode, done.stdout + done.stderr


@bash
def test_the_deploy_tool_accepts_the_unit():
    code, said = unit_ok(UNIT, UNIT.name)
    assert code == 0, said


@bash
@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("User=sudarshan-family", "User=sudarshan"),
        ("Group=sudarshan-family", "Group=sudarshan"),
        ("User=sudarshan-family", "User=root"),
        ("ConditionPathExists=/etc/sudarshan-family/provisioned\n", ""),
        ("NoNewPrivileges=yes", "NoNewPrivileges=no"),
        ("ExecStart=/", "ExecStart=+/"),
        ("UMask=0077", "UMask=0077\nSupplementaryGroups=sudarshan"),
    ],
)
def test_the_policy_check_is_real_for_this_unit(tmp_path, old, new):
    bad = tmp_path / UNIT.name
    assert old in UNIT.read_text()
    bad.write_text(UNIT.read_text().replace(old, new))
    assert unit_ok(bad, UNIT.name)[0] != 0


def test_the_unit_says_what_the_deploys_policy_and_the_design_ask():
    text = UNIT.read_text()
    exact = {
        "User": ["sudarshan-family"],
        "Group": ["sudarshan-family"],
        "NoNewPrivileges": ["yes"],
        "ConditionPathExists": ["/etc/sudarshan-family/provisioned"],
        "EnvironmentFile": ["-/etc/sudarshan-family/family.env"],
        "ReadOnlyPaths": ["-/etc/sudarshan-family"],
        "InaccessiblePaths": [
            "-/etc/sudarshan -/opt/sudarshan/apps/openalgo/db -/opt/sudarshan/apps/openalgo/.env"
        ],
        "ReadWritePaths": ["-/var/lib/sudarshan-family-export"],
        "StateDirectory": ["sudarshan-family"],
        "StateDirectoryMode": ["0700"],
        "UMask": ["0077"],
        "MemoryHigh": ["192M"],
        "MemoryMax": ["256M"],
        "CPUWeight": ["40"],
        "Slice": ["sudarshan.slice"],
        "Type": ["simple"],
        "Restart": ["on-failure"],
        "RestartPreventExitStatus": ["2"],
        "WantedBy": ["multi-user.target"],
        "ExecStart": ["/opt/sudarshan/apps/openalgo/.venv/bin/python -m sudarshan.family"],
        "ProtectSystem": ["strict"],
        "ProtectHome": ["yes"],
    }
    for key, values in exact.items():
        assert directives(text, key) == values, key
    assert "nft-sudarshan-family.service" in directives(text, "After")
    # Wants=, never Requires=: before the owner's set-up the firewall unit does not exist, and a
    # Requires= on it fails every start instead of letting ConditionPathExists skip it.
    assert "nft-sudarshan-family.service" in directives(text, "Wants")
    assert not directives(text, "Requires")
    assert not directives(text, "IPAddressDeny")  # Telegram and push are outbound on 443
    assert not directives(text, "SupplementaryGroups")


def test_the_units_paths_are_the_codes():
    text = UNIT.read_text()
    assert f"-{config.ETC_DIR}/family.env" in directives(text, "EnvironmentFile")
    assert f"-{config.EXPORT_DIR}" in directives(text, "ReadWritePaths")
    assert store.STATE_DIR.name in directives(text, "StateDirectory")
    tool = DEPLOY_TOOL.read_text()
    assert f"sudarshan-family.service) echo {contract.PORT}" in tool  # the tool asks /health here


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="needs systemd-analyze")
def test_systemd_reads_the_unit(tmp_path):
    """Set aside: the server's interpreter (absent here), and the firewall unit, which the owner's
    one-time set-up installs (the unit only wants it, so its absence never fails a start)."""
    for unit in (UNIT, SLICE):
        shutil.copy(unit, tmp_path / unit.name)
    done = subprocess.run(
        ["systemd-analyze", "verify", str(tmp_path / UNIT.name)], capture_output=True, text=True
    )
    set_aside = re.compile(
        r"Command /opt/sudarshan/\S+ is not executable: No such file or directory"
        r"|Unit nft-sudarshan-family\.service not found"
    )
    complaints = [
        line
        for line in (done.stdout + done.stderr).splitlines()
        if "sudarshan-family" in line and not set_aside.search(line)
    ]
    assert complaints == []
