"""Built-in lists through the bridge (M8.8, W12): view, lists, symbol, the lock, copy, the sealed
route's reader and the exception's limits for the new code. The builders are in
test_watchlists_presets.py."""

from __future__ import annotations

import ast
import gc
import json
import threading
from datetime import datetime
from pathlib import Path

import pytest
from presets_master import V, index_of, indices_rows, ms, snapshot_of
from watchlists_bridge_app import OWNER, csrf_headers, login_session, make_app, reset_users

import blueprints.sudarshan_watchlists as bp_module
from sudarshan.watchlists import contract, engine, master, ops, presets, schema, versions
from sudarshan.watchlists.refs import preset as reader
from sudarshan.watchlists.words import presets as words

API = contract.API_PREFIX
ROOT = Path(__file__).resolve().parents[1]


def test_the_calendar_is_asked_only_when_an_expiry_falls_today(monkeypatch):
    asked = []
    monkeypatch.setattr(reader, "calendar_close", lambda iso: asked.append(iso) or 123)
    prepared = presets.entry("index_futures").prepare(index_of(V["master"]))
    assert reader.closes_for(prepared, ms("2026-11-10T10:00:00+05:30")) == {}
    assert asked == []
    assert reader.closes_for(prepared, ms("2026-11-24T10:00:00+05:30")) == {"2026-11-24": 123}
    assert (
        reader.closes_for(prepared, ms("2026-11-23T23:59:00+05:30")) == {}
    )  # IST's day, not UTC's


def test_a_calendar_with_no_window_leaves_the_default_close(monkeypatch):
    monkeypatch.setattr(reader, "calendar_close", lambda iso: None)
    prepared = presets.entry("index_futures").prepare(index_of(V["master"]))
    assert reader.closes_for(prepared, ms("2026-11-24T10:00:00+05:30")) == {}


class Box:
    def __init__(self):
        self.snap = None

    def __call__(self):
        return self.snap


@pytest.fixture
def box(monkeypatch, tmp_path):
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
    b = Box()
    monkeypatch.setattr(master, "openalgo_snapshot", b)
    monkeypatch.setattr(versions, "emit_from_any_thread", lambda *a, **k: None)
    shared = master.shared()
    shared.drop()
    gc.collect()
    yield b
    b.snap = None
    shared.drop()
    gc.collect()
    engine.dispose()
    versions.forget()
    bp_module._record.clear()


@pytest.fixture
def client(box):
    reset_users()
    c = make_app(ops.discover(), real=True).test_client()
    login_session(c)
    c.headers = csrf_headers(c)
    return c


def day(box, started=datetime(2026, 11, 9, 8, 40)):
    box.snap, _ = snapshot_of(V["master"] + indices_rows(), started)


def view(client, ident):
    return client.get(f"{API}/view?ref=preset:{ident}", headers=client.headers)


def test_the_view_of_the_fno_list_is_locked_and_carries_the_masters_date(client, box):
    day(box)
    r = view(client, "fno_stocks")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["ref"] == "preset:fno_stocks" and data["kind"] == "preset"
    assert data["locked"] is True and data["version"] == "0" and data["as_of"] == "2026-11-09"
    assert data["lines"][0] == "Built-in list, locked. F&O stocks: 4 symbols."
    assert data["lines"][1] == (
        "From the Upstox contract list of 9 Nov 2026: every stock with futures. "
        "Updated each morning after the day's login.")  # fmt: skip
    assert [r["contract"]["key"] for r in data["rows"]] == V["fno_stocks"]["rows"]
    assert data["missing"] == V["fno_stocks"]["missing"]
    assert data["sections"] == [] and data["follow"] == [] and data["total"] == 4
    assert all(r["item"] is None and r["section_id"] is None and not r["expired"]
               for r in data["rows"])  # fmt: skip
    assert data["rows"][3]["contract"]["isin"].startswith("INE")


