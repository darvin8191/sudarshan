"""The lists version, the change push and the hook (M8.4, W5)."""

from __future__ import annotations

import logging

import pytest
from flask import Blueprint, jsonify
from sqlalchemy import select
from watchlists_bridge_app import (
    OWNER,
    csrf_headers,
    echo_handlers,
    login_session,
    make_app,
    reset_users,
)

import blueprints.sudarshan_watchlists as bp_module
from sudarshan.watchlists import contract, engine, ops, schema, versions
from sudarshan.watchlists.bridge import push
from sudarshan.watchlists.ops import state as state_ops

NOW = 1_700_000_000_000


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv(contract.DB_ENV, str(tmp_path / "wl.db"))
    monkeypatch.setenv(contract.SWITCH_ENV, "on")
    bp_module._record.clear()
    engine.dispose()
    versions.forget()
    yield tmp_path / "wl.db"
    engine.dispose()
    versions.forget()
    bp_module._record.clear()


@pytest.fixture
def emitted(monkeypatch):
    sent = []
    monkeypatch.setattr(
        versions, "emit_from_any_thread", lambda event, data, **kw: sent.append((event, data, kw))
    )
    return sent


def stand_in(status: int = 200, listing: bool = False) -> Blueprint:
    """OpenAlgo's watchlist blueprint's names and routes, answering ``status``."""
    bp = Blueprint("watchlist_bp", __name__)

    def answer(**_):
        return jsonify({"status": "success", "data": {"id": 9}}), status

    base = "/watchlist/api/lists"
    for rule, name, method in (
        (base, "lists", "GET"), (base, "create_list", "POST"),
        (base + "/<int:watchlist_id>", "rename_list", "PATCH"),
        (base + "/<int:watchlist_id>", "remove_list", "DELETE"),
        (base + "/<int:watchlist_id>/clear", "clear_list", "POST"),
        (base + "/<int:watchlist_id>/items", "add_symbol", "POST"),
        (base + "/<int:watchlist_id>/items/<int:item_id>", "remove_symbol", "DELETE"),
        (base + "/<int:watchlist_id>/items/order", "order_symbols", "PUT"),
    ):  # fmt: skip
        bp.add_url_rule(rule, endpoint=name, view_func=answer, methods=[method])
    return bp


def screener_stand_in(found: bool = True) -> Blueprint:
    bp = Blueprint("sudarshan_screener_internal", __name__, url_prefix="/internal")

    @bp.route("/watchlist", methods=["POST"])
    def internal_watchlist():
        return jsonify({"status": "success", "found": found, "list_id": 4})

    return bp


def build(status: int = 200, found: bool = True):
    from database import watchlist_db as oa

    oa.Base.metadata.create_all(oa.engine)  # the add's look before it reads OpenAlgo's lists
    reset_users()
    app = make_app(echo_handlers())
    app.register_blueprint(stand_in(status))
    app.register_blueprint(screener_stand_in(found))
    client = app.test_client()
    login_session(client)
    return app, client, csrf_headers(client)


def send(client, headers, method, path, **kw):
    return client.open(path, method=method, headers=headers, json={}, **kw)


WRITES = [
    ("POST", "/watchlist/api/lists", "lists", "list:9"),
    ("PATCH", "/watchlist/api/lists/7", "lists", "list:7"),
    ("DELETE", "/watchlist/api/lists/7", "lists", "list:7"),
    ("POST", "/watchlist/api/lists/7/clear", "items", "list:7"),
    ("POST", "/watchlist/api/lists/7/items", "items", "list:7"),
    ("DELETE", "/watchlist/api/lists/7/items/3", "items", "list:7"),
    ("PUT", "/watchlist/api/lists/7/items/order", "arranged", "list:7"),
]


# -- the versions ------------------------------------------------------------------------------
def stored(name):
    with engine.open_session() as s:
        return s.scalar(select(schema.MetaRow.value).where(schema.MetaRow.name == name))


