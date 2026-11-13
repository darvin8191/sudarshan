"""What sudarshan/README.md says about the ops lane is what the code does (review L1, L7, L13 and
the text the other fixes added).

A figure in a README nobody checks drifts from the code within a month, and an owner who reads
"13:35 to 16:00" plans around it. These read the README and the code side by side.
"""

from __future__ import annotations

import inspect
import re
from datetime import date
from pathlib import Path

from sudarshan.ops import backup

SUDARSHAN = Path(__file__).resolve().parents[2]
RAW_README = (SUDARSHAN / "README.md").read_text()
README = " ".join(RAW_README.split())  # one line: a phrase may be wrapped anywhere
ALERTS_README = " ".join((SUDARSHAN / "alerts" / "README.md").read_text().split())
DEPLOY = (SUDARSHAN / "ops" / "deploy" / "sudarshan-deploy").read_text()


class _Evening:
    """A calendar with a special session (Muhurat 2026: 14:05 to 15:40) on a day that is closed."""

    def day_window(self, _day):
        return (14 * 60 + 5, 15 * 60 + 40)

    def trades_anyway(self, _day):
        return False


# ---- the port table (L7) ----------------------------------------------------------------------


def test_no_port_5585_and_the_health_document_is_served_on_5582():
    assert "5585" not in RAW_README
    row = [line for line in RAW_README.splitlines() if line.startswith("| 5582 |")]
    assert len(row) == 1 and "/health" in row[0], row


# ---- the first check (L13) --------------------------------------------------------------------


def test_the_first_check_is_run_as_the_jobs_own_user_and_names_what_it_prints():
    assert "**Before the first night.**" in README
    assert "sudo -u sudarshan .venv/bin/python -m sudarshan.ops.backup --check" in RAW_README
    shown = inspect.getsource(backup.check)
    for line in ("not taken:", "cannot be read:"):
        assert f"`{line}`" in README, line
        assert line in shown, line  # and the check does print what the README says it prints


# ---- the hours (L1, L12, M3) ------------------------------------------------------------------


def test_the_hours_the_readme_gives_are_the_ones_in_the_code():
    assert backup.BLOCKED == ("08:30", "16:00")
    assert backup.START_CLEAR_MIN == 60
    assert backup.OVERDUE_S == 30 * 3600
    for phrase in (
        "Never from 08:30 to 16:00 on a trading day",
        "less than 60 minutes",
        "more than 30 hours old",
    ):
        assert phrase in README, phrase


def test_a_special_session_is_kept_for_the_window_the_readme_names():
    spans = backup.block_spans(_Evening(), date(2026, 11, 8))
    assert spans == ((13 * 60 + 35, 16 * 60),)
    assert "13:35 to 16:00 for a session from 14:05 to 15:40" in README
    assert "30 minutes before and 20 after" in README  # the margins that make it so


# ---- the causes (M1, M3) ----------------------------------------------------------------------


def test_every_cause_a_failed_backup_can_give_is_in_both_readmes():
    causes = {
        backup.SPACE,
        backup.CAP,
        backup.WRITE,
        backup.SOURCE,
        backup.INTEGRITY,
        backup.OFFSITE,
        backup.SETTINGS,
        backup.LOCK,
        backup.OVERDUE,
        backup.OFFSITE_SOON,
    }
    assert {"lock", "overdue"} <= causes
    for cause in sorted(causes):
        assert f"`{cause}`" in README, f"{cause} is not in sudarshan/README.md"
        assert re.search(rf"\b{cause}\b", ALERTS_README), f"{cause} is not in alerts/README.md"


# ---- what an install does as root (H1, M1, M3, L4, L11) ---------------------------------------


def test_the_limits_and_modes_the_readme_gives_for_an_install_are_the_tools():
    def setting(name: str) -> int:
        return int(re.search(rf"^{name}=(\d+)\b", DEPLOY, re.MULTILINE).group(1))

    assert (setting("FETCH_TIMEOUT"), setting("DOWNLOAD_TIMEOUT"), setting("GIT_TIMEOUT")) == (
        60,
        900,
        600,
    )
    assert (
        "(a list or a checksum 60 s, the release's frontend 15 minutes, the fetch of the code 10 minutes)"
        in README
    )
    assert 'chmod 644 "${STATE}/deploy.lock"' in DEPLOY and "is mode 644" in README
    assert "if (( n > 10 )); then" in DEPLOY and "keeps the newest ten" in README
    assert "trap on_signal TERM INT HUP" in DEPLOY
    assert "a stop by the system, `^C` or a hangup" in README
