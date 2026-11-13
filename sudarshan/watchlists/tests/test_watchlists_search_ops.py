"""The ``search`` and ``strikes`` ops over the master (M8.18, W17): the grammar's vectors through
the bridge, the grouping, the Added mark, the strike ladder, the class limit, the one generation
and the timing on a full-size master."""

from __future__ import annotations

import gc
import threading
import time
from datetime import datetime

import pytest
from presets_master import Row as PlainRow
from presets_master import Snap
from test_watchlists_search import NOW, V, make_rows
from watchlists_bridge_app import OWNER, csrf_headers, login_session, make_app, reset_users

import blueprints.sudarshan_watchlists as bp_module
from sudarshan.watchlists import contract, engine, master, ops, sessions, versions
from sudarshan.watchlists.ops import search as search_ops
from sudarshan.watchlists.refs import preset as preset_reader
from sudarshan.watchlists.words import search as words
from sudarshan.watchlists.words import strikes as strike_words

API = contract.API_PREFIX


def snap_of(rows):
    by_ex: dict = {}
    for r in rows:
        by_ex.setdefault(r.exchange, []).append(r)
    return Snap(
        by_exchange={k: tuple(v) for k, v in by_ex.items()},
        by_symbol_exchange={(r.symbol, r.exchange): r for r in rows},
        by_token={r.token: r for r in rows},
        session_start=datetime(2026, 11, 20, 8, 40),
    )


class Box:
    def __init__(self):
        self.snap = None

    def __call__(self):
        return self.snap


def ist(text: str) -> int:
    return int(datetime.fromisoformat(text).timestamp() * 1000)


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
    monkeypatch.setattr(search_ops, "now_ms", lambda: NOW)
    monkeypatch.setattr(preset_reader, "calendar_close", lambda iso: None)
    search_ops._close.clear()
    shared = master.shared()
    shared.drop()
    gc.collect()
    b.snap = snap_of(make_rows())
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


def get(client, op, **query):
    return client.get(f"{API}/{op}", query_string=query, headers=client.headers)


def find(client, q, **kw):
    r = get(client, "search", q=q, **kw)
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]


def shown(hit):
    c = hit["contract"]
    return f"{c['exchange']}:{hit['title']} | {hit['detail']}"


ORDER = ("stocks", "indices", "futures", "options", "commodities")


@pytest.mark.parametrize(
    "c", V["cases"], ids=lambda c: f"{c['q']}-{c.get('seg', '')}-{c.get('price', '')}"
)
def test_the_grammars_vectors_pass_through_the_op(client, c):
    kw = {k: v for k, v in (("seg", c.get("seg")), ("p", c.get("price"))) if v is not None}
    data = find(client, c["q"], **kw)
    got = [shown(h) for h in data["hits"]]
    if "hits" in c:  # the op groups; each group keeps the grammar's order
        want = sorted(c["hits"], key=lambda t: ORDER.index(
            next(h["group"] for h in data["hits"] if shown(h) == t)) if got else 0)  # fmt: skip
        assert got == want and sorted(got) == sorted(c["hits"])
    if "n" in c:
        assert len(got) == c["n"]
    if "pick" in c:
        assert data["underlying"] == c["pick"]
    if "line" in c:
        assert data["understood"] == c["line"]
    if "words" in c:
        assert data["suggestion"] == c["words"]
    if "groups" in c:
        assert sorted({h["group"] for h in data["hits"]}) == c["groups"]
    ranks = [ORDER.index(h["group"]) for h in data["hits"]]
    assert ranks == sorted(ranks)


def test_a_hit_is_a_contract_of_the_master_in_trader_words(client):
    hit = find(client, "nifty next week 26000 pe")["hits"][0]
    assert (hit["group"], hit["title"], hit["detail"]) == (
        "options",
        "NIFTY 26,000 PE",
        "NFO · 1 Dec 2026 · lot 65",
    )
    assert hit["contract"] == {
        "exchange": "NFO", "symbol": "NIFTY01-DEC-2626000PE", "key": "NFO:NIFTY01-DEC-2626000PE",
        "name": "NIFTY", "lot": 65, "tick": None, "expiry": "2026-12-01", "isin": None,
    }  # fmt: skip
    assert hit["added"] is False
    cash = find(client, "reliance")["hits"][0]["contract"]
    assert cash["isin"] == "INE002A01018" and cash["key"] == "NSE:RELIANCE"


