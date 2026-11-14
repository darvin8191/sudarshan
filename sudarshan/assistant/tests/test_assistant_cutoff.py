"""Ending the assistant's access at once (M17.9): ``cutoff.py`` and the column it keeps.

The real transport's side (a token that worked is refused, an open stream closes) is in
test_assistant_locks.py, on the real app. The kill switch's step is in the guard's own tests.
"""

from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy.exc import OperationalError

from sudarshan.assistant import cutoff
from sudarshan.tests.fdwatch import FdWatch


class Refused(Exception):
    pass


def test_nothing_is_refused_until_access_is_first_ended(risk_store):
    assert cutoff.valid_after_ms() is None
    assert not cutoff.refuses({"iat": 1})
    assert not cutoff.refuses({})  # upstream's own check has already passed it


def test_a_token_from_before_the_cutoff_is_refused_and_a_later_one_is_not(risk_store):
    moment = cutoff.set_now(lambda: 1_000.5)
    assert moment == 1_000_500 == cutoff.valid_after_ms()
    assert cutoff.refuses({"iat": 900})
    assert cutoff.refuses({"iat": 1_000}), "issued in the cutoff's own second: refused"
    assert not cutoff.refuses({"iat": 1_001})
    for doubtful in ({}, {"iat": None}, {"iat": "1001"}, {"iat": True}, None):
        assert cutoff.refuses(doubtful), doubtful


def test_the_cutoff_never_moves_back(risk_store):
    assert cutoff.set_now(lambda: 2_000.0) == 2_000_000
    assert cutoff.set_now(lambda: 1_500.0) == 2_000_000, "a clock set back ends nothing early"
    assert cutoff.set_now(lambda: 2_500.0) == 2_500_000
    assert risk_store.mcp_valid_after() == 2_500_000


def test_a_thousand_calls_read_the_file_once_a_second(risk_store, monkeypatch):
    cutoff.set_now(lambda: 1_000.0)
    reads = []
    real = risk_store.mcp_valid_after
    monkeypatch.setattr(risk_store, "mcp_valid_after", lambda: reads.append(1) or real())
    started = time.monotonic()
    for _ in range(1000):
        assert cutoff.refuses({"iat": 999})
    assert len(reads) <= 1 + int((time.monotonic() - started) / cutoff.CACHE_TTL_S)
    time.sleep(cutoff.CACHE_TTL_S + 0.05)
    before = len(reads)
    assert cutoff.refuses({"iat": 999}) and len(reads) == before + 1, "and it is read again"


def test_a_cutoff_that_cannot_be_read_refuses_and_is_asked_again(risk_store, monkeypatch):
    broken = [True]

    def read():
        if broken[0]:
            raise OperationalError("SELECT", {}, Exception("disk I/O error"))
        return None

    monkeypatch.setattr(risk_store, "mcp_valid_after", read)
    assert cutoff.refuses({"iat": 10**10}), "a doubt refuses, whenever the token was issued"
    broken[0] = False
    assert not cutoff.refuses({"iat": 1}), "the failure was not kept: the next call reads again"


def test_the_writer_drops_the_cached_moment_after_its_commit(risk_store):
    assert cutoff.valid_after_ms() is None  # cached
    cutoff.set_now(lambda: 3_000.0)
    assert cutoff.refuses({"iat": 2_999}), "no second of the old answer"


def test_checked_refuses_as_an_expired_token_is_refused(risk_store):
    verify = cutoff.checked(lambda token: {"iat": int(token)}, Refused)
    assert verify("5") == {"iat": 5}
    cutoff.set_now(lambda: 10.0)
    with pytest.raises(Refused) as refusal:
        verify("5")
    assert str(refusal.value) == "invalid_token"
    assert verify("11") == {"iat": 11}

    def upstream_refuses(token):
        raise Refused("invalid_request")

    with pytest.raises(Refused, match="invalid_request"):
        cutoff.checked(upstream_refuses, Refused)("x")


