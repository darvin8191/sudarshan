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
        # Review H2: ceilings that hold on an idle box too, beside the service's own bounds.
        "CPUQuota": ["60%"],
        "TasksMax": ["64"],
        "LimitNOFILE": ["1024"],
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


# ---- v0.12.0 review L1: the owner's switch-on steps, written down in order ----------------------
README = REPO / "sudarshan" / "family" / "README.md"
ENV_LIST = REPO / "sudarshan" / "ops" / "deploy" / "family-env.conf"


def switch_on_section() -> str:
    text = README.read_text(encoding="utf-8")
    start = text.index("## Switching the family view on")
    return text[start : text.index("\n## ", start + 10)]


def test_l1_the_readme_lists_the_switch_on_steps_in_order_and_the_sentences_the_service_says():
    """Fails on the old README: it named the switch and its file and listed no step."""
    from sudarshan.family import privacy, words

    section = switch_on_section()
    flat = " ".join(section.split())
    order = ["The release is installed", "The one-time family set-up", "The nginx lines",
             "The family bot's token", "The switch.", "The Family page", "First-day checks"]  # fmt: skip
    at = [section.index(f"{n}. **{name}") for n, name in enumerate(order, 1)]
    assert at == sorted(at)
    assert f"{contract.SWITCH_ENV}=on" in section and "family-env.conf" in section
    assert f"{config.ETC_DIR}/family.env" in section and "/etc/sudarshan/alerts.env" in section
    assert words.BOOK_SHARE_REFUSED in flat and privacy.PNL_HIDDEN_NOTE in flat
    # a public repository: no address, no login, nobody's name
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", section) and "@" not in section
    assert ENV_LIST.read_text(encoding="utf-8").split("\n")[-2] == f"{contract.SWITCH_ENV}  off"


@bash
@pytest.mark.parametrize(
    ("family_env", "alerts_env"),
    [
        ("SUDARSHAN_FAMILY='off'\n", None),
        ("SUDARSHAN_FAMILY_LOG_LEVEL=INFO\n  SUDARSHAN_FAMILY = off\n", "SUDARSHAN_ALERTS=on\n"),
        ("SUDARSHAN_FAMILY=on\n", "SUDARSHAN_FAMILY=off\nSUDARSHAN_JOURNAL=off\n"),
    ],
)
def test_l1_the_switch_line_sets_the_switch_once_in_both_files_and_keeps_the_rest(
    tmp_path, family_env, alerts_env
):
    """The pasted line of step 5, run as it is in the README with the two paths moved into a
    folder of the test's and the root-only commands replaced by stand-ins that record their call."""
    line = next(row.strip() for row in switch_on_section().splitlines()
                if row.strip().startswith("sudo bash -c '"))  # fmt: skip
    fam, alerts, said = tmp_path / "family.env", tmp_path / "alerts.env", tmp_path / "said"
    fam.write_text(family_env)
    if alerts_env is not None:
        alerts.write_text(alerts_env)
    script = line.removeprefix("sudo ").replace(f"{config.ETC_DIR}/family.env", str(fam))
    script = script.replace("/etc/sudarshan/alerts.env", str(alerts))
    shims = tmp_path / "bin"
    shims.mkdir()
    for name in ("systemctl", "chown"):
        (shims / name).write_text(f'#!/bin/sh\necho "{name} $*" >> "{said}"\n')
        (shims / name).chmod(0o755)
    (shims / "install").write_text(f'#!/bin/sh\necho "install $*" >> "{said}"\n: > "{alerts}"\n')
    (shims / "install").chmod(0o755)
    import os

    env = {**os.environ, "PATH": f"{shims}:{os.environ['PATH']}"}
    done = subprocess.run(["bash", "-c", script[len("bash -c ") :].strip("'")], env=env,
                          capture_output=True, text=True)  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.strip() == "The family view is switched on."
    for path, before in ((fam, family_env), (alerts, alerts_env or "")):
        rows = path.read_text().splitlines()
        assert [r for r in rows if "SUDARSHAN_FAMILY" in r.split("=")[0].split("_LOG")[0]
                and r.replace(" ", "").startswith("SUDARSHAN_FAMILY=")] == ["SUDARSHAN_FAMILY=on"]  # fmt: skip
        kept = [
            r for r in before.splitlines() if not r.replace(" ", "").startswith("SUDARSHAN_FAMILY=")
        ]
        assert rows[:-1] == kept
        assert config.read_env_file(path)["SUDARSHAN_FAMILY"] == "on"
    calls = said.read_text().splitlines()
    assert calls[-1] == "systemctl restart sudarshan-family sudarshan-alerts"
    assert f"chown root:sudarshan-family {fam}" in calls
    assert (alerts_env is None) == any(
        c.startswith("install -m 640 -o root -g sudarshan") for c in calls
    )


@bash
def test_l1_the_switch_line_changes_nothing_before_the_set_up_has_made_the_file(tmp_path):
    line = next(row.strip() for row in switch_on_section().splitlines()
                if row.strip().startswith("sudo bash -c '"))  # fmt: skip
    script = line.removeprefix("sudo bash -c ").strip("'")
    script = script.replace(f"{config.ETC_DIR}/family.env", str(tmp_path / "family.env"))
    script = script.replace("/etc/sudarshan/alerts.env", str(tmp_path / "alerts.env"))
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert done.returncode == 1 and "Nothing was changed." in done.stdout
    assert list(tmp_path.iterdir()) == []