def test_the_lists_version_reads_boot_and_count_and_the_other_two_are_the_stores_integers(emitted):
    engine.ready()
    first = versions.snapshot()
    assert first["lists"].startswith(versions.BOOT + ":")
    assert first["favourites"] == 0 and first["flags"] == 0  # an absent row reads as 0
    assert stored("favourites_version") is None
    with engine.open_session() as s, s.begin():
        n = versions.advance_flags(s, 0)
    versions.committed("flags", n)
    after = versions.snapshot()
    assert after["lists"] == first["lists"] and emitted == []
    assert after["flags"] == 1 and after["favourites"] == 0 and stored("flags_version") == "1"
    for held in (0, 5):  # a stale or impossible version is refused and moves nothing
        with pytest.raises(versions.Stale), engine.open_session() as s, s.begin():
            versions.advance_flags(s, held)
    assert stored("flags_version") == "1"
    assert versions.bump("items", ["list:1"]) != first["lists"]


def test_a_favourites_advance_on_a_held_version_refuses_a_stale_one_and_changes_nothing():
    engine.ready()
    with engine.open_session() as s, s.begin():
        assert versions.advance_favourites(s, 0) == 1  # no row yet: 0 is what it holds
        assert versions.advance_favourites(s, 1) == 2
    for held in (0, 1, 3):
        with engine.open_session() as s, pytest.raises(versions.Stale), s.begin():
            versions.advance_favourites(s, held)
    assert stored("favourites_version") == "2"


def test_a_writer_adds_one_in_its_own_transaction_and_a_rollback_adds_nothing():
    engine.ready()
    with engine.open_session() as s, s.begin():
        assert [versions.advance_favourites(s) for _ in range(3)] == [1, 2, 3]
    with engine.open_session() as s:
        with s.begin():
            versions.advance_favourites(s)
            s.rollback()
    assert stored("favourites_version") == "3"


def test_the_versions_survive_a_restart_because_the_store_holds_them():
    engine.ready()
    with engine.open_session() as s, s.begin():
        s.add(schema.MetaRow(name="favourites_version", value="41"))
        s.add(schema.MetaRow(name="flags_version", value="7"))
    versions.forget()  # a restart: the mirror is empty, the store is not
    assert (versions.favourites(), versions.flags()) == (41, 7)


def test_the_mirror_is_loaded_once_and_a_push_then_opens_no_session(emitted, monkeypatch):
    engine.ready()
    versions.bump("items", [])  # the first load
    opened = []
    real = engine.open_session
    monkeypatch.setattr(engine, "open_session", lambda: opened.append(1) or real())
    versions.bump("items", ["list:2"])
    versions.snapshot()
    assert opened == [] and emitted[-1][1]["favourites"] == 0 and emitted[-1][1]["flags"] == 0


def test_the_mirror_only_moves_forward_and_a_late_load_cannot_undo_a_commit():
    versions.committed("flags", 5)
    versions.committed("flags", 4)
    assert versions.flags() == 5
    engine.ready()  # the store says 0 (no row): the first load must not undo the 5
    versions.committed("flags", 0, loaded=True)
    assert versions.flags() == 5


def read_state():
    """The state op called as the bridge calls it, inside a request (it names the session's user
    for the favourites) and over OpenAlgo's own lists."""
    from flask import Flask

    from database import watchlist_db as oa

    oa.Base.metadata.create_all(oa.engine)
    with Flask("state-for-tests").test_request_context():
        return state_ops.state({})


def test_a_store_that_cannot_be_read_leaves_the_two_out_and_never_raises(emitted, store):
    assert not store.exists()
    assert versions.snapshot() == {"lists": versions.read()}
    versions.bump("items", ["list:1"])
    assert set(emitted[-1][1]) == {"v", "ref", "refs", "why"}
    assert "versions" not in read_state()
    engine.ready()
    assert set(versions.snapshot()) == {"lists", "favourites", "flags"}
    assert read_state()["versions"] == versions.snapshot()


