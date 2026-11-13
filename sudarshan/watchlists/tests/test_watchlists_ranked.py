"""Ranked rows (W21): mapping and words against hand-typed vectors."""

from __future__ import annotations

import ast
import json
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from sudarshan.watchlists import ranked
from sudarshan.watchlists.words import ranked as words

V = json.loads((Path(__file__).parent / "vectors" / "ranked.json").read_text(encoding="utf-8"))
MASTER = {
    k: SimpleNamespace(symbol=v[0], exchange=v[1], name=v[2], underlying=v[3], lotsize=v[4])
    for k, v in V["master"].items()
}


def ms(iso):
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


@pytest.mark.parametrize("c", V["cases"], ids=lambda c: c["name"])
def test_mapping(c):
    r = ranked.map_rows(c["answer"], c["category"], MASTER.get)
    assert [[x.rank, x.symbol, x.figure] for x in r.rows] == c["rows"]
    assert r.left_out == c.get("left_out", 0)
    if "made" in c:
        assert r.made_at == ms(c["made"])
    if c.get("no_time"):
        assert r.made_at is None
    if "metric_key" in c:
        assert r.metric_key == c["metric_key"]


def test_rows_keep_upstoxs_order_and_the_figures_as_read():
    c = V["cases"][0]
    r = ranked.map_rows(c["answer"], c["category"], MASTER.get)
    assert [x.rank for x in r.rows] == [1, 2]  # the unknown key was third, so no gap in front of it
    assert r.rows[0].metric["previous"] == 100000 and r.rows[0].price["close_price"] == 25900.0
    assert r.rows[0].lot == 65 and r.rows[0].underlying == "NIFTY"


def test_a_rank_counts_unknown_rows_too():
    ans = {
        "data": {
            "data": [{"instrument_key": "x|0"}, {"instrument_key": "NSE_FO|35003", "metric": {}}]
        }
    }
    r = ranked.map_rows(ans, "OI_GAINERS", MASTER.get)
    assert [x.rank for x in r.rows] == [2] and r.left_out == 1


@pytest.mark.parametrize("c", V["shapes"], ids=lambda c: c["name"])
def test_an_answer_says_whether_its_rows_can_be_read(c):
    """Review M2: rows under another name read as an empty ranking, and rows whose key field has
    another name as every contract missing from the master; both are unusable answers now."""
    assert ranked.extract(c["answer"]).found is c["found"]
    assert ranked.unusable(c["answer"]) == c["why"]


@pytest.mark.parametrize(("value", "want"), V["numbers"], ids=repr)
def test_a_figure_is_a_finite_number_or_nothing(value, want):
    assert ranked.number(value) == want


def test_the_shape_of_an_unreadable_answer_is_key_names_only():
    answer = {"status": "success", "data": {"time_stamp": 1, "entries": [{"instrumentKey": "S3"}]}}
    assert ranked.shape_of(answer) == {
        "answer": ["data", "status"],
        "data": ["entries", "time_stamp"],
        "row": [],
    }
    rows = {"data": {"data": [{"instrumentKey": "NSE_FO|35003", "secret": "do-not-log"}]}}
    got = ranked.shape_of(rows)
    assert got["row"] == ["instrumentKey", "secret"]
    assert "do-not-log" not in repr(got) and "NSE_FO|35003" not in repr(got)
    many = {"data": {f"k{i:02}": i for i in range(50)}}
    assert len(ranked.shape_of(many)["data"]) == 20  # bounded
    assert ranked.shape_of("nonsense") == {"answer": [], "data": [], "row": []}


@pytest.mark.parametrize("c", V["times"], ids=str)
def test_time_stamp(c):
    want = None if c["iso"] is None else ms(c["iso"])
    got = ranked.parse_time(c["value"])
    assert got == want if want is None else abs(got - want) <= 1000


@pytest.mark.parametrize("c", V["ages"], ids=lambda c: c["now"])
def test_age_and_stale(c):
    assert words.age(ms(c["made"]), ms(c["now"])) == c["age"]
    assert words.is_stale(ms(c["made"]), ms(c["now"])) is c["stale"]


