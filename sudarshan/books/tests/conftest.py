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


@pytest.fixture(autouse=True)
def _books_that_show_the_asked_quantity_still_held(monkeypatch):
    """A scalping or strategy close is sized from Upstox's positions and order book
    (sudarshan/books/surface_exits.py, ``upstox_book``). Unless a test says otherwise, they show
    500 lots of 65 still held on the side the close closes, nothing else working, and the order
    book's statuses are whatever the test serves through ``surface_exits.upstox_statuses``; no
    sizing refusal is remembered from the test before. The tests of the sizing itself
    (test_books_surface_sizing.py) put the real reader back over a simulated Upstox.
    """
    from sudarshan.books import surface_exits

    def book(auth_token, broker, exchange, symbol, product, side):
        held = 32500 if str(side).upper() == "SELL" else -32500
        return surface_exits.Book(
            statuses=surface_exits.upstox_statuses(auth_token, broker), held=held
        )

    monkeypatch.setattr(surface_exits, "upstox_book", book)
    monkeypatch.setattr(surface_exits, "REFUSALS", surface_exits._Refusals())


@pytest.fixture(autouse=True)
def held_exit_messages(monkeypatch):
    """M6.9 (O1): nothing remembered of a held strategy exit from the test before, and what
    would be told on Telegram lands in this list instead (sudarshan/books/held_exits.py)."""
    from sudarshan.books import held_exits

    told: list[str] = []
    held_exits.HOLDS.reset()
    monkeypatch.setattr(held_exits, "_tell", told.append)
    yield told
    held_exits.HOLDS.reset()
