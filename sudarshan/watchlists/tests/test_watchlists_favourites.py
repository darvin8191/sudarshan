"""Favourites, view preferences and the lists op (M8.5, M8.7, W10), through the real bridge, with
OpenAlgo's lists in their own database and the overlay in a throw-away file."""

from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import quote

import pytest
from sqlalchemy import select
from watchlists_bridge_app import OWNER, csrf_headers, login_session, make_app, reset_users

import blueprints.sudarshan_watchlists as bp_module
from sudarshan.watchlists import contract, engine, master, ops, ranked, schema, versions
from sudarshan.watchlists.ops import lists as lists_op
from sudarshan.watchlists.words import favourites as words

NOW = 1_700_000_000_000
API = contract.API_PREFIX
RANKED = "ranked:futures:INDEX:OI_GAINERS"


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


def make_lists(*names, items=("INFY", "TCS")):
    from database import watchlist_db as oa

    ids = []
    for name in names:
        made = oa.create_watchlist(OWNER, name, [{"symbol": s, "exchange": "NSE"} for s in items])
        ids.append(made["id"])
    oa.db_session.remove()
    return ids


def put(client, slots, version):
    body = {"slots": [{"slot": n, "ref": ref} for n, ref in slots], "version": version}
    return client.put(API + "/favourites", json=body, headers=client.headers)


def state(client):
    return client.get(API + "/state").get_json()["data"]


def show(client):
    return [(f["slot"], f["ref"], f["name"], f["kind"]) for f in state(client)["favourites"]]


def rows(table):
    with engine.open_session() as s:
        return list(s.scalars(select(table)))


def set_pref(client, ref, **fields):
    return client.put(f"{API}/prefs/{quote(ref)}", json=fields, headers=client.headers)


def pref_of(client, lid):
    return client.get(f"{API}/view", query_string={"ref": f"list:{lid}"}).get_json()["data"]["pref"]


# -- favourites --------------------------------------------------------------------------------
def test_slots_resolve_in_slot_order_with_kind_name_and_count(client):
    a, b = make_lists("Swing", "Banks", items=("INFY", "TCS", "SBIN"))
    done = put(client, [(3, f"list:{b}"), (1, RANKED), (2, "flag:2")], 0).get_json()["data"]
    assert done == {"version": "1", "id": None, "words": None}
    got = state(client)["favourites"]
    assert got == [
        {"slot": 1, "ref": RANKED, "kind": "ranked", "name": "Index futures · OI gainers",
         "count": None},
        {"slot": 2, "ref": "flag:2", "kind": "flag", "name": "Breakout", "count": 0},
        {"slot": 3, "ref": f"list:{b}", "kind": "list", "name": "Banks", "count": 3},
    ]  # fmt: skip
    assert state(client)["versions"]["favourites"] == 1


def test_a_deleted_list_and_a_missing_preset_or_flag_are_dropped_at_read_and_the_rows_stay(client):
    from database import watchlist_db as oa

    a, b = make_lists("Swing", "Banks")
    put(client, [(1, f"list:{a}"), (2, f"list:{b}")], 0)
    oa.delete_watchlist(OWNER, a)  # OpenAlgo's own function: no hook, no prune
    oa.db_session.remove()
    assert show(client) == [(2, f"list:{b}", "Banks", "list")]
    with engine.open_session() as s:  # rows that name nothing real, written under the owner's hand
        s.add_all(
            [
                schema.FavouriteRow(slot=3, ref="preset:nifty-50", updated_at=NOW),
                schema.FavouriteRow(slot=4, ref="flag:7", updated_at=NOW),
                schema.FavouriteRow(slot=5, ref="ranked:futures:INDEX:NOPE", updated_at=NOW),
                schema.FavouriteRow(slot=6, ref="sideways", updated_at=NOW),
            ]
        )
        s.commit()
    assert show(client) == [(2, f"list:{b}", "Banks", "list")]
    assert len(rows(schema.FavouriteRow)) == 6  # a read deletes nothing


