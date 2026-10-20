"""Why the worker hands the token to OpenAlgo instead of writing ``openalgo.db`` itself.

The running gunicorn worker caches broker tokens in memory (``database/auth_db.py``):
``auth_cache`` keeps the token per API key until the next session expiry, and it also keeps a
"no session" answer. ``upsert_auth`` clears those caches only in the process that calls it, and
nothing tells gunicorn about a write made elsewhere (the ZeroMQ invalidation reaches only the
WebSocket proxy). These tests reproduce both stale cases with a write from outside, then show
the in-process apply the worker's hand-off uses serving the new token at once.
"""

from __future__ import annotations

import sqlite3

import pytest
from autologin_app import (
    FAKE_OPENALGO_API_KEY,
    OWNER,
    add_owner,
    reset_openalgo_db,
    stub_outside_world,
    use_app_state,
)
from autologin_fakes import FAKE_TOKEN, OLD_TOKEN

import blueprints.sudarshan_autologin as bp_module
from database import auth_db


def _db_file() -> str:
    return auth_db.engine.url.database


def _write_from_another_process(token: str) -> None:
    """What a worker writing openalgo.db directly would do: new row, no cache clearing here."""
    with sqlite3.connect(_db_file()) as conn:
        conn.execute(
            "UPDATE auth SET auth = ?, broker = 'upstox', is_revoked = 0 WHERE name = ?",
            (auth_db.encrypt_token(token), OWNER),
        )


@pytest.fixture
def seeded(monkeypatch, vault):
    calls = stub_outside_world(monkeypatch)
    reset_openalgo_db()
    add_owner()
    auth_db.upsert_api_key(OWNER, FAKE_OPENALGO_API_KEY)
    use_app_state(monkeypatch, vault=vault)
    yield calls
    auth_db.db_session.remove()


def test_a_cached_token_survives_a_write_from_another_process(seeded):
    auth_db.upsert_auth(OWNER, OLD_TOKEN, "upstox")  # yesterday's login
    assert auth_db.get_auth_token_broker(FAKE_OPENALGO_API_KEY) == (OLD_TOKEN, "upstox")

    _write_from_another_process(FAKE_TOKEN)
    # The hazard: this process keeps serving the dead token to the API, the bot, the strategies.
    assert auth_db.get_auth_token_broker(FAKE_OPENALGO_API_KEY) == (OLD_TOKEN, "upstox")

    bp_module._apply_token(OWNER, FAKE_TOKEN)  # what the hand-off runs, in this process
    assert auth_db.get_auth_token_broker(FAKE_OPENALGO_API_KEY) == (FAKE_TOKEN, "upstox")
    assert auth_db.get_auth_token(OWNER) == FAKE_TOKEN


def test_a_cached_no_session_answer_survives_a_write_from_another_process(seeded):
    auth_db.upsert_auth(OWNER, OLD_TOKEN, "upstox")
    auth_db.upsert_auth(OWNER, "", "", revoke=True)  # logged out, or the 03:00 sweep ran
    assert auth_db.get_auth_token_broker(FAKE_OPENALGO_API_KEY) == (None, None)  # now cached

    _write_from_another_process(FAKE_TOKEN)
    assert auth_db.get_auth_token_broker(FAKE_OPENALGO_API_KEY) == (None, None)  # still "none"

    bp_module._apply_token(OWNER, FAKE_TOKEN)
    assert auth_db.get_auth_token_broker(FAKE_OPENALGO_API_KEY) == (FAKE_TOKEN, "upstox")


def test_the_in_process_apply_restarts_the_order_feed_here(seeded):
    auth_db.upsert_auth(OWNER, OLD_TOKEN, "upstox")
    seeded.clear()
    bp_module._apply_token(OWNER, FAKE_TOKEN)
    # The order-update adapter lives in the gunicorn process; a write elsewhere would restart
    # nothing here (or start a second broker socket in the writer's own process).
    assert ("start_order_adapter", (OWNER, "upstox"), {}) in seeded
    assert ("cleanup_pools", (OWNER,), {"broker_name": "upstox"}) in seeded
