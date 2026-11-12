"""arrange, copy and move (M8.4, W8), through the real bridge, with OpenAlgo's lists in their own
database and the overlay in a throw-away file."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from watchlists_bridge_app import OWNER, csrf_headers, login_session, make_app, reset_users

import blueprints.sudarshan_watchlists as bp_module
from sudarshan.watchlists import contract, engine, master, ops, versions, view
from sudarshan.watchlists.ops import copy as copy_ops
from sudarshan.watchlists.words import arrange as words
from sudarshan.watchlists.words import sections as section_words

API = contract.API_PREFIX
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    from database import watchlist_db as oa

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
    snap = SimpleNamespace(by_symbol_exchange={})
    monkeypatch.setattr(master, "openalgo_snapshot", lambda: snap)
    yield
    engine.dispose()
    versions.forget()
    bp_module._record.clear()


@pytest.fixture(autouse=True)
def emitted(monkeypatch):
    sent = []
    monkeypatch.setattr(versions, "emit_from_any_thread", lambda e, data, **kw: sent.append(data))
    return sent


@pytest.fixture
def client():
    reset_users()
    c = make_app(ops.discover(), real=True).test_client()
    login_session(c)
    c.headers = csrf_headers(c)
    return c


def make_list(symbols=("INFY", "TCS", "SBIN", "ITC"), name="Swing"):
    from database import watchlist_db as oa

    made = oa.create_watchlist(OWNER, name, [{"symbol": s, "exchange": "NSE"} for s in symbols])
    oa.db_session.remove()
    return made["id"]


def oa_order(lid):
    from database import watchlist_db as oa

    try:
        (found,) = [w for w in oa.get_watchlists(OWNER) if w["id"] == lid]
    finally:
        oa.db_session.remove()
    return [i["symbol"] for i in sorted(found["items"], key=lambda i: (i["position"], i["id"]))]


def oa_lists():
    from database import watchlist_db as oa

    try:
        return {w["name"]: w["id"] for w in oa.get_watchlists(OWNER)}
    finally:
        oa.db_session.remove()


def sec(client, lid, name, version):
    body = {"list": lid, "name": name, "version": version}
    r = client.post(API + "/sections", json=body, headers=client.headers)
    return r.get_json()["data"]["id"]


def read(client, lid):
    r = client.get(API + "/view", query_string={"ref": f"list:{lid}"})
    return r.get_json()["data"]


def arrange(client, lid, version, sections, items):
    body = {"version": version, "sections": sections, "items": items}
    return client.put(f"{API}/lists/{lid}/arrange", json=body, headers=client.headers)


def put(client, body):
    return client.post(API + "/copy", json=body, headers=client.headers)


def pick(symbol, section_id=None):
    return {"exchange": "NSE", "symbol": symbol, "section_id": section_id}


def table(name):
    with engine.open_session() as s:
        return [tuple(r) for r in s.execute(text(f"SELECT * FROM {name} ORDER BY 1, 2")).all()]


def refused(r, status, code, field=None):
    body = r.get_json()
    assert (r.status_code, body["code"], body.get("field")) == (status, code, field), body
    return body["message"]


def two_sections(client):
    lid = make_list()
    a, b = sec(client, lid, "Banks", "0"), sec(client, lid, "IT", "1")
    return lid, a, b  # the list is at version 2


# -- arrange -------------------------------------------------------------------------------------
def test_arrange_writes_the_overlay_then_openalgo_in_section_major_order(client):
    lid, banks, it = two_sections(client)
    items = [pick("TCS", it), pick("SBIN", banks), pick("INFY", it), pick("ITC")]
    r = arrange(client, lid, "2", [it, banks], items)
    assert r.get_json()["data"] == {"version": "3", "id": lid, "words": None}
    assert oa_order(lid) == ["ITC", "TCS", "INFY", "SBIN"]  # no section, IT, Banks
    data = read(client, lid)
    assert data["version"] == "3"
    assert [(s["id"], s["position"]) for s in data["sections"]] == [(it, 1), (banks, 2)]
    assert [(r["contract"]["symbol"], r["section_id"]) for r in data["rows"]] == [
        ("ITC", None), ("TCS", it), ("INFY", it), ("SBIN", banks)]  # fmt: skip


def test_symbols_the_request_leaves_out_keep_their_section_and_go_after(client):
    lid, banks, it = two_sections(client)
    arrange(client, lid, "2", [banks, it], [pick("TCS", it), pick("INFY", banks)])
    again = arrange(client, lid, "3", [banks, it], [pick("SBIN", banks), pick("GHOST", it)])
    assert again.status_code == 200
    rows = [(r["contract"]["symbol"], r["section_id"]) for r in read(client, lid)["rows"]]
    assert rows == [("ITC", None), ("SBIN", banks), ("INFY", banks), ("TCS", it)]
    assert oa_order(lid) == ["ITC", "SBIN", "INFY", "TCS"]


def test_a_stale_version_writes_nothing_to_either_file(client, emitted):
    lid, banks, it = two_sections(client)
    before = (table("wl_list"), table("wl_section"), table("wl_section_item"), oa_order(lid))
    emitted.clear()
    r = arrange(client, lid, "1", [it, banks], [pick("TCS", it)])
    assert refused(r, 409, "conflict") == section_words.CONFLICT
    assert (
        table("wl_list"),
        table("wl_section"),
        table("wl_section_item"),
        oa_order(lid),
    ) == before
    assert emitted == []


def test_sections_and_items_that_do_not_match_the_list_are_refused_and_roll_back(client):
    lid, banks, it = two_sections(client)
    before = (table("wl_list"), table("wl_section"))
    refused(arrange(client, lid, "2", [banks], []), 400, "invalid", "sections")
    refused(arrange(client, lid, "2", [banks, it, 99], []), 400, "invalid", "sections")
    refused(arrange(client, lid, "2", [banks, banks], []), 400, "invalid", "sections")
    refused(arrange(client, lid, "2", [banks, it], [pick("TCS", 99)]), 400, "invalid", "items")
    assert (table("wl_list"), table("wl_section")) == before
    refused(arrange(client, 999, "0", [], []), 404, "gone")


def test_a_failure_between_the_two_writes_says_so_and_leaves_a_valid_state(client, monkeypatch):
    from database import watchlist_db as oa

    lid, banks, it = two_sections(client)
    flat = oa_order(lid)
    with monkeypatch.context() as patch:
        patch.setattr(oa, "reorder_items", lambda *a: False)
        r = arrange(client, lid, "2", [it, banks], [pick("TCS", it), pick("SBIN", banks)])
    assert refused(r, 500, "failed") == words.ARRANGE_PARTIAL
    assert words.ARRANGE_PARTIAL == (
        "Saved here, but the order on /trading is unchanged until you arrange the list again."
    )
    assert oa_order(lid) == flat
    data = read(client, lid)  # the overlay is saved and the view is whole
    assert data["version"] == "3" and [s["id"] for s in data["sections"]] == [it, banks]
    assert arrange(client, lid, "3", [it, banks], [pick("TCS", it)]).status_code == 200


def test_arrange_bumps_once_with_why_arranged_and_its_ref(client, emitted):
    lid, banks, it = two_sections(client)
    emitted.clear()
    arrange(client, lid, "2", [banks, it], [pick("TCS", it)])
    assert [(p["why"], p["ref"]) for p in emitted] == [("arranged", f"list:{lid}")]


# -- copy and move -------------------------------------------------------------------------------
def test_copy_into_another_list_adds_in_order_and_keeps_the_source(client):
    src, dst = make_list(), make_list(("HDFCBANK",), "Banks")
    r = put(client, {"from": {"ref": f"list:{src}", "keys": ["NSE:SBIN", "NSE:INFY"]},
                     "to": {"list": dst}})  # fmt: skip
    assert r.get_json()["data"] == {
        "list": dst, "added": 2, "skipped": 0, "version": None, "words": None}  # fmt: skip
    assert oa_order(dst) == ["HDFCBANK", "SBIN", "INFY"]
    assert oa_order(src) == ["INFY", "TCS", "SBIN", "ITC"]


def test_copy_a_whole_section_into_a_section_of_another_list(client):
    src, banks, it = two_sections(client)
    arrange(client, src, "2", [banks, it], [pick("TCS", it), pick("INFY", it), pick("SBIN", banks)])
    dst = make_list(("HDFCBANK",), "Other")
    mine = sec(client, dst, "Picked", "0")  # the list is at 1
    r = put(client, {"from": {"ref": f"list:{src}", "section_id": it},
                     "to": {"list": dst, "section_id": mine}, "version": "1"})  # fmt: skip
    assert r.get_json()["data"]["added"] == 2 and r.get_json()["data"]["version"] == "2"
    rows = [(r["contract"]["symbol"], r["section_id"]) for r in read(client, dst)["rows"]]
    assert rows == [("HDFCBANK", None), ("TCS", mine), ("INFY", mine)]


def test_a_stale_version_for_the_target_section_writes_nothing(client):
    src, dst = make_list(), make_list(("HDFCBANK",), "Other")
    mine = sec(client, dst, "Picked", "0")
    before = (table("wl_list"), table("wl_section_item"), oa_order(dst))
    r = put(client, {"from": {"ref": f"list:{src}", "keys": ["NSE:TCS"]},
                     "to": {"list": dst, "section_id": mine}, "version": "0"})  # fmt: skip
    refused(r, 409, "conflict")
    assert (table("wl_list"), table("wl_section_item"), oa_order(dst)) == before
    refused(put(client, {"from": {"ref": f"list:{src}", "keys": ["NSE:TCS"]},
                         "to": {"list": dst, "section_id": mine}}), 400, "invalid", "version")  # fmt: skip


def test_copy_into_a_new_list_in_one_call(client, emitted):
    src = make_list()
    emitted.clear()
    r = put(client, {"from": {"ref": f"list:{src}", "keys": ["NSE:ITC", "NSE:TCS"]},
                     "to": {"name": "Fresh"}})  # fmt: skip
    data = r.get_json()["data"]
    assert (data["added"], data["skipped"]) == (2, 0) and data["list"] == oa_lists()["Fresh"]
    assert oa_order(data["list"]) == ["ITC", "TCS"]  # the pick's order
    assert [(p["why"], p["ref"]) for p in emitted] == [("items", f"list:{data['list']}")]
    refused(put(client, {"from": {"ref": f"list:{src}", "keys": ["NSE:ITC"]},
                         "to": {"name": "Fresh"}}), 400, "invalid", "name")  # fmt: skip


def test_move_adds_then_removes_from_the_source_and_drops_its_section_rows(client, emitted):
    src, banks, it = two_sections(client)
    arrange(client, src, "2", [banks, it], [pick("TCS", it), pick("SBIN", banks)])
    dst = make_list(("SBIN",), "Other")
    emitted.clear()
    r = put(client, {"from": {"ref": f"list:{src}", "keys": ["NSE:SBIN", "NSE:TCS"]},
                     "to": {"list": dst}, "move": True})  # fmt: skip
    data = r.get_json()["data"]
    assert (data["added"], data["skipped"]) == (1, 1)  # SBIN was there already
    assert oa_order(dst) == ["SBIN", "TCS"] and oa_order(src) == ["INFY", "ITC"]
    assert table("wl_section_item") == []
    assert [(p["why"], p["ref"]) for p in emitted] == [("items", "lists")]  # two refs: the lists


def test_a_whole_section_can_be_moved_into_a_new_list(client):
    src, banks, it = two_sections(client)
    arrange(client, src, "2", [banks, it], [pick("TCS", it), pick("INFY", it)])
    r = put(client, {"from": {"ref": f"list:{src}", "section_id": it},
                     "to": {"name": "IT"}, "move": True})  # fmt: skip
    assert r.get_json()["data"]["added"] == 2
    assert oa_order(src) == ["SBIN", "ITC"] and oa_order(oa_lists()["IT"]) == ["TCS", "INFY"]


def test_other_sources_targets_and_picks_are_refused_in_place(client):
    src = make_list()
    ref = {"ref": f"list:{src}"}
    for source in (
        {"ref": "preset:nifty", "keys": ["NSE:INFY"]},
        {"ref": "ranked:x", "keys": []},
        {"ref": "flag:1", "keys": []},
        {"ref": "list:abc", "keys": []},
        {**ref, "keys": ["NSE:GHOST"]},
        {**ref},
        {**ref, "keys": [], "section_id": 1},
    ):
        refused(put(client, {"from": source, "to": {"name": "X"}}), 400, "invalid", "from")
    refused(put(client, {"from": {**ref, "keys": ["NSE:INFY"]}, "to": {}}), 400, "invalid", "to")
    refused(put(client, {"from": {**ref, "keys": ["NSE:INFY"]}, "to": {"list": src}}),
            400, "invalid", "to")  # fmt: skip
    refused(put(client, {"from": {**ref, "section_id": 5}, "to": {"name": "X"}}), 404, "gone")
    refused(put(client, {"from": {**ref, "keys": ["NSE:INFY"]}, "to": {"list": 999}}), 404, "gone")


# -- the limits ----------------------------------------------------------------------------------
def many(n, prefix="S"):
    return [f"{prefix}{i:03d}" for i in range(n)]


def test_a_list_holds_250_and_the_words_carry_the_numbers(client):
    src = make_list(many(8), "Source")
    dst = make_list(many(247, "T"), "Swing")
    r = put(client, {"from": {"ref": f"list:{src}", "keys": [f"NSE:{s}" for s in many(8)]},
                     "to": {"list": dst}})  # fmt: skip
    said = refused(r, 422, "limit")
    assert said == (
        "Swing has room for 3 more symbols; you picked 8. Make room in Swing or pick another list."
    )
    assert len(oa_order(dst)) == 247  # nothing was written
    r = put(client, {"from": {"ref": f"list:{src}", "keys": [f"NSE:{s}" for s in many(3)]},
                     "to": {"list": dst}})  # fmt: skip
    assert r.status_code == 200 and len(oa_order(dst)) == 250
    assert words.room("Swing", 1, 2, False).startswith("Swing has room for 1 more symbol; ")
    assert words.room("Swing", 0, 1, True).startswith(
        "Swing has room for 0 more symbols; this section has 1"
    )


def test_the_section_wording_and_a_pick_over_250_for_a_new_list(client):
    assert words.room("Swing", 3, 8, True) == (
        "Swing has room for 3 more symbols; this section has 8. "
        "Make room in Swing or pick another list."
    )
    src = make_list(many(250), "Source")
    r = put(client, {"from": {"ref": f"list:{src}", "keys": [f"NSE:{s}" for s in many(250)]},
                     "to": {"name": "Copy"}})  # fmt: skip
    assert r.status_code == 200 and len(oa_order(oa_lists()["Copy"])) == 250


def test_fifty_lists_is_the_most_and_a_new_list_says_so(client):
    src = make_list(("INFY",), "L0")
    for n in range(1, 50):
        make_list((), f"L{n}")
    assert len(oa_lists()) == 50
    r = put(client, {"from": {"ref": f"list:{src}", "keys": ["NSE:INFY"]}, "to": {"name": "One"}})
    said = refused(r, 422, "limit")
    assert said == "You have 50 lists, the most there can be. Delete one to make a new list."
    assert len(oa_lists()) == 50


def test_a_duplicate_symbol_in_the_target_is_not_added_twice_and_is_no_error(client):
    src = make_list()
    dst = make_list(("TCS", "INFY"), "Other")
    r = put(client, {"from": {"ref": f"list:{src}", "keys": ["NSE:INFY", "NSE:TCS", "NSE:ITC"]},
                     "to": {"list": dst}})  # fmt: skip
    data = r.get_json()["data"]
    assert (data["added"], data["skipped"]) == (1, 2)
    assert oa_order(dst) == ["TCS", "INFY", "ITC"]


# -- the two files -------------------------------------------------------------------------------
def test_a_failed_add_says_how_far_it_got_and_a_move_does_not_remove(client, monkeypatch):
    from database import watchlist_db as oa

    src = make_list()
    dst = make_list((), "Other")
    real, seen = oa.add_item, []

    def flaky(user, lid, symbol, exchange):
        seen.append(symbol)
        return None if len(seen) == 3 else real(user, lid, symbol, exchange)

    monkeypatch.setattr(oa, "add_item", flaky)
    r = put(client, {"from": {"ref": f"list:{src}", "keys": [f"NSE:{s}" for s in ("INFY", "TCS", "SBIN")]},
                     "to": {"list": dst}, "move": True})  # fmt: skip
    assert refused(r, 500, "failed") == words.added_some(2, 3, "Other")
    assert oa_order(dst) == ["INFY", "TCS"] and len(oa_order(src)) == 4


def test_a_failure_setting_the_section_leaves_the_symbols_in_no_section(client, monkeypatch):
    src = make_list()
    dst = make_list(("HDFCBANK",), "Other")
    mine = sec(client, dst, "Picked", "0")

    def boom(*a, **k):
        raise RuntimeError("disk")

    with monkeypatch.context() as patch:
        patch.setattr(copy_ops.store_items, "put", boom)
        r = put(client, {"from": {"ref": f"list:{src}", "keys": ["NSE:TCS"]},
                         "to": {"list": dst, "section_id": mine}, "version": "1"})  # fmt: skip
    assert refused(r, 500, "failed") == words.section_lost("Other")
    data = read(client, dst)
    assert [(r["contract"]["symbol"], r["section_id"]) for r in data["rows"]] == [
        ("HDFCBANK", None), ("TCS", None)]  # fmt: skip
    assert data["version"] == "1"  # the claim rolled back with the rows


def test_a_failed_removal_says_what_stayed_in_the_source(client, monkeypatch):
    from database import watchlist_db as oa

    src, dst = make_list(), make_list((), "Other")
    monkeypatch.setattr(oa, "remove_item", lambda *a: False)
    r = put(client, {"from": {"ref": f"list:{src}", "keys": ["NSE:TCS", "NSE:ITC"]},
                     "to": {"list": dst}, "move": True})  # fmt: skip
    assert refused(r, 500, "failed") == words.not_removed(2, "Other", "Swing")
    assert oa_order(dst) == ["TCS", "ITC"] and len(oa_order(src)) == 4


# -- the yield -----------------------------------------------------------------------------------
def test_writes_yield_to_the_hub_between_items(monkeypatch):
    src = make_list(many(40), "Source")
    dst = make_list((), "Other")
    pauses = []
    monkeypatch.setattr(copy_ops.time, "sleep", lambda s: pauses.append(s))
    keys = [("NSE", s) for s in many(40)]
    assert copy_ops.add_all(OWNER, dst, keys) == 40
    assert pauses == [0] * 40
    from database import watchlist_db as oa

    ids = [i["id"] for w in oa.get_watchlists(OWNER) if w["id"] == src for i in w["items"]]
    oa.db_session.remove()
    pauses.clear()
    assert copy_ops.remove_all(OWNER, src, ids) == 40 and pauses == [0] * 40


EVENTLET = """
import eventlet
eventlet.monkey_patch()
import os, sys, tempfile
tmp = tempfile.mkdtemp()  # noqa
os.environ.update(API_KEY_PEPPER="f" * 64, APP_KEY="test-only-app-key-" + "0" * 32,
                  DATABASE_URL="sqlite:///" + tmp + "/oa.db", LOG_DIR=tmp + "/log")
