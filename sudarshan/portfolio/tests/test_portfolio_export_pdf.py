"""M7.40 (P7 lane X, X2): a tab's PDF export per view, printed by the journal's report renderer.

The PDF is read back with the journal's own small reader (``journal/tests/render_pdfread.py``):
every figure is checked against the view the page is made from, a gain or a loss is never told
by colour alone, and the text of each page of an invented tab is kept as a golden file.
"""

from __future__ import annotations

import ast
import base64
import importlib.util
import os
import re
import sys
import zlib
from pathlib import Path

from test_portfolio_export import (  # noqa: F401  (the rig and the invented tab)
    DAY,
    NOW,
    OWNER,
    clean,
    file_of,
    golden,
    on,
    scenario_of,
    stocked,
)

from sudarshan.journal.render import doc as D
from sudarshan.journal.render import style
from sudarshan.portfolio import export, export_pdf
from sudarshan.portfolio.core import kinds
from sudarshan.portfolio.core import words as W
from sudarshan.portfolio.ops import REGISTRY, export_ops

RENDER = Path(D.__file__).parent
_spec = importlib.util.spec_from_file_location(
    "render_pdfread", RENDER.parent / "tests" / "render_pdfread.py"
)
pdfread = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pdfread  # its dataclasses look the module up by name
_spec.loader.exec_module(pdfread)


def _stream(obj: bytes) -> bytes | None:
    """The reader's own ``_stream`` strips line ends off the data, and a compressed stream may
    end in one of those bytes (a 400-row page here does). This one takes the stream by its
    /Length, as the format says; the reader is the journal's and is not changed."""
    start = re.search(rb"stream\r?\n", obj)
    if not start:
        return None
    length = int(re.search(rb"/Length (\d+)", obj[: start.start()]).group(1))
    raw = obj[start.end() : start.end() + length]
    return zlib.decompress(raw) if b"/FlateDecode" in obj[: start.start()] else raw


pdfread._stream = _stream

MINUS = "−"
TAB = {"id": 7, "name": "Long term", "name_alt": None}


def rgb(colour):
    return tuple(round(part / 255, 3) for part in colour)


def near(got, want) -> bool:
    return all(abs(a - b) < 0.003 for a, b in zip(got, rgb(want), strict=True))


async def pdf_of(rig, view):
    file = await file_of(rig, view, "export_pdf")
    assert file["filename"] == f"SUDARSHAN_Portfolio_Long-term_{view}_{DAY}.pdf"
    data = base64.b64decode(file["base64"], validate=True)
    assert data.startswith(b"%PDF-")
    return pdfread.read_pdf(data)


def lines_of(pdf) -> list[str]:
    return [line for page in pdf.pages for line in page.lines]


def said(pdf, label: str) -> str:
    """The rest of the table row that starts with ``label``."""
    found = [line for line in lines_of(pdf) if line.startswith(label)]
    assert found, label
    return found[0][len(label) :]


def word(pdf, label: str) -> str | None:
    """The word in the filled box on the row that starts with ``label`` (None: no box)."""
    starts = [(page, t) for page in pdf.pages for t in page.texts if t.string == label]
    assert starts, label
    for page, start in starts:
        for t in page.texts:
            if (
                t.string in style.CHIP_FILL
                and abs(t.y - start.y) < 4
                and near(t.colour, style.WHITE)
            ):
                return t.string
    return None


def money(paise: int) -> str:
    return export_pdf.text_of(export.Amount(paise))


def gain(paise: int) -> str:
    return D.Money(paise).text


def check_look(pdf):
    """The report header on every page, a light page, and never colour alone."""
    for page in pdf.pages:
        assert "SUDARSHAN" in page.text and page.count("c") > 40  # the wordmark's outlines
        for rect in page.rects:  # nothing dark is filled but a chip, a rule or the gold run
            dark = sum(rect.colour) < 1.5
            assert not dark or rect.h < 13 or rect.w < 2, rect
        for text in page.texts:
            if near(text.colour, style.PROFIT):
                assert text.string.startswith("+"), text
            if near(text.colour, style.LOSS):
                assert text.string.startswith(MINUS), text
            assert not text.string.endswith("...") or "\u20b9" not in text.string, (
                text
            )  # no sum cut
        foot = "Exported from SUDARSHAN. As of 26 Oct 2026, 13:03 IST."
        assert any(line.startswith(foot) for line in page.lines[-2:]), page.lines[-2:]
    assert pdf.info["Creator"] == "SUDARSHAN"