def test_ending_access_records_the_cutoff_and_revokes_the_sign_ins(risk_store, monkeypatch):
    from database import oauth_db

    asked = []
    monkeypatch.setattr(oauth_db, "revoke_all_tokens", lambda reason: asked.append(reason) or 3)
    before = cutoff.ended_count()
    assert cutoff.end_all_access("a_reason") == {"cutoff": True, "revoked": 3}
    assert asked == ["a_reason"] and cutoff.ended_count() != before
    assert risk_store.mcp_valid_after() is not None
    assert cutoff.end_all_access("again", revoke=False) == {"cutoff": True, "revoked": None}
    assert asked == ["a_reason"]


def test_each_half_is_tried_whatever_the_other_did(risk_store, monkeypatch):
    from database import oauth_db

    def broke(*args, **kwargs):
        raise RuntimeError("broke")

    monkeypatch.setattr(oauth_db, "revoke_all_tokens", broke)
    assert cutoff.end_all_access("r") == {"cutoff": True, "revoked": None}
    monkeypatch.setattr(oauth_db, "revoke_all_tokens", lambda reason: 2)
    monkeypatch.setattr(risk_store, "set_mcp_valid_after", broke)
    assert cutoff.end_all_access("r") == {"cutoff": False, "revoked": 2}


def test_where_remote_mcp_was_never_on_there_is_nothing_to_revoke(risk_store, monkeypatch):
    from database import oauth_db

    def no_table(reason):
        raise OperationalError("UPDATE", {}, Exception("no such table: oauth_refresh_tokens"))

    monkeypatch.setattr(oauth_db, "revoke_all_tokens", no_table)
    assert cutoff.end_all_access("r") == {"cutoff": True, "revoked": 0}


def test_ending_the_owners_sessions_ends_ai_access_too(risk_store, monkeypatch):
    """A logout, a password change or a reset: the consent's session has ended (P8 4.5)."""
    import database.auth_db as auth_db
    from database import oauth_db
    from utils import session as sessions

    monkeypatch.setattr(auth_db, "set_sessions_valid_after", lambda username, when: True)
    monkeypatch.setattr(auth_db, "clear_browser_sessions", lambda username: None)
    monkeypatch.setattr(auth_db, "clear_user_sessions", lambda username: None)
    reasons = []
    monkeypatch.setattr(oauth_db, "revoke_all_tokens", lambda reason: reasons.append(reason) or 1)
    for keep in (True, False):
        before = risk_store.mcp_valid_after() or 0
        assert sessions.end_every_session("owner", keep_server_logins=keep) is True
        assert risk_store.mcp_valid_after() >= before > -1
    assert reasons == ["owner_sessions_ended"] * 2 and risk_store.mcp_valid_after()
    # and a cutoff that cannot be written never breaks the logout itself
    monkeypatch.setattr(risk_store, "set_mcp_valid_after", lambda moment: 1 / 0)
    assert sessions.end_every_session("owner") is True


def test_two_enders_at_once_both_count_and_the_later_moment_stays(risk_store):
    start = threading.Barrier(2)
    before = cutoff.ended_count()

    def end(at):
        start.wait(timeout=10)
        cutoff.set_now(lambda: at)

    threads = [threading.Thread(target=end, args=(at,)) for at in (5_000.0, 6_000.0)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert risk_store.mcp_valid_after() == 6_000_000
    assert cutoff.ended_count() >= before + 2
    assert cutoff.refuses({"iat": 5_999})


def test_reads_and_writes_leave_no_descriptor_open(risk_store):
    cutoff.set_now(lambda: 1.0)  # the engine and the file exist before the window
    watch = FdWatch()
    for n in range(200):
        cutoff.set_now(lambda n=n: 10.0 + n)
        cutoff._cache.clear()
        assert cutoff.refuses({"iat": 1})
    watch.assert_clean()
