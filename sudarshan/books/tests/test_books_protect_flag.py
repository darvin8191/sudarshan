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


def test_the_session_facts_serve_the_stage_and_no_order_code_reads_it_directly():
    auth = (ROOT / "blueprints" / "auth.py").read_text(encoding="utf-8")
    assert auth.count('"gtt_protect": protect_flag.stage()') == 1
    # No order code reads the switch itself. The one rule that does is
    # sudarshan/books/protect_check.sandbox_only, which can only move a Protect to the sandbox
    # (test_books_protect_check.py): the GTT services ask it, and nothing else does.
    for folder in ("services", "broker/upstox", "restx_api", "sandbox", "sudarshan/guard"):
        for path in (ROOT / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "protect_flag" not in text, path
            if "protect_check.sandbox_only" in text:
                assert path.name in ("place_gtt_order_service.py", "modify_gtt_order_service.py")


def test_no_release_file_sets_it_to_on():
    """Protect in Live is a later release (G3), after the owner's session S13. A release may set
    ``sandbox``; none sets ``on`` yet."""
    env = (ROOT / "sudarshan" / "ops" / "deploy" / "openalgo-env.conf").read_text(encoding="utf-8")
    for line in env.splitlines():
        if line.strip().startswith(protect_flag.ENV):
            value = line.replace("=", " ").split()[1].strip("'\"").lower()
            assert value in ("off", "sandbox"), line
