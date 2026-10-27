"""M18.x: the notices sudarshan-deploy records for the owner's Telegram.

The tool runs as root and holds no Telegram or OpenAlgo secret, so it only records what happened,
one JSON line per fact, in ``events.jsonl`` under its state folder; ``sudarshan-alerts`` words and
sends each one once (``alerts/notices.py``). These tests run the whole tool on the fake server of
``test_deploy_calendar.py`` (fake curl, sudo, git, systemctl, uv, chown, sleep, date first on PATH)
and read what it left, with the Python reader the service uses; ``notice`` itself is cut out of the
tool exactly as it ships and run under the script's own shell options.

What they pin: one line per install, rollback (with its cause and whether the old version is up),
refused unit file and waiting release; a release that fails again at every timer run told once; a
waiting release told once however often the tool looks; the first run of a tool that records
notices telling an install the earlier tool made without a word, once; and a recorder that is
broken in any way costing the notice and never the install or the rollback.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest
from test_deploy_calendar import (
    DEPLOY_TOOL,
    TODAYS_SESSION,
    Box,
    _function,
    _shim,
    whole_tool,
)

from sudarshan.alerts.notices import compose
from sudarshan.autologin.messages import BRAND
from sudarshan.autologin.schedule import IST
from sudarshan.ops.events import read_events

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

EVENTS = "events.jsonl"
HEALTH = "http://127.0.0.1:5000/"
JQ = shutil.which("jq") or "jq"
MV = shutil.which("mv") or "mv"


def aged(box: Box, seconds: float = 3 * 86400) -> Box:
    """A server whose installed release went in long ago, so the first run has no install to tell."""
    old = time.time() - seconds
    os.utime(box.state / "deployed_tag", (old, old))
    return box


def told(box: Box):
    return read_events(box.state / EVENTS)


def install_box(tmp_path: Path, **kw) -> Box:
    """v1.0.0 installed long ago, v1.0.1 published, a Saturday noon: it installs at once."""
    box = aged(Box(tmp_path, latest="v1.0.1", **kw))
    box.calendar("v1.0.0", b"")
    box.calendar("v1.0.1", b"")
    return box


def now_text(box: Box, *events) -> str:
    return compose(list(events), datetime.now(IST))


# ---- an install -------------------------------------------------------------------------------


@whole_tool
def test_an_install_records_one_notice_the_ring_can_read(tmp_path):
    box = install_box(tmp_path)
    box.state.chmod(0o700)  # as a server's folder may be: only root gets in
    (box.state / "deployed_tag").chmod(0o600)
    started = time.time()
    old = os.umask(0o077)  # a root shell with a strict umask must not hide the files
    try:
        done = box.run()
    finally:
        os.umask(old)
    assert done.returncode == 0, done.stdout + done.stderr
    assert box.deployed() == "v1.0.1"
    (event,) = told(box)
    (bdir,) = box.backups.iterdir()
    assert (event.kind, event.src) == ("installed", "deploy")
    assert event.id == f"deploy-{bdir.name}-installed" and event.run == bdir.name
    assert dict(event.facts) == {"tag": "v1.0.1", "was": "v1.0.0", "backup": "yes"}
    assert started - 5 <= event.at <= time.time() + 5
    assert stat.S_IMODE((box.state / EVENTS).stat().st_mode) == 0o644
    assert stat.S_IMODE((box.state / "deployed_tag").stat().st_mode) == 0o644
    assert box.state.stat().st_mode & stat.S_IXOTH  # the ring can walk in to read both
    text = now_text(box, event)
    assert text.splitlines()[0] == BRAND
    assert text.splitlines()[1].startswith("v1.0.1 installed at ")
    assert text.endswith(" IST. A backup was taken.")


@whole_tool
def test_an_install_is_not_told_a_second_time_by_the_next_run(tmp_path):
    box = install_box(tmp_path)
    assert box.run().returncode == 0
    assert (box.state / "notice_announced").read_text() == "v1.0.1\n"
    again = box.run()  # v1.0.1 is installed, minutes old, nothing newer: nothing to say
    assert again.returncode == 0
    assert [e.kind for e in told(box)] == ["installed"]


# ---- the first run of a tool that records notices ---------------------------------------------


@whole_tool
@pytest.mark.parametrize(
    ("age", "backup", "facts"),
    [
        (600, True, {"tag": "v1.0.0", "backup": "yes"}),
        (600, False, {"tag": "v1.0.0", "backup": "no"}),
        (11 * 3600, True, {"tag": "v1.0.0", "backup": "yes"}),
        (13 * 3600, True, None),  # hours ago: history, not news
    ],
)
def test_the_first_run_tells_an_install_the_earlier_tool_made_without_a_word(
    tmp_path, age, backup, facts
):
    box = Box(tmp_path)  # v1.0.0 is installed and is the latest release
    made = time.time() - age
    os.utime(box.state / "deployed_tag", (made, made))
    if backup:
        (box.backups / "20261024-115000-v1.0.0").mkdir()  # the earlier tool's backup of it
    box.calendar("v1.0.0", b"")
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert (box.state / "notice_announced").read_text() == "v1.0.0\n"  # said, or not worth saying
    if facts is None:
        assert told(box) == []
    else:
        (event,) = told(box)
        assert (event.kind, event.id) == ("installed", "deploy-catchup-v1.0.0")
        assert event.run == "catchup-v1.0.0" and dict(event.facts) == facts
        assert abs(event.at - made) <= 1  # told as when it happened, not when it was noticed
    again = box.run()
    assert again.returncode == 0 and len(told(box)) == (0 if facts is None else 1)  # once


@whole_tool
def test_a_server_the_tool_never_installed_on_has_nothing_to_catch_up(tmp_path):
    box = Box(tmp_path)
    (box.state / "deployed_tag").unlink()
    api = f"https://api.github.com/repos/{box.OWNER_REPO}/releases/latest"
    box.serve(api, json.dumps({"tag_name": "", "assets": []}).encode())  # and nothing to install
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert not (box.state / EVENTS).exists() and not (box.state / "notice_announced").exists()


# ---- a rollback -------------------------------------------------------------------------------


def break_install(box: Box, why: str) -> str:
    """Make the install fail the way ``why`` names; returns what the owner reads as the cause."""
    if why == "migrations":
        box.migrations = "1"
        return "a database update did not finish"
    if why == "health":
        box.serve(HEALTH, b"", code="500")
        return "the app did not come up healthy"
    if why == "settings":
        (box.app / ".env").write_text("MAX_WEBSOCKET_CONNECTIONS=3\n")
        path = "v1.0.1/sudarshan/ops/deploy/openalgo-env.conf"
        box.serve(
            f"https://raw.githubusercontent.com/{box.OWNER_REPO}/{path}",
            b"MAX_WEBSOCKET_CONNECTIONS  1  3\n",
        )
        # the backup of .env cannot be written: the settings step fails and the install is undone
        _shim(
            box.bin,
            "mv",
            f'for a in "$@"; do case "$a" in */openalgo.env) exit 1;; esac; done\nexec {MV} "$@"',
        )
        return "a settings update did not finish"
    assert why == "error"  # an unexpected failure: the error trap rolls back
    _shim(
        box.bin,
        "git",
        'if [[ " $* " == *" checkout "* && " $* " == *" refs/tags/v1.0.1"* ]]; then exit 1; fi\n'
        'if [[ " $* " == *" rev-parse "* ]]; then echo 0123456789abcdef0123456789abcdef01234567; fi',
    )
    return "the update stopped unexpectedly"


@whole_tool
@pytest.mark.parametrize("why", ["migrations", "health", "settings", "error"])
def test_a_rollback_records_its_cause_and_that_the_old_version_is_back(tmp_path, why):
    box = install_box(tmp_path)
    cause = break_install(box, why)
    done = box.run()
    assert done.returncode == 1 and "rolling back" in done.stdout, done.stdout + done.stderr
    assert box.deployed() == "v1.0.0"
    (event,) = told(box)
    (bdir,) = box.backups.iterdir()
    assert (event.kind, event.src, event.run) == ("rolled_back", "deploy", bdir.name)
    assert event.id == f"deploy-rolledback-v1.0.1-{why}-yes"
    assert dict(event.facts) == {"tag": "v1.0.1", "was": "v1.0.0", "why": why, "up": "yes"}
    line = now_text(box, event).splitlines()[1]
    assert line.startswith("Could not install v1.0.1 at ")
    assert (
        f": {cause}, so v1.0.0 is back. Nothing for you to do; the release will be looked at."
        in line
    )


@whole_tool
def test_a_rollback_that_leaves_the_old_version_down_says_so(tmp_path):
    box = install_box(tmp_path)
    box.migrations = "1"
    _shim(
        box.bin,
        "systemctl",
        'echo "$*" >> "${FAKE_WEB}/systemctl"\nif [[ "$1" == is-active ]]; then echo failed; exit 3; fi',
    )
    done = box.run()
    assert done.returncode == 1 and "rolling back" in done.stdout, done.stdout + done.stderr
    (event,) = told(box)
    assert event.id == "deploy-rolledback-v1.0.1-migrations-no" and event.fact("up") == "no"
    text = now_text(box, event)
    assert "did not start either. SUDARSHAN is not running." in text
    assert "so v1.0.0 is back" not in text  # never claims what is not true


@whole_tool
def test_a_release_that_fails_at_every_timer_run_is_told_once_per_cause(tmp_path):
    """The timer tries a failed release again every ten minutes; each try must not be a message."""
    box = install_box(tmp_path)
    box.migrations = "1"
    for _ in range(3):
        assert box.run().returncode == 1
    assert [e.kind for e in told(box)] == ["rolled_back"]
    box.migrations = "0"
    break_install(box, "health")  # a different cause is news
    assert box.run().returncode == 1
    assert [(e.kind, e.fact("why")) for e in told(box)] == [
        ("rolled_back", "migrations"), ("rolled_back", "health"),
    ]  # fmt: skip
    box.serve(HEALTH, b"")  # fixed: it installs, and that is told too
    assert box.run().returncode == 0 and box.deployed() == "v1.0.1"
    assert [e.kind for e in told(box)] == ["rolled_back", "rolled_back", "installed"]


# ---- a release that waits ---------------------------------------------------------------------


def waits_at_the_first_gate(tmp_path) -> Box:
    box = aged(Box(tmp_path, latest="v1.0.1", now="3 1000 2026-10-21"))  # a Wednesday, 10:00
    box.calendar("v1.0.0", b"")
    return box


def waits_when_the_window_opens_during_the_download(tmp_path) -> Box:
    box = aged(Box(tmp_path, latest="v1.0.1", now="3 0828 2026-10-21"))
    box.clock_moves_on("https://example.invalid/d", "3 0831 2026-10-21")
    box.calendar("v1.0.0", b"")
    return box


def waits_for_a_session_its_list_adds(tmp_path) -> Box:
    box = aged(Box(tmp_path, latest="v1.0.1"))  # Saturday noon: only the list makes it a session
    box.calendar("v1.0.0", b"")
    box.calendar("v1.0.1", TODAYS_SESSION)
    return box


@whole_tool
@pytest.mark.parametrize(
    "make",
    [waits_at_the_first_gate, waits_when_the_window_opens_during_the_download,
     waits_for_a_session_its_list_adds],
)  # fmt: skip
def test_a_release_that_waits_is_told_once_however_often_the_tool_looks(tmp_path, make):
    box = make(tmp_path)
    for _ in range(3):
        done = box.run()
        assert done.returncode == 0 and "waiting for market close" in done.stdout, done.stdout
    (event,) = told(box)
    assert (event.kind, event.id) == ("waiting", "deploy-wait-v1.0.1")
    assert dict(event.facts) == {"tag": "v1.0.1"} and box.deployed() == "v1.0.0"
    assert now_text(box, event).splitlines()[1] == (
        "v1.0.1 is ready. It will be installed when the market is closed. Nothing for you to do."
    )


@whole_tool
def test_a_release_that_waited_is_told_again_when_it_installs(tmp_path):
    box = waits_at_the_first_gate(tmp_path)
    box.calendar("v1.0.1", b"")
    assert box.run().returncode == 0 and [e.kind for e in told(box)] == ["waiting"]
    box.now = "3 1730 2026-10-21"  # the market has closed
    assert box.run().returncode == 0 and box.deployed() == "v1.0.1"
    assert [e.kind for e in told(box)] == ["waiting", "installed"]


# ---- a refused unit file ----------------------------------------------------------------------

UNIT = "[Unit]\nDescription=x\n[Service]\nUser={user}\nNoNewPrivileges=yes\nExecStart=/bin/true\n"


@whole_tool
def test_a_refused_unit_is_recorded_with_the_install_it_came_with(tmp_path):
    box = install_box(tmp_path)
    units = {
        "sudarshan-evil.service": UNIT.format(user="root"),
        "sudarshan-evil.timer": "[Timer]\nUnit=other.service\n",
    }
    listing = [{"type": "file", "name": name} for name in units]
    api = f"https://api.github.com/repos/{box.OWNER_REPO}/contents/sudarshan/ops/systemd?ref=v1.0.1"
    box.serve(api, json.dumps(listing).encode())
    raw = f"https://raw.githubusercontent.com/{box.OWNER_REPO}/v1.0.1/sudarshan/ops/systemd/"
    for name, body in units.items():
        box.serve(raw + name, body.encode())
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert "ring: sudarshan-evil.service REFUSED by the unit policy" in done.stdout
    assert list(box.units.iterdir()) == []  # nothing was installed from the refused files
    found = told(box)
    assert [e.kind for e in found] == ["installed", "unit_refused", "unit_refused"]
    assert {e.fact("unit") for e in found[1:]} == {"sudarshan-evil.service", "sudarshan-evil.timer"}
    assert {e.id for e in found[1:]} == {
        "deploy-refused-v1.0.1-sudarshan-evil.service",
        "deploy-refused-v1.0.1-sudarshan-evil.timer",
    }
    assert len({e.run for e in found}) == 1  # one run: the owner is told in one message
    text = now_text(box, *found)
    assert text.count("\n") == 2  # the brand line, the install, the refusals as one line
    assert "2 background services in v1.0.1 were not installed: " in text
    assert "sudarshan-evil.service, sudarshan-evil.timer" in text


# ---- a broken recorder never costs an install -------------------------------------------------


def jq_cannot_write_a_notice(box: Box) -> None:
    # the notice builds its line with `jq -cn`; the tool's other uses of jq are left working
    _shim(box.bin, "jq", f'if [[ "$1" == "-cn" ]]; then exit 5; fi\nexec {JQ} "$@"')


def the_file_is_a_folder(box: Box) -> None:
    (box.state / EVENTS).mkdir()


def the_disk_is_full(box: Box) -> None:
    (box.state / EVENTS).symlink_to("/dev/full")


BREAKS = [jq_cannot_write_a_notice, the_file_is_a_folder]
if Path("/dev/full").exists():
    BREAKS.append(the_disk_is_full)


@whole_tool
@pytest.mark.parametrize("break_it", BREAKS, ids=lambda f: f.__name__)
def test_a_notice_that_cannot_be_recorded_never_stops_an_install(tmp_path, break_it):
    box = install_box(tmp_path)
    break_it(box)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert "deployed v1.0.1 OK" in done.stdout and box.deployed() == "v1.0.1"
    assert "unexpected error" not in done.stdout and "rolling back" not in done.stdout


@whole_tool
@pytest.mark.parametrize("break_it", BREAKS, ids=lambda f: f.__name__)
def test_a_notice_that_cannot_be_recorded_never_stops_a_rollback(tmp_path, break_it):
    box = install_box(tmp_path)
    box.migrations = "1"
    break_it(box)
    done = box.run()
    assert done.returncode == 1, done.stdout + done.stderr
    assert "rollback done (openalgo active)" in done.stdout and box.deployed() == "v1.0.0"
    assert "start openalgo" in (box.web / "systemctl").read_text()  # OpenAlgo was started again


@whole_tool
def test_a_unit_refusal_that_cannot_be_recorded_does_not_stop_the_ring_step(tmp_path):
    box = install_box(tmp_path)
    jq_cannot_write_a_notice(box)
    api = f"https://api.github.com/repos/{box.OWNER_REPO}/contents/sudarshan/ops/systemd?ref=v1.0.1"
    box.serve(api, json.dumps([{"type": "file", "name": "sudarshan-evil.service"}]).encode())
    raw = f"https://raw.githubusercontent.com/{box.OWNER_REPO}/v1.0.1/sudarshan/ops/systemd/"
    box.serve(raw + "sudarshan-evil.service", UNIT.format(user="root").encode())
    done = box.run()
    assert done.returncode == 0 and "REFUSED by the unit policy" in done.stdout, done.stdout
    assert "WARNING: ring service step failed" not in done.stdout


# ---- the recorder on its own ------------------------------------------------------------------


def run_notice(tmp_path: Path, calls: str, *, path: Path | None = None, state: str = ""):
    """``notice`` as shipped, under the script's own options, with its error trap set."""
    script = (
        "set -Eeuo pipefail\numask 077\n"
        f'STATE="{state or tmp_path}"; EVENTS="{tmp_path}/{EVENTS}"; NOTICE_RUN=""; NOTICE_AT=""\n'
        "trap 'echo TRAP-FIRED' ERR\n" + _function("notice") + calls + "\necho finished\n"
    )
    env = {**os.environ}
    if path:
        env["PATH"] = f"{path}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        errors="replace",
        env=env,
        timeout=30,
    )


