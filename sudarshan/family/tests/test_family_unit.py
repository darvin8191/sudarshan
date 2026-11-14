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


def switch_line(tmp_path: Path, value: str = "on") -> tuple[str, dict[str, str], Path, Path, Path]:
    """The pasted line of step 5, as it is in the README, with the two paths moved into a folder of
    the test's and the root-only commands replaced by stand-ins that record their call."""
    import os

    line = next(row.strip() for row in switch_on_section().splitlines()
                if row.strip().startswith("sudo bash -c '"))  # fmt: skip
    fam, alerts, said = tmp_path / "family.env", tmp_path / "alerts.env", tmp_path / "said"
    script = line.removeprefix("sudo bash -c ").strip("'")
    assert script.count(f"{config.ETC_DIR}/family.env") == 1 and script.count("v=on;") == 1
    script = script.replace(f"{config.ETC_DIR}/family.env", str(fam))
    script = script.replace("/etc/sudarshan/alerts.env", str(alerts)).replace(
        "v=on;", f"v={value};"
    )
    shims = tmp_path / "bin"
    shims.mkdir()
    for name in ("systemctl", "chown"):
        (shims / name).write_text(f'#!/bin/sh\necho "{name} $*" >> "{said}"\n')
        (shims / name).chmod(0o755)
    (shims / "install").write_text(f'#!/bin/sh\necho "install $*" >> "{said}"\n: > "{alerts}"\n')
    (shims / "install").chmod(0o755)
    return script, {**os.environ, "PATH": f"{shims}:{os.environ['PATH']}"}, fam, alerts, said


def is_switch(row: str) -> bool:
    return row.replace(" ", "").startswith("SUDARSHAN_FAMILY=")


@bash
@pytest.mark.parametrize("value", ["on", "off"])
@pytest.mark.parametrize(
    ("family_env", "alerts_env"),
    [
        ("SUDARSHAN_FAMILY='off'\n", None),
        ("SUDARSHAN_FAMILY_LOG_LEVEL=INFO\n  SUDARSHAN_FAMILY = off\n", "SUDARSHAN_ALERTS=on\n"),
        ("SUDARSHAN_FAMILY=on\n", "SUDARSHAN_FAMILY=off\nSUDARSHAN_JOURNAL=off\n"),
        # verification N2: a last line with no line end after it. The old line glued the switch
        # onto it (SUDARSHAN_ALERTS_TOKEN=abcSUDARSHAN_FAMILY=on): a wrong value for the owner's
        # own alerts setting, and no switch.
        ("SUDARSHAN_FAMILY_LOG_LEVEL=INFO", "SUDARSHAN_ALERTS=on\nSUDARSHAN_ALERTS_TOKEN=abc"),
        ("SUDARSHAN_FAMILY=off", "SUDARSHAN_FAMILY=off\nSUDARSHAN_ALERTS_TOKEN=abc"),
        ("", ""),
    ],
)
def test_l1_the_switch_line_sets_the_switch_once_in_both_files_and_keeps_the_rest(
    tmp_path, family_env, alerts_env, value
):
    """Step 5's line, for on and for off (verification N3: switching off said "switched on"), on
    files that end with a line end and files that do not (N2). Every other setting keeps its own
    line and its value, the switch stands once, and the line says the value it wrote. Fails on the
    old line: for off (it printed "on" whatever was written, and had no ``v``), and for both
    files without a final line end."""
    script, env, fam, alerts, said = switch_line(tmp_path, value)
    fam.write_text(family_env)
    if alerts_env is not None:
        alerts.write_text(alerts_env)
    done = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.strip() == f"The family view is switched {value}."
    for path, before in ((fam, family_env), (alerts, alerts_env or "")):
        text = path.read_text()
        rows = text.splitlines()
        assert text.endswith("\n") and "" not in rows  # no blank line is left behind either
        assert [r for r in rows if is_switch(r)] == [f"SUDARSHAN_FAMILY={value}"]
        kept = [r for r in before.splitlines() if not is_switch(r)]
        assert rows[:-1] == kept
        read = config.read_env_file(path)
        assert read["SUDARSHAN_FAMILY"] == value
        for row in kept:  # each of the owner's settings reads as it did
            key, _, was = row.partition("=")
            assert read[key.strip()] == was
    calls = said.read_text().splitlines()
    assert calls[-1] == "systemctl restart sudarshan-family sudarshan-alerts"
    assert f"chown root:sudarshan-family {fam}" in calls
    assert (alerts_env is None) == any(
        c.startswith("install -m 640 -o root -g sudarshan") for c in calls
    )


@bash
def test_n2_n3_switching_on_and_off_again_and_again_leaves_one_line(tmp_path):
    script, env, fam, alerts, _ = switch_line(tmp_path)
    fam.write_text("")
    alerts.write_text("SUDARSHAN_ALERTS_TOKEN=abc")
    for value in ("on", "off", "off", "on"):
        done = subprocess.run(["bash", "-c", script.replace("v=on;", f"v={value};")], env=env,
                              capture_output=True, text=True)  # fmt: skip
        assert done.stdout.strip() == f"The family view is switched {value}.", done.stderr
    assert fam.read_text() == "SUDARSHAN_FAMILY=on\n"
    assert alerts.read_text() == "SUDARSHAN_ALERTS_TOKEN=abc\nSUDARSHAN_FAMILY=on\n"


@bash
def test_n3_a_value_that_is_neither_on_nor_off_changes_nothing(tmp_path):
    script, env, fam, alerts, said = switch_line(tmp_path, "yes")
    fam.write_text("SUDARSHAN_FAMILY=off\n")
    done = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    assert done.returncode == 1 and "Nothing was changed." in done.stdout
    assert fam.read_text() == "SUDARSHAN_FAMILY=off\n" and not alerts.exists() and not said.exists()


def test_n1_n4_the_steps_say_where_the_file_comes_from_and_where_the_share_is():
    """Verification N1: step 2 promised the file "within ten minutes" from a part of the deploy
    that ran only at an install. N4: step 6 and the table sent the owner to the Portfolio page,
    which has no share control. Fails on the old README on each line below."""
    from sudarshan.family import words

    flat = " ".join(switch_on_section().split())
    assert "on every run, every ten minutes, also when there is nothing to install" in flat
    assert "fam_env_run || true" in DEPLOY_TOOL.read_text()  # the every-run step the sentence names
    assert "wait ten minutes" not in flat
    assert 'on the Family page\'s Shares tab: under "Portfolio tabs" share the Upstox tab' in flat
    assert "in Portfolio" not in flat and "in Portfolio" not in words.BOOK_SHARE_REFUSED
    assert "under Portfolio tabs on the Family page" in words.BOOK_SHARE_REFUSED
    assert "`v=off` in place of `v=on`" in flat


def test_l7_no_sentence_calls_the_feed_live():
    """The words rule: "LIVE" is the trading-mode chip only. Fails on the old sentences: both
    said "Live P&L"."""
    from sudarshan.family import words
    from sudarshan.portfolio import privacy as portfolio_privacy

    refused = portfolio_privacy.valid_shown({"pnl": True, "qty": False})
    assert refused == words.WORDS["privacy_rule"]
    for sentence in [*words.WORDS.values(), refused]:
        assert not re.search(r"\blive\b", sentence, re.IGNORECASE), sentence


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
