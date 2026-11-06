"""imports/csvutil.py, imports/generic.py and imports/preview.py (PA7): a generic CSV in and out.

The golden vectors (vectors/import_csv.json, import_generic.json) are written by make_pa7.py, which
reads the same text a character at a time and works the keys out again; this file runs the code on
them (I1-I7 and the CSV cases), and adds what a vector cannot say: the caps, the listing order,
that no float reaches a result, and that an export read back is the same ledger.
"""

from __future__ import annotations

import dataclasses
import json
import random
import string
from pathlib import Path

import pytest
from test_portfolio_vectors import load

from sudarshan.portfolio.core import kinds, words
from sudarshan.portfolio.core.ledger import ordered, replay
from sudarshan.portfolio.core.model import Entry
from sudarshan.portfolio.core.words import Names
from sudarshan.portfolio.imports import csvutil, generic
from sudarshan.portfolio.imports.csvutil import BadFile
from sudarshan.portfolio.imports.generic import AssetRef
from sudarshan.portfolio.imports.preview import Context

VECTORS = Path(__file__).parent / "vectors"
CSV = json.loads((VECTORS / "import_csv.json").read_text(encoding="utf-8"))
GEN = json.loads((VECTORS / "import_generic.json").read_text(encoding="utf-8"))
WORLD = GEN["world"]
BY_ID = {case["id"]: case for case in GEN["previews"]}


def context(case=None, **over):
    """The Context a vector's world and case describe."""
    assets = {int(k): v for k, v in WORLD["assets"].items()}
    lookup = {}
    for number, found in assets.items():
        lookup[found["symbol"].upper()] = number
        lookup[found["isin"].upper()] = number

    def resolve(symbol, exchange, isin):
        return lookup.get(symbol.upper()) or lookup.get(isin.upper())

    accounts = {int(k): v for k, v in WORLD["accounts"].items()}
    case = case or {}
    args = {
        "today": WORLD["today"],
        "resolve": resolve,
        "accounts": accounts,
        "default_account": WORLD["default_account"],
        "existing": tuple(_entry(row) for row in case.get("existing", ())),
        "keys": dict(case.get("keys", {})),
        "names": Names(accounts, {n: found["name"] for n, found in assets.items()}),
    }
    args.update(over)
    return Context(**args)


def _entry(row):
    return dataclasses.replace(Entry.of(row), id=row["id"], seq=row["seq"])


def shape(preview):
    return {
        "summary": preview.summary,
        "rows": [
            {"row": r.row, "status": r.status, "words": r.words, "entry": r.entry, "key": r.key}
            for r in preview.rows
        ],
    }


# ---- the CSV ----------------------------------------------------------------------------------


@pytest.mark.parametrize("case", CSV["tables"], ids=lambda c: c["id"])
def test_a_text_is_read_as_the_vector_says(case):
    want = case["expect"]
    if "refused" in want:
        with pytest.raises(BadFile) as caught:
            csvutil.read_table(case["text"])
        assert str(caught.value) == want["refused"]
        return
    table = csvutil.read_table(case["text"])
    assert list(table.header) == want["header"]
    assert [[r.number, r.cells, r.extra] for r in table.records] == want["records"]


@pytest.mark.parametrize("case", CSV["numbers"], ids=lambda c: repr(c["text"]))
def test_a_number_is_a_whole_count_of_its_unit(case):
    want = case["expect"]
    result = _number(case["text"], case["decimals"])
    assert result == want


def _number(text, places):
    """What csvutil.scaled answers, in the vector's words: a count, or number/negative/places."""
    try:
        return csvutil.scaled(text, places)
    except csvutil.Unreadable as bad:
        return places if bad.kind == "decimals" else bad.kind


@pytest.mark.parametrize("case", CSV["days"], ids=lambda c: repr(c["text"]))
def test_a_day_is_read_day_first(case):
    assert csvutil.parse_day(case["text"]) == case["expect"]


@pytest.mark.parametrize("case", CSV["ratios"], ids=lambda c: repr(c["text"]))
def test_a_ratio(case):
    found = csvutil.parse_ratio(case["text"])
    assert (None if found is None else list(found)) == case["expect"]


@pytest.mark.parametrize("case", CSV["guards"], ids=lambda c: repr(c["cell"]))
def test_the_formula_guard_and_its_undoing(case):
    assert csvutil.guard(case["cell"]) == case["written"]
    assert csvutil.unguard(case["written"]) == case["cell"]


