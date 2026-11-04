"""imports/zerodha.py (PA8): the Zerodha tradebook, equity segment, trade id as the duplicate key.

The golden vectors (vectors/import_zerodha.json, written by make_pa8.py from synthetic rows) are
Z1 and its neighbours. The rest is what a vector does not say: a header problem is the whole
file's, the format keeps no raw text, the column table is the single place the names live.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_portfolio_import import _no_floats, context, shape

from sudarshan.portfolio.core import kinds
from sudarshan.portfolio.imports import generic, zerodha
from sudarshan.portfolio.imports.csvutil import BadFile

VECTORS = Path(__file__).parent / "vectors"
FILE = json.loads((VECTORS / "import_zerodha.json").read_text(encoding="utf-8"))
CASES = {case["id"]: case for case in FILE["previews"]}


def ctx(case=None):
    """The Context of this file's world (the same accounts and holdings as the generic vectors)."""
    from test_portfolio_import import WORLD

    for key in ("today", "accounts", "default_account"):
        assert FILE["world"][key] == WORLD[key]
    assert FILE["world"]["assets"].items() <= WORLD["assets"].items()
    return context(case)


@pytest.mark.parametrize("case", FILE["previews"], ids=lambda c: c["id"])
def test_a_tradebook_previews_as_the_vector_says(case):
    got = zerodha.preview(case["text"], ctx(case))
    want = case["expect"]
    tail = f". Not used: {', '.join(want['unused'])}." if want["unused"] else ""
    assert got.summary == want["summary"] + tail
    assert list(got.unused) == want["unused"]
    assert shape(got)["rows"] == want["rows"]
    _no_floats(shape(got))


@pytest.mark.parametrize("case", FILE["refused"], ids=lambda c: c["id"])
def test_a_file_that_is_not_a_tradebook_is_refused_in_words(case):
    with pytest.raises(BadFile) as caught:
        zerodha.preview(case["text"], ctx())
    assert str(caught.value) == case["expect"]


def test_the_vector_names_the_columns_the_module_expects():
    """The columns table is the one place the header names live (a real file is checked against it)."""
    assert list(zerodha.COLUMNS) == FILE["columns"]
    assert {name for name, needed in zerodha.COLUMNS.items() if needed} == {
        "symbol",
        "trade_date",
        "trade_type",
        "quantity",
        "price",
        "trade_id",
    }


def test_re_importing_a_file_adds_nothing():
    """Z1: what the first import keeps (the keys) makes the second import empty."""
    case = CASES["Z1"]
    first = zerodha.preview(case["text"], ctx())
    keys = {row.key: 20261015 for row in first.rows}
    again = zerodha.preview(case["text"], _with_keys(keys))
    assert again.additions() == []
    assert again.count("dup") == 3


def _with_keys(keys):
    return context(None, keys=keys)


def test_the_trade_id_is_the_key_not_the_content():
    """A change to the price of a trade with the same id is still the same trade."""
    case = CASES["Z1"]
    first = zerodha.preview(case["text"], ctx())
    keys = {row.key: 20261015 for row in first.rows}
    edited = case["text"].replace("1400.50", "1400.55")
    again = zerodha.preview(edited, _with_keys(keys))
    assert again.count("dup") == 3


def test_a_non_equity_row_does_not_stop_the_equity_rows():
    got = zerodha.preview(CASES["Z4"]["text"], ctx())
    assert got.count("new") == 1 and got.count("bad") == 6
    listed, _ = got.listing()
    assert [r["status"] for r in listed][:6] == ["bad"] * 6


def test_the_size_and_row_caps_are_those_of_the_generic_format():
    over = "x" * (kinds.IMPORT_MAX_FILE_BYTES + 1)
    with pytest.raises(BadFile) as caught:
        zerodha.preview(over, ctx())
    assert str(caught.value) == kinds.WORDS["import_too_big"]
    head = CASES["Z1"]["text"].splitlines()[0] + "\n"
    one = "TCS,,2026-01-05,NSE,EQ,EQ,buy,false,1,1,{n},O,t\n"
    body = "".join(one.format(n=n) for n in range(kinds.IMPORT_MAX_ROWS + 1))
    with pytest.raises(BadFile) as caught:
        zerodha.preview(head + body, ctx())
    assert "5,000 rows" in str(caught.value)


def test_a_tradebook_is_not_a_generic_file_and_the_other_way_round():
    with pytest.raises(BadFile):
        generic.preview(CASES["Z1"]["text"], ctx())
    with pytest.raises(BadFile):
        zerodha.preview("date,action,symbol\n2026-01-05,BUY,TCS\n", ctx())


def test_the_result_keeps_the_normalised_rows_and_not_the_file():
    got = zerodha.preview(CASES["Z1"]["text"], ctx())
    blob = json.dumps(shape(got))
    for raw in ("ORD100001", "09:20:05", "false"):
        assert raw not in blob