def test_an_unreadable_store_is_opened_and_logged_once_for_a_run_of_pushes(
    emitted, store, monkeypatch, caplog
):
    """Review L6: every OpenAlgo watchlist write pushes and each push loaded the mirror, so a
    broken store was opened and logged once per write. It is left alone for a few seconds."""
    from utils.thread_safe_cache import LockedTTLCache

    store.write_bytes(b"this is not a database file")
    now = [1000.0]
    memo = LockedTTLCache(maxsize=1, ttl=versions.STORE_RETRY_S, timer=lambda: now[0])
    monkeypatch.setattr(versions, "_unreadable", memo)
    looks, real = [], engine.ready
    monkeypatch.setattr(engine, "ready", lambda: looks.append(1) or real())
    with caplog.at_level(logging.ERROR):
        for _ in range(3):
            versions.bump("items", ["list:1"])
    assert len(looks) == 1
    assert sum("could not be opened" in m for m in caplog.messages) == 1
    assert [set(data) for _, data, _ in emitted] == [{"v", "ref", "refs", "why"}] * 3
    now[0] += versions.STORE_RETRY_S + 1
    versions.bump("items", ["list:1"])
    assert len(looks) == 2  # looked again once the few seconds had passed


def test_the_boot_part_changes_at_each_process_start():
    import subprocess
    import sys

    code = "from sudarshan.watchlists import versions; print(versions.BOOT)"
    boots = {
        subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=push.__file__.rsplit("/sudarshan/", 1)[0]).stdout.strip()
        for _ in range(2)
    }  # fmt: skip
    assert len(boots) == 2 and versions.BOOT not in boots


def test_a_bump_pushes_v_ref_why_and_the_other_versions_to_the_owners_room(emitted):
    engine.ready()
    v = versions.bump("items", ["list:5"])
    ((event, data, kw),) = emitted
    assert event == contract.EVENT_PUSH == "sudarshan_watchlists"
    assert kw == {"to": contract.ROOM, "namespace": "/"}
    assert data == {"v": v, "ref": "list:5", "refs": ["list:5"], "why": "items", "favourites": 0,
                    "flags": 0}  # fmt: skip
    versions.bump("lists", ["list:1", "list:2"])
    assert (emitted[-1][1]["ref"], emitted[-1][1]["refs"]) == ("lists", ["list:1", "list:2"])


def test_the_push_names_every_ref_once_in_order_and_none_means_read_everything(emitted):
    """``refs`` carries every ref a write changed (a move's two lists, a mark's two flag lists),
    which the phone's per-ref sync needs; ``ref`` collapses them and stays for the phone."""
    engine.ready()
    versions.bump("flags", ["flag:2", "flag:4", "flag:2"])
    versions.bump("items", [])
    first, second = emitted[0][1], emitted[1][1]
    assert (first["ref"], first["refs"]) == ("lists", ["flag:2", "flag:4"])
    assert (second["ref"], second["refs"]) == ("lists", [])
    before = versions.read()
    versions.push("favourites")  # a push alone never moves the lists version
    assert versions.read() == before and emitted[-1][1]["v"] == before
    assert emitted[-1][1]["refs"] == [] and emitted[-1][1]["why"] == "favourites"
    push_fields = {f.name for f in contract.record("Push")}
    assert {"v", "ref", "refs", "why"} <= push_fields and set(first) <= push_fields


def test_a_push_that_cannot_be_sent_is_logged_and_the_bump_stands(monkeypatch, caplog):
    engine.ready()

    def broken(*a, **k):
        raise RuntimeError("no server")

    monkeypatch.setattr(versions, "emit_from_any_thread", broken)
    before = versions.read("lists")
    with caplog.at_level(logging.ERROR):
        assert versions.bump("items", []) != before
    assert versions.read("lists") != before


def test_state_answers_the_three_versions():
    reset_users()
    client = make_app(ops.discover(), real=True).test_client()
    login_session(client)
    got = client.get(contract.API_PREFIX + "/state").get_json()["data"]["versions"]
    assert got == versions.snapshot() and set(got) == {"lists", "favourites", "flags"}
    assert got["favourites"] == 0 and got["flags"] == 0
    assert read_state()["versions"] == versions.snapshot()