def test_a_renamed_flag_and_the_flags_count_come_from_the_store(client):
    with engine.open_session() as s:
        s.add(schema.FlagNameRow(mark=2, name="Setups"))
        s.add_all([schema.SymbolRow(exchange="NSE", symbol=n, flag=2, updated_at=NOW)
                   for n in ("INFY", "TCS")] + [
                   schema.SymbolRow(exchange="NSE", symbol="SBIN", flag=1, updated_at=NOW),
                   schema.SymbolRow(exchange="NSE", symbol="ITC", note="x", updated_at=NOW)])  # fmt: skip
        s.commit()
    put(client, [(1, "flag:2")], 0)
    assert state(client)["favourites"][0] == {
        "slot": 1,
        "ref": "flag:2",
        "kind": "flag",
        "name": "Setups",
        "count": 2,
    }


def test_a_slot_does_not_follow_another_list_that_took_a_deleted_lists_id(client):
    from database import watchlist_db as oa

    (a,) = make_lists("Swing")
    put(client, [(1, f"list:{a}")], 0)
    with engine.open_session() as s:
        (row,) = s.scalars(select(schema.FavouriteRow))
        assert row.oa_created_at == lists_op.merge_view.created_key(_created(a))
    assert show(client) == [(1, f"list:{a}", "Swing", "list")]
    # the id is handed to a list made later: the same id, another created_at
    later = _created(a).replace(year=2030)
    oa.db_session.query(oa.Watchlist).filter_by(id=a).update(
        {"name": "Momentum", "created_at": later}
    )
    oa.db_session.commit()
    oa.db_session.remove()
    assert show(client) == []  # dropped at read, not followed
    assert state(client)["versions"]["favourites"] == 1 and len(rows(schema.FavouriteRow)) == 1


def test_the_eighth_slot_is_refused_in_words_and_nothing_is_written(client, emitted):
    ids = make_lists(*[f"L{n}" for n in range(8)])
    too_many = [(n, f"list:{lid}") for n, lid in enumerate(ids, 1)]
    r = put(client, too_many, 0)
    assert r.status_code == 422 and r.get_json()["code"] == "limit"
    assert r.get_json()["message"] == "Seven favourites at most. Remove one first."
    r = put(client, [(8, f"list:{ids[0]}")], 0)  # slot 8 on its own is the same refusal
    assert r.get_json()["code"] == "limit"
    assert rows(schema.FavouriteRow) == [] and emitted == []
    assert put(client, too_many[:7], 0).status_code == 200  # seven are fine


def test_the_suggestion_is_the_first_seven_lists_in_openalgos_order_and_is_never_stored(client):
    from database import watchlist_db as oa

    ids = make_lists(*[f"L{n}" for n in range(9)])
    for pos, lid in enumerate(reversed(ids)):  # OpenAlgo's order is its position, not its id
        oa.db_session.query(oa.Watchlist).filter_by(id=lid).update({"position": pos})
    oa.db_session.commit()
    oa.db_session.remove()
    first_seven = [f"list:{lid}" for lid in reversed(ids)][:7]
    got = state(client)
    assert [f["ref"] for f in got["favourites"]] == first_seven
    assert [f["slot"] for f in got["favourites"]] == list(range(1, 8))
    assert got["versions"]["favourites"] == 0
    listed = client.get(API + "/lists").get_json()["data"]
    assert [f["ref"] for f in listed["favourites"]] == first_seven
    assert rows(schema.FavouriteRow) == []  # computed, never stored as his choice
    assert all(r.name != "favourites_version" for r in rows(schema.MetaRow))


def test_after_the_owner_has_pinned_and_emptied_the_slots_there_is_no_suggestion(client):
    (a,) = make_lists("Swing")
    assert len(state(client)["favourites"]) == 1  # the suggestion
    put(client, [(1, f"list:{a}")], 0)
    put(client, [], 1)
    assert state(client)["favourites"] == []
    assert state(client)["versions"]["favourites"] == 2


