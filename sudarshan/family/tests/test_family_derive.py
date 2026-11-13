"""O2' (cross-design Y2.1): ``python -m sudarshan.family.derive`` prints the family's seal keys.

The deploy tool runs it as ``sudarshan`` and takes its stdout only when it is five lines, each a
known name and 44 characters of urlsafe base64, and nothing else (``sudarshan-deploy`` fam_provision;
``sudarshan/ops/tests/test_deploy_family.py`` holds the tool to that). These hold the module to its
side: the names, the keys, the order, an empty stdout whenever it fails, and no secret on stderr.
"""

from __future__ import annotations

import base64
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sudarshan.family import derive
from sudarshan.vault.keys import (
    FAMILY_ADMIN_INFO,
    FAMILY_INTERNAL_INFO,
    FAMILY_NUDGE_INFO,
    JOURNAL_FAMILY_INFO,
    PORTFOLIO_FAMILY_API_INFO,
    PURPOSES,
    derive_key,
)

REPO = Path(__file__).resolve().parents[3]
PEPPER = "p" * 31 + "-a-test-pepper-not-a-real-one"
NAMES = ["admin", "nudge", "internal", "portfolio-family", "journal-family"]


@pytest.fixture
def app(tmp_path: Path) -> Path:
    """An app folder whose .env holds a pepper (and other settings around it)."""
    (tmp_path / ".env").write_text(f"APP_KEY='x'\nAPI_KEY_PEPPER='{PEPPER}'\nOTHER=1\n")
    return tmp_path


def run(env: dict[str, str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = derive.main([], env=env, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_five_lines_one_per_family_purpose_in_order(app):
    code, out, err = run({"SUDARSHAN_OPENALGO_DIR": str(app)})
    assert (code, err) == (0, "")
    assert out.endswith("\n") and out.count("\n") == 5
    rows = [line.split(" ") for line in out.splitlines()]
    assert [name for name, _key in rows] == NAMES
    infos = [
        FAMILY_ADMIN_INFO,
        FAMILY_NUDGE_INFO,
        FAMILY_INTERNAL_INFO,
        PORTFOLIO_FAMILY_API_INFO,
        JOURNAL_FAMILY_INFO,
    ]
    for (name, key), info in zip(rows, infos, strict=True):
        assert (
            len(key) == 44
            and key.endswith("=")
            and set(key) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=")
        ), name
        assert base64.urlsafe_b64decode(key) == derive_key(PEPPER, info), name


def test_the_purposes_are_the_vaults_and_each_key_is_its_own(app):
    assert {info for _name, info in derive.PURPOSES} <= set(PURPOSES)
    _code, out, _err = run({"SUDARSHAN_OPENALGO_DIR": str(app)})
    keys = [line.split(" ")[1] for line in out.splitlines()]
    assert len(set(keys)) == 5
    assert PEPPER not in out


def test_the_process_environment_wins_over_the_file_as_for_the_other_ring_services(app):
    other = "q" * 40
    _code, out, _err = run({"SUDARSHAN_OPENALGO_DIR": str(app), "API_KEY_PEPPER": other})
    first = out.splitlines()[0].split(" ")[1]
    assert base64.urlsafe_b64decode(first) == derive_key(other, FAMILY_ADMIN_INFO)


def test_the_env_file_can_be_named_on_its_own(tmp_path, app):
    moved = tmp_path / "elsewhere.env"
    moved.write_text((app / ".env").read_text())
    (app / ".env").unlink()
    code, out, _err = run(
        {
            "SUDARSHAN_OPENALGO_DIR": str(tmp_path / "nowhere"),
            "SUDARSHAN_OPENALGO_ENV_FILE": str(moved),
        }
    )
    assert code == 0 and out.count("\n") == 5


@pytest.mark.parametrize(
    ("env_text", "says"),
    [
        (None, "cannot read"),
        ("APP_KEY='x'\n", "API_KEY_PEPPER is missing or too short"),
        ("API_KEY_PEPPER='short'\n", "API_KEY_PEPPER is missing or too short"),
    ],
)
def test_without_a_pepper_nothing_is_printed_and_the_reason_is_said(tmp_path, env_text, says):
    if env_text is not None:
        (tmp_path / ".env").write_text(env_text)
    code, out, err = run({"SUDARSHAN_OPENALGO_DIR": str(tmp_path)})
    assert (code, out) == (2, "")
    assert err.startswith("The family keys were not made: ") and says in err
    assert err.count("\n") == 1 and "short'" not in err  # the value itself is never repeated


def test_as_the_deploy_tool_runs_it_stdout_holds_the_five_lines_and_nothing_else(app):
    """The real command, from the app folder (the tool's working directory), as a new process."""
    env = {"PATH": os.environ.get("PATH", ""), "SUDARSHAN_OPENALGO_DIR": str(app)}
    done = subprocess.run(
        [sys.executable, "-m", "sudarshan.family.derive"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert (done.returncode, done.stderr) == (0, "")
    assert [line.split(" ")[0] for line in done.stdout.splitlines()] == NAMES
    assert all(len(line.split(" ")) == 2 for line in done.stdout.splitlines())


def test_it_takes_no_arguments():
    with pytest.raises(SystemExit) as stop:
        derive.main(["--print-pepper"], env={}, out=io.StringIO(), err=io.StringIO())
    assert stop.value.code == 2
