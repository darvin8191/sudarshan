"""Fixtures for the TRINETRA view's tests: no network, fake keys, throw-away folders.

OpenAlgo's modules read their settings when they are imported, so the environment is set here
before any of them can be, with setdefault: the other suites' conftests set the same names, and
one pytest process must give every suite the same values. python-dotenv is disabled so an
operator's .env is never read into a test. Every value below is a fake, and every document a test
reads is invented (``vectors/``): nothing of TRINETRA's is in this repository.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

import dotenv

_TMP = tempfile.mkdtemp(prefix="sudarshan-signals-tests-")
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)
os.environ.setdefault("API_KEY_PEPPER", "f" * 64)  # fake, test only
os.environ.setdefault("APP_KEY", "test-only-app-key-" + "7" * 32)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP}/openalgo-unused.db")
os.environ.setdefault("LOG_DIR", f"{_TMP}/log")
os.environ.setdefault("LOG_TO_FILE", "False")

dotenv.load_dotenv = lambda *args, **kwargs: False
dotenv.main.load_dotenv = dotenv.load_dotenv

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    # httpx honours proxy variables; every fake the service talks to lives on 127.0.0.1.
    for name in [key for key in os.environ if key.lower().endswith("_proxy")]:
        monkeypatch.delenv(name, raising=False)