def test_a_stale_version_says_conflict_changes_nothing_and_pushes_nothing(client, emitted):
    a, b = make_lists("Swing", "Banks")
    assert put(client, [(1, f"list:{a}")], 0).status_code == 200
    emitted.clear()
    for held in (0, 5, -1):
        r = put(client, [(1, f"list:{b}")], held)
        assert r.status_code == 409 and r.get_json()["code"] == "conflict", held
        assert r.get_json()["message"] == words.CONFLICT
    assert show(client) == [(1, f"list:{a}", "Swing", "list")]
    assert emitted == []
    assert put(client, [(1, f"list:{b}")], 1).get_json()["data"]["version"] == "2"


def test_refs_are_checked_before_anything_is_written(client):
    (a,) = make_lists("Swing")
    cases = {
        "list:9999": "gone",
        "preset:nifty-50": "gone",
        "list:x": "invalid",
        "flag:6": "invalid",
        "ranked:futures:INDEX:NOPE": "invalid",
        "nonsense": "invalid",
        "": "invalid",
    }
    for ref, code in cases.items():
        assert put(client, [(1, ref)], 0).get_json()["code"] == code, ref
    twice = put(client, [(1, f"list:{a}"), (2, f"list:{a}")], 0).get_json()
    assert (twice["code"], twice["field"]) == ("invalid", "slots")
    same_slot = put(client, [(1, f"list:{a}"), (1, RANKED)], 0).get_json()
    assert (same_slot["code"], same_slot["field"]) == ("invalid", "slots")
    assert put(client, [(0, f"list:{a}")], 0).get_json()["code"] == "invalid"
    assert rows(schema.FavouriteRow) == []


def test_the_delete_hook_prune_and_a_favourite_write_share_one_version_line(client):
    a, b = make_lists("Swing", "Banks")
    put(client, [(1, f"list:{a}"), (2, f"list:{b}")], 0)
    from sudarshan.watchlists.store import prune

    assert prune.drop_list(a) is True  # a slot emptied: the version moved in its transaction
    assert versions.favourites() == 2
    assert put(client, [(1, f"list:{b}")], 1).get_json()["code"] == "conflict"
    assert put(client, [(1, f"list:{b}")], 2).status_code == 200


def test_a_favourites_write_pushes_once_with_the_new_version_and_bumps_no_lists_version(
    client, emitted
):
    (a,) = make_lists("Swing")
    before = versions.read()
    assert put(client, [(1, f"list:{a}")], 0).status_code == 200
    assert versions.read() == before  # no list's membership changed
    assert emitted == [{"v": before, "ref": "lists", "why": "favourites", "favourites": 1,
                        "flags": 0}]  # fmt: skip
    state(client)
    client.get(API + "/lists")
    assert len(emitted) == 1  # a read pushes nothing


# -- preferences -------------------------------------------------------------------------------
def test_a_list_with_no_preference_reads_as_the_defaults_and_a_read_writes_nothing(client):
    (a,) = make_lists("Swing")
    assert pref_of(client, a) == {"sort": "list", "descending": False, "basis": "close",
                                  "held": False}  # fmt: skip
    assert rows(schema.PrefRow) == []


def test_a_preference_round_trips_and_only_the_fields_sent_change(client, emitted):
    (a,) = make_lists("Swing")
    ref, before = f"list:{a}", versions.read()
    assert set_pref(client, ref, sort="change", descending=True).get_json()["status"] == "ok"
    assert pref_of(client, a) == {"sort": "change", "descending": True, "basis": "close",
                                  "held": False}  # fmt: skip
    assert set_pref(client, ref, basis="open", held=True).get_json()["status"] == "ok"
    assert pref_of(client, a) == {"sort": "change", "descending": True, "basis": "open",
                                  "held": True}  # fmt: skip
    assert set_pref(client, ref, descending=False).status_code == 200
    assert pref_of(client, a)["descending"] is False
    assert len(rows(schema.PrefRow)) == 1
    assert emitted == [] and versions.read() == before  # a preference bumps nothing


