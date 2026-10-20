"""The systemd unit: accepted by the deploy tool's own policy, sandboxed, enabled like the feed."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sudarshan.alerts import config, outbox, service

REPO = Path(__file__).resolve().parents[3]
UNITS = REPO / "sudarshan" / "ops" / "systemd"
DEPLOY_TOOL = REPO / "sudarshan" / "ops" / "deploy" / "sudarshan-deploy"
SERVICE = UNITS / "sudarshan-alerts.service"
FEED = UNITS / "sudarshan-feed.service"


def _directives(path: Path, key: str) -> list[str]:
    pattern = re.compile(rf"^\s*{key}\s*=\s*(.*?)\s*$")
    return [m.group(1) for line in path.read_text().splitlines() if (m := pattern.match(line))]


def _policy_says(unit: Path, name: str) -> tuple[int, str]:
    """The deploy tool's unit_ok(), cut out of the script exactly as it ships, on ``unit``."""
    text = DEPLOY_TOOL.read_text()
    start = text.index("unit_ok() {")
    function = text[start : text.index("\n}\n", start) + 3]
    script = 'log() { echo "$*"; }\n' + function + '\nunit_ok "$1" "$2"\n'
    done = subprocess.run(
        ["bash", "-c", script, "policy", str(unit), name], capture_output=True, text=True
    )
    return done.returncode, done.stdout + done.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_the_deploy_tool_accepts_the_unit():
    code, output = _policy_says(SERVICE, SERVICE.name)
    assert code == 0, output


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("User=sudarshan", "User=root"),
        ("ExecStart=/", "ExecStart=+/"),
        ("NoNewPrivileges=yes", "NoNewPrivileges=no"),
        ("UMask=0077", "UMask=0077\nAmbientCapabilities=CAP_NET_ADMIN"),  # pragma: allowlist secret
    ],
)
def test_the_policy_check_is_real(tmp_path, old, new):
    bad = tmp_path / SERVICE.name
    bad.write_text(SERVICE.read_text().replace(old, new))
    assert _policy_says(bad, bad.name)[0] != 0


def test_the_deploy_tool_installs_and_enables_it_like_the_feed():
    """ring_deploy() takes every sudarshan-*.service in ops/systemd, then enable + restart:
    enabling works only with an [Install] section, which the feed has too."""
    text = DEPLOY_TOOL.read_text()
    assert re.fullmatch(r"sudarshan-[a-z0-9-]+\.(service|timer)", SERVICE.name)
    assert 'systemctl enable "${n}"' in text and 'systemctl restart "${n}"' in text
    assert (
        _directives(SERVICE, "WantedBy") == _directives(FEED, "WantedBy") == ["multi-user.target"]
    )


def test_service_shape():
    assert _directives(SERVICE, "Type") == ["simple"]
    assert _directives(SERVICE, "User") == ["sudarshan"]
    assert _directives(SERVICE, "Group") == ["sudarshan"]
    assert _directives(SERVICE, "Slice") == ["sudarshan.slice"]
    assert _directives(SERVICE, "WorkingDirectory") == ["/opt/sudarshan/apps/openalgo"]
    assert _directives(SERVICE, "ExecStart") == [
        "/opt/sudarshan/apps/openalgo/.venv/bin/python -m sudarshan.alerts"
    ]
    assert _directives(SERVICE, "Restart") == ["always"]
    # Optional: a missing file means the defaults.
    assert _directives(SERVICE, "EnvironmentFile") == ["-/etc/sudarshan/alerts.env"]


def test_it_writes_only_its_state_directory():
    assert _directives(SERVICE, "ProtectSystem") == ["strict"]
    assert _directives(SERVICE, "ProtectHome") == ["yes"]
    assert _directives(SERVICE, "ReadWritePaths") == []  # not even OpenAlgo's db/
    assert _directives(SERVICE, "StateDirectory") == ["sudarshan-alerts"]
    assert _directives(SERVICE, "StateDirectoryMode") == ["0700"]
    assert Path(config.STATE_DIR).name == "sudarshan-alerts"  # where the code keeps alerts.db


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
        ("RestrictNamespaces", "yes"),
        ("RestrictAddressFamilies", "AF_UNIX AF_INET AF_INET6"),
        ("RestrictSUIDSGID", "yes"),
        ("LockPersonality", "yes"),
        ("SystemCallFilter", "@system-service"),
        ("SystemCallErrorNumber", "EPERM"),
        ("UMask", "0077"),
        ("ReadOnlyPaths", "-/etc/sudarshan"),
        # It holds the owner's API key and needs nothing but OpenAlgo on this server.
        ("IPAddressDeny", "any"),
        ("IPAddressAllow", "localhost"),
    ],
)
def test_service_hardening(key, value):
    assert _directives(SERVICE, key) == [value]


def test_it_waits_for_openalgo_but_never_starts_it():
    """After= orders a boot; Wants= would start OpenAlgo while a deploy has it stopped."""
    after = " ".join(_directives(SERVICE, "After")).split()
    assert "openalgo.service" in after
    for key in ("Wants", "Requires", "BindsTo", "Requisite", "PartOf"):
        assert "openalgo.service" not in " ".join(_directives(SERVICE, key))


def test_the_stop_limit_outlasts_a_clean_stop():
    """Stream closed (watchdog tick + close handshake), then the waiting alerts get their time."""
    (stop,) = _directives(SERVICE, "TimeoutStopSec")
    clean_stop = service.WATCH_EVERY_S + service.WS_OPTIONS["close_timeout"] + service.STOP_FLUSH_S
    assert int(stop) >= clean_stop + 5
    assert outbox.FLUSH_S <= service.STOP_FLUSH_S


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="needs systemd-analyze")
def test_systemd_reads_the_unit_without_complaint(tmp_path):
    shutil.copy(SERVICE, tmp_path / SERVICE.name)
    done = subprocess.run(
        ["systemd-analyze", "verify", str(tmp_path / SERVICE.name)], capture_output=True, text=True
    )
    # The server's own interpreter under /opt/sudarshan is absent on a CI runner; that says
    # nothing about the unit, so only that complaint is set aside.
    absent_on_this_machine = re.compile(
        r"Command /opt/sudarshan/\S+ is not executable: No such file or directory"
    )
    complaints = [
        line
        for line in (done.stdout + done.stderr).splitlines()
        if "sudarshan-alerts" in line and not absent_on_this_machine.search(line)
    ]
    assert complaints == []
