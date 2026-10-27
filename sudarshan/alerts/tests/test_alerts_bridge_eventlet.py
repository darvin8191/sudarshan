"""The bridge's forward under eventlet: the request waits for the service without stopping the hub.

Production runs ``gunicorn --worker-class eventlet -w 1``: one process, one hub. While a request
waits for sudarshan-alerts, every other request of the app must keep being served. The forward
uses the shared httpx client, whose sockets ``eventlet.monkey_patch()`` makes green, so the wait
yields. Each case runs in a subprocess (``monkey_patch()`` is global and cannot be undone) with a
slow fake service as a greenlet of the same hub: had the forward blocked, that service could not
even answer, and the ticker would show the stall. The first test shows the measurement catches a
blocking wait, so the second cannot pass vacuously.
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
os.environ.update({{
    "API_KEY_PEPPER": "f" * 64, "APP_KEY": "test-only-app-key-" + "0" * 32,
    "DATABASE_URL": "sqlite:///" + os.path.join(os.getcwd(), "openalgo-test.db"),
    "LOG_DIR": os.path.join(os.getcwd(), "log"), "LOG_TO_FILE": "False",
    "REDIRECT_URL": "http://127.0.0.1:5000/upstox/callback",
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

def ticks_between(start, end):
    return sum(1 for t in ticks if start < t < end)
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
    """The defect this guards against, measured: a wait served by C code stops every greenlet."""
    result = run(
        """
        import eventlet.patcher
        original_time = eventlet.patcher.original("time")
        start = time.monotonic()
        original_time.sleep(1.0)
        end = time.monotonic()
        print(json.dumps({"ticks": ticks_between(start, end)}))
        """,
        tmp_path,
    )
    assert result["ticks"] == 0


def test_the_forward_to_a_slow_service_yields_to_the_hub(tmp_path):
    result = run(
        """
        listener = eventlet.listen(("127.0.0.1", 0))
        port = listener.getsockname()[1]

        def serve(sock):  # a service that takes a second, in a greenlet of this same hub
            reader = sock.makefile("rb")
            length = 0
            while (line := reader.readline()) not in (b"\\r\\n", b""):
                name, _, value = line.decode("latin-1").partition(":")
                if name.strip().lower() == "content-length":
                    length = int(value)
            reader.read(length)
            eventlet.sleep(1.0)
            data = json.dumps({"status": "success", "data": {"telegram": True, "app": True}})
            sock.sendall(
                b"HTTP/1.1 200 OK\\r\\nContent-Type: application/json\\r\\nContent-Length: "
                + str(len(data)).encode() + b"\\r\\nConnection: close\\r\\n\\r\\n" + data.encode()
            )
            sock.close()

        def accept():
            while True:
                sock, _ = listener.accept()
                eventlet.spawn(serve, sock)

        eventlet.spawn(accept)
        os.environ["SUDARSHAN_ALERTS_API_URL"] = f"http://127.0.0.1:{port}"

        import httpx
        from flask import Flask
        import blueprints.sudarshan_alerts as bridge

        client = httpx.Client(trust_env=False)
        bridge.get_httpx_client = lambda: client
        with Flask("eventlet-bridge").app_context():
            start = time.monotonic()
            response, status = bridge._call("prefs_get")
            end = time.monotonic()
        print(json.dumps({"status": status, "body": response.get_json(), "took": end - start,
                          "ticks": ticks_between(start, end)}))
        """,
        tmp_path,
    )
    assert result["status"] == 200 and result["body"]["data"] == {"telegram": True, "app": True}
    assert 0.9 < result["took"] < 2.9  # answered, not cut at the 3 s timeout
    assert result["ticks"] >= 10  # the hub ran on through the wait (20 ticks a second)