# -- the hook ----------------------------------------------------------------------------------
@pytest.mark.parametrize(("method", "path", "why", "ref"), WRITES)
def test_each_openalgo_write_bumps_once_and_emits_once(emitted, method, path, why, ref):
    _, client, headers = build()
    before = versions.read("lists")
    r = send(client, headers, method, path)
    assert r.status_code == 200 and r.get_json()["status"] == "success"  # the answer, untouched
    assert versions.read("lists") != before
    assert int(versions.read("lists").split(":")[1]) == int(before.split(":")[1]) + 1
    ((event, data, _),) = emitted
    assert event == contract.EVENT_PUSH and (data["why"], data["ref"]) == (why, ref)
    assert data["refs"] == [ref]


def test_an_add_of_a_symbol_the_list_already_holds_bumps_nothing(emitted):
    """OpenAlgo answers an add of a symbol already in the list as a success with the row it had
    (review L3): nothing changed, so nothing is bumped or pushed; a new symbol is, once."""
    import blueprints.watchlist as real
    from database import watchlist_db as oa

    oa.Base.metadata.create_all(oa.engine)
    oa.db_session.query(oa.WatchlistItem).delete()
    oa.db_session.query(oa.Watchlist).delete()
    oa.db_session.commit()
    made = oa.create_watchlist(OWNER, "Swing", [{"symbol": "INFY", "exchange": "NSE"}])
    oa.db_session.remove()
    reset_users()
    app = make_app(echo_handlers())
    app.register_blueprint(real.watchlist_bp)
    client = app.test_client()
    login_session(client)
    headers = csrf_headers(client)
    url = f"/watchlist/api/lists/{made['id']}/items"
    before = versions.read()
    for body in ({"symbol": "INFY", "exchange": "NSE"}, {"symbol": " infy ", "exchange": "nse"}):
        r = client.post(url, json=body, headers=headers)
        assert r.status_code == 201 and r.get_json()["status"] == "success"  # OpenAlgo's answer
    assert versions.read() == before and emitted == []
    r = client.post(url, json={"symbol": "TCS", "exchange": "NSE"}, headers=headers)
    assert r.status_code == 201
    ((_, data, _),) = emitted
    assert (data["why"], data["refs"]) == ("items", [f"list:{made['id']}"])


def test_a_get_bumps_nothing(emitted):
    _, client, headers = build()
    before = versions.snapshot()
    assert client.get("/watchlist/api/lists", headers=headers).status_code == 200
    assert versions.snapshot() == before and emitted == []


@pytest.mark.parametrize("status", [400, 404, 409, 500])
def test_a_refused_or_failed_change_bumps_nothing(emitted, status):
    _, client, headers = build(status)
    before = versions.snapshot()
    for method, path, _, _ in WRITES:
        assert send(client, headers, method, path).status_code == status
    assert versions.snapshot() == before and emitted == []


def test_a_request_of_another_blueprint_costs_nothing_and_bumps_nothing(emitted, monkeypatch):
    app, client, headers = build()
    monkeypatch.setattr(push, "enabled", lambda: pytest.fail("read the switch"))
    r = client.post(contract.API_PREFIX + "/state", headers=headers, json={})
    assert r.status_code in (200, 405) and emitted == []


def test_the_screeners_apply_bumps_once_and_its_read_and_a_miss_bump_nothing(emitted):
    app, client, headers = build()
    base = versions.read("lists")
    assert client.post("/internal/watchlist?op=watchlist_read", headers=headers).status_code == 200
    assert emitted == [] and versions.read("lists") == base
    assert client.post("/internal/watchlist?op=watchlist_apply", headers=headers).status_code == 200
    ((_, data, _),) = emitted
    assert (data["why"], data["ref"], data["refs"]) == ("lists", "list:4", ["list:4"])
    app2, client2, headers2 = build(found=False)
    emitted.clear()
    client2.post("/internal/watchlist?op=watchlist_apply", headers=headers2)
    assert emitted == []


def test_the_hook_pushes_nothing_with_the_switch_off_and_makes_no_store(
    emitted, monkeypatch, store
):
    _, client, headers = build()
    monkeypatch.setenv(contract.SWITCH_ENV, "off")
    before = versions.snapshot()
    for method, path, _, _ in WRITES:
        assert send(client, headers, method, path).status_code == 200
    assert versions.snapshot() == before and emitted == [] and not store.exists()