def test_groups_come_in_the_designs_order_one_heading_each(client, box):
    extra = PlainRow(
        symbol="NIFTYBEES",
        name="Nifty BeES",
        exchange="NSE",
        token="NSE_EQ|INF1",
        instrumenttype="EQ",
        underlying=None,
        expiry=None,
        strike=None,
        lotsize=1,
    )
    box.snap = snap_of(make_rows() + [extra])
    data = find(client, "nif")
    assert [h["contract"]["symbol"] for h in data["hits"]] == ["NIFTYBEES", "NIFTY", "BANKNIFTY"]
    assert [h["group"] for h in data["hits"]] == ["stocks", "indices", "indices"]
    mixed = find(client, "tcs")
    assert [h["group"] for h in mixed["hits"]] == ["stocks", "futures", "futures"]


def test_a_chip_keeps_only_its_kind(client):
    cases = {"stocks": "reliance", "futures": "reliance", "options": "nifty 26000",
             "indices": "nifty", "commodities": "crude"}  # fmt: skip
    for seg, q in cases.items():
        data = find(client, q, seg=seg)
        assert data["hits"] and {h["group"] for h in data["hits"]} == {seg}, seg
    assert {h["group"] for h in find(client, "reliance", seg="all")["hits"]} == {
        "stocks",
        "futures",
    }


def test_a_symbol_the_list_holds_is_marked_added(client):
    from database import watchlist_db as oa

    made = oa.create_watchlist(OWNER, "Mine", [{"symbol": "RELIANCE", "exchange": "NSE"},
                                               {"symbol": "TCS", "exchange": "NSE"}])  # fmt: skip
    oa.db_session.remove()
    lid = made["id"]
    hits = find(client, "reliance", list=lid)["hits"]
    assert [(h["contract"]["key"], h["added"]) for h in hits[:2]] == [
        ("NSE:RELIANCE", True), ("BSE:RELIANCE", False)]  # fmt: skip
    assert all(not h["added"] for h in hits[2:])
    assert all(not h["added"] for h in find(client, "reliance")["hits"])


def test_a_list_that_is_not_the_owners_is_gone(client):
    from database import watchlist_db as oa

    other = oa.create_watchlist("someone-else", "Theirs", [])["id"]
    oa.db_session.remove()
    for lid in (other, 99999):
        r = get(client, "search", q="reliance", list=lid)
        assert r.status_code == 404 and r.get_json()["code"] == "gone"


def test_the_query_as_understood_and_the_near_miss(client):
    data = find(client, "nifty next week 26000 pe")
    assert data["understood"] == "NIFTY · next expiry 1 Dec · strike 26,000 · PE"
    assert data["suggestion"] is None
    miss = find(client, "relaince")
    assert miss["hits"] == [] and miss["suggestion"] == (
        "No exact match for RELAINCE. Did you mean RELIANCE?"
    )
    assert find(client, "xyzq")["suggestion"] == "No exact match for XYZQ."
    nothing = find(client, "reliance", seg="indices")
    assert nothing["hits"] == [] and nothing["suggestion"] == words_for("RELIANCE", "indices")
    waiting = find(client, "nifty atm ce")
    assert waiting["hits"] == [] and waiting["suggestion"] is None
    assert "the price is not known yet" in waiting["understood"]
    blank = find(client, "   ")
    assert blank == {
        "understood": "",
        "hits": [],
        "underlying": None,
        "underlying_exchange": None,
        "suggestion": None,
    }


def words_for(query, seg):
    return strike_words.segment_empty(seg, query)


def test_bad_requests_are_refused_in_words(client):
    for kw, field in (({"q": "x" * 101}, "q"), ({"q": "tcs", "limit": 0}, "limit"),
                      ({"q": "tcs", "limit": 51}, "limit"), ({"q": "tcs", "p": -1}, "p")):  # fmt: skip
        r = get(client, "search", **kw)
        assert r.status_code == 400 and r.get_json()["field"] == field, kw
    assert len(find(client, "nifty 26000", limit=2)["hits"]) == 2


def test_the_master_not_loaded_is_said_in_words(client, box):
    box.snap = None
    for op, q in (("search", {"q": "tcs"}), ("strikes", {"u": "NIFTY", "x": "NSE_INDEX"})):
        r = get(client, op, **q)
        assert r.get_json()["code"] == "failed"
        assert "not loaded yet" in r.get_json()["message"]


