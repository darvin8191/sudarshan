"""The outside heartbeat (M19.5, P8 7.1): which link a run asks for, and that the link is never
written anywhere. No test here leaves the machine: the request is a stand-in."""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path

import pytest

from sudarshan.ops.watch import checks, heartbeat, run

LINK = "https://beat.example.invalid/ping/3f9c1a52-not-a-real-check"
ENV = {heartbeat.PING_ENV: LINK}
FINE = {
    "ist_minute": 600,
    "ist_date": "2026-11-16",
    "window_open": True,
    "health": {
        "feed": {"state": "connected", "market_window": {"open": True}},
        "alerts": {"state": "connected"},
    },
    "enabled": {"alerts": True},
    "openalgo": True,
    "master_date": "2026-11-16",
    "disk": {"free_gb": 40.0, "inodes_free_pct": 80.0},
    "mem_available_mb": 4000,
    "cert_hours_left": 120.0,
}


@pytest.mark.parametrize(
    ("link", "good"),
    [
        (LINK, True),
        ("https://hc.example.invalid", True),
        ("https://hc.example.invalid:8443/a/b", True),
        ("http://hc.example.invalid/ping/x", False),  # not https
        ("https://user:secret@hc.example.invalid/x", False),
        ("https://hc.example.invalid/x?account=1", False),
        ("https://hc.example.invalid/x y", False),
        ("https://127.0.0.1/x", False),
        ("https://localhost/x", False),
        ("https://192.168.1.4/x", False),
        ("ftp://hc.example.invalid/x", False),
        ("", False),
    ],
)
def test_which_links_can_be_used(link, good):
    assert heartbeat.usable(link) is good


def test_the_links_come_from_the_units_environment():
    assert heartbeat.links({}) == heartbeat.NOT_SET
    assert heartbeat.links({heartbeat.PING_ENV: "  "}) == heartbeat.NOT_SET
    assert heartbeat.links(ENV) == (LINK, LINK + "/fail")
    assert heartbeat.links({heartbeat.PING_ENV: f"'{LINK}/'"}) == (LINK + "/", LINK + "/fail")
    other = "https://beat.example.invalid/down/abc"
    assert heartbeat.links({**ENV, heartbeat.FAIL_ENV: other}) == (LINK, other)
    assert heartbeat.links({heartbeat.PING_ENV: "http://x.example.invalid"}) == heartbeat.BAD_LINK
    assert heartbeat.links({**ENV, heartbeat.FAIL_ENV: "nonsense"}) == heartbeat.BAD_LINK


def test_a_beat_asks_for_the_plain_link_or_its_failure_form():
    asked = []
    send = lambda link: asked.append(link) or True  # noqa: E731
    assert heartbeat.beat(ENV, True, send) == heartbeat.SENT
    assert heartbeat.beat(ENV, False, send) == heartbeat.SENT_FAILURE
    assert asked == [LINK, LINK + "/fail"]
    assert heartbeat.beat({}, True, send) == heartbeat.NOT_SET and len(asked) == 2
    assert heartbeat.beat(ENV, True, lambda link: False) == heartbeat.NOT_SENT
    assert heartbeat.beat(ENV, True, lambda link: 1 / 0) == heartbeat.NOT_SENT
    assert set(heartbeat.WORDS) == {
        heartbeat.NOT_SET, heartbeat.BAD_LINK, heartbeat.SENT, heartbeat.SENT_FAILURE,
        heartbeat.NOT_SENT,
    }  # fmt: skip


def test_notices_cannot_go_while_openalgo_or_the_alerts_service_is_failing():
    fine = [checks.Result("openalgo", checks.FINE), checks.Result("service_alerts", checks.FINE)]
    assert heartbeat.notices_can_go(fine)
    assert heartbeat.notices_can_go([checks.Result("service_feed", checks.FAILING)])
    assert heartbeat.notices_can_go([checks.Result("openalgo", checks.UNREAD)])
    assert not heartbeat.notices_can_go([checks.Result("openalgo", checks.FAILING)])
    assert not heartbeat.notices_can_go([checks.Result("service_alerts", checks.FAILING)])


def test_request_never_raises_on_an_address_nothing_answers():
    """A refused connection on this machine: False, no exception (nothing leaves the machine)."""
    assert heartbeat.request("http://127.0.0.1:1/x", timeout=1.0) is False
    assert heartbeat.request("not a link at all") is False


