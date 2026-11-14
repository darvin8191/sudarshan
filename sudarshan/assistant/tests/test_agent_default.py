"""The /agent page stays as upstream ships it: unconfigured, and unable to trade (M17.1, P8 4.10).

SUDARSHAN does not use the page, so nothing here changes it. These pin what it relies on: a
rebase onto an upstream that turns the agent's trading switch on by default, or that stops the
kill switch from stopping the agent, fails here until a person decides.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
README = " ".join((REPO / "sudarshan" / "README.md").read_text(encoding="utf-8").split())


def _default_of(key_name: str):
    """The default ``_SPEC`` gives ``key_name`` in services/agent/settings.py, from its source
    (importing the module would open the agent's database)."""
    tree = ast.parse((REPO / "services" / "agent" / "settings.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if getattr(key, "id", None) == key_name and isinstance(value, ast.Call):
                kind, default = (ast.literal_eval(argument) for argument in value.args[:2])
                return kind, default
    raise AssertionError(f"services/agent/settings.py no longer gives {key_name} a default")


def test_the_agents_trading_switch_ships_off():
    assert _default_of("KEY_TRADING_ENABLED") == ("bool", False)
    assert _default_of("KEY_VOICE_TRADING_ENABLED") == ("bool", False)


def test_the_kill_switch_still_stops_the_agent():
    source = (REPO / "sudarshan" / "guard" / "killswitch.py").read_text(encoding="utf-8")
    assert 'agent = steps.run("agent", _engage_agent)' in source
    assert "agent_settings.set_kill_switch(True)" in source


def test_the_readme_says_how_to_use_the_laptop_route_safely():
    """M17.2: the stdio server offers every tool unless the laptop sets the read-only setting."""
    assert "## The AI assistant (M17)" in (REPO / "sudarshan" / "README.md").read_text()
    for phrase in (
        "`OPENALGO_MCP_READ_ONLY=1`",
        "ssh -N -L 5000:127.0.0.1:5000",
        "an order-capable secret",
        "The Agent page",
    ):
        assert phrase in README, phrase