@pytest.mark.parametrize("case", CSV["writes"], ids=lambda c: c["id"])
def test_text_is_written_as_the_vector_says(case):
    assert csvutil.write_table(case["header"], case["rows"]) == case["text"]


def test_a_written_table_is_read_back_whatever_the_cells_are():
    rng = random.Random(7)
    pool = string.ascii_letters + string.digits + ",\"'\n\r\t =+-@;:" + chr(0x20B9) + chr(0x939)
    for _ in range(300):
        rows = [
            ["".join(rng.choice(pool) for _ in range(rng.randint(0, 8))) for _ in range(3)]
            for _ in range(rng.randint(1, 4))
        ]
        text = csvutil.write_table(["a", "b", "c"], rows)
        table = csvutil.read_table(text)
        for record, row in zip(
            table.records, [r for r in rows if any(c.strip() for c in r)], strict=True
        ):
            assert [record.cells[n] for n in "abc"] == [c.strip() for c in row]


def test_every_cell_a_file_writes_that_a_spreadsheet_would_run_is_guarded():
    rng = random.Random(11)
    for _ in range(300):
        cell = "".join(rng.choice("='+-@\t\rab 1") for _ in range(rng.randint(1, 5)))
        written = csvutil.guard(cell)
        assert csvutil.unguard(written) == cell
        if cell.lstrip("'")[:1] in ("=", "+", "-", "@", "\t", "\r"):
            assert written.startswith("'")
            assert written.lstrip("'")[:1] in ("=", "+", "-", "@", "\t", "\r")


# ---- the generic format (I1-I6) -----------------------------------------------------------------


@pytest.mark.parametrize("case", GEN["previews"], ids=lambda c: c["id"])
def test_a_file_previews_as_the_vector_says(case):
    got = generic.preview(case["text"], context(case))
    want = case["expect"]
    assert got.summary == want["summary"]
    assert [shape(got)["rows"][i] for i in range(len(got.rows))] == want["rows"]


def test_the_vectors_cover_the_numbered_cases():
    assert {"I1", "I2", "I3", "I3b", "I3c", "I4", "I5", "I6", "I8", "I9"} <= set(BY_ID)


def test_the_occurrence_number_keeps_identical_rows_apart():
    """I3: three identical rows are three keys; with one removed from the file, the rest still are."""
    text = BY_ID["I3"]["text"]
    keys = [r.key for r in generic.preview(text, context()).rows]
    assert len(set(keys)) == 3
    assert [k.rsplit(":", 1)[1] for k in keys] == ["1", "2", "3"]
    two = text.rsplit("\n", 2)[0] + "\n"
    assert [r.key for r in generic.preview(two, context()).rows] == keys[:2]


def test_importing_the_same_file_again_adds_nothing():
    case = BY_ID["I3"]
    first = generic.preview(case["text"], context())
    keys = {r.key: 20261029 for r in first.rows}
    again = generic.preview(case["text"], context(keys=keys))
    assert again.additions() == []
    assert again.count("dup") == 3


def test_a_trade_is_one_key_whatever_its_charges_note_or_price():
    base = "date,action,symbol,quantity,price,amount\n2026-01-05,BUY,TCS,5,3000,15000\n"
    other = (
        "date,action,symbol,quantity,price,amount,charges,note\n"
        "2026-01-05,BUY,TCS,5,3000,15000,9.99,corrected\n"
    )
    a = generic.preview(base, context()).rows[0].key
    b = generic.preview(other, context()).rows[0].key
    assert a == b
    # a different amount, day, account or quantity is a different trade
    for changed in (
        "2026-01-05,BUY,TCS,5,3000,15001",
        "2026-01-06,BUY,TCS,5,3000,15000",
        "2026-01-05,BUY,TCS,6,3000,15000",
        "2026-01-05,SELL,TCS,5,3000,15000",
    ):
        text = "date,action,symbol,quantity,price,amount\n" + changed + "\n"
        assert generic.preview(text, context()).rows[0].key != a


def test_a_key_the_tab_holds_is_a_duplicate_with_its_date():
    case = BY_ID["I2"]
    got = generic.preview(case["text"], context(case))
    assert [r.status for r in got.rows] == ["dup", "new"]
    assert got.rows[0].words == "Row 2 is already in this tab (imported 28 Oct)."


def test_the_listing_tells_the_problems_first_and_says_when_there_is_more():
    case = BY_ID["I4"]
    got = generic.preview(case["text"], context())
    listed, more = got.listing()
    assert [r["status"] for r in listed] == ["bad", "new"]
    assert more is False
    listed, more = got.listing(limit=1)
    assert [r["row"] for r in listed] == [2] and more is True
    assert set(listed[0]) == {"row", "status", "words", "entry"}


