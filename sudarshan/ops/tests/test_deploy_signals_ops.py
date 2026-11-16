"""K3 (M13.3): the TRINETRA view's deploy half. Its unit is taken by the deploy tool's own policy
and installed from the release with the other ring units; its migration status is logged before it
starts (``ring_status`` finds ``sudarshan/signals/migrate.py`` by itself); its /health on
127.0.0.1:5588 is asked after the start; its store is in every backup by the ring's rule; and its
one switch is the release's line in openalgo-env.conf. What sudarshan/README.md says is the code's."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest
from test_deploy_journal_ops import policy

from sudarshan.signals import contract
from sudarshan.vault import keys

REPO = Path(__file__).resolve().parents[3]
SYSTEMD = REPO / "sudarshan/ops/systemd"
DEPLOY = REPO / "sudarshan/ops/deploy"
README = (REPO / "sudarshan/README.md").read_text()
UNIT = "sudarshan-signals.service"

pytestmark = pytest.mark.skipif(os.name != "posix", reason="the deploy tool is bash")
bash = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@bash
def test_the_unit_passes_the_deploy_tools_own_policy(tmp_path):
    done = policy(SYSTEMD / UNIT, UNIT)
    assert done.stdout.strip() == "ACCEPTED", done.stdout + done.stderr
    worse = tmp_path / UNIT
    worse.write_text((SYSTEMD / UNIT).read_text().replace("NoNewPrivileges=yes", ""))
    assert policy(worse, UNIT).stdout.splitlines()[-1] == "REFUSED"


def test_the_deploy_tool_asks_its_health_on_its_own_port():
    text = (DEPLOY / "sudarshan-deploy").read_text()
    assert re.search(r"\nring_port\(\) \{[^}]*sudarshan-signals\.service\) echo 5588 ;;", text)
    assert contract.PORT == 5588
    # ring_status runs sudarshan/<service>/migrate.py --status for sudarshan-<service>
    module = "sudarshan.signals.migrate"
    assert (REPO / "sudarshan/signals/migrate.py").is_file()
    assert re.fullmatch(r"sudarshan(\.[a-z0-9_]+)+\.migrate", module)
    # and the ring's backup rule takes /var/lib/sudarshan-*/*.db, signals.db included
    assert contract.STATE_DIR.startswith("/var/lib/sudarshan-") and contract.DB_FILENAME.endswith(
        ".db"
    )


def test_one_switch_shipped_off():
    lines = [
        line.split("#", 1)[0].split()
        for line in (DEPLOY / "openalgo-env.conf").read_text().splitlines()
        if line.split("#", 1)[0].strip()
    ]
    assert [w for w in lines if w[0] == contract.SWITCH_ENV] == [["SUDARSHAN_SIGNALS", "off"]]


def test_the_readme_gives_5588_to_the_trinetra_view_once_with_its_health():
    rows = [line for line in README.splitlines() if line.startswith("| 5588 |")]
    assert len(rows) == 1 and "`sudarshan-signals`" in rows[0] and "GET /health" in rows[0]
    (package,) = [line for line in README.splitlines() if line.startswith("| `signals/` |")]
    assert "`sudarshan-signals`" in package and "SUDARSHAN_SIGNALS" in package


def test_the_readme_seal_table_names_each_of_the_four_purposes_once_with_its_limit():
    limits = {"api": "16 KiB", "journal": "4 KiB", "push": "32 KiB", "relay": "16 KiB"}
    for name, purpose in contract.PURPOSES.items():
        rows = [line for line in README.splitlines() if f"| `{purpose}` |" in line]
        assert len(rows) == 1, purpose
        assert rows[0].rstrip().endswith(f"| {limits[name]} |"), rows[0]
        assert purpose.encode() in keys.PURPOSES
        assert contract.SEAL_LIMITS[name] == int(limits[name].split()[0]) * 1024
