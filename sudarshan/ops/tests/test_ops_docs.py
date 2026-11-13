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


# ---- the offsite copy against a taken-over server (release review v0.8.0, H1) -------------------

CHANGELOG = (SUDARSHAN / "CHANGELOG.md").read_text()
STANZA = " ".join(CHANGELOG.split("\n## ")[1].split())  # the newest release's stanza, one line


def test_no_claim_says_a_taken_over_server_cannot_upload_and_each_says_what_it_can():
    """The bucket keeps versions, so a copy already uploaded cannot be replaced or removed with the
    upload link; the server can still upload new objects, junk or forged, and a restore picks a
    version by the upload time Oracle shows. Before: the README told the owner to leave versioning
    off, and the README, the module and the stanza said a taken-over server could not delete the
    copies, while about 30 uploads under the logged names would have replaced every one."""
    from sudarshan.ops import offsite

    module = " ".join(offsite.__doc__.split())
    for text in (README, STANZA, module):
        for wrong in ("neither read", "nor delete", "Leave versioning off"):
            assert wrong not in text, wrong
        assert "previous version" in text
        assert "upload" in text and "before anything suspicious" in text
        assert "forged" in text
    assert "tick **Enable object versioning**" in README
    assert "Object versioning must read Enabled" in README
    assert "Target **Latest version of objects**" in README
    assert "Target **Previous versions of objects**" in README
    assert "choose View versions" in README
    assert "That catches a damaged file, not a forged one" in README
    assert "delete the upload link" in README  # before restoring after a takeover


def test_the_readme_says_what_a_restore_needs_and_what_the_bucket_can_cost():
    """Release review v0.8.0 L5: decrypting does not stream, and the free allowance is not a cap
    on a Pay As You Go account."""
    assert "Decrypting needs free memory of about three times the file's size" in README
    assert "Billing, Budgets" in README and "20 GB" in README
    assert "Always Free)" not in STANZA  # the free allowance is not the whole story


def _offsite_steps() -> dict[int, str]:
    """The numbered steps of "Setting up the offsite copy", each as one line of text."""
    part = RAW_README.split("### Setting up the offsite copy")[1].split("\n### ")[0]
    steps: dict[int, str] = {}
    for found in re.finditer(r"^(\d+)\. (.*?)(?=^\d+\. |\Z)", part, re.MULTILINE | re.DOTALL):
        steps[int(found.group(1))] = " ".join(found.group(2).split())
    return steps


def test_the_steps_a_notice_names_are_the_steps_that_do_it():
    """Review L4: a refused upload, an expired link and unusable settings name the README steps
    that make a new link (4, 5, 9) or save the settings (9)."""
    from sudarshan.alerts import notices

    steps = _offsite_steps()
    assert sorted(steps) == list(range(1, 13))
    assert "Create pre-authenticated request" in steps[4] and "Permit object writes" in steps[4]
    assert "only once" in steps[5]
    assert "sudarshan-offsite.bat" in steps[9]
    assert notices.NEW_LINK_STEPS == "(offsite set-up steps 4, 5 and 9 in the README)"
    assert notices.SET_UP_AGAIN == "Run the offsite set-up again (step 9 in the README)"


def test_the_expiry_the_readme_describes_is_the_one_the_code_keeps():
    """Review L1: the date typed is the expiry date the console shows, the first day the link is
    not used; the warnings start 15 days before it, and the check names the date (its words are
    tested in test_backup_offsite.py)."""
    from sudarshan.ops import offsite

    assert offsite.WARN_DAYS == 15
    assert "From 15 days before the date" in README
    assert "From 15 days before the link's expiry date" in README
    assert "the last copy goes the day before" in README
    assert "Expiry date of the upload link, as the console shows it" in README
    assert "`offsite: set (the link stops working in N days, on <the date you typed>)`" in README
    assert "set (the link stops working" in inspect.getsource(backup._offsite_state)


def test_the_notes_say_a_logout_leaves_the_api_key_and_what_to_do_after_a_lost_phone():
    """Release review v0.8.0 M2: "a logout ends every device's session" read as if a lost phone
    were harmless after a password change, while the API key it could read keeps placing orders
    from the server's own address."""
    assert "a logout or a password change does not change the API key" in STANZA
    assert "A logout or a password change leaves the key working" in README
    for text in (README, STANZA):
        assert "choose Regenerate" in text and "TradingView alert" in text
        assert "server's own" in text  # why the static IP rule does not stop them
    assert "use the API key and are not affected" not in STANZA  # not "protected"
    assert "## Signing out every device, and a lost phone (M2.3)" in RAW_README


def test_every_status_row_the_release_touches_names_it():
    """Release review v0.8.0 M3 and L11: M5.13 still listed v0.4.0 to v0.5.1 for a change that
    ships in v0.8.0, M2.3 named v0.7.0, and M18.11 kept v0.4.0 and called the offsite copy a hook
    that does nothing until set."""
    rows = {
        cells[1].strip(): cells
        for line in (SUDARSHAN / "STATUS.md").read_text().splitlines()
        if line.startswith("| M") and len(cells := line.split(" | ")) >= 5
    }
    for feature in ("| M2.3", "| M5.13", "| M16.7", "| M18.11", "| M20.7"):
        (cells,) = [c for key, c in rows.items() if c[0] == feature]
        assert "v0.8.0" in cells[3].split(", "), (feature, cells[3])
    (sessions,) = [c for c in rows.values() if c[0] == "| M2.3"]
    assert "v0.7.0" not in sessions[3]
    (backups,) = [c for c in rows.values() if c[0] == "| M18.11"]
    assert "a documented hook" not in backups[1]
