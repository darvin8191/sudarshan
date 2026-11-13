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


def test_index_futures_and_bse_contracts_wait_for_a_checked_form_on_the_way_in(client):
    """Review M3: the import mirrors the export. An index future other than NIFTY's and
    BANKNIFTY's, in the chart's spelling or the index's own, any BSE future and a BSE option are
    left out with one sentence, not turned into rows; INDIAVIX, with no futures at all, still
    says so (above)."""
    text = "NSE:CNXFINANCE1!,NSE:FINNIFTY1!,BSE:SENSEX1!,BSE:SENSEX261126C80000,NSE:NIFTY1!"
    d = data(preview(client, text))
    assert d["matched"] == 1 and d["follow"] == 1
    assert [(x["token"], x["reason"]) for x in d["left_out"]] == [
        ("NSE:CNXFINANCE1!", "unchecked_future"), ("NSE:FINNIFTY1!", "unchecked_future"),
        ("BSE:SENSEX1!", "unchecked_future"), ("BSE:SENSEX261126C80000", "unchecked_option"),
    ]  # fmt: skip
    assert (
        "Left out until their TradingView form is checked: 3 index futures (NSE:CNXFINANCE1!, "
        "NSE:FINNIFTY1!, BSE:SENSEX1!) and 1 BSE option (BSE:SENSEX261126C80000)."
    ) in d["words"]


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


# -- the import ----------------------------------------------------------------------------------
def v(n: int) -> str:
    return "0" if n == 0 else str(START + n - 1)


def do_import(client, text, **more):
    body = {"text": text, "hash": tvimport.text_hash(text), **more}
    return client.post(API + "/tv/import", json=body, headers=client.headers)


def table(name):
    from sqlalchemy import text

    with engine.open_session() as s:
        return [tuple(r) for r in s.execute(text(f"SELECT * FROM {name} ORDER BY 1, 2")).all()]


def view_of(client, lid):
    return data(client.get(API + "/view", query_string={"ref": f"list:{lid}"}))


TEXT = "###Banks,NSE:SBIN,NSE:HDFCBANK,###IT,NSE:TCS,NSE:INFY,NSE:NIFTY1!,NASDAQ:AAPL"


def test_a_changed_file_is_refused_and_writes_nothing(client, emitted):
    r = client.post(API + "/tv/import", json={"text": TEXT + ",NSE:SBIN", "hash": tvimport.text_hash(TEXT),
                                              "name": "Swing"}, headers=client.headers)  # fmt: skip
    assert (
        refused(r, 400, "invalid", "hash")
        == "The file changed since its preview. Preview it again."
    )
    assert oa_lists() == {} and emitted == []


def test_an_import_into_a_new_list_makes_it_in_one_call_with_sections_and_rows(
    client, emitted, monkeypatch
):
    from database import watchlist_db as oa

    calls = []
    real = oa.create_watchlist
    monkeypatch.setattr(oa, "create_watchlist", lambda *a, **k: calls.append(a) or real(*a, **k))
    r = do_import(client, TEXT, name="Swing")
    d = data(r)
    assert len(calls) == 1 and [i["symbol"] for i in calls[0][2]] == [
        "SBIN",
        "HDFCBANK",
        "TCS",
        "INFY",
    ]
    lid = oa_lists()["Swing"]
    assert (
        d["lists"] == [lid] and d["added"] == 5 and d["words"] == "Swing: 5 symbols in 2 sections."
    )
    assert d["lists_version"] and d["version"] == v(1)
    shown = view_of(client, lid)
    assert [s["name"] for s in shown["sections"]] == ["Banks", "IT"]
    assert {(r["contract"]["symbol"], r["section_id"]) for r in shown["rows"]} == {
        ("SBIN", shown["sections"][0]["id"]), ("HDFCBANK", shown["sections"][0]["id"]),
        ("TCS", shown["sections"][1]["id"]), ("INFY", shown["sections"][1]["id"])}  # fmt: skip
    (row,) = shown["follow"]
    assert (row["kind"], row["underlying"], row["underlying_exchange"], row["expiry_rule"]) == (
        "month_future",
        "NIFTY",
        "NSE_INDEX",
        "month",
    )  # a FUT row from NIFTY1!
    assert row["section_id"] == shown["sections"][1]["id"]
    assert [(p["why"], p["refs"]) for p in emitted] == [("items", [f"list:{lid}"])]
    assert emitted[0]["v"] == d["lists_version"]


def test_a_name_taken_and_fifty_lists_are_refused_before_anything_is_written(client):
    make_list((), "Swing")
    refused(do_import(client, TEXT, name="Swing"), 400, "invalid", "name")
    for n in range(49):
        make_list((), f"L{n}")
    said = refused(do_import(client, TEXT, name="Fresh"), 422, "limit")
    assert said == "You have 50 lists, the most there can be. Delete one to import."
    assert len(oa_lists()) == 50
    lid = oa_lists()["Swing"]
    refused(do_import(client, TEXT, name="X", list=lid), 400, "invalid", "list")