def chips(pdf) -> list[str]:
    return [t.string for page in pdf.pages for t in page.texts
            if t.string in style.CHIP_FILL and near(t.colour, style.WHITE)]  # fmt: skip


def test_the_pdf_op_is_built_and_the_renderer_is_only_imported():
    assert "export_pdf" in REGISTRY and kinds.SWITCH_OF["export_pdf"] == "goals"
    # The five files the export leans on know nothing of the journal's record or its service.
    for name in ("doc", "pdf", "style", "fonts", "paths"):
        tree = ast.parse((RENDER / f"{name}.py").read_text())
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom):
                names = [f"{node.module}.{a.name}" for a in node.names]
            for dotted in names:
                assert not any(part in dotted.split(".") for part in
                               ("store", "service", "ops", "api", "portfolio")), (name, dotted)  # fmt: skip


@scenario_of
async def test_while_the_switch_is_off_no_pdf_is_made(rig):
    for view in (None, *kinds.EXPORT_VIEWS):
        args = {"view": view} if view else {}
        answer = clean(await rig.call("export_pdf", id=rig.tab["id"], **args))
        assert (answer["code"], answer["message"]) == ("not_ready", kinds.WORDS["not_ready"])


@scenario_of
async def test_the_holdings_page_says_the_tabs_figures_with_sign_and_word(rig):
    stocked(rig)
    shown = (await rig.call("tab_get", id=rig.tab["id"], accounts=[]))["data"]
    pdf = await pdf_of(rig, "holdings")
    check_look(pdf)
    totals, fixed, lines = shown["totals"], shown["fixed"], lines_of(pdf)
    assert "Holdings · as of 26 Oct 2026, 13:03 IST" in lines
    for text in (money(totals["value_paise"]), money(totals["cost_paise"]),
                 gain(totals["unrealised_paise"]), gain(totals["overall_gain_paise"])):  # fmt: skip
        assert any(text in line for line in lines[:12]), text  # the four big figures
    assert said(pdf, "Total") == (
        money(totals["cost_paise"]) + money(totals["value_paise"])
        + gain(totals["unrealised_paise"])
    )  # fmt: skip
    assert word(pdf, "Total") == "PROFIT"
    assert said(pdf, "Realised gain") == gain(fixed["realised_paise"])
    assert word(pdf, "Realised gain") == "LOSS" and word(pdf, "Income") is None
    assert said(pdf, "Realised gain").startswith(MINUS)
    assert said(pdf, "Income") == money(fixed["income_paise"])
    assert said(pdf, "Absolute return") == "+5.12%"  # two decimals of the view's 5.1218
    assert said(pdf, "Loans outstanding") == money(fixed["liabilities_paise"])
    assert said(pdf, "Home loan") == money(20_000_000) + "1 Oct 2026"
    assert said(pdf, "Reliance Industries") == "Zerodha10" + money(1_001_500) * 2 + gain(
        0
    )  # at cost
    assert word(pdf, "Reliance Industries") == "FLAT" and word(pdf, "Gold bar") == "PROFIT"
    assert "Reliance Industries: No price yet" in lines
    assert "1 holding has no price yet and is counted at its cost." in lines
    # One word in a filled box for every gain on the page, the three big ones' chips included.
    assert sorted(chips(pdf)) == sorted(
        ["PROFIT", "PROFIT", "PROFIT", "PROFIT", "FLAT", "PROFIT", "LOSS"]
    )
    golden("holdings.pdf.txt", "\n".join(lines) + "\n")


