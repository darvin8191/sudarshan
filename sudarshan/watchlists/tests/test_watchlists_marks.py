"""Notes, flags, flag lists and expired contracts (M8.6, W9), through the real bridge, with
OpenAlgo's lists in their own database and the overlay in a throw-away file."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from watchlists_bridge_app import OWNER, csrf_headers, login_session, make_app, reset_users

import blueprints.sudarshan_watchlists as bp_module
from sudarshan.watchlists import contract, engine, master, ops, refs, versions, view
from sudarshan.watchlists.ops.state import DEFAULT_FLAG_NAMES
from sudarshan.watchlists.store import symbols as store
from sudarshan.watchlists.words import marks as words

API = contract.API_PREFIX
LISTED = ("INFY", "TCS", "SBIN", "RELIANCE")


@pytest.fixture(autouse=True)
def store_file(tmp_path, monkeypatch):
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
    rows = {(s, "NSE"): SimpleNamespace(name=s, lotsize=1, tick_size=0.05, expiry=None, token=None)
            for s in LISTED}  # fmt: skip
    snap = SimpleNamespace(by_symbol_exchange=rows)
    monkeypatch.setattr(master, "openalgo_snapshot", lambda: snap)
    clock = iter(range(1_700_000_000_000, 1_800_000_000_000, 1000))  # every write a second later
    monkeypatch.setattr("sudarshan.watchlists.store.sections.now_ms", lambda: next(clock))
    monkeypatch.setattr(store, "now_ms", lambda: next(clock))
    yield snap
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


def make_list(items=("INFY", "TCS"), name="Swing"):
    from database import watchlist_db as oa

    made = oa.create_watchlist(OWNER, name, [{"symbol": s, "exchange": "NSE"} for s in items])
    oa.db_session.remove()
    return made["id"]


def mark(client, key, version=None, **fields):
    body = dict(fields)
    if version is not None:
        body["version"] = int(version)  # the answers carry it as text, the requests as a number
    return client.put(f"{API}/symbols/{key}", json=body, headers=client.headers)


def rename(client, names, version):
    return client.put(API + "/flags", json={"names": names, "version": version},
                      headers=client.headers)  # fmt: skip


def clear_expired(client, version):
    return client.post(API + "/symbols/expired/clear", json={"version": version},
                       headers=client.headers)  # fmt: skip


def read(client, ref):
    r = client.get(API + "/view", query_string={"ref": ref})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]


def info(client, key):
    return client.get(API + "/symbol", query_string={"k": key}).get_json()["data"]


def rows_of(client, ref):
    return [(r["contract"]["key"], r["flag"], r["has_note"], r["expired"])
            for r in read(client, ref)["rows"]]  # fmt: skip


def table(name):
    with engine.open_session() as s:
        return [tuple(r) for r in s.execute(text(f"SELECT * FROM {name} ORDER BY 1, 2")).all()]


def refused(r, status, code, field=None):
    body = r.get_json()
    assert (r.status_code, body["code"], body.get("field")) == (status, code, field), body
    return body["message"]


# -- notes and flags -----------------------------------------------------------------------------
def test_a_note_of_501_characters_is_refused_in_words_and_changes_nothing(client, emitted):
    long = "x" * 501
    assert refused(mark(client, "NSE:INFY", note=long), 400, "invalid", "note") == words.NOTE_LONG
    assert table("wl_symbol") == [] and emitted == []
    assert mark(client, "NSE:INFY", note="x" * 500).status_code == 200
    assert len(info(client, "NSE:INFY")["note"]) == 500


def test_flag_and_note_are_kept_and_a_symbol_with_neither_is_deleted(client):
    lid = make_list()
    first = mark(client, "NSE:INFY", flag=2, note="  Results on Friday  ").get_json()["data"]
    assert first["id"] is None and first["words"] is None
    got = info(client, "NSE:INFY")
    assert (got["flag"], got["flag_name"], got["note"]) == (2, "Breakout", "Results on Friday")
    assert got["version"] == int(first["version"]) and got["note_at"] is not None
    assert rows_of(client, f"list:{lid}") == [
        ("NSE:INFY", 2, True, False),
        ("NSE:TCS", None, False, False),
    ]
    # clearing the flag keeps the note, clearing the note then removes the row
    second = mark(client, "NSE:INFY", version=first["version"], flag=None).get_json()["data"]
    assert (info(client, "NSE:INFY")["flag"], info(client, "NSE:INFY")["note"]) == (
        None,
        "Results on Friday",
    )
    last = mark(client, "NSE:INFY", version=second["version"], note="").get_json()["data"]
    assert last["version"] is None and table("wl_symbol") == []
    assert info(client, "NSE:INFY")["version"] is None


def test_a_flag_outside_one_to_five_is_refused_and_a_write_with_nothing_to_change_too(client):
    assert refused(mark(client, "NSE:INFY", flag=6), 400, "invalid", "flag") == words.FLAG_BAD
    assert refused(mark(client, "NSE:INFY", flag=0), 400, "invalid", "flag") == words.FLAG_BAD
    assert refused(mark(client, "NSE:INFY"), 400, "invalid", "flag") == words.NOTHING
    assert refused(mark(client, "INFY", flag=1), 400, "invalid", "k") == words.KEY_BAD
    assert table("wl_symbol") == []


def test_a_stale_version_answers_conflict_and_changes_nothing(client, emitted):
    made = mark(client, "NSE:INFY", flag=1, note="a").get_json()["data"]
    before, pushes = table("wl_symbol"), len(emitted)
    stale = str(int(made["version"]) - 1)
    for version in (stale, 0, None):  # an old one, a number no row holds, and "no row yet"
        got = mark(client, "NSE:INFY", version=version, flag=3, note="b")
        assert refused(got, 409, "conflict") == words.CONFLICT
    assert refused(mark(client, "NSE:TCS", version=5, flag=3), 409, "conflict") == words.CONFLICT
    assert refused(mark(client, "NSE:INFY", version=2**70, flag=3), 409, "conflict")
    assert table("wl_symbol") == before and len(emitted) == pushes


def test_the_version_a_write_answers_is_the_one_the_next_write_must_name(client):
    v1 = mark(client, "NSE:INFY", flag=1).get_json()["data"]["version"]
    v2 = mark(client, "NSE:INFY", version=v1, note="n").get_json()["data"]["version"]
    assert v2 != v1
    assert mark(client, "NSE:INFY", version=v1, note="again").status_code == 409
    assert mark(client, "NSE:INFY", version=v2, note="again").status_code == 200


# -- the bumps -----------------------------------------------------------------------------------
def test_one_bump_and_one_push_per_write_with_the_flag_lists_that_changed(client, emitted):
    v = mark(client, "NSE:INFY", flag=2).get_json()["data"]["version"]
    assert [(p["why"], p["ref"]) for p in emitted] == [("flags", "flag:2")]  # one list changed
    v = mark(client, "NSE:INFY", version=v, flag=4).get_json()["data"]["version"]
    assert [(p["why"], p["ref"]) for p in emitted[1:]] == [("flags", "lists")]  # two refs: lists
    v = mark(client, "NSE:INFY", version=v, flag=4, note="only a note").get_json()["data"][
        "version"
    ]
    assert [(p["why"], p["ref"]) for p in emitted[2:]] == [("flags", "lists")]  # no membership
    mark(client, "NSE:INFY", version=v, flag=None)
    assert [(p["why"], p["ref"]) for p in emitted[3:]] == [("flags", "flag:4")]
    assert len(emitted) == 4 and len({p["v"] for p in emitted}) == 4


def test_a_refused_write_makes_no_bump_and_no_push(client, emitted):
    mark(client, "NSE:INFY", flag=1, note="a")
    emitted.clear()
    for r in (mark(client, "NSE:INFY", flag=9), mark(client, "NSE:INFY", version=3, flag=2),
              mark(client, "NSE:TCS", note="x" * 501), rename(client, [], 0),
              rename(client, [{"mark": 1, "name": "Mine"}], 7)):  # fmt: skip
        assert r.status_code in (400, 409)
    assert emitted == []


# -- flag lists ----------------------------------------------------------------------------------
def test_a_flag_list_is_every_symbol_with_the_flag_across_lists_newest_flag_first(client):
    make_list(("INFY", "TCS"), "Swing")
    make_list(("TCS", "SBIN"), "Momentum")
    mark(client, "NSE:INFY", flag=3)
    mark(client, "NSE:TCS", flag=3)
    mark(client, "NSE:SBIN", flag=1)
    mark(client, "NSE:RELIANCE", flag=3, note="in no list")  # kept, and in the flag list
    got = read(client, "flag:3")
    assert [r["contract"]["key"] for r in got["rows"]] == ["NSE:RELIANCE", "NSE:TCS", "NSE:INFY"]
    assert (got["ref"], got["kind"], got["name"], got["locked"]) == (
        "flag:3",
        "flag",
        "Results",
        True,
    )
    assert (got["total"], got["sections"], got["follow"], got["lines"]) == (
        3,
        [],
        [],
        [words.FLAG_LIST],
    )
    assert [r["has_note"] for r in got["rows"]] == [True, False, False]
    assert all(
        r["item"] is None and r["section_id"] is None and r["flag"] == 3 for r in got["rows"]
    )
    assert [r["contract"]["key"] for r in read(client, "flag:1")["rows"]] == ["NSE:SBIN"]
    assert read(client, "flag:5")["rows"] == []
    mark(client, "NSE:INFY", version=info(client, "NSE:INFY")["version"], flag=None)  # moves out
    assert [r["contract"]["key"] for r in read(client, "flag:3")["rows"]] == [
        "NSE:RELIANCE",
        "NSE:TCS",
    ]


def test_moving_a_flag_puts_the_symbol_first_in_its_new_list_and_re_sending_it_keeps_its_place(
    client,
):
    mark(client, "NSE:INFY", flag=2)
    v = mark(client, "NSE:TCS", flag=2).get_json()["data"]["version"]
    assert [r[0] for r in rows_of(client, "flag:2")] == ["NSE:TCS", "NSE:INFY"]
    mark(client, "NSE:INFY", version=info(client, "NSE:INFY")["version"], flag=2, note="same flag")
    assert [r[0] for r in rows_of(client, "flag:2")] == ["NSE:TCS", "NSE:INFY"]
    mark(client, "NSE:TCS", version=v, flag=5)
    mark(client, "NSE:INFY", version=info(client, "NSE:INFY")["version"], flag=5)
    assert [r[0] for r in rows_of(client, "flag:5")] == ["NSE:INFY", "NSE:TCS"]


def test_a_ref_that_is_not_one_of_the_five_flags_is_invalid_and_the_kind_is_found_by_discovery(
    client,
):
    assert refs.readers()["flag"].KIND == "flag"
    for ref in ("flag:6", "flag:0", "flag:x", "flag:", "flag:01"):
        r = client.get(API + "/view", query_string={"ref": ref})
        assert (r.status_code, r.get_json()["code"], r.get_json()["field"]) == (
            400,
            "invalid",
            "ref",
        )


def test_notes_never_leave_in_a_view_and_the_flag_reader_has_no_members_yet(client):
    lid = make_list()
    mark(client, "NSE:INFY", flag=1, note="private words")
    for ref in ("flag:1", f"list:{lid}"):
        assert "private words" not in json.dumps(read(client, ref))
    with pytest.raises(NotImplementedError):
        refs.readers()["flag"].members("1", OWNER)


# -- expired contracts ---------------------------------------------------------------------------
def test_an_expired_contract_keeps_its_flag_and_reads_as_expired(client, store_file):
    lid = make_list(("INFY", "OLD25DEC24000CE"))
    mark(client, "NSE:INFY", flag=1)
    mark(client, "NSE:OLD25DEC24000CE", flag=1, note="kept")
    assert rows_of(client, "flag:1") == [
        ("NSE:OLD25DEC24000CE", 1, True, True), ("NSE:INFY", 1, False, False)]  # fmt: skip
    got = read(client, "flag:1")
    assert got["missing"] == ["NSE:OLD25DEC24000CE"]
    assert read(client, f"list:{lid}")["missing"] == ["NSE:OLD25DEC24000CE"]
    sheet = info(client, "NSE:OLD25DEC24000CE")
    assert (sheet["expired"], sheet["note"]) == (True, "kept")
    assert info(client, "NSE:INFY")["expired"] is False


def test_with_the_master_not_loaded_nothing_reads_as_expired_and_clearing_waits(
    client, monkeypatch
):
    mark(client, "NSE:GONE", flag=2)
    monkeypatch.setattr(master, "openalgo_snapshot", lambda: None)
    assert rows_of(client, "flag:2") == [("NSE:GONE", 2, False, False)]
    assert info(client, "NSE:GONE")["expired"] is False
    assert refused(clear_expired(client, 0), 500, "failed") == words.NOT_LOADED
    assert len(table("wl_symbol")) == 1


def test_clear_expired_removes_only_the_expired_and_moves_the_flags_version(client, emitted):
    mark(client, "NSE:INFY", flag=1, note="live")
    mark(client, "NSE:GONE1", flag=2)
    mark(client, "NSE:GONE2", note="only a note")
    mark(client, "NSE:GONE3", flag=2, note="both")
    emitted.clear()
    at = versions.flags() or 0
    done = clear_expired(client, at).get_json()["data"]
    assert (done["version"], done["words"]) == (str(at + 1), words.cleared(3))
    assert [r[:2] for r in table("wl_symbol")] == [("owner", "NSE")] and table("wl_symbol")[0][
        2
    ] == "INFY"
    assert (info(client, "NSE:INFY")["flag"], info(client, "NSE:INFY")["note"]) == (1, "live")
    assert [(p["why"], p["ref"], p["flags"]) for p in emitted] == [("flags", "flag:2", at + 1)]
    again = clear_expired(client, at + 1)  # nothing left: said so, nothing moved
    assert again.get_json()["data"] == {
        "version": str(at + 1),
        "id": None,
        "words": words.NONE_EXPIRED,
    }
    assert versions.flags() == at + 1


def test_clear_expired_on_a_stale_flags_version_is_conflict_and_removes_nothing(client):
    mark(client, "NSE:GONE", flag=2)
    assert refused(clear_expired(client, 4), 409, "conflict") == words.CONFLICT
    assert len(table("wl_symbol")) == 1


# -- the five names ------------------------------------------------------------------------------
def test_flags_read_as_their_default_names_and_nothing_is_written_until_one_is_renamed(client):
    got = client.get(API + "/state").get_json()["data"]
    assert [f["name"] for f in got["flag_names"]] == list(DEFAULT_FLAG_NAMES)
    assert table("wl_flag") == [] and info(client, "NSE:INFY")["flag_name"] is None
    done = rename(client, [{"mark": 2, "name": " Setup "}, {"mark": 5, "name": "Avoid"}], 0)
    assert done.get_json()["data"] == {"version": "1", "id": None, "words": None}
    assert table("wl_flag") == [("owner", 2, "Setup")]  # a default name is never stored as chosen
    names = client.get(API + "/state").get_json()["data"]["flag_names"]
    assert [f["name"] for f in names] == ["Watch", "Setup", "Results", "Long term", "Avoid"]
    assert client.get(API + "/state").get_json()["data"]["versions"]["flags"] == 1
    mark(client, "NSE:INFY", flag=2)
    assert (
        info(client, "NSE:INFY")["flag_name"] == "Setup"
        and read(client, "flag:2")["name"] == "Setup"
    )
    rename(client, [{"mark": 2, "name": "Breakout"}], 1)  # back to the default: the row goes
    assert table("wl_flag") == []


def test_renaming_is_one_bump_with_no_ref_and_a_stale_version_changes_nothing(client, emitted):
    rename(client, [{"mark": 1, "name": "Mine"}], 0)
    assert [(p["why"], p["ref"], p["flags"]) for p in emitted] == [("flags", "lists", 1)]
    assert (
        refused(rename(client, [{"mark": 1, "name": "Other"}], 0), 409, "conflict")
        == words.CONFLICT
    )
    assert refused(rename(client, [{"mark": 1, "name": "Other"}], 2), 409, "conflict")
    assert table("wl_flag") == [("owner", 1, "Mine")] and len(emitted) == 1
    assert rename(client, [{"mark": 1, "name": "Other"}], 1).status_code == 200


@pytest.mark.parametrize(
    ("names", "message"),
    [
        ([], words.NAMES_NONE),
        ([{"mark": 1, "name": "   "}], words.NAME_EMPTY),
        ([{"mark": 1, "name": "x" * 25}], words.NAME_LONG),
        ([{"mark": 6, "name": "Six"}], words.FLAG_BAD),
        ([{"mark": 1, "name": "A"}, {"mark": 1, "name": "B"}], words.NAME_TWICE),
    ],
)
def test_a_name_that_cannot_be_kept_is_refused_in_words(client, emitted, names, message):
    assert refused(rename(client, names, 0), 400, "invalid", "names") == message
    assert table("wl_flag") == []
    assert emitted == []
    assert rename(client, [{"mark": 1, "name": "x" * 24}], 0).status_code == 200  # 24 is allowed


# -- the symbol sheet ----------------------------------------------------------------------------
def test_symbol_answers_the_flag_the_note_and_the_own_lists_holding_it(client):
    a = make_list(("INFY", "TCS"), "Swing")
    make_list(("SBIN",), "Other")
    b = make_list(("TCS",), "Momentum")
    mark(client, "NSE:TCS", flag=5, note="watch the gap")
    got = info(client, "NSE:TCS")
    assert (got["key"], got["flag"], got["flag_name"], got["note"], got["expired"]) == (
        "NSE:TCS", 5, "Avoid", "watch the gap", False)  # fmt: skip
    assert got["lists"] == [
        {"ref": f"list:{a}", "name": "Swing"},
        {"ref": f"list:{b}", "name": "Momentum"},
    ]
    assert set(got) == {f.name for f in contract.record("SymbolInfo")}
    bare = info(client, "NSE:SBIN")
    assert (bare["flag"], bare["note"], bare["version"], bare["note_at"]) == (
        None,
        None,
        None,
        None,
    )
    assert info(client, "NSE:NOWHERE")["lists"] == []


def test_the_flag_view_matches_the_contracts_view_record_field_for_field(client):
    mark(client, "NSE:INFY", flag=1)
    got = read(client, "flag:1")
    assert set(got) == {f.name for f in contract.record("View")}
    assert set(got["rows"][0]) == {f.name for f in contract.record("Row")}
    assert view.merge_flag(1, "Watch", [], lambda e, s: None)["rows"] == []