def test_every_built_in_view_matches_the_contracts_view_record(client, box):
    day(box)
    want = [f.name for f in contract.record("View")]
    for e in presets.CATALOGUE:
        data = view(client, e.id).get_json()["data"]
        assert set(data) == set(want), e.id
        for row in data["rows"]:
            assert set(row) == {f.name for f in contract.record("Row")}
            assert set(row["contract"]) == {f.name for f in contract.record("Contract")}


def test_without_a_session_date_the_line_says_so(client, box):
    day(box, started=None)
    data = view(client, "fno_stocks").get_json()["data"]
    assert data["as_of"] is None
    assert data["lines"][1].startswith("From the Upstox contract list (date not recorded): ")


def test_the_view_of_indices_names_what_the_master_lacks(client, box):
    box.snap, _ = snapshot_of(
        indices_rows(skip=("NIFTYIT", "NIFTYMEDIA")), datetime(2026, 11, 9, 9)
    )
    data = view(client, "indices").get_json()["data"]
    assert data["total"] == 22 and data["missing"] == ["NSE_INDEX:NIFTYIT", "NSE_INDEX:NIFTYMEDIA"]
    assert data["lines"][-1] == (
        "2 names in this list are not in today's contract list: NIFTYIT, NIFTYMEDIA. "
        "The list is checked at each index review.")  # fmt: skip


def test_index_futures_follow_the_clock_and_the_calendar(client, box, monkeypatch):
    day(box)
    monkeypatch.setattr(reader, "calendar_close", lambda iso: None)
    for now, want in (("2026-11-24T15:29:59+05:30", "NIFTY24NOV26FUT"),
                      ("2026-11-24T15:30:00+05:30", "NIFTY29DEC26FUT")):  # fmt: skip
        monkeypatch.setattr(reader, "now_ms", lambda now=now: ms(now))
        data = view(client, "index_futures").get_json()["data"]
        assert data["rows"][0]["contract"]["symbol"] == want


def test_flags_and_notes_show_on_a_built_in_row_and_sort_prefs_are_kept(client, box):
    day(box)
    r = client.put(f"{API}/symbols/NSE:RELIANCE", json={"flag": 2, "note": "results"},
                   headers=client.headers)  # fmt: skip
    assert r.status_code == 200, r.get_json()
    r = client.put(f"{API}/prefs/preset:fno_stocks", json={"sort": "name", "descending": True},
                   headers=client.headers)  # fmt: skip
    assert r.status_code == 200, r.get_json()
    data = view(client, "fno_stocks").get_json()["data"]
    rel = next(x for x in data["rows"] if x["contract"]["symbol"] == "RELIANCE")
    assert rel["flag"] == 2 and rel["has_note"] is True
    assert "results" not in json.dumps(data), "a note is never sent"
    assert data["pref"]["sort"] == "name" and data["pref"]["descending"] is True
    held = client.get(f"{API}/symbol?k=NSE:RELIANCE", headers=client.headers).get_json()["data"]
    assert {"ref": "preset:fno_stocks", "name": "F&O stocks"} in held["lists"]
    other = client.get(f"{API}/symbol?k=NSE:NOFUTCO", headers=client.headers).get_json()["data"]
    assert other["lists"] == []


def test_the_lists_op_names_the_built_in_lists_with_counts_and_dates(client, box):
    got = client.get(f"{API}/lists", headers=client.headers).get_json()["data"]["presets"]
    assert [p["ref"] for p in got] == [f"preset:{e.id}" for e in presets.CATALOGUE]
    assert all(p["locked"] and p["kind"] == "preset" and p["count"] is None and p["note"] is None
               for p in got), "master not loaded: not known, never 0"  # fmt: skip
    day(box)
    got = {p["ref"]: p for p in client.get(f"{API}/lists", headers=client.headers)
           .get_json()["data"]["presets"]}  # fmt: skip
    assert got["preset:fno_stocks"]["count"] == 4 and got["preset:indices"]["count"] == 24
    assert got["preset:fno_stocks"]["note"] == "As of 9 Nov 2026"
    assert got["preset:fno_stocks"]["room"] is None


