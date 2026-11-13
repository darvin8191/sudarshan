"""tv_export (M8.12, W20) through the real bridge: a list, a section, a built-in, a flag and a
ranked list, from OpenAlgo's lists in their own database and the overlay in a throw-away file."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from watchlists_bridge_app import OWNER, csrf_headers, login_session, make_app, reset_users
from watchlists_follow_master import Snap, cash, fo

import blueprints.sudarshan_watchlists as bp_module
from sudarshan.watchlists import contract, engine, feedsnap, master, ops, refs, tvimport, versions
from sudarshan.watchlists.ops import tvexport as op

API = contract.API_PREFIX
V = json.loads((Path(__file__).parent / "vectors" / "tvimport.json").read_text(encoding="utf-8"))
START = 1_700_000_000_000
NOW = int(datetime.fromisoformat("2026-11-13T11:00:00+05:30").timestamp() * 1000)


def make_snap():
    rows = []
    for key in V["known"]:
        exch, sym = key.split(":", 1)
        rows.append(cash(sym, exch))
        if exch in ("NSE", "NSE_INDEX", "BSE_INDEX", "BSE") and sym != "INDIAVIX":
            fx = "NFO" if exch.startswith("NSE") else "BFO"
            rows.append(fo(f"{sym}26NOV26FUT", sym, fx, "FUT", "24-NOV-26"))
            rows.append(fo(f"{sym}26DEC26FUT", sym, fx, "FUT", "29-DEC-26"))
    rows.append(fo("NIFTY27JAN27FUT", "NIFTY", "NFO", "FUT", "27-JAN-27"))
    for k in (25800.0, 25900.0):
        for typ in ("CE", "PE"):
            rows.append(fo(f"NIFTY26NOV26{int(k)}{typ}", "NIFTY", "NFO", typ, "24-NOV-26", k))
    return Snap(rows)


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
    snap = make_snap()
    monkeypatch.setattr(master, "openalgo_snapshot", lambda: snap)
    master.shared().drop()
    monkeypatch.setattr(op, "_now_ms", lambda: NOW)
    monkeypatch.setattr(feedsnap, "read", lambda keys: {})
    yield
    master._shared.get().drop()
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


def make_list(items=(), name="Swing"):
    from database import watchlist_db as oa

    made = oa.create_watchlist(OWNER, name, [{"exchange": e, "symbol": s} for e, s in items])
    oa.db_session.remove()
    return made["id"]


def data(r):
    body = r.get_json()
    assert body["status"] == "ok", body
    return body["data"]


def refused(r, status, code, field=None):
    body = r.get_json()
    assert (r.status_code, body["code"], body.get("field")) == (status, code, field), body
    return body["message"]


def export(client, ref):
    return client.post(API + "/tv/export", json={"ref": ref}, headers=client.headers)


def version(client, lid):
    return data(client.get(API + "/view", query_string={"ref": f"list:{lid}"}))["version"]


def add_section(client, lid, name):
    body = {"list": lid, "name": name, "version": version(client, lid)}
    return data(client.post(API + "/sections", json=body, headers=client.headers))["id"]


def add_row(client, lid, **rule):
    body = {"list": lid, "version": version(client, lid), **rule}
    return data(client.post(API + "/rows", json=body, headers=client.headers))


def place(client, lid, sections, items):
    body = {"version": version(client, lid), "sections": sections, "items": items}
    data(client.put(f"{API}/lists/{lid}/arrange", json=body, headers=client.headers))


def follow(rule="month", **more):
    return {"kind": "month_future", "underlying": "NIFTY", "underlying_exchange": "NSE_INDEX",
            "expiry_rule": rule, **more}  # fmt: skip


def lists_snapshot():
    from database import watchlist_db as oa

    try:
        return [
            (w["id"], w["name"], sorted((i["exchange"], i["symbol"]) for i in w["items"]))
            for w in oa.get_watchlists(OWNER)
        ]
    finally:
        oa.db_session.remove()


def table_counts():
    from sqlalchemy import text

    with engine.open_session() as s:
        names = [r[0] for r in s.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))]
        return {n: s.execute(text(f"SELECT count(*) FROM {n}")).scalar() for n in names}


def test_a_list_exports_sections_symbols_and_follow_rows_and_reads_back(client):
    lid = make_list([("NSE", "SBIN"), ("NSE", "HDFCBANK"), ("NSE", "TCS"), ("NSE", "BAJAJ-AUTO"),
                     ("NSE_INDEX", "FINNIFTY"), ("NSE", "INFY")])  # fmt: skip
    banks = add_section(client, lid, "Banks")
    it = add_section(client, lid, "IT")
    place(client, lid, [banks, it], [
        {"exchange": "NSE", "symbol": "SBIN", "section_id": banks},
        {"exchange": "NSE", "symbol": "HDFCBANK", "section_id": banks},
        {"exchange": "NSE", "symbol": "TCS", "section_id": it},
        {"exchange": "NSE", "symbol": "INFY", "section_id": it},
    ])  # fmt: skip
    add_row(client, lid, section_id=it, **follow("month"))
    add_row(client, lid, **follow("next_month"))
    d = data(export(client, f"list:{lid}"))
    assert d["filename"] == "Swing.txt"
    assert (d["symbols"], d["sections"], d["left_out"]) == (8, 2, [])
    assert d["text"].count("###") == 2
    # the file TradingView reads: the import's own plan of it has the same symbols and sections
    got = data(client.post(API + "/tv/preview", json={"text": d["text"]}, headers=client.headers))
    assert (got["sections"], got["symbols"], got["matched"], got["left_out"]) == (2, 8, 8, [])
    plan = tvimport.parse(d["text"], master.shared().known)
    names = [s.name for s in plan.sections]
    assert names == [None, "Banks", "IT"]
    inside = {s.name: {(i.kind, i.symbol) for i in s.items} for s in plan.sections}
    assert inside["Banks"] == {("fixed", "SBIN"), ("fixed", "HDFCBANK")}
    assert inside["IT"] == {("follow", "NIFTY"), ("fixed", "TCS"), ("fixed", "INFY")}
    assert inside[None] == {("fixed", "BAJAJ-AUTO"), ("fixed", "FINNIFTY"), ("follow", "NIFTY")}


def test_it_writes_nothing_and_bumps_nothing(client, emitted):
    lid = make_list([("NSE", "SBIN")])
    add_row(client, lid, **follow("month"))
    emitted.clear()
    before = (lists_snapshot(), table_counts(), versions.read())
    data(export(client, f"list:{lid}"))
    assert (lists_snapshot(), table_counts(), versions.read()) == before and emitted == []


def test_a_section_exports_alone_and_the_file_is_named_for_both(client):
    lid = make_list([("NSE", "SBIN"), ("NSE", "TCS")], name="My: list?")
    banks = add_section(client, lid, "Banks")
    place(client, lid, [banks], [{"exchange": "NSE", "symbol": "SBIN", "section_id": banks}])
    d = data(export(client, f"list:{lid}/section:{banks}"))
    assert d["text"] == "###Banks,NSE:SBIN" and (d["symbols"], d["sections"]) == (1, 1)
    assert d["filename"] == "My list - Banks.txt"
    refused(export(client, f"list:{lid}/section:99"), 404, "gone")
    refused(export(client, "list:999"), 404, "gone")


def test_commas_in_section_names_are_dropped_and_said(client):
    lid = make_list([("NSE", "SBIN"), ("NSE", "TCS")])
    a, b = add_section(client, lid, "Banks, large"), add_section(client, lid, "IT, mid, small")
    place(client, lid, [a, b], [{"exchange": "NSE", "symbol": "SBIN", "section_id": a},
                                {"exchange": "NSE", "symbol": "TCS", "section_id": b}])  # fmt: skip
    d = data(export(client, f"list:{lid}"))
    assert d["text"] == "###Banks large,NSE:SBIN,###IT mid small,NSE:TCS"
    assert (
        "2 section names had a comma, which TradingView cannot read; it was left out." in d["words"]
    )
    assert [s.name for s in tvimport.parse(d["text"], master.shared().known).sections] == [
        "Banks large", "IT mid small"]  # fmt: skip


def test_future_rows_atm_rows_and_strike_rows(client, monkeypatch):
    lid = make_list(
        [("NFO", "NIFTY26NOV26FUT"), ("NFO", "NIFTY26DEC26FUT"), ("NFO", "NIFTY27JAN27FUT")]
    )
    add_row(client, lid, **follow("month"))
    add_row(client, lid, kind="atm_option", underlying="NIFTY", underlying_exchange="NSE_INDEX",
            expiry_rule="nearest", strike_rule="atm", offset=0, opt_right="CE")  # fmt: skip
    add_row(client, lid, kind="strike_pair", underlying="NIFTY", underlying_exchange="NSE_INDEX",
            expiry_rule="nearest", strike_rule="fixed", strike=25900)  # fmt: skip
    nifty = master.shared().row("NSE_INDEX", "NIFTY")
    monkeypatch.setattr(feedsnap, "read", lambda keys: {nifty.token: (25812.0, NOW)})
    d = data(export(client, f"list:{lid}"))
    tokens = d["text"].split(",")
    assert tokens[0] == "NSE:NIFTY1!"  # the follow row first, as a list shows them
    assert set(tokens) == {"NSE:NIFTY1!", "NSE:NIFTY2!", "NSE:NIFTY261124C25800",
                           "NSE:NIFTY261124C25900", "NSE:NIFTY261124P25900"}  # fmt: skip
    assert [x["token"] for x in d["left_out"]] == ["NFO:NIFTY27JAN27FUT"]
    assert d["left_out"][0]["reason"] == "dated_future"
    said = " ".join(d["words"])
    assert (
        "NSE:NIFTY1! is the current-month future of NIFTY, and follows it on TradingView." in said
    )
    assert "will not follow the price on TradingView" in said and "NSE:NIFTY261124C25900" in said
    assert "NSE:NIFTY2!" in said  # written as the rolling future, said so
    assert any(w.startswith("These are futures of one month") for w in d["words"])


def test_an_atm_row_with_no_price_is_left_out_in_words(client):
    lid = make_list([("NSE", "SBIN")])
    add_row(client, lid, kind="atm_option", underlying="NIFTY", underlying_exchange="NSE_INDEX",
            expiry_rule="nearest", strike_rule="atm", offset=0, opt_right="CE")  # fmt: skip
    d = data(export(client, f"list:{lid}"))
    assert d["text"] == "NSE:SBIN" and d["left_out"] == [{"token": "NIFTY", "reason": "waiting"}]
    assert "These rows had no price to work from, so they are left out: NIFTY." in d["words"]


def test_a_built_in_list_and_a_flag_list(client, monkeypatch):
    lid = make_list([("NSE", "SBIN"), ("NSE", "TCS")], name="Flagged")
    for sym, mark in (("SBIN", 2), ("TCS", 2)):
        data(client.put(f"{API}/symbols/NSE:{sym}", json={"flag": mark}, headers=client.headers))
    d = data(export(client, "flag:2"))
    assert sorted(d["text"].split(",")) == ["NSE:SBIN", "NSE:TCS"] and d["symbols"] == 2
    assert d["filename"].endswith(".txt") and lid
    from datetime import datetime as dt

    from presets_master import V as PV
    from presets_master import indices_rows, snapshot_of

    snap, _ = snapshot_of(PV["master"] + indices_rows(), dt(2026, 11, 9, 8, 40))
    monkeypatch.setattr(master, "openalgo_snapshot", lambda: snap)
    master.shared().drop()
    got = data(export(client, "preset:fno_stocks"))
    assert sorted(got["text"].split(",")) == sorted(
        ["NSE:BAJAJ_AUTO", "NSE:FARCO", "NSE:M_M", "NSE:RELIANCE"]
    )
    assert got["filename"] == "F&O stocks.txt" and got["left_out"] == []
    shown = data(client.get(API + "/view?ref=preset:indices", headers=client.headers))
    idx = data(export(client, "preset:indices"))
    assert idx["symbols"] + len(idx["left_out"]) == shown["total"]
    assert all(x["reason"] == "no_tv_name" for x in idx["left_out"])
    assert idx["symbols"] > 0 and idx["text"].startswith("NSE:")
    refused(export(client, "flag:9"), 400, "invalid", "ref")


def test_a_ranked_list_reads_through_its_view(client, monkeypatch):
    ranked = refs.readers()["ranked"]
    m = master.shared()
    rows = [
        {"item": None, "contract": _contract(m.row("NFO", "NIFTY26NOV26FUT")), "section_id": None,
         "flag": None, "has_note": False, "expired": False, "metric": "Traded Rs 1 cr"},
        {"item": None, "contract": _contract(m.row("NFO", "NIFTY27JAN27FUT")), "section_id": None,
         "flag": None, "has_note": False, "expired": False, "metric": None},
        {"item": None, "contract": _contract(m.row("NSE", "SBIN")), "section_id": None,
         "flag": None, "has_note": False, "expired": False, "metric": None},
    ]  # fmt: skip
    seen = []

    def fake(ident, user):
        seen.append(ident)
        return {"ref": "ranked:" + ident, "name": "Futures: OI gainers", "rows": rows,
                "sections": [], "follow": []}  # fmt: skip

    monkeypatch.setattr(ranked, "view", fake)
    d = data(export(client, "ranked:futures:INDEX:OI_GAINERS"))
    assert seen == ["futures:INDEX:OI_GAINERS"]
    assert d["text"] == "NSE:NIFTY1!,NSE:SBIN" and d["filename"] == "Futures OI gainers.txt"
    assert d["left_out"] == [{"token": "NFO:NIFTY27JAN27FUT", "reason": "dated_future"}]


def _contract(row):
    from sudarshan.watchlists import view as merge_view

    return merge_view.contract(row.exchange, row.symbol, row, None)


def test_a_list_with_nothing_exportable_says_so(client):
    lid = make_list([], name="Empty")
    d = data(export(client, f"list:{lid}"))
    assert (d["text"], d["symbols"], d["sections"]) == ("", 0, 0)
    assert d["words"] == ["Nothing in Empty can be exported to TradingView."]
    lid = make_list([("NFO", "NIFTY27JAN27FUT")], name="Old")
    d = data(export(client, f"list:{lid}"))
    assert d["text"] == "" and d["left_out"][0]["reason"] == "dated_future"
    assert d["words"][0] == "Nothing in Old can be exported to TradingView."
    assert d["words"][1].startswith("These are futures of one month")


def test_a_ref_that_is_not_one_is_invalid(client):
    refused(export(client, "nonsense"), 400, "invalid", "ref")
    refused(export(client, "list:abc"), 400, "invalid", "ref")
    r = client.post(API + "/tv/export", json={}, headers=client.headers)
    assert r.status_code == 400


def test_no_master_is_said_in_words_when_a_contract_needs_it(client, monkeypatch):
    lid = make_list([("NFO", "NIFTY26NOV26FUT")])
    add_row(client, lid, **follow("month"))
    monkeypatch.setattr(master, "openalgo_snapshot", lambda: None)
    master.shared().drop()
    r = export(client, f"list:{lid}")
    assert refused(r, 500, "failed").startswith("Today's contract list is not loaded yet")


def test_the_request_class_is_read_and_its_limit_holds():
    spec = next(o for o in contract.OPS if o.name == "tv_export")
    assert (spec.cls, spec.method, spec.route) == ("read", "POST", "/tv/export")
    assert contract.LIMITS_PER_MIN["read"] == 120
    app = make_app(ops.discover(), rate_limits=True, real=True)
    reset_users()
    c = app.test_client()
    login_session(c)
    headers = csrf_headers(c)
    codes = [
        c.post(API + "/tv/export", json={"ref": "flag:9"}, headers=headers).status_code
        for _ in range(contract.LIMITS_PER_MIN["read"] + 3)
    ]
    assert codes[: contract.LIMITS_PER_MIN["read"]].count(429) == 0
    assert codes[-1] == 429


def test_it_never_starts_a_thread_or_leaves_a_session_open(client):
    import threading

    lid = make_list([("NSE", "SBIN")])
    before = {t.name for t in threading.enumerate()}
    for _ in range(5):
        data(export(client, f"list:{lid}"))
    assert {t.name for t in threading.enumerate()} == before
