"""Fixtures for the tests of SUDARSHAN's edits to OpenAlgo's live-data path.

There is no `sudarshan-proxy` service. The code under test is OpenAlgo's WebSocket proxy
(`websocket_proxy/`) and its Upstox stream adapter (`broker/upstox/streaming/`), where every
SUDARSHAN block is marked `SUDARSHAN:`. The tests live here because SUDARSHAN CI runs only
`sudarshan/`.

OpenAlgo's modules read their settings when they are imported, so the environment is made safe
here, before any of them can be, and python-dotenv is disabled so an operator's .env is never
read into a test (the same guards as `test/conftest.py` and the auto-login tests). No test here
opens a database, a port, a ZeroMQ socket or a broker connection.
"""

from __future__ import annotations

import os
import tempfile

import dotenv

_TMP = tempfile.mkdtemp(prefix="sudarshan-proxy-tests-")
_TEMP_ROOT = tempfile.gettempdir()


def _isolate(name: str, value: str, isolated_prefix: str) -> None:
    """Point `name` at a throwaway location unless another test package already has.

    Assigned, never defaulted, so a production value exported in the shell cannot win. A value
    already inside the temporary directory came from another SUDARSHAN conftest that collected
    first; its tests (and the processes they start) rely on it, so it is kept.
    """
    if not os.environ.get(name, "").startswith(isolated_prefix):
        os.environ[name] = value


os.environ.setdefault("API_KEY_PEPPER", "e" * 64)  # fake, test only
os.environ.setdefault("APP_KEY", "test-only-app-key-" + "1" * 32)
for _name in ("DATABASE_URL", "SANDBOX_DATABASE_URL", "LOGS_DATABASE_URL", "LATENCY_DATABASE_URL"):
    _isolate(_name, f"sqlite:///{_TMP}/{_name.lower()}.db", f"sqlite:///{_TEMP_ROOT}")
_isolate("LOG_DIR", f"{_TMP}/log", _TEMP_ROOT)

dotenv.load_dotenv = lambda *args, **kwargs: False
dotenv.main.load_dotenv = dotenv.load_dotenv