def test_a_view_without_the_master_says_what_to_do(client, box):
    r = view(client, "fno_stocks")
    assert r.status_code == 500 and r.get_json()["code"] == "failed"
    assert r.get_json()["message"] == words.NOT_LOADED
    assert "login" in words.NOT_LOADED and "HTTP" not in words.NOT_LOADED


def test_an_unknown_built_in_list_is_gone_in_words(client, box):
    day(box)
    r = view(client, "nope")
    assert r.status_code == 404 and r.get_json()["message"] == words.GONE


def test_a_preset_is_a_favourite_and_a_preference_target_only_when_it_exists(client, box):
    got = client.put(f"{API}/favourites", json={"slots": [{"slot": 1, "ref": "preset:indices"}],
                                                "version": 0}, headers=client.headers)  # fmt: skip
    assert got.status_code == 200, got.get_json()
    gone = client.put(f"{API}/prefs/preset:nope", json={"sort": "name"}, headers=client.headers)
    assert gone.status_code == 404


def make_list(name="Mine", symbols=()):
    from database import watchlist_db as oa

    made = oa.create_watchlist(OWNER, name, [{"symbol": s, "exchange": "NSE"} for s in symbols])
    oa.db_session.remove()
    return made["id"]


def copy(client, **body):
    return client.post(f"{API}/copy", json=body, headers=client.headers)


def test_a_move_out_of_a_built_in_list_is_refused_in_words_and_changes_nothing(client, box):
    day(box)
    r = copy(client, **{"from": {"ref": "preset:fno_stocks", "keys": ["NSE:RELIANCE"]},
                        "to": {"name": "Mine"}, "move": True})  # fmt: skip
    assert r.status_code == 400 and r.get_json()["code"] == "invalid"
    assert r.get_json()["message"] == words.LOCKED and r.get_json()["field"] == "from"
    assert "built-in list, locked" in words.LOCKED
    from database import watchlist_db as oa

    assert oa.db_session.query(oa.Watchlist).count() == 0
    oa.db_session.remove()


def test_no_write_op_can_be_pointed_at_a_built_in_list(client, box):
    """The writes take a list's number: a ref of a built-in list is not one, so it is refused
    before anything runs."""
    day(box)
    for op in contract.OPS:
        if op.cls != "write" or "<id>" not in op.route or op.name == "pref":
            continue
        path = API + op.route.replace("<id>", "preset:fno_stocks")
        r = client.open(path, method=op.method, json={"version": "0"}, headers=client.headers)
        assert r.status_code in (400, 404, 405), (op.name, r.status_code)
    for body in ({"list": "preset:fno_stocks", "name": "x", "version": "0"},):
        r = client.post(f"{API}/sections", json=body, headers=client.headers)
        assert r.status_code == 400
    r = copy(client, **{"from": {"ref": "list:0", "keys": []}, "to": {"list": "preset:indices"}})
    assert r.status_code == 400


def test_a_section_cannot_be_asked_of_a_built_in_source(client, box):
    day(box)
    r = copy(client, **{"from": {"ref": "preset:fno_stocks", "section_id": 1}, "to": {"name": "x"}})
    assert r.status_code == 400 and r.get_json()["message"] == words.NO_SECTION


def lists_named(name):
    from database import watchlist_db as oa

    try:
        w = oa.db_session.query(oa.Watchlist).filter_by(name=name).first()
        return (
            None
            if w is None
            else [(i.exchange, i.symbol) for i in sorted(w.items, key=lambda i: i.position)]
        )
    finally:
        oa.db_session.remove()


def test_copy_makes_an_openalgo_list_of_the_whole_built_in_list(client, box):
    day(box)
    r = copy(client, **{"from": {"ref": "preset:fno_stocks"}, "to": {"name": "F&O (my copy)"}})
    assert r.status_code == 200, r.get_json()
    done = r.get_json()["data"]
    assert done["added"] == 4 and done["skipped"] == 0 and done["words"] is None
    assert lists_named("F&O (my copy)") == [("NSE", "BAJAJ-AUTO"), ("NSE", "FARCO"),
                                            ("NSE", "M&M"), ("NSE", "RELIANCE")]  # fmt: skip
    assert done["lists_version"], "the copy bumped the lists version once"


