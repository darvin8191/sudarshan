"""A tab's view on paper (M7.40; phase P7, lane X2), printed by the journal's report renderer.

``doc_of`` turns a sheet (export.py) into the renderer's blocks and ``pdf_file`` prints it, so
the page carries the report header every SUDARSHAN report has: the wordmark outline beside
SUDARSHAN, light look. Nothing of the renderer is changed; this module only imports it
(``journal/render/doc.py`` and ``pdf.py``).

Never colour alone: a gain or a loss is printed with its sign and has a filled box beside it
with its word (PROFIT, LOSS, FLAT); every other sum of money is plain ink, with a true minus
when it is below zero. A percentage is printed to two decimals; the CSV keeps all four. A
table's Note column is printed as whole sentences under the table, never cut to a cell's width.
The renderer cuts a cell that is wider than its column and ends it with "...": the columns
here are sized so that a sum up to about Rs 99 crore and a quantity of ten digits are printed
whole, and a long name is what gives way.
What the vendored fonts cannot draw (a tab named in another script) is the renderer's ``?``,
so a tab whose English name cannot be drawn is headed "Tab <id>".
"""

from __future__ import annotations

import base64
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sudarshan.journal.render import doc as D
from sudarshan.journal.render import fonts

from . import export
from .core import words as W
from .core.days import day_of_instant

MINUS = "−"
#: The renderer's ReportDoc asks for one of the journal's report kinds, which it uses only for
#: the journal's own file names; an export is named by export.file_name and never by this.
DOC_KIND = "monthly"
RESULT = D.Column("Result", 0.8, "c")
NOTE = "Note"


def _rupees(paise: int) -> str:
    return (MINUS if paise < 0 else "") + W.rupees(abs(paise))


def _pct(cell: export.Pct) -> str:
    value = Decimal(cell.text).quantize(Decimal("0.01"), ROUND_HALF_UP)
    sign = MINUS if value < 0 else "+" if cell.signed and value > 0 else ""
    return f"{sign}{abs(value)}%"


def text_of(cell: export.Cell) -> str:
    """A cell as paper writes it; a gain keeps its sign here too (a figure's label, a note)."""
    if cell is None or (not isinstance(cell, str) and cell[0] is None):
        return ""
    if isinstance(cell, str):
        return cell
    if isinstance(cell, export.Gain):
        return D.Money(cell.paise).text
    if isinstance(cell, export.Amount):
        return _rupees(cell.paise)
    if isinstance(cell, export.Qty):
        return W.quantity(cell.qty_u)
    if isinstance(cell, export.Price):
        return W.price(cell.price_u)
    if isinstance(cell, export.Pct):
        return _pct(cell)
    return W.date(cell.day)


def _title(text: str) -> str:
    return text.removesuffix(" %")


def _cells(row: tuple, keep: list[int], chips: set[int], bold: bool = False) -> tuple:
    out: list[D.Cell] = []
    for at in keep:
        cell = row[at]
        if at == 0 and isinstance(cell, str):
            cell = _title(cell)  # "XIRR %" is a row's label too; its value says the percent
        gain = isinstance(cell, export.Gain) and cell.paise is not None
        out.append(D.Money(cell.paise) if gain else D.Ink(text_of(cell), bold))
        if at in chips:
            out.append(D.Chip(D.Money(cell.paise).result) if gain else "")
    return tuple(out)


def _table(section: export.Section) -> list[D.Block]:
    """The section's table, and under it the sentences of its Note column."""
    cols = section.cols
    keep = [i for i, col in enumerate(cols) if col.paper and col.title != NOTE]
    every = [*section.rows, *([section.total] if section.total else [])]
    chips = {i for i in keep if any(isinstance(row[i], export.Gain) for row in every)}
    columns: list[D.Column] = []
    for at in keep:
        columns.append(D.Column(_title(cols[at].title), cols[at].share, cols[at].align))
        if at in chips:
            columns.append(RESULT)
    blocks: list[D.Block] = [
        D.Heading(section.title),
        D.Table(
            tuple(columns),
            tuple(_cells(row, keep, chips) for row in section.rows),
            _cells(section.total, keep, chips, True) if section.total else None,
            section.empty,
        ),
    ]
    said: list[str] = []
    for at in (i for i, col in enumerate(cols) if col.paper and col.title == NOTE):
        for row in section.rows:
            line = f"{_title(text_of(row[0]))}: {row[at]}"
            if row[at] and line not in said:
                said.append(line)
    if said:
        blocks.append(D.Paragraph(tuple(said)))
    return blocks


def _figure(label: str, cell: export.Cell) -> D.Figure:
    if isinstance(cell, export.Gain) and cell.paise is not None:
        return D.Figure(_title(label), D.Money(cell.paise))
    return D.Figure(_title(label), text_of(cell) or export.WORDS["not_yet"])


def doc_of(sheet: export.Sheet) -> D.ReportDoc:
    tab = sheet.tab
    name = tab["name"] if fonts.can_draw(tab["name"], "barlow", "noto") else f"Tab {tab['id']}"
    blocks: list[D.Block] = []
    if sheet.figures:
        blocks.append(D.Figures(tuple(_figure(*each) for each in sheet.figures[:4])))
    for section in sheet.sections:
        if section.on != "csv":
            blocks += _table(section)
    if sheet.notes:
        blocks += [D.Heading("Notes"), D.Paragraph(sheet.notes)]
    when = sheet.as_of
    return D.ReportDoc(
        kind=DOC_KIND,
        period_key=str(day_of_instant(sheet.at)),
        title=name,
        period_label=f"{sheet.title} · {when[0].lower()}{when[1:]}",
        built_label=f"Exported from SUDARSHAN. {when}.",
        blocks=tuple(blocks),
    )


def pdf_bytes(sheet: export.Sheet) -> bytes:
    from sudarshan.journal.render.pdf import render_pdf  # fpdf2 is loaded on the first export

    return render_pdf(doc_of(sheet))


def pdf_file(sheet: export.Sheet) -> dict[str, Any]:
    """types.ts ``ExportPdf``: the answer is JSON, so the file travels as base64."""
    return {
        "filename": export.file_name(sheet.tab, sheet.view, sheet.at, "pdf"),
        "base64": base64.b64encode(pdf_bytes(sheet)).decode("ascii"),
    }