sys.path.insert(0, {root!r})
from database import watchlist_db as oa
from sudarshan.watchlists.ops import copy as c
oa.Base.metadata.create_all(oa.engine)
made = oa.create_watchlist("u", "Target", [])
if {silenced}:
    c.time.sleep = lambda s: None
ticks = [0]
def other():
    while True:
        ticks[0] += 1
        eventlet.sleep(0)
eventlet.spawn(other)
eventlet.sleep(0)
before = ticks[0]
added = c.add_all("u", made["id"], [("NSE", f"S{{i:03d}}") for i in range(250)])
print("RESULT", added, ticks[0] - before)
"""


def run_eventlet(silenced: bool) -> tuple[int, int]:
    code = textwrap.dedent(EVENTLET).format(root=str(ROOT), silenced=silenced)
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          timeout=120, cwd=ROOT)  # fmt: skip
    line = next(ln for ln in done.stdout.splitlines() if ln.startswith("RESULT"))
    _, added, ticks = line.split()
    return int(added), int(ticks)


def test_a_second_greenlet_runs_during_a_250_item_copy_under_eventlet():
    pytest.importorskip("eventlet", reason="eventlet is installed by the production installer")
    added, ticks = run_eventlet(silenced=False)
    assert added == 250 and ticks >= 240
    # the defect itself, so the proof cannot pass vacuously: without the yield nothing else runs
    added, ticks = run_eventlet(silenced=True)
    assert added == 250 and ticks == 0


def test_the_views_guard_still_reads_what_these_ops_wrote(client):
    lid, banks, it = two_sections(client)
    arrange(client, lid, "2", [it, banks], [pick("TCS", it)])
    with engine.open_session() as s:
        guard = s.execute(text("SELECT oa_created_at FROM wl_list")).scalar()
    from database import watchlist_db as oa

    created = oa.db_session.query(oa.Watchlist).filter_by(id=lid).one().created_at
    oa.db_session.remove()
    assert guard == view.created_key(created)
