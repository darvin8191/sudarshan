"""The vendored fonts load, carry the licence, and never fall back silently (JB8; Y39)."""

from __future__ import annotations

import shutil

import pytest
from PIL import ImageFont
from render_pdfread import read_pdf
from render_samples import monthly

from sudarshan.journal.render import fonts, pdf


def test_every_vendored_font_is_there_and_loads():
    fonts.verify()
    for key, name in fonts.FONTS.items():
        path = fonts.font_path(key)
        assert path.parent == fonts.FONT_DIR and path.name == name
        assert path.read_bytes()[:4] in (b"\x00\x01\x00\x00", b"true", b"OTTO")
        ImageFont.truetype(str(path), 20)  # Pillow opens the same file


def test_each_family_has_its_licence_beside_it():
    for family, licence in fonts.LICENCES.items():
        text = (fonts.FONT_DIR / licence).read_text(encoding="utf-8")
        assert "SIL OPEN FONT LICENSE Version 1.1" in text, family
        assert "Copyright" in text


def test_the_rupee_sign_and_the_true_minus_are_in_the_fonts_that_print_money():
    for key in fonts.NEED_RUPEE:
        assert ord(fonts.RUPEE) in fonts.coverage(key), key
        assert ord(fonts.MINUS) in fonts.coverage(key), key


def test_barlow_has_no_rupee_so_the_fallback_is_what_draws_it():
    assert ord(fonts.RUPEE) not in fonts.coverage("barlow")


def test_the_pdf_embeds_only_the_vendored_families():
    names = read_pdf(pdf.render_pdf(monthly())).base_fonts
    families = {n.split("+", 1)[-1] for n in names}
    assert families == {"Barlow", "BarlowBold", "BarlowSemiBold", "KhandSemiBold", "NotoSans"} | (
        {"NotoSansBold"} & families
    )
    assert "NotoSans" in families and "KhandSemiBold" in families and "Barlow" in families


def test_a_missing_font_fails_loudly(tmp_path, monkeypatch):
    copy = tmp_path / "fonts"
    shutil.copytree(fonts.FONT_DIR, copy)
    (copy / "Khand-SemiBold.ttf").unlink()
    monkeypatch.setattr(fonts, "FONT_DIR", copy)
    with pytest.raises(fonts.FontError, match="Khand-SemiBold.ttf"):
        fonts.verify()
    with pytest.raises(fonts.FontError):
        pdf.render_pdf(monthly())


def test_a_missing_licence_fails_loudly(tmp_path, monkeypatch):
    copy = tmp_path / "fonts"
    shutil.copytree(fonts.FONT_DIR, copy)
    (copy / "OFL-Barlow.txt").unlink()
    monkeypatch.setattr(fonts, "FONT_DIR", copy)
    with pytest.raises(fonts.FontError, match="licence"):
        fonts.verify()


def test_a_file_that_is_not_a_font_fails_loudly(tmp_path, monkeypatch):
    copy = tmp_path / "fonts"
    shutil.copytree(fonts.FONT_DIR, copy)
    (copy / "NotoSans-Regular.ttf").write_bytes(b"not a font" * 400)
    monkeypatch.setattr(fonts, "FONT_DIR", copy)
    with pytest.raises(fonts.FontError):
        fonts.verify()


def test_an_unknown_font_key_is_refused():
    with pytest.raises(fonts.FontError):
        fonts.font_path("comic")


def test_text_the_fonts_cannot_draw_is_a_question_mark_never_a_gap():
    assert pdf.clean("Plain +₹ 1,000") == "Plain +₹ 1,000"
    assert pdf.clean("सु") == "??"
    assert pdf.clean("a\x07b") == "ab"
