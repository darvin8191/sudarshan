"""The assistant's locks (M17.9): remote MCP never starts with settings that would allow orders.

Pure vectors for ``locks.py``, a check that it reads each setting exactly as upstream does, the
settings the release ships, and the real app (``locks_probe.py``, in an interpreter of its own):
with remote MCP switched on and a lock open, no MCP rule is registered and ``/mcp`` is a 404
while the app still answers; with both locks set it answers, and no tool that writes exists.
"""

from __future__ import annotations

import io
import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values

from sudarshan.assistant import locks
from sudarshan.assistant.tests import locks_probe
from sudarshan.family.tests.test_g5_routes import probe_environment

REPO = Path(__file__).resolve().parents[3]
SHIPPED = REPO / "sudarshan" / "ops" / "deploy" / "openalgo-env.conf"
ORDER_TOOLS = {
    "place_order", "place_smart_order", "place_basket_order", "place_split_order",
    "place_options_order", "place_options_multi_order", "modify_order", "cancel_order",
    "cancel_all_orders", "close_all_positions", "analyzer_toggle", "send_telegram_alert",
}  # fmt: skip

ON = {"MCP_HTTP_ENABLED": "True"}
LOCKED = {"OPENALGO_MCP_READ_ONLY": "1", "MCP_OAUTH_WRITE_SCOPE_ENABLED": "False"}


@pytest.mark.parametrize(
    ("settings", "is_locked"),
    [
        ({}, False),  # upstream's defaults: tools not read-only, write scope offered
        ({"OPENALGO_MCP_READ_ONLY": "1"}, False),  # the write scope is on unless set off
        ({"MCP_OAUTH_WRITE_SCOPE_ENABLED": "False"}, False),
        (LOCKED, True),
        ({"OPENALGO_MCP_READ_ONLY": " On ", "MCP_OAUTH_WRITE_SCOPE_ENABLED": "false"}, True),
        ({"OPENALGO_MCP_READ_ONLY": "true", "MCP_OAUTH_WRITE_SCOPE_ENABLED": "0"}, True),
        ({"OPENALGO_MCP_READ_ONLY": "yes", "MCP_OAUTH_WRITE_SCOPE_ENABLED": "TRUE"}, False),
        ({"OPENALGO_MCP_READ_ONLY": "0", "MCP_OAUTH_WRITE_SCOPE_ENABLED": "False"}, False),
        ({"OPENALGO_MCP_READ_ONLY": "", "MCP_OAUTH_WRITE_SCOPE_ENABLED": "False"}, False),
        ({"OPENALGO_MCP_READ_ONLY": "2", "MCP_OAUTH_WRITE_SCOPE_ENABLED": "False"}, False),
    ],
)
def test_locked_only_when_both_settings_hold(settings, is_locked):
    assert locks.locked(settings) is is_locked
    assert locks.boot_allows({**ON, **settings}) is is_locked
    assert locks.state({**ON, **settings})["running"] is is_locked


def test_a_refusal_is_logged_once_in_the_owners_words_and_never_raises(caplog):
    logger = logging.getLogger("locks-test")
    with caplog.at_level(logging.ERROR, logger="locks-test"):
        assert locks.boot_allows({**ON}, logger) is False
        assert locks.boot_allows({**ON, **LOCKED}, logger) is True
        assert locks.boot_allows({}, logger) is False  # remote MCP is off: nothing to say
    assert [record.getMessage() for record in caplog.records] == [locks.REFUSED]
    assert "OPENALGO_MCP_READ_ONLY=1" in locks.REFUSED
    assert "MCP_OAUTH_WRITE_SCOPE_ENABLED=False" in locks.REFUSED


def test_the_state_says_which_of_the_three_it_is():
    assert locks.state({})["words"] == locks.OFF
    assert locks.state(LOCKED)["words"] == locks.OFF
    assert locks.state({**ON})["words"] == locks.REFUSED
    assert locks.state({**ON, **LOCKED})["words"] == locks.ON
    assert locks.state({**ON, **LOCKED, "MCP_OAUTH_REQUIRE_APPROVAL": "True"}) == {
        "switched_on": True,
        "read_only": True,
        "write_scope_offered": False,
        "approval_required": True,
        "running": True,
        "words": locks.ON,
    }
    for words in (locks.OFF, locks.ON, locks.REFUSED):
        assert "arm" not in words.lower().split() and "paper" not in words.lower()