def test_copy_of_picked_symbols_keeps_the_order_the_page_sent(client, box):
    day(box)
    r = copy(client, **{"from": {"ref": "preset:fno_stocks", "keys": ["NSE:RELIANCE", "NSE:FARCO"]},
                        "to": {"name": "Two"}})  # fmt: skip
    assert r.status_code == 200, r.get_json()
    assert lists_named("Two") == [("NSE", "RELIANCE"), ("NSE", "FARCO")]
    bad = copy(client, **{"from": {"ref": "preset:fno_stocks", "keys": ["NSE:NOFUTCO"]},
                          "to": {"name": "Bad"}})  # fmt: skip
    assert bad.status_code == 400 and bad.get_json()["field"] == "from"
    empty = copy(client, **{"from": {"ref": "preset:fno_stocks", "keys": []}, "to": {"name": "E"}})
    assert empty.status_code == 400


def test_copy_into_an_existing_list_skips_what_it_holds(client, box):
    day(box)
    lid = make_list("Mine", ["RELIANCE"])
    r = copy(client, **{"from": {"ref": "preset:fno_stocks"}, "to": {"list": lid}})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["added"] == 3 and r.get_json()["data"]["skipped"] == 1


def big_master(n):
    """A master whose F&O list is longer than a list can hold."""
    spec = []
    for i in range(n):
        name = f"STK{i:04d}"
        spec += [
            (name, "NSE", "EQ", None, None),
            (f"{name}24NOV26FUT", "NFO", "FUT", name, "24-NOV-26"),
        ]
    return spec


def test_copy_over_250_takes_the_first_250_in_the_order_shown_and_says_so(client, box):
    box.snap, _ = snapshot_of(big_master(300), datetime(2026, 11, 9, 8, 40))
    shown = [f"NSE:STK{i:04d}" for i in reversed(range(300))]  # the page sorted it the other way
    r = copy(client, **{"from": {"ref": "preset:fno_stocks", "keys": shown},
                        "to": {"name": "Top"}})  # fmt: skip
    assert r.status_code == 200, r.get_json()
    done = r.get_json()["data"]
    assert done["added"] == 250
    assert done["words"] == "Copied the first 250 of 300, in the order shown. A list holds 250."
    got = lists_named("Top")
    assert len(got) == 250 and got[0] == ("NSE", "STK0299") and got[-1] == ("NSE", "STK0050")
    whole = copy(client, **{"from": {"ref": "preset:fno_stocks"}, "to": {"name": "Whole"}})
    assert whole.get_json()["data"]["words"] == (
        "Copied the first 250 of 300, in the order shown. A list holds 250."
    )
    assert lists_named("Whole")[0] == ("NSE", "STK0000")


def test_a_list_over_250_says_how_prices_update(client, box):
    box.snap, _ = snapshot_of(big_master(300), datetime(2026, 11, 9, 8, 40))
    data = view(client, "fno_stocks").get_json()["data"]
    assert data["total"] == 300 and words.WINDOW in data["lines"]
    assert "120 rows around what you see" in words.WINDOW


def test_a_list_of_250_or_fewer_does_not_say_it(client, box):
    day(box)
    assert words.WINDOW not in view(client, "fno_stocks").get_json()["data"]["lines"]


def test_members_are_contracts_with_the_roll_instant_and_no_notes(client, box, monkeypatch):
    day(box)
    monkeypatch.setattr(reader, "calendar_close", lambda iso: None)
    monkeypatch.setattr(reader, "now_ms", lambda: ms("2026-11-10T10:00:00+05:30"))
    got = reader.members("index_futures", "owner")
    assert [m["key"] for m in got["members"]] == V["index_futures"][0]["rows"]
    assert got["valid_until"] == ms("2026-11-24T15:30:00+05:30")
    assert got["ref"] == "preset:index_futures" and got["version"] == versions.read()
    fno = reader.members("fno_stocks", "owner")
    assert fno["valid_until"] is None and fno["left_out"] == [
        {"token": "NSE:GHOSTCO", "reason": words.LEFT_OUT}]  # fmt: skip
    with pytest.raises(ops.OpError):
        reader.members("nope", "owner")


