"""The report PDF (JB8; design 8.2 Renderer): it opens, it holds the words, it never leans on
colour alone."""

from __future__ import annotations

import os
import re
import time

import pytest
from render_pdfread import read_pdf
from render_samples import COLUMNS, monthly, trade_rows, week_equity

from sudarshan.journal.core import words
from sudarshan.journal.render import doc as D
from sudarshan.journal.render import pdf, png, style

MONEY = re.compile(r"[+−]?₹[\d,]+\.\d\d")


def _doc(*blocks, kind="eod", key="20261109"):
    return D.ReportDoc(
        kind, key, "Day report", "Mon 9 Nov 2026", "Built Mon 9 Nov 2026, 15:50", tuple(blocks)
    )


def test_the_pdf_opens_with_a_pdf_parser_and_its_structure_is_sound():
    data = pdf.render_pdf(monthly())
    parsed = read_pdf(data)  # strict: header, trailer, every offset on its object
    assert len(parsed.pages) == 1
    assert parsed.info["Title"] == "SUDARSHAN - Monthly statement - October 2026"
    assert parsed.info["Creator"] == "SUDARSHAN"


def test_it_holds_the_expected_text():
    parsed = read_pdf(pdf.render_pdf(monthly()))
    lines = "\n".join(parsed.pages[0].lines)
    for expected in (
        "SUDARSHAN",
        "Monthly statement",
        "October 2026",
        "Net after est. charges",
        "+₹3,12,345.67",
        "−₹4,567.00",
        "Net +₹3,12,345.67 (after est. charges ₹4,862.00)",
        "Deepest drawdown",
        "Total",
        "ALPHA 25000 CE",
        "A working paper: verify with CA; the broker's own report is the filing source.",
        "Page 1 of 1",
        "Built Sun 1 Nov 2026, 07:00",
    ):
        assert expected in lines, expected


def test_the_page_header_names_the_platform_on_every_page_and_pages_are_counted():
    parsed = read_pdf(pdf.render_pdf(monthly(rows=80)))
    n = len(parsed.pages)
    assert n >= 3
    for i, page in enumerate(parsed.pages, 1):
        assert "SUDARSHAN" in page.text
        assert "Monthly statement" in page.text
        assert f"Page {i} of {n}" in page.lines[-1] or f"Page {i} of {n}" in "\n".join(page.lines)


def test_the_header_line_of_the_words_is_not_printed_twice():
    text = words.eod(20261109, 431250, -60000, 6, 3, 2, 1, [], [], None, None, None)
    para = D.Paragraph.from_words(text)
    assert words.HEADER not in para.lines
    assert para.lines[0].startswith("Mon 9 Nov")


def test_v1_holds_latin_and_the_rupee_only_no_indic_text():
    d = _doc(D.Paragraph(("Strategy सुदर्शन ok ₹5.00",)))
    parsed = read_pdf(pdf.render_pdf(d))
    text = parsed.text
    assert not re.search("[ऀ-ॿ]", text)
    assert "Strategy ??????? ok ₹5.00" in "\n".join(parsed.pages[0].lines)


def test_every_money_cell_carries_its_sign():
    cells = [D.Money(v) for v in (1, -1, 99, -99, 123456, -123456, 18240000, -18240000)]
    for c in cells:
        assert c.text[0] in "+−", c.text
        assert "-" not in c.text  # the true minus, never a hyphen
    assert D.Money(0).text == "₹0.00"  # zero has no sign
    rows = tuple((f"r{i}", D.Money(v.paise)) for i, v in enumerate(cells))
    table = D.Table((D.Column("Row"), D.Column("Net", 1, "r")), rows, total=("Total", D.Money(-5)))
    parsed = read_pdf(pdf.render_pdf(_doc(D.Heading("Money"), table)))
    amounts = [ln for ln in parsed.pages[0].lines if ln.startswith(("r", "Total"))]
    found = [m for ln in amounts for m in MONEY.findall(ln)]
    assert len(found) == len(cells) + 1
    assert all(m[0] in "+−" for m in found), found
    assert "-₹" not in parsed.text


def test_a_money_cell_has_no_unsigned_form():
    assert not hasattr(D.Money(5), "signed")
    with pytest.raises(TypeError):
        D.Money(5, signed=False)  # type: ignore[call-arg]


def test_chips_are_filled_boxes_with_their_word_inside():
    cells = tuple((D.Chip(w),) for w in ("PROFIT", "LOSS", "LONG", "SHORT", "FLAT"))
    table = D.Table((D.Column("Word", 1, "c"),), cells)
    page = read_pdf(pdf.render_pdf(_doc(table))).pages[0]
    for word in ("PROFIT", "LOSS", "LONG", "SHORT", "FLAT"):
        text = next(t for t in page.texts if t.string == word)
        want = tuple(round(c / 255, 4) for c in style.CHIP_FILL[word])
        box = [
            r
            for r in page.rects
            if tuple(round(c, 4) for c in r.colour) == want
            and r.x <= text.x <= r.x + r.w
            and r.y <= text.y <= r.y + r.h
        ]
        assert box, f"{word} is not inside a box of its own fill"
        assert tuple(round(c, 4) for c in text.colour) == (1.0, 1.0, 1.0)  # white on the fill


