"""A forgotten confirm phrase is set back on the server, and every wrong phrase is in History.

The reset runs in a process of its own beside the app, so the tests keep two stores on one
database file: the app's (which caches what it read) and the command's.
"""

from __future__ import annotations

import pytest

from sudarshan.guard import killswitch, phrase, settings_rules, store, words
from sudarshan.guard.store import RiskStore

TYPED = "Kesari7"  # a phrase the owner set; it must never appear in any output or row


@pytest.fixture
def two(tmp_path, monkeypatch):
    """The app's store (installed as STORE) and the command's, on one file."""
    path = tmp_path / "risk.db"
    app, cli = RiskStore(path), RiskStore(path)
    monkeypatch.setattr(store, "STORE", app)
    yield app, cli
    app.dispose()
    cli.dispose()


def _lines():
    said = []
    return said, said.append


def _history(app, kind):
    return [row for row in app.history() if row.kind == kind]


# -- the command ----------------------------------------------------------------------------------


def test_status_says_whether_it_is_the_default_and_never_the_phrase(two):
    app, _cli = two
    said, out = _lines()
    assert phrase.status(out) == 0
    assert said == [phrase.IS_DEFAULT]
    app.save_settings("owner", confirm_phrase=TYPED)
    said, out = _lines()
    assert phrase.status(out) == 0
    assert said == [phrase.NOT_DEFAULT]
    assert TYPED not in " ".join(said) and "STOP" not in " ".join(said)


def test_a_reset_sets_the_default_writes_history_and_tells_the_owner(two):
    app, cli = two
    app.save_settings("owner", confirm_phrase=TYPED)
    told = []
    said, out = _lines()
    assert phrase.reset(out, tell=lambda text: told.append(text) or "Telegram was told.") == 0
    assert app.settings(fresh=True).confirm_phrase == "STOP"
    rows = _history(app, "phrase_reset")
    assert [(r.summary, r.surface) for r in rows] == [(words.PHRASE_RESET_AUDIT, "server")]
    assert len(told) == 1 and "set back to the default on the server" in told[0]
    assert "within seconds" in " ".join(said) and "Telegram was told." in said
    for text in (*said, *told, rows[0].summary, str(rows[0].detail)):
        assert TYPED not in text


def test_a_reset_of_the_default_changes_and_tells_nothing(two):
    app, _cli = two
    told = []
    said, out = _lines()
    assert phrase.reset(out, tell=told.append) == 0
    assert told == [] and _history(app, "phrase_reset") == []
    assert said == [f"{phrase.IS_DEFAULT} {phrase.NOTHING_CHANGED}"]


def test_a_notice_that_cannot_be_sent_is_said_in_the_output(two, monkeypatch):
    app, _cli = two
    app.save_settings("owner", confirm_phrase=TYPED)
    monkeypatch.setattr(phrase, "tell_owner", lambda text: "Telegram was not told: the bot is off.")
    said, out = _lines()
    assert phrase.reset(out, tell=phrase.tell_owner) == 0
    assert said[-1] == "Telegram was not told: the bot is off."
    assert app.settings(fresh=True).confirm_phrase == "STOP"  # the reset stands without it


def test_a_phrase_that_cannot_be_saved_is_not_told_as_done(two, monkeypatch):
    app, _cli = two
    app.save_settings("owner", confirm_phrase=TYPED)

    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(app, "save_settings", broken)
    said, out = _lines()
    assert phrase.reset(out, tell=lambda text: pytest.fail("told")) == 1
    assert said == [phrase.NOT_SAVED]


def test_the_notice_says_the_bot_is_off_when_it_is(monkeypatch):
    from services.telegram_alert_service import telegram_alert_service

    monkeypatch.setattr(telegram_alert_service, "is_bot_active", lambda: False)
    assert phrase.tell_owner("x") == "Telegram was not told: the Telegram bot is off."


def test_main_takes_one_of_status_or_reset(two, capsys):
    app, _cli = two
    assert phrase.main(["--status"]) == 0
    assert capsys.readouterr().out.strip() == phrase.IS_DEFAULT
    with pytest.raises(SystemExit):
        phrase.main([])