def test_with_the_switch_off_a_delete_and_a_clear_still_prune_the_overlay(emitted, monkeypatch):
    """Review L1: the guard's ``created_at`` has one-second resolution, so a list deleted while v2
    is off and a new one given its id in the same second would inherit the overlay. The prune is
    the overlay following OpenAlgo's own change, whatever the switch; only the push waits."""
    _, client, headers = build()
    seed()
    monkeypatch.setenv(contract.SWITCH_ENV, "off")
    lists = versions.read()
    assert send(client, headers, "DELETE", "/watchlist/api/lists/7").status_code == 200
    assert send(client, headers, "POST", "/watchlist/api/lists/8/clear").status_code == 200
    assert counts() == {"wl_list": 1, "wl_section": 1, "wl_section_item": 0, "wl_row": 1,
                        "wl_pref": 1, "wl_favourite": 2}  # fmt: skip
    with engine.open_session() as s:
        assert s.scalars(select(schema.ListRow)).one().list_id == 8  # list 8 keeps its sections
    assert stored("favourites_version") == "1"  # list 7's slot went, in the same transaction
    assert versions.read() == lists and emitted == []  # and nobody is told while it is off


@pytest.mark.parametrize("broken", ["bump", "prune"])
def test_a_failing_hook_never_changes_the_answer(emitted, monkeypatch, caplog, broken):
    _, client, headers = build()
    target = (versions, "bump") if broken == "bump" else (push.prune, "drop_list")

    def boom(*a, **k):
        raise RuntimeError("broken")

    monkeypatch.setattr(*target, boom)
    with caplog.at_level(logging.ERROR):
        r = send(client, headers, "DELETE", "/watchlist/api/lists/7")
    assert r.status_code == 200 and r.get_json() == {"status": "success", "data": {"id": 9}}
    assert any("could not be pruned" in m or "hook failed" in m for m in caplog.messages)
    if broken == "prune":
        assert len(emitted) == 1  # the lists did change, so the phone is told


def seed(list_ids=(7, 8)):
    with engine.open_session() as s, s.begin():
        for n in list_ids:
            s.add(schema.ListRow(list_id=n, oa_created_at="t", updated_at=NOW))
            s.add(schema.SectionRow(id=n, list_id=n, name="S", position=0, created_at=NOW,
                                    updated_at=NOW))  # fmt: skip
            s.add(schema.SectionItemRow(list_id=n, exchange="NSE", symbol="X", section_id=n))
            s.add(schema.FollowRow(id=n, list_id=n, position=0, kind="month_future",
                                   underlying="NIFTY", underlying_exchange="NSE_INDEX",
                                   expiry_rule="month", created_at=NOW, updated_at=NOW))  # fmt: skip
            s.add(schema.PrefRow(ref=f"list:{n}", updated_at=NOW))
            s.add(schema.FavouriteRow(slot=n - 5, ref=f"list:{n}", updated_at=NOW))
        s.add(schema.FavouriteRow(slot=7, ref="preset:nifty", updated_at=NOW))


def counts():
    with engine.open_session() as s:
        return {
            t.__tablename__: len(s.scalars(select(t)).all())
            for t in (schema.ListRow, schema.SectionRow, schema.SectionItemRow, schema.FollowRow,
                      schema.PrefRow, schema.FavouriteRow)
        }  # fmt: skip


def test_a_delete_prunes_the_overlay_and_empties_the_favourite_slot(emitted):
    _, client, headers = build()
    seed()
    fav = versions.favourites()
    assert send(client, headers, "DELETE", "/watchlist/api/lists/7").status_code == 200
    assert counts() == {"wl_list": 1, "wl_section": 1, "wl_section_item": 1, "wl_row": 1,
                        "wl_pref": 1, "wl_favourite": 2}  # fmt: skip
    with engine.open_session() as s:
        assert sorted(f.ref for f in s.scalars(select(schema.FavouriteRow))) == [
            "list:8", "preset:nifty"]  # fmt: skip
        assert s.scalars(select(schema.ListRow)).one().list_id == 8
    assert fav == 0 and versions.favourites() == 1 and stored("favourites_version") == "1"
    assert len(emitted) == 1
    assert emitted[0][1]["favourites"] == versions.favourites()  # the push already knows


