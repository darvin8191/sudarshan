"""The option chain bridge (M11.1, O2): the checks, the states, the 5 s cache, the single flight.

A fake HTTP client stands in for Upstox throughout, and fails the test when it is called where it
must not be. The app is a minimal one with the real blueprint, the limiter and OpenAlgo's user
table (the owner check reads it). Every symbol, key and token is synthetic.
"""

from __future__ import annotations

import gc
import logging
import threading
import time
from datetime import datetime

import httpx
import pytest
import pytz
from flask import Flask
from options_master import V, world

import blueprints.sudarshan_options as bridge
from database import user_db
from limiter import limiter
from sudarshan.options import chainsnap, snapshot, words
from sudarshan.watchlists import master
from utils.thread_safe_cache import LockedTTLCache

PATH = "/sudarshan/options/api/chain"
NIFTY = "?exchange=NFO&underlying=NIFTY&expiry=2026-11-17"
OWNER, VIEWER = "owner", "family-viewer"
PASSWORD = "Fake-Pass-123!"  # pragma: allowlist secret
TOKEN = "tok-" + "A1b2" * 8  # synthetic, test only
TOKEN_2 = "tok-" + "Z9y8" * 8
STOPS_AT = 1_794_909_600_000
NOW = STOPS_AT - 3 * 86_400_000


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def monotonic(self):
        return self.t


class Reply:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeUpstox:
    """``replies`` are given in turn; with none left a call is a violation the test fails on."""

    def __init__(self):
        self.calls, self.replies, self.violations = [], [], []
        self.before_reply = None
        self.lock = threading.Lock()

    def get(self, url, **kw):
        with self.lock:
            self.calls.append((url, kw))
            nxt = self.replies.pop(0) if self.replies else None
        if nxt is None:
            self.violations.append(url)
            raise AssertionError("Upstox was called where it must not be")
        if self.before_reply:
            self.before_reply()
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def make_app(rate_limits=False) -> Flask:
    app = Flask("sudarshan-options-bridge-tests")
    app.secret_key = "test-only-secret-key-" + "3" * 32  # pragma: allowlist secret
    app.config.update(TESTING=True, RATELIMIT_ENABLED=rate_limits)
    limiter.init_app(app)
    if rate_limits:
        limiter.reset()
    app.register_blueprint(bridge.sudarshan_options_bp)

    @app.teardown_appcontext
    def remove_sessions(exception=None):
        user_db.db_session.remove()

    return app


def sign_in(client, username=OWNER, broker="upstox"):
    with client.session_transaction() as session:
        session.update(user=username, logged_in=True, broker=broker,
                       login_time=datetime.now(pytz.timezone("Asia/Kolkata")).isoformat())  # fmt: skip
    return client


@pytest.fixture(scope="module")
def users():
    """OpenAlgo's user table with the owner and one other sign-in (hashing a password is slow, so
    once for the file)."""
    user_db.Base.metadata.create_all(user_db.engine)
    user_db.User.query.delete()
    user_db.db_session.commit()
    user_db.add_user(OWNER, "owner@example.invalid", PASSWORD, is_admin=True)
    user_db.add_user(VIEWER, "viewer@example.invalid", PASSWORD, is_admin=False)
    user_db.db_session.remove()


@pytest.fixture
def upstox(monkeypatch, users):
    import broker.upstox.api.rate_limiter as plugin_limiter
    import utils.session as user_session

    _, _, snap = world()
    fake, clock, token = FakeUpstox(), Clock(), {"now": TOKEN}
    monkeypatch.setattr(user_session, "session_ended_elsewhere", lambda: False)
    monkeypatch.setattr(master, "openalgo_snapshot", lambda: snap)
    monkeypatch.setattr(chainsnap, "get_httpx_client", lambda: fake)
    monkeypatch.setattr(plugin_limiter, "apply_rate_limit", lambda category: None)
    monkeypatch.setattr(snapshot, "owner_token", lambda user: token["now"])
    monkeypatch.setattr(
        snapshot, "_stop_of", lambda name, exchange, days: dict.fromkeys(days, STOPS_AT)
    )
    monkeypatch.setattr(snapshot, "now_ms", lambda: NOW)
    monkeypatch.setattr(
        snapshot, "_fresh", LockedTTLCache(64, snapshot.CACHE_S, timer=clock.monotonic)
    )
    master.shared().drop()
    yield type("Up", (), {"fake": fake, "clock": clock, "token": token, "snap": snap})
    assert fake.violations == [], "Upstox was called where it must not be"
    master.shared().drop()
    gc.collect()