@scenario_of
async def test_the_performance_and_income_pages_say_the_performance_views_figures(rig):
    stocked(rig)
    report = (await rig.call("performance", id=rig.tab["id"]))["data"]
    pdf = await pdf_of(rig, "performance")
    check_look(pdf)
    lines = lines_of(pdf)
    assert said(pdf, "Money in") == money(report["money_in_paise"])
    assert said(pdf, "Net put in") == money(report["net_put_in_paise"])
    assert said(pdf, "Total gain") == gain(report["total_gain_paise"])
    assert word(pdf, "Total gain") == "PROFIT" and word(pdf, "Money in") is None
    assert said(pdf, "XIRR") == "+9.09%" and report["xirr"]["pct"] == "9.0861"
    assert f"TWR: {report['twr']['words']}" in lines
    for fy in report["by_fy"]:
        assert word(pdf, fy["label"]) == D.Money(fy["realised_paise"]).result
        assert said(pdf, fy["label"]) == (
            gain(fy["realised_paise"]) + money(fy["income_paise"]) + money(fy["fees_paise"])
        )
    # The paper lists the last value of each month, the latest day included; the CSV every day.
    # (The history stops the day before a holding with no closing price was bought.)
    ends = [p for p, after in zip(report["series"], [*report["series"][1:], None], strict=True)
            if after is None or after["day"] // 100 != p["day"] // 100]  # fmt: skip
    assert len(ends) == 10 and ends[-1]["day"] == report["series"][-1]["day"]
    at = lines.index("Month-end values")
    assert lines[at + 2 : at + 2 + len(ends)] == [
        W.date(p["day"]) + money(p["value_paise"]) + money(p["net_put_in_paise"]) for p in ends
    ]
    assert "Daily values" not in lines
    assert any(line.startswith("NIFTY 50: Dividends not included") for line in lines)
    golden("performance.pdf.txt", "\n".join(lines) + "\n")

    pdf = await pdf_of(rig, "income")
    check_look(pdf)
    found = report["income"]
    assert said(pdf, "Last 12 months") == money(found["last_12m_paise"])
    assert said(pdf, "Mar 2026") == money(120_000) and said(pdf, "Yield on cost") == "0.50%"
    assert chips(pdf) == []  # income is no gain or loss: plain ink, no box
    golden("income.pdf.txt", "\n".join(lines_of(pdf)) + "\n")


@scenario_of
async def test_the_allocation_and_entries_pages(rig):
    stocked(rig)
    shown = (await rig.call("tab_get", id=rig.tab["id"], accounts=[]))["data"]
    pdf = await pdf_of(rig, "allocation")
    check_look(pdf)
    lines = lines_of(pdf)
    for by, title in export.ALLOCATION_TITLES.items():
        at = lines.index(title)
        bars = shown["allocation"][by]
        assert lines[at + 2 : at + 2 + len(bars)] == [
            b["label"] + money(b["value_paise"]) + export_pdf.text_of(export.Pct(b["pct"]))
            + str(b["holdings"]) for b in bars
        ], by  # fmt: skip
    golden("allocation.pdf.txt", "\n".join(lines) + "\n")

    pdf = await pdf_of(rig, "activity")
    check_look(pdf)
    lines = lines_of(pdf)
    listed = rig.book.activities_list(OWNER, rig.tab["id"])["items"]
    assert said(pdf, "1 Sep 2025") == "BuyGold barZerodha3" + money(30_000_000) + money(45_000)
    assert said(pdf, "20 Aug 2026") == "DividendReliance IndustriesZerodha" + money(5_000)
    rows = lines[lines.index("Entries") + 2 :][: len(listed)]
    assert len(rows) == len(listed) == 9
    assert [row[: len("1 Sep 2025")].strip() for row in rows][0] == "1 Sep 2025"
    golden("activity.pdf.txt", "\n".join(lines) + "\n")
    # With no view asked for, the PDF is the holdings.
    plain = clean(await rig.call("export_pdf", id=rig.tab["id"]))["data"]
    assert plain["filename"] == f"SUDARSHAN_Portfolio_Long-term_holdings_{DAY}.pdf"


def test_a_long_ledger_prints_its_newest_entries_and_says_so():
    rows = [
        {"id": n, "seq": n, "kind": "FEE", "status": "posted", "trade_date": 20240101 + n % 28,
         "account_id": 1, "amount_paise": 100 + n, "note": None}
        for n in range(1, 451)
    ]  # fmt: skip
    rows.append({**rows[0], "id": 999, "seq": 999, "status": "draft"})
    sheet = export.entries(TAB, rows, {1: "Bank"}, {}, NOW)
    assert len(sheet.sections[0].rows) == export.PAPER_ENTRIES == 400
    assert sheet.figures[0] == ("Entries", "450")
    assert sheet.notes == ("The 400 newest of 450 entries. The CSV file has every entry.",)
    assert sheet.sections[0].rows[-1][0] == export.Day(20240128)
    pdf = pdfread.read_pdf(export_pdf.pdf_bytes(sheet))
    assert 9 <= len(pdf.pages) <= 14 and sheet.notes[0] in lines_of(pdf)
    assert f"Page {len(pdf.pages)} of {len(pdf.pages)}" in pdf.pages[-1].text


def test_a_holding_of_crores_is_printed_whole_never_cut_to_its_column():
    crore = 10_000_000_00
    line = {"account_id": 1, "qty_u": 1_234_560_000, "open_qty_u": 1_234_560_000,
            "cost_paise": 9 * crore + 12_345, "in_today_paise": 0, "out_today_paise": 0}  # fmt: skip
    row = {
        "asset": {"name": "A holding with quite a long name indeed", "symbol": None,
                  "exchange": None, "isin": None, "asset_class": "equity"},
        "price": {"price_u": 1_000_025_000, "prev_close_u": None, "day": 20261023},
        "fresh": {"words": None}, "today_mode": "none", "lines": [line],
    }  # fmt: skip
    value = 12_345_908_640  # 1,234.56 units at 1,00,002.50
    shown = {
        "tab": TAB, "read_at": NOW, "rows": [row], "liabilities": [], "notes": [],
        "fixed": {"realised_paise": -3 * crore, "income_paise": 0, "fees_paise": 0,
                  "liabilities_paise": 0},
        "totals": {"value_paise": value, "cost_paise": line["cost_paise"],
                   "unrealised_paise": value - line["cost_paise"], "overall_gain_paise": 0,
                   "absolute_return_pct": None, "unpriced": 0},
    }  # fmt: skip
    pdf = pdfread.read_pdf(export_pdf.pdf_bytes(export.holdings(shown, {1: "Demat account one"})))
    check_look(pdf)
    texts = {t.string for t in pdf.pages[0].texts}
    joined = "".join(lines_of(pdf))
    assert money(value) == "\u20b9123,45,90,86.40".replace("123,45,90,86", "12,34,59,086")
    assert money(value) in joined and money(line["cost_paise"]) in joined
    assert "1,234.56" in texts
    assert any(t.endswith("...") for t in texts)  # the long name is what gives way
    assert gain(value - line["cost_paise"]) in joined and gain(-3 * crore) in joined


def test_paper_writes_a_minus_a_percent_and_a_tab_it_cannot_spell():
    text = export_pdf.text_of
    assert text(export.Amount(-50_000)) == MINUS + "₹500.00"
    assert text(export.Gain(-50_000)) == MINUS + "₹500.00" and text(export.Gain(0)) == "₹0.00"
    assert text(export.Gain(142_825)) == "+₹1,428.25"
    assert [text(export.Pct(t, True)) for t in ("8.4274", "-1.2050", "0.0000", "0.0049")] == [
        "+8.43%", MINUS + "1.21%", "0.00%", "0.00%"]  # fmt: skip
    assert text(export.Pct("41.8943")) == "41.89%" and text(export.Qty(1_500_000)) == "1.5"
    assert [text(c) for c in (None, export.Pct(None), export.Day(None), export.Price(None))] == [
        ""
    ] * 4
    sheet = export.Sheet("holdings", {"id": 7, "name": "લાંબા ગાળા"}, NOW, (), ())
    assert export_pdf.doc_of(sheet).title == "Tab 7"
    assert export_pdf.doc_of(sheet).period_label == "Holdings · as of 26 Oct 2026, 13:03 IST"


@scenario_of
async def test_a_pdf_that_cannot_be_made_is_said_in_words_and_refusals_are_the_csvs(rig):
    on(rig)
    system = next(t for t in rig.book.tabs(OWNER) if t["system"])
    refused = clean(await rig.call("export_pdf", id=system["id"], view="holdings"))
    assert refused["code"] == "bad_request" and "Reports" in refused["message"]
    assert clean(await rig.call("export_pdf", id=999, view="activity"))["code"] == "not_found"
    assert clean(await rig.call("export_pdf", id=rig.tab["id"], view="x"))["field"] == "view"
    real = export_pdf.pdf_file
    export_pdf.pdf_file = lambda sheet: (_ for _ in ()).throw(ImportError("No module named 'fpdf'"))
    try:
        failed = clean(await rig.call("export_pdf", id=rig.tab["id"], view="holdings"))
    finally:
        export_pdf.pdf_file = real
    assert (failed["code"], failed["message"]) == ("unavailable", export_ops.NO_PDF)
    assert "fpdf" not in failed["message"]


def test_making_pdfs_leaves_no_file_open():
    sheet = export.Sheet("holdings", TAB, NOW, (("Value", export.Amount(100)),), ())
    export_pdf.pdf_bytes(sheet)  # the first one loads the library and reads the fonts
    before = len(os.listdir("/proc/self/fd"))
    for _ in range(5):
        assert export_pdf.pdf_bytes(sheet).startswith(b"%PDF-")
    assert len(os.listdir("/proc/self/fd")) == before
