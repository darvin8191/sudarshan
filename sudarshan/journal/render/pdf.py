"""The report PDF (JB8, M15.6): fpdf2, vendored fonts, no browser.

What a v1 PDF holds is Latin text, the rupee sign and the wordmark outline, and nothing else
(cross-design Y39: Indic text needs shaping and the build has none). A character outside the
vendored fonts, a strategy name in Devanagari for one, is printed as ``?``, never as a gap or a
different font, and the page header carries सुदर्शन as outlines, the same ones the app draws.

Never colour alone (design 6.5): every chip is a filled box with its word inside, every money
cell carries its sign, a profit or loss figure is a signed number as well as a colour.
"""

from __future__ import annotations

import io
import re
from collections.abc import Sequence

from fpdf import FPDF
from fpdf.drawing import DeviceRGB

from sudarshan.journal.render import doc as D
from sudarshan.journal.render import fonts, paths, style

PAGE_W = 210.0
PAGE_H = 297.0
MARGIN = 14.0
CONTENT_W = PAGE_W - 2 * MARGIN
TOP = 31.0
BOTTOM = PAGE_H - 17.0
ROW_H = 6.4

# Family names registered with fpdf2. Barlow SemiBold is its own family because fpdf2 knows
# only regular and bold per family.
BODY = "Barlow"
SEMI = "BarlowSemi"
TITLE = "Khand"
NUM = "Noto"

_MARK_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _rgb(c: style.RGB) -> DeviceRGB:
    return DeviceRGB(c[0] / 255, c[1] / 255, c[2] / 255)


def clean(text: str) -> str:
    """Text the vendored fonts can draw: what they lack becomes ``?`` (Y39), controls go."""
    text = _MARK_RE.sub("", text.replace("\t", " "))
    if fonts.can_draw(text, "barlow", "noto"):
        return text
    return "".join(c if fonts.can_draw(c, "barlow", "noto") else "?" for c in text)


