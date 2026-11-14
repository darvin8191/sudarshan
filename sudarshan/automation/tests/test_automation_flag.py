"""SUDARSHAN_AUTOMATION: off by default, read on every call, an unknown value is off (A1)."""

from __future__ import annotations

import logging

import pytest

from sudarshan.automation import flag


def test_it_ships_off():
    assert flag.stage() == "off"


@pytest.mark.parametrize(
    ("raw", "reads"),
    [
        ("off", "off"),
        ("sandbox", "sandbox"),
        ("on", "on"),
        (" 'Sandbox' ", "sandbox"),
        ('"ON"', "on"),
    ],
)
def test_the_three_stages(monkeypatch, raw, reads):
    monkeypatch.setenv(flag.ENV, raw)
    assert flag.stage() == reads


@pytest.mark.parametrize("raw", ["live", "true", "1", "yes", "", "sandbox on"])
def test_any_other_value_is_off_and_said_once(monkeypatch, caplog, raw):
    monkeypatch.setenv(flag.ENV, raw)
    monkeypatch.setattr(flag, "_warned_about", None)
    with caplog.at_level(logging.WARNING, logger=flag.logger.name):
        assert [flag.stage() for _ in range(3)] == ["off"] * 3
    said = [r for r in caplog.records if "automation stays off" in r.getMessage()]
    assert len(said) == (1 if raw.strip() else 0)
    assert all(raw not in r.getMessage() for r in said if raw.strip())  # the value is not echoed


def test_it_is_read_on_every_call(monkeypatch):
    monkeypatch.setenv(flag.ENV, "sandbox")
    assert flag.stage() == "sandbox"
    monkeypatch.setenv(flag.ENV, "off")
    assert flag.stage() == "off"
