"""SUDARSHAN_RISK_GUARD: off unless set, read on every call, unknown values off (M5.13)."""

from __future__ import annotations

import logging

import pytest

from sudarshan.guard import flag


@pytest.mark.parametrize(
    ("value", "stage"),
    [
        (None, "off"),
        ("", "off"),
        ("off", "off"),
        ("sandbox", "sandbox"),
        ("on", "on"),
        (" ON ", "on"),
        ("'sandbox'", "sandbox"),
        ("Sandbox", "sandbox"),
    ],
)
def test_the_stage_is_read_as_written(monkeypatch, value, stage):
    if value is not None:
        monkeypatch.setenv(flag.ENV, value)
    assert flag.stage() == stage


def test_it_is_read_on_every_call(monkeypatch):
    monkeypatch.setenv(flag.ENV, "on")
    assert flag.covers("live")
    monkeypatch.setenv(flag.ENV, "off")
    assert not flag.covers("live")


@pytest.mark.parametrize("value", ["true", "1", "yes", "live", "enabled", "of"])
def test_a_value_it_does_not_know_reads_as_off_with_one_warning(monkeypatch, caplog, value):
    monkeypatch.setattr(flag, "_warned_about", None)
    monkeypatch.setenv(flag.ENV, value)
    with caplog.at_level(logging.WARNING):
        assert flag.stage() == "off"
        assert flag.stage() == "off"
        assert not flag.covers("live") and not flag.covers("sandbox")
    warnings = [r for r in caplog.records if "SUDARSHAN_RISK_GUARD" in r.getMessage()]
    assert len(warnings) == 1


@pytest.mark.parametrize(
    ("value", "live", "sandbox"),
    [("off", False, False), ("sandbox", False, True), ("on", True, True)],
)
def test_which_pipes_each_stage_covers(monkeypatch, value, live, sandbox):
    monkeypatch.setenv(flag.ENV, value)
    assert (flag.covers("live"), flag.covers("sandbox")) == (live, sandbox)
    assert flag.covers("elsewhere") is False


def test_it_ships_off():
    import os

    assert flag.ENV not in os.environ  # conftest: as it ships
    assert flag.stage() == flag.OFF
