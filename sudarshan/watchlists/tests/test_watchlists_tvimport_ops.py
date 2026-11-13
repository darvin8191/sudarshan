"""tv_preview and tv_import (M8.12, W19), through the real bridge, with OpenAlgo's lists in their
own database and the overlay in a throw-away file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from watchlists_bridge_app import OWNER, csrf_headers, login_session, make_app, reset_users
from watchlists_follow_master import Snap, cash, fo

import blueprints.sudarshan_watchlists as bp_module
from sudarshan.watchlists import contract, engine, master, ops, tvimport, versions
from sudarshan.watchlists.words import tvimport as words

API = contract.API_PREFIX
V = json.loads((Path(__file__).parent / "vectors" / "tvimport.json").read_text(encoding="utf-8"))
START = 1_700_000_000_000


def make_snap():
    rows = []
    for key in V["known"]:
        exch, sym = key.split(":", 1)
        rows.append(cash(sym, exch))
        if exch in ("NSE", "NSE_INDEX", "BSE_INDEX", "BSE") and sym != "INDIAVIX":
            fx = "NFO" if exch.startswith("NSE") else "BFO"
            if (sym, fx) not in {(r.symbol, r.exchange) for r in rows}:
                rows.append(fo(f"{sym}26NOV26FUT", sym, fx, "FUT", "24-NOV-26"))
                rows.append(fo(f"{sym}26DEC26FUT", sym, fx, "FUT", "29-DEC-26"))
    for k in (25800.0, 25900.0):
        rows.append(fo(f"NIFTY26NOV26{int(k)}CE", "NIFTY", "NFO", "CE", "24-NOV-26", k))
    for i in range(400):  # a long file's worth of symbols
        rows.append(cash(f"Z{i:03d}", "NSE"))
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
    from sudarshan.watchlists.store import sections as sec_store

    monkeypatch.setattr(sec_store, "now_ms", lambda: START)
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


def make_list(symbols=(), name="Swing"):
    from database import watchlist_db as oa

    made = oa.create_watchlist(OWNER, name, [{"symbol": s, "exchange": "NSE"} for s in symbols])
    oa.db_session.remove()
    return made["id"]


def oa_items(lid):
    from database import watchlist_db as oa

    try:
        (found,) = [w for w in oa.get_watchlists(OWNER) if w["id"] == lid]
    finally:
        oa.db_session.remove()
    return [
        (i["exchange"], i["symbol"]) for i in sorted(found["items"], key=lambda i: i["position"])
    ]


def oa_lists():
    from database import watchlist_db as oa

    try:
        return {w["name"]: w["id"] for w in oa.get_watchlists(OWNER)}
    finally:
        oa.db_session.remove()


def preview(client, text, **more):
    return client.post(API + "/tv/preview", json={"text": text, **more}, headers=client.headers)


def data(r):
    body = r.get_json()
    assert body["status"] == "ok", body
    return body["data"]


def refused(r, status, code, field=None):
    body = r.get_json()
    assert (r.status_code, body["code"], body.get("field")) == (status, code, field), body
    return body["message"]


def big(n, sections=1):
    """A file of ``n`` known symbols in ``sections`` sections."""
    per = -(-n // sections)
    out, k = [], 0
    for s in range(sections):
        out.append(f"###Part {s + 1}")
        for _ in range(min(per, n - k)):
            out.append(f"NSE:Z{k:03d}")
            k += 1
    return ",".join(out)


# -- the preview ---------------------------------------------------------------------------------
def test_the_preview_says_counts_left_out_and_conversions_and_writes_nothing(client, emitted):
    text = "###Banks,NSE:SBIN,NSE:HDFCBANK,###Index,NSE:NIFTY1!,NASDAQ:AAPL,BINANCE:BTCUSDT"
    d = data(preview(client, text, name="Swing"))
    assert (d["sections"], d["symbols"], d["matched"], d["follow"]) == (2, 5, 3, 1)
    assert d["hash"] == tvimport.text_hash(text)
    assert d["left_out"] == [{"token": "NASDAQ:AAPL", "reason": "not_indian"},
                             {"token": "BINANCE:BTCUSDT", "reason": "not_indian"}]  # fmt: skip
    assert d["words"][:3] == [
        "Found 2 sections and 5 symbols. 3 match.",
        "2 are not on Indian exchanges and are left out: NASDAQ:AAPL, BINANCE:BTCUSDT.",
        "NIFTY1! becomes a current-month FUT row that follows NIFTY.",
    ]
    assert d["lists"] == [{"name": "Swing", "count": 3}]
    assert oa_lists() == {} and emitted == []


def test_a_file_over_64_kib_is_said_before_anything_is_read(client, monkeypatch):
    monkeypatch.setattr(master, "shared", lambda: pytest.fail("the master was read"))
    said = refused(preview(client, "N" * (64 * 1024 + 1)), 400, "invalid", "text")
    assert said == "That file is larger than 64 KiB. Export a shorter list and try again."


def test_the_room_in_the_open_list(client):
    lid = make_list([f"Z{i:03d}" for i in range(47)])
    d = data(preview(client, "NSE:SBIN,NSE:TCS", list=lid))
    assert d["words"][-1] == "Swing · room for 203"
    assert d["lists"] == [{"name": "Swing", "count": 2}]
    refused(preview(client, "NSE:SBIN", list=999), 404, "gone")


def test_a_file_over_the_room_of_the_open_list_says_so(client):
    lid = make_list([f"Z{i:03d}" for i in range(248)])
    d = data(preview(client, "NSE:SBIN,NSE:TCS,NSE:INFY", list=lid))
    assert d["words"][-2:] == [
        "Swing · room for 2",
        "Swing has room for 2 more; this file has 3 symbols. Make room in it or import into a new "
        "list.",
    ]


def test_a_file_over_250_goes_into_two_lists_split_at_a_section(client):
    text = ",".join(["###A"] + [f"NSE:Z{i:03d}" for i in range(250)] + ["###B"]
                    + [f"NSE:Z{i:03d}" for i in range(250, 312)])  # fmt: skip
    d = data(preview(client, text, name="Swing"))
    assert d["words"][-1] == (
        "This file has 312 symbols. A list holds 250, so they go into two lists: Swing (1) with "
        "250 and Swing (2) with 62, split at a section."
    )
    assert d["lists"] == [{"name": "Swing (1)", "count": 250}, {"name": "Swing (2)", "count": 62}]


def test_fifty_lists_is_the_most_and_the_preview_says_so(client):
    for n in range(50):
        make_list((), f"L{n}")
    d = data(preview(client, "NSE:SBIN", name="Swing"))
    assert d["words"][-1] == "You have 50 lists, the most there can be. Delete one to import."
    d = data(preview(client, "NSE:SBIN", name="L3"))
    assert "You already have a list called L3. Pick another name." in d["words"]


def test_a_follow_row_for_an_underlying_without_futures_is_left_out(client):
    d = data(preview(client, "NSE:INDIAVIX1!,NSE:SBIN"))
    assert d["follow"] == 0 and d["matched"] == 1
    assert d["left_out"] == [{"token": "NSE:INDIAVIX1!", "reason": "no_futures"}]


def test_nothing_matched_says_so(client):
    d = data(preview(client, "NASDAQ:AAPL"))
    assert d["words"][0].startswith("None of these symbols could be matched") and d["lists"] == []


def test_the_vectors_through_the_preview(client):
    for c in V["cases"]:
        if "follow_room" in c:
            continue
        d = data(preview(client, c["text"], name="Vec"))
        assert d["matched"] == c["matched"], c["name"]
        assert (d["sections"], d["symbols"]) == (c["found_sections"], c["found_symbols"]), c["name"]
        if "preview" in c:
            assert d["words"][: len(c["preview"])] == c["preview"], c["name"]
        assert [[x["token"], x["reason"]] for x in d["left_out"]] == c["cuts"], c["name"]
