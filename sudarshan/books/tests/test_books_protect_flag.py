"""The Protect switch (G1): off unless set, and served to the page with the session facts."""

from __future__ import annotations

from pathlib import Path

import pytest

from sudarshan.books import protect_flag

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    ("raw", "stage"),
    [
        (None, "off"), ("", "off"), ("off", "off"), ("sandbox", "sandbox"), (" ON ", "on"),
        ("'sandbox'", "sandbox"), ("yes", "off"), ("true", "off"), ("live", "off"),
    ],
)  # fmt: skip
def test_the_stage_is_off_unless_it_is_one_of_the_three_words(monkeypatch, raw, stage):
    if raw is None:
        monkeypatch.delenv(protect_flag.ENV, raising=False)
    else:
        monkeypatch.setenv(protect_flag.ENV, raw)
    assert protect_flag.stage() == stage


def test_the_session_facts_serve_the_stage_and_nothing_else_reads_it():
    auth = (ROOT / "blueprints" / "auth.py").read_text(encoding="utf-8")
    assert auth.count('"gtt_protect": protect_flag.stage()') == 1
    # No order code reads the switch: the GTT service and the gate are the existing ones.
    for folder in ("services", "broker/upstox", "restx_api", "sandbox", "sudarshan/guard"):
        for path in (ROOT / folder).rglob("*.py"):
            assert "protect_flag" not in path.read_text(encoding="utf-8"), path


def test_the_release_does_not_switch_it_on():
    env = (ROOT / "sudarshan" / "ops" / "deploy" / "openalgo-env.conf").read_text(encoding="utf-8")
    assert protect_flag.ENV not in env or f"{protect_flag.ENV}=off" in env.replace(" ", "")