def strikes(client, **q):
    q.setdefault("u", "NIFTY")
    q.setdefault("x", "NSE_INDEX")
    r = get(client, "strikes", **q)
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]


def test_strikes_around_the_atm_with_both_sides_from_the_master(client):
    data = strikes(client, p=25912.35, n=1)
    assert data["underlying"] == "NIFTY" and data["exchange"] == "NSE_INDEX"
    assert [e["label"] for e in data["expiries"]] == ["Nearest 24 Nov", "1 Dec", "29 Dec (month)"]
    assert [e["date"] for e in data["expiries"]] == ["2026-11-24", "2026-12-01", "2026-12-29"]
    assert [e["month"] for e in data["expiries"]] == [True, False, True]
    assert data["expiry"] == "2026-11-24" and data["atm"] == 25900
    assert [(s["strike"], s["atm"]) for s in data["strikes"]] == [
        (25850, False), (25900, True), (25950, False)]  # fmt: skip
    mid = data["strikes"][1]
    assert mid["ce"]["symbol"] == "NIFTY24-NOV-2625900CE" and mid["pe"]["symbol"].endswith("PE")
    assert mid["ce"]["lot"] == 65 and mid["ce"]["expiry"] == "2026-11-24"
    assert data["words"] == ""
    wider = strikes(client, p=25912.35)  # n is 10: the whole ladder of seven strikes
    assert [s["strike"] for s in wider["strikes"]][0] == 25800 and len(wider["strikes"]) == 7
    assert sum(s["atm"] for s in wider["strikes"]) == 1


def test_the_atm_tie_goes_to_the_lower_strike_and_edges_clip(client):
    assert strikes(client, p=25925, n=0)["atm"] == 25900
    low = strikes(client, p=100, n=2)
    assert low["atm"] == 25800 and [s["strike"] for s in low["strikes"]] == [25800, 25850, 25900]


def test_another_expiry_is_asked_by_its_date(client):
    data = strikes(client, p=26000, exp="2026-12-29", n=0)
    assert data["expiry"] == "2026-12-29" and data["strikes"][0]["ce"]["expiry"] == "2026-12-29"
    assert len(data["expiries"]) == 3
    r = get(client, "strikes", u="NIFTY", x="NSE_INDEX", exp="2026-12-15")
    assert r.status_code == 400 and r.get_json()["field"] == "exp"
    assert r.get_json()["message"] == strike_words.EXPIRY_GONE


def test_with_no_price_the_strikes_carry_no_atm_and_say_so(client):
    data = strikes(client, n=1)
    assert data["atm"] is None and not any(s["atm"] for s in data["strikes"])
    assert [s["strike"] for s in data["strikes"]] == [
        25900,
        25950,
        26000,
    ]  # the middle of the ladder
    assert data["words"] == strike_words.NO_PRICE


def at(monkeypatch, when):
    monkeypatch.setattr(search_ops, "now_ms", lambda: ist(when))
    search_ops._close.clear()


def test_an_index_expiry_before_7_dec_runs_to_the_fo_close_1540(client, monkeypatch):
    at(monkeypatch, "2026-11-24T15:35:00+05:30")  # 15:30 would already have dropped it
    data = strikes(client, p=25900, n=0)
    assert data["expiries"][0]["label"] == "Nearest 24 Nov" and data["expiry"] == "2026-11-24"
    assert data["words"] == "This expiry ends today at 15:40."
    at(monkeypatch, "2026-11-24T15:41:00+05:30")
    after = strikes(client, p=25900, n=0)
    assert [e["label"] for e in after["expiries"]] == ["Nearest 1 Dec", "29 Dec (month)"]
    assert after["expiry"] == "2026-12-01" and after["words"] == ""
    assert get(client, "strikes", u="NIFTY", x="NSE_INDEX", exp="2026-11-24").status_code == 400


def test_an_index_expiry_from_7_dec_stops_at_1530(client, monkeypatch):
    at(monkeypatch, "2026-12-29T15:29:00+05:30")
    data = strikes(client, p=25900, n=0)
    assert data["expiry"] == "2026-12-29" and data["words"] == "This expiry ends today at 15:30."
    at(monkeypatch, "2026-12-29T15:31:00+05:30")  # 15:40 would still hold it
    gone = strikes(client, p=25900, n=0)
    assert gone["expiries"] == [] and gone["words"] == strike_words.no_open_expiry("NIFTY")