@pytest.fixture
def client(upstox):
    return sign_in(make_app().test_client())


def ok():
    return Reply(200, V["answer"])


def data(answer):
    assert answer.status_code == 200, answer.get_json()
    body = answer.get_json()
    assert body["status"] == "success"
    return body["data"]


# -- who may read ------------------------------------------------------------------------------
def test_no_session_and_a_family_phone_are_refused_before_anything_is_read(upstox):
    anonymous = make_app().test_client()
    refused = anonymous.get(PATH + NIFTY)
    assert refused.status_code == 401
    assert refused.get_json() == {"status": "error", "message": words.SESSION}
    # A family phone sends only its own cookie, which OpenAlgo does not read (P6 0.1).
    anonymous.set_cookie("__Host-sudarshan_family", "a-viewer-session")
    assert anonymous.get(PATH + NIFTY).status_code == 401
    assert upstox.fake.calls == []


def test_a_session_that_is_not_the_owner_s_is_refused(upstox):
    viewer = sign_in(make_app().test_client(), VIEWER)
    refused = viewer.get(PATH + NIFTY)
    assert refused.status_code == 403
    assert refused.get_json() == {"status": "error", "message": words.OWNER_ONLY}
    assert b"NIFTY17NOV26" not in refused.data and upstox.fake.calls == []


def test_the_route_takes_no_write(client):
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)(PATH + NIFTY).status_code == 405


# -- the states --------------------------------------------------------------------------------
def test_the_owner_reads_the_chain_as_openalgo_symbols(client, upstox):
    upstox.fake.replies.append(ok())
    answer = client.get(PATH + NIFTY)
    got = data(answer)
    assert answer.headers["Cache-Control"] == "no-store"
    assert got["state"] == "ok" and got["words"] is None
    chain = got["chain"]
    assert chain["strikes"] == V["expected"]["strikes"]
    assert chain["spot"] == V["expected"]["spot"]
    assert chain["pcr"] == pytest.approx(V["expected"]["pcr"])
    assert (chain["left_out"], chain["by_strike"]) == (2, 2)
    assert (chain["exchange"], chain["underlying"], chain["expiry"]) == (
        "NFO",
        "NIFTY",
        "2026-11-17",
    )
    assert chain["underlying_exchange"] == "NSE_INDEX" and chain["expiry_ts"] == STOPS_AT
    assert chain["as_of"] == NOW
    ((url, kw),) = upstox.fake.calls
    assert kw["params"] == {"instrument_key": "NSE_INDEX|Nifty 50", "expiry_date": "2026-11-17"}
    assert TOKEN not in answer.get_data(as_text=True)


def test_no_token_today_and_another_broker_are_signin_with_no_call(upstox):
    upstox.token["now"] = None
    got = data(sign_in(make_app().test_client()).get(PATH + NIFTY))
    assert got == {"state": "signin", "words": words.SIGNIN_TODAY, "chain": None}
    upstox.token["now"] = TOKEN
    got = data(sign_in(make_app().test_client(), broker="zerodha").get(PATH + NIFTY))
    assert got == {"state": "signin", "words": words.OTHER_BROKER, "chain": None}
    assert upstox.fake.calls == []


@pytest.mark.parametrize(
    ("reply", "state", "said"),
    [
        (Reply(401, {"status": "error"}), "signin", words.SIGNIN_TODAY),
        (Reply(429, {"status": "error"}), "wait", words.WAIT),
        (Reply(500, None), "unavailable", words.FAILED),
        (httpx.ReadTimeout("slow"), "unavailable", words.FAILED),
        (Reply(200, {"status": "success", "data": {"unexpected": 1}}), "unavailable", words.FAILED),
    ],
)
def test_each_outcome_has_its_state_and_sentence_and_is_never_retried(
    client, upstox, reply, state, said
):
    upstox.fake.replies.append(reply)
    assert data(client.get(PATH + NIFTY)) == {"state": state, "words": said, "chain": None}
    assert len(upstox.fake.calls) == 1


