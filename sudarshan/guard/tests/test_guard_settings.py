"""The rules for changing the risk settings (M20.7): what is read as valid, what counts as
loosening, and that a change is judged and saved as one step even with two at once.
"""

from __future__ import annotations

import threading

import pytest

from sudarshan.guard import settings_rules, store, words
from sudarshan.guard.store import RiskSettings


@pytest.mark.parametrize(
    ("values", "changes"),
    [
        ({"band_on": True, "band_both_sides": False}, {"band_on": True, "band_both_sides": False}),
        ({"max_lots_fo": 3, "max_qty_cash": 500.0}, {"max_lots_fo": 3, "max_qty_cash": 500}),
        ({"band_opt_pct": 20, "band_fut_pct": 0.5}, {"band_opt_pct": 20.0, "band_fut_pct": 0.5}),
        ({"daily_loss_limit": 4000}, {"daily_loss_limit": 4000.0}),
        ({"daily_loss_limit": None}, {"daily_loss_limit": None}),
        ({"confirm_phrase": "  Stop Now "}, {"confirm_phrase": "Stop Now"}),
        ({}, {}),
    ],
)
def test_values_read_as_valid(values, changes):
    assert settings_rules.parse(values) == (changes, None)


@pytest.mark.parametrize(
    ("values", "problem"),
    [
        ({"band_on": 1}, words.setting_invalid("band_on")),
        ({"max_lots_fo": True}, words.setting_invalid("max_lots_fo")),
        ({"max_lots_fo": 1.5}, words.setting_invalid("max_lots_fo")),
        ({"max_lots_fo": 10_001}, words.setting_invalid("max_lots_fo")),
        ({"band_opt_pct": float("nan")}, words.setting_invalid("band_opt_pct")),
        ({"band_cash_pct": float("inf")}, words.setting_invalid("band_cash_pct")),
        ({"band_fut_pct": 0.05}, words.setting_invalid("band_fut_pct")),
        ({"burst_window_s": None}, words.setting_invalid("burst_window_s")),
        ({"daily_loss_limit": "4000"}, words.setting_invalid("daily_loss_limit")),
        ({"confirm_phrase": ""}, words.PHRASE_INVALID),
        ({"confirm_phrase": "x" * 41}, words.PHRASE_INVALID),
        ({"confirm_phrase": "stop;"}, words.PHRASE_INVALID),
        ({"updated_by": "someone"}, words.SETTINGS_NOT_UNDERSTOOD),
        (["band_on"], words.SETTINGS_NOT_UNDERSTOOD),
    ],
)
def test_values_that_cannot_be_saved(values, problem):
    assert settings_rules.parse(values) == ({}, problem)


OLD = RiskSettings(
    daily_loss_limit=4000.0, band_on=True, size_on=True, burst_on=True, max_lots_fo=2
)


@pytest.mark.parametrize(
    ("changes", "looser"),
    [
        ({"daily_loss_limit": 5000.0}, ["daily_loss_limit"]),
        ({"daily_loss_limit": None}, ["daily_loss_limit"]),
        ({"daily_loss_limit": 3000.0}, []),
        ({"band_on": False}, ["band_on"]),
        ({"band_both_sides": True}, []),
        ({"revoke_mcp": False}, ["revoke_mcp"]),
        ({"band_opt_pct": 25.0, "band_cash_pct": 1.0}, ["band_opt_pct"]),
        ({"band_opt_min_rs": 5.0}, ["band_opt_min_rs"]),
        ({"max_lots_fo": 3}, ["max_lots_fo"]),
        ({"max_lots_fo": 1}, []),
        ({"burst_n": 20}, ["burst_n"]),
        ({"burst_window_s": 30.0}, ["burst_window_s"]),  # the same orders in less time
        ({"burst_window_s": 120.0}, []),
        ({"confirm_phrase": "HALT"}, ["confirm_phrase"]),
        ({"size_on": True}, []),
    ],
)
def test_what_counts_as_loosening(changes, looser):
    assert settings_rules.loosens(OLD, changes) == looser


def test_turning_a_daily_limit_on_from_none_is_tightening():
    assert settings_rules.loosens(RiskSettings(), {"daily_loss_limit": 4000.0}) == []


def test_a_save_judged_against_an_older_version_is_refused(risk_store):
    first = risk_store.settings()
    assert first.updated_at is None  # as seeded
    saved = risk_store.save_settings("owner", since=None, daily_loss_limit=4000.0)
    with pytest.raises(store.SettingsChanged):
        risk_store.save_settings("owner", since=first.updated_at, daily_loss_limit=4500.0)
    assert risk_store.settings().daily_loss_limit == 4000.0
    again = risk_store.save_settings("owner", since=saved.updated_at, daily_loss_limit=3500.0)
    assert again.daily_loss_limit == 3500.0 and again.updated_at > saved.updated_at


def test_two_changes_judged_at_once_cannot_turn_a_tightening_into_a_loosening(
    risk_store, monkeypatch
):
    """Barrier: both changes are judged against 4,000 before either is saved. Each alone is a
    tightening; saved one after the other, 3,000 then 3,500 would raise the limit without the
    phrase. Exactly one is saved; the other is told to look again."""
    risk_store.save_settings("owner", daily_loss_limit=4000.0)
    barrier = threading.Barrier(2, timeout=10)
    judge = settings_rules.loosens

    def judged_together(old, changes):
        looser = judge(old, changes)
        barrier.wait()
        return looser

    monkeypatch.setattr(settings_rules, "loosens", judged_together)
    answers = {}

    def change(limit):
        answers[limit] = settings_rules.change({"daily_loss_limit": limit}, phrase=None, by="o")

    threads = [threading.Thread(target=change, args=(limit,)) for limit in (3000.0, 3500.0)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    statuses = sorted(answer[0] for answer in answers.values())
    assert statuses == [200, 409]
    kept = next(limit for limit, answer in answers.items() if answer[0] == 200)
    lost = next(answer for answer in answers.values() if answer[0] == 409)
    assert lost[1] == words.SETTINGS_CHANGED_MEANWHILE
    assert risk_store.settings().daily_loss_limit == kept


def test_a_settings_store_that_fails_answers_in_words(risk_store, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(risk_store, "save_settings", broken)
    assert settings_rules.change({"size_on": True}, phrase=None, by="o") == (
        503,
        words.SETTINGS_NOT_SAVED,
        None,
    )