@whole_tool
def test_the_recorder_survives_the_scripts_own_error_handling(tmp_path):
    """The tool runs under set -Eeuo pipefail with an error trap that rolls an install back: a step
    of the recorder that fails (here jq, and here the folder it is to make traversable) must not
    reach that trap."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _shim(bin_dir, "jq", "exit 5")
    done = run_notice(tmp_path, "notice installed some-id tag=v1.0.0", path=bin_dir)
    assert (done.returncode, done.stdout) == (0, "finished\n"), done.stdout + done.stderr
    assert not (tmp_path / EVENTS).exists()
    gone = str(tmp_path / "no-such-folder")  # the line is written, then chmod of the folder fails
    done = run_notice(tmp_path, "notice installed some-id tag=v1.0.0", state=gone)
    assert (done.returncode, done.stdout) == (0, "finished\n"), done.stdout + done.stderr
    assert [e.id for e in read_events(tmp_path / EVENTS)] == ["some-id"]


@whole_tool
def test_a_fact_is_escaped_and_a_line_is_one_line(tmp_path):
    odd = "'tag=v1\"x\\y' 'note=one'$'\\n''two' 'eq=a=b=c' 'empty='"
    done = run_notice(tmp_path, f"notice installed odd-id {odd}")
    assert done.stdout == "finished\n", done.stdout + done.stderr
    (text,) = (tmp_path / EVENTS).read_text().splitlines()
    data = json.loads(text)
    assert data["tag"] == 'v1"x\\y' and data["note"] == "one\ntwo"
    assert data["eq"] == "a=b=c" and data["empty"] == ""
    assert (data["v"], data["src"], data["kind"], data["id"]) == (
        "1",
        "deploy",
        "installed",
        "odd-id",
    )
    assert data["run"] == "odd-id" and data["at"].isdigit()


@whole_tool
def test_an_id_is_recorded_once_and_a_longer_id_is_a_different_one(tmp_path):
    calls = "notice installed deploy-10\nnotice installed deploy-1\nnotice installed deploy-1\n"
    done = run_notice(tmp_path, calls + "notice installed deploy-10")
    assert done.stdout == "finished\n", done.stdout + done.stderr
    assert [e.id for e in read_events(tmp_path / EVENTS)] == ["deploy-10", "deploy-1"]


@whole_tool
def test_the_run_and_the_time_can_be_set_by_the_caller(tmp_path):
    calls = 'NOTICE_RUN="run-7"; NOTICE_AT=1700000000; notice installed a-1 tag=v1.0.0'
    done = run_notice(tmp_path, calls)
    assert done.stdout == "finished\n", done.stdout + done.stderr
    (event,) = read_events(tmp_path / EVENTS)
    assert (event.run, event.at) == ("run-7", 1700000000.0)


@whole_tool
def test_the_file_is_kept_short_and_readable_for_the_ring(tmp_path):
    rows = [
        json.dumps(
            {"v": "1", "id": f"old-{n}", "src": "deploy", "kind": "waiting", "at": "1700000000"}
        )
        for n in range(400)
    ]
    (tmp_path / EVENTS).write_text("\n".join(rows) + "\n")
    (tmp_path / EVENTS).chmod(0o644)
    done = run_notice(tmp_path, "notice installed newest tag=v1.0.0")
    assert done.stdout == "finished\n", done.stdout + done.stderr
    found = read_events(tmp_path / EVENTS)
    assert len(found) == 200 and found[-1].id == "newest" and found[0].id == "old-201"
    assert stat.S_IMODE((tmp_path / EVENTS).stat().st_mode) == 0o644
    assert not list(tmp_path.glob("*.new"))


@whole_tool
def test_the_file_is_created_readable_whatever_the_umask(tmp_path):
    done = run_notice(tmp_path, "notice installed a-1 tag=v1.0.0")  # the harness sets umask 077
    assert done.stdout == "finished\n", done.stdout + done.stderr
    assert stat.S_IMODE((tmp_path / EVENTS).stat().st_mode) == 0o644


def test_the_recorder_holds_no_secret_and_makes_no_call():
    """The tool is root: what it records is facts, and nothing in the recording reads a settings file
    of the ring, an API key or a token, or talks to the network (sudarshan-alerts does the sending)."""
    code = (_function("notice") + _function("notice_catch_up")).lower()
    for word in (
        "curl",
        "wget",
        "/etc/sudarshan",
        ".env",
        "apikey",
        "api_key",
        "token",
        "telegram",
    ):
        assert word not in code, word


def test_every_cause_the_tool_gives_is_one_the_service_words():
    from sudarshan.alerts.notices import ROLLBACK_CAUSES

    text = DEPLOY_TOOL.read_text()
    causes = set(re.findall(r"ROLLBACK_WHY=(\w+);", text))
    assert causes == set(ROLLBACK_CAUSES)
    assert 'ROLLBACK_WHY="error"' in text  # and the error trap's default is worded too