def test_a_profit_and_a_loss_figure_carry_their_word_as_well_as_their_colour():
    fig = D.Figures((D.Figure("Net", D.Money(500)), D.Figure("Net", D.Money(-500))))
    lines = "\n".join(read_pdf(pdf.render_pdf(_doc(fig))).pages[0].lines)
    assert "+₹5.00" in lines and "−₹5.00" in lines
    assert "PROFIT" in lines and "LOSS" in lines


def test_every_chip_word_is_a_word_not_an_icon():
    for word in style.CHIP_FILL:
        assert word.isascii() and word.isalpha() and word.isupper()
    with pytest.raises(ValueError):
        D.Chip("ARMED")  # type: ignore[arg-type]


def test_a_long_cell_is_cut_with_dots_and_never_runs_over_its_column():
    long = "X" * 200
    table = D.Table((D.Column("Name", 1), D.Column("Qty", 1, "r")), ((long, "5"),))
    page = read_pdf(pdf.render_pdf(_doc(table))).pages[0]
    cell = next(t for t in page.texts if t.string.startswith("XXX"))
    assert cell.string.endswith("...") and len(cell.string) < 100


def test_an_empty_table_says_so():
    table = D.Table(COLUMNS, ())
    lines = "\n".join(read_pdf(pdf.render_pdf(_doc(table))).pages[0].lines)
    assert "Nothing to show for this period." in lines


def test_a_table_that_runs_over_a_page_repeats_its_header_and_keeps_every_row():
    parsed = read_pdf(pdf.render_pdf(monthly(rows=90)))
    assert len(parsed.pages) >= 3
    for page in parsed.pages[:-1]:
        assert any(t.string == "Symbol" for t in page.texts)
    rows = sum(1 for p in parsed.pages for t in p.texts if t.string in ("LONG", "SHORT"))
    assert rows == 90


def test_a_picture_is_placed_with_its_caption():
    pic = D.Picture(
        png.render_equity_png(week_equity(), "Week", "Mon 2 Nov"), "Equity curve, last −₹699.44"
    )
    parsed = read_pdf(pdf.render_pdf(_doc(pic)))
    assert "Equity curve, last −₹699.44" in "\n".join(parsed.pages[0].lines)
    assert b"/Subtype /Image" in b"".join(parsed.objects.values())


def test_the_file_name_is_ascii_and_follows_the_design():
    assert D.pdf_file_name("monthly", "202610") == "SUDARSHAN_Monthly-statement_202610.pdf"
    assert monthly().file_name == "SUDARSHAN_Monthly-statement_202610.pdf"
    for kind in style.FILE_KIND:
        name = D.pdf_file_name(kind, "FY2627")
        assert (
            name.isascii()
            and " " not in name
            and re.fullmatch(r"SUDARSHAN_[A-Za-z-]+_[A-Za-z0-9-]+\.pdf", name)
        )
    for bad in ("", "2026 10", "सु", "../x"):
        with pytest.raises(ValueError):
            D.pdf_file_name("monthly", bad)
    with pytest.raises(ValueError):
        D.pdf_file_name("daily", "1")


def test_a_row_with_the_wrong_number_of_cells_is_refused():
    with pytest.raises(ValueError):
        D.Table(COLUMNS, (("only", "two"),))


def test_figures_hold_one_to_four():
    with pytest.raises(ValueError):
        D.Figures(())
    with pytest.raises(ValueError):
        D.Figures(tuple(D.Figure("x", "1") for _ in range(5)))


def test_the_trade_rows_helper_is_synthetic_and_signed():
    assert all(isinstance(r[4], D.Money) for r in trade_rows(5))


def _soft_timing_ok() -> bool:
    """The timing assertions need an idle box of the speed they were measured on."""
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        return False
    return os.getloadavg()[0] < (os.cpu_count() or 1)


def test_a_report_pdf_builds_in_under_200_ms():
    report = monthly(rows=14)
    pdf.render_pdf(report)  # warm: fonts parsed, imports done
    best = min(_time(lambda: pdf.render_pdf(report)) for _ in range(9))
    if not _soft_timing_ok():
        pytest.skip(f"timing not asserted on a shared or CI machine (best {best * 1000:.0f} ms)")
    assert best < 0.2, f"{best * 1000:.0f} ms"


def _time(fn) -> float:
    t = time.perf_counter()
    fn()
    return time.perf_counter() - t
