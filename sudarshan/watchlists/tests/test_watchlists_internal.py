"""The sealed members route (M8.14, W23): the seal and its refusals, each kind of ref, the futures
and the rows an alert cannot hold, the versions, the switch, and the alerts service's own client
reading the route through a test app. Real OpenAlgo lists and a master of the tests' own."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime

import pytest
from presets_master import V as PRESETS
from presets_master import indices_rows, snapshot_of
from watchlists_bridge_app import (
    OWNER,
    VIEWER,
    csrf_headers,
    login_session,
    make_app,
    reset_users,
)
from watchlists_follow_master import build_master

import blueprints.sudarshan_watchlists as bp_module
from sudarshan.autologin.handoff import seal
from sudarshan.vault import WATCHLISTS_INTERNAL_INFO, fernet_for, pepper_from
from sudarshan.watchlists import contract, engine, master, ops, versions, view
from sudarshan.watchlists import expiry as stops
from sudarshan.watchlists.bridge import internal
from sudarshan.watchlists.refs import list as list_reader
from sudarshan.watchlists.refs import preset as preset_reader
from sudarshan.watchlists.words import members as words
from sudarshan.watchlists.words import ranked as ranked_words

API = contract.API_PREFIX
URL = contract.INTERNAL_PREFIX + "/read"
NOW = 1_700_000_000_000
AT = int(datetime.fromisoformat("2026-11-20T11:00:00+05:30").timestamp() * 1000)
CLOSE_24 = int(datetime.fromisoformat("2026-11-24T15:40:00+05:30").timestamp() * 1000)


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    from database import watchlist_db as oa
    from sudarshan.watchlists import sessions
    from sudarshan.watchlists.store import sections as sections_store

    monkeypatch.setenv(contract.DB_ENV, str(tmp_path / "wl.db"))
    monkeypatch.setenv(contract.SWITCH_ENV, "on")
    bp_module._record.clear()
    engine.dispose()
    versions.forget()
    oa.Base.metadata.create_all(oa.engine)
    oa.db_session.query(oa.WatchlistItem).delete()
    oa.db_session.query(oa.Watchlist).delete()
    oa.db_session.commit()
    oa.db_session.remove()
    use(build_master(), monkeypatch)
    monkeypatch.setattr(sections_store, "now_ms", lambda: NOW)
    monkeypatch.setattr(versions, "emit_from_any_thread", lambda *a, **k: None)
    monkeypatch.setattr(list_reader, "now_ms", lambda: AT)
    monkeypatch.setattr(preset_reader, "now_ms", lambda: AT)
    monkeypatch.setattr(preset_reader, "calendar_close", lambda iso: None)
    monkeypatch.setattr(internal, "reader_now", lambda: AT)
    monkeypatch.setattr(sessions, "market_open", lambda exchange, now: True)
    monkeypatch.setattr(sessions, "closes", lambda dates, exchange: {})
    internal._nonces._until.clear()
    yield
    master.shared().drop()
    engine.dispose()
    versions.forget()
    bp_module._record.clear()


def use(snap, monkeypatch):
    monkeypatch.setattr(master, "openalgo_snapshot", lambda: snap)
    master.shared().drop()


@pytest.fixture
def app():
    reset_users()
    return make_app(ops.discover(), real=True)


@pytest.fixture
def client(app):
    return app.test_client()


def fernet():
    return fernet_for(pepper_from(os.environ), WATCHLISTS_INTERNAL_INFO)


def ask(client, op="members", ref=None, key=None, **headers):
    fields = {} if ref is None else {"ref": ref}
    blob = seal(key or fernet(), op, **fields)
    return client.post(URL, query_string={"op": op}, data=blob, headers={
        "Content-Type": "application/octet-stream", **headers})  # fmt: skip


def data_of(r):
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]


def make_list(items=("SBIN",), name="Swing"):
    from database import watchlist_db as oa

    made = oa.create_watchlist(OWNER, name, [
        {"symbol": s.rpartition(":")[2], "exchange": s.rpartition(":")[0] or "NSE"} for s in items
    ])  # fmt: skip
    created = oa.db_session.query(oa.Watchlist).filter_by(id=made["id"]).one().created_at
    oa.db_session.remove()
    return made["id"], view.created_key(created)


def follow(lid, version, **more):
    reset = login_session
    c = make_app(ops.discover(), real=True).test_client()
    reset(c)
    body = {"list": lid, "version": version, "kind": "atm_option", "underlying": "NIFTY",
            "underlying_exchange": "NSE_INDEX", "opt_right": "CE", **more}  # fmt: skip
    r = c.post(API + "/rows", json=body, headers=csrf_headers(c))
    assert r.status_code == 200, r.get_json()


# -- the seal ------------------------------------------------------------------------------------
def test_a_sealed_request_is_answered_without_a_session_or_a_csrf_token(client):
    lid, _ = make_list()
    got = data_of(ask(client, ref=f"list:{lid}"))
    assert [r["symbol"] for r in got["rows"]] == ["SBIN"]
    assert data_of(ask(client, "version")) == {"version": versions.read()}


def test_a_wrong_key_is_refused(client):
    from cryptography.fernet import Fernet

    r = ask(client, ref="list:1", key=Fernet(Fernet.generate_key()))
    assert r.status_code == 403 and "rows" not in r.get_data(as_text=True)


def test_a_key_of_another_purpose_is_refused(client):
    other = fernet_for(pepper_from(os.environ), b"sudarshan/screener/internal/v1")
    assert ask(client, ref="list:1", key=other).status_code == 403


def test_a_replayed_request_is_refused(client):
    lid, _ = make_list()
    blob = seal(fernet(), "members", ref=f"list:{lid}")
    send = lambda: client.post(URL, query_string={"op": "members"}, data=blob)  # noqa: E731
    assert send().status_code == 200
    assert send().status_code == 403


def test_a_seal_for_one_op_does_not_open_another(client):
    blob = seal(fernet(), "version")
    assert client.post(URL, query_string={"op": "members"}, data=blob).status_code == 403


def test_a_request_over_16_kib_is_refused_before_it_is_opened(client):
    big = b"x" * (contract.INTERNAL_MAX_BYTES + 1)
    assert client.post(URL, query_string={"op": "members"}, data=big).status_code == 413


def test_a_missing_seal_is_refused(client):
    assert client.post(URL, query_string={"op": "members"}).status_code == 411  # no length
    assert client.post(URL, query_string={"op": "members"}, data=b"").status_code in (403, 411)
    assert client.post(URL, query_string={"op": "members"}, data=b"junk").status_code == 403


@pytest.mark.parametrize("header", internal.PROXY_HEADERS)
def test_a_request_nginx_forwarded_is_a_404_however_it_is_sealed(client, header):
    lid, _ = make_list()
    assert ask(client, ref=f"list:{lid}", **{header: "203.0.113.9"}).status_code == 404


def test_the_proxy_headers_are_the_screeners(client):
    import blueprints.sudarshan_screener as screener

    assert internal.PROXY_HEADERS == screener.PROXY_HEADERS


def test_an_op_the_route_does_not_have_is_a_404(client):
    for op in ("", "orders", "members2"):
        assert client.post(URL, query_string={"op": op}, data=b"x").status_code == 404
    assert client.get(URL, query_string={"op": "members"}).status_code == 405


def test_the_route_reads_so_it_is_not_a_write_route_and_a_viewer_cannot_reach_it(app):
    assert not [r for r in contract.WRITE_ROUTES if r[1].startswith(contract.INTERNAL_PREFIX)]
    assert all(op.cls != "write" for op in contract.INTERNAL_OPS)
    assert URL not in [API + op.route for op in contract.OPS]
    viewer = app.test_client()
    login_session(viewer, VIEWER)
    headers = csrf_headers(viewer)
    for op in ("members", "version"):  # a session is no key: the seal is the only way in
        r = viewer.post(URL, query_string={"op": op}, data=b"", headers=headers)
        assert r.status_code in (403, 411)
        r = viewer.post(URL, query_string={"op": op}, json={"ref": "list:1"}, headers=headers)
        assert r.status_code == 403


# -- the refs ------------------------------------------------------------------------------------
def test_a_list_answers_each_row_with_its_fields_its_versions_and_its_identity(client):
    lid, created = make_list(("SBIN", "NSE_INDEX:NIFTY"))
    got = data_of(ask(client, ref=f"list:{lid}"))
    sbin = got["rows"][0]
    assert sbin == {"symbol": "SBIN", "exchange": "NSE", "instrument_key": "NSE|SBIN",
                    "tick_size": "0.05", "expiry": None, "lot": 1}  # fmt: skip
    assert got["created_at"] == got["list_key"] == created
    assert got["version"] == versions.read() and len(got["ref_version"]) == 12
    assert got["left_out"] == [] and got["valid_until"] is None


def test_a_future_is_kept_resolved_with_the_instant_it_stops_and_atm_rows_are_not(client):
    lid, _ = make_list(("SBIN",))
    follow(lid, "0", kind="month_future")
    follow(lid, str(NOW), kind="atm_option")
    follow(lid, str(NOW + 1), kind="strike_pair", strike_rule="fixed", strike=25900, opt_right=None)
    got = data_of(ask(client, ref=f"list:{lid}"))
    future, sbin = got["rows"]
    assert (
        future["symbol"] == "NIFTY24NOV26FUT" and future["instrument_key"] == "NFO|NIFTY24NOV26FUT"
    )
    assert future["expiry"] == "2026-11-24" and future["lot"] == 75
    assert future["valid_until"] == CLOSE_24 == got["valid_until"]
    assert "valid_until" not in sbin
    assert [(c["why"], c["symbol"]) for c in got["left_out"]] == [("atm", "NIFTY"),
                                                                  ("strike", "NIFTY")]  # fmt: skip
    assert got["left_out"][0]["reason"] == words.FOLLOW_AT_THE_MONEY
    assert "changes contract during the day" in got["left_out"][1]["reason"]


def test_a_special_session_close_moves_valid_until(client, monkeypatch):
    from sudarshan.watchlists import sessions

    lid, _ = make_list(("SBIN",))
    follow(lid, "0", kind="month_future", expiry_rule="nearest")
    monkeypatch.setattr(internal, "reader_now", lambda: CLOSE_24 - 3_600_000)
    monkeypatch.setattr(list_reader, "now_ms", lambda: CLOSE_24 - 3_600_000)
    early = CLOSE_24 - 7_200_000
    monkeypatch.setattr(
        sessions, "closes", lambda dates, ex: {d: early for d in dates if d == "2026-11-24"}
    )
    pass
    # the 24th stopped at the calendar's early close, so the row already follows 1 Dec
    got = data_of(ask(client, ref=f"list:{lid}"))
    assert got["rows"][0]["symbol"] == "NIFTY01DEC26FUT"
    assert got["valid_until"] == stops.stop_ms("2026-12-01", index=True)
    monkeypatch.setattr(internal, "reader_now", lambda: early - 60_000)
    monkeypatch.setattr(list_reader, "now_ms", lambda: early - 60_000)
    assert data_of(ask(client, ref=f"list:{lid}"))["valid_until"] == early  # before it: the 24th


def test_a_future_on_an_index_the_master_names_stops_at_1530_from_7_dec(client, monkeypatch):
    """Review M1: the route asks OpenAlgo's one rule (``index_contracts``) which underlyings are
    an index's. The old tuple did not name NEWINDEX, so a list alert kept its expiring future to
    15:45 while the chart and the sandbox stopped it at 15:30."""
    from watchlists_follow_master import new_index_rows

    from sudarshan import index_contracts

    use(build_master(more=new_index_rows(("29-DEC-26", "26-JAN-27"))), monkeypatch)
    day = int(datetime.fromisoformat("2026-12-29T11:00:00+05:30").timestamp() * 1000)
    monkeypatch.setattr(internal, "reader_now", lambda: day)
    monkeypatch.setattr(list_reader, "now_ms", lambda: day)
    lid, _ = make_list(("SBIN",))
    follow(lid, "0", kind="month_future", underlying="NEWINDEX", expiry_rule="nearest")
    known = index_contracts.KNOWN
    monkeypatch.setattr(
        index_contracts, "underlyings", lambda: known._replace(indices=known.indices | {"NEWINDEX"})
    )
    got = data_of(ask(client, ref=f"list:{lid}"))
    assert got["rows"][0]["symbol"] == "NEWINDEX29DEC26FUT"
    at_1530 = int(datetime.fromisoformat("2026-12-29T15:30:00+05:30").timestamp() * 1000)
    assert got["rows"][0]["valid_until"] == at_1530 == got["valid_until"]
    monkeypatch.setattr(index_contracts, "underlyings", lambda: known)  # the master unread
    held = data_of(ask(client, ref=f"list:{lid}"))
    assert held["rows"][0]["valid_until"] == at_1530 + 15 * 60_000  # a stock's 15:45: no guess


def test_a_section_answers_its_rows_and_the_lists_identity(client):
    lid, created = make_list(("SBIN",))
    c = make_app(ops.discover(), real=True).test_client()
    login_session(c)
    sid = c.post(API + "/sections", json={"list": lid, "name": "Hedge", "version": "0"},
                 headers=csrf_headers(c)).get_json()["data"]["id"]  # fmt: skip
    follow(lid, str(NOW), kind="month_future", section_id=sid)
    got = data_of(ask(client, ref=f"list:{lid}/section:{sid}"))
    assert [r["symbol"] for r in got["rows"]] == ["NIFTY24NOV26FUT"]
    assert got["list_key"] == created


def test_a_built_in_list_answers_its_futures_with_each_ones_stop_and_no_list_key(
    client, monkeypatch
):
    snap, _ = snapshot_of(PRESETS["master"] + indices_rows(), datetime(2026, 11, 9, 8, 40))
    use(snap, monkeypatch)
    monkeypatch.setattr(preset_reader, "now_ms", lambda: AT)
    got = data_of(ask(client, ref="preset:index_futures"))
    assert [f"{r['exchange']}:{r['symbol']}" for r in got["rows"]] == [
        "NFO:NIFTY24NOV26FUT", "NFO:BANKNIFTY24NOV26FUT", "NFO:FINNIFTY24NOV26FUT",
        "BFO:SENSEX26NOV26FUT"]  # fmt: skip
    assert [r["valid_until"] for r in got["rows"]] == [CLOSE_24] * 3 + [
        stops.stop_ms("2026-11-26", index=True, exchange="BFO")
    ]  # each row stops on its own day
    assert got["valid_until"] == CLOSE_24  # the first of them
    assert "list_key" not in got and "created_at" not in got
    stocks = data_of(ask(client, ref="preset:fno_stocks"))
    assert all("valid_until" not in r for r in stocks["rows"])
    assert [c["why"] for c in stocks["left_out"]] == ["missing"]


def test_a_flag_list_answers_its_rows_and_never_a_flag_or_a_note(client):
    lid, _ = make_list(("SBIN",))
    c = make_app(ops.discover(), real=True).test_client()
    login_session(c)
    r = c.put(API + "/symbols/NSE:SBIN", json={"flag": 3, "note": "private words"},
              headers=csrf_headers(c))  # fmt: skip
    assert r.status_code == 200
    flagged = ask(client, ref="flag:3")
    got = data_of(flagged)
    assert [r["symbol"] for r in got["rows"]] == ["SBIN"] and "list_key" not in got
    for ref in ("flag:3", f"list:{lid}"):
        text = ask(client, ref=ref).get_data(as_text=True)
        assert "private words" not in text and '"flag"' not in text and "note" not in text


def test_the_refs_own_version_moves_with_its_members_and_not_with_another_lists(client):
    from database import watchlist_db as oa

    a, _ = make_list(("SBIN",), "A")
    b, _ = make_list(("SBIN",), "B")
    first = {n: data_of(ask(client, ref=f"list:{n}"))["ref_version"] for n in (a, b)}
    assert first[a] == first[b]  # the same members: the same digest
    oa.add_item(OWNER, a, "NIFTY", "NSE_INDEX")
    oa.db_session.remove()
    after = {n: data_of(ask(client, ref=f"list:{n}"))["ref_version"] for n in (a, b)}
    assert after[a] != first[a] and after[b] == first[b]


def test_a_ranked_ref_is_refused_in_words(client):
    r = ask(client, ref="ranked:futures:INDEX:TOP_TRADED")
    assert r.status_code == 400 and r.get_json()["message"] == ranked_words.NO_ALERT
    assert r.get_json()["code"] == "invalid"


@pytest.mark.parametrize("ref", ["nope:1", "list", "list:", "list:abc", "flag:9", "list:1/x:2", 7])
def test_a_ref_that_is_not_one_is_invalid(client, ref):
    r = ask(client, ref=ref)
    assert r.status_code == 400 and r.get_json()["code"] == "invalid"


def test_a_deleted_or_unknown_list_is_gone(client):
    from database import watchlist_db as oa

    lid, _ = make_list()
    oa.delete_watchlist(OWNER, lid)
    oa.db_session.remove()
    for ref in (f"list:{lid}", "list:999", f"list:{lid}/section:1", "preset:nope"):
        r = ask(client, ref=ref)
        assert r.status_code == 404 and r.get_json()["code"] == "gone", ref


def test_with_the_switch_off_every_op_answers_off(client, monkeypatch):
    lid, _ = make_list()
    monkeypatch.setenv(contract.SWITCH_ENV, "off")
    for op, ref in (("members", f"list:{lid}"), ("version", None)):
        r = ask(client, op, ref)
        assert r.status_code == 503 and r.get_json()["code"] == "off"
    monkeypatch.setenv(contract.SWITCH_ENV, "on")
    assert ask(client, "version").status_code == 200


def test_a_master_that_is_not_loaded_is_failed_so_the_caller_changes_nothing(client, monkeypatch):
    lid, _ = make_list()
    monkeypatch.setattr(master, "openalgo_snapshot", lambda: None)
    master.shared().drop()
    r = ask(client, ref=f"list:{lid}")
    assert r.status_code == 500 and r.get_json()["code"] == "failed"


# -- the alerts service's own client -------------------------------------------------------------
def test_the_alerts_services_members_client_reads_this_route(app, monkeypatch):
    """The stub the alerts were built against cannot drift from the route: its real client reads
    the route through a test app and gets what its sync expects."""
    httpx = pytest.importorskip("httpx")
    members = pytest.importorskip("sudarshan.alerts.market.members")
    groups = pytest.importorskip("sudarshan.alerts.market.groups")
    monkeypatch.setattr(members.loopback, "refusal", lambda url: None)  # no real listener here
    served = app.test_client()

    class Flask(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            r = served.post(request.url.path, query_string=dict(request.url.params),
                            data=request.content, headers=dict(request.headers))  # fmt: skip
            return httpx.Response(r.status_code, content=r.get_data(), headers=dict(r.headers))

    lid, created = make_list(("SBIN",))
    follow(lid, "0", kind="month_future")
    follow(lid, str(NOW), kind="atm_option")

    async def go(function):
        async with httpx.AsyncClient(transport=Flask()) as client:
            source = members.OpenAlgoMembers("http://127.0.0.1:5000", client, fernet())
            return await function(source)

    got = asyncio.run(go(lambda s: s.members(f"list:{lid}")))
    assert got.state == groups.OK and got.list_key == created
    assert [r.symbol for r in got.rows] == ["NIFTY24NOV26FUT", "SBIN"]
    assert (
        got.rows[0].instrument_key == "NFO|NIFTY24NOV26FUT" and got.rows[0].valid_until == CLOSE_24
    )
    assert got.rows[1].tick_size == "0.05" and got.rows[1].valid_until is None
    assert [item["why"] for item in got.left_out] == ["atm"]
    assert groups.left_out_words(got.left_out)  # the words the card shows
    assert got.version == asyncio.run(go(lambda s: s.version())).version == versions.read()
    assert asyncio.run(go(lambda s: s.members("list:999"))).state == groups.GONE
    monkeypatch.setenv(contract.SWITCH_ENV, "off")
    assert asyncio.run(go(lambda s: s.members(f"list:{lid}"))).state == groups.OFF
    assert asyncio.run(go(lambda s: s.version())).state == groups.OFF
    monkeypatch.setenv(contract.SWITCH_ENV, "on")
    with pytest.raises(members.Unavailable):  # a ranked ref is refused, not read as a list
        asyncio.run(go(lambda s: s.members("ranked:futures:INDEX:TOP_TRADED")))


def test_the_route_is_discovered_not_registered_by_the_bridge_module():
    import ast
    from pathlib import Path

    source = Path(bp_module.__file__).read_text(encoding="utf-8")
    assert "internal" not in {n.id for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Name)}
    assert json.dumps(contract.INTERNAL_OPS[0].name) == '"members"'