def test_the_lists_are_cached_with_the_generation_and_dropped_with_it(client, box):
    day(box)
    view(client, "fno_stocks")
    view(client, "indices")
    gen = master.shared().generation()
    first = reader.prepared_for(presets.entry("fno_stocks"), gen)
    assert reader.prepared_for(presets.entry("fno_stocks"), gen) is first, "built once"
    assert len(reader._prepared) == 1 and master.generations_alive() == 1
    box.snap, _ = snapshot_of(V["master"] + indices_rows(), datetime(2026, 11, 10, 8, 40))
    del gen, first
    view(client, "fno_stocks")
    gc.collect()
    assert len(reader._prepared) == 1 and master.generations_alive() == 1, "one generation"
    box.snap = None
    master.shared().generation()  # lapsed: the generation goes
    gc.collect()
    assert len(reader._prepared) == 0 and master.generations_alive() == 0


def test_a_prepared_list_holds_the_masters_own_rows_never_copies(client, box):
    box.snap, rows = snapshot_of(V["master"], datetime(2026, 11, 9, 8, 40))
    gen = master.shared().generation()
    ids = {id(r) for r in rows}
    for e in presets.CATALOGUE:
        p = reader.prepared_for(e, gen)
        assert all(id(r) in ids for r in p.rows)
        assert all(id(r) in ids for _, listed in p.futures for _, r in listed)


def test_the_old_rows_are_not_kept_alive_by_a_prepared_list(client, box):
    import weakref

    box.snap, rows = snapshot_of(V["master"], datetime(2026, 11, 9, 8, 40))
    view(client, "fno_stocks")
    probe = weakref.ref(next(r for r in rows if r.symbol == "RELIANCE" and r.exchange == "NSE"))
    del rows
    box.snap, _ = snapshot_of(V["master"], datetime(2026, 11, 10, 8, 40))
    view(client, "fno_stocks")
    gc.collect()
    assert probe() is None, "a prepared list kept a row of a master that was replaced"


def test_no_thread_starts_for_any_built_in_list(client, box):
    day(box)
    before = {t.ident for t in threading.enumerate()}
    for e in presets.CATALOGUE:
        view(client, e.id)
        reader.members(e.id, "owner")
    copy(client, **{"from": {"ref": "preset:indices"}, "to": {"name": "Idx"}})
    client.get(f"{API}/lists", headers=client.headers)
    assert {t.ident for t in threading.enumerate()} == before


def test_the_new_code_reaches_no_network_and_no_download():
    banned = {"requests", "httpx", "urllib", "urllib3", "socket", "ftplib", "http", "aiohttp",
              "subprocess", "asyncio", "threading", "concurrent"}  # fmt: skip
    files = [*(ROOT / "presets").glob("*.py"), ROOT / "refs" / "preset.py",
             ROOT / "ops" / "presets.py", ROOT / "words" / "presets.py"]  # fmt: skip
    assert len(files) >= 5
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                assert n.split(".")[0] not in banned, (path.name, n)


def test_the_switch_off_changes_nothing(client, box, monkeypatch):
    day(box)
    monkeypatch.setenv(contract.SWITCH_ENV, "off")
    for path in ("/view?ref=preset:fno_stocks", "/lists"):
        r = client.get(API + path, headers=client.headers)
        assert r.status_code == 503 and r.get_json()["code"] == "off", path
    r = copy(client, **{"from": {"ref": "preset:fno_stocks"}, "to": {"name": "x"}})
    assert r.status_code == 503 and lists_named("x") is None


def test_schema_has_no_new_table_for_built_in_lists():
    names = set(schema.Base.metadata.tables)
    assert not any("preset" in n for n in names), "a built-in list is made, never stored"