def test_the_summary_names_a_column_nothing_is_read_from():
    text = "date,action,symbol,quantity,price,quanity\n2026-01-05,BUY,TCS,5,3000,7\n"
    got = generic.preview(text, context())
    assert got.unused == ("quanity",)
    assert got.summary == "1 new. Not used: quanity."


def test_a_file_without_a_date_or_action_column_is_refused_in_words():
    with pytest.raises(BadFile) as caught:
        generic.preview("symbol,quantity\nTCS,5\n", context())
    assert str(caught.value) == (
        "The first row of the file must name its columns, and the 'date' and 'action' columns are needed."
    )
    with pytest.raises(BadFile) as caught:
        generic.preview("date,symbol\n2026-01-05,TCS\n", context())
    assert "the 'action' column is needed" in str(caught.value)


def test_an_empty_file_has_no_rows():
    got = generic.preview("date,action\n", context())
    assert got.rows == () and got.summary == "The file has no rows."


# ---- the caps (P19 revised) ---------------------------------------------------------------------


def test_the_caps_are_the_revised_ones():
    limits = GEN["limits"]
    assert kinds.IMPORT_MAX_FILE_BYTES == limits["max_file_bytes"] == 900_000
    assert kinds.IMPORT_MAX_ROWS == limits["max_rows"] == 5_000