@pytest.mark.parametrize(
    ("query", "said"),
    [
        ("?exchange=MCX&underlying=CRUDEOIL&expiry=2026-11-17", words.NO_MCX),
        ("?exchange=NFO&underlying=NOSUCH&expiry=2026-11-17",
         words.not_listed("NOSUCH", "2026-11-17")),
        ("?exchange=NFO&underlying=NIFTY&expiry=2026-12-29",
         words.not_listed("NIFTY", "2026-12-29")),
    ],
)  # fmt: skip
def test_a_chain_that_is_not_offered_says_so_and_makes_no_call(client, upstox, query, said):
    assert data(client.get(PATH + query)) == {"state": "none", "words": said, "chain": None}
    assert upstox.fake.calls == []


def test_before_the_master_is_loaded_nothing_is_asked_of_upstox(client, upstox, monkeypatch):
    monkeypatch.setattr(master, "openalgo_snapshot", lambda: None)
    got = data(client.get(PATH + NIFTY))
    assert got == {"state": "unavailable", "words": words.NOT_LOADED, "chain": None}
    assert upstox.fake.calls == []


@pytest.mark.parametrize(
    "query",
    ["", "?exchange=NFO&underlying=NIFTY", "?exchange=NSE&underlying=NIFTY&expiry=2026-11-17",
     "?exchange=NFO&underlying=NIFTY&expiry=17NOV26", "?exchange=NFO&underlying=NI FTY&expiry=2026-11-17",
     "?exchange=NFO&underlying=" + "A" * 41 + "&expiry=2026-11-17",
     "?exchange=NFO&underlying=NIFTY&expiry=2026-13-40"],
)  # fmt: skip
def test_a_request_that_does_not_name_a_chain_is_refused_in_words(client, upstox, query):
    refused = client.get(PATH + query)
    assert refused.status_code == 400
    assert refused.get_json() == {"status": "error", "message": words.PICK_AGAIN}
    assert upstox.fake.calls == []


def test_an_answer_that_cannot_be_read_is_logged_once_by_its_key_names(client, upstox, caplog):
    snapshot._shapes_logged.clear()
    odd = {"status": "success", "data": {"rows": [1]}, "secret_figure": 4242}
    upstox.fake.replies += [Reply(200, odd), Reply(200, odd)]
    with caplog.at_level(logging.WARNING):
        client.get(PATH + NIFTY)
        upstox.clock.t += snapshot.CACHE_S + 1
        client.get(PATH + NIFTY)
    lines = [r.getMessage() for r in caplog.records if "shape" in r.getMessage()]
    assert len(lines) == 1 and "secret_figure" in lines[0]
    assert "4242" not in caplog.text and TOKEN not in caplog.text


def test_legs_matched_by_strike_are_noted_once_in_counts_only(client, upstox, caplog):
    snapshot._mapping_noted.clear()
    upstox.fake.replies += [ok(), ok()]
    with caplog.at_level(logging.WARNING):
        client.get(PATH + NIFTY)
        upstox.clock.t += snapshot.CACHE_S + 1
        client.get(PATH + NIFTY)
    notes = [r.getMessage() for r in caplog.records if "matched by strike" in r.getMessage()]
    assert len(notes) == 1 and "2 were matched" in notes[0] and "2 were left out" in notes[0]
    assert "NIFTY17NOV26" not in caplog.text and TOKEN not in caplog.text


def test_a_failure_inside_the_read_is_a_sentence_never_the_error_s_text(
    client, upstox, monkeypatch
):
    def broken(*args):
        raise RuntimeError("calendar table locked")

    monkeypatch.setattr(snapshot, "_stop_of", broken)
    upstox.fake.replies.append(ok())
    answer = client.get(PATH + NIFTY)
    assert data(answer) == {"state": "unavailable", "words": words.FAILED, "chain": None}
    assert "locked" not in answer.get_data(as_text=True)


# -- the expiries ------------------------------------------------------------------------------
EXPIRIES = "/sudarshan/options/api/expiries"


def test_the_expiries_route_meets_the_same_checks(upstox):
    anonymous = make_app().test_client()
    assert anonymous.get(EXPIRIES + "?exchange=NFO&underlying=NIFTY").status_code == 401
    viewer = sign_in(make_app().test_client(), VIEWER)
    assert viewer.get(EXPIRIES + "?exchange=NFO&underlying=NIFTY").status_code == 403
    owner = sign_in(make_app().test_client())
    assert owner.get(EXPIRIES + "?exchange=NFO").status_code == 400
    assert owner.post(EXPIRIES + "?exchange=NFO&underlying=NIFTY").status_code == 405


