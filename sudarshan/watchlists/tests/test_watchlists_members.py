"""The members of a ref (M8.14, W23): what each reader answers before the sealed route wraps it.
Real OpenAlgo lists and a small master of the tests' own; the clock and the calendar are typed."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from watchlists_bridge_app import OWNER, csrf_headers, login_session, make_app, reset_users
from watchlists_follow_master import build_master

import blueprints.sudarshan_watchlists as bp_module
from sudarshan.watchlists import contract, engine, master, ops, refs, versions, view
from sudarshan.watchlists.refs import flag as flag_reader
from sudarshan.watchlists.refs import list as reader
from sudarshan.watchlists.words import members as words

API = contract.API_PREFIX
NOW = 1_700_000_000_000
AT = int(
    __import__("datetime").datetime.fromisoformat("2026-11-20T11:00:00+05:30").timestamp() * 1000
)


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
    snap = build_master()
    monkeypatch.setattr(master, "openalgo_snapshot", lambda: snap)
    monkeypatch.setattr(sections_store, "now_ms", lambda: NOW)
    monkeypatch.setattr(versions, "emit_from_any_thread", lambda *a, **k: None)
    monkeypatch.setattr(reader, "now_ms", lambda: AT)
    monkeypatch.setattr(sessions, "market_open", lambda exchange, now: True)
    monkeypatch.setattr(sessions, "closes", lambda dates, exchange: {})
    master.shared().drop()
    yield
    master.shared().drop()
    engine.dispose()
    versions.forget()
    bp_module._record.clear()


@pytest.fixture
def client():
    reset_users()
    c = make_app(ops.discover(), real=True).test_client()
    login_session(c)
    c.headers = csrf_headers(c)
    return c


def v(n: int) -> str:
    return "0" if n == 0 else str(NOW + n - 1)


def make_list(items=("SBIN",), name="Swing"):
    from database import watchlist_db as oa

    made = oa.create_watchlist(OWNER, name, [
        {"symbol": s.rpartition(":")[2], "exchange": s.rpartition(":")[0] or "NSE"} for s in items
    ])  # fmt: skip
    created = oa.db_session.query(oa.Watchlist).filter_by(id=made["id"]).one().created_at
    oa.db_session.remove()
    return made["id"], view.created_key(created)


def row(client, lid, version, **more):
    body = {"list": lid, "version": version, "kind": "atm_option", "underlying": "NIFTY",
            "underlying_exchange": "NSE_INDEX", "opt_right": "CE", **more}  # fmt: skip
    r = client.post(API + "/rows", json=body, headers=client.headers)
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]["id"]


def section(client, lid, version, name="Hedge"):
    r = client.post(API + "/sections", json={"list": lid, "name": name, "version": version},
                    headers=client.headers)  # fmt: skip
    return r.get_json()["data"]["id"]


def keys(got):
    return [m["key"] for m in got["members"]]


def test_a_list_answers_its_symbols_in_order_with_its_identity_and_the_lists_version(client):
    lid, created = make_list(("SBIN", "NSE_INDEX:NIFTY"))
    got = reader.members(str(lid), OWNER)
    assert keys(got) == ["NSE:SBIN", "NSE_INDEX:NIFTY"] and got["left_out"] == []
    assert got["ref"] == f"list:{lid}" and got["created_at"] == created
    assert got["version"] == versions.read() and got["valid_until"] is None
    assert set(got["members"][0]) == {f.name for f in contract.record("Contract")}


def test_a_symbol_the_master_lacks_is_left_out_in_words(client):
    lid, _ = make_list(("SBIN", "OLDCO"))
    got = reader.members(str(lid), OWNER)
    assert keys(got) == ["NSE:SBIN"]
    assert got["left_out"] == [
        {"token": "NSE:OLDCO", "why": "missing", "reason": words.NOT_LISTED}]  # fmt: skip


def test_a_month_future_row_is_the_contract_valid_now_and_rolls_with_the_clock(client, monkeypatch):
    lid, _ = make_list(("SBIN",))
    row(client, lid, "0", kind="month_future", expiry_rule="nearest")
    got = reader.members(str(lid), OWNER)
    assert keys(got) == ["NFO:NIFTY24NOV26FUT", "NSE:SBIN"]  # follow rows first, as the page shows
    assert got["members"][0]["expiry"] == "2026-11-24"
    monkeypatch.setattr(reader, "now_ms", lambda: AT + 5 * 86_400_000)  # after the 24th's close
    assert keys(reader.members(str(lid), OWNER))[0] == "NFO:NIFTY01DEC26FUT"


def test_atm_and_strike_rows_are_left_out_each_with_its_words(client):
    lid, _ = make_list(("SBIN",))
    row(client, lid, "0")
    row(client, lid, v(1), kind="strike_pair")
    row(client, lid, v(2), kind="strike_pair", strike_rule="fixed", strike=25900, opt_right=None)
    got = reader.members(str(lid), OWNER)
    assert keys(got) == ["NSE:SBIN"]
    assert [(c["why"], c["token"]) for c in got["left_out"]] == [
        ("atm", "NIFTY"), ("atm", "NIFTY"), ("strike", "NIFTY")]  # fmt: skip
    assert got["left_out"][0]["reason"] == words.FOLLOW_AT_THE_MONEY
    assert got["left_out"][2]["reason"] == words.FOLLOW_STRIKE
    assert "changes contract during the day" in words.FOLLOW_AT_THE_MONEY + words.FOLLOW_STRIKE


def test_a_section_ref_holds_only_that_sections_rows(client):
    lid, created = make_list(("SBIN", "NSE_INDEX:NIFTY"))
    sid = section(client, lid, "0")
    client.put(f"{API}/lists/{lid}/arrange", headers=client.headers, json={
        "version": v(1), "sections": [sid],
        "items": [{"exchange": "NSE_INDEX", "symbol": "NIFTY", "section_id": sid},
                  {"exchange": "NSE", "symbol": "SBIN", "section_id": None}]})  # fmt: skip
    row(client, lid, v(2), kind="month_future", section_id=sid)
    got = reader.members(f"{lid}/section:{sid}", OWNER)
    assert keys(got) == ["NFO:NIFTY24NOV26FUT", "NSE_INDEX:NIFTY"]
    assert got["ref"] == f"list:{lid}/section:{sid}" and got["created_at"] == created
    assert keys(reader.members(str(lid), OWNER)) == [
        "NSE:SBIN",
        "NFO:NIFTY24NOV26FUT",
        "NSE_INDEX:NIFTY",
    ]


@pytest.mark.parametrize(
    "ident",
    [
        "7/section",
        "7/sections:1",
        "7/section:",
        "7/section:x",
        "7/section:0",
        "x/section:1",
        "7/section:1/x",
    ],
)
def test_a_badly_written_section_ref_is_invalid(ident):
    with pytest.raises(ops.OpError) as refused:
        reader.members(ident, OWNER)
    assert refused.value.code == "invalid" and refused.value.field == "ref"


def test_a_list_or_section_that_is_not_there_is_gone(client):
    lid, _ = make_list()
    for ident in ("999", f"{lid}/section:5"):
        with pytest.raises(ops.OpError) as refused:
            reader.members(ident, OWNER)
        assert refused.value.code == "gone"


def test_a_reused_list_id_does_not_carry_the_old_lists_sections(client):
    lid, _ = make_list()
    sid = section(client, lid, "0")
    from database import watchlist_db as oa

    oa.db_session.query(oa.Watchlist).filter_by(id=lid).update({"created_at": datetime(2001, 1, 1)})
    oa.db_session.commit()
    oa.db_session.remove()
    with pytest.raises(
        ops.OpError
    ) as refused:  # the overlay is another list's: its section is gone
        reader.members(f"{lid}/section:{sid}", OWNER)
    assert refused.value.code == "gone"


def test_the_master_not_loaded_says_failed_for_a_list_with_a_future_row(client, monkeypatch):
    lid, _ = make_list()
    row(client, lid, "0", kind="month_future")
    monkeypatch.setattr(master, "openalgo_snapshot", lambda: None)
    master.shared().drop()
    with pytest.raises(ops.OpError) as refused:
        reader.members(str(lid), OWNER)
    assert refused.value.code == "failed"


def test_a_flag_list_answers_its_contracts_and_never_a_note(client):
    lid, _ = make_list(("SBIN", "OLDCO"))
    for key in ("NSE:SBIN", "NSE:OLDCO"):
        r = client.put(f"{API}/symbols/{key}", json={"flag": 2, "note": "private words"},
                       headers=client.headers)  # fmt: skip
        assert r.status_code == 200
    got = flag_reader.members("2", OWNER)
    assert got["ref"] == "flag:2" and got["version"] == versions.read()
    assert set(keys(got)) == {"NSE:SBIN"}
    assert got["left_out"][0]["why"] == "missing" and got["left_out"][0]["token"] == "NSE:OLDCO"
    assert "private words" not in json.dumps(got)
    with pytest.raises(ops.OpError):
        flag_reader.members("6", OWNER)


def test_a_list_answer_never_holds_a_flag_or_a_note(client):
    lid, _ = make_list(("SBIN",))
    client.put(f"{API}/symbols/NSE:SBIN", json={"flag": 1, "note": "private words"},
               headers=client.headers)  # fmt: skip
    text = json.dumps(reader.members(str(lid), OWNER))
    assert "private words" not in text and '"flag"' not in text and "has_note" not in text


def test_every_reader_builds_members_now():
    for module in refs.readers().values():
        assert module.members.__doc__
