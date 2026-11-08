"""The screener's deploy half (OP1): its unit and the ring slice installed by the policy, its own
settings file made with the switch OFF and an owner's value kept, the /health check after its
start, its database in every backup and rollback, and --remove-ring as the uninstall path."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from test_deploy_calendar import Box, whole_tool
from test_deploy_notices import install_box
from test_deploy_ring_databases import make_db, rows_of, the_backup

REPO = Path(__file__).resolve().parents[3]
SYSTEMD = REPO / "sudarshan/ops/systemd"
SCREENER = "sudarshan-screener"
UNIT = f"{SCREENER}.service"
SLICE = "sudarshan.slice"
ENV_LIST = (REPO / "sudarshan/ops/deploy/screener-env.conf").read_bytes()

pytestmark = pytest.mark.skipif(os.name != "posix", reason="the deploy tool is bash")


def serve_units(box: Box, tag: str = "v1.0.1", *names: str) -> None:
    listing = [{"type": "file", "name": name} for name in names]
    api = f"https://api.github.com/repos/{box.OWNER_REPO}/contents/sudarshan/ops/systemd?ref={tag}"
    box.serve(api, json.dumps(listing).encode())
    raw = f"https://raw.githubusercontent.com/{box.OWNER_REPO}/{tag}/sudarshan/ops/systemd/"
    for name in names:
        box.serve(raw + name, (SYSTEMD / name).read_bytes())


def serve_env_list(box: Box, body: bytes = ENV_LIST, tag: str = "v1.0.1") -> None:
    url = f"https://raw.githubusercontent.com/{box.OWNER_REPO}/{tag}/sudarshan/ops/deploy/screener-env.conf"
    box.serve(url, body)


def serve_health(box: Box, body: dict | None) -> None:
    url = "http://127.0.0.1:5584/health"
    if body is None:
        box.serve(url, b"", code="000")
    else:
        box.serve(url, json.dumps(body).encode())


def systemctl_log(box: Box) -> list[str]:
    log = box.web / "systemctl"
    return log.read_text().splitlines() if log.exists() else []


def env_values(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.startswith("#"):
            out[key.strip()] = value.strip().strip("'")
    return out


# ---- the files the release ships ------------------------------------------------------------------
def test_the_slice_carries_resource_limits_only_and_every_ring_unit_runs_in_it():
    text = (SYSTEMD / SLICE).read_text()
    for line in text.splitlines():
        line = line.strip()
        assert not line or line.startswith(
            ("#", "[", "Description=", "CPUWeight=", "MemoryHigh=", "MemoryMax=", "TasksMax=")
        ), line
    for unit in SYSTEMD.glob("sudarshan-*.service"):
        if unit.name in ("sudarshan-autologin.service",):
            continue
        assert "Slice=sudarshan.slice" in unit.read_text(), unit.name
    keys = env_values(REPO / "sudarshan/ops/deploy/screener-env.conf")
    assert keys == {}, "the list is KEY VALUE lines, not KEY=VALUE"
    words = [
        line.split()
        for line in ENV_LIST.decode().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert words == [["SUDARSHAN_SCREENER", "off"], ["SUDARSHAN_SCREENER_HTTP_PORT", "5584"]]


# ---- an install ---------------------------------------------------------------------------------------
@whole_tool
def test_an_install_puts_in_the_unit_and_the_slice_makes_the_env_file_off_and_checks_health(
    tmp_path,
):
    box = install_box(tmp_path)
    serve_units(box, "v1.0.1", UNIT, SLICE)
    serve_env_list(box)
    serve_health(
        box, {"state": "running", "release": "v1.0.1", "screener_state": {"enabled": False}}
    )
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert (box.units / UNIT).read_bytes() == (SYSTEMD / UNIT).read_bytes()
    assert (box.units / SLICE).read_bytes() == (SYSTEMD / SLICE).read_bytes()
    assert (
        f"ring: installed {UNIT}\n" in done.stdout and f"ring: installed {SLICE}\n" in done.stdout
    )
    managed = (box.state / "managed_units").read_text().split()
    assert set(managed) == {UNIT, SLICE}
    calls = systemctl_log(box)
    assert f"restart {UNIT}" in calls and f"enable {UNIT}" in calls
    assert not any(SLICE in call and ("restart" in call or "enable" in call) for call in calls), (
        "a slice is not started"
    )
    env_file = box.etc / "screener.env"
    assert env_file.is_file() and stat.S_IMODE(env_file.stat().st_mode) == 0o640
    assert env_values(env_file) == {
        "SUDARSHAN_SCREENER": "off",
        "SUDARSHAN_SCREENER_HTTP_PORT": "5584",
    }
    assert f"ring: made {env_file}\n" in done.stdout
    assert f"ring: {UNIT} health: running (release v1.0.1)\n" in done.stdout
    assert "http://127.0.0.1:5584/health" in box.requests()
    assert "WARNING" not in done.stdout


@whole_tool
def test_an_owners_switch_stays_on_and_a_silent_service_is_a_warning_never_a_rollback(tmp_path):
    box = install_box(tmp_path)
    serve_units(box, "v1.0.1", UNIT)
    serve_env_list(box)
    serve_health(box, None)
    env_file = box.etc / "screener.env"
    env_file.write_text("# mine\nSUDARSHAN_SCREENER='on'\n")
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert env_values(env_file) == {
        "SUDARSHAN_SCREENER": "on",
        "SUDARSHAN_SCREENER_HTTP_PORT": "5584",
    }
    assert "settings: SUDARSHAN_SCREENER left alone: the owner chose its value" in done.stdout
    assert env_file.read_text().startswith("# mine\n")
    assert (
        f"WARNING: ring: {UNIT} did not answer on 127.0.0.1:5584/health within 30 s" in done.stdout
    )
    assert box.deployed() == "v1.0.1", "a ring service never rolls OpenAlgo back"
    assert "ring: made" not in done.stdout


@whole_tool
def test_a_release_without_a_settings_list_leaves_the_env_file_alone(tmp_path):
    box = install_box(tmp_path)
    serve_units(box, "v1.0.1", UNIT)
    serve_health(box, {"state": "running"})
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert not (box.etc / "screener.env").exists()


@whole_tool
def test_a_slice_with_anything_but_limits_is_refused(tmp_path):
    box = install_box(tmp_path)
    serve_units(box, "v1.0.1", UNIT)
    api = f"https://api.github.com/repos/{box.OWNER_REPO}/contents/sudarshan/ops/systemd?ref=v1.0.1"
    box.serve(
        api, json.dumps([{"type": "file", "name": UNIT}, {"type": "file", "name": SLICE}]).encode()
    )
    raw = f"https://raw.githubusercontent.com/{box.OWNER_REPO}/v1.0.1/sudarshan/ops/systemd/"
    box.serve(raw + SLICE, b"[Slice]\nCPUWeight=50\n[Service]\nExecStart=/bin/true\n")
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"ring: {SLICE} REFUSED by the unit policy" in done.stdout
    assert not (box.units / SLICE).exists() and (box.units / UNIT).exists()


# ---- the store in every backup and rollback -----------------------------------------------------------
@whole_tool
def test_the_screeners_store_is_in_the_backup_and_put_back_on_a_rollback(tmp_path):
    box = install_box(tmp_path)
    make_db(box.ringvar / SCREENER / "screener.db", [21, 22])
    (box.state / "managed_units").write_text(f"{UNIT}\n")
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    copy = the_backup(box) / "ring" / SCREENER / "screener.db"
    assert rows_of(copy) == [21, 22] and f"ring: backed up {SCREENER}/screener.db\n" in done.stdout
    assert stat.S_IMODE(copy.stat().st_mode) & 0o077 == 0

    # the next release's migrations fail: the install rolls back and the store goes back whole
    (tmp_path / "again").mkdir()
    box = install_box(tmp_path / "again")
    make_db(box.ringvar / SCREENER / "screener.db", [21, 22])
    (box.state / "managed_units").write_text(f"{UNIT}\n")
    box.migrations = "1"
    from test_deploy_ring_databases import migration_that

    migration_that(
        box, f'sqlite3 "{box.ringvar / SCREENER / "screener.db"}" "INSERT INTO t VALUES (999)"'
    )
    done = box.run()
    assert done.returncode != 0 and "rolling back" in done.stdout
    assert rows_of(box.ringvar / SCREENER / "screener.db") == [21, 22]
    assert f"ring: put back {SCREENER}/screener.db\n" in done.stdout


# ---- the uninstall path -------------------------------------------------------------------------------
@whole_tool
def test_remove_ring_stops_the_unit_copies_the_store_and_keeps_the_owners_settings(tmp_path):
    box = install_box(tmp_path)
    (box.units / UNIT).write_bytes((SYSTEMD / UNIT).read_bytes())
    (box.state / "managed_units").write_text(f"sudarshan-alerts.service\n{UNIT}\n")
    make_db(box.ringvar / SCREENER / "screener.db", [31, 32])
    env_file = box.etc / "screener.env"
    env_file.write_text("SUDARSHAN_SCREENER='on'\n")
    box.serve(f"http://127.0.0.1:5580/claims/{SCREENER}", b'{"dropped": 302}')
    done = box.run("--remove-ring", UNIT)
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"disable --now {UNIT}" in systemctl_log(box)
    assert not (box.units / UNIT).exists()
    assert (box.state / "managed_units").read_text().split() == ["sudarshan-alerts.service"]
    (bdir,) = box.backups.iterdir()
    assert bdir.name.endswith(f"-remove-{SCREENER}")
    assert rows_of(bdir / "ring" / SCREENER / "screener.db") == [31, 32]
    assert rows_of(box.ringvar / SCREENER / "screener.db") == [31, 32], "the state folder stays"
    assert env_file.read_text() == "SUDARSHAN_SCREENER='on'\n", "the owner's settings stay"
    assert f"ring: removed {UNIT}" in done.stdout
    claims = f"http://127.0.0.1:5580/claims/{SCREENER}"
    assert box.requests() == [claims], "no release is fetched for a removal; only the claims go"
    assert (box.web / "methods").read_text().splitlines() == [f"DELETE {claims}"]
    assert f"ring: the price feed no longer keeps {SCREENER}'s instruments" in done.stdout
    bad = box.run("--remove-ring", "openalgo.service")
    assert bad.returncode == 2
    worse = box.run("--remove-ring", "../etc/passwd")
    assert worse.returncode == 2
