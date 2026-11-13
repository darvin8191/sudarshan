"""/api/v1/telegram/notify-file under eventlet: waiting for Telegram never stops the hub (JC2).

Production runs ``gunicorn --worker-class eventlet -w 1``: one process, one hub. With
``wait_for_delivery`` the route sends the file inline, on OpenAlgo's existing httpx client, whose
sockets ``eventlet.monkey_patch()`` makes green, so the wait for Telegram must yield. Each case runs
in a subprocess (``monkey_patch()`` is global and cannot be undone) with a slow fake Telegram as a
greenlet of the same hub: had the route blocked, that fake could not even answer, and the ticker
would show the stall. The first test shows the measurement catches a blocking wait, so the second
cannot pass vacuously.
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


def test_the_route_waiting_for_a_slow_telegram_yields_to_the_hub(tmp_path):
    result = run(
        """
        import importlib.util, io, types
        listener = eventlet.listen(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        seen = []

        def serve(sock):  # a Telegram that takes a second, in a greenlet of this same hub
            reader = sock.makefile("rb")
            request_line = reader.readline().decode("latin-1")
            length = 0
            while (line := reader.readline()) not in (b"\\r\\n", b""):
                name, _, value = line.decode("latin-1").partition(":")
                if name.strip().lower() == "content-length":
                    length = int(value)
            body = reader.read(length)
            seen.append((request_line.split(" ")[1], b'name="document"' in body))
            eventlet.sleep(1.0)
            data = b'{"ok": true}'
            sock.sendall(
                b"HTTP/1.1 200 OK\\r\\nContent-Type: application/json\\r\\nContent-Length: "
                + str(len(data)).encode() + b"\\r\\nConnection: close\\r\\n\\r\\n" + data
            )
            sock.close()

        def accept():
            while True:
                sock, _ = listener.accept()
                eventlet.spawn(serve, sock)

        eventlet.spawn(accept)

        import services.telegram_alert_service as alert_service
        alert_service.TELEGRAM_API_BASE = f"http://127.0.0.1:{port}"

        repo = sys.path[0]
        parent = types.ModuleType("restx_api")
        parent.__path__ = [os.path.join(repo, "restx_api")]
        sys.modules["restx_api"] = parent
        spec = importlib.util.spec_from_file_location(
            "restx_api.telegram_bot", os.path.join(repo, "restx_api", "telegram_bot.py")
        )
        routes = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(routes)
        routes.verify_api_key = lambda key: True
        routes.get_telegram_user_by_username = lambda name: {"telegram_id": 424242}
        routes.telegram_alert.is_bot_active = lambda: True
        routes.telegram_alert._get_bot_token = lambda: "123456:test-bot-token-fake"

        from flask import Flask
        from flask_restx import Api
        from limiter import limiter

        app = Flask("eventlet-notify-file")
        app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
        limiter.init_app(app)
        Api(app).add_namespace(routes.api, path="/api/v1/telegram")
        client = app.test_client()
        form = {
            "apikey": "test-api-key", "username": "owner", "caption": "Day report",
            "wait_for_delivery": "true",
            "file": (io.BytesIO(b"%PDF-1.7\\n%%EOF\\n"), "SUDARSHAN_Day-report_20261109.pdf"),
        }
        eventlet.sleep(0.2)
        start = time.monotonic()
        response = client.post(
            "/api/v1/telegram/notify-file", data=form, content_type="multipart/form-data"
        )
        end = time.monotonic()
        print(json.dumps({"status": response.status_code, "body": response.get_json(),
                          "seen": seen, "took": end - start,
                          "ticks": ticks_between(start, end), "gap": widest_gap(start, end)}))
        """,
        tmp_path,
    )
    assert result["status"] == 200
    assert result["body"] == {"status": "success", "message": "File sent successfully"}
    assert result["seen"] == [["/bot123456:test-bot-token-fake/sendDocument", True]]
    assert 0.9 < result["took"] < 10  # it waited for the slow Telegram, and got its answer
    assert result["ticks"] >= 10  # the hub ran on through the wait (20 ticks a second)
    assert result["gap"] < 0.3  # and never stood still: a stall of 0.8 s passed the count above