def test_each_setting_is_read_as_upstream_reads_it():
    """A rebase that changes how upstream reads a setting must change locks.py with it."""
    server = (REPO / "mcp" / "mcpserver.py").read_text(encoding="utf-8")
    assert (
        'return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")'
        in server
    )
    assert 'def _env_flag(name: str, default: str = "0") -> bool:' in server
    assert 'READ_ONLY = _env_flag("OPENALGO_MCP_READ_ONLY")' in server
    assert "active = toolset in ACTIVE_TOOLSETS and not (READ_ONLY and is_write)" in server
    oauth = (REPO / "blueprints" / "mcp_oauth.py").read_text(encoding="utf-8")
    assert 'if os.getenv("MCP_OAUTH_WRITE_SCOPE_ENABLED", "True").lower() == "true":' in oauth
    assert 'return os.getenv("MCP_OAUTH_REQUIRE_APPROVAL", "False").lower() == "true"' in oauth
    app = (REPO / "app.py").read_text(encoding="utf-8")
    guard = app.index("sudarshan_assistant_locks.boot_allows(os.environ, logger)")
    assert guard < app.index("from blueprints.mcp_http import mcp_http_bp")
    assert (
        'if sudarshan_mcp_ok and os.getenv("MCP_HTTP_ENABLED", "False").lower() == "true":' in app
    )


def _shipped_lines() -> dict[str, list[str]]:
    lines = {}
    for line in SHIPPED.read_text(encoding="utf-8").splitlines():
        words = line.split("#", 1)[0].split()
        if words:
            lines[words[0]] = words[1:]
    return lines


def test_the_release_ships_the_three_settings_and_never_the_switch():
    lines = _shipped_lines()
    assert lines["OPENALGO_MCP_READ_ONLY"] == ["1"]
    assert lines["MCP_OAUTH_WRITE_SCOPE_ENABLED"] == ["False", "True"]
    assert lines["MCP_OAUTH_REQUIRE_APPROVAL"] == ["True", "False"]
    assert "MCP_HTTP_ENABLED" not in lines, "the owner switches remote MCP on, never a release"


def test_a_fresh_install_ends_locked_with_remote_mcp_still_off():
    """What .sample.env holds after the shipped list is applied, read as OpenAlgo reads it."""
    sample = dotenv_values(stream=io.StringIO((REPO / ".sample.env").read_text(encoding="utf-8")))
    assert not locks.locked(sample), "upstream's defaults are the reason for this lock"
    after = dict(sample)
    for key, words in _shipped_lines().items():
        if key not in after or after[key] in words[1:]:
            after[key] = words[0]
    assert locks.locked(after) and locks.approval_required(after)
    assert not locks.switched_on(after)


@pytest.fixture(scope="module")
def starts(tmp_path_factory) -> dict:
    here = tmp_path_factory.mktemp("locks-probe").resolve()
    out, log = here / "report.json", here / "probe.log"
    with log.open("w", encoding="utf-8") as sink:
        done = subprocess.run(
            [sys.executable, "-m", "sudarshan.assistant.tests.locks_probe", str(out)],
            cwd=here,
            env={**probe_environment(here), "SUDARSHAN_RISK_DB": f"{here}/sudarshan_risk.db"},
            stdout=sink,
            stderr=subprocess.STDOUT,
            timeout=280,
            check=False,
        )
    if done.returncode != 0 or not out.exists():
        tail = log.read_text(encoding="utf-8", errors="replace")[-6000:]
        pytest.fail(f"the locks probe did not finish (exit {done.returncode}):\n{tail}")
    return json.loads(out.read_text(encoding="utf-8"))


@pytest.mark.timeout(300)
@pytest.mark.parametrize("case", [name for name in locks_probe.CASES if name != "locked"])
def test_with_a_lock_open_the_app_starts_without_the_assistant(starts, case):
    seen = starts[case]
    assert seen["mcp_rules"] == [], "no MCP or OAuth rule may be registered"
    assert seen["mcp_status"] == 404
    assert seen["app_status"] == 200, "an AI setting never stops the app"


@pytest.mark.timeout(300)
def test_with_both_locks_set_it_answers_and_no_tool_writes(starts):
    seen = starts["locked"]
    assert "/mcp" in seen["mcp_rules"] and "/oauth/token" in seen["mcp_rules"]
    assert seen["mcp_status"] == 401, "no token: refused, but the route is there"
    assert "resource_metadata" in seen["mcp_challenge"]
    assert seen["app_status"] == 200
    assert seen["write_tools_registered"] == []
    assert len(seen["tools_registered"]) > 30, "the read tools are there"
    assert not ORDER_TOOLS & set(seen["tools_registered"])


@pytest.mark.timeout(300)
def test_a_token_that_worked_is_refused_once_access_is_ended(starts):
    """M17.9 on the real transport: the same token, a second later, after ``end_all_access``."""
    seen = starts["locked"]["cutoff"]
    assert seen["before"] == 200 and seen["cutoff_recorded"] is True
    assert seen["after"] == 401, "refused on its next call, not after its 15 minutes"
    assert 'error="invalid_token"' in seen["after_challenge"]
    assert "resource_metadata" in seen["after_challenge"], "so the client signs in again"
    assert seen["stream_after"] == 401
    assert seen["new_token"] == 200, "a sign-in after the cutoff works"


@pytest.mark.timeout(300)
def test_an_open_stream_closes_when_access_is_ended(starts):
    seen = starts["locked"]["cutoff"]
    assert seen["stream_opened"] is True
    assert seen["stream_closed_in_s"] < 5, seen