def test_a_stock_expiry_runs_to_the_fo_close_before_and_after_7_dec(client, monkeypatch):
    at(monkeypatch, "2026-11-24T15:39:00+05:30")
    before = strikes(client, u="RELIANCE", x="NSE", p=950, n=0)
    assert (
        before["expiry"] == "2026-11-24" and before["words"] == "This expiry ends today at 15:40."
    )
    at(monkeypatch, "2026-11-24T15:41:00+05:30")
    assert strikes(client, u="RELIANCE", x="NSE", p=950, n=0)["expiry"] == "2026-12-29"
    at(monkeypatch, "2026-12-29T15:44:00+05:30")  # an index would be gone at 15:30
    after = strikes(client, u="RELIANCE", x="NSE", p=950, n=0)
    assert after["expiry"] == "2026-12-29" and after["words"] == "This expiry ends today at 15:45."
    at(monkeypatch, "2026-12-29T15:46:00+05:30")
    assert strikes(client, u="RELIANCE", x="NSE", p=950, n=0)["expiries"] == []


def test_a_special_session_day_takes_its_close_from_the_calendar(client, monkeypatch):
    monkeypatch.setattr(preset_reader, "calendar_close", lambda iso: ist(f"{iso}T19:15:00+05:30"))
    at(monkeypatch, "2026-11-24T18:00:00+05:30")  # the regime's 15:40 has passed
    data = strikes(client, p=25900, n=0)
    assert data["expiry"] == "2026-11-24" and data["words"] == "This expiry ends today at 19:15."
    at(monkeypatch, "2026-11-24T19:16:00+05:30")
    assert strikes(client, p=25900, n=0)["expiry"] == "2026-12-01"


def mcx_rows():
    rows = []
    for e in ("24-NOV-26", "18-DEC-26"):
        for k in (6000, 6050):
            for t in ("CE", "PE"):
                rows.append(PlainRow(symbol=f"CRUDEOIL{e}{k}{t}", name="CRUDEOIL", exchange="MCX",
                                     token=f"MCX_FO|{e}{k}{t}", instrumenttype=t,
                                     underlying="CRUDEOIL", expiry=e, strike=float(k),
                                     lotsize=100))  # fmt: skip
    return rows


def bse_rows():
    """SENSEX: a BSE index with its options on BFO."""
    rows = [PlainRow(symbol="SENSEX", name="Sensex", exchange="BSE_INDEX", token="BSE_INDEX|SENSEX",
                     instrumenttype="INDEX")]  # fmt: skip
    for k in (80000, 80100):
        for t in ("CE", "PE"):
            rows.append(PlainRow(symbol=f"SENSEX24NOV26{k}{t}", name="SENSEX", exchange="BFO",
                                 token=f"BSE_FO|SENSEX{k}{t}", instrumenttype=t, underlying="SENSEX",
                                 expiry="24-NOV-26", strike=float(k), lotsize=20))  # fmt: skip
    return rows


def test_the_answer_names_the_exchange_of_an_index_underlying(client):
    data = find(client, "nifty 26000 ce")
    assert data["underlying"] == "NIFTY" and data["underlying_exchange"] == "NSE_INDEX"
    assert strikes(client, u=data["underlying"], x=data["underlying_exchange"])["strikes"]


def test_the_answer_names_the_exchange_of_a_stock_listed_on_two_exchanges(client):
    data = find(client, "reliance 950 ce")  # NSE and BSE both list it; its options are on NFO
    assert data["underlying"] == "RELIANCE" and data["underlying_exchange"] == "NSE"
    assert strikes(client, u=data["underlying"], x=data["underlying_exchange"])["strikes"]


def test_the_answer_names_the_exchange_of_a_bse_index_underlying(client, box):
    box.snap = snap_of(make_rows() + bse_rows())
    data = find(client, "sensex 80000 ce")
    assert data["underlying"] == "SENSEX" and data["underlying_exchange"] == "BSE_INDEX"
    assert strikes(client, u=data["underlying"], x=data["underlying_exchange"])["strikes"]


