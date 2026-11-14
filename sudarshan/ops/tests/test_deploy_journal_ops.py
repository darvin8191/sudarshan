"""The journal's deploy half (JE2, M18.11): its unit taken by the deploy tool's own policy,
installed, enabled and restarted from the release with the other ring units, its migration status
logged before it starts, its /health on 127.0.0.1:5586 asked after the start (a silent journal is
a warning, never a rollback), its store in every backup and rollback, and one switch only, the
line in openalgo-env.conf. And what sudarshan/README.md says about it is what the code does."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from test_deploy_calendar import Box, _function, _shim, whole_tool
from test_deploy_notices import install_box
from test_deploy_ring_databases import make_db, migration_that, rows_of, the_backup

from sudarshan.feed.rest import CEILINGS
from sudarshan.feed.subscriptions import QUOTAS

REPO = Path(__file__).resolve().parents[3]
SYSTEMD = REPO / "sudarshan/ops/systemd"
DEPLOY = REPO / "sudarshan/ops/deploy"
README = (REPO / "sudarshan/README.md").read_text()
JOURNAL = "sudarshan-journal"
UNIT = f"{JOURNAL}.service"
SLICE = "sudarshan.slice"
HEALTH = "http://127.0.0.1:5586/health"

pytestmark = pytest.mark.skipif(os.name != "posix", reason="the deploy tool is bash")
bash = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def serve_units(box: Box, tag: str = "v1.0.1", *names: str) -> None:
    """The release's ops/systemd folder as GitHub lists it, each unit the repository's own file."""
    listing = [{"type": "file", "name": name} for name in names]
    api = f"https://api.github.com/repos/{box.OWNER_REPO}/contents/sudarshan/ops/systemd?ref={tag}"
    box.serve(api, json.dumps(listing).encode())
    raw = f"https://raw.githubusercontent.com/{box.OWNER_REPO}/{tag}/sudarshan/ops/systemd/"
    for name in names:
        box.serve(raw + name, (SYSTEMD / name).read_bytes())


def serve_health(box: Box, body: dict | None) -> None:
    if body is None:
        box.serve(HEALTH, b"", code="000")
    else:
        box.serve(HEALTH, json.dumps(body).encode())


def systemctl_log(box: Box) -> list[str]:
    log = box.web / "systemctl"
    return log.read_text().splitlines() if log.exists() else []


def policy(unit: Path, name: str) -> subprocess.CompletedProcess:
    """unit_ok, cut out of the tool as it ships and run alone, as the units' own tests run it."""
    script = (
        'set -Eeuo pipefail\nlog() { echo "$*"; }\n'
        + _function("unit_ok")
        + 'if unit_ok "$1" "$2"; then echo ACCEPTED; else echo REFUSED; fi\n'
    )
    return subprocess.run(
        ["bash", "-c", script, "policy", str(unit), name], capture_output=True, text=True
    )


# ---- the unit and the switch the release ships ----------------------------------------------------
@bash
def test_the_journal_unit_passes_the_deploy_tools_own_policy(tmp_path):
    done = policy(SYSTEMD / UNIT, UNIT)
    assert done.stdout.strip() == "ACCEPTED", done.stdout + done.stderr
    worse = tmp_path / UNIT
    worse.write_text((SYSTEMD / UNIT).read_text().replace("User=sudarshan", "User=root"))
    assert policy(worse, UNIT).stdout.splitlines()[-1] == "REFUSED"


def test_the_journal_is_a_ring_unit_on_its_own_port_and_its_feed_client():
    text = (DEPLOY / "sudarshan-deploy").read_text()
    assert re.search(r"\nring_port\(\) \{[^}]*sudarshan-journal\.service\) echo 5586 ;;", text)
    assert re.search(r"FEED_CLIENTS=\([^)]*\bsudarshan-journal\b", text)  # its feed token
    assert "Slice=sudarshan.slice" in (SYSTEMD / UNIT).read_text()


def test_one_switch_in_openalgos_env_and_no_settings_list_of_its_own():
    """Y37: the journal's switch is the release's line in openalgo-env.conf, read by the service
    and, through its /health, by the alerts' report desk; a journal-env.conf would be a second."""
    assert not (DEPLOY / "journal-env.conf").exists()
    lines = [
        line.split("#", 1)[0].split()
        for line in (DEPLOY / "openalgo-env.conf").read_text().splitlines()
        if line.split("#", 1)[0].strip()
    ]
    assert ["SUDARSHAN_JOURNAL", "on"] in lines
    for conf in DEPLOY.glob("*-env.conf"):
        if conf.name != "openalgo-env.conf":
            assert "SUDARSHAN_JOURNAL" not in conf.read_text(), conf.name


# ---- an install ---------------------------------------------------------------------------------------
@whole_tool
def test_an_install_puts_in_enables_and_restarts_the_journal_and_asks_its_health(tmp_path):
    box = install_box(tmp_path)
    serve_units(box, "v1.0.1", UNIT, SLICE)
    serve_health(box, {"state": "running", "release": "v1.0.1", "journal_state": {"enabled": True}})
    done = box.run()
    out = done.stdout
    assert done.returncode == 0 and box.deployed() == "v1.0.1", out + done.stderr
    assert (box.units / UNIT).read_bytes() == (SYSTEMD / UNIT).read_bytes()
    assert f"ring: installed {UNIT}\n" in out
    assert set((box.state / "managed_units").read_text().split()) == {UNIT, SLICE}
    calls = systemctl_log(box)
    assert f"enable {UNIT}" in calls and f"restart {UNIT}" in calls
    assert calls.index(f"enable {UNIT}") < calls.index(f"restart {UNIT}")
    assert f"ring: {UNIT} active\n" in out
    assert f"ring: {UNIT} health: running (release v1.0.1)\n" in out
    assert HEALTH in box.requests()
    assert not (box.etc / "journal.env").exists(), "the release ships no list for it"
    assert "WARNING" not in out


