"""Run one "Test now" attempt as a child process of OpenAlgo and wait for it to finish.

Why a child process rather than code in the request: it is the very program the timer starts at
08:35 (same .env reading, same vault, same hand-off back into OpenAlgo), so a passing test means
tomorrow morning's run works, and no login code runs inside the eventlet worker.

Why waiting in the request is safe:
  * eventlet (the production default): ``eventlet.monkey_patch()`` makes ``subprocess.Popen`` green,
    so ``wait()`` polls and yields instead of blocking in C. The hub keeps serving every other
    request, including the child's own sealed hand-off calls into this same worker. No thread, no
    lock and no asyncio loop is created. ``test_autologin_eventlet.py`` proves it under a real hub.
  * gthread and the dev server: the wait blocks one request thread of many.

File-descriptor hygiene: the child writes to an append-only log file (never a PIPE); the parent's
copy of that descriptor is closed as soon as the child has started; the child is always reaped
here, and terminated then killed if it overruns.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
#: The worker's own budget is about 40 s (5 s connect + 15 s read to Upstox, 20 s hand-off).
TEST_TIMEOUT_S = 50.0
KILL_GRACE_S = 5.0
LOG_MAX_BYTES = 1_000_000


def test_log_path() -> Path:
    override = os.environ.get("SUDARSHAN_AUTOLOGIN_TEST_LOG")
    return Path(override) if override else REPO_ROOT / "log" / "sudarshan_autologin.log"


def _rotate(path: Path) -> None:
    with contextlib.suppress(OSError):
        if path.stat().st_size > LOG_MAX_BYTES:
            os.replace(path, path.with_name(path.name + ".1"))


def run_test_child(
    run_id: str,
    *,
    timeout_s: float = TEST_TIMEOUT_S,
    command: list[str] | None = None,
    cwd: Path | None = None,
    log_path: Path | None = None,
) -> int | None:
    """Start ``python -m sudarshan.autologin --test`` and wait for it.

    Returns:
        The child's exit status, or None when it overran ``timeout_s`` and was stopped.
    """
    log_path = log_path or test_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _rotate(log_path)
    argv = command or [sys.executable, "-m", "sudarshan.autologin", "--test", "--run-id", run_id]
    # The file object owns the descriptor from the moment it exists (mode 600 on creation), and
    # the parent's copy is closed as soon as the child holds its own.
    with open(log_path, "ab", buffering=0, opener=_private_opener) as log_handle:
        child = subprocess.Popen(
            argv,
            cwd=str(cwd or REPO_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            close_fds=True,
        )
    try:
        return child.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        child.terminate()  # SIGTERM: the worker unwinds and releases its lease
        try:
            child.wait(timeout=KILL_GRACE_S)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        return None
    except BaseException:
        # The request itself is being torn down (worker shutdown): never leave a child unreaped.
        child.kill()
        child.wait()
        raise


def _private_opener(path, flags):
    return os.open(path, flags, 0o600)
