""" "Test now" under a real gunicorn worker: the production shape, end to end.

The request waits for the worker child while the child calls back into the same single gunicorn
worker (preflight, then the sealed token hand-off). Under eventlet that only completes if the wait
yields to the hub; a blocking wait would leave the hand-off unanswered until the child gave up.
The same flow runs under gthread, OpenAlgo's opt-in worker.

The server runs in a process group of its own, and the teardown stops the whole group and waits
for it, whatever the test's outcome. Stopping only the arbiter left an eventlet worker of about
140 MB behind after every run: gunicorn 25's arbiter dies on SIGTERM under eventlet (its control
socket's thread join raises eventlet's Timeout, a BaseException, before the workers are told to
stop), and the eventlet worker, unlike the gthread one, never checks whether its parent is gone.
"""

from __future__ import annotations

import _thread
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from contextlib import nullcontext
from pathlib import Path

import httpx
import pytest
from autologin_fakes import FAKE_PIN, FAKE_TOTP_SECRET, OLD_TOKEN, FakeUpstox

from sudarshan.autologin.worker import SECRET_PIN, SECRET_TOTP
from sudarshan.vault import Vault

TESTS_DIR = Path(__file__).resolve().parent
REPO = TESTS_DIR.parents[2]

posix_only = pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass  # every member has exited already


def _has_exited(proc: subprocess.Popen) -> bool:
    """True once the leader has exited. It is not reaped, so its pid (the group id) stays taken."""
    try:
        return os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    except ChildProcessError:
        return True  # reaped already


def _process_state(pid: int) -> tuple[bytes, int] | None:
    """(state, process group) of a running ``pid``, from /proc; None once it has exited."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as stat_file:
            fields = stat_file.read().rsplit(b")", 1)[1].split()  # the name may hold spaces
    except (OSError, IndexError):
        return None
    return None if fields[0] in (b"Z", b"X") else (fields[0], int(fields[2]))


def _running_members(pgid: int) -> list[int]:
    """Members of process group ``pgid`` still running (an exited one awaiting its reaper is not).

    Read from /proc (Linux, as CI); elsewhere the list is empty and only the leader is waited for.
    """
    pids = [int(e) for e in os.listdir("/proc") if e.isdigit()] if os.path.isdir("/proc") else []
    return [pid for pid in pids if (found := _process_state(pid)) and found[1] == pgid]


def _stop_process_group(proc: subprocess.Popen, grace_s: float = 5.0, settle_s: float = 15.0):
    """Stop ``proc`` and every process in its group, then wait until none of them runs.

    ``proc`` was started with ``start_new_session=True``, so its pid is the group id. SIGTERM goes
    to the whole group at once, as systemd stops a unit, so every process gets a clean exit; SIGKILL
    then follows for whatever is left. That is in a ``finally``, so it still runs when the grace wait
    is cut short (a test timeout raises here). The leader is reaped only after the SIGKILL, so the
    group id cannot have passed to an unrelated process by then.

    The wait for the other members is in the ``finally`` too. ``killpg`` returns before the kernel
    has finished them: on a loaded machine a member was still running (state R) right after the
    SIGKILL, and a cut-short grace wait used to skip straight past it.
    """
    pgid = proc.pid
    left: list[int] = []
    try:
        _signal_group(pgid, signal.SIGTERM)
        deadline = time.monotonic() + grace_s
        while not _has_exited(proc) and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        _signal_group(pgid, signal.SIGKILL)
        proc.wait(timeout=settle_s)
        deadline = time.monotonic() + settle_s
        while (left := _running_members(pgid)) and time.monotonic() < deadline:
            time.sleep(0.05)
    assert not left, f"processes {left} of the test server outlived SIGKILL"


_ARBITER = r"""
import os, signal, sys, time
child = os.fork()
if child == 0:  # a worker that ignores SIGTERM and never looks at its parent
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        time.sleep(1)
with open(sys.argv[1] + ".part", "w") as f:
    f.write(f"{child}\n")
os.rename(sys.argv[1] + ".part", sys.argv[1])  # never read half written
if sys.argv[2] == "stuck":  # an arbiter that does not stop on SIGTERM either
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
while True:
    time.sleep(1)
