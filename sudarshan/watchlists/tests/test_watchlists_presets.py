"""The built-in lists made from the master (M8.8, W12): the builders against hand-typed vectors
and against the screener's rule. The bridge side is in test_watchlists_presets_bridge.py."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from presets_master import V, index_of, indices_rows, keys, ms

from sudarshan.watchlists import presets
from sudarshan.watchlists.presets import master_lists

ROOT = Path(__file__).resolve().parents[1]


def test_the_typed_indices_are_the_twenty_four_of_the_design():
    assert [n for n, _ in master_lists.INDICES] == V["indices"]
    assert len(master_lists.INDICES) == 24
    assert {n for n, e in master_lists.INDICES if e == "BSE_INDEX"} == set(V["bse_indices"])


def test_indices_all_present():
    entry = presets.entry("indices")
    built = entry.members(entry.prepare(index_of(indices_rows())), 0, {})
    assert [r.symbol for r in built.rows] == V["indices"] and built.missing == ()


def test_indices_with_one_name_missing_leaves_it_out_and_says_so_in_words():
    gone = V["index_missing_one"]
    entry = presets.entry("indices")
    built = entry.members(entry.prepare(index_of(indices_rows(skip=(gone,)))), 0, {})
    assert [r.symbol for r in built.rows] == [n for n in V["indices"] if n != gone]
    assert built.missing == (f"NSE_INDEX:{gone}",)
    out = presets.lines(entry, built, "2026-11-09")
    assert out[2] == (f"1 name in this list is not in today's contract list: {gone}. "
                      "The list is checked at each index review.")  # fmt: skip


def test_two_missing_names_use_the_plural_and_name_both():
    entry = presets.entry("indices")
    built = entry.members(entry.prepare(index_of(indices_rows(skip=("NIFTYIT", "BANKEX")))), 0, {})
    assert presets.lines(entry, built, None)[2] == (
        "2 names in this list are not in today's contract list: NSE_INDEX:NIFTYIT, "
        "BSE_INDEX:BANKEX. The list is checked at each index review.".replace(
            "NSE_INDEX:", ""
        ).replace("BSE_INDEX:", "")
    )


def test_a_name_on_the_wrong_exchange_is_not_the_index():
    spec = [r for r in indices_rows() if r[0] != "SENSEX"] + [
        ("SENSEX", "NSE_INDEX", "INDEX", None, None)
    ]
    entry = presets.entry("indices")
    assert entry.prepare(index_of(spec)).missing == ("BSE_INDEX:SENSEX",)


def test_fno_stocks_against_the_vector():
    entry = presets.entry("fno_stocks")
    built = entry.members(entry.prepare(index_of(V["master"])), 0, {})
    want = V["fno_stocks"]
    assert keys(built.rows) == want["rows"], "RELIANCE counted once, a far expiry included"
    assert list(built.missing) == want["missing"], "an underlying with no cash row is reported"
    assert not set(want["never"]) & set(keys(built.rows)), "NIFTY and the rest stay out"


def test_fno_stocks_says_who_has_futures_and_no_stock_listing():
    entry = presets.entry("fno_stocks")
    built = entry.members(entry.prepare(index_of(V["master"])), 0, {})
    assert presets.lines(entry, built, "2026-11-09")[-1] == (
        "1 stock has futures but no stock listing today: GHOSTCO."
    )


def test_fno_stocks_equals_the_screeners_rule_on_the_same_master():
    """The screener's pure function reads its own Listing copies; ours reads the master's rows.
    One synthetic master, two readers, the same answer."""
    from datetime import date

    from sudarshan.screener import universe

    mapping = [
        {"symbol": s, "exchange": e, "token": f"{e}|{n}", "name": s, "expiry": x,
         "strike": None, "lotsize": 1, "instrumenttype": t, "tick_size": 0.05}
        for n, (s, e, t, _, x) in enumerate(V["master"])
    ]  # fmt: skip
    listings = [universe.listing_of(r) for r in mapping]
    cash = {r.symbol: r for r in listings if r.exchange == "NSE" and r.kind == "EQ"}
    futures = [r for r in listings if r.exchange == "NFO" and r.kind == "FUT"]
    theirs = sorted(m.symbol for m in universe.fno_stocks(cash, futures, date(2026, 11, 10)))
    entry = presets.entry("fno_stocks")
    mine = sorted(r.symbol for r in entry.members(entry.prepare(index_of(V["master"])), 0, {}).rows)
    assert mine == theirs and mine, (mine, theirs)
    # and the set of index underlyings is the screener's
    from sudarshan.alerts.market.sessions import INDEX_UNDERLYINGS

    assert set(master_lists.INDEX_UNDERLYINGS) == set(INDEX_UNDERLYINGS)


@pytest.mark.parametrize("case", V["index_futures"], ids=lambda c: c["name"][:50])
def test_index_futures_roll_at_the_close_of_expiry_day(case):
    entry = presets.entry("index_futures")
    prepared = entry.prepare(index_of(V["master"]))
    closes = {d: ms(t) for d, t in case["closes"].items()}
    built = entry.members(prepared, ms(case["now"]), closes)
    assert keys(built.rows) == case["rows"]
    assert built.valid_until == (None if case["valid_until"] is None else ms(case["valid_until"]))


def test_the_catalogue_is_found_by_discovery_with_a_seam_for_typed_lists():
    # the repository's two typed files are offered with no server folder; the rest need theirs
    assert [e.id for e in presets.CATALOGUE] == [
        "nifty50", "niftybank", "indices", "fno_stocks", "index_futures"]  # fmt: skip
    assert {e.kind for e in presets.CATALOGUE} == {"master", "typed"}
    assert all(e.ref == f"preset:{e.id}" for e in presets.CATALOGUE)
    modules = {m.__name__.rpartition(".")[2] for m in presets._MODULES}
    assert {
        "master_lists",
        "typed_lists",
    } <= modules  # W13 adds typed_lists.py beside it and edits nothing here


def test_the_pure_modules_import_no_flask_and_no_openalgo():
    files = [*(ROOT / "presets").glob("*.py"), ROOT / "words" / "presets.py"]
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                if path.name == "typed_lists.py" and m == "utils.logging":
                    continue  # the one module that reads files, and logs a folder it cannot read
                assert m.split(".")[0] not in ("flask", "database", "utils", "extensions"), (
                    path,
                    m,
                )
                assert not m.startswith("sudarshan.watchlists.ops"), (path, m)