def test_a_preference_can_be_kept_for_a_ranked_list_and_is_refused_for_what_is_not_there(client):
    (a,) = make_lists("Swing")
    assert set_pref(client, RANKED, sort="price").status_code == 200
    assert [r.ref for r in rows(schema.PrefRow)] == [RANKED]
    assert set_pref(client, "list:9999", sort="name").get_json()["code"] == "gone"
    assert set_pref(client, "preset:x", sort="name").get_json()["code"] == "gone"
    assert set_pref(client, "nonsense", sort="name").get_json()["code"] == "invalid"
    nothing = set_pref(client, f"list:{a}").get_json()
    assert (nothing["code"], nothing["message"]) == ("invalid", words.PREF_NOTHING)
    assert set_pref(client, f"list:{a}", sort="sideways").get_json()["code"] == "invalid"
    assert len(rows(schema.PrefRow)) == 1


# -- the lists op ------------------------------------------------------------------------------
def test_lists_answers_own_lists_in_openalgos_order_with_counts_and_room(client):
    from database import watchlist_db as oa

    a, b = make_lists("Swing", "Banks", items=("INFY", "TCS", "SBIN"))
    oa.db_session.query(oa.Watchlist).filter_by(id=a).update({"position": 5})
    oa.db_session.query(oa.Watchlist).filter_by(id=b).update({"position": 1})
    oa.db_session.commit()
    oa.db_session.remove()
    with engine.open_session() as s:  # two follow rows count; a reused id's rows would not
        s.add(
            schema.ListRow(
                list_id=a,
                oa_created_at=lists_op.merge_view.created_key(_created(a)),
                version=1,
                updated_at=NOW,
            )
        )
        s.add_all(
            [
                schema.FollowRow(
                    list_id=a,
                    position=n,
                    kind="month_future",
                    underlying="NIFTY",
                    underlying_exchange="NSE_INDEX",
                    expiry_rule="month",
                    created_at=NOW,
                    updated_at=NOW,
                )
                for n in (1, 2)
            ]
        )
        s.commit()
    data = client.get(API + "/lists").get_json()["data"]
    assert [(e["ref"], e["name"], e["count"], e["room"]) for e in data["own"]] == [
        (f"list:{b}", "Banks", 3, 247),
        (f"list:{a}", "Swing", 5, 247),
    ]  # follow rows count
    assert all(e["kind"] == "list" and not e["locked"] for e in data["own"])
    assert data["version"] == versions.read()


def _created(lid):
    from database import watchlist_db as oa

    got = oa.db_session.query(oa.Watchlist).filter_by(id=lid).one().created_at
    oa.db_session.remove()
    return got


def test_lists_answers_the_catalogues_the_flags_and_the_favourites(client):
    (a,) = make_lists("Swing")
    with engine.open_session() as s:
        s.add(schema.SymbolRow(exchange="NSE", symbol="INFY", flag=3, updated_at=NOW))
        s.commit()
    put(client, [(1, RANKED)], 0)
    data = client.get(API + "/lists").get_json()["data"]
    assert data["presets"] == []  # none until the built-in lists are built (W12)
    assert [e["ref"] for e in data["ranked"]] == [e.ref for e in ranked.catalogue()]
    assert len(data["ranked"]) == 44 and all(e["locked"] for e in data["ranked"])
    assert [(e["ref"], e["name"], e["count"]) for e in data["flags"]] == [
        ("flag:1", "Watch", 0), ("flag:2", "Breakout", 0), ("flag:3", "Results", 1),
        ("flag:4", "Long term", 0), ("flag:5", "Avoid", 0)]  # fmt: skip
    assert [f["ref"] for f in data["favourites"]] == [RANKED]


def test_every_record_matches_the_contract(client):
    make_lists("Swing")
    put(client, [(1, "flag:1")], 0)
    want = {f.name for f in contract.record("Lists")}
    assert set(client.get(API + "/lists").get_json()["data"]) == want
    entry = {f.name for f in contract.record("Entry")}
    fav = {f.name for f in contract.record("Favourite")}
    data = client.get(API + "/lists").get_json()["data"]
    assert all(set(e) == entry for k in ("own", "ranked", "flags") for e in data[k])
    assert all(set(f) == fav for f in data["favourites"])
