"""The Smartlist read and the ``ranked`` op (M8.9, W22): sign-in states, cache, single flight.

A fake HTTP client stands in for Upstox throughout, and fails the test when it is called where it
must not be (no token, a cached answer, a pull inside the cache). Every answer, key and token here
is synthetic. The gthread barrier test is in ``test/test_gthread_watchlists.py``.
"""

from __future__ import annotations

import gc
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from presets_master import Row, Snap
from watchlists_bridge_app import csrf_headers, login_session, make_app, reset_users

import blueprints.sudarshan_watchlists as bp_module
from sudarshan.watchlists import (
    contract,
    engine,
    master,
    ops,
    ranked,
    sessions,
    smartlist,
    versions,
)
from sudarshan.watchlists.ops import ranked as op
from sudarshan.watchlists.words import ranked as words
from utils.thread_safe_cache import LockedTTLCache

API = contract.API_PREFIX
IST = timezone(timedelta(hours=5, minutes=30))
TOKEN = "tok-" + "A1b2" * 8  # synthetic, test only
TOKEN_2 = "tok-" + "Z9y8" * 8
NOON = datetime(2026, 11, 9, 10, 43, 10, tzinfo=IST)  # a Monday, the market open


class Clock:
    def __init__(self):
        self.t = NOON.timestamp()

    def monotonic(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds

    def ms(self):
        return int(self.t * 1000)


class Reply:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeClient:
    """Upstox. ``replies`` are given in turn; with none left, or ``forbid`` set, a call is a
    violation the test fails on (a call where none may be made)."""

    def __init__(self):
        self.calls, self.replies, self.forbid, self.violations = [], [], True, []
        self.before_reply = None

    def get(self, url, **kw):
        self.calls.append((url, kw))
        if self.forbid or not self.replies:
            self.violations.append(url)
            raise AssertionError("Upstox was called where it must not be")
        if self.before_reply:
            self.before_reply()
        nxt = self.replies.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def allow(self, *replies):
        self.forbid = False
        self.replies += list(replies)


def smart_row(n, key=None):
    return {"instrument_key": key or f"NSE_FO|{35000 + n}",
            "price": {"current": 100.0 + n, "close_price": 99.0, "change_abs": 1.0, "change_pct": 1.0},
            "metric": {"current": 1000 + n, "previous": 900, "change_abs": 100 + n,
                       "change_pct": 11.0 + n / 10}}  # fmt: skip


def answer(n=30, stamp="2026-11-09T10:42:05+05:30", extra=()):
    return {"status": "success", "data": {"time_stamp": stamp, "metric_key": "oi_change_pct",
                                          "data": [smart_row(i) for i in range(n)] + list(extra)}}  # fmt: skip


def ok(n=30, **kw):
    return Reply(200, answer(n, **kw))


def master_rows(n=60):
    rows = []
    for i in range(n):
        rows.append(Row(symbol=f"FUT{i}25NOVFUT", name=f"FUT{i}", exchange="NFO", instrumenttype="FUT",
                        underlying=f"FUT{i}", expiry="25-NOV-26", strike=None, lotsize=10,
                        tick_size=0.05, token=f"NSE_FO|{35000 + i}"))  # fmt: skip
    return rows


def snapshot():
    rows = master_rows()
    return Snap(by_exchange={"NFO": tuple(rows)},
                by_symbol_exchange={(r.symbol, r.exchange): r for r in rows},
                by_token={r.token: r for r in rows}, session_start=None)  # fmt: skip


class Box:
    snap = None

    def __call__(self):
        return self.snap


@pytest.fixture
def upstox(monkeypatch, tmp_path):
    from database import watchlist_db as oa

    monkeypatch.setenv(contract.DB_ENV, str(tmp_path / "wl.db"))
    monkeypatch.setenv(contract.SWITCH_ENV, "on")
    bp_module._record.clear()
    engine.dispose()
    versions.forget()
    oa.Base.metadata.create_all(oa.engine)
    oa.db_session.remove()
    clock = Clock()
    box = Box()
    box.snap = snapshot()
    fake = FakeClient()
    token = {"now": TOKEN}
    monkeypatch.setattr(master, "openalgo_snapshot", box)
    monkeypatch.setattr(versions, "emit_from_any_thread", lambda *a, **k: None)
    monkeypatch.setattr(smartlist, "get_httpx_client", lambda: fake)
    monkeypatch.setattr(op, "owner_token", lambda user: token["now"])
    monkeypatch.setattr(op, "stored_broker", lambda user: "upstox")
    monkeypatch.setattr(op, "now_ms", clock.ms)
    monkeypatch.setattr(sessions, "market_open", lambda exchange, now: True)
    for name, ttl in (("_fresh", op.CACHE_S), ("_shared", op.SHARE_S), ("_last", op.LAST_S)):
        monkeypatch.setattr(op, name, LockedTTLCache(64, ttl, timer=clock.monotonic))
    shared = master.shared()
    shared.drop()
    gc.collect()
    yield type("Up", (), {"fake": fake, "clock": clock, "token": token, "box": box})
    assert fake.violations == [], "Upstox was called where it must not be"
    box.snap = None
    shared.drop()
    gc.collect()
    engine.dispose()
    versions.forget()
    bp_module._record.clear()


def new_client(broker="upstox"):
    reset_users()
    c = make_app(ops.discover(), real=True).test_client()
    login_session(c)
    with c.session_transaction() as s:
        if broker:
            s["broker"] = broker
    c.headers = csrf_headers(c)
    return c


@pytest.fixture
def client(upstox):
    return new_client()


def get(client, cat="OI_GAINERS", src="futures", asset="INDEX", size=None):
    q = f"src={src}&asset={asset}&cat={cat}" + (f"&size={size}" if size else "")
    return client.get(f"{API}/ranked?{q}", headers=client.headers)


def data(r):
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]


