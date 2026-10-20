"""The launcher's reading of the unit's EnvironmentFile, so Test now runs with the timer's settings.

The child itself is covered by test_autologin_e2e.py (a real Test now with a settings file that
overrides the inherited environment) and test_autologin_eventlet.py (the cooperative wait).
"""

from __future__ import annotations

import sys

from sudarshan.autologin import launcher


def test_reads_only_sudarshan_settings_like_systemd(tmp_path):
    path = tmp_path / "autologin.env"
    path.write_text(
        "# comment\n"
        "; another comment\n"
        "\n"
        "SUDARSHAN_AUTOLOGIN_WINDOW=08:30-12:00\n"
        "  SUDARSHAN_AUTOLOGIN_LOG_LEVEL = 'DEBUG'  \n"
        'SUDARSHAN_OPENALGO_URL="http://127.0.0.1:5000"\n'
        "SUDARSHAN_NO_VALUE\n"
        "PATH=/nowhere\n"
        "LD_PRELOAD=/tmp/x.so\n"
        "sudarshan_lower=1\n"
    )
    assert launcher.read_settings(path) == {
        "SUDARSHAN_AUTOLOGIN_WINDOW": "08:30-12:00",
        "SUDARSHAN_AUTOLOGIN_LOG_LEVEL": "DEBUG",
        "SUDARSHAN_OPENALGO_URL": "http://127.0.0.1:5000",
    }


def test_no_file_means_no_settings(tmp_path):
    assert launcher.read_settings(tmp_path / "missing.env") == {}


def test_an_unreadable_file_is_reported_not_fatal(tmp_path, caplog):
    path = tmp_path / "autologin.env"
    path.write_bytes(b"SUDARSHAN_X=\xff\xfe\n")
    assert launcher.read_settings(path) == {}
    assert "cannot read" in caplog.text


def test_the_child_gets_the_file_and_the_file_wins(tmp_path, monkeypatch):
    settings = tmp_path / "autologin.env"
    settings.write_text("SUDARSHAN_AUTOLOGIN_WINDOW=09:00-10:00\n")
    monkeypatch.setenv("SUDARSHAN_AUTOLOGIN_ENV_FILE", str(settings))
    monkeypatch.setenv("SUDARSHAN_AUTOLOGIN_WINDOW", "08:30-15:30")
    monkeypatch.setenv("SUDARSHAN_KEPT", "inherited")
    out = tmp_path / "child.log"
    show = (
        "import os; print(os.environ['SUDARSHAN_AUTOLOGIN_WINDOW'], os.environ['SUDARSHAN_KEPT'])"
    )
    status = launcher.run_test_child(
        "run-env", command=[sys.executable, "-c", show], log_path=out, timeout_s=20
    )
    assert status == 0
    assert out.read_text().split() == ["09:00-10:00", "inherited"]
