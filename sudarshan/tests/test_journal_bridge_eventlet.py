"""The journal bridge's forward under eventlet: the request waits for the service without stopping
the hub (JC1).

Production runs ``gunicorn --worker-class eventlet -w 1``: one process, one hub. While a request
waits for sudarshan-journal, every other request of the app must keep being served. The forward
uses the shared httpx client, whose sockets ``eventlet.monkey_patch()`` makes green, so the wait
yields. Each case runs in a subprocess (``monkey_patch()`` is global and cannot be undone) with a
slow fake service as a greenlet of the same hub: had the forward blocked, that service could not
even answer, and the ticker would show the stall. The first test shows the measurement catches a
blocking wait, so the others cannot pass vacuously.
"""

from __future__ import annotations

import base64
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

def widest_gap(start, end):
    inside = [start] + [t for t in ticks if start < t < end] + [end]
    return max(b - a for a, b in zip(inside, inside[1:]))

def slow_service(answer: bytes, delay: float):
    listener = eventlet.listen(("127.0.0.1", 0))

    def serve(sock):  # a service that takes its time, in a greenlet of this same hub
        reader = sock.makefile("rb")
        length = 0
        while (line := reader.readline()) not in (b"\\r\\n", b""):
            name, _, value = line.decode("latin-1").partition(":")
            if name.strip().lower() == "content-length":
                length = int(value)
        reader.read(length)
        eventlet.sleep(delay)
        sock.sendall(
            b"HTTP/1.1 200 OK\\r\\nContent-Type: application/json\\r\\nContent-Length: "
            + str(len(answer)).encode() + b"\\r\\nConnection: close\\r\\n\\r\\n" + answer
        )
        sock.close()

    def accept():
        while True:
            sock, _ = listener.accept()
            eventlet.spawn(serve, sock)

    eventlet.spawn(accept)
    return listener.getsockname()[1]
"""


def run(body: str, tmp_path) -> dict:
    script = PREAMBLE.format(repo=str(REPO)) + textwrap.dedent(body)
    done = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=90,
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


@pytest.mark.parametrize("op", ["state", "report_file"])
def test_the_forward_to_a_slow_service_yields_to_the_hub(tmp_path, op):
    result = run(
        f"""
        import base64, hashlib
        pdf = b"%PDF-1.7\\n%%EOF\\n"
        data = {{"state": {{"open_trips": 0}}, "report_file": {{
            "file_name": "SUDARSHAN_Day-report_20261109.pdf", "file_mime": "application/pdf",
            "file_sha256": hashlib.sha256(pdf).hexdigest(),
            "file_b64": base64.b64encode(pdf).decode(),
        }}}}[{op!r}]
        port = slow_service(json.dumps({{"status": "success", "data": data}}).encode(), 1.0)
        os.environ["SUDARSHAN_JOURNAL_API_URL"] = f"http://127.0.0.1:{{port}}"

        from flask import Flask
        import blueprints.sudarshan_journal as bridge

        spec = next(s for s in bridge.OPS if s.name == {op!r})
        from utils.httpx_client import get_httpx_client
        get_httpx_client()  # built once, at the first request of a real server: not measured here
        bridge._fernet()
        eventlet.sleep(0.2)
        with Flask("eventlet-journal-bridge").app_context():
            start = time.monotonic()
            answer = bridge._call(spec, {{"id": 3}} if {op!r} == "report_file" else {{}})
            end = time.monotonic()
            response, status = answer if isinstance(answer, tuple) else (answer, answer.status_code)
            body = response.get_data()
        print(json.dumps({{"status": status, "took": end - start,
                          "body": base64.b64encode(body).decode(),
                          "ticks": ticks_between(start, end), "gap": widest_gap(start, end)}}))
        """,
        tmp_path,
    )
    assert result["status"] == 200
    body = base64.b64decode(result["body"])
    if op == "state":
        assert json.loads(body) == {"status": "success", "enabled": True,
                                    "data": {"open_trips": 0}}  # fmt: skip
    else:
        assert body == b"%PDF-1.7\n%%EOF\n"
    assert 0.9 < result["took"] < 2.9  # answered, not cut at the 3 s timeout
    assert result["ticks"] >= 10  # the hub ran on through the wait (20 ticks a second)
    assert result["gap"] < 0.2  # and never stood still: a stall of 0.8 s passed the count above