def ms(dt):
    return int(dt.timestamp() * 1000)


# -- the states that make no call --------------------------------------------------------------
def test_no_token_today_is_signin_and_no_http_call_at_all(client, upstox):
    upstox.token["now"] = None
    d = data(get(client))
    assert d["state"] == "signin" and d["rows"] == [] and d["words"] == words.SIGNIN_TODAY
    assert d["made_at"] is None and d["read_at"] is None
    assert upstox.fake.calls == []


def test_another_broker_gets_its_sentence_and_no_call(upstox):
    d = data(get(new_client(broker="zerodha")))
    assert d["state"] == "signin" and d["words"] == words.OTHER_BROKER
    assert upstox.fake.calls == []


def test_a_session_without_a_broker_falls_back_to_the_stored_signin(upstox, monkeypatch):
    monkeypatch.setattr(op, "stored_broker", lambda user: "zerodha")
    d = data(get(new_client(broker=None)))
    assert d["words"] == words.OTHER_BROKER and upstox.fake.calls == []


def test_state_says_whether_ranked_lists_are_available_and_why_not(client, upstox):
    r = client.get(f"{API}/state", headers=client.headers)
    assert r.get_json()["data"]["ranked"] == {"ok": True, "message": None}
    upstox.token["now"] = None
    r = client.get(f"{API}/state", headers=client.headers)
    assert r.get_json()["data"]["ranked"] == {"ok": False, "message": words.SIGNIN_TODAY}
    assert upstox.fake.calls == []


def test_switched_off_there_is_no_ranked_list_and_no_call(client, upstox, monkeypatch):
    monkeypatch.setenv(contract.SWITCH_ENV, "off")
    for path in ("/ranked?src=futures&asset=INDEX&cat=OI_GAINERS", "/lists",
                 "/view?ref=ranked:futures:INDEX:OI_GAINERS"):  # fmt: skip
        r = client.get(API + path, headers=client.headers)
        assert r.status_code == 503 and r.get_json()["code"] == "off", path
    assert upstox.fake.calls == []


