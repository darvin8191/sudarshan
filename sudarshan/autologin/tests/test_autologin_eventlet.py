""" "Test now" waits for its child process inside a request; under eventlet that must not stall.

Production runs ``gunicorn --worker-class eventlet -w 1``: one process, one hub. While the request
waits for the worker child, the hub must keep serving, not least the child's own hand-off calls
back into this same process. ``eventlet.monkey_patch()`` makes ``subprocess.Popen.wait`` a polling
wait that yields; these tests prove the launcher gets that wait, under a real hub, by measuring
whether a ticking greenlet keeps running. The first test shows that a blocking wait is caught.

Each case runs in a subprocess: ``monkey_patch()`` is global and cannot be undone.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip(
    "eventlet", reason="eventlet is installed by the production installer, not by pyproject"
)

REPO = Path(__file__).resolve().parents[3]

PREAMBLE = """
import eventlet
eventlet.monkey_patch()

import json, os, sys, time
sys.path.insert(0, {repo!r})
ticks = []

def ticker():
    while True:
        ticks.append(time.monotonic())
        eventlet.sleep(0.05)

eventlet.spawn(ticker)
eventlet.sleep(0.1)

def ticks_between(start, end):
    return sum(1 for t in ticks if start < t < end)

def leftover_children():
    try:
        return os.waitpid(-1, os.WNOHANG) != (0, 0)
    except ChildProcessError:
        return False
"""


def run(body: str, tmp_path) -> dict:
    script = PREAMBLE.format(repo=str(REPO)) + textwrap.dedent(body)
    done = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_a_blocking_wait_would_freeze_the_hub(tmp_path):
    """The defect this guards against, measured: a wait served by C code stops every greenlet.

    (Eventlet greens ``os.waitpid`` as well as ``Popen``, so only the original C call blocks.)
    """
    result = run(
        """
        import subprocess
        import eventlet.patcher
        original_os = eventlet.patcher.original("os")
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.0)"])
        start = time.monotonic()
        original_os.waitpid(child.pid, 0)
        end = time.monotonic()
        print(json.dumps({"elapsed": end - start, "ticks": ticks_between(start, end)}))
        """,
        tmp_path,
    )
    assert result["elapsed"] >= 0.8
    assert result["ticks"] <= 2  # a live hub ticks ~20 times in that second


def test_launcher_waits_cooperatively_and_reaps(tmp_path):
    log = tmp_path / "child.log"
    result = run(
        f"""
        from sudarshan.autologin.launcher import run_test_child
        start = time.monotonic()
        status = run_test_child(
            "run-1",
            command=[sys.executable, "-c", "import time; time.sleep(1.0); print('child done')"],
            log_path=__import__("pathlib").Path({str(log)!r}),
            timeout_s=20,
        )
        end = time.monotonic()
        print(json.dumps({{
            "status": status,
            "elapsed": end - start,
            "ticks": ticks_between(start, end),
            "leftover": leftover_children(),
        }}))
        """,
        tmp_path,
    )
    assert result["status"] == 0
    assert result["elapsed"] >= 1.0
    assert result["ticks"] >= 10  # the hub kept serving while the child ran
    assert result["leftover"] is False  # reaped: no zombie left behind
    assert "child done" in log.read_text()
    assert oct(log.stat().st_mode & 0o777) == "0o600"


def test_launcher_stops_an_overrunning_child_without_stalling(tmp_path):
    result = run(
        f"""
        from sudarshan.autologin.launcher import run_test_child
        start = time.monotonic()
        status = run_test_child(
            "run-2",
            command=[sys.executable, "-c", "import time; time.sleep(30)"],
            log_path=__import__("pathlib").Path({str(tmp_path / "child.log")!r}),
            timeout_s=0.5,
        )
        end = time.monotonic()
        print(json.dumps({{
            "status": status,
            "elapsed": end - start,
            "ticks": ticks_between(start, end),
            "leftover": leftover_children(),
        }}))
        """,
        tmp_path,
    )
    assert result["status"] is None
    assert result["elapsed"] < 5.0
    assert result["ticks"] >= 5
    assert result["leftover"] is False