def test_the_expiries_that_still_trade_with_the_spot_and_the_nearest_future(
    client, upstox, monkeypatch
):
    day = 86_400_000
    stops = {"2026-11-17": NOW - 1, "2026-11-24": NOW + 7 * day, "2026-12-29": NOW + 42 * day}
    monkeypatch.setattr(
        snapshot, "_stop_of", lambda name, exchange, days: {d: stops[d] for d in days}
    )
    upstox.token["now"] = None  # no Upstox sign-in is needed: the master and the calendar answer
    got = data(client.get(EXPIRIES + "?exchange=nfo&underlying=nifty"))
    assert got["state"] == "ok" and got["words"] is None
    assert got["listing"] == {
        "underlying": "NIFTY", "exchange": "NFO", "now": NOW,
        "spot": {"symbol": "NIFTY", "exchange": "NSE_INDEX"},
        "future": {"symbol": "NIFTY24NOV26FUT", "exchange": "NFO", "expiry": "2026-11-24"},
        "expiries": [{"date": "2026-11-24", "stops_at": NOW + 7 * day}],
    }  # fmt: skip
    stock = data(client.get(EXPIRIES + "?exchange=NFO&underlying=ACME"))["listing"]
    assert stock["spot"] == {"symbol": "ACME", "exchange": "NSE"} and stock["future"] is None
    assert upstox.fake.calls == []


@pytest.mark.parametrize(
    ("query", "said"),
    [
        ("?exchange=MCX&underlying=CRUDEOIL", words.NO_MCX),
        ("?exchange=NFO&underlying=NOSUCH", words.no_options("NOSUCH")),
        ("?exchange=BFO&underlying=NIFTY", words.no_options("NIFTY")),
    ],
)
def test_an_underlying_with_no_options_says_so(client, query, said):
    assert data(client.get(EXPIRIES + query)) == {"state": "none", "words": said, "listing": None}


def test_an_underlying_whose_every_expiry_has_stopped_has_none(client, monkeypatch):
    monkeypatch.setattr(snapshot, "_stop_of", lambda name, exchange, days: dict.fromkeys(days, NOW))
    got = data(client.get(EXPIRIES + "?exchange=BFO&underlying=SENSEX"))
    assert got == {"state": "none", "words": words.no_options("SENSEX"), "listing": None}


def test_the_expiries_say_in_words_when_they_cannot_be_read(client, monkeypatch):
    def broken(*args):
        raise RuntimeError("calendar table locked")

    monkeypatch.setattr(snapshot, "_stop_of", broken)
    answer = client.get(EXPIRIES + "?exchange=NFO&underlying=NIFTY")
    assert data(answer) == {"state": "unavailable", "words": words.EXPIRIES_FAILED, "listing": None}
    assert "locked" not in answer.get_data(as_text=True)


def test_a_contract_stops_by_the_regime_of_its_expiry_day(monkeypatch):
    from datetime import timedelta, timezone

    from sudarshan.watchlists import sessions

    ist = timezone(timedelta(hours=5, minutes=30))
    monkeypatch.setattr(sessions, "closes", lambda days, exchange: {})
    monkeypatch.setattr(sessions, "index_underlyings", lambda: frozenset({"NIFTY"}))
    at = lambda *t: int(datetime(*t, tzinfo=ist).timestamp() * 1000)  # noqa: E731
    got = snapshot._stop_of("NIFTY", "NFO", ["2026-11-17", "2026-12-08"])
    assert got == {"2026-11-17": at(2026, 11, 17, 15, 40), "2026-12-08": at(2026, 12, 8, 15, 30)}
    assert snapshot._stop_of("ACME", "NFO", ["2026-12-29"]) == {
        "2026-12-29": at(2026, 12, 29, 15, 45)
    }
    monkeypatch.setattr(sessions, "closes", lambda days, exchange: {"2026-11-17": 42})
    assert snapshot._stop_of("NIFTY", "NFO", ["2026-11-17"]) == {"2026-11-17": 42}


# -- the cache and the single flight -----------------------------------------------------------
def test_a_second_read_inside_five_seconds_costs_no_call_and_the_next_one_does(client, upstox):
    upstox.fake.replies += [ok(), ok()]
    first = data(client.get(PATH + NIFTY))
    upstox.clock.t += snapshot.CACHE_S - 1
    assert data(client.get(PATH + "?exchange=nfo&underlying=nifty&expiry=2026-11-17")) == first
    assert len(upstox.fake.calls) == 1
    upstox.clock.t += 2
    data(client.get(PATH + NIFTY))
    assert len(upstox.fake.calls) == 2