def _run(tmp_path, readings_by_run, env=ENV, installing=bool, record=None):
    settings = run.Settings(tmp_path / "state", tmp_path, tmp_path / ".env")
    asked, codes, at = [], [], 1_800_000_000.0
    for readings in readings_by_run:
        at += 120
        codes.append(
            run.run_once(
                settings,
                clock=lambda at=at: at,
                gather=lambda *a, r=readings, at=at: {**r, "now": at},
                installing=installing,
                env=env,
                send=lambda link: asked.append(link) or True,
            )  # fmt: skip
        )
    return settings, asked, codes


def test_each_run_beats_and_says_failure_once_notices_cannot_go(tmp_path):
    silent = {**FINE, "openalgo": False}
    no_alerts = {**FINE, "health": {**FINE["health"], "alerts": None}}
    settings, asked, codes = _run(tmp_path, [FINE, silent, silent, FINE, no_alerts, no_alerts])
    assert codes == [0] * 6
    assert asked == [LINK, LINK, LINK + "/fail", LINK, LINK, LINK + "/fail"], (
        "one run without an answer is not yet a failure; two are"
    )
    report = json.loads(settings.report_file.read_text())
    assert report["heartbeat"]["state"] == heartbeat.SENT_FAILURE


def test_a_run_that_could_not_keep_its_files_says_failure(tmp_path, monkeypatch):
    from sudarshan.ops import events

    monkeypatch.setattr(events, "record", lambda *a, **k: False)
    low = {**FINE, "disk": {"free_gb": 2.0, "inodes_free_pct": 80.0}}
    _settings, asked, codes = _run(tmp_path, [low])
    assert codes == [1] and asked == [LINK + "/fail"]


def test_during_an_install_the_server_still_says_it_is_alive(tmp_path):
    settings, asked, codes = _run(tmp_path, [FINE, FINE], installing=lambda: True)
    assert codes == [0, 0] and asked == [LINK, LINK]
    assert not settings.state_file.exists()


def test_without_a_link_the_watch_does_everything_else(tmp_path):
    settings, asked, codes = _run(tmp_path, [FINE], env={})
    assert codes == [0] and asked == []
    report = json.loads(settings.report_file.read_text())
    assert report["heartbeat"]["state"] == heartbeat.NOT_SET
    assert "nobody is told if the whole server stops" in report["heartbeat"]["words"]
    settings, asked, codes = _run(tmp_path / "b", [FINE], env={heartbeat.PING_ENV: "http://x.y"})
    assert codes == [0] and asked == []
    assert json.loads(settings.report_file.read_text())["heartbeat"]["state"] == heartbeat.BAD_LINK


def test_the_link_is_never_written_or_logged(tmp_path, caplog, capsys):
    silent = {**FINE, "openalgo": False}
    with caplog.at_level(logging.DEBUG):
        settings, _asked, _codes = _run(tmp_path, [FINE, silent, silent])
    secret = "3f9c1a52"
    for path in settings.state_dir.iterdir():
        if path.is_file():
            assert secret not in path.read_text(errors="replace"), path.name
    assert secret not in caplog.text and "beat.example" not in caplog.text
    printed = capsys.readouterr()
    assert secret not in printed.out + printed.err


def test_the_module_logs_nothing_and_names_no_service_of_its_own():
    """No log call that could carry the link, and no outside host is written into the code: the
    only address it asks for is the owner's."""
    source = (Path(heartbeat.__file__)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "logging" not in imported and "print(" not in source
    hosts = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "://" in node.value
    ]
    assert all(".com" not in host and ".io" not in host for host in hosts), hosts


def test_the_readme_gives_the_owners_steps_and_the_program_agrees():
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
    flat = " ".join(readme.split())
    assert "### The outside heartbeat: the owner's steps" in readme
    for phrase in (
        f"{heartbeat.PING_ENV}=https://",
        f"`{heartbeat.FAIL_ENV}=https://...`",
        "sudo install -m 640 -o root -g sudarshan /dev/null /etc/sudarshan/watch.env",
        "**Period 2 minutes** and **Grace 8 minutes**",
        "SUDARSHAN creates no account anywhere.",
        "with `/fail` added",
        heartbeat.WORDS[heartbeat.NOT_SET],
        "`heartbeat set`",
        "`bad_link`",
    ):
        assert phrase in flat, phrase
    assert checks.RUN_EVERY_MIN == 2