def test_the_answer_names_the_exchange_of_an_mcx_underlying(client, box):
    box.snap = snap_of(make_rows() + mcx_rows())
    data = find(client, "crudeoil 6000 ce")
    assert data["underlying"] == "CRUDEOIL" and data["underlying_exchange"] == "MCX"
    assert strikes(client, u=data["underlying"], x=data["underlying_exchange"])["strikes"]


def test_no_underlying_means_no_exchange(client):
    data = find(client, "tcs")
    assert data["underlying_exchange"] == (None if data["underlying"] is None else "NSE")
    assert find(client, "")["underlying_exchange"] is None


def test_an_mcx_expiry_says_no_end_time_and_stays_to_the_end_of_the_day(client, box, monkeypatch):
    box.snap = snap_of(make_rows() + mcx_rows())
    at(monkeypatch, "2026-11-24T23:45:00+05:30")  # after any F&O close, MCX is still trading
    data = strikes(client, u="CRUDEOIL", x="MCX", p=6010, n=0)
    assert data["expiry"] == "2026-11-24" and data["words"] == ""
    at(monkeypatch, "2026-11-25T00:01:00+05:30")
    assert strikes(client, u="CRUDEOIL", x="MCX", p=6010, n=0)["expiry"] == "2026-12-18"


def test_an_mcx_expiry_drops_by_the_calendars_window_when_it_has_one(client, box, monkeypatch):
    box.snap = snap_of(make_rows() + mcx_rows())
    monkeypatch.setattr(sessions, "closes", lambda dates, ex: {d: ist(f"{d}T17:00:00+05:30")
                                                               for d in dates if ex == "MCX"})  # fmt: skip
    at(monkeypatch, "2026-11-24T16:59:00+05:30")
    data = strikes(client, u="CRUDEOIL", x="MCX", p=6010, n=0)
    assert data["expiry"] == "2026-11-24" and data["words"] == ""
    at(monkeypatch, "2026-11-24T17:01:00+05:30")
    assert strikes(client, u="CRUDEOIL", x="MCX", p=6010, n=0)["expiry"] == "2026-12-18"


def test_a_side_missing_from_the_master_is_said_never_built(client, box):
    rows = [r for r in make_rows() if r.symbol != "NIFTY24-NOV-2625950PE"
            and r.symbol != "NIFTY24-NOV-2625900CE"]  # fmt: skip
    box.snap = snap_of(rows)
    data = strikes(client, p=25912.35, n=1)
    by = {s["strike"]: s for s in data["strikes"]}
    assert by[25950]["pe"] is None and by[25950]["ce"] is not None
    assert by[25900]["ce"] is None and by[25900]["pe"] is not None
    assert data["words"] == ("No CE in today's contract list for 25,900. "
                             "No PE in today's contract list for 25,950.")  # fmt: skip


def test_strikes_refuses_what_is_not_there(client):
    for q, code, field in (({"u": "BANKNIFTY", "x": "NSE_INDEX"}, "gone", None),
                           ({"u": "CRUDEOIL", "x": "MCX"}, "gone", None),
                           ({"u": "NIFTY", "x": "NSE"}, "invalid", "x"),
                           ({"u": "NIFTY", "x": "NSE_INDEX", "n": 26}, "invalid", "n"),
                           ({"u": "NIFTY", "x": "NSE_INDEX", "p": 0}, "invalid", "p")):  # fmt: skip
        r = get(client, "strikes", **q)
        assert (r.get_json()["code"], r.get_json().get("field")) == (code, field), q
    assert strikes(client, u="reliance", x="NSE", p=950, n=0)["strikes"][0]["strike"] == 950


def test_the_switch_off_changes_nothing_and_answers_off(client, monkeypatch):
    monkeypatch.setenv(contract.SWITCH_ENV, "off")
    for op, q in (("search", {"q": "tcs"}), ("strikes", {"u": "NIFTY", "x": "NSE_INDEX"})):
        assert get(client, op, **q).get_json()["code"] == "off"


