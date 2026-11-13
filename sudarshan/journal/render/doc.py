"""What a PDF is made of (JB8): a small, closed set of blocks.

The report builders (JB9) describe a report with these and ``pdf.render_pdf`` prints it. Money
is held in paise and printed by this module, so a money cell can never lose its sign: a profit
is ``+₹1,23,456.00``, a loss ``−₹1,23,456.00`` (true minus), and only exactly zero is plain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sudarshan.journal.core import words
from sudarshan.journal.render import style

ChipWord = Literal["PROFIT", "LOSS", "FLAT", "LONG", "SHORT"]
Align = Literal["l", "r", "c"]


@dataclass(frozen=True)
class Money:
    """A money cell in paise; always printed with its sign."""

    paise: int

    @property
    def text(self) -> str:
        return words.rupees(self.paise, signed=True)

    @property
    def result(self) -> ChipWord:
        return "PROFIT" if self.paise > 0 else "LOSS" if self.paise < 0 else "FLAT"


@dataclass(frozen=True)
class Chip:
    """A filled box with its word inside."""

    word: ChipWord

    def __post_init__(self) -> None:
        if self.word not in style.CHIP_FILL:
            raise ValueError(f"no chip called {self.word!r}")


@dataclass(frozen=True)
class Ink:
    """Plain text; ``bold`` for a total or a label."""

    text: str
    bold: bool = False


Cell = str | Money | Chip | Ink


@dataclass(frozen=True)
class Column:
    """One table column: its title, its share of the width and its alignment."""

    title: str
    share: float = 1.0
    align: Align = "l"


@dataclass(frozen=True)
class Heading:
    text: str


@dataclass(frozen=True)
class Paragraph:
    """Lines of text, one per line, as the words module returns them."""

    lines: tuple[str, ...]

    @classmethod
    def from_words(cls, text: str) -> Paragraph:
        """The report's words without the brand line, which the page header already carries."""
        lines = text.split("\n")
        if lines and lines[0] == words.HEADER:
            lines = lines[1:]
        return cls(tuple(lines))


@dataclass(frozen=True)
class Figure:
    """One big figure with its label: a sum of money (with a chip) or a ready text."""

    label: str
    value: Money | str


@dataclass(frozen=True)
class Figures:
    """A row of up to four figures."""

    items: tuple[Figure, ...]

    def __post_init__(self) -> None:
        if not 1 <= len(self.items) <= 4:
            raise ValueError("a row of figures holds one to four figures")


@dataclass(frozen=True)
class Table:
    columns: tuple[Column, ...]
    rows: tuple[tuple[Cell, ...], ...]
    total: tuple[Cell, ...] | None = None
    empty: str = "Nothing to show for this period."

    def __post_init__(self) -> None:
        n = len(self.columns)
        for row in (*self.rows, *([self.total] if self.total else [])):
            if len(row) != n:
                raise ValueError(f"a row has {len(row)} cells for {n} columns")


@dataclass(frozen=True)
class Note:
    """A boxed note on a gold tint (the working-paper line of the tax pack)."""

    text: str


@dataclass(frozen=True)
class Picture:
    """A PNG with a caption in words (the chart's figures are also in the caption)."""

    png: bytes
    caption: str
    width_mm: float = 182.0


Block = Heading | Paragraph | Figures | Table | Note | Picture


@dataclass(frozen=True)
class ReportDoc:
    """A report ready to print."""

    kind: str  # a ReportKind
    period_key: str  # YYYYMMDD, YYYYWww, YYYYMM or FY2627
    title: str  # e.g. "Monthly statement"
    period_label: str  # e.g. "October 2026"
    built_label: str  # e.g. "Built Sun 1 Nov 2026, 07:00" (the caller owns the clock)
    blocks: tuple[Block, ...] = field(default_factory=tuple)
    footer_note: str = ""

    def __post_init__(self) -> None:
        if self.kind not in style.FILE_KIND:
            raise ValueError(f"no report kind {self.kind!r}")

    @property
    def file_name(self) -> str:
        return pdf_file_name(self.kind, self.period_key)


def pdf_file_name(kind: str, period_key: str, ext: str = "pdf") -> str:
    """``SUDARSHAN_Monthly-statement_202610.pdf``: ASCII, no spaces, the download's name."""
    if kind not in style.FILE_KIND:
        raise ValueError(f"no report kind {kind!r}")
    if not period_key or not period_key.isascii() or not period_key.replace("-", "").isalnum():
        raise ValueError(f"a period key is ASCII letters, digits and hyphens: {period_key!r}")
    return f"{style.BRAND_WORD}_{style.FILE_KIND[kind]}_{period_key}.{ext}"