# -- the app takes it within seconds, with no restart ---------------------------------------------


def test_the_app_judges_the_phrase_as_reset_while_its_cache_still_holds_the_old_one(two):
    app, cli = two
    app.save_settings("owner", band_on=True, confirm_phrase=TYPED)
    assert app.settings().confirm_phrase == TYPED  # cached
    cli.save_settings("server", confirm_phrase="STOP")  # another process; the app's cache is stale
    assert app.settings().confirm_phrase == TYPED
    status, message, saved = settings_rules.change({"band_on": False}, phrase="stop", by="owner")
    assert (status, message) == (200, words.SETTINGS_SAVED) and saved.band_on is False
    # and the old phrase is no longer accepted
    app.save_settings("owner", band_on=True)
    assert settings_rules.change({"band_on": False}, phrase=TYPED, by="owner")[0] == 400


def test_the_kill_switch_asks_for_the_phrase_fresh_too(two, monkeypatch):
    app, cli = two
    app.save_settings("owner", confirm_phrase=TYPED)
    assert app.settings().confirm_phrase == TYPED
    cli.save_settings("server", confirm_phrase="STOP")
    seen = []
    monkeypatch.setattr(killswitch, "recover", lambda: seen.append("past the phrase"))
    monkeypatch.setattr(killswitch.risk_gate.GATE, "state", lambda: None)
    owner = killswitch.Owner("o", "app", "o", None, None, None)
    killswitch.resume(owner, phrase="stop")
    assert seen == ["past the phrase"]


# -- a wrong phrase leaves a line in History ------------------------------------------------------


def test_a_wrong_phrase_on_a_settings_change_is_in_history_without_the_text(two):
    app, _cli = two
    app.save_settings("owner", size_on=True, max_lots_fo=2)
    status, message, _saved = settings_rules.change(
        {"max_lots_fo": 9}, phrase="hunter2-wrong", by="owner"
    )
    assert (status, message) == (400, words.PHRASE_WRONG)
    (row,) = _history(app, "phrase_refused")
    assert row.summary == words.phrase_refused("change lots per F&O order")
    assert (row.surface, row.actor) == ("app", "owner")
    assert row.detail == {"asked": "change lots per F&O order", "changes": ["max_lots_fo"]}
    assert "hunter2" not in repr(row)


def test_a_wrong_phrase_to_change_the_phrase_names_it_but_never_either_phrase(two):
    app, _cli = two
    settings_rules.change({"confirm_phrase": "NewOne1"}, phrase="wrongone", by="owner")
    (row,) = _history(app, "phrase_refused")
    assert row.summary == words.phrase_refused("change the confirm phrase")
    assert "NewOne1" not in repr(row) and "wrongone" not in repr(row)


def test_a_missing_phrase_is_asked_for_not_recorded_as_wrong(two):
    app, _cli = two
    app.save_settings("owner", size_on=True, max_lots_fo=2)
    assert settings_rules.change({"max_lots_fo": 9}, phrase="", by="owner")[0] == 400
    assert _history(app, "phrase_refused") == []


@pytest.mark.parametrize(
    ("surface", "exit_all", "asked"),
    [
        ("app", False, words.PHRASE_ASKED_KS_ON),
        ("telegram", True, words.PHRASE_ASKED_KS_EXIT),
    ],
)
def test_a_wrong_phrase_on_the_kill_switch_is_in_history_from_either_surface(
    two, monkeypatch, surface, exit_all, asked
):
    from sudarshan.guard import flag

    app, _cli = two
    monkeypatch.setenv(flag.ENV, "on")
    owner = killswitch.Owner("o", surface, f"{surface}:1", None, None, None)
    answer = killswitch.activate(owner, exit_all=exit_all, phrase="nope-text")
    assert (answer.status, answer.message) == (400, words.PHRASE_WRONG)
    (row,) = _history(app, "phrase_refused")
    assert (row.summary, row.surface, row.actor) == (
        words.phrase_refused(asked),
        surface,
        f"{surface}:1",
    )
    assert "nope-text" not in repr(row)