# -- the read ------------------------------------------------------------------------------------
def test_one_read_makes_rows_in_upstoxs_order_with_the_time_and_the_figure(client, upstox):
    upstox.fake.allow(ok())
    d = data(get(client))
    assert d["ref"] == "ranked:futures:INDEX:OI_GAINERS" and d["state"] == "ok"
    assert len(d["rows"]) == 20 and d["left_out"] == 0  # 20 by default
    assert d["rows"][0]["contract"]["key"] == "NFO:FUT0" + "25NOVFUT"
    assert d["rows"][0]["metric"] == "OI +11.0%" and d["rows"][0]["item"] is None
    assert d["made_at"] == ms(datetime(2026, 11, 9, 10, 42, 5, tzinfo=IST))
    assert d["read_at"] == upstox.clock.ms()
    assert d["words"] == "Ranked by Upstox · 1 min ago"
    assert set(d) == {f.name for f in contract.record("Ranked")}
    for row in d["rows"]:
        assert set(row) == {f.name for f in contract.record("Row")}
        assert set(row["contract"]) == {f.name for f in contract.record("Contract")}
    url, kw = upstox.fake.calls[0]
    assert url == "https://api.upstox.com/v2/market/smartlist/futures"
    assert kw["params"] == {"asset_type": "INDEX", "category": "OI_GAINERS", "page_number": 1,
                            "page_size": 50}  # fmt: skip
    assert kw["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert kw["timeout"].connect == 1.0 and kw["timeout"].read == 8.0


def test_fifty_rows_on_request_and_never_more(client, upstox):
    upstox.fake.allow(ok(55))
    assert len(data(get(client, size=50))["rows"]) == 50
    assert len(data(get(client, size=500))["rows"]) == 50
    assert len(upstox.fake.calls) == 1  # one read of the page serves 20 and 50


def test_the_cache_serves_a_second_device_and_a_second_size_without_a_call(client, upstox):
    upstox.fake.allow(ok())
    first = data(get(client))
    second_device = new_client()
    again = data(get(second_device, size=50))
    assert len(upstox.fake.calls) == 1
    assert again["read_at"] == first["read_at"] and again["made_at"] == first["made_at"]
    upstox.clock.advance(59)
    data(get(second_device))
    assert len(upstox.fake.calls) == 1
    upstox.clock.advance(2)  # past the minute
    upstox.fake.allow(ok())
    assert data(get(second_device))["read_at"] == upstox.clock.ms()
    assert len(upstox.fake.calls) == 2


def test_each_category_is_its_own_read(client, upstox):
    upstox.fake.allow(ok(), ok(), ok())
    data(get(client))
    data(get(client, cat="OI_LOSERS"))
    data(get(client, src="options", cat="IV_GAINERS"))
    assert [c[0].rsplit("/", 1)[1] for c in upstox.fake.calls] == ["futures", "futures", "options"]


def test_a_category_the_page_does_not_offer_is_refused_before_any_call(client, upstox):
    for q in ("src=futures&asset=COMMODITY&cat=PREMIUM", "src=futures&asset=INDEX&cat=IV_GAINERS",
              "src=mtf&asset=INDEX&cat=TOP_TRADED"):  # fmt: skip
        r = client.get(f"{API}/ranked?{q}", headers=client.headers)
        assert r.status_code == 400 and r.get_json()["code"] == "invalid", q
    assert upstox.fake.calls == []


def test_unknown_keys_are_counted_in_words_and_left_out(client, upstox):
    upstox.fake.allow(Reply(200, answer(3, extra=[smart_row(0, key="NSE_FO|99999"),
                                                  smart_row(0, key="NSE_FO|99998")])))  # fmt: skip
    d = data(get(client))
    assert len(d["rows"]) == 3 and d["left_out"] == 2
    assert d["words"].endswith(
        "2 contracts from Upstox are not in today's contract list and are left out.")  # fmt: skip


def test_the_master_not_loaded_is_failed_in_words_and_no_call(client, upstox):
    upstox.box.snap = None
    r = get(client)
    assert r.status_code == 500 and "contract list is not loaded" in r.get_json()["message"]
    assert upstox.fake.calls == []


# -- refusals ------------------------------------------------------------------------------------
def test_a_refusal_keeps_the_last_ranking_with_its_time(client, upstox):
    upstox.fake.allow(ok(), Reply(503, {"status": "error"}))
    good = data(get(client))
    upstox.clock.advance(61)
    d = data(get(client))
    assert d["state"] == "unavailable" and len(d["rows"]) == 20
    assert d["rows"] == good["rows"] and d["made_at"] == good["made_at"]
    assert d["read_at"] == good["read_at"]
    assert (
        d["words"] == "Upstox's ranking could not be read just now. Showing the ranking from 10:42."
    )


def test_an_upstox_that_does_not_answer_is_the_same_and_without_a_ranking_says_so(client, upstox):
    upstox.fake.allow(httpx.ConnectTimeout("slow"))
    d = data(get(client))
    assert d["state"] == "unavailable" and d["rows"] == [] and d["made_at"] is None
    assert d["words"] == "Upstox's ranking could not be read just now. Pull down to try again."


def test_a_failed_read_is_not_cached_beyond_a_moment(client, upstox):
    upstox.fake.allow(Reply(500), ok())
    assert data(get(client))["state"] == "unavailable"
    upstox.clock.advance(op.SHARE_S + 1)
    assert data(get(client))["state"] == "ok"  # the owner's pull tries again
    assert len(upstox.fake.calls) == 2


def test_an_error_body_on_a_200_is_a_failed_read_not_an_empty_ranking(client, upstox):
    upstox.fake.allow(Reply(200, {"status": "error", "errors": [{"message": "x"}]}))
    assert data(get(client))["state"] == "unavailable"


def test_a_refused_token_is_signin_never_retried_and_never_touches_the_session(
    client, upstox, monkeypatch
):
    from database import auth_db

    touched = []
    for name in ("upsert_auth", "invalidate_user_auth_cache", "invalidate_user_cache"):
        monkeypatch.setattr(auth_db, name, lambda *a, n=name, **k: touched.append(n), raising=False)
    with client.session_transaction() as s:
        before = dict(s)
    upstox.fake.allow(Reply(401, {"status": "error"}))
    d = data(get(client))
    assert d["state"] == "signin" and d["rows"] == [] and d["words"] == words.EXPIRED
    assert len(upstox.fake.calls) == 1  # no retry
    data(get(client))
    data(get(new_client()))
    assert len(upstox.fake.calls) == 1  # the refusal is cached for the minute, not asked again
    with client.session_transaction() as s:
        assert dict(s) == before
    assert touched == []
    upstox.token["now"] = TOKEN_2  # a new sign-in: tried once more, with the new token
    upstox.fake.allow(ok())
    assert data(get(client))["state"] == "ok"
    assert upstox.fake.calls[-1][1]["headers"]["Authorization"] == f"Bearer {TOKEN_2}"


def test_a_refused_token_after_a_ranking_labels_the_old_one_with_its_age(client, upstox):
    upstox.fake.allow(ok(), Reply(403))
    good = data(get(client))
    upstox.clock.advance(8 * 3600)
    d = data(get(client))
    assert d["state"] == "signin" and d["made_at"] == good["made_at"]
    assert d["words"] == ("Ranked by Upstox · 8 h ago · Your Upstox sign-in has expired. "
                          "Sign in to read it again.")  # fmt: skip


@pytest.mark.parametrize("reply", [Reply(429), Reply(200, {"status": "error", "errors": [
    {"error_code": "UDAPI10005"}]})], ids=["status", "code"])  # fmt: skip
def test_upstox_asking_us_to_wait_is_its_sentence_with_no_retry(client, upstox, reply):
    upstox.fake.allow(reply)
    d = data(get(client))
    assert d["state"] == "wait" and d["words"] == words.WAIT == (
        "Upstox asked us to wait. Pull down again in a minute.")  # fmt: skip
    data(get(client))
    assert len(upstox.fake.calls) == 1  # asked to wait: not asked again inside the minute


def test_the_plugins_own_limiter_saying_busy_is_a_wait_with_no_call(client, upstox, monkeypatch):
    from broker.upstox.api import rate_limiter
    from utils.broker_backpressure import BrokerBusyError

    def busy(category="standard"):
        raise BrokerBusyError("too many")

    monkeypatch.setattr(rate_limiter, "apply_rate_limit", busy)
    assert data(get(client))["state"] == "wait"
    assert upstox.fake.calls == []


def test_the_read_goes_through_the_plugins_standard_limiter_first(client, upstox, monkeypatch):
    from broker.upstox.api import rate_limiter

    order = []
    monkeypatch.setattr(rate_limiter, "apply_rate_limit", lambda cat="standard": order.append(cat))
    upstox.fake.before_reply = lambda: order.append("sent")
    upstox.fake.allow(ok())
    data(get(client))
    assert order == ["standard", "sent"]


# -- the token --------------------------------------------------------------------------------------
def test_a_changed_token_drops_the_cache_and_the_last_ranking(client, upstox):
    upstox.fake.allow(ok(), ok(), Reply(500))
    data(get(client))
    upstox.token["now"] = TOKEN_2  # the daily rollover, or a new login
    again = data(get(client))
    assert len(upstox.fake.calls) == 2 and again["state"] == "ok"
    upstox.token["now"] = TOKEN  # and back: nothing of the other token's is shown
    upstox.clock.advance(1)
    d = data(get(client))
    assert d["state"] == "unavailable" and d["rows"] == [] and d["made_at"] is None


def test_an_answer_in_flight_when_the_token_changes_is_not_kept(client, upstox):
    def rollover():  # another device acts under the new sign-in while the first read is out
        upstox.fake.before_reply = None
        upstox.token["now"] = TOKEN_2
        data(get(new_client(), cat="OI_LOSERS"))

    upstox.fake.before_reply = rollover
    upstox.fake.allow(ok(), ok(), ok())
    assert data(get(client))["state"] == "ok"  # its own caller still gets what it asked for
    data(get(client))  # under the new token: the old token's answer was not kept for it
    auth = [c[1]["headers"]["Authorization"] for c in upstox.fake.calls]
    assert auth == [f"Bearer {TOKEN}", f"Bearer {TOKEN_2}", f"Bearer {TOKEN_2}"]


def test_the_token_is_in_no_log_line_no_answer_and_no_message(client, upstox, caplog):
    caplog.set_level(logging.DEBUG)
    handler_lines = []

    class Tap(logging.Handler):
        def emit(self, record):
            handler_lines.append(record.getMessage() + repr(record.args) + str(record.exc_info))

    tap = Tap(level=logging.DEBUG)
    logging.getLogger().addHandler(tap)
    seen = []
    try:
        upstox.fake.allow(ok(), httpx.ConnectError("refused"), Reply(401), Reply(429), Reply(502),
                          Reply(200, {"status": "error"}))  # fmt: skip
        for category in ("OI_GAINERS", "OI_LOSERS", "TOP_TRADED", "MOST_ACTIVE", "PRICE_GAINERS",
                         "PRICE_LOSERS"):  # fmt: skip
            r = get(client, cat=category)
            seen.append(r.get_data(as_text=True))
        for token in (None, TOKEN):
            upstox.token["now"] = token
            seen.append(get(client, cat="OI_GAINERS").get_data(as_text=True))
        seen.append(client.get(f"{API}/state", headers=client.headers).get_data(as_text=True))
    finally:
        logging.getLogger().removeHandler(tap)
    logged = [
        r.getMessage() + repr(r.args) + str(r.exc_text) for r in caplog.records
    ] + handler_lines
    for secret in (TOKEN, TOKEN_2, "A1b2A1b2"):
        assert not any(secret in line for line in logged), "the token reached a log line"
        assert not any(secret in text for text in seen), "the token reached an answer"
    assert logged, "nothing was logged, so the check proves nothing"
    assert op.digest(TOKEN) not in "".join(seen)  # not even its fingerprint


# -- closed and stale ---------------------------------------------------------------------------
def test_market_closed_is_upstoxs_own_last_ranking_labelled_with_its_time(
    client, upstox, monkeypatch
):
    monkeypatch.setattr(sessions, "market_open", lambda exchange, now: False)
    upstox.clock.t = datetime(2026, 11, 10, 8, 30, tzinfo=IST).timestamp()
    upstox.fake.allow(ok(stamp="2026-11-09T15:29:40+05:30"))
    d = data(get(client))
    assert d["state"] == "closed" and len(d["rows"]) == 20
    assert d["words"] == "Market closed · ranking from 15:29 yesterday"


def test_commodities_ask_the_mcx_calendar_and_the_rest_nfo(client, upstox, monkeypatch):
    asked = []
    monkeypatch.setattr(sessions, "market_open", lambda ex, now: asked.append(ex) or True)
    upstox.fake.allow(ok(), ok())
    data(get(client))
    data(get(client, asset="COMMODITY", cat="TOP_TRADED"))
    assert asked == ["NFO", "MCX"]


def test_a_ranking_open_market_older_than_ten_minutes_is_stale(client, upstox):
    upstox.fake.allow(ok(stamp="2026-11-09T10:30:00+05:30"))
    d = data(get(client))
    assert (
        d["state"] == "stale"
        and d["words"] == "Ranked by Upstox · 13 min ago · Pull down to read it again"
    )


def test_an_answer_without_a_time_says_so_and_stays_ok(client, upstox):
    body = answer(3)
    del body["data"]["time_stamp"]
    upstox.fake.allow(Reply(200, body))
    d = data(get(client))
    assert d["state"] == "ok" and d["made_at"] is None and d["words"].startswith("Ranked by Upstox")


# -- the exception's limits ---------------------------------------------------------------------------
def test_a_read_starts_no_thread_and_builds_no_second_generation_of_the_master(client, upstox):
    builds = master.shared().builds
    before = {t.ident for t in threading.enumerate()}
    upstox.fake.allow(ok())
    data(get(client))
    data(get(client))
    after = {t.ident for t in threading.enumerate()}
    assert after <= before, "a thread was started"
    gc.collect()
    # the rows come from the snapshot's own by-key lookup: no map of the master is built or kept
    assert master.shared().builds == builds and master.generations_alive() <= 1


def test_two_threads_asking_one_category_make_one_call(client, upstox):
    gate = threading.Barrier(2, timeout=30)
    release = threading.Event()
    upstox.fake.before_reply = lambda: release.wait(30)
    upstox.fake.allow(ok())
    out = []

    def ask():
        gate.wait()
        out.append(op.reading_of(TOKEN, "futures", "INDEX", "OI_GAINERS"))

    threads = [threading.Thread(target=ask) for _ in range(2)]
    for t in threads:
        t.start()
    deadline = time.monotonic() + 10
    while not upstox.fake.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)  # the second is now waiting on the first's flight
    release.set()
    for t in threads:
        t.join(30)
    assert len(out) == 2 and out[0] is out[1] and len(upstox.fake.calls) == 1