class _Report(FPDF):
    def __init__(self, report: D.ReportDoc) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self.report = report
        self.set_auto_page_break(False)
        self.set_margins(MARGIN, TOP, MARGIN)
        self.set_creator("SUDARSHAN")
        self.set_producer("SUDARSHAN journal renderer")
        self.set_title(f"{style.BRAND_WORD} - {report.title} - {report.period_label}")
        self.set_author("SUDARSHAN")
        self._register_fonts()

    def _register_fonts(self) -> None:
        fonts.verify()
        for family, face, key in (
            (BODY, "", "barlow"),
            (BODY, "B", "barlow_bold"),
            (SEMI, "", "barlow_semibold"),
            (TITLE, "", "khand_semibold"),
            (NUM, "", "noto"),
            (NUM, "B", "noto_bold"),
        ):
            self.add_font(family, face, str(fonts.font_path(key)))
        # Barlow has no rupee sign: a glyph it lacks is drawn from Noto Sans in the same style.
        self.set_fallback_fonts([NUM], exact_match=True)

    # ---- page furniture ------------------------------------------------------------------
    def header(self) -> None:
        r = self.report
        mark_h = 7.6
        (vw, vh), cmds = paths.wordmark()
        scale = mark_h / vh
        mark_w = vw * scale
        top = 10.0
        self._draw_path(paths.transform(cmds, scale, MARGIN, top), style.INK, stroke=0.12)
        # SUDARSHAN beside it, Khand 600, tracked 0.4 em, in the brand colour.
        size = 15.0
        self.set_font(TITLE, "", size)
        self.set_text_color(*style.BRAND)
        self.set_char_spacing(size * style.TRACKING_EM)
        self.text(MARGIN + mark_w + 3.2, top + mark_h - 0.4, style.BRAND_WORD)
        self.set_char_spacing(0)
        # The report's name and period, right-aligned.
        self.set_text_color(*style.INK)
        self.set_font(SEMI, "", 11)
        self._right(PAGE_W - MARGIN, top + 3.2, clean(r.title))
        self.set_text_color(*style.INK_2)
        self.set_font(BODY, "", 9)
        self._right(PAGE_W - MARGIN, top + 7.8, clean(r.period_label))
        # A gold run and a divider: decoration only.
        self.set_fill_color(*style.DIVIDER)
        self.rect(MARGIN, 21.2, CONTENT_W, 0.3, style="F")
        self.set_fill_color(*style.GOLD)
        self.rect(MARGIN, 21.0, 22.0, 0.7, style="F")
        self.set_y(TOP)

    def footer(self) -> None:
        """Drawn after the body, once the page count is known (``stamp_footers``)."""

    def stamp_footers(self) -> None:
        total = self.pages_count
        for number in range(1, total + 1):
            self.page = number
            self._footer(number, total)

    def _footer(self, number: int, total: int) -> None:
        y = PAGE_H - 11.0
        self.set_fill_color(*style.DIVIDER)
        self.rect(MARGIN, y - 4.2, CONTENT_W, 0.3, style="F")
        self.set_text_color(*style.INK_3)
        # A page stamped after the body has its own font selection in its stream: ask for a
        # different size first so fpdf2 writes the font change for this page.
        self.set_font(BODY, "", 7.4)
        self.set_font(BODY, "", 7.5)
        left = clean(self.report.footer_note or self.report.built_label)
        self.text(MARGIN, y, left)
        self._right(PAGE_W - MARGIN, y, f"Page {number} of {total}")
        if self.report.footer_note and self.report.built_label:
            self.text(MARGIN, y + 3.6, clean(self.report.built_label))

    # ---- small helpers -------------------------------------------------------------------
    def _right(self, x: float, y: float, text: str) -> None:
        self.text(x - self.get_string_width(text), y, text)

    def _draw_path(self, cmds: Sequence[tuple], fill: style.RGB, stroke: float = 0.0) -> None:
        with self.new_path() as p:
            for c in cmds:
                if c[0] == "M":
                    p.move_to(c[1], c[2])
                elif c[0] == "L":
                    p.line_to(c[1], c[2])
                elif c[0] == "C":
                    p.curve_to(*c[1:])
                else:
                    p.close()
            p.style.fill_color = _rgb(fill)
            if stroke:
                p.style.stroke_color = _rgb(fill)
                p.style.stroke_width = stroke
            else:
                p.style.stroke_color = None

    def _fit(self, text: str, width: float) -> str:
        """The text cut with an ellipsis so it fits ``width`` in the current font."""
        text = clean(text)
        if self.get_string_width(text) <= width:
            return text
        while text and self.get_string_width(text + "...") > width:
            text = text[:-1]
        return text.rstrip() + "..."

    def ensure(self, height: float) -> None:
        if self.get_y() + height > BOTTOM:
            self.add_page()

    # ---- blocks --------------------------------------------------------------------------
    def heading(self, b: D.Heading) -> None:
        self.ensure(34)  # the heading never stands alone at the foot of a page
        self.set_y(self.get_y() + 3)
        self.set_font(TITLE, "", 15)
        self.set_text_color(*style.BRAND)
        y = self.get_y()
        self.text(MARGIN, y + 5.5, clean(b.text))
        self.set_y(y + 8.5)

    def paragraph(self, b: D.Paragraph) -> None:
        self.set_font(BODY, "", 9.5)
        self.set_text_color(*style.INK)
        for line in b.lines:
            line = clean(line)
            if not line:
                self.set_y(self.get_y() + 2)
                continue
            n = len(
                self.multi_cell(CONTENT_W, 4.9, line, dry_run=True, output="LINES", wrapmode="WORD")
            )
            self.ensure(4.9 * n)
            self.set_x(MARGIN)
            self.multi_cell(CONTENT_W, 4.9, line, new_x="LMARGIN", new_y="NEXT", wrapmode="WORD")
        self.set_y(self.get_y() + 1.5)

    def chip(self, word: str, x: float, y: float, h: float = 4.2, size: float = 6.6) -> float:
        """A filled box with its word inside; returns its width."""
        self.set_font(SEMI, "", size)
        w = self.get_string_width(word) + 3.4
        self.set_fill_color(*style.CHIP_FILL[word])
        self.rect(x, y, w, h, style="F")
        self.set_text_color(*style.WHITE)
        self.text(x + 1.7, y + h - 1.25, word)
        return w

    def figures(self, b: D.Figures) -> None:
        n = len(b.items)
        gap = 3.0
        w = (CONTENT_W - gap * (n - 1)) / n
        h = 21.0
        self.ensure(h + 3)
        y = self.get_y() + 1
        for i, fig in enumerate(b.items):
            x = MARGIN + i * (w + gap)
            self.set_fill_color(*style.CARD)
            self.set_draw_color(*style.BORDER)
            self.set_line_width(0.25)
            self.rect(x, y, w, h, style="DF", round_corners=True, corner_radius=2.2)
            self.set_font(BODY, "", 8)
            self.set_text_color(*style.INK_2)
            self.text(x + 3, y + 5.6, self._fit(fig.label, w - 6))
            if isinstance(fig.value, D.Money):
                m = fig.value
                colour = {"PROFIT": style.PROFIT, "LOSS": style.LOSS, "FLAT": style.INK}[m.result]
                size = 17.0
                self.set_font(TITLE, "", size)
                while self.get_string_width(m.text) > w - 6 and size > 9:
                    size -= 1
                    self.set_font(TITLE, "", size)
                self.set_text_color(*colour)
                self.text(x + 3, y + 14.0, m.text)
                self.chip(m.result, x + 3, y + h - 6.0)
            else:
                size = 17.0
                text = clean(fig.value)
                self.set_font(TITLE, "", size)
                while self.get_string_width(text) > w - 6 and size > 9:
                    size -= 1
                    self.set_font(TITLE, "", size)
                self.set_text_color(*style.INK)
                self.text(x + 3, y + 14.0, text)
        self.set_y(y + h + 3)

    def note(self, b: D.Note) -> None:
        self.set_font(SEMI, "", 9)
        lines = self.multi_cell(
            CONTENT_W - 8, 4.8, clean(b.text), dry_run=True, output="LINES", wrapmode="WORD"
        )
        h = 4.8 * len(lines) + 5
        self.ensure(h + 3)
        y = self.get_y() + 1
        self.set_fill_color(*style.GOLD_TINT)
        self.rect(MARGIN, y, CONTENT_W, h, style="F")
        self.set_fill_color(*style.GOLD)
        self.rect(MARGIN, y, 1.2, h, style="F")
        self.set_text_color(*style.WARNING_TEXT)
        self.set_xy(MARGIN + 5, y + 2.5)
        self.multi_cell(CONTENT_W - 8, 4.8, clean(b.text), new_x="LMARGIN", new_y="NEXT")
        self.set_y(y + h + 3)

    def picture(self, b: D.Picture) -> None:
        from PIL import Image

        with Image.open(io.BytesIO(b.png)) as im:
            ratio = im.height / im.width
        w = min(b.width_mm, CONTENT_W)
        h = w * ratio
        self.ensure(h + 10)
        y = self.get_y() + 1
        self.image(io.BytesIO(b.png), x=MARGIN, y=y, w=w, h=h)
        self.set_y(y + h + 1.5)
        self.set_font(BODY, "", 8.5)
        self.set_text_color(*style.INK_2)
        self.set_x(MARGIN)
        self.multi_cell(CONTENT_W, 4.4, clean(b.caption), new_x="LMARGIN", new_y="NEXT")
        self.set_y(self.get_y() + 2)

    def _cell(self, cell: D.Cell, x: float, y: float, w: float, align: str, bold: bool) -> None:
        pad = 1.8
        base = y + ROW_H - 1.9
        if isinstance(cell, D.Money):
            colour = {"PROFIT": style.PROFIT, "LOSS": style.LOSS, "FLAT": style.INK}[cell.result]
            self.set_font(NUM, "B" if bold else "", 8)
            self.set_text_color(*colour)
            text = self._fit(cell.text, w - 2 * pad)
            self.text(x + w - pad - self.get_string_width(text), base, text)
        elif isinstance(cell, D.Chip):
            self.set_font(SEMI, "", 6.6)
            cw = self.get_string_width(cell.word) + 3.4
            cx = x + (w - cw) / 2 if align == "c" else x + pad
            self.chip(cell.word, cx, y + (ROW_H - 4.2) / 2)
        else:
            if isinstance(cell, D.Ink):
                text, bold = cell.text, cell.bold or bold
            else:
                text = cell
            numeric = align == "r"
            self.set_font(NUM if numeric else BODY, "B" if bold else "", 8 if numeric else 8.5)
            self.set_text_color(*style.INK)
            text = self._fit(text, w - 2 * pad)
            tw = self.get_string_width(text)
            tx = x + w - pad - tw if numeric else (x + (w - tw) / 2 if align == "c" else x + pad)
            self.text(tx, base, text)

    def table(self, b: D.Table) -> None:
        total_share = sum(c.share for c in b.columns)
        widths = [CONTENT_W * c.share / total_share for c in b.columns]

        def head(y: float) -> float:
            self.set_fill_color(*style.BRAND_TINT)
            self.rect(MARGIN, y, CONTENT_W, ROW_H, style="F")
            x = MARGIN
            for col, w in zip(b.columns, widths, strict=True):
                self.set_font(SEMI, "", 8)
                self.set_text_color(*style.BRAND)
                text = self._fit(col.title, w - 3.6)
                tw = self.get_string_width(text)
                tx = (
                    x + w - 1.8 - tw
                    if col.align == "r"
                    else (x + (w - tw) / 2 if col.align == "c" else x + 1.8)
                )
                self.text(tx, y + ROW_H - 1.9, text)
                x += w
            return y + ROW_H

        def row(y: float, cells: Sequence[D.Cell], bold: bool = False, rule: bool = True) -> float:
            x = MARGIN
            for col, w, cell in zip(b.columns, widths, cells, strict=True):
                self._cell(cell, x, y, w, col.align, bold)
                x += w
            if rule:
                self.set_fill_color(*style.DIVIDER)
                self.rect(MARGIN, y + ROW_H - 0.15, CONTENT_W, 0.3, style="F")
            return y + ROW_H

        self.ensure(ROW_H * 3)
        y = head(self.get_y() + 1)
        if not b.rows:
            self.set_font(BODY, "", 9)
            self.set_text_color(*style.INK_2)
            self.text(MARGIN + 1.8, y + ROW_H - 1.9, clean(b.empty))
            y += ROW_H
        for cells in b.rows:
            if y + ROW_H > BOTTOM:
                self.add_page()
                y = head(self.get_y())
            y = row(y, cells)
        if b.total:
            if y + ROW_H > BOTTOM:
                self.add_page()
                y = head(self.get_y())
            self.set_fill_color(*style.INK_2)
            self.rect(MARGIN, y, CONTENT_W, 0.35, style="F")
            y = row(y, b.total, bold=True, rule=False)
        self.set_y(y + 4)

    def title_block(self) -> None:
        r = self.report
        self.set_font(TITLE, "", 26)
        self.set_text_color(*style.INK)
        y = self.get_y()
        self.text(MARGIN, y + 8, clean(r.title))
        self.set_font(BODY, "", 11)
        self.set_text_color(*style.INK_2)
        self.text(MARGIN, y + 14.5, clean(r.period_label))
        self.set_y(y + 18)

    def body(self) -> None:
        self.add_page()
        self.title_block()
        for block in self.report.blocks:
            if isinstance(block, D.Heading):
                self.heading(block)
            elif isinstance(block, D.Paragraph):
                self.paragraph(block)
            elif isinstance(block, D.Figures):
                self.figures(block)
            elif isinstance(block, D.Table):
                self.table(block)
            elif isinstance(block, D.Note):
                self.note(block)
            elif isinstance(block, D.Picture):
                self.picture(block)
            else:  # pragma: no cover - the Block union is closed
                raise TypeError(f"unknown block {type(block).__name__}")


def render_pdf(report: D.ReportDoc) -> bytes:
    """The report as PDF bytes."""
    pdf = _Report(report)
    pdf.body()
    pdf.stamp_footers()
    return bytes(pdf.output())
