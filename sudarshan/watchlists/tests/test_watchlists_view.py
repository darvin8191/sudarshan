"""Own lists and ``view`` (M8.4, W6): the merge against hand-typed vectors, the guard, the refs
interface and the op through the real bridge."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from watchlists_bridge_app import OWNER, csrf_headers, login_session, make_app, reset_users

import blueprints.sudarshan_watchlists as bp_module
from sudarshan.watchlists import contract, engine, master, ops, refs, schema, view
from sudarshan.watchlists.refs import list as list_reader
from sudarshan.watchlists.words import view as words

V = json.loads((Path(__file__).parent / "vectors" / "view.json").read_text(encoding="utf-8"))
NOW = 1_700_000_000_000
URL = contract.API_PREFIX + "/view"


def master_row(spec):
    return SimpleNamespace(**spec)


def split(key):
    exchange, _, symbol = key.partition(":")
    return exchange, symbol


def tuple_keys(raw):
    return {split(k): v for k, v in raw.items()}


def run(case):
    rows = {split(k): master_row(v) for k, v in V["master"].items()}
    overlay = dict(case["overlay"])
    if "items" in overlay:
        overlay["items"] = tuple_keys(overlay["items"])
    if "symbols" in overlay:
        overlay["symbols"] = {k: tuple(v) for k, v in tuple_keys(overlay["symbols"]).items()}
    live = dict(case["live"], items=[tuple(i) for i in case["live"]["items"]])
    return view.merge(live, overlay, lambda e, s: rows.get((e, s)),
                      loaded=case.get("loaded", True), isin=master.Master.isin)  # fmt: skip


@pytest.mark.parametrize("case", V["cases"], ids=lambda c: c["name"][:60])
def test_the_merge_against_the_vectors(case):
    got, want = run(case), case["expect"]
    assert [[r["contract"]["key"], r["section_id"]] for r in got["rows"]] == want["rows"]
    assert [
        [s["id"], s["name"], s["mark"], s["position"], s["count"]] for s in got["sections"]
    ] == (want["sections"])
    assert [[f["id"], f["section_id"]] for f in got["follow"]] == want["follow"]
    assert got["total"] == want["total"] and got["version"] == want["version"]
    assert got["pref"] == want["pref"]
    assert got["ref"] == "list:7" and got["kind"] == "list" and got["name"] == "Swing"
    assert got["locked"] is False and got["as_of"] is None and got["lines"] == []
    by_key = {r["contract"]["key"]: r for r in got["rows"]}
    assert got["missing"] == want.get("missing", [])
    for key, flag in want.get("flags", {}).items():
        assert by_key[key]["flag"] == flag
    for key, noted in want.get("notes", {}).items():
        if key in by_key:
            assert by_key[key]["has_note"] is noted
    for key, expired in want.get("expired", {}).items():
        assert by_key[key]["expired"] is expired
    for key, isin in want.get("isin", {}).items():
        assert by_key[key]["contract"]["isin"] == isin
    for key, fields in want.get("contracts", {}).items():
        assert {k: by_key[key]["contract"][k] for k in fields} == fields
    for fid, fields in want.get("follow_detail", {}).items():
        found = next(f for f in got["follow"] if f["id"] == int(fid))
        assert {k: found[k] for k in fields} == fields


def test_the_merge_matches_the_contracts_view_record_field_for_field():
    got = run(V["cases"][0])
    assert set(got) == {f.name for f in contract.record("View")}
    assert set(got["rows"][0]) == {f.name for f in contract.record("Row")}
    assert set(got["rows"][0]["contract"]) == {f.name for f in contract.record("Contract")}
    assert set(got["sections"][0]) == {f.name for f in contract.record("Section")}
    follow = run(V["cases"][2])["follow"][0]
    assert set(follow) == {f.name for f in contract.record("FollowRow")}
    assert set(got["pref"]) == {f.name for f in contract.record("Pref")}


def test_the_merge_does_not_change_what_it_was_given():
    case = V["cases"][2]
    before = json.dumps(case, sort_keys=True)
    run(case)
    assert json.dumps(case, sort_keys=True) == before


def test_created_key_is_the_text_sqlite_writes():
    from datetime import datetime

    assert view.created_key(datetime(2026, 11, 10, 9, 0, 0)) == "2026-11-10 09:00:00"
    assert view.created_key(datetime(2026, 11, 10, 9, 0, 0, 5)) == "2026-11-10 09:00:00.000005"
    assert view.created_key(None) == "" and view.created_key("x") == "x"


# -- the refs interface --------------------------------------------------------------------------
def test_the_refs_package_finds_the_list_reader_and_nothing_unbuilt():
    assert set(refs.readers()) == {"list"}
    assert refs.reader_of("list:7")[1] == "7"
    for ref in (
        "preset:nifty",
        "flag:2",
        "ranked:futures:INDEX:TOP_TRADED",
        "nope:1",
        "list",
        "list:",
    ):
        assert refs.reader_of(ref) is None, ref


def test_the_readers_are_found_once_and_a_view_lists_and_imports_nothing(monkeypatch, client):
    """Review L14: the directory was listed and every reader imported on every view request."""
    from types import SimpleNamespace

    assert set(refs.readers()) == {"list"}  # found and kept before the two go away
    monkeypatch.setattr(
        refs, "pkgutil", SimpleNamespace(iter_modules=lambda *a: pytest.fail("listed"))
    )
    monkeypatch.setattr(
        refs, "importlib", SimpleNamespace(import_module=lambda *a: pytest.fail("imported"))
    )
    assert refs.reader_of("list:7")[1] == "7" and refs.readers() is refs.readers()
    lid, _ = make_list()
    assert get(client, f"list:{lid}").status_code == 200


def test_every_reader_gives_the_interface_and_members_is_documented_not_built():
    for kind, module in refs.readers().items():
        assert module.KIND == kind and callable(module.view) and callable(module.members)
        assert module.members.__doc__
        with pytest.raises(NotImplementedError):
            module.members("1", OWNER)


@pytest.mark.parametrize("ident", ["", "0", "-1", "abc", "1.5", " 7", "7 ", "99999999999", "٣"])
def test_a_list_id_must_be_a_whole_number(ident):
    with pytest.raises(ops.OpError) as refused:
        list_reader.list_id(ident)
    assert refused.value.code == "invalid" and refused.value.field == "ref"


# -- the op through the bridge -------------------------------------------------------------------
@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    from database import watchlist_db as oa

    monkeypatch.setenv(contract.DB_ENV, str(tmp_path / "wl.db"))
    monkeypatch.setenv(contract.SWITCH_ENV, "on")
    bp_module._record.clear()
    engine.dispose()
    oa.Base.metadata.create_all(oa.engine)
    oa.db_session.query(oa.WatchlistItem).delete()
    oa.db_session.query(oa.Watchlist).delete()
    oa.db_session.commit()
    oa.db_session.remove()
    # OpenAlgo's own key order: (symbol, exchange)
    snap = SimpleNamespace(
        by_symbol_exchange={split(k)[::-1]: master_row(v) for k, v in V["master"].items()}
    )
    monkeypatch.setattr(master, "openalgo_snapshot", lambda: snap)
    yield tmp_path / "wl.db"
    engine.dispose()
    bp_module._record.clear()


@pytest.fixture
def client():
    reset_users()
    c = make_app(ops.discover(), real=True).test_client()
    login_session(c)
    return c


def get(client, ref):
    return client.get(URL, query_string={"ref": ref})


def make_list(items=("INFY", "TCS", "SBIN")):
    from database import watchlist_db as oa

    made = oa.create_watchlist(OWNER, "Swing", [{"symbol": s, "exchange": "NSE"} for s in items])
    created = oa.db_session.query(oa.Watchlist).filter_by(id=made["id"]).one().created_at
    oa.db_session.remove()
    return made["id"], view.created_key(created)


def seed(lid, created, sections=True):
    with engine.open_session() as s, s.begin():
        s.add(schema.ListRow(list_id=lid, oa_created_at=created, version=4, updated_at=NOW))
        if sections:
            s.add(schema.SectionRow(id=10, list_id=lid, name="Banks", mark=2, position=1,
                                    created_at=NOW, updated_at=NOW))  # fmt: skip
            s.add(schema.SectionItemRow(list_id=lid, exchange="NSE", symbol="SBIN", section_id=10))
            s.add(schema.FollowRow(id=1, list_id=lid, section_id=10, position=0, kind="month_future",
                                   underlying="NIFTY", underlying_exchange="NSE_INDEX",
                                   expiry_rule="month", created_at=NOW, updated_at=NOW))  # fmt: skip
            s.add(schema.PrefRow(ref=f"list:{lid}", sort="name", descending=1, updated_at=NOW))
        s.add(schema.SymbolRow(exchange="NSE", symbol="INFY", flag=2, note="private words",
                               updated_at=NOW))  # fmt: skip


def tables():
    with engine.open_session() as s:
        return {t: [tuple(r) for r in s.execute(text(f"SELECT * FROM {t} ORDER BY 1, 2")).all()]
                for t in (m.name for m in schema.TABLES)}  # fmt: skip


def test_view_reads_a_list_with_its_overlay(client):
    lid, created = make_list()
    seed(lid, created)
    r = get(client, f"list:{lid}")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    data = r.get_json()["data"]
    assert data["version"] == "4" and data["name"] == "Swing" and data["total"] == 3
    assert [(x["contract"]["key"], x["section_id"], x["flag"]) for x in data["rows"]] == [
        ("NSE:INFY", None, 2), ("NSE:TCS", None, None), ("NSE:SBIN", 10, None)]  # fmt: skip
    assert data["rows"][0]["has_note"] is True and "private words" not in r.get_data(as_text=True)
    assert data["rows"][0]["contract"]["isin"] == "INE009A01021"
    assert data["sections"] == [{"id": 10, "name": "Banks", "mark": 2, "position": 1, "count": 2}]
    assert [f["id"] for f in data["follow"]] == [1]
    assert data["pref"] == {"sort": "name", "descending": True, "basis": "close", "held": False}
    assert "price" not in json.dumps(data).lower()


def test_view_of_a_list_with_no_overlay_is_openalgos_order_and_writes_nothing(client, store):
    lid, _ = make_list()
    assert (
        get(client, f"list:{lid}").status_code == 200
    )  # opens the store, which creates its tables
    before = tables()
    data = get(client, f"list:{lid}").get_json()["data"]
    assert [x["contract"]["symbol"] for x in data["rows"]] == ["INFY", "TCS", "SBIN"]
    assert data["version"] == "0" and data["sections"] == [] and data["pref"]["sort"] == "list"
    assert tables() == before and all(
        not rows for name, rows in before.items() if name != "wl_meta"
    )


def test_a_reused_list_id_reads_without_the_stale_overlay_and_the_read_writes_nothing(client):
    lid, _ = make_list()
    seed(lid, "2020-01-01 00:00:00")  # the guard row of a list that had this id before
    before = tables()
    data = get(client, f"list:{lid}").get_json()["data"]
    assert data["sections"] == [] and data["follow"] == [] and data["version"] == "0"
    assert data["pref"]["sort"] == "list" and all(x["section_id"] is None for x in data["rows"])
    assert data["rows"][0]["flag"] == 2  # a flag is the symbol's, not the list's
    assert tables() == before


def test_a_real_reuse_of_an_id_after_a_delete(client):
    from database import watchlist_db as oa

    lid, created = make_list()
    seed(lid, created)
    assert oa.delete_watchlist(OWNER, lid)  # OpenAlgo's own delete: the overlay still holds rows
    again, _ = make_list(("ITC",))
    assert again == lid  # SQLite handed the id out again
    with engine.open_session() as s, s.begin():
        s.execute(schema.ListRow.__table__.update().values(oa_created_at="2020-01-01 00:00:00"))
    data = get(client, f"list:{again}").get_json()["data"]
    assert [x["contract"]["symbol"] for x in data["rows"]] == ["ITC"]
    assert data["sections"] == [] and data["follow"] == []


def test_a_symbol_added_on_trading_shows_at_the_end_of_no_section(client):
    from database import watchlist_db as oa

    lid, created = make_list()
    seed(lid, created)
    assert oa.add_item(OWNER, lid, "WIPRO", "NSE")
    data = get(client, f"list:{lid}").get_json()["data"]
    assert [(x["contract"]["symbol"], x["section_id"]) for x in data["rows"]] == [
        ("INFY", None), ("TCS", None), ("WIPRO", None), ("SBIN", 10)]  # fmt: skip


def test_a_list_that_does_not_exist_is_gone_and_another_users_list_too(client):
    lid, _ = make_list()
    for ref in (f"list:{lid + 5}", f"list:{lid}"):
        if ref.endswith(str(lid)):
            from database import watchlist_db as oa

            oa.db_session.query(oa.Watchlist).update({"user_id": "someone-else"})
            oa.db_session.commit()
            oa.db_session.remove()
        r = get(client, ref)
        assert r.status_code == 404
        body = r.get_json()
        assert body["code"] == "gone" and body["message"] == words.LIST_GONE


@pytest.mark.parametrize("ref", ["preset:nifty", "flag:2", "ranked:futures:INDEX:TOP_TRADED",
                                 "list:abc", "list:", "nonsense", "list:0"])  # fmt: skip
def test_an_unbuilt_kind_or_a_bad_ref_is_invalid_with_the_ref_field(client, ref):
    r = get(client, ref)
    assert r.status_code == 400
    body = r.get_json()
    assert body["code"] == "invalid" and body["field"] == "ref"


def test_the_words_have_no_status_class_or_route(client):
    for sentence in (words.LIST_GONE,):
        assert not any(w in sentence.lower() for w in ("404", "http", "/sudarshan", "error", "ref"))


def test_view_needs_its_ref_and_is_refused_when_off_and_to_a_viewer(client, monkeypatch):
    from watchlists_bridge_app import VIEWER

    assert client.get(URL).get_json()["field"] == "ref"
    monkeypatch.setenv(contract.SWITCH_ENV, "off")
    assert get(client, "list:1").get_json()["code"] == "off"
    monkeypatch.setenv(contract.SWITCH_ENV, "on")
    login_session(client, VIEWER)
    assert get(client, "list:1").get_json()["code"] == "owner_only"


def test_a_master_that_is_not_loaded_calls_nothing_expired(client, monkeypatch):
    lid, _ = make_list()
    monkeypatch.setattr(master, "openalgo_snapshot", lambda: None)
    data = get(client, f"list:{lid}").get_json()["data"]
    assert data["missing"] == [] and not any(x["expired"] for x in data["rows"])
    assert data["rows"][0]["contract"]["name"] == "INFY"  # the symbol stands for the name


def test_many_reads_start_no_thread_and_hold_no_descriptor(client):
    import os
    import threading

    lid, created = make_list()
    seed(lid, created)
    for _ in range(5):  # warm up: the engine, the sessions, the limiter
        assert get(client, f"list:{lid}").status_code == 200
    threads = {t.ident for t in threading.enumerate()}
    fds = len(os.listdir("/proc/self/fd")) if os.path.isdir("/proc/self/fd") else None
    for _ in range(300):
        assert get(client, f"list:{lid}").status_code == 200
        assert get(client, f"list:{lid + 50}").status_code == 404
    assert {t.ident for t in threading.enumerate()} == threads
    if fds is not None:
        assert len(os.listdir("/proc/self/fd")) <= fds + 2