def test_a_delete_with_no_favourite_leaves_the_favourites_version(emitted):
    _, client, headers = build()
    seed((8,))
    fav = versions.favourites()
    send(client, headers, "DELETE", "/watchlist/api/lists/7")
    assert versions.favourites() == fav and counts()["wl_list"] == 1


def test_a_clear_drops_membership_only(emitted):
    _, client, headers = build()
    seed()
    fav = versions.favourites()
    assert send(client, headers, "POST", "/watchlist/api/lists/7/clear").status_code == 200
    assert counts() == {"wl_list": 2, "wl_section": 2, "wl_section_item": 1, "wl_row": 2,
                        "wl_pref": 2, "wl_favourite": 3}  # fmt: skip
    with engine.open_session() as s:
        assert s.scalars(select(schema.SectionItemRow)).one().list_id == 8
    assert versions.favourites() == fav and len(emitted) == 1


def test_a_missing_store_is_not_created_by_a_delete_and_a_newer_one_is_skipped(
    emitted, store, caplog
):
    _, client, headers = build()
    assert send(client, headers, "DELETE", "/watchlist/api/lists/7").status_code == 200
    assert not store.exists() and len(emitted) == 1
    engine.prepare(engine._engine.get())
    with engine.open_session() as s, s.begin():
        s.execute(schema.MetaRow.__table__.update().where(schema.MetaRow.name == "schema")
                  .values(value="999"))  # fmt: skip
    engine.dispose()
    with caplog.at_level(logging.ERROR):
        assert send(client, headers, "DELETE", "/watchlist/api/lists/7").status_code == 200
    assert len(emitted) == 2 and not [m for m in caplog.messages if "hook failed" in m]


# -- an op that declares it bumps --------------------------------------------------------------
def test_an_op_that_declares_a_bump_bumps_once_after_it_returns_and_a_refusal_not_at_all(emitted):
    reset_users()

    @ops.bumps("sections", lambda args, data: [f"list:{args['list']}"])
    def fake(args):
        if args["name"] == "no":
            raise ops.OpError("conflict")
        return {"done": True}

    handlers = echo_handlers()
    handlers["section_add"] = fake
    client = make_app(handlers).test_client()
    login_session(client)
    headers = csrf_headers(client)
    op = next(o for o in contract.OPS if o.name == "section_add")
    path = contract.API_PREFIX + op.route
    body = {"list": 3, "name": "ok", "version": "v"}
    assert client.open(path, method=op.method, json=body, headers=headers).status_code == 200
    ((_, data, _),) = emitted
    assert (data["why"], data["ref"], data["refs"]) == ("sections", "list:3", ["list:3"])
    body["name"] = "no"
    assert client.open(path, method=op.method, json=body, headers=headers).status_code == 409
    assert len(emitted) == 1


def test_a_refusal_after_part_of_the_change_committed_bumps_once_for_what_it_names(emitted):
    """A write that commits a step and then refuses names what it committed (review M1): the
    bridge bumps the lists version once and pushes those refs before it answers the refusal."""
    reset_users()

    @ops.bumps("items", lambda args, data: [f"list:{args['list']}"])
    def half(args):
        if args["name"] == "nothing":
            raise ops.OpError("failed", "Nothing was added to Swing. Try again.")
        raise ops.OpError("conflict", "Added 2 symbols ...", changed=["list:3", "list:4", "list:3"])

    handlers = echo_handlers()
    handlers["section_add"] = half
    client = make_app(handlers).test_client()
    login_session(client)
    headers = csrf_headers(client)
    path = contract.API_PREFIX + "/sections"
    before = versions.read()
    r = client.post(path, json={"list": 3, "name": "half", "version": "v"}, headers=headers)
    assert r.status_code == 409 and r.get_json()["message"] == "Added 2 symbols ..."
    assert int(versions.read().split(":")[1]) == int(before.split(":")[1]) + 1
    ((_, data, _),) = emitted
    assert (data["why"], data["ref"], data["refs"]) == ("items", "lists", ["list:3", "list:4"])
    r = client.post(path, json={"list": 3, "name": "nothing", "version": "v"}, headers=headers)
    assert r.status_code == 500 and len(emitted) == 1  # nothing committed: nothing pushed


