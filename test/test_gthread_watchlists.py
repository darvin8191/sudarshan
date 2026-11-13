"""The Watchlists v2 bridge under a real gunicorn gthread worker (M8.4, W4).

The bridge is the first SUDARSHAN code to sit in OpenAlgo's request path with state of its own (the
record check's cache, the shared limiter), so it is run where the threads are real: a tiny Flask app
with the real blueprint, CSRF and the shared limiter, six or more requests released by a barrier
at once. The session checks are replaced by a cookie the app sets itself (OpenAlgo's own checks are
tested in-process beside the bridge); everything after them is the production code.

Three things are held: concurrent reads each answer correctly (no request sees another's fields,
and the cold first requests that open the store all succeed), a switched-off server answers every
registered op as off and opens nothing, and the per-class limit lets no more than its count
through when the requests arrive together.

Linux with gunicorn installed only (the production server); skipped elsewhere.
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import os
import socket
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or importlib.util.find_spec("gunicorn") is None,
    reason="needs gunicorn on Linux (the production server)",
)

APP = textwrap.dedent(
    """
    from flask import Blueprint, Flask, jsonify, session
    from flask_wtf.csrf import CSRFProtect, generate_csrf

    import blueprints.sudarshan_watchlists as bridge
    from limiter import limiter
    from sudarshan.watchlists import contract, ops

    app = Flask(__name__)
    app.secret_key = "test-only-secret-key-" + "0" * 32  # pragma: allowlist secret
    app.config.update(WTF_CSRF_ENABLED=True, WTF_CSRF_TIME_LIMIT=None)
    app.csrf = CSRFProtect(app)
    limiter.init_app(app)
    bridge.is_session_valid = lambda: bool(session.get("in"))
    bridge.is_owner_session = lambda: session.get("in") == "owner"

    def echo(name):
        return lambda args: {"op": name, "args": args}

    handlers = {op.name: echo(op.name) for op in contract.OPS}
    handlers.update(ops.discover())  # the real state op where there is one
    # No master is loaded in this server, so the concurrency test of a read with its own fields
    # keeps an echo for ``strikes`` (the real op is tested in-process, test_watchlists_search_ops).
    handlers["strikes"] = echo("strikes")
    # The preview reads the master too, so the limit tests below keep an echo for it.
    handlers["tv_preview"] = echo("tv_preview")
    app.register_blueprint(bridge.attach(
        Blueprint("wl_gthread", __name__, url_prefix=contract.API_PREFIX), handlers))

    # W5: OpenAlgo's watchlist blueprint's name and add route, a recorder where the push is
    # emitted (no Socket.IO server here), and a bump of the bridge's own.
    from sudarshan.watchlists import versions

    emitted = []
    versions.emit_from_any_thread = lambda event, data, **kw: emitted.append(data)
    stand_in = Blueprint("watchlist_bp", __name__)

    @stand_in.route("/watchlist/api/lists/<int:watchlist_id>/items", methods=["POST"])
    def add_symbol(watchlist_id):
        return jsonify(status="success"), 201

    app.register_blueprint(stand_in)

    @app.route("/bump", methods=["POST"])
    def bump():
        versions.bump("items", ["list:1"])
        return "ok"

    # W7: the real section ops, over a list OpenAlgo "has" (its own database is not under test)
    from sudarshan.watchlists.refs import list as listref

    listref.read_live = lambda user, lid: {
        "id": lid, "name": "L", "created_at": "2026-01-01 00:00:00", "items": []}

    # W10: the real favourites op; OpenAlgo's lists are not under test, so none are read
    from sudarshan.watchlists.ops import lists as wl_lists

    wl_lists.own_lists = lambda user: []

    # W22: the real ranked op, over a fake Upstox that counts its calls and takes half a second, a
    # fake sign-in and a small master. Nothing here is real data.
    import time as _time

    from sudarshan.watchlists import master as wl_master, sessions, smartlist
    from sudarshan.watchlists.ops import ranked as wl_ranked

    class _Row:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    _rows = [_Row(symbol=f"F{i}25NOVFUT", name=f"F{i}", exchange="NFO", instrumenttype="FUT",
                  underlying=f"F{i}", expiry="25-NOV-26", strike=None, lotsize=10, tick_size=0.05,
                  token=f"NSE_FO|{i}") for i in range(30)]
    _snap = _Row(by_exchange={"NFO": tuple(_rows)}, by_token={r.token: r for r in _rows},
                 by_symbol_exchange={(r.symbol, r.exchange): r for r in _rows}, session_start=None)
    upstox_calls = []

    class _Reply:
        status_code = 200

        def json(self):
            from datetime import datetime, timedelta, timezone

            made = datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat()
            return {"data": {"time_stamp": made, "data": [
                {"instrument_key": r.token, "price": {}, "metric": {"current": 5, "change_pct": 1.0}}
                for r in _rows]}}

    class _Upstox:
        def get(self, url, **kw):
            upstox_calls.append(kw["params"]["category"])
            _time.sleep(0.5)  # long enough for every other thread to arrive and wait
            return _Reply()

    wl_master.openalgo_snapshot = lambda: _snap
    smartlist.get_httpx_client = lambda: _Upstox()
    wl_ranked.owner_token = lambda user: "tok-" + "Q7r8" * 8  # fake, test only
    wl_ranked.stored_broker = lambda user: "upstox"
    sessions.market_open = lambda exchange, now: True

    @app.route("/upstox_calls")
    def upstox_calls_made():
        return jsonify(upstox_calls)

    @app.route("/emitted")
    def emitted_pushes():
        return jsonify(emitted)

    @app.route("/ping")
    def ping():
        return "ok"

    @app.route("/login")
    def login():
        session["in"] = "owner"
        session["user"] = "owner"
        session["broker"] = "upstox"
        return jsonify(csrf=generate_csrf())
    """
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Server:
    def __init__(self, tmp_path: Path, switch: str):
        self.db = tmp_path / "wl.db"
        (tmp_path / "fixture_wl_app.py").write_text(APP)
        env = dict(os.environ)
        env.pop("GUNICORN_CMD_ARGS", None)
        env.update(
            PYTHONPATH=os.pathsep.join([str(tmp_path), str(ROOT)]),
            SUDARSHAN_WATCHLISTS_V2=switch,
            SUDARSHAN_WATCHLISTS_DB=str(self.db),
            API_KEY_PEPPER="f" * 64,  # fake, test only
            APP_KEY="test-only-app-key-" + "0" * 32,
            DATABASE_URL=f"sqlite:///{tmp_path}/unused.db",
            LOG_DIR=str(tmp_path / "log"),
        )
        self.port = _free_port()
        self.log = tmp_path / "gunicorn.log"
        self.logfile = self.log.open("w")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "gunicorn",
                "--worker-class",
                "gthread",
                "--threads",
                "16",
                "--workers",
                "1",
                "--bind",
                f"127.0.0.1:{self.port}",
                "--no-control-socket",
                "--log-level",
                "warning",
                "fixture_wl_app:app",
            ],  # fmt: skip
            cwd=tmp_path,
            env=env,
            stdout=self.logfile,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                if self.request("GET", "/ping")[0] == 200:
                    return
            except OSError:
                time.sleep(0.2)
        self.process.kill()
        raise AssertionError("gunicorn did not start:\n" + self.log.read_text())

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        data = json.dumps(body) if body is not None else None
        hdrs = {"Content-Type": "application/json", **(headers or {})}
        conn.request(method, path, body=data, headers=hdrs)
        r = conn.getresponse()
        raw = r.read()
        cookie = (r.getheader("Set-Cookie") or "").split(";")[0]
        conn.close()
        return r.status, raw, cookie

    def login(self):
        """A signed-in owner's cookie and CSRF token."""
        status, raw, cookie = self.request("GET", "/login")
        assert status == 200
        return {"Cookie": cookie, "X-CSRFToken": json.loads(raw)["csrf"]}

    def stop(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.logfile.close()


@pytest.fixture
def server_on(tmp_path):
    server = Server(tmp_path, "on")
    yield server
    server.stop()


@pytest.fixture
def server_off(tmp_path):
    server = Server(tmp_path, "off")
    yield server
    server.stop()


def at_once(count, call):
    """Run ``call(i)`` on ``count`` threads released together; the results in order."""
    barrier = threading.Barrier(count)
    results = [None] * count

    def run(i):
        barrier.wait(30)
        results[i] = call(i)

    threads = [threading.Thread(target=run, args=(i,), name=f"wl-test-{i}") for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert all(r is not None for r in results), "a request did not finish"
    return results


def lists_count(server, headers) -> int:
    status, raw, _ = server.request("GET", "/sudarshan/watchlists/api/state", headers=headers)
    assert status == 200
    return int(json.loads(raw)["data"]["versions"]["lists"].split(":")[1])


def test_the_harness_sees_a_lost_update_in_a_counter_that_checks_then_acts():
    """The control: a counter read, then written back after a yield loses bumps under twelve
    threads, so the tests below can fail on a broken version."""
    box = {"n": 0}

    def racy(i):
        n = box["n"]
        time.sleep(0.001)
        box["n"] = n + 1
        return True

    at_once(12, racy)
    assert box["n"] < 12


def test_twelve_bumps_at_once_raise_the_lists_version_by_exactly_twelve(server_on):
    headers = server_on.login()
    before = lists_count(server_on, headers)
    at_once(12, lambda i: server_on.request("POST", "/bump", headers=headers))
    assert lists_count(server_on, headers) == before + 12
    _, raw, _ = server_on.request("GET", "/emitted")
    versions = [push["v"] for push in json.loads(raw)]
    assert len(versions) == 12 and len(set(versions)) == 12, "a push repeated a version"


def test_concurrent_writes_through_the_hook_emit_once_each(server_on):
    headers = server_on.login()
    before = lists_count(server_on, headers)
    results = at_once(
        12,
        lambda i: server_on.request(
            "POST", f"/watchlist/api/lists/{i + 1}/items", body={}, headers=headers
        ),
    )
    assert [status for status, _, _ in results] == [201] * 12
    assert lists_count(server_on, headers) == before + 12
    _, raw, _ = server_on.request("GET", "/emitted")
    pushes = json.loads(raw)
    assert sorted(p["ref"] for p in pushes) == sorted(f"list:{i + 1}" for i in range(12))
    assert all(p["refs"] == [p["ref"]] for p in pushes)
    assert len({p["v"] for p in pushes}) == 12 and {p["why"] for p in pushes} == {"items"}


def test_six_concurrent_reads_each_answer_for_themselves(server_on):
    headers = server_on.login()
    base = "/sudarshan/watchlists/api"
    states = at_once(6, lambda i: server_on.request("GET", f"{base}/state", headers=headers))
    for status, raw, _ in states:
        body = json.loads(raw)
        assert status == 200 and body["status"] == "ok"
        assert body["data"]["enabled"] is True
        assert body["data"]["limits"]["lists"] == 50
    assert server_on.db.exists(), "the first requests did not open the store"
    # another op at once, each with its own fields: no request may answer with another's
    seen = at_once(
        6,
        lambda i: server_on.request("GET", f"{base}/strikes?u=U{i}&x=NFO&n={i}", headers=headers),
    )
    for i, (status, raw, _) in enumerate(seen):
        assert status == 200
        assert json.loads(raw)["data"]["args"] == {"u": f"U{i}", "x": "NFO", "n": i}


def test_switched_off_every_registered_op_answers_off(server_off):
    from sudarshan.watchlists import contract

    headers = server_off.login()
    ops = list(contract.OPS)
    results = at_once(
        len(ops),
        lambda i: server_off.request(
            ops[i].method, "/sudarshan/watchlists/api" + ops[i].route.replace("<id>", "7")
            .replace("<k>", "k").replace("<ref>", "r"),
            body={} if ops[i].method not in ("GET", "DELETE") else None, headers=headers,
        ),
    )  # fmt: skip
    for op, (status, raw, _) in zip(ops, results, strict=True):
        body = json.loads(raw)
        if op.name == "state":
            assert (status, body) == (200, {"status": "ok", "data": {"enabled": False}})
        else:
            assert (status, body["code"]) == (503, "off"), op.name
    assert not server_off.db.exists(), "the store was opened while switched off"


def test_the_limit_of_a_class_lets_no_more_through_than_it_allows(server_on):
    from sudarshan.watchlists import contract

    headers = server_on.login()
    allowed = contract.LIMITS_PER_MIN["preview"]
    results = at_once(
        allowed * 4,
        lambda i: server_on.request(
            "POST", "/sudarshan/watchlists/api/tv/preview", body={"text": "x"}, headers=headers
        ),
    )
    codes = sorted(status for status, _, _ in results)
    assert codes.count(200) == allowed, codes
    assert codes.count(429) == len(results) - allowed
    busy = next(json.loads(raw) for status, raw, _ in results if status == 429)
    assert busy["code"] == "busy" and busy["status"] == "error"


def test_requests_without_a_session_released_together_spend_none_of_the_owners_limit(server_on):
    """The limit counts after the session check: strangers arriving at once (all from one address,
    as behind nginx) are refused as signed out and leave the owner's whole budget."""
    from sudarshan.watchlists import contract

    allowed = contract.LIMITS_PER_MIN["preview"]
    path = "/sudarshan/watchlists/api/tv/preview"
    strangers = at_once(allowed * 4, lambda i: server_on.request("POST", path, body={"text": "x"}))
    assert {status for status, _, _ in strangers} == {401}
    assert {json.loads(raw)["code"] for _, raw, _ in strangers} == {"session"}
    headers = server_on.login()
    mine = at_once(
        allowed, lambda i: server_on.request("POST", path, body={"text": "x"}, headers=headers)
    )
    assert [status for status, _, _ in mine] == [200] * allowed


# -- W7: the section writes decide on a version, as one compare-and-set -----------------------------
SECTIONS = "/sudarshan/watchlists/api/sections"


def add_section(server, headers, list_id, name, version):
    status, raw, _ = server.request(
        "POST", SECTIONS, {"list": list_id, "name": name, "version": version}, headers
    )
    return status, json.loads(raw)


def store_rows(server, sql):
    with sqlite3.connect(server.db) as conn:
        return conn.execute(sql).fetchall()


def test_two_writers_on_one_version_one_wins_and_the_other_is_told_conflict(server_on):
    headers = server_on.login()
    assert add_section(server_on, headers, 5, "First", "0")[0] == 200
    for round_no in range(3):  # a race is only seen when the threads really overlap: try again
        held = store_rows(server_on, "SELECT version FROM wl_list WHERE list_id = 5")[0][0]
        got = at_once(
            8,
            lambda i, r=round_no, v=held: add_section(server_on, headers, 5, f"R{r}-{i}", str(v)),
        )
        assert sorted(status for status, _ in got) == [200] + [409] * 7, got
        assert {body["code"] for status, body in got if status == 409} == {"conflict"}
        (winner,) = (body for status, body in got if status == 200)
        assert winner["data"]["version"] == str(held + 1)
        (now,) = store_rows(server_on, "SELECT version FROM wl_list WHERE list_id = 5")
        assert now[0] == held + 1
    names = store_rows(server_on, "SELECT count(*) FROM wl_section WHERE list_id = 5")
    assert names[0][0] == 1 + 3  # exactly one section per round
    status, raw, _ = server_on.request("GET", "/emitted")
    assert len(json.loads(raw)) == 1 + 3  # one push per successful write, none for a conflict


def test_two_first_writes_to_one_list_make_one_row_and_the_other_is_told_conflict(server_on):
    headers = server_on.login()
    for list_id in range(10, 14):
        got = at_once(8, lambda i, lid=list_id: add_section(server_on, headers, lid, f"N{i}", "0"))
        assert sorted(status for status, _ in got) == [200] + [409] * 7, got  # never a 500
        assert {body["code"] for status, body in got if status == 409} == {"conflict"}
        rows = store_rows(server_on, f"SELECT version FROM wl_list WHERE list_id = {list_id}")
        assert len(rows) == 1 and rows[0][0] > 10**12  # one row, at the epoch ms (review L2)
        sections = store_rows(server_on, f"SELECT 1 FROM wl_section WHERE list_id = {list_id}")
        assert len(sections) == 1


FAVOURITES = "/sudarshan/watchlists/api/favourites"


def put_favourites(server, headers, ref, version):
    status, raw, _ = server.request(
        "PUT", FAVOURITES, {"slots": [{"slot": 1, "ref": ref}], "version": version}, headers
    )
    return status, json.loads(raw)


def test_two_writers_on_one_favourites_version_one_wins_and_the_other_is_told_conflict(server_on):
    headers = server_on.login()
    server_on.request("GET", "/sudarshan/watchlists/api/state", headers=headers)  # makes the store
    for _ in range(4):  # a race is only seen when the threads really overlap: try again
        rows = store_rows(server_on, "SELECT value FROM wl_meta WHERE name = 'favourites_version'")
        held = int(rows[0][0]) if rows else 0  # round 0 is the first write: no row yet
        got = at_once(
            8, lambda i, v=held: put_favourites(server_on, headers, f"flag:{i % 5 + 1}", v)
        )
        assert sorted(status for status, _ in got) == [200] + [409] * 7, got  # never a 500
        assert {body["code"] for status, body in got if status == 409} == {"conflict"}
        (winner,) = (body for status, body in got if status == 200)
        assert winner["data"]["version"] == str(held + 1)
        (now,) = store_rows(
            server_on, "SELECT value FROM wl_meta WHERE name = 'favourites_version'"
        )
        assert int(now[0]) == held + 1
        assert len(store_rows(server_on, "SELECT 1 FROM wl_favourite")) == 1  # one set of slots
    status, raw, _ = server_on.request("GET", "/emitted")
    pushed = json.loads(raw)
    assert [p["favourites"] for p in pushed] == [1, 2, 3, 4]  # one push per win, none for a loss
    assert {p["why"] for p in pushed} == {"favourites"}
    assert len({p["v"] for p in pushed}) == 1  # and the lists version never moved


# -- W9: a mark decides on the symbol's version, a rename on the flags version ---------------------
API = "/sudarshan/watchlists/api"


def put(server, headers, path, body):
    status, raw, _ = server.request("PUT", API + path, body, headers)
    return status, json.loads(raw)


def mark_once(server, headers, round_no, i, held):
    body = {"flag": 1 + i % 5, "note": f"r{round_no}-{i}"}
    if held is not None:
        body["version"] = held
    return put(server, headers, "/symbols/NSE:INFY", body)


def test_two_marks_on_one_version_one_wins_and_the_other_is_told_conflict(server_on):
    headers = server_on.login()
    sql = "SELECT version FROM wl_symbol WHERE symbol = 'INFY'"
    for round_no in range(4):  # a race is only seen when the threads really overlap: try again
        held = store_rows(server_on, sql)[0][0] if round_no else None
        got = at_once(8, lambda i, r=round_no, v=held: mark_once(server_on, headers, r, i, v))
        assert sorted(status for status, _ in got) == [200] + [409] * 7, got  # never a 500
        assert {b["code"] for status, b in got if status == 409} == {"conflict"}
        (row,) = store_rows(server_on, "SELECT flag, note, version FROM wl_symbol")
        (won,) = (i for i, (status, _) in enumerate(got) if status == 200)
        assert row[:2] == (1 + won % 5, f"r{round_no}-{won}") and row[2] != held
    assert len(store_rows(server_on, "SELECT 1 FROM wl_symbol")) == 1
    status, raw, _ = server_on.request("GET", "/emitted")
    assert len(json.loads(raw)) == 4  # one push per successful write, none for a conflict


def test_two_renames_on_one_flags_version_one_wins_and_the_version_moves_once(server_on):
    headers = server_on.login()
    for round_no in range(3):
        got = at_once(
            8,
            lambda i, r=round_no: put(
                server_on, headers, "/flags", {"names": [{"mark": 1, "name": f"N{r}{i}"}],
                                               "version": r},
            ),
        )  # fmt: skip
        assert sorted(status for status, _ in got) == [200] + [409] * 7, got
        assert store_rows(server_on, "SELECT value FROM wl_meta WHERE name = 'flags_version'") == [
            (str(round_no + 1),)
        ]
    assert len(store_rows(server_on, "SELECT 1 FROM wl_flag")) == 1


# -- W22: the Smartlist read is one call for any number of requests -----------------------------------
RANKED = "/sudarshan/watchlists/api/ranked?src=futures&asset=INDEX&cat=OI_GAINERS"


def upstox_calls(server):
    status, raw, _ = server.request("GET", "/upstox_calls")
    assert status == 200
    return json.loads(raw)


def test_eight_threads_asking_one_category_at_once_make_one_upstox_call(server_on):
    headers = server_on.login()
    results = at_once(8, lambda i: server_on.request("GET", RANKED, headers=headers))
    bodies = [json.loads(raw) for _, raw, _ in results]
    assert [status for status, _, _ in results] == [200] * 8
    assert all(b["status"] == "ok" and b["data"]["state"] == "ok" for b in bodies)
    assert all(len(b["data"]["rows"]) == 20 for b in bodies)
    assert len({b["data"]["read_at"] for b in bodies}) == 1, "the requests were not one read"
    assert upstox_calls(server_on) == ["OI_GAINERS"], "single flight made more than one call"


def test_a_second_device_inside_the_minute_costs_no_call_and_another_category_costs_one(server_on):
    headers = server_on.login()
    other = server_on.login()  # a second device
    assert server_on.request("GET", RANKED, headers=headers)[0] == 200
    assert server_on.request("GET", RANKED + "&size=50", headers=other)[0] == 200
    assert upstox_calls(server_on) == ["OI_GAINERS"]
    results = at_once(
        4,
        lambda i: server_on.request(
            "GET", RANKED.replace("OI_GAINERS", "OI_LOSERS"), headers=headers if i % 2 else other
        ),
    )
    assert [status for status, _, _ in results] == [200] * 4
    assert sorted(upstox_calls(server_on)) == ["OI_GAINERS", "OI_LOSERS"]
