"""Fixtures for the options tests: fake keys and throw-away settings for the bridge's app.

OpenAlgo's modules read their settings when they are imported, so the environment is set here
before any of them can be, with setdefault (the other suites' conftests set the same names).
python-dotenv is disabled so an operator's .env is never read into a test. Every value is a fake.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

import dotenv

_TMP = tempfile.mkdtemp(prefix="sudarshan-options-tests-")
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)
os.environ.setdefault("API_KEY_PEPPER", "f" * 64)  # fake, test only
os.environ.setdefault("APP_KEY", "test-only-app-key-" + "0" * 32)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP}/openalgo-unused.db")
os.environ.setdefault("LOG_DIR", f"{_TMP}/log")

dotenv.load_dotenv = lambda *args, **kwargs: False
dotenv.main.load_dotenv = dotenv.load_dotenv