"""


@posix_only
@pytest.mark.skipif(not os.path.isdir("/proc"), reason="reads /proc")
@pytest.mark.parametrize(
    ("arbiter", "outcome"),
    [("dies-on-term", "passed"), ("stuck", "passed"), ("dies-on-term", "failed"), ("stuck", "timed-out")],
)  # fmt: skip
def test_the_teardown_leaves_no_process_behind(tmp_path, arbiter, outcome):
    """The shape of the leak, a leader that goes and a child that stays: nothing may survive."""
    pid_file = tmp_path / "worker.pid"
    proc = subprocess.Popen(
        [sys.executable, "-c", _ARBITER, str(pid_file), arbiter], start_new_session=True
    )
    started, workers = time.monotonic(), []
    with pytest.raises((RuntimeError, KeyboardInterrupt)) if outcome != "passed" else nullcontext():
        try:
            while not pid_file.exists():
                assert proc.poll() is None and time.monotonic() < started + 20, "no fake server"
                time.sleep(0.02)
            workers.append(int(pid_file.read_text()))
            assert _process_state(workers[0]) is not None
            if outcome == "failed":
                raise RuntimeError("the test body failed")
            if outcome == "timed-out":  # as a test timeout does: an exception inside the grace wait
                threading.Timer(0.5, _thread.interrupt_main).start()
        finally:
            _stop_process_group(proc, grace_s=30.0 if outcome == "timed-out" else 1.0)
    assert time.monotonic() - started < 20  # a timed-out wait was cut short, not sat out
    assert _process_state(workers[0]) is None
    assert _running_members(proc.pid) == []


@posix_only
def test_a_cut_short_stop_still_waits_for_the_group_to_go(monkeypatch):
    """SIGKILL lands asynchronously, so even an interrupted stop waits until no member runs.

    A member that takes a moment to die is simulated: /proc reports it running for two more reads
    after the kill. The interrupt arrives in the grace wait, as a test timeout does.
    """
    this_module = sys.modules[__name__]
    reads: list[int] = []

    def slow_to_die(pgid: int) -> list[int]:
        reads.append(pgid)
        return [pgid + 1] if len(reads) < 3 else []

    def interrupted(proc: subprocess.Popen) -> bool:
        raise KeyboardInterrupt

    monkeypatch.setattr(this_module, "_running_members", slow_to_die)
    monkeypatch.setattr(this_module, "_has_exited", interrupted)
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    with pytest.raises(KeyboardInterrupt):
        _stop_process_group(proc, grace_s=30.0, settle_s=5.0)
    assert proc.returncode is not None  # the leader was stopped and reaped
    assert reads == [proc.pid] * 3  # read until the group was empty, not skipped


@pytest.mark.parametrize("worker_class", ["eventlet", "gthread"])
def test_test_now_round_trip_under_gunicorn(
    tmp_path, pepper, openalgo_env_file, holidays_file, worker_class
):
    pytest.importorskip("gunicorn", reason="gunicorn is installed by the production installer")
    if worker_class == "eventlet":
        pytest.importorskip("eventlet", reason="eventlet is installed by the production installer")
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    vault_path = tmp_path / "db" / "sudarshan_vault.db"
    vault = Vault(vault_path, pepper=pepper)
    vault.set_secret(SECRET_TOTP, FAKE_TOTP_SECRET)
    vault.set_secret(SECRET_PIN, FAKE_PIN)
    child_log = tmp_path / "log" / "sudarshan_autologin.log"
    command = [
        sys.executable, "-m", "gunicorn",
        "--worker-class", worker_class, "-w", "1",
        *(["--threads", "8"] if worker_class == "gthread" else []),
        "--bind", f"127.0.0.1:{port}", "--timeout", "120",
        "--chdir", str(REPO), "autologin_gunicorn_app:app",
    ]  # fmt: skip
    upstox = FakeUpstox().start()
    server = None
    try:
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
            # gunicorn 25.1+ opens a control socket by default; this keeps it in the test's own
            # folder, not ~/.gunicorn, which every server run by the same user would share.
            "XDG_RUNTIME_DIR": str(tmp_path),
        }
        # A process group of its own, so the teardown reaches the worker and anything it starts.
        # The child holds its own copy of the log descriptor; the parent's closes right away.
        with (tmp_path / "gunicorn.log").open("wb") as server_log:
            server = subprocess.Popen(
                command,
                env=env,
                stdout=server_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        with httpx.Client(base_url=url, trust_env=False, timeout=90) as client:
            deadline = time.monotonic() + 60
            while True:
                try:
                    if client.get("/csrf-token-for-tests").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                # Not poll(): reaping the arbiter here would free its pid, the group's id.
                assert not _has_exited(server), (tmp_path / "gunicorn.log").read_text()
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
        try:
            if server is not None:
                _stop_process_group(server)
        finally:
            upstox.stop()