@whole_tool
def test_a_silent_journal_is_a_warning_and_never_a_rollback(tmp_path):
    box = install_box(tmp_path)
    serve_units(box, "v1.0.1", UNIT)
    serve_health(box, None)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert box.deployed() == "v1.0.1", "a ring service never rolls OpenAlgo back"
    assert (
        f"WARNING: ring: {UNIT} did not answer on 127.0.0.1:5586/health within 30 s; "
        f"see journalctl -u {UNIT}" in done.stdout
    )
    assert "rolling back" not in done.stdout


@whole_tool
def test_the_journals_migration_status_is_logged_as_its_account_before_it_starts(tmp_path):
    box = install_box(tmp_path)
    make_db(box.ringvar / JOURNAL / "journal.db", [41])
    (box.state / "managed_units").write_text(f"{UNIT}\n")
    module = box.app / "sudarshan" / "journal" / "migrate.py"
    module.parent.mkdir(parents=True)
    module.write_text("")
    _shim(
        box.app / ".venv" / "bin",
        "python",
        'if [[ "$1" == -m ]]; then\n'
        '    cp "${FAKE_WEB}/systemctl" "${FAKE_WEB}/systemctl_at_status" 2>/dev/null || true\n'
        '    echo "journal.db: version 3, code 3, nothing pending"; exit 0\n'
        'fi\nexit "${FAKE_MIGRATIONS}"',
    )
    serve_units(box, "v1.0.1", UNIT)
    serve_health(box, {"state": "running", "release": "v1.0.1"})
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    line = (
        "ring: sudarshan.journal.migrate --status: journal.db: version 3, code 3, nothing pending"
    )
    assert line + "\n" in done.stdout
    asked = (box.web / "sudo").read_text()
    assert f"-u sudarshan -H -- {box.app}/.venv/bin/python -m sudarshan.journal.migrate" in asked
    seen = box.web / "systemctl_at_status"
    assert f"restart {UNIT}" not in (seen.read_text() if seen.exists() else "")
    assert f"restart {UNIT}" in systemctl_log(box)


# ---- the store in every backup and rollback -----------------------------------------------------------
@whole_tool
def test_the_journals_store_is_in_the_backup_and_put_back_on_a_rollback(tmp_path):
    box = install_box(tmp_path)
    make_db(box.ringvar / JOURNAL / "journal.db", [51, 52])
    (box.state / "managed_units").write_text(f"{UNIT}\n")
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    copy = the_backup(box) / "ring" / JOURNAL / "journal.db"
    assert rows_of(copy) == [51, 52] and f"ring: backed up {JOURNAL}/journal.db\n" in done.stdout
    assert stat.S_IMODE(copy.stat().st_mode) & 0o077 == 0

    # the next release's migrations fail: the install rolls back and the store goes back whole
    (tmp_path / "again").mkdir()
    box = install_box(tmp_path / "again")
    store = make_db(box.ringvar / JOURNAL / "journal.db", [51, 52])
    (box.state / "managed_units").write_text(f"{UNIT}\n")
    box.migrations = "1"
    migration_that(box, f'sqlite3 "{store}" "INSERT INTO t VALUES (999)"')
    done = box.run()
    assert done.returncode != 0 and "rolling back" in done.stdout
    assert rows_of(store) == [51, 52]
    assert f"ring: put back {JOURNAL}/journal.db\n" in done.stdout


# ---- what sudarshan/README.md says --------------------------------------------------------------------
def test_the_readme_gives_5586_to_the_journal_once_with_its_health():
    rows = [line for line in README.splitlines() if line.startswith("| 5586 |")]
    assert len(rows) == 1 and JOURNAL in rows[0] and "GET /health" in rows[0], rows
    assert "not built" not in rows[0]


def test_the_readme_lists_the_journal_as_a_ring_service_and_not_the_reports_as_ops_timers():
    services = [line for line in README.splitlines() if line.startswith("| `journal/` |")]
    assert len(services) == 1 and f"`{JOURNAL}`" in services[0]
    assert "SUDARSHAN_JOURNAL" in services[0]
    (ops,) = [line for line in README.splitlines() if line.startswith("| `ops/` |")]
    assert "15:45 EOD" not in ops and f"`{JOURNAL}` builds them" in ops


def test_the_readme_tables_hold_the_journals_figures_from_the_code():
    (keys,) = [line for line in README.splitlines() if line.startswith(f"| `{JOURNAL}` | 50 (")]
    assert keys.rstrip().endswith(f"| {QUOTAS[JOURNAL]} |")  # the feed's quota for it
    per_second, per_minute, per_half_hour = CEILINGS[JOURNAL]
    (share,) = [
        line for line in README.splitlines()
        if line.startswith(f"| `{JOURNAL}` |") and "%" in line
    ]  # fmt: skip
    assert share.rstrip().endswith(f"| {per_second} | {per_minute} | {per_half_hour} |")
    for purpose in ("sudarshan/journal/api/v1", "sudarshan/journal/delivery/v1",
                    "sudarshan/journal/nudge/v1", "sudarshan/portfolio/api/journal/v1"):  # fmt: skip
        assert f"`{purpose}`" in README, purpose