# -- a ranked list can be copied as shown (M8.9, "Make a copy") ---------------------------------
RANKED = "ranked:futures:INDEX:OI_GAINERS"


@pytest.fixture
def clean(upstox):
    """OpenAlgo's tables outlive a test's store: start each copy test with no list."""
    from database import watchlist_db as oa

    oa.db_session.query(oa.WatchlistItem).delete()
    oa.db_session.query(oa.Watchlist).delete()
    oa.db_session.commit()
    oa.db_session.remove()


def copy(client, keys, to=None, **more):
    body = {"from": {"ref": RANKED, "keys": keys}, "to": to or {"name": "Copied"}, **more}
    return client.post(f"{API}/copy", json=body, headers=client.headers)


def list_named(name):
    from database import watchlist_db as oa

    try:
        w = oa.db_session.query(oa.Watchlist).filter_by(name=name).first()
        return None if w is None else [
            (i.exchange, i.symbol) for i in sorted(w.items, key=lambda i: i.position)]  # fmt: skip
    finally:
        oa.db_session.remove()


def test_a_ranked_list_is_copied_in_the_order_the_page_sent_with_no_upstox_call(client, upstox, clean):
    r = copy(client, ["NFO:FUT7" + "25NOVFUT", "NFO:FUT2" + "25NOVFUT", "NFO:FUT5" + "25NOVFUT"])
    assert r.status_code == 200, r.get_json()
    done = r.get_json()["data"]
    assert done["added"] == 3 and done["skipped"] == 0 and done["words"] is None
    assert list_named("Copied") == [("NFO", "FUT725NOVFUT"), ("NFO", "FUT225NOVFUT"),
                                    ("NFO", "FUT525NOVFUT")]  # fmt: skip
    assert upstox.fake.calls == []  # the fixture also fails the test on a call


