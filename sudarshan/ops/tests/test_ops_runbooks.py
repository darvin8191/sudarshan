"""The README's runbooks say what the code does (M19.4, M19.8, M19.9, M16.2, M19.10).

A runbook is read on the worst day, by someone in a hurry. These hold its file names, figures and
command names to the code, and keep anything private out of a public repository.
"""

from __future__ import annotations

import re
from pathlib import Path

from sudarshan.feed import config as feed_config
from sudarshan.ops import backup
from sudarshan.ops.watch import heartbeat

REPO = Path(__file__).resolve().parents[3]
RAW = (REPO / "sudarshan" / "README.md").read_text(encoding="utf-8")
README = " ".join(RAW.split())
SECTIONS = (
    "## Secrets: where each one lives and what changing it breaks (M19.4)",
    "## If the server is lost (M19.8)",
    "## Changing the server's registered address (M19.9)",
    "## The Telegram bot's name and picture (M16.2)",
    "## Records to keep each year (M19.10)",
)


def _section(title: str) -> str:
    start = RAW.index(title)
    following = RAW.find("\n## ", start + 1)
    return RAW[start : following if following > 0 else len(RAW)]


def test_the_sections_are_there_once_each():
    for title in SECTIONS:
        assert RAW.count(title) == 1, title


def test_the_secrets_table_names_the_files_the_code_reads():
    table = _section(SECTIONS[0])
    assert f"`{feed_config.TOKEN_FILE}`" in table
    assert "`/etc/sudarshan/watch.env`" in table and "`/etc/sudarshan/offsite.json`" in table
    assert "`/etc/sudarshan/feed-clients/`" in table and "`/etc/sudarshan-family/`" in table
    assert "`db/sudarshan-vault/`" in table and "`db/sudarshan_risk.db`" in table
    assert "`keys/`" in table
    assert 'KEYS_DIR = Path(os.getenv("MCP_OAUTH_KEYS_DIR", "keys"))' in (
        REPO / "utils" / "oauth_keys.py"
    ).read_text(encoding="utf-8")
    sample = (REPO / ".sample.env").read_text(encoding="utf-8")
    for key in ("APP_KEY", "API_KEY_PEPPER", "BROKER_API_KEY", "BROKER_API_SECRET"):
        assert f"`{key}`" in table and re.search(rf"^{key}\s*=", sample, re.M), key
    assert "shared with TRINETRA" in table and "**Do not change it.**" in table
    rows = [line for line in table.splitlines() if line.startswith("| ") and "---" not in line]
    assert len(rows) >= 14 and all(line.count("|") == 5 for line in rows)
    assert heartbeat.PING_ENV not in table, "the table says where, the steps say how"


def test_the_lost_server_runbook_follows_the_restore_steps_that_exist():
    text = " ".join(_section(SECTIONS[1]).split())
    for phrase in (
        "the same reserved public address",
        f"`{backup.MANIFEST}`",
        '"Restoring from the offsite copy"',
        "python -m sudarshan.ops.watch --check",
        '"Test now" on the Auto login page',
        "TRINETRA is restored by its own operators from its own runbook; nothing here touches it.",
        "What has not been proven",
    ):
        assert phrase in text, phrase
    assert "### Restoring from the offsite copy" in RAW and "**Restoring** is a manual step" in RAW
    steps = re.findall(r"^(\d)\. \*\*", _section(SECTIONS[1]), re.M)
    assert steps == [str(n) for n in range(1, 9)]


def test_the_address_runbook_carries_the_shipped_order_rate():
    text = " ".join(_section(SECTIONS[2]).split())
    shipped = (REPO / "sudarshan" / "ops" / "deploy" / "openalgo-env.conf").read_text()
    rate = re.search(r"^UPSTOX_ORDER_MAX_PER_SECOND\s+(\d+)", shipped, re.M)[1]
    assert f"at most {rate} orders a second (`UPSTOX_ORDER_MAX_PER_SECOND`)" in text
    assert "once in a calendar week" in text and "Ask Upstox support first" in text
    assert "`MCP_PUBLIC_URL`" in text


def test_the_bot_menu_lists_only_commands_the_bot_has():
    section = _section(SECTIONS[3])
    bot = (REPO / "services" / "telegram_bot_service.py").read_text(encoding="utf-8")
    handled = set(re.findall(r'CommandHandler\(\s*"([a-z_]+)"', bot))
    menu = re.findall(r"^([a-z]+) - \S", section, re.M)
    assert len(menu) >= 10 and set(menu) <= handled, set(menu) - handled
    assert "killswitch" in menu
    left_out = {"closeall", "mode", "stoppython", "link", "unlink"}
    assert not left_out & set(menu) and left_out <= handled
    for name in left_out:
        assert f"`/{name}`" in section
    assert (REPO / "frontend" / "public" / "images" / "android-chrome-512x512.png").is_file()
    assert "सुदर्शन · SUDARSHAN" in section


def test_the_records_list_says_whose_record_counts():
    text = " ".join(_section(SECTIONS[4]).split())
    assert "The statements Upstox issues are the record the regulator and the tax office" in text
    assert "a question for your CA" in text
    for name in ("`journal.db`", "`logs.db`", "`sudarshan_risk.db`"):
        assert name in text, name


def test_nothing_private_is_in_the_readme():
    """A public repository: no address of a real server, no account, no key-like value."""
    addresses = set(re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", RAW))
    assert addresses <= {"127.0.0.1", "160.79.104.0", "0.0.0.0"}, addresses
    assert not re.search(r"hc-ping\.com/[0-9a-f-]{8,}", RAW)
    assert not re.search(r"\b[0-9a-f]{32,}\b", RAW), "nothing that looks like a key"
    assert not re.search(r"\b\d{10}\b", RAW), "nothing that looks like a phone number"
