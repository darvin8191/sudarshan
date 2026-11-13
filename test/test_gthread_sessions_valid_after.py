"""A logout of every device cannot be undone by a check that read the old answer.

SUDARSHAN review HIGH-2. The server keeps, per user, the moment every session was last ended at
once (``database.auth_db.SessionCutoff``), and every signed-in request compares its cookie's login
time with it through one LockedTTLCache that every request thread shares, so the quiet path reads
no database. Under eventlet a read, its query and its store never yielded; under gthread a logout
can commit between them, and a reader that then stored the answer it read before the commit would
let every ended device back in until the entry expired.

Each interleaving is staged deterministically or released together from a barrier. The code before
the change kept no such record, so every test here fails on it.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta

import pytest
import pytz
from flask import Flask, session

import database.auth_db as auth_db
from utils import session as session_utils

IST = pytz.timezone("Asia/Kolkata")


@pytest.fixture
def user():
    auth_db.init_db()
    name = f"gt-sva-{uuid.uuid4().hex[:10]}"
    auth_db.session_cutoff_cache.invalidate()
    yield name
    auth_db.SessionCutoff.query.filter_by(username=name).delete()
    auth_db.ActiveSession.query.filter_by(username=name).delete()
    auth_db.db_session.commit()
    auth_db.db_session.remove()
    auth_db.session_cutoff_cache.invalidate()


@pytest.fixture
def app():
    app = Flask(__name__)
    app.secret_key = "test-secret"
    return app


def ended(app, user: str, login_time: datetime) -> bool:
    """session_ended_elsewhere() for a cookie of ``user`` that logged in at ``login_time``, inside
    a request of its own whose database session is removed at the end, as app.py's teardown does."""
    with app.test_request_context("/"):
        session.update(user=user, logged_in=True, login_time=login_time.isoformat())
        try:
            return session_utils.session_ended_elsewhere()
        finally:
            auth_db.db_session.remove()


def test_a_check_whose_read_overlapped_a_logout_cannot_store_the_old_answer(app, user, monkeypatch):
    stale_login = datetime.now(IST) - timedelta(minutes=5)
    real = auth_db._load_sessions_valid_after
    reader: list[int] = []
    read_done, release = threading.Event(), threading.Event()

    def staged(username):
        value = real(username)  # no logout on record yet
        if threading.get_ident() in reader:
            read_done.set()
            assert release.wait(10), "the test never released the reader"
        return value

    monkeypatch.setattr(auth_db, "_load_sessions_valid_after", staged)
    result = {}

    def check():
        reader.append(threading.get_ident())
        result["ended"] = ended(app, user, stale_login)

    thread = threading.Thread(target=check)
    thread.start()
    assert read_done.wait(10), "the reader never reached its query"
    session_utils.end_every_session(user)  # commits, then drops the cached answer
    release.set()
    thread.join(10)

    assert result["ended"] is False  # it asked before the logout had committed
    # Had it stored what it read, the ended session would stay valid until the entry expired.
    assert ended(app, user, stale_login) is True


def test_no_check_that_starts_after_a_logout_finds_the_ended_session_valid(app, user):
    stale_login = datetime.now(IST) - timedelta(minutes=5)
    workers = 8
    barrier = threading.Barrier(workers + 1)
    done = threading.Event()
    late: list[bool] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def keep_checking():
        try:
            barrier.wait(10)
            seen_after = 0
            while seen_after < 25:
                after = done.is_set()  # read before the check starts
                answer = ended(app, user, stale_login)
                if after:
                    seen_after += 1
                    with lock:
                        late.append(answer)
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=keep_checking) for _ in range(workers)]
    for thread in threads:
        thread.start()
    barrier.wait(10)
    time.sleep(0.05)  # every thread has read "no logout" and the cache holds it
    session_utils.end_every_session(user)
    done.set()
    for thread in threads:
        thread.join(30)

    assert errors == []
    assert len(late) == workers * 25 and all(late)


def test_the_quiet_path_reads_the_database_once_not_on_every_request(app, user, monkeypatch):
    """Every signed-in request asks. A session no logout has ended is answered from the cache, so
    the default worker's quiet path adds no database read per request."""
    login = datetime.now(IST) - timedelta(minutes=5)
    real = auth_db._load_sessions_valid_after
    loads: list[str] = []
    monkeypatch.setattr(auth_db, "_load_sessions_valid_after",
                        lambda username: loads.append(username) or real(username))  # fmt: skip
    assert [ended(app, user, login) for _ in range(50)] == [False] * 50
    assert loads == [user]
