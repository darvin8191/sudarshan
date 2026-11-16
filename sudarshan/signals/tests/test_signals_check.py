"""O12: ``python -m sudarshan.signals.check`` proves the letterbox and refuses a stranger's file."""

from __future__ import annotations

import os

import pytest

from sudarshan.signals import check

ME = os.geteuid()


@pytest.fixture
def setup(tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    os.chmod(folder, 0o1733)
    env_file = tmp_path / "signals.env"
    env_file.write_text(f"SUDARSHAN_SIGNALS_PUBLISHER_UID={ME + 1}\n")
    return folder, env_file


def test_a_set_up_letterbox_passes_and_the_probe_is_gone(setup, capsys):
    folder, env_file = setup
    assert check.run(folder, env_file) == 0
    out = capsys.readouterr().out
    assert out.rstrip().endswith("PASS.") and "removed unread" in out
    assert list(folder.iterdir()) == []


def test_without_the_set_up_there_is_nothing_to_check(setup, capsys):
    folder, env_file = setup
    env_file.unlink()
    assert check.run(folder, env_file) == 2
    assert "Run the set-up first" in capsys.readouterr().out


def test_the_publisher_must_not_be_this_account(setup, capsys, monkeypatch):
    folder, env_file = setup
    env_file.write_text("SUDARSHAN_SIGNALS_PUBLISHER_UID=4321\n")
    monkeypatch.setattr(check.os, "geteuid", lambda: 4321)  # as if run as that account
    assert check.run(folder, env_file) == 1
    assert "this account itself" in capsys.readouterr().out
    assert list(folder.iterdir()) == []


def test_a_wrong_folder_fails_before_anything_is_written(setup, capsys):
    folder, env_file = setup
    os.chmod(folder, 0o755)
    assert check.run(folder, env_file) == 1
    assert "mode 1733" in capsys.readouterr().out and list(folder.iterdir()) == []
    assert check.run(folder.parent / "missing", env_file) == 1
    assert "is missing" in capsys.readouterr().out


def test_other_files_in_the_letterbox_are_left_alone(setup):
    folder, env_file = setup
    waiting = folder / "1794800712250-1.json"
    waiting.write_text("{}")
    assert check.run(folder, env_file) == 0
    assert waiting.exists()  # the check judges its own file only
