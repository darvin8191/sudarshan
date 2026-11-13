"""The family bridge's forward under eventlet: the owner's request waits for sudarshan-family without
stopping the hub (P6 B1, design 7.3).

Production runs ``gunicorn --worker-class eventlet -w 1``: one process, one hub. While a request
waits for the family service, every other request of the app must keep being served. The forward
streams through the shared httpx client, whose sockets ``eventlet.monkey_patch()`` makes green, so
each wait yields. Each case runs in a subprocess (``monkey_patch()`` is global and cannot be
undone) with a slow fake service as a greenlet of the same hub: had the forward blocked, that
service could not even answer, and the ticker would show the stall. The first test shows that the
measurement catches a blocking wait, so the others cannot pass vacuously.
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

def slow_service(answer: bytes, delay: float, seen: list):
    listener = eventlet.listen(("127.0.0.1", 0))

    def serve(sock):  # a service that takes its time, in a greenlet of this same hub
        reader = sock.makefile("rb")
        request_line = reader.readline().decode("latin-1")
        length = 0
        while (line := reader.readline()) not in (b"\\r\\n", b""):
            name, _, value = line.decode("latin-1").partition(":")
            if name.strip().lower() == "content-length":
                length = int(value)
        seen.append((request_line.split()[1], reader.read(length)))
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


@pytest.mark.parametrize(
    ("op", "fields"),
    [("member_list", {}), ("member_create", {"display_name": "Member A", "role": "viewer"})],
)
def test_the_forward_to_a_slow_family_service_yields_to_the_hub(tmp_path, op, fields):
    result = run(
        f"""
        seen = []
        data = [{{"id": 1, "display_name": "Member A"}}]
        port = slow_service(json.dumps({{"status": "success", "data": data}}).encode(), 1.0, seen)
        os.environ["SUDARSHAN_FAMILY_API_URL"] = f"http://127.0.0.1:{{port}}"

        from flask import Flask
        import blueprints.sudarshan_family as bridge
        from sudarshan.family import contract

        bridge._family_uid = os.getuid  # the fake service is this process's own listener
        spec = next(s for s in contract.ADMIN_OPS if s.name == {op!r})
        from utils.httpx_client import get_httpx_client
        get_httpx_client()  # built once, at the first request of a real server: not measured here
        bridge._admin_lazy.get()
        eventlet.sleep(0.2)
        with Flask("eventlet-family-bridge").app_context():
            start = time.monotonic()
            response = bridge._send(spec, {fields!r})
            end = time.monotonic()
            body = response.get_json()
        from sudarshan.vault import FAMILY_ADMIN_INFO, fernet_for
        path, blob = seen[0]
        message = json.loads(fernet_for(os.environ["API_KEY_PEPPER"], FAMILY_ADMIN_INFO).decrypt(blob))
        print(json.dumps({{"status": response.status_code, "body": body, "took": end - start,
                          "path": path, "op": message["op"], "sent": len(seen),
                          "ticks": ticks_between(start, end), "gap": widest_gap(start, end)}}))
        """,
        tmp_path,
    )
    assert result["status"] == 200
    assert result["body"] == {"status": "success", "data": [{"id": 1, "display_name": "Member A"}]}
    assert (result["path"], result["op"], result["sent"]) == (f"/v1/admin/{op}", op, 1)
    assert 0.9 < result["took"] < 2.9  # answered, not cut at the 3 s timeout
    assert result["ticks"] >= 10  # the hub ran on through the wait (20 ticks a second)
    assert result["gap"] < 0.2  # and never stood still: a stall of 0.8 s passed the count above


def test_a_write_cut_at_its_timeout_yields_and_is_not_confirmed(tmp_path):
    """A service slower than the write's 6 s: the request gives up at the timeout without ever
    stopping the hub, and says the write is not confirmed (it is never sent again)."""
    result = run(
        """
        seen = []
        port = slow_service(b'{"status": "success", "data": null}', 8.0, seen)
        os.environ["SUDARSHAN_FAMILY_API_URL"] = f"http://127.0.0.1:{port}"

        from flask import Flask
        import blueprints.sudarshan_family as bridge
        from sudarshan.family import contract

        bridge._family_uid = os.getuid
        bridge.TIMEOUTS_S = {"read": 1.0, "write": 1.0}
        spec = next(s for s in contract.ADMIN_OPS if s.name == "member_remove")
        from utils.httpx_client import get_httpx_client
        get_httpx_client()
        bridge._admin_lazy.get()
        eventlet.sleep(0.2)
        with Flask("eventlet-family-bridge").app_context():
            start = time.monotonic()
            response = bridge._send(spec, {"id": 2, "version": 1})
            end = time.monotonic()
            body = response.get_json()
        print(json.dumps({"status": response.status_code, "code": body["code"],
                          "took": end - start, "sent": len(seen),
                          "ticks": ticks_between(start, end), "gap": widest_gap(start, end)}))
        """,
        tmp_path,
    )
    assert (result["status"], result["code"], result["sent"]) == (504, "not_confirmed", 1)
    assert 0.9 < result["took"] < 3.0
    assert result["ticks"] >= 10 and result["gap"] < 0.2