def test_the_search_class_allows_300_a_minute_and_then_says_busy(box):
    reset_users()
    app = make_app(ops.discover(), rate_limits=True, real=True)
    c = app.test_client()
    login_session(c)
    headers = csrf_headers(c)
    codes = [c.get(f"{API}/search", query_string={"q": "tcs"}, headers=headers).status_code
             for _ in range(contract.LIMITS_PER_MIN["search"] + 2)]  # fmt: skip
    assert contract.LIMITS_PER_MIN["search"] == 300
    assert codes[:300] == [200] * 300 and codes[300:] == [429, 429]
    assert (
        c.get(f"{API}/search", query_string={"q": "tcs"}, headers=headers).get_json()["code"]
        == "busy"
    )
    assert c.get(f"{API}/state", headers=headers).status_code == 200  # reads have their own count


def test_the_index_is_one_generation_built_once_and_dropped_with_its_snapshot(client, box):
    shared = master.shared()
    before = shared.builds
    threads = {t.name for t in threading.enumerate()}
    for q in ("tcs", "reliance", "nifty 26000", "relaince"):
        find(client, q)
    strikes(client, p=25900)
    assert shared.builds == before + 1 and master.generations_alive() == 1
    box.snap = snap_of(make_rows())  # the next snapshot: the old rows and maps go with the old one
    gc.collect()
    assert master.generations_alive() == 0
    find(client, "tcs")
    assert shared.builds == before + 2 and master.generations_alive() == 1
    box.snap = None
    gc.collect()
    assert master.generations_alive() == 0
    assert {t.name for t in threading.enumerate()} == threads


def synthetic(count_underlyings=600):
    """About 120,000 rows: 600 underlyings, three expiries, 33 strikes, two sides, and stocks."""
    rows = []
    for u in range(count_underlyings):
        for e in ("24-NOV-26", "01-DEC-26", "29-DEC-26"):
            for k in range(25000, 25000 + 33 * 50, 50):
                for t in ("CE", "PE"):
                    rows.append(PlainRow(symbol=f"U{u}{e}{k}{t}", name="x", exchange="NFO",
                                         token="t", expiry=e, strike=float(k), lotsize=25,
                                         instrumenttype=t, underlying=f"U{u}"))  # fmt: skip
    rows += [PlainRow(symbol=f"S{i}", name=f"Stock {i}", exchange="NSE",
                      token=f"NSE_EQ|INE{i:06d}A0{i % 10}00{i % 7}", instrumenttype="EQ",
                      underlying=None, expiry=None, strike=None, lotsize=1)
             for i in range(3000)]  # fmt: skip
    return rows


def test_the_build_in_slices_and_the_query_time_on_120000_rows():
    """Timing: generous margins for CI (the design's 50 ms a slice, 300 ms in all, 20 ms a query)."""
    snap = snap_of(synthetic())
    assert sum(len(v) for v in snap.by_exchange.values()) >= 120_000
    marks = []
    m = master.Master(lambda: snap, pause=lambda s: marks.append(time.perf_counter()))
    best = None
    for _ in range(3):
        marks.clear()
        m.drop()
        t0 = time.perf_counter()
        gen = m.generation()
        end = time.perf_counter()
        edges = [t0, *marks, end]
        slices = [b - a for a, b in zip(edges, edges[1:], strict=False)]
        run = (max(slices), end - t0, len(marks))
        best = run if best is None or run[1] < best[1] else best
        assert gen is not None and gen.rows >= 120_000
    worst, whole, yields = best
    print(f"build: {whole * 1000:.0f} ms, {yields} yields, longest slice {worst * 1000:.0f} ms")
    assert yields >= 20, "the build yields to the hub between slices"
    assert worst < 0.05, best
    assert whole < 0.3, best


def test_a_query_through_the_op_on_120000_rows_is_under_20_ms(box, monkeypatch):
    box.snap = snap_of(synthetic())
    master.shared().index()  # the first call builds; the timing is of the queries after it
    with __import__("flask").Flask("t").test_request_context():
        best = 1.0
        for q in ("u7 25500 ce", "s2999", "u599 atm ce"):
            for _ in range(3):
                t0 = time.perf_counter()
                out = search_ops.search_op({"q": q, "p": 25900.0})
                best = min(best, time.perf_counter() - t0)
            assert out["hits"], q
            assert time.perf_counter() - t0 < 0.1
        assert best < 0.02, best
        t0 = time.perf_counter()
        out = search_ops.strikes_op({"u": "U7", "x": "NFO", "p": 25900.0})
        took = time.perf_counter() - t0
        print(f"strikes: {took * 1000:.1f} ms")
        assert took < 0.1 and len(out["strikes"]) == 21