def test_a_write_whose_refs_say_unchanged_bumps_and_pushes_nothing(emitted):
    """A write that changed nothing this time (review L3) says so from its refs function."""
    reset_users()

    @ops.bumps("sections", lambda args, data: data["changed"] or ops.UNCHANGED)
    def maybe(args):
        return {"changed": [] if args["name"] == "same" else [f"list:{args['list']}"]}

    handlers = echo_handlers()
    handlers["section_add"] = maybe
    client = make_app(handlers).test_client()
    login_session(client)
    headers = csrf_headers(client)
    path = contract.API_PREFIX + "/sections"
    before = versions.read()
    r = client.post(path, json={"list": 3, "name": "same", "version": "v"}, headers=headers)
    assert r.status_code == 200 and versions.read() == before and emitted == []
    client.post(path, json={"list": 3, "name": "new", "version": "v"}, headers=headers)
    assert [d["refs"] for _, d, _ in emitted] == [["list:3"]]


def test_an_op_that_does_not_declare_a_bump_pushes_nothing(emitted):
    reset_users()
    client = make_app(echo_handlers()).test_client()
    login_session(client)
    client.get(contract.API_PREFIX + "/state")
    assert emitted == []


# -- the room ----------------------------------------------------------------------------------
def join_as(app, monkeypatch, who):
    joined = []
    monkeypatch.setattr(push, "join_room", joined.append)
    with app.test_request_context():
        from flask import session

        session["user"], session["logged_in"] = who, True
        from datetime import datetime

        import pytz

        session["login_time"] = datetime.now(pytz.timezone("Asia/Kolkata")).isoformat()
        return push.join_owner_room({}), joined


def test_the_owner_joins_with_an_ack_and_another_session_is_refused(monkeypatch):
    reset_users()
    app = make_app(echo_handlers())
    ack, joined = join_as(app, monkeypatch, OWNER)
    assert ack == {"ok": True} and joined == [contract.ROOM]
    from watchlists_bridge_app import VIEWER

    ack, joined = join_as(app, monkeypatch, VIEWER)
    assert ack["ok"] is False and "owner's" in ack["message"] and joined == []


def test_the_switch_off_refuses_the_join_in_words_and_pushes_nothing(monkeypatch, emitted):
    reset_users()
    app = make_app(echo_handlers())
    monkeypatch.setenv(contract.SWITCH_ENV, "off")
    ack, joined = join_as(app, monkeypatch, OWNER)
    assert ack["ok"] is False and "switched off" in ack["message"] and joined == []
    app.register_blueprint(stand_in())
    client = app.test_client()
    login_session(client)
    send(client, csrf_headers(client), "POST", "/watchlist/api/lists/7/items")
    assert emitted == []


def test_the_hook_is_registered_once_per_app_and_relies_on_openalgos_real_names():
    app = make_app(echo_handlers())
    bridge_hooks = [
        f for f in app.after_request_funcs.get(None, []) if f is push.after_watchlist_request
    ]
    assert len(bridge_hooks) == 1
    push.install(app)
    assert app.after_request_funcs[None].count(push.after_watchlist_request) == 1
    from flask import Flask

    import blueprints.sudarshan_screener as screener
    import blueprints.watchlist as real

    probe = Flask("probe")
    probe.register_blueprint(real.watchlist_bp)
    probe.register_blueprint(screener.sudarshan_screener_internal_bp)
    endpoints = {r.endpoint: r.methods for r in probe.url_map.iter_rules()}
    assert real.watchlist_bp.name == push.OPENALGO_BLUEPRINT
    for name in push.CHANGES:
        assert name in endpoints, name
    assert {
        n
        for n, m in endpoints.items()
        if n.startswith("watchlist_bp.") and m - {"GET", "HEAD", "OPTIONS"}
    } == set(push.CHANGES)
    assert push.SCREENER_ENDPOINT in endpoints