def test_tries_in_a_row_share_one_line_counted_up(two, monkeypatch):
    from sudarshan.guard import flag

    app, _cli = two
    monkeypatch.setenv(flag.ENV, "on")
    owner = killswitch.Owner("o", "app", "o", None, None, None)
    for _ in range(3):
        killswitch.activate(owner, exit_all=False, phrase="no")
    (row,) = _history(app, "phrase_refused")
    assert row.count == 3


def test_the_readme_gives_the_exact_command_the_owner_types():
    from pathlib import Path

    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text()
    assert (
        "cd /opt/sudarshan/apps/openalgo && sudo -u sudarshan .venv/bin/python "
        "-m sudarshan.guard.phrase --reset"
    ) in readme


# -- History counts every wrong phrase the same way (release review v0.8.0, L9) -------------------


def test_an_empty_phrase_to_turn_the_kill_switch_on_is_asked_for_not_recorded(two, monkeypatch):
    """Before: Telegram /killswitch on with no phrase wrote "A wrong confirm phrase was refused
    when asked to turn the kill switch on", while an empty box for a settings change wrote nothing."""
    from sudarshan.guard import flag

    app, _cli = two
    monkeypatch.setenv(flag.ENV, "on")
    owner = killswitch.Owner("o", "telegram", "telegram:1", None, None, None)
    for empty in ("", "   ", None):
        answer = killswitch.activate(owner, exit_all=False, phrase=empty)
        assert (answer.status, answer.message) == (400, words.PHRASE_WRONG)
    assert _history(app, "phrase_refused") == []


def test_wrong_phrases_to_turn_the_kill_switch_off_share_one_line_counted_up(two, monkeypatch):
    """Before: each wrong phrase on resume wrote a line of its own ("Resume refused. ..."), so five
    tries in a minute were five lines and no count, unlike turning it on or changing a limit."""
    app, _cli = two
    monkeypatch.setattr(killswitch.risk_gate.GATE, "state", lambda: None)
    owner = killswitch.Owner("o", "app", "o", None, None, None)
    for _ in range(5):
        answer = killswitch.resume(owner, phrase="nope-text")
        assert (answer.status, answer.message) == (400, words.PHRASE_WRONG)
    killswitch.resume(owner, phrase="")  # an empty box is asked for again, not recorded

    (row,) = _history(app, "phrase_refused")
    assert (row.summary, row.count, row.surface) == (
        words.phrase_refused(words.PHRASE_ASKED_KS_OFF),
        5,
        "app",
    )
    assert _history(app, "resume_refused") == []
    assert "nope-text" not in repr(row)


# -- a failed save never writes the phrase into the log (release review v0.8.0, L10) --------------


def test_a_phrase_that_cannot_be_saved_is_not_written_into_the_log(two, caplog):
    """Before: SQLAlchemy's error text carried the statement's parameters, the new phrase among
    them, and logger.exception wrote it into log/errors.jsonl; the redaction there only knows
    key=value shapes."""
    from sqlalchemy import text

    app, _cli = two
    app.settings()  # the engine is made
    with app._engine.begin() as connection:  # the disk fills up between the check and the save
        connection.execute(text(
            "CREATE TRIGGER no_room BEFORE UPDATE ON risk_settings "
            "BEGIN SELECT RAISE(ABORT, 'database or disk is full'); END"
        ))  # fmt: skip
    caplog.set_level("DEBUG")

    status, message, _saved = settings_rules.change(
        {"confirm_phrase": TYPED}, phrase="STOP", by="owner"
    )

    assert (status, message) == (503, words.SETTINGS_NOT_SAVED)
    assert "could not be saved" in caplog.text  # logged, with its cause
    assert TYPED not in caplog.text


def test_the_owner_vault_hides_statement_parameters_too(tmp_path):
    """L10's rule for every store of the owner's secrets: the vault's sealed TOTP key and PIN
    never reach a log through a failed statement either."""
    from sudarshan.vault.store import Vault

    vault = Vault(tmp_path / "vault.db", pepper="test-only-pepper-" + "0" * 32)
    vault._session().close()
    engine = vault._sessionmaker.kw["bind"]
    try:
        assert engine.hide_parameters is True
    finally:
        engine.dispose()