def test_a_file_over_250_makes_two_lists(client, emitted):
    text = ",".join(["###A"] + [f"NSE:Z{i:03d}" for i in range(250)] + ["###B"]
                    + [f"NSE:Z{i:03d}" for i in range(250, 312)])  # fmt: skip
    d = data(do_import(client, text, name="Swing"))
    lists = oa_lists()
    assert d["lists"] == [lists["Swing (1)"], lists["Swing (2)"]] and d["added"] == 312
    assert (len(oa_items(lists["Swing (1)"])), len(oa_items(lists["Swing (2)"]))) == (250, 62)
    assert d["version"] is None
    assert d["words"] == "Swing (1) and Swing (2): 312 symbols in 2 sections."
    assert [(p["why"], p["refs"]) for p in emitted] == [
        ("items", [f"list:{i}" for i in d["lists"]])
    ]


def test_an_import_into_the_open_list_needs_its_version_and_a_stale_one_changes_nothing(
    client, emitted
):
    lid = make_list(("SBIN",))
    refused(do_import(client, TEXT, list=lid), 400, "invalid", "version")
    sec = client.post(API + "/sections", json={"list": lid, "name": "Old", "version": "0"},
                      headers=client.headers).get_json()["data"]  # fmt: skip
    assert sec["version"] == v(1)
    emitted.clear()
    refused(do_import(client, TEXT, list=lid, version="0"), 409, "conflict")
    assert oa_items(lid) == [("NSE", "SBIN")] and emitted == []
    d = data(do_import(client, TEXT, list=lid, version=v(1)))
    assert d["words"] == "Swing: 4 symbols in 2 sections." and d["lists"] == [lid]
    assert oa_items(lid) == [("NSE", s) for s in ("SBIN", "HDFCBANK", "TCS", "INFY")]
    shown = view_of(client, lid)
    assert [s["name"] for s in shown["sections"]] == ["Old", "Banks", "IT"] and len(
        shown["follow"]
    ) == 1
    assert [(p["why"], p["refs"]) for p in emitted] == [("items", [f"list:{lid}"])]
    # the same file again changes nothing and bumps nothing
    emitted.clear()
    again = data(do_import(client, TEXT, list=lid, version=d["version"]))
    assert again["added"] == 0 and emitted == []


def test_an_open_list_with_too_little_room_or_too_many_sections_is_refused(client):
    lid = make_list([f"Z{i:03d}" for i in range(249)])
    said = refused(do_import(client, "NSE:SBIN,NSE:TCS", list=lid, version="0"), 422, "limit")
    assert said == ("Swing has room for 1 more; this file has 2 symbols. Make room in it or import "
                    "into a new list.")  # fmt: skip
    lid = make_list((), "Other")
    text = ",".join(f"###S{i},NSE:SBIN" for i in range(21))
    refused(do_import(client, text, name="Many"), 422, "limit")
    refused(do_import(client, text, list=lid, version="0"), 422, "limit")


def test_a_future_past_the_follow_rows_a_list_holds_is_imported_as_todays_contract(
    client, monkeypatch
):
    from datetime import datetime

    from sudarshan.watchlists.ops import tvimport as op

    monkeypatch.setattr(
        op,
        "_now_ms",
        lambda: int(datetime.fromisoformat("2026-11-13T11:00:00+05:30").timestamp() * 1000),
    )
    lid = make_list()
    n = 0
    for off in range(-10, 9):  # 19 rows, one short of the 20 a list holds
        body = {"list": lid, "version": v(n), "kind": "atm_option", "underlying": "NIFTY",
                "underlying_exchange": "NSE_INDEX", "opt_right": "CE", "offset": off}  # fmt: skip
        data(client.post(API + "/rows", json=body, headers=client.headers))
        n += 1
    d = data(do_import(client, "NSE:NIFTY1!,NSE:NIFTY2!", list=lid, version=v(n)))
    assert d["added"] == 2
    assert oa_items(lid) == [("NFO", "NIFTY26DEC26FUT")]  # next month's contract, today's name


def test_the_vectors_through_the_import(client):
    for c in V["cases"]:
        if "follow_room" in c or c["matched"] == 0:
            continue
        name = c["name"][:40]
        d = data(do_import(client, c["text"], name=name))
        lid = d["lists"][0]
        fixed = [
            i.split(" ", 1)[1].split(":")
            for s in c["sections"]
            for i in s[1]
            if i.startswith("fixed")
        ]
        follow = [i for s in c["sections"] for i in s[1] if i.startswith("follow")]
        assert oa_items(lid) == [tuple(f) for f in fixed], c["name"]
        shown = view_of(client, lid)
        assert len(shown["follow"]) == len(follow), c["name"]
        assert [s["name"] for s in shown["sections"]] == [s[0] for s in c["sections"] if s[0]], c[
            "name"
        ]


