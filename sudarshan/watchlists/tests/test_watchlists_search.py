"""The search grammar (W16) against hand-typed vectors, plus its reference-map rules."""

from __future__ import annotations

import ast
import gc
import json
import time
import weakref
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from sudarshan import regimes
from sudarshan.watchlists import search
from sudarshan.watchlists.words import search as words

V = json.loads((Path(__file__).parent / "vectors" / "search.json").read_text(encoding="utf-8"))
NOW = int(datetime.fromisoformat(V["now"]).timestamp() * 1000)
#: The index underlyings the master lists (review M1): here the names every reader knows.
KNOWN = frozenset(regimes.INDEX_UNDERLYINGS)


@dataclass
class Row:
    symbol: str
    name: str
    exchange: str
    token: str
    expiry: str | None = None
    strike: float | None = None
    lotsize: int | None = None
    instrumenttype: str | None = None
    underlying: str | None = None


def make_rows() -> list[Row]:
    rows = [Row(s, n, ex, tok, instrumenttype=t) for s, n, ex, tok, t in V["master"]["cash"]]
    for d in V["master"]["derivs"]:
        u, ex, lot = d["u"], d["ex"], d["lot"]
        for e in d["fut"]:
            rows.append(Row(f"{u}{e}FUT", u, ex, f"{ex}_FO|{u}{e}", e, None, lot, "FUT", u))
        for e, (lo, hi, step) in d["opt"].items():
            for k in range(lo, hi + 1, step):
                for t in ("CE", "PE"):
                    rows.append(
                        Row(f"{u}{e}{k}{t}", u, ex, f"{ex}_FO|{u}{e}{k}{t}", e, float(k), lot, t, u)
                    )
    return rows


ROWS = make_rows()
IX = search.build_index(ROWS)


def shown(hit):
    return f"{hit[1].exchange}:{words.title(hit[1])} | {words.detail(hit[1])}"


def run(c):
    return search.search(
        IX, c["q"], price=c.get("price"), now_ms=NOW, indices=KNOWN, segment=c.get("seg", "all")
    )


@pytest.mark.parametrize(
    "c", V["cases"], ids=lambda c: f"{c['q']}-{c.get('seg', '')}-{c.get('price', '')}"
)
def test_vectors(c):
    r = run(c)
    got = [shown(h) for h in r.hits]
    if "hits" in c:
        assert got == c["hits"]
    if "n" in c:
        assert len(got) == c["n"]
    if "first" in c:
        assert got[0] == c["first"]
    if "pick" in c:
        assert r.underlying == c["pick"]
    if "line" in c:
        assert words.understood_line(r.understood) == c["line"]
    if "suggest" in c:
        assert list(r.suggestions) == c["suggest"]
    if "words" in c:
        assert words.no_match(c["q"], r.suggestions) == c["words"]
    if "groups" in c:
        assert sorted({h[0] for h in r.hits}) == c["groups"]


def test_a_day_and_month_is_a_date_not_a_strike():
    p = search.parse("tcs 24 nov")
    assert p["strike"] is None and p["expiry"] == ("day", 11, 24)


@pytest.mark.parametrize(
    ("q", "strike"),
    [("reliance 950 ce", 950), ("tatamotors 85 pe", 85), ("m&m 2700 pe", 2700), ("nifty 26000", 26000),
     ("nifty 26,000 pe", 26000), ("pe 26000 nifty", 26000), ("abc 99.5 pe", 99.5), ("abc 50", None)],
)  # fmt: skip
def test_strikes(q, strike):
    assert search.parse(q)["strike"] == strike


def test_a_bare_small_number_is_part_of_the_name():
    assert search.parse("nifty 50")["alias"] == "NIFTY"


def test_the_price_is_only_asked_for_by_atm():
    assert search.search(IX, "nifty atm ce", now_ms=NOW, indices=KNOWN).understood.needs_price
    assert not search.search(IX, "nifty 25900 ce", now_ms=NOW, indices=KNOWN).understood.needs_price


def test_at_most_fifty_hits():
    r = search.search(IX, "nifty ce", price=25900, now_ms=NOW, indices=KNOWN, limit=500)
    assert len(r.hits) <= 50
    assert len(search.search(IX, "nifty 26000", now_ms=NOW, indices=KNOWN, limit=2).hits) == 2


