"""Fixtures for the tests of SUDARSHAN's edits to OpenAlgo's book mappings (M6.9).

There is no `sudarshan-books` service: the phone's book cards are in the frontend
(`frontend/src/sudarshan/books/cards/`), and the code under test here is OpenAlgo's Upstox book
mapping (`broker/upstox/mapping/order_data.py`), where the SUDARSHAN block is marked. The tests
live here because SUDARSHAN CI runs only `sudarshan/`.

OpenAlgo's modules read their settings when they are imported, so the environment is made safe
here, before any of them can be, and python-dotenv is disabled so an operator's .env is never
read into a test (the same guards as the proxy tests). No test here opens a database, a port or a
broker connection.
"""

from __future__ import annotations

import os
import tempfile

import dotenv

_TMP = tempfile.mkdtemp(prefix="sudarshan-books-tests-")
_TEMP_ROOT = tempfile.gettempdir()


def _isolate(name: str, value: str, isolated_prefix: str) -> None:
    """Point `name` at a throwaway location unless another test package already has."""
    if not os.environ.get(name, "").startswith(isolated_prefix):
        os.environ[name] = value


os.environ.setdefault("API_KEY_PEPPER", "d" * 64)  # fake, test only
os.environ.setdefault("APP_KEY", "test-only-app-key-" + "2" * 32)
for _name in ("DATABASE_URL", "SANDBOX_DATABASE_URL", "LOGS_DATABASE_URL", "LATENCY_DATABASE_URL"):
    _isolate(_name, f"sqlite:///{_TMP}/{_name.lower()}.db", f"sqlite:///{_TEMP_ROOT}")
_isolate("LOG_DIR", f"{_TMP}/log", _TEMP_ROOT)

dotenv.load_dotenv = lambda *args, **kwargs: False
dotenv.main.load_dotenv = dotenv.load_dotenv