# -- part way, and the hub ------------------------------------------------------------------------
def test_an_add_that_stops_part_way_says_how_many_and_bumps_once_with_refs(
    client, monkeypatch, emitted
):
    from database import watchlist_db as oa

    lid = make_list()
    real, seen = oa.add_item, []

    def flaky(user, lid_, symbol, exchange):
        seen.append(symbol)
        return None if len(seen) == 3 else real(user, lid_, symbol, exchange)

    monkeypatch.setattr(oa, "add_item", flaky)
    said = refused(do_import(client, TEXT, list=lid, version="0"), 500, "failed")
    assert (
        said
        == words.part_open(2, 4, "Swing")
        == "Added 2 of 4 symbols to Swing. The rest were not added; try again."
    )
    assert [s for _, s in oa_items(lid)] == ["SBIN", "HDFCBANK"]
    assert [(p["why"], p["refs"]) for p in emitted] == [("items", [f"list:{lid}"])]
    assert [s["name"] for s in view_of(client, lid)["sections"]] == [
        "Banks",
        "IT",
    ]  # placed as far as it went


def test_a_second_list_that_cannot_be_made_names_what_was_made(client, monkeypatch, emitted):
    from sudarshan.watchlists.ops import tvimport as op

    real, n = op._make_list, []

    def flaky(user, name, keys):
        n.append(name)
        if len(n) == 2:
            raise ops.OpError("failed")
        return real(user, name, keys)

    monkeypatch.setattr(op, "_make_list", flaky)
    text = ",".join(
        ["###A"]
        + [f"NSE:Z{i:03d}" for i in range(250)]
        + ["###B"]
        + [f"NSE:Z{i:03d}" for i in range(250, 312)]
    )
    said = refused(do_import(client, text, name="Swing"), 500, "failed")
    assert said == ("Only Swing (1) was made, with 250 of the file's 312 symbols. Swing (2) was not "
                    "made. Delete it and import again.")  # fmt: skip
    first = oa_lists()["Swing (1)"]
    assert [(p["why"], p["refs"]) for p in emitted] == [("items", [f"list:{first}"])]


def test_the_open_list_writes_yield_to_the_hub_between_items(client, monkeypatch):
    import threading

    from sudarshan.watchlists.ops import copy as copy_ops

    lid = make_list()
    pauses, mine = [], threading.get_ident()
    monkeypatch.setattr(
        copy_ops.time,
        "sleep",
        lambda s: pauses.append(s) if threading.get_ident() == mine else None,
    )
    text = ",".join(f"NSE:Z{i:03d}" for i in range(40))
    d = data(do_import(client, text, list=lid, version="0"))
    assert d["added"] == 40 and len(oa_items(lid)) == 40
    assert pauses.count(0) >= 40  # one pause after each of OpenAlgo's commits


def test_a_hostile_file_costs_one_parse_not_the_hub(client):
    import time

    text = ",".join(f"NSE:Q{i}" for i in range(7000))[: 64 * 1024]
    t0 = time.monotonic()
    d = data(preview(client, text, name="Big"))
    assert d["matched"] == 0 and time.monotonic() - t0 < 2


EVENTLET = """
import eventlet
eventlet.monkey_patch()
import os, sys, tempfile
tmp = tempfile.mkdtemp()  # noqa
os.environ.update(API_KEY_PEPPER="f" * 64, APP_KEY="test-only-app-key-" + "0" * 32,
                  DATABASE_URL="sqlite:///" + tmp + "/oa.db", LOG_DIR=tmp + "/log",
                  SUDARSHAN_WATCHLISTS_DB=tmp + "/wl.db")
sys.path.insert(0, {root!r})
sys.path.insert(0, {tests!r})
from flask import Flask
from database import watchlist_db as oa
from watchlists_follow_master import Snap, cash
from sudarshan.watchlists import master, tvimport
from sudarshan.watchlists.ops import copy as c
from sudarshan.watchlists.ops import tvimport as op
oa.Base.metadata.create_all(oa.engine)
snap = Snap([cash(f"S{{i:03d}}", "NSE") for i in range(250)])
master.openalgo_snapshot = lambda: snap
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
text = ",".join(f"NSE:S{{i:03d}}" for i in range(250))
app = Flask("t")
app.secret_key = "x"
with app.test_request_context():
    from flask import session
    session["user"] = "u"
    out = op.tv_import({{"text": text, "hash": tvimport.text_hash(text), "list": made["id"],
                         "version": "0"}})
print("RESULT", out["added"], ticks[0] - before)
"""


def run_eventlet(silenced: bool) -> tuple[int, int]:
    import subprocess
    import sys
    import textwrap

    root = Path(__file__).resolve().parents[3]
    code = textwrap.dedent(EVENTLET).format(
        root=str(root), tests=str(Path(__file__).parent), silenced=silenced
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          timeout=120, cwd=root)  # fmt: skip
    line = next((ln for ln in done.stdout.splitlines() if ln.startswith("RESULT")), None)
    assert line, done.stderr[-2000:]
    _, added, ticks = line.split()
    return int(added), int(ticks)


def test_a_second_greenlet_runs_during_a_250_symbol_import_under_eventlet():
    pytest.importorskip("eventlet", reason="eventlet is installed by the production installer")
    added, ticks = run_eventlet(silenced=False)
    assert added == 250 and ticks >= 240
    # the defect itself, so the proof cannot pass vacuously: without the yield nothing else runs
    added, ticks = run_eventlet(silenced=True)
    assert added == 250 and ticks < 20
