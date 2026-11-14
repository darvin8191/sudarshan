"""The System page's status read under eventlet: it never waits on a file (M20.8, P8 10.7).

Production runs ``gunicorn --worker-class eventlet -w 1``: one process, one hub. The status
route reads files another program writes. A plain ``open()`` of a named pipe put where the
report should be waits inside the kernel for a writer, and under eventlet that wait stops every
request of the app, for ever. ``sudarshan.ops.files.read_regular`` opens without waiting and
refuses what is not a regular file. Each case runs in a subprocess (``monkey_patch()`` is
global and cannot be undone) and asserts on elapsed time and on the hub's own beat. The first
test shows the measurement catches a wait served by C code, so the second cannot pass vacuously.
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

REPO = Path(__file__).resolve().parents[2]
PREAMBLE = """
import eventlet
eventlet.monkey_patch()

import json, os, sys, time
sys.path.insert(0, {repo!r})
os.environ.update({{
    "API_KEY_PEPPER": "f" * 64, "APP_KEY": "test-only-app-key-" + "0" * 32,
    "DATABASE_URL": "sqlite:///" + os.path.join(os.getcwd(), "openalgo-test.db"),
    "LOG_DIR": os.path.join(os.getcwd(), "log"), "LOG_TO_FILE": "False",
    "SUDARSHAN_WATCH_STATE_DIR": os.path.join(os.getcwd(), "watch"),
    "SUDARSHAN_BACKUP_STATE_DIR": os.path.join(os.getcwd(), "backup"),
}})
import dotenv
dotenv.load_dotenv = lambda *args, **kwargs: False
dotenv.main.load_dotenv = dotenv.load_dotenv
ticks = []

def ticker():
    while True:
        ticks.append(time.monotonic())
        eventlet.sleep(0.05)

eventlet.spawn(ticker)
eventlet.sleep(0.1)

def widest_gap(start, end):
    inside = [start] + [t for t in ticks if start < t < end] + [end]
    return max(b - a for a, b in zip(inside, inside[1:]))
"""


def run(body: str, tmp_path) -> dict:
    script = PREAMBLE.format(repo=str(REPO)) + textwrap.dedent(body)
    done = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=90, cwd=tmp_path
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_a_wait_served_by_c_code_would_freeze_the_hub(tmp_path):
    """The defect this guards against, measured: a named pipe opened the plain way waits in the
    kernel, and nothing else runs until a writer comes (here, after 0.8 s, from a real thread)."""
    result = run(
        """
        import eventlet.patcher
        real_threading = eventlet.patcher.original("threading")
        real_time = eventlet.patcher.original("time")
        os.mkfifo("pipe")

        def writer():
            real_time.sleep(0.8)
            os.close(os.open("pipe", os.O_WRONLY))

        real_threading.Thread(target=writer, name="system-eventlet-test-writer").start()
        start = time.monotonic()
        os.close(os.open("pipe", os.O_RDONLY))  # what a plain open() of the report would do
        end = time.monotonic()
        print(json.dumps({"took": end - start, "gap": widest_gap(start, end)}))
        """,
        tmp_path,
    )
    assert result["took"] > 0.6 and result["gap"] > 0.6


def test_the_status_read_never_waits_on_what_is_in_the_reports_place(tmp_path):
    result = run(
        """
        os.makedirs("watch"); os.makedirs("backup")
        os.mkfifo("watch/report.json")   # nobody will ever write to it
        os.mkfifo("backup/drill.json")

        from flask import Flask
        import blueprints.sudarshan_system as bridge

        app = Flask("eventlet-system-bridge")
        eventlet.sleep(0.2)
        with app.app_context():
            start = time.monotonic()
            seen = [(bridge.watch_report(time.time()), bridge.drill_result()) for _ in range(200)]
            piped = time.monotonic() - start
            os.unlink("watch/report.json")
            with open("watch/report.json", "w") as handle:
                json.dump({"v": 1, "at": time.time(), "release": None, "heartbeat": {},
                           "checks": [{"name": "disk", "level": "fine"}]}, handle)
            begun = time.monotonic()
            for _ in range(200):
                report = bridge.watch_report(time.time())
                eventlet.sleep(0)
            ended = time.monotonic()
            assistant = bridge.assistant_state()
        print(json.dumps({"piped": piped, "none": all(pair == (None, None) for pair in seen),
                          "checks": len(report["checks"]), "took": ended - begun,
                          "gap": widest_gap(begun, ended), "running": assistant["running"]}))
        """,
        tmp_path,
    )
    assert result["none"] is True, "a pipe is not a report"
    assert result["piped"] < 2.0, "and it was refused, not waited on"
    assert result["checks"] == 1 and result["running"] is False
    assert result["took"] < 2.0 and result["gap"] < 0.5