def test_a_file_over_the_size_cap_is_refused_before_it_is_read():
    head = "date,action,symbol,quantity,price\n"
    line = "2026-01-05,BUY,TCS,1,1\n"
    exactly = head + line * ((kinds.IMPORT_MAX_FILE_BYTES - len(head)) // len(line))
    exactly += "#" * (kinds.IMPORT_MAX_FILE_BYTES - len(exactly))
    assert len(exactly.encode()) == kinds.IMPORT_MAX_FILE_BYTES
    # the size is the bytes, not the characters: a file of 2-byte letters is twice its length
    over = exactly + "x"
    with pytest.raises(BadFile) as caught:
        generic.preview(over, context())
    assert str(caught.value) == GEN["limits"]["too_big"]
    wide = chr(0xE9) * (kinds.IMPORT_MAX_FILE_BYTES // 2 + 1)
    with pytest.raises(BadFile) as caught:
        generic.preview(wide, context())
    assert str(caught.value) == GEN["limits"]["too_big"]


def test_a_file_of_5001_rows_is_refused_and_one_of_5000_is_not():
    head = "date,action,symbol,quantity,price\n"
    body = "".join(f"2026-01-05,BUY,TCS,1,{1 + n % 3}\n" for n in range(kinds.IMPORT_MAX_ROWS))
    assert len((head + body).encode()) < kinds.IMPORT_MAX_FILE_BYTES
    got = generic.preview(head + body, context())
    assert len(got.rows) == 5000
    with pytest.raises(BadFile) as caught:
        generic.preview(head + body + "2026-01-05,BUY,TCS,1,1\n", context())
    assert str(caught.value) == GEN["limits"]["too_many_rows"]


# ---- money is whole numbers ------------------------------------------------------------------------


def _no_floats(value):
    if isinstance(value, float):
        raise AssertionError(value)
    if isinstance(value, dict):
        for item in value.values():
            _no_floats(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _no_floats(item)


@pytest.mark.parametrize("case", GEN["previews"], ids=lambda c: c["id"])
def test_no_float_reaches_a_result(case):
    got = generic.preview(case["text"], context(case))
    _no_floats(shape(got))
    _no_floats([r.entry for r in got.rows])


def test_a_number_with_too_many_decimals_is_refused_not_rounded():
    text = "date,action,symbol,quantity,price\n2026-01-05,BUY,TCS,1,10.00001\n"
    row = generic.preview(text, context()).rows[0]
    assert row.status == "bad"
    assert row.words == "Row 2: The price '10.00001' has more than 4 decimals."


# ---- export (I7) ----------------------------------------------------------------------------------


def export_of(case):
    assets = {int(k): AssetRef(**v) for k, v in case["assets"].items()}
    accounts = {int(k): v for k, v in case["accounts"].items()}
    return generic.export(case["rows"], accounts, assets, case["tab_id"], case["today"])


def test_the_export_is_the_vector_i7():
    case = GEN["export"]
    got = export_of(case)
    assert got.filename == case["expect"]["filename"]
    assert got.text == case["expect"]["text"]
    assert got.filename.isascii()
    assert got.wire() == {"filename": got.filename, "text": got.text}


def test_a_draft_is_not_exported():
    case = GEN["export"]
    assert len(export_of(case).text.splitlines()) == 1 + 9


def test_the_export_guards_every_cell_that_would_run_in_a_spreadsheet():
    """Mutation target 24: with the guard gone, a note of =HYPERLINK(1) is written as a formula."""
    case = dict(GEN["export"])
    rows = [dict(r) for r in case["rows"]]
    for note in ("=1+1", "+1", "-1", "@x", "'=y"):
        rows[0]["note"] = note
        text = export_of({**case, "rows": rows}).text
        cell = csvutil.read_table(text).records[0].cells["note"]
        assert cell == note
        assert text.splitlines()[1].split(",")[12] == "'" + note


def test_the_file_name_is_ascii_even_when_the_names_are_not():
    case = GEN["export"]
    got = generic.export([], {1: "Zerodha"}, {}, 12, 20261104)
    assert got.filename == "SUDARSHAN_Portfolio_12_2026-11-04.csv"
    assert got.text == ",".join(generic.HEADER) + "\r\n"
    assert case["tab_id"] == 7


def _plain(entry):
    return dataclasses.replace(entry, id=None, seq=0)


def test_an_export_read_back_into_an_empty_tab_is_the_same_ledger():
    case = GEN["export"]
    file = export_of(case)
    ctx = Context(
        today=case["today"],
        resolve=lambda s, e, i: {"RELIANCE": 1, "TCS": 2}.get(s.upper()),
        accounts={1: "Zerodha", 2: "Account B"},
        default_account=None,
    )
    got = generic.preview(file.text, ctx)
    assert got.summary == "9 new"
    first = [_plain(e) for e in ordered(_with(r) for r in case["rows"])]
    back = [_plain(Entry.of(r.entry)) for r in got.additions()]
    assert first == back
    assert len(first) == 9
    notes_in = [r.get("note") for r in case["rows"] if r["status"] == "posted"]
    notes_out = [r.entry.get("note") for r in got.additions()]
    assert notes_out == [n or None for n in notes_in]

    def ledger(entries):
        numbered = [dataclasses.replace(e, id=n, seq=n) for n, e in enumerate(entries, 1)]
        done = replay(numbered)
        return {
            k: (
                p.qty_u,
                p.cost_paise,
                p.realised_paise,
                p.income_paise,
                p.fees_paise,
                [(lot.day, lot.qty_u, lot.cost_paise) for lot in p.lots],
            )
            for k, p in done.positions.items()
        }

    assert ledger(first) == ledger([Entry.of(r.entry) for r in got.additions()])


def _with(row):
    return (
        Entry.of(row)
        if row.get("status") == "posted"
        else dataclasses.replace(Entry.of(row), status="draft")
    )


def test_a_split_with_no_account_exports_without_one_and_comes_back_without_one():
    text = generic.export(
        [
            {
                "id": 1,
                "seq": 1,
                "status": "posted",
                "kind": "SPLIT",
                "trade_date": 20260402,
                "account_id": None,
                "asset_id": 2,
                "ratio_num": 1,
                "ratio_den": 10,
            }
        ],
        {1: "Zerodha"},
        {2: AssetRef("TCS", "NSE", "INE000A00002")},
        1,
        20261030,
    ).text
    assert (
        text.splitlines()[1].split(",")[9] == "" and text.splitlines()[1].split(",")[11] == "1:10"
    )
    ctx = Context(
        today=20261030,
        resolve=lambda s, e, i: 2,
        accounts={1: "Zerodha"},
        default_account=1,
        decimals={2: 0},
    )
    entry = generic.preview(text, ctx).rows[0].entry
    assert (entry["account_id"], entry["ratio_num"], entry["ratio_den"]) == (None, 1, 10)


def test_the_sentences_are_for_a_trader():
    """No code, no exception name, no emoji in what a row says."""
    for case in GEN["previews"]:
        for row in case["expect"]["rows"]:
            said = row["words"] or ""
            assert said.isascii() or "₹" in said
            for banned in ("Exception", "Error", "Traceback", "{", "}"):
                assert banned not in said


def test_the_words_module_has_the_import_sentences():
    assert words.import_summary(0, 0, 0) == "The file has no rows."
    assert words.import_summary(42, 3, 2) == "42 new, 3 already in this tab, 2 with problems"
    assert words.import_summary(0, 0, 1) == "1 with a problem"