def test_keys_not_in_todays_master_are_left_out_and_counted_in_words(client, upstox, clean):
    r = copy(client, ["NFO:FUT125NOVFUT", "NFO:GONE25NOVFUT", "NFO:ALSO25NOVFUT"])
    done = r.get_json()["data"]
    assert r.status_code == 200 and done["added"] == 1
    assert done["words"] == "2 symbols are not in today's contract list and were left out of the copy."
    assert list_named("Copied") == [("NFO", "FUT125NOVFUT")]
    r = copy(client, ["NFO:FUT225NOVFUT", "NFO:GONE25NOVFUT"], {"name": "Second"})
    assert r.get_json()["data"]["words"] == (
        "1 symbol is not in today's contract list and was left out of the copy.")  # fmt: skip


def test_a_ranked_copy_that_leaves_everything_out_writes_nothing(client, upstox, clean):
    r = copy(client, ["NFO:GONE25NOVFUT"])
    assert r.status_code == 400 and r.get_json()["field"] == "from"
    assert "nothing was copied" in r.get_json()["message"]
    assert list_named("Copied") is None


def test_a_ranked_copy_needs_keys_in_a_shape_and_cannot_be_a_move_or_a_section(client, upstox, clean):
    for keys in ([], ["NFO"], [":X"], ["NFO:"]):
        r = copy(client, keys)
        assert r.status_code == 400 and r.get_json()["field"] == "from", keys
    too_many = [f"NFO:FUT{i}25NOVFUT" for i in range(51)]
    assert copy(client, too_many).status_code == 400
    r = client.post(f"{API}/copy", headers=client.headers, json={
        "from": {"ref": RANKED, "section_id": 1}, "to": {"name": "X"}})  # fmt: skip
    assert r.status_code == 400
    r = copy(client, ["NFO:FUT125NOVFUT"], move=True)
    assert r.status_code == 400 and r.get_json()["message"] == words.LOCKED
    r = client.post(f"{API}/copy", headers=client.headers, json={
        "from": {"ref": "ranked:futures:INDEX:NOPE", "keys": ["NFO:FUT125NOVFUT"]},
        "to": {"name": "X"}})  # fmt: skip
    assert r.status_code == 400 and r.get_json()["field"] == "from"
    assert list_named("Copied") is None and list_named("X") is None


def test_a_ranked_copy_with_the_master_not_loaded_is_failed_in_words(client, upstox, clean):
    upstox.box.snap = None
    r = copy(client, ["NFO:FUT125NOVFUT"])
    assert r.status_code == 500 and "contract list is not loaded" in r.get_json()["message"]
