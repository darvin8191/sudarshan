"""A run's results as a CSV file (M9.16, gap audit GA7.5).

The file is the run's own match set, ranked as the screen ranks, with the prices of the run; it is
named in ASCII whatever the screen is called; a cell a spreadsheet would run as a formula is
written as text; and it is answered as ``{filename, text}``, the shape every SUDARSHAN export has.
"""

from __future__ import annotations

import csv
import io

import pytest
from test_screener_results import call, judged, make, record
from test_screener_results import service as service  # noqa: F401  (the fixture)

from sudarshan.portfolio.imports.csvutil import BOM
from sudarshan.screener import contract, screen_ops
from sudarshan.screener.ops import ApiError
from sudarshan.screener.runs import Judged, Member
from sudarshan.screener.types import NO_FEED, NOT_CHECKED, Outcome, Why


def rows_of(text: str) -> list[list[str]]:
    assert text.startswith(BOM) and "\r\n" in text
    return list(csv.reader(io.StringIO(text[len(BOM) :], newline="")))


def test_the_contract_carries_the_op():
    ops = {s.name: s for s in contract.OPS}
    assert (ops["run_csv"].method, ops["run_csv"].route, ops["run_csv"].cls) == (
        "GET",
        "/runs/<id>/csv",
        "read",
    )


def test_a_run_s_results_as_a_file(service):  # noqa: F811
    screen = make(service, name="Above hundred")
    found = judged({"AAA": 100, "BBB": 200, "CCC": 300}) + [
        Judged(Member("NSE_EQ|Z", "ZZZ", "NSE", "NSE_EQ"), Outcome(NOT_CHECKED, (Why(NO_FEED),)))
    ]
    report = record(service, screen, 1, found)
    got = call(service, "run_csv", id=report.run_id)
    assert set(got) == {"filename", "text"}
    assert got["filename"] == "SUDARSHAN_Screener_S_2026-10-28_0945.csv"
    lines = rows_of(got["text"])
    assert lines[0] == ["सुदर्शन SUDARSHAN", "Screener results"]
    assert lines[1] == ["S", "2026-10-28", "09:30 to 09:45 candle (IST)"]
    assert ["Matches", "3"] in lines and ["Not checked", "1"] in lines
    head = lines.index(list(screen_ops.CSV_COLUMNS))
    assert lines[head + 1 : head + 4] == [
        ["1", "CCC", "NSE", "300.00", "233.33", "", "closed"],
        ["2", "BBB", "NSE", "200.00", "122.22", "", "closed"],
        ["3", "AAA", "NSE", "100.00", "11.11", "", "closed"],
    ], "ranked as the screen ranks, rupees and a plain percentage"
    tail = lines[head + 4 :]
    assert tail == [
        [],
        ["Not checked", "Instruments", "First names"],
        ["Not checked: no price from Upstox yet", "1", "ZZZ"],
    ]
    # the latest run and the same run by its id are the same rows
    latest = call(service, "results", id=screen["id"])
    assert [r["symbol"] for r in latest["rows"]] == ["CCC", "BBB", "AAA"]


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("15-min EMA cross", "15-min-EMA-cross"),
        ("ब्रेकआउट", "screen7"),
        ("Résumé / ../etc", "R-sum-etc"),
        ("", "screen7"),
        ("A" * 60, "A" * 24),
    ],
)
def test_the_file_name_is_ascii_whatever_the_screen_is_called(name, slug):
    made = screen_ops.csv_name(name, 7, 1793160900000)
    assert made == f"SUDARSHAN_Screener_{slug}_2026-10-28_0945.csv"
    assert made.isascii() and "/" not in made and ".." not in made


def test_a_cell_a_spreadsheet_would_run_is_written_as_text(service):  # noqa: F811
    run = {"at": 1793160900000, "candle": "09:30 to 09:45", "matches": 1, "marks": ["=cmd()"]}
    row = {
        "rank": 1,
        "symbol": "=HYPERLINK(1)",
        "exchange": "NSE",
        "ltp": None,
        "change_pct": None,
        "values": "-1 below",
        "forming": True,
    }
    text = screen_ops.csv_text("+SUM(A1)", run, [row], {"count": 0, "groups": []})
    lines = rows_of(text)
    assert lines[1][0] == "'+SUM(A1)" and ["'=cmd()"] in lines
    assert lines[-1] == ["1", "'=HYPERLINK(1)", "NSE", "", "", "'-1 below", "forming"]


def test_a_run_with_no_match_says_so_and_a_run_that_is_gone_is_said(service):  # noqa: F811
    screen = make(service)
    report = record(service, screen, 1, judged({}, missed=("AAA",)))
    lines = rows_of(call(service, "run_csv", id=report.run_id)["text"])
    assert lines[-1] == ["Nothing matched at this check."]
    with pytest.raises(ApiError) as caught:
        call(service, "run_csv", id=999)
    assert caught.value.code == "not_found"
