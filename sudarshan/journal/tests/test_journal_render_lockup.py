"""The PDF's wordmark is the outline the app draws (JB8; design 8.2 Renderer)."""

from __future__ import annotations

import pytest
from render_pdfread import read_pdf
from render_samples import monthly

from sudarshan.journal.render import doc as D
from sudarshan.journal.render import lockup_paths, paths, pdf
from sudarshan.journal.render.tools import generate_lockup_paths as gen


def _marks() -> str:
    return gen.MARKS_TS.read_text(encoding="utf-8")


def test_the_generated_file_is_what_the_generator_writes_from_marks_ts():
    assert gen.TARGET.read_text(encoding="utf-8") == gen.render_module(_marks())


def test_the_lockup_data_equals_marks_ts():
    box, d = gen.read_wordmark(_marks())
    assert lockup_paths.WORDMARK_VIEWBOX == box
    assert lockup_paths.WORDMARK_D == d
    assert box == (0, 0, 2551, 1230)


def test_the_check_mode_reports_current(capsys):
    assert gen.main(["--check"]) == 0
    assert "current" in capsys.readouterr().out


def test_a_changed_outline_makes_the_generated_text_differ():
    changed = _marks().replace("M364 951H354", "M365 951H354", 1)
    assert gen.render_module(changed) != gen.TARGET.read_text(encoding="utf-8")


def test_a_marks_file_without_a_wordmark_is_refused():
    with pytest.raises(ValueError):
        gen.read_wordmark("export const ICON = {}")


def test_a_path_command_the_reader_does_not_know_is_refused():
    with pytest.raises(ValueError):
        paths.parse("M0 0S1 1 2 2Z")
    with pytest.raises(ValueError):
        paths.parse("M0 0L1Z")


def test_the_reader_turns_the_path_into_the_same_number_of_curves_and_contours():
    d = lockup_paths.WORDMARK_D
    cmds = paths.parse(d)
    assert sum(1 for c in cmds if c[0] == "C") == d.count("Q")
    assert sum(1 for c in cmds if c[0] == "M") == d.count("M")
    assert sum(1 for c in cmds if c[0] == "Z") == d.count("Z")


def test_a_quadratic_curve_is_lifted_to_the_equal_cubic():
    cmds = paths.parse("M0 0Q30 60 60 0Z")
    assert cmds[1] == ("C", 20.0, 40.0, 40.0, 40.0, 60.0, 0.0)


def test_every_page_of_the_pdf_draws_the_wordmark_outline():
    d = lockup_paths.WORDMARK_D
    only_text = D.ReportDoc(
        "eod",
        "20261109",
        "Day report",
        "Mon 9 Nov 2026",
        "Built Mon 9 Nov 2026, 15:50",
        (D.Paragraph(("Trades 6",)),),
    )
    parsed = read_pdf(pdf.render_pdf(only_text))
    page = parsed.pages[0]
    # No other curve is drawn on this page, so these are the lockup's.
    assert page.count("c") == d.count("Q")
    assert page.count("m") == d.count("M")
    assert page.count("h") == d.count("Z")

    long = read_pdf(pdf.render_pdf(monthly(rows=80)))
    assert len(long.pages) > 1
    for p in long.pages:
        assert p.count("m") >= d.count("M")
        assert "SUDARSHAN" in p.text


def test_the_outline_lands_where_the_lockup_is_set():
    # The path's extent on the page matches the viewBox ratio.
    (vw, vh), cmds = paths.wordmark()
    xs = [v for c in cmds if c[0] != "Z" for v in c[1::2]]
    ys = [v for c in cmds if c[0] != "Z" for v in c[2::2]]
    assert 0 <= min(xs) and max(xs) <= vw
    assert 0 <= min(ys) and max(ys) <= vh