def test_lines():
    made, read = ms("2026-11-09T10:42:05+05:30"), ms("2026-11-09T10:43:10+05:30")
    assert words.times_line(made, read) == "Ranking made 10:42:05, read 10:43:10"
    assert words.age_line(
        made, read
    ) == "Ranked by Upstox · just now" or "1 min ago" in words.age_line(made, read)
    later = ms("2026-11-09T10:55:00+05:30")
    assert (
        words.age_line(made, later) == "Ranked by Upstox · 12 min ago · Pull down to read it again"
    )
    assert words.expired_line(made, ms("2026-11-09T18:42:05+05:30")) == (
        "Ranked by Upstox · 8 h ago · Your Upstox sign-in has expired. Sign in to read it again."
    )
    assert words.closed_line(ms("2026-11-06T15:29:00+05:30"), ms("2026-11-07T10:00:00+05:30")) == (
        "Market closed · ranking from 15:29 yesterday"
    )
    assert words.closed_line(ms("2026-11-06T15:29:00+05:30"), ms("2026-11-06T16:00:00+05:30")) == (
        "Market closed · ranking from 15:29"
    )
    assert words.closed_line(
        ms("2026-11-06T15:29:00+05:30"), ms("2026-11-09T09:00:00+05:30")
    ).endswith("from 15:29 on 6 Nov")
    assert words.unavailable(ms("2026-11-09T10:41:00+05:30")) == (
        "Upstox's ranking could not be read just now. Showing the ranking from 10:41."
    )
    assert words.no_time_line(made, made + 30_000) == (
        "Read just now · Upstox gave no time for this ranking"
    )
    assert words.no_time_line(made, made + 125_000).startswith("Read 2 min ago · ")
    assert (
        words.left_out(2)
        == "2 contracts from Upstox are not in today's contract list and are left out."
    )
    assert words.left_out(1).startswith("1 contract from Upstox is not")
    assert words.left_out(0) is None
    assert words.updated_line(ms("2026-11-09T10:43:00+05:30"), ["A", "B", "C", "D"]) == (
        "Ranking updated 10:43 · 4 new: A, B, C and 1 more"
    )
    assert words.updated_line(0, []) is None
    assert words.underlying_chip(12, 50, "NIFTY") == "12 of the top 50 are NIFTY contracts"
    assert words.refresh_wait(20) == "Read 20 s ago"


def test_live_oi_figure_against_previous():
    assert words.oi_figure(112400, 100000) == "OI +12.4%"
    assert words.oi_figure(90000, 100000) == "OI −10.0%"
    assert words.oi_figure(5, None) is None and words.oi_figure(5, 0) is None


def test_catalogue_never_offers_mtf_or_a_commodity_premium():
    cat = ranked.catalogue()
    assert len(cat) == 44 and len({e.ref for e in cat}) == 44
    assert not any("MTF" in e.ref.upper() for e in cat)
    assert not ranked.valid("futures", "COMMODITY", "PREMIUM")
    assert not ranked.valid("options", "INDEX", "PREMIUM")
    assert ranked.valid("options", "STOCK", "UNDER_10000") and ranked.valid(
        "futures", "INDEX", "DISCOUNT"
    )
    assert not ranked.valid("mtf", "INDEX", "TOP_TRADED")
    assert {e.source for e in cat} == {"futures", "options"}


def test_refs_round_trip_and_refuse_what_is_not_offered():
    for e in ranked.catalogue():
        assert ranked.parse_ref(e.ref) == (e.source, e.asset, e.category)
    assert ranked.parse_ref("ranked:futures:COMMODITY:PREMIUM") is None
    assert (
        ranked.parse_ref("list:3") is None
        and ranked.parse_ref("ranked:mtf:INDEX:TOP_TRADED") is None
    )


def test_size_is_clamped():
    assert [ranked.clamp_size(n) for n in (None, 0, 3, 50, 120)] == [20, 20, 3, 50, 50]


def test_new_since_and_the_underlying_chip():
    c = V["cases"][0]
    r = ranked.map_rows(c["answer"], c["category"], MASTER.get)
    assert [x.symbol for x in ranked.new_since(r.rows[:1], r.rows)] == ["BANKNIFTY25NOVFUT"]
    assert [x.symbol for x in ranked.by_underlying(r.rows, "nifty")] == ["NIFTY25NOVFUT"]


def _strings(mod):
    """Every string a module can say, leaving out docstrings."""
    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]  # fmt: skip


def test_a_ranking_is_never_called_live():
    shown = _strings(words) + _strings(ranked)
    assert shown
    assert not any(re.search(r"\blive\b", s, re.I) for s in shown)
    assert not any(re.search(r"\barm(ed|ing)?\b", s, re.I) for s in shown)
    assert not any("Real-time" in s for s in shown)


def test_the_modules_are_pure():
    for mod in (ranked, words):
        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert names <= {"__future__", "typing", "datetime", "collections.abc", "math",
                         "sudarshan.watchlists", "sudarshan.watchlists.words"}  # fmt: skip
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert not attrs & {"now", "utcnow", "monotonic", "sleep", "time"}