def test_a_refusal_is_not_asked_again_inside_the_five_seconds(client, upstox):
    upstox.fake.replies.append(Reply(429, {"status": "error"}))
    for _ in range(3):
        assert data(client.get(PATH + NIFTY))["state"] == "wait"
    assert len(upstox.fake.calls) == 1


def test_a_new_sign_in_is_never_served_the_old_token_s_answer(client, upstox):
    upstox.fake.replies += [Reply(401, {"status": "error"}), ok()]
    assert data(client.get(PATH + NIFTY))["state"] == "signin"
    upstox.token["now"] = TOKEN_2
    assert data(client.get(PATH + NIFTY))["state"] == "ok"
    assert upstox.fake.calls[1][1]["headers"]["Authorization"] == f"Bearer {TOKEN_2}"


def test_two_chains_are_two_reads(client, upstox):
    upstox.fake.replies += [ok(), Reply(200, {"status": "success", "data": []})]
    data(client.get(PATH + NIFTY))
    other = data(client.get(PATH + "?exchange=NFO&underlying=NIFTY&expiry=2026-11-24"))
    assert other["state"] == "ok" and other["chain"]["strikes"] == []
    assert len(upstox.fake.calls) == 2


def test_eight_callers_released_together_make_one_read(upstox):
    """The single flight under real threads: every caller gets the chain, Upstox is read once.
    Without the keyed lock each of the eight would find the cache empty and read."""
    app = make_app()
    upstox.fake.replies += [ok()] * 8
    upstox.fake.before_reply = lambda: time.sleep(0.2)
    barrier, results = threading.Barrier(8), [None] * 8

    def run(i):
        caller = sign_in(app.test_client())
        barrier.wait(30)
        results[i] = caller.get(PATH + NIFTY).get_json()

    threads = [threading.Thread(target=run, args=(i,), name=f"options-test-{i}") for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert all(r and r["data"]["state"] == "ok" for r in results)
    assert len({r["data"]["chain"]["as_of"] for r in results}) == 1
    assert len(upstox.fake.calls) == 1
    upstox.fake.replies.clear()


def test_the_control_without_the_flight_reads_more_than_once(upstox, monkeypatch):
    """The control for the test above: with the keyed lock taken away the same eight callers
    read Upstox more than once, so that test cannot pass vacuously."""
    from contextlib import contextmanager

    @contextmanager
    def no_hold(key):
        yield

    monkeypatch.setattr(snapshot._flight, "hold", no_hold)
    app = make_app()
    upstox.fake.replies += [ok()] * 8
    upstox.fake.before_reply = lambda: time.sleep(0.2)
    barrier = threading.Barrier(8)

    def run(i):
        caller = sign_in(app.test_client())
        barrier.wait(30)
        caller.get(PATH + NIFTY)

    threads = [threading.Thread(target=run, args=(i,), name=f"options-ctl-{i}") for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert len(upstox.fake.calls) > 1
    upstox.fake.replies.clear()


def test_the_limit_is_counted_per_owner_and_answers_in_words(upstox):
    app = make_app(rate_limits=True)
    owner = sign_in(app.test_client())
    upstox.fake.replies.append(ok())
    limit = int(bridge.LIMIT.split()[0])
    statuses = [owner.get(PATH + NIFTY).status_code for _ in range(limit + 1)]
    assert statuses[:limit] == [200] * limit and statuses[limit] == 429
    refused = owner.get(PATH + NIFTY)
    assert refused.get_json() == {"status": "error", "message": words.BUSY}
    limiter.reset()
    app.config["RATELIMIT_ENABLED"] = False


def test_the_package_starts_no_thread_timer_or_loop():
    """The condition of running inside OpenAlgo: nothing in the package or its bridge names a
    thread, a timer, a scheduler or an event loop."""
    from pathlib import Path

    root = Path(snapshot.__file__).parent
    sources = [*root.glob("*.py"), Path(bridge.__file__)]
    banned = ("threading.Thread", "Timer(", "asyncio", "apscheduler", "BackgroundScheduler",
              "ThreadPoolExecutor", "time.sleep", "TTLCache(")  # fmt: skip
    for path in sources:
        text = path.read_text(encoding="utf-8").replace("LockedTTLCache(", "")
        for word in banned:
            assert word not in text, f"{path.name} names {word}"
