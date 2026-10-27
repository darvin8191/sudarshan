"""Fixtures for the tests of SUDARSHAN's edits to OpenAlgo for the phone books (M6.9).

There is no `sudarshan-books` service: the phone's book cards are in the frontend
(`frontend/src/sudarshan/books/cards/`), and the code under test here is OpenAlgo's, where the
SUDARSHAN blocks are marked: the Upstox book mapping and order stream, and what the close and
cancel routes answer. The tests live here because SUDARSHAN CI runs only `sudarshan/`.

OpenAlgo's modules read their settings when they are imported, so the environment is made safe
here, before any of them can be, and python-dotenv is disabled so an operator's .env is never
read into a test (the same guards as the proxy tests). No test here opens a port or a broker
connection; the route tests (test_books_exit_answers.py) record their latency in a throwaway
database under the temporary folder above.
"""

from __future__ import annotations

import os
import tempfile

import dotenv
import pytest

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


@pytest.fixture(autouse=True)
def _no_exit_claimed_or_placed_lately(monkeypatch):
    """Each test starts with no exit claimed and none placed lately (sudarshan/books/exit_guard.py).

    An exit one test placed would otherwise count as placed just now in the next, whose Exit all
    would then send nothing for that position.
    """
    from sudarshan.books import exit_guard

    monkeypatch.setattr(exit_guard, "EXITS", exit_guard.ExitsInFlight())


@pytest.fixture(autouse=True)
def _an_order_book_with_nothing_working(monkeypatch):
    """Upstox's Exit all reads the order book once, for orders still working to close a position.

    Unless a test says otherwise, it is read and lists none; the simulated Upstox of
    books_upstox_world.py serves a real one.
    """
    import broker.upstox.api.order_api as upstox

    monkeypatch.setattr(upstox, "get_order_book", lambda auth: {"status": "success", "data": []})
