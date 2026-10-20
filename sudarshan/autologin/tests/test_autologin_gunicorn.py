""" "Test now" under a real gunicorn worker: the production shape, end to end.

The request waits for the worker child while the child calls back into the same single gunicorn
worker (preflight, then the sealed token hand-off). Under eventlet that only completes if the wait
yields to the hub; a blocking wait would leave the hand-off unanswered until the child gave up.
The same flow runs under gthread, OpenAlgo's opt-in worker.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from autologin_fakes import FAKE_PIN, FAKE_TOTP_SECRET, OLD_TOKEN, FakeUpstox

from sudarshan.autologin.worker import SECRET_PIN, SECRET_TOTP
from sudarshan.vault import Vault

pytest.importorskip("gunicorn", reason="gunicorn is installed by the production installer")

TESTS_DIR = Path(__file__).resolve().parent
REPO = TESTS_DIR.parents[2]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.parametrize("worker_class", ["eventlet", "gthread"])
def test_test_now_round_trip_under_gunicorn(
    tmp_path, pepper, openalgo_env_file, holidays_file, worker_class
):
    if worker_class == "eventlet":
        pytest.importorskip("eventlet", reason="eventlet is installed by the production installer")
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    vault_path = tmp_path / "db" / "sudarshan_vault.db"
    vault = Vault(vault_path, pepper=pepper)
    vault.set_secret(SECRET_TOTP, FAKE_TOTP_SECRET)
    vault.set_secret(SECRET_PIN, FAKE_PIN)
    upstox = FakeUpstox().start()
    child_log = tmp_path / "log" / "sudarshan_autologin.log"
    env = {
        **{k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")},
        "API_KEY_PEPPER": pepper,
        "APP_KEY": "test-only-app-key-" + "0" * 32,
        "DATABASE_URL": f"sqlite:///{tmp_path}/openalgo-gunicorn-test.db",
        "LOG_DIR": str(tmp_path / "log"),
        "REDIRECT_URL": "http://127.0.0.1:5000/upstox/callback",
        "SUDARSHAN_VAULT_DB": str(vault_path),
        "SUDARSHAN_NSE_HOLIDAYS_FILE": str(holidays_file),
        "SUDARSHAN_AUTOLOGIN_TEST_LOG": str(child_log),
        "SUDARSHAN_OPENALGO_URL": url,
        "SUDARSHAN_UPSTOX_API_BASE": upstox.api_base,
        "SUDARSHAN_OPENALGO_ENV_FILE": str(openalgo_env_file),
        "AUTOLOGIN_TEST_STORED_TOKEN": OLD_TOKEN,
        "PYTHONPATH": os.pathsep.join([str(REPO), str(TESTS_DIR)]),
    }
    command = [
        sys.executable, "-m", "gunicorn",
        "--worker-class", worker_class, "-w", "1",
        *(["--threads", "8"] if worker_class == "gthread" else []),
        "--bind", f"127.0.0.1:{port}", "--timeout", "120",
        "--chdir", str(REPO), "autologin_gunicorn_app:app",
    ]  # fmt: skip
    server_log = (tmp_path / "gunicorn.log").open("wb")
    server = subprocess.Popen(command, env=env, stdout=server_log, stderr=subprocess.STDOUT)
    try:
        with httpx.Client(base_url=url, trust_env=False, timeout=90) as client:
            deadline = time.monotonic() + 60
            while True:
                try:
                    if client.get("/csrf-token-for-tests").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                assert server.poll() is None, (tmp_path / "gunicorn.log").read_text()
                assert time.monotonic() < deadline, "gunicorn did not start"
                time.sleep(0.3)
            assert client.get("/login-for-tests").status_code == 200
            token = client.get("/csrf-token-for-tests").json()["csrf_token"]
            started = time.monotonic()
            response = client.post(
                "/sudarshan/autologin/test",
                headers={"X-CSRFToken": token, "Accept": "application/json"},
            )
            took = time.monotonic() - started
            assert response.status_code == 200, (response.text, child_log.read_text())
            assert response.json()["data"]["outcome"] == "ok"
            assert took < 20  # the child's hand-off was answered at once, not after a timeout
            status = client.get("/sudarshan/autologin/status").json()["data"]
            assert status["connected_today"] is True
            assert status["last_run"]["outcome"] == "ok"
        assert len(upstox.requests) == 1
        # The stored token was checked with Upstox from inside the worker, while the request
        # that started the child was still waiting for it.
        assert upstox.checks == ["/v3/user/get-funds-and-margin"]
        logs = child_log.read_text() + (tmp_path / "gunicorn.log").read_text()
        for secret in (FAKE_TOTP_SECRET, FAKE_PIN, OLD_TOKEN):
            assert secret not in logs
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
        server_log.close()
        upstox.stop()
