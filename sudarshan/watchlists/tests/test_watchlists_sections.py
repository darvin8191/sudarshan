"""Sections: store and ops (M8.4, W7), through the real bridge, with OpenAlgo's list in its own
database and the overlay in a throw-away file."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from watchlists_bridge_app import OWNER, csrf_headers, login_session, make_app, reset_users

import blueprints.sudarshan_watchlists as bp_module
from sudarshan.watchlists import contract, engine, master, ops, schema, versions, view
from sudarshan.watchlists.words import sections as words

NOW = 1_700_000_000_000
SECTIONS = contract.API_PREFIX + "/sections"
STALE = "2020-01-01 00:00:00"


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
    yield tmp_path / "wl.db"
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


def make_list(items=("INFY", "TCS", "SBIN"), name="Swing"):
    from database import watchlist_db as oa

    made = oa.create_watchlist(OWNER, name, [{"symbol": s, "exchange": "NSE"} for s in items])
    created = oa.db_session.query(oa.Watchlist).filter_by(id=made["id"]).one().created_at
    oa.db_session.remove()
    return made["id"], view.created_key(created)


def add(client, lid, name, version, **more):
    body = {"list": lid, "name": name, "version": version, **more}
    return client.post(SECTIONS, json=body, headers=client.headers)


def edit(client, sid, version, **fields):
    return client.patch(
        f"{SECTIONS}/{sid}", json={"version": version, **fields}, headers=client.headers
    )


def remove(client, sid, version):
    return client.delete(
        f"{SECTIONS}/{sid}", query_string={"version": version}, headers=client.headers
    )


def read(client, lid):
    r = client.get(contract.API_PREFIX + "/view", query_string={"ref": f"list:{lid}"})
    return r.get_json()["data"]


def table(name):
    with engine.open_session() as s:
        return [tuple(r) for r in s.execute(text(f"SELECT * FROM {name} ORDER BY 1, 2")).all()]


def refused(r, status, code, field=None):
    body = r.get_json()
    assert (r.status_code, body["code"], body.get("field")) == (status, code, field), body
    return body["message"]


# -- the writes ----------------------------------------------------------------------------------
def test_add_rename_mark_and_remove_each_move_the_version_view_reports(client):
    lid, _ = make_list()
    assert read(client, lid)["version"] == "0"
    done = add(client, lid, "Banks", "0", mark=2).get_json()["data"]
    assert (done["version"], done["words"]) == ("1", None) and isinstance(done["id"], int)
    sid = done["id"]
    data = read(client, lid)
    assert data["version"] == "1"
    assert data["sections"] == [{"id": sid, "name": "Banks", "mark": 2, "position": 1, "count": 0}]
    assert edit(client, sid, "1", name="Private banks").get_json()["data"]["version"] == "2"
    assert edit(client, sid, "2", mark=0).get_json()["data"]["version"] == "3"
    assert edit(client, sid, "3", name="PSU", mark=5).get_json()["data"]["version"] == "4"
    (got,) = read(client, lid)["sections"]
    assert (got["name"], got["mark"]) == ("PSU", 5)
    other = add(client, lid, "Metals", "4").get_json()["data"]["id"]
    assert [s["position"] for s in read(client, lid)["sections"]] == [1, 2]
    assert remove(client, sid, "5").get_json()["data"] == {"version": "6", "id": sid, "words": None}
    assert [s["id"] for s in read(client, lid)["sections"]] == [other]


def test_the_first_write_stores_the_live_created_at_exactly_as_the_guard_reads_it(client):
    lid, created = make_list()
    add(client, lid, "Banks", "0")
    with engine.open_session() as s:
        row = s.execute(select(schema.ListRow.oa_created_at, schema.ListRow.version)).one()
    assert tuple(row) == (created, 1)
    assert read(client, lid)["version"] == "1"  # the guard accepts it: the overlay is not stale


def test_a_removed_sections_symbols_read_as_no_section_and_its_follow_rows_too(client):
    lid, created = make_list()
    sid = add(client, lid, "Banks", "0").get_json()["data"]["id"]
    with engine.open_session() as s, s.begin():
        s.add(schema.SectionItemRow(list_id=lid, exchange="NSE", symbol="SBIN", section_id=sid))
        s.add(schema.SectionItemRow(list_id=lid + 9, exchange="NSE", symbol="SBIN", section_id=sid))
        s.add(schema.FollowRow(id=1, list_id=lid, section_id=sid, position=0, kind="month_future",
                               underlying="NIFTY", underlying_exchange="NSE_INDEX",
                               expiry_rule="month", created_at=NOW, updated_at=NOW))  # fmt: skip
    before = read(client, lid)
    assert [(r["contract"]["symbol"], r["section_id"]) for r in before["rows"]][-1] == ("SBIN", sid)
    assert remove(client, sid, "1").status_code == 200
    after = read(client, lid)
    assert [(r["contract"]["symbol"], r["section_id"]) for r in after["rows"]] == [
        ("INFY", None), ("TCS", None), ("SBIN", None)]  # fmt: skip
    assert after["sections"] == [] and [f["section_id"] for f in after["follow"]] == [None]
    assert [r[0] for r in table("wl_section_item")] == [OWNER] and len(
        table("wl_section_item")
    ) == 1


# -- the limits ----------------------------------------------------------------------------------
def test_a_name_is_one_to_forty_letters_and_unique_in_its_list(client):
    lid, _ = make_list()
    assert add(client, lid, "a" * 40, "0").status_code == 200
    said = refused(add(client, lid, "a" * 41, "1"), 400, "invalid", "name")
    assert said == words.NAME_LONG == "Section names can be up to 40 letters."
    refused(add(client, lid, "   ", "1"), 400, "invalid", "name")
    refused(
        add(client, lid, "A" * 40, "1"), 400, "invalid", "name"
    )  # the same name, letter case aside
    assert refused(add(client, lid, "a" * 40, "1"), 400, "invalid", "name") == words.NAME_TAKEN
    sid = add(client, lid, "Banks", "1").get_json()["data"]["id"]
    refused(edit(client, sid, "2", name="b" * 41), 400, "invalid", "name")
    refused(edit(client, sid, "2", name="a" * 40), 400, "invalid", "name")
    assert edit(client, sid, "2", name="BANKS").status_code == 200  # its own name is no duplicate
    refused(add(client, lid, "x", "2", mark=6), 400, "invalid", "mark")
    refused(add(client, lid, "x", "2", mark=-1), 400, "invalid", "mark")
    refused(edit(client, sid, "3"), 400, "invalid", "name")  # nothing to change
    assert read(client, lid)["version"] == "3"  # every refusal left it where it was


def test_a_list_holds_twenty_sections_and_the_twenty_first_is_a_limit(client, emitted):
    lid, _ = make_list()
    for n in range(20):
        assert add(client, lid, f"S{n}", str(n)).status_code == 200
    said = refused(add(client, lid, "one more", "20"), 422, "limit")
    assert said == words.SECTIONS_FULL == "A list can have 20 sections."
    assert read(client, lid)["version"] == "20" and len(table("wl_section")) == 20
    assert len(emitted) == 20
    other, _ = make_list(name="Other")
    assert add(client, other, "S0", "0").status_code == 200  # the limit is per list


def test_a_mark_and_a_name_are_checked_before_the_list_is_touched(client):
    refused(add(client, 99, "A", "0", mark=9), 400, "invalid", "mark")  # not "gone": it is wrong
    assert table("wl_list") == []


# -- the version ---------------------------------------------------------------------------------
@pytest.mark.parametrize("held", ["0", "2", "x", "", "-1", "1.0", "99999999999999999999"])
def test_a_stale_version_changes_nothing_and_says_conflict(client, emitted, held):
    lid, _ = make_list()
    sid = add(client, lid, "Banks", "0").get_json()["data"]["id"]  # the list is at 1
    before = (table("wl_list"), table("wl_section"))
    emitted.clear()
    for r in (add(client, lid, "New", held), edit(client, sid, held, name="New"),
              remove(client, sid, held)):  # fmt: skip
        assert refused(r, 409, "conflict") == words.CONFLICT
    assert words.CONFLICT == "Changed on another screen. The list is shown again."
    assert (table("wl_list"), table("wl_section")) == before and emitted == []


def test_a_first_write_on_a_list_that_has_a_row_is_stale_and_a_version_on_none_too(client):
    lid, _ = make_list()
    add(client, lid, "Banks", "0")
    refused(add(client, lid, "More", "0"), 409, "conflict")
    other, _ = make_list(name="Other")
    refused(add(client, other, "More", "3"), 409, "conflict")
    assert len(table("wl_list")) == 1


# -- a reused id ---------------------------------------------------------------------------------
def seed_stale(lid):
    with engine.open_session() as s, s.begin():
        s.add(schema.ListRow(list_id=lid, oa_created_at=STALE, version=4, updated_at=NOW))
        s.add(schema.SectionRow(id=10, list_id=lid, name="Old", mark=1, position=1,
                                created_at=NOW, updated_at=NOW))  # fmt: skip
        s.add(schema.SectionItemRow(list_id=lid, exchange="NSE", symbol="SBIN", section_id=10))
        s.add(schema.FollowRow(id=1, list_id=lid, section_id=10, position=0, kind="month_future",
                               underlying="NIFTY", underlying_exchange="NSE_INDEX",
                               expiry_rule="month", created_at=NOW, updated_at=NOW))  # fmt: skip
        s.add(schema.PrefRow(ref=f"list:{lid}", sort="name", updated_at=NOW))
        s.add(schema.PrefRow(ref=f"list:{lid + 1}", sort="name", updated_at=NOW))
        s.add(schema.SectionRow(id=11, list_id=lid + 1, name="Other list", mark=0, position=1,
                                created_at=NOW, updated_at=NOW))  # fmt: skip


def test_the_first_write_after_an_id_was_reused_prunes_the_whole_stale_overlay(client, emitted):
    lid, created = make_list()
    seed_stale(lid)
    assert read(client, lid)["version"] == "0"  # the reader already ignores it
    done = add(client, lid, "Fresh", "0").get_json()["data"]
    assert len(emitted) == 1
    with engine.open_session() as s:
        names = sorted(s.scalars(select(schema.SectionRow.name)).all())
        guard = s.execute(select(schema.ListRow.oa_created_at, schema.ListRow.version)).one()
        prefs = s.scalars(select(schema.PrefRow.ref)).all()
    assert names == ["Fresh", "Other list"]  # the other list's overlay is not touched
    assert table("wl_section_item") == [] and table("wl_row") == []
    assert prefs == [f"list:{lid + 1}"]
    assert guard[0] == created and guard[1] > 10**12  # the epoch ms: no old client can hold it
    assert done["version"] == str(guard[1]) and read(client, lid)["version"] == done["version"]
    assert edit(client, done["id"], done["version"], mark=3).get_json()["data"]["version"] == str(
        guard[1] + 1
    )


def test_a_write_holding_the_old_overlays_version_changes_nothing_and_prunes_nothing(client):
    lid, _ = make_list()
    seed_stale(lid)
    before = {t.name: table(t.name) for t in schema.TABLES}
    refused(add(client, lid, "Fresh", "4"), 409, "conflict")
    refused(edit(client, 10, "4", name="x"), 409, "conflict")
    refused(remove(client, 10, "4"), 409, "conflict")
    assert {t.name: table(t.name) for t in schema.TABLES} == before


# -- gone ----------------------------------------------------------------------------------------
def test_a_list_or_section_that_does_not_exist_is_gone(client, emitted):
    lid, _ = make_list()
    for r in (add(client, lid + 40, "A", "0"), add(client, 0, "A", "0"),
              add(client, 2**40, "A", "0")):  # fmt: skip
        refused(r, 404, "gone")
    for r in (edit(client, 5, "0", name="A"), remove(client, 5, "0"), remove(client, 2**40, "0")):
        assert refused(r, 404, "gone") == words.SECTION_GONE
    from database import watchlist_db as oa

    sid = add(client, lid, "Banks", "0").get_json()["data"]["id"]
    oa.delete_watchlist(OWNER, lid)  # the list goes while the page still shows it
    emitted.clear()
    for r in (
        edit(client, sid, "1", name="x"),
        remove(client, sid, "1"),
        add(client, lid, "A", "1"),
    ):
        refused(r, 404, "gone")
    assert emitted == [] and table("wl_list")[0][1] == lid


def test_another_users_list_is_gone(client):
    from database import watchlist_db as oa

    lid, _ = make_list()
    oa.db_session.query(oa.Watchlist).update({"user_id": "someone-else"})
    oa.db_session.commit()
    oa.db_session.remove()
    refused(add(client, lid, "A", "0"), 404, "gone")
    assert not engine.store_path().exists() or table("wl_list") == []


# -- the push ------------------------------------------------------------------------------------
def test_each_write_bumps_and_pushes_once_and_a_refusal_not_at_all(client, emitted):
    lid, _ = make_list()
    sid = add(client, lid, "Banks", "0").get_json()["data"]["id"]
    edit(client, sid, "1", mark=2)
    remove(client, sid, "2")
    assert [(p["why"], p["ref"], p["refs"]) for p in emitted] == [
        ("sections", f"list:{lid}", [f"list:{lid}"])] * 3  # fmt: skip
    assert len({p["v"] for p in emitted}) == 3
    emitted.clear()
    for r in (
        add(client, lid, "A", "1"),
        add(client, lid, "a" * 41, "3"),
        edit(client, 77, "3", mark=1),
    ):
        assert r.status_code >= 400
    assert emitted == []
    seen = versions.read()
    add(client, lid, "Banks", "3")
    assert versions.read() != seen and len(emitted) == 1


def test_the_overlay_is_the_only_file_written(client):
    from sqlalchemy import event

    from database import watchlist_db as oa

    lid, _ = make_list()
    sql = []

    def spy(conn, cursor, statement, *rest):
        sql.append(statement)

    event.listen(oa.engine, "before_cursor_execute", spy)
    try:
        assert add(client, lid, "Banks", "0").status_code == 200
        assert edit(client, 1, "1", mark=1).status_code == 200
    finally:
        event.remove(oa.engine, "before_cursor_execute", spy)
    assert not any(q.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in sql)
