"""Follow rows: store and ops (M8.10, M8.11, W15), through the real bridge, with OpenAlgo's list in
its own database, the overlay in a throw-away file and a small master of the tests' own."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from watchlists_bridge_app import OWNER, csrf_headers, login_session, make_app, reset_users
from watchlists_follow_master import build_master

import blueprints.sudarshan_watchlists as bp_module
from sudarshan.watchlists import contract, engine, master, ops, schema, versions, view
from sudarshan.watchlists.words import rows as words
from sudarshan.watchlists.words import sections as section_words

NOW = 1_700_000_000_000
ROWS = contract.API_PREFIX + "/rows"


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
    snap = build_master()
    monkeypatch.setattr(master, "openalgo_snapshot", lambda: snap)
    master.shared().drop()
    yield tmp_path / "wl.db"
    master.shared().drop()
    engine.dispose()
    versions.forget()
    bp_module._record.clear()


def v(n: int) -> str:
    return "0" if n == 0 else str(NOW + n - 1)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    from sudarshan.watchlists.store import sections as sections_store

    monkeypatch.setattr(sections_store, "now_ms", lambda: NOW)


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


def make_list(items=("INFY",), name="Swing"):
    from database import watchlist_db as oa

    made = oa.create_watchlist(OWNER, name, [{"symbol": s, "exchange": "NSE"} for s in items])
    created = oa.db_session.query(oa.Watchlist).filter_by(id=made["id"]).one().created_at
    oa.db_session.remove()
    return made["id"], view.created_key(created)


def add(client, lid, version, **more):
    body = {"list": lid, "version": version, "kind": "atm_option", "underlying": "NIFTY",
            "underlying_exchange": "NSE_INDEX", "opt_right": "CE", **more}  # fmt: skip
    return client.post(ROWS, json=body, headers=client.headers)


def edit(client, rid, version, **fields):
    return client.patch(
        f"{ROWS}/{rid}", json={"version": version, **fields}, headers=client.headers
    )


def remove(client, rid, version):
    return client.delete(f"{ROWS}/{rid}", query_string={"version": version}, headers=client.headers)


def read(client, lid):
    return client.get(
        contract.API_PREFIX + "/view", query_string={"ref": f"list:{lid}"}
    ).get_json()["data"]


def table(name):
    with engine.open_session() as s:
        return [tuple(r) for r in s.execute(text(f"SELECT * FROM {name} ORDER BY 1, 2")).all()]


def refused(r, status, code, field=None):
    body = r.get_json()
    assert (r.status_code, body["code"], body.get("field")) == (status, code, field), body
    return body["message"]


# -- the writes ----------------------------------------------------------------------------------
def test_add_edit_and_remove_each_move_the_version_once_and_push_the_list(client, emitted):
    lid, _ = make_list()
    done = add(client, lid, "0", offset=2).get_json()["data"]
    assert done["version"] == v(1) and done["words"] is None and isinstance(done["id"], int)
    rid = done["id"]
    (got,) = read(client, lid)["follow"]
    assert (got["id"], got["kind"], got["underlying"], got["underlying_exchange"]) == (
        rid, "atm_option", "NIFTY", "NSE_INDEX")  # fmt: skip
    assert (got["expiry_rule"], got["strike_rule"], got["offset"], got["opt_right"]) == (
        "nearest", "atm", 2, "CE")  # fmt: skip
    assert (got["strike"], got["section_id"], got["position"]) == (None, None, 1)
    assert read(client, lid)["version"] == v(1)
    edited = edit(client, rid, v(1), offset=-3, opt_right="PE", expiry_rule="next")
    assert edited.get_json()["data"]["version"] == v(2)
    (got,) = read(client, lid)["follow"]
    assert (got["offset"], got["opt_right"], got["expiry_rule"]) == (-3, "PE", "next")
    assert remove(client, rid, v(2)).get_json()["data"]["version"] == v(3)
    assert read(client, lid)["follow"] == [] and table("wl_row") == []
    assert [(e["why"], e["ref"], e["refs"]) for e in emitted] == [
        ("rows", f"list:{lid}", [f"list:{lid}"])
    ] * 3


def test_a_future_a_strike_pair_and_a_fixed_strike_row_store_what_they_follow(client):
    lid, _ = make_list()
    add(
        client,
        lid,
        "0",
        kind="month_future",
        underlying="SBIN",
        underlying_exchange="NSE",
        opt_right="CE",
        offset=4,
    )  # a side and an offset mean nothing to a future
    add(client, lid, v(1), kind="strike_pair", opt_right="PE", offset=-1, expiry_rule="month")
    add(client, lid, v(2), kind="strike_pair", strike_rule="fixed", strike=25900, opt_right=None)
    fut, pair, fixed = read(client, lid)["follow"]
    assert (fut["kind"], fut["expiry_rule"], fut["strike_rule"], fut["offset"], fut["strike"],
            fut["opt_right"]) == ("month_future", "month", None, None, None, None)  # fmt: skip
    assert (pair["kind"], pair["offset"], pair["opt_right"]) == ("strike_pair", -1, None)
    assert (fixed["strike_rule"], fixed["strike"], fixed["offset"]) == ("fixed", 25900.0, 0)
    assert [f["position"] for f in (fut, pair, fixed)] == [1, 2, 3]
    with engine.open_session() as s:
        assert s.scalars(
            select(schema.FollowRow.strike_paise).order_by(schema.FollowRow.id)
        ).all() == [None, None, 2590000]


def test_a_section_holds_a_row_and_a_row_can_move_between_sections(client):
    from sudarshan.watchlists.store import sections as sections_store

    lid, created = make_list()
    sid = client.post(contract.API_PREFIX + "/sections", json={"list": lid, "name": "Hedge",
        "version": "0"}, headers=client.headers).get_json()["data"]["id"]  # fmt: skip
    rid = add(client, lid, v(1), section_id=sid).get_json()["data"]["id"]
    assert read(client, lid)["follow"][0]["section_id"] == sid
    assert edit(client, rid, v(2), section_id=None).status_code == 200
    assert read(client, lid)["follow"][0]["section_id"] is None
    assert sections_store.find  # the section store is the one W7 wrote
    refused(add(client, lid, v(3), section_id=sid + 50, offset=1), 404, "gone")


# -- unchanged writes ----------------------------------------------------------------------------
def test_adding_what_the_list_already_follows_changes_nothing_and_pushes_nothing(client, emitted):
    lid, _ = make_list()
    first = add(client, lid, "0", offset=1).get_json()["data"]
    n = len(emitted)
    again = add(client, lid, v(1), offset=1).get_json()["data"]
    assert (again["id"], again["version"], again["words"]) == (first["id"], v(1), words.same_row())
    assert "lists_version" not in again and len(emitted) == n
    assert read(client, lid)["version"] == v(1) and len(table("wl_row")) == 1
    assert add(client, lid, "0", offset=1).status_code == 409  # a stale version is still a conflict


def test_an_edit_that_changes_nothing_writes_and_pushes_nothing(client, emitted):
    lid, _ = make_list()
    rid = add(client, lid, "0", offset=1).get_json()["data"]["id"]
    n, before = len(emitted), table("wl_row")
    same = edit(client, rid, v(1), offset=1, opt_right="CE", underlying="NIFTY").get_json()["data"]
    assert (same["version"], same["id"]) == (v(1), rid) and "lists_version" not in same
    assert len(emitted) == n and table("wl_row") == before and read(client, lid)["version"] == v(1)
    assert edit(client, rid, v(1), section_id=None).get_json()["data"]["version"] == v(1)
    assert len(emitted) == n


def test_an_edit_into_another_row_of_the_list_is_refused(client):
    lid, _ = make_list()
    add(client, lid, "0", offset=1)
    rid = add(client, lid, v(1), offset=2).get_json()["data"]["id"]
    assert refused(edit(client, rid, v(2), offset=1), 400, "invalid", "kind") == words.same_row()
    assert read(client, lid)["version"] == v(2)


# -- the limits ----------------------------------------------------------------------------------
def test_a_list_holds_twenty_rows_and_the_twenty_first_is_a_limit(client, emitted):
    lid, _ = make_list()
    for n in range(20):
        assert add(client, lid, v(n), offset=n - 10).status_code == 200
    assert refused(add(client, lid, v(20), kind="month_future"), 422, "limit") == words.ROWS_FULL
    assert (
        read(client, lid)["version"] == v(20) and len(table("wl_row")) == 20 and len(emitted) == 20
    )


def test_the_rows_are_checked_and_each_refusal_leaves_the_version_where_it_was(client, emitted):
    lid, _ = make_list()
    refused(add(client, lid, "0", offset=11), 400, "invalid", "offset")
    refused(add(client, lid, "0", offset=-11), 400, "invalid", "offset")
    refused(add(client, lid, "0", opt_right=None), 400, "invalid", "opt_right")
    refused(add(client, lid, "0", strike_rule="fixed"), 400, "invalid", "strike")
    refused(add(client, lid, "0", strike_rule="fixed", strike=0), 400, "invalid", "strike")
    refused(add(client, lid, "0", strike_rule="fixed", strike=25900.005), 400, "invalid", "strike")
    refused(add(client, lid, "0", underlying="  "), 400, "invalid", "underlying")
    assert refused(
        add(client, lid, "0", underlying="NOPE"), 400, "invalid", "underlying"
    ) == words.not_listed("NOPE")
    assert refused(add(client, lid, "0", underlying="SBIN", underlying_exchange="NSE"), 400,
                   "invalid", "underlying") == words.no_options("SBIN", "NFO")  # fmt: skip
    assert refused(add(client, lid, "0", kind="month_future", underlying="SENSEX",
                       underlying_exchange="BSE_INDEX"), 400, "invalid", "underlying"
                   ) == words.no_futures("SENSEX", "BFO")  # fmt: skip
    assert read(client, lid)["version"] == "0" and table("wl_row") == [] and emitted == []


def test_mcx_and_other_exchanges_are_refused_in_words(client):
    lid, _ = make_list()
    said = refused(add(client, lid, "0", underlying="CRUDEOIL", underlying_exchange="MCX"),
                   400, "invalid", "underlying_exchange")  # fmt: skip
    assert said == words.EXCHANGE_LATER and "MCX" in said and "400" not in said
    assert refused(add(client, lid, "0", underlying_exchange="NFO"), 400, "invalid",
                   "underlying_exchange") == words.EXCHANGE_BAD  # fmt: skip
    assert table("wl_row") == []


def test_a_bse_underlying_follows_the_bfo_side(client):
    lid, _ = make_list()
    assert (
        add(client, lid, "0", underlying="SENSEX", underlying_exchange="BSE_INDEX").status_code
        == 200
    )


def test_with_the_master_not_loaded_a_row_is_not_added_and_says_so(client, monkeypatch):
    lid, _ = make_list()
    monkeypatch.setattr(master, "openalgo_snapshot", lambda: None)
    assert refused(add(client, lid, "0"), 500, "failed") == words.NOT_LOADED


# -- versions, lists and ids ---------------------------------------------------------------------
def test_a_stale_version_is_a_conflict_and_changes_nothing(client, emitted):
    lid, _ = make_list()
    rid = add(client, lid, "0", offset=1).get_json()["data"]["id"]
    n = len(emitted)
    said = refused(add(client, lid, "0", offset=2), 409, "conflict")
    assert said == section_words.CONFLICT
    refused(edit(client, rid, "0", offset=3), 409, "conflict")
    refused(remove(client, rid, "0"), 409, "conflict")
    refused(add(client, lid, "x", offset=2), 409, "conflict")
    assert (
        len(emitted) == n
        and len(table("wl_row")) == 1
        and read(client, lid)["follow"][0]["offset"] == 1
    )


def test_a_row_or_a_list_that_is_gone_says_so(client):
    lid, _ = make_list()
    refused(edit(client, 999, "0", offset=1), 404, "gone")
    refused(remove(client, 999, "0"), 404, "gone")
    refused(add(client, 4242, "0"), 404, "gone")
    refused(edit(client, 1, "0"), 400, "invalid", "kind")  # nothing to change


def test_a_deleted_lists_rows_go_with_its_overlay(client):
    from database import watchlist_db as oa
    from sudarshan.watchlists.store import prune

    lid, _ = make_list()
    add(client, lid, "0", offset=1)
    with engine.open_session() as s, s.begin():
        prune.drop_overlay(s, lid)
    assert table("wl_row") == []
    oa.db_session.remove()


# -- resolve, through the bridge -------------------------------------------------------------------------
RESOLVE = contract.API_PREFIX + "/resolve"
AT = int(
    __import__("datetime").datetime.fromisoformat("2026-11-20T11:00:00+05:30").timestamp() * 1000
)


@pytest.fixture
def world(monkeypatch):
    """The clock at 11:00 IST on a Friday, an open market, no calendar rows and a feed that has
    one tick; ``asked`` records what the feed was asked."""
    from sudarshan.watchlists import feedsnap, sessions
    from sudarshan.watchlists.ops import rows as rows_ops

    asked = []
    monkeypatch.setattr(rows_ops, "_now_ms", lambda: AT)
    monkeypatch.setattr(sessions, "market_open", lambda exchange, now: True)
    monkeypatch.setattr(sessions, "closes", lambda dates, exchange: {})

    def feed(keys):
        asked.append(keys)
        return {"NSE_INDEX|NIFTY": (25851.0, AT - 1000)} if "NSE_INDEX|NIFTY" in keys else {}

    monkeypatch.setattr(feedsnap, "read", feed)
    return asked


def ask(client, rows, connected=True):
    return client.post(RESOLVE, json={"rows": rows, "connected": connected}, headers=client.headers)


def page(i, price=25912.35, source="tick", held=None):
    return {"id": i, "price": price, "price_at": AT - 500, "source": source, "held": held}


def test_resolve_answers_each_row_with_its_contracts_and_writes_nothing(client, emitted, world):
    lid, _ = make_list()
    a = add(client, lid, "0", offset=1).get_json()["data"]["id"]
    b = add(client, lid, v(1), kind="strike_pair").get_json()["data"]["id"]
    c = add(client, lid, v(2), kind="month_future").get_json()["data"]["id"]
    n, before = len(emitted), (table("wl_row"), table("wl_list"))
    r = ask(client, [page(a), page(b), page(c, None, None)])
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    first, pair, fut = r.get_json()["data"]["rows"]
    assert (first["id"], first["state"], first["strike"]) == (a, "ok", 25950.0)
    assert first["ce"]["symbol"] == "NIFTY24NOV2625950CE" and first["pe"] is None
    assert (pair["ce"]["symbol"], pair["pe"]["symbol"]) == (
        "NIFTY24NOV2625900CE", "NIFTY24NOV2625900PE")  # fmt: skip
    assert fut["future"]["symbol"] == "NIFTY24NOV26FUT" and fut["state"] == "ok"
    assert first["valid_until"] == AT + (4 * 24 * 3600 + 4 * 3600 + 40 * 60) * 1000
    assert (len(emitted), (table("wl_row"), table("wl_list"))) == (n, before)  # no write, no push
    assert world == []  # the page had a price for every row that needs one


def test_resolve_asks_the_feed_once_for_rows_the_page_has_no_price_for(client, world):
    lid, _ = make_list()
    a = add(client, lid, "0").get_json()["data"]["id"]
    b = add(client, lid, v(1), offset=2).get_json()["data"]["id"]
    got = ask(client, [page(a, None, None), page(b, None, None)]).get_json()["data"]["rows"]
    assert world == [["NSE_INDEX|NIFTY"]]
    assert [g["strike"] for g in got] == [25850.0, 25950.0]
    assert got[0]["words"] == "Now: NIFTY 25,850 CE, 24 Nov 2026 (NIFTY at 25,851)"


def test_resolve_names_a_row_that_is_gone_and_checks_its_request(client, world):
    lid, _ = make_list()
    a = add(client, lid, "0").get_json()["data"]["id"]
    got = ask(client, [page(a), page(999)]).get_json()["data"]["rows"]
    assert [(g["id"], g["state"]) for g in got] == [(a, "ok"), (999, "missing")]
    assert got[1]["words"] == words.ROW_GONE
    assert ask(client, []).get_json()["data"] == {"rows": []}
    refused(client.post(RESOLVE, json={"rows": [{"id": 1}], "connected": True},
                        headers=client.headers), 400, "invalid", "rows")  # fmt: skip
    refused(
        client.post(RESOLVE, json={"rows": []}, headers=client.headers), 400, "invalid", "connected"
    )
    many = [page(i) for i in range(1, 102)]
    assert refused(ask(client, many), 400, "invalid", "rows") == words.TOO_MANY


def test_resolve_with_the_master_not_loaded_says_so_and_a_list_of_gone_rows_does_not(
    client, world, monkeypatch
):
    lid, _ = make_list()
    a = add(client, lid, "0").get_json()["data"]["id"]
    monkeypatch.setattr(master, "openalgo_snapshot", lambda: None)
    assert refused(ask(client, [page(a)]), 500, "failed") == words.NOT_LOADED
    assert ask(client, [page(999)]).get_json()["data"]["rows"][0]["state"] == "missing"


def test_resolve_follows_the_rows_through_a_roll_without_a_write(client, world, monkeypatch):
    from sudarshan.watchlists.ops import rows as rows_ops

    lid, _ = make_list()
    a = add(client, lid, "0").get_json()["data"]["id"]
    first = ask(client, [page(a)]).get_json()["data"]["rows"][0]
    close = first["valid_until"]
    assert first["rolled"] is False
    monkeypatch.setattr(rows_ops, "_now_ms", lambda: close + 1000)
    after = ask(client, [page(a)]).get_json()["data"]["rows"][0]
    assert (
        after["ce"]["expiry"] == "2026-12-01" and after["words"] == "Rolled to 1 Dec 2026 at 15:40"
    )
    assert after["rolled"] is True  # the flag, so the page need not read the words
    monkeypatch.setattr(rows_ops, "_now_ms", lambda: close + 61_000)
    late = ask(client, [page(a)]).get_json()["data"]["rows"][0]
    assert late["rolled"] is False and not late["words"].startswith("Rolled to ")
    # a draft that rolls says so in the same way
    monkeypatch.setattr(rows_ops, "_now_ms", lambda: close + 1000)
    (preview,) = ask_drafts(client, [draft()]).get_json()["data"]["drafts"]
    assert preview["rolled"] is True
    assert len(table("wl_row")) == 1


# -- draft rows (M8.10, the add form's preview) --------------------------------------------------
def draft(**more):
    return {"kind": "atm_option", "underlying": "NIFTY", "underlying_exchange": "NSE_INDEX",
            "opt_right": "CE", "price": 25912.35, "price_at": AT - 500, "source": "tick",
            **more}  # fmt: skip


def ask_drafts(client, drafts, rows=(), connected=True):
    body = {"rows": list(rows), "connected": connected, "drafts": drafts}
    return client.post(RESOLVE, json=body, headers=client.headers)


def test_a_draft_row_is_previewed_in_the_shape_of_a_stored_one_and_nothing_is_written(
    client, emitted, world
):
    lid, _ = make_list()
    a = add(client, lid, "0", offset=1).get_json()["data"]["id"]
    n, before = len(emitted), (table("wl_row"), table("wl_list"))
    r = ask_drafts(client, [draft(), draft(kind="month_future", price=None), draft(offset=2)],
                   rows=[page(a)])  # fmt: skip
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert [x["id"] for x in data["rows"]] == [a]  # the stored row is answered as before
    atm, fut, two = data["drafts"]
    assert [d["id"] for d in data["drafts"]] == [0, 0, 0]
    assert (atm["state"], atm["strike"], atm["ce"]["symbol"], atm["pe"]) == (
        "ok", 25900.0, "NIFTY24NOV2625900CE", None)  # fmt: skip
    assert atm["words"] == "Now: NIFTY 25,900 CE, 24 Nov 2026 (NIFTY at 25,912.35)"
    assert fut["future"]["symbol"] == "NIFTY24NOV26FUT" and fut["strike"] is None
    assert two["strike"] == 26000.0  # in the order sent, each its own rule
    assert (len(emitted), (table("wl_row"), table("wl_list"))) == (n, before)
    assert table("wl_row") == list(before[0])  # still the one stored row


def test_a_draft_without_stored_rows_and_without_drafts_keeps_the_old_answer(client, world):
    assert ask(client, []).get_json()["data"] == {"rows": []}  # no drafts key when none were sent
    only = ask_drafts(client, [draft()]).get_json()["data"]
    assert only["rows"] == [] and len(only["drafts"]) == 1
    assert ask_drafts(client, []).get_json()["data"] == {"rows": [], "drafts": []}


def test_a_draft_is_checked_as_row_add_checks_a_row(client, world, monkeypatch):
    refused(ask_drafts(client, [draft(kind="nope")]), 400, "invalid", "drafts")  # not an enum value
    refused(ask_drafts(client, [draft(offset=99)]), 400, "invalid", "offset")
    refused(ask_drafts(client, [draft(opt_right=None)]), 400, "invalid", "opt_right")
    refused(ask_drafts(client, [draft(underlying="GHOSTCO")]), 400, "invalid", "underlying")
    refused(ask_drafts(client, [draft(), {"underlying": "NIFTY"}]), 400, "invalid", "drafts")
    monkeypatch.setattr(master, "openalgo_snapshot", lambda: None)
    assert refused(ask_drafts(client, [draft()]), 500, "failed") == words.NOT_LOADED


def test_drafts_count_in_the_limit_of_a_hundred_with_the_stored_rows(client, world):
    ok = ask_drafts(client, [draft() for _ in range(100)])
    assert ok.status_code == 200 and len(ok.get_json()["data"]["drafts"]) == 100
    assert refused(ask_drafts(client, [draft() for _ in range(101)]), 400, "invalid", "drafts") == (
        words.TOO_MANY)  # fmt: skip
    rows = [page(i) for i in range(1, 51)]
    assert ask_drafts(client, [draft() for _ in range(50)], rows).status_code == 200
    assert refused(ask_drafts(client, [draft() for _ in range(51)], rows), 400, "invalid",
                   "drafts") == words.TOO_MANY  # fmt: skip


def test_a_stored_id_the_page_sent_is_never_taken_for_a_draft(client, world):
    got = ask_drafts(client, [draft()], rows=[page(2**31), page(-1)]).get_json()["data"]
    assert [(x["id"], x["state"]) for x in got["rows"]] == [(2**31, "missing"), (-1, "missing")]
    assert len(got["drafts"]) == 1 and got["drafts"][0]["state"] == "ok"