def test_typo_vocabulary_rules():
    names = ["RELIANCE", "TCS", "TATAMOTORS"]
    assert search.suggest("relaince", names) == ("RELIANCE",)
    assert search.suggest("rlance", names) == ()  # two edits
    assert search.suggest("tcz", names) == ()  # under four letters
    assert search.suggest("reliancee", names) == ("RELIANCE",)
    many = ["ABCDE", "ABCDF", "ABCDG", "ABCDH"]
    assert len(search.suggest("abcdx", many)) == 3


def test_expired_futures_are_not_offered():
    r = search.search(IX, "crudeoil fut", now_ms=NOW, indices=KNOWN)
    assert [words.title(h[1]) for h in r.hits] == ["CRUDEOIL FUT, 18 Dec 2026"]


def test_an_index_the_master_lists_beyond_the_known_names_ends_by_the_index_rule():
    """Review M1: from 7 Dec 2026 an expiring index contract stops at 15:30 and a stock's at 15:45;
    which underlyings are an index's is the master's answer, passed in, never a list of search's."""
    rows = [Row("NEWINDEX", "NEWINDEX", "NSE_INDEX", "NSE_INDEX|NEWINDEX", instrumenttype="INDEX")]
    for e in ("29-DEC-26", "26-JAN-27"):
        rows.append(Row(f"NEWINDEX{e.replace('-', '')}FUT", "NEWINDEX", "NFO", f"NSE_FO|N{e}", e,
                        None, 25, "FUT", "NEWINDEX"))  # fmt: skip
    ix = search.build_index(rows)
    at = int(datetime.fromisoformat("2026-12-29T15:35:00+05:30").timestamp() * 1000)

    def futures(indices):
        return [
            h[1].symbol for h in search.search(ix, "newindex fut", now_ms=at, indices=indices).hits
        ]

    assert futures(KNOWN | {"NEWINDEX"}) == ["NEWINDEX26JAN27FUT"]  # 29 Dec stopped at 15:30
    assert futures(KNOWN) == ["NEWINDEX29DEC26FUT", "NEWINDEX26JAN27FUT"]  # read as a stock's


def test_the_index_holds_the_rows_themselves_never_copies():
    ids = {id(r) for r in ROWS}
    refs = [r for lst in IX.cash.values() for r in lst]
    refs += [r for lst in IX.futures.values() for _, r in lst]
    refs += [r for by in IX.options.values() for lst in by.values() for r in lst]
    refs += [r for lst in IX.isin.values() for r in lst]
    assert refs and all(id(r) in ids for r in refs)


def test_after_the_master_is_replaced_the_old_rows_are_gone():
    rows = make_rows()
    ref = weakref.ref(rows[0])
    ix = search.build_index(rows)
    assert search.search(ix, "reliance", now_ms=NOW, indices=KNOWN).hits
    del rows, ix
    gc.collect()
    assert ref() is None


def test_build_and_query_times_on_a_full_size_master():
    rows = []
    for u in range(600):
        for e in ("24-NOV-26", "01-DEC-26", "29-DEC-26"):
            for k in range(25000, 25000 + 33 * 50, 50):
                for t in ("CE", "PE"):
                    rows.append(Row(f"U{u}{e}{k}{t}", "x", "NFO", "t", e, float(k), 25, t, f"U{u}"))
    rows += [
        Row(
            f"S{i}",
            f"Stock {i}",
            "NSE",
            f"NSE_EQ|INE{i:06d}A0{i % 10}00{i % 7}",
            instrumenttype="EQ",
        )
        for i in range(3000)
    ]
    assert len(rows) >= 120_000
    builds = []
    for _ in range(3):
        t0 = time.perf_counter()
        b = search.IndexBuilder()
        for i in range(0, len(rows), 5000):
            b.add(rows[i : i + 5000])
        ix = b.finish()
        builds.append(time.perf_counter() - t0)
    assert min(builds) < 0.3, builds
    t0 = time.perf_counter()
    r = search.search(ix, "u7 25500 ce", now_ms=NOW, indices=KNOWN)
    assert time.perf_counter() - t0 < 0.02 and len(r.hits) == 3


def test_the_module_is_pure():
    for mod in (search, words):
        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert names <= {
            "__future__",
            "collections.abc",
            "re",
            "typing",
            "sudarshan.watchlists",
            "sudarshan.watchlists.search",
        }
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert not attrs & {"now", "utcnow", "time", "monotonic", "sleep"}
