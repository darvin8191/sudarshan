"""CSV text and the cells in it (M7.22).

A file of entries is read as a table: the first row names the columns, every later row is a record
that keeps its number in the file (the header is row 1, as a spreadsheet counts), and every cell is
plain text that a format then reads as a date, an amount or a name.

* Read: a leading byte-order mark, any of the three line endings, quoted cells with commas, quotes
  and line breaks inside, spaces after a comma, and blank rows (which keep their number, and are
  skipped) all pass. A
  column name is read without regard to case, with spaces and hyphens as underscores. A name that
  appears twice, a quote that never closes and a file with no header refuse the whole file; a row
  with more cells than the header has is the row's own problem (a note with a comma and no quotes
  is the usual cause), told by the caller.
* Formulas: a cell that starts with = + - @, a tab or a carriage return is a formula to a
  spreadsheet. The platform writes such a cell with one apostrophe in front (``guard``) and reads it
  back without (``unguard``); the two undo each other for every text, and a cell of any other kind
  is written and read as it is.
* Numbers are plain: digits and one decimal point, no sign, no thousands commas, no currency sign.
  They are read as whole numbers in the unit asked for (``scaled``), never as floats, and a
  number with more decimals than the unit holds is refused rather than rounded.
* Days are written YYYY-MM-DD, DD-MM-YYYY or DD/MM/YYYY, day first, never month first.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Sequence
from typing import NamedTuple

from ..core import words
from ..core.days import is_day

#: Some spreadsheets put this at the start of a UTF-8 file.
BOM = chr(0xFEFF)
#: A cell longer than this is no number; it also keeps ``int`` from digesting a book.
MAX_NUMBER_CHARS = 30
_PLAIN = re.compile(r"[0-9]+(?:\.[0-9]*)?|\.[0-9]+")
_ISO_DAY = re.compile(r"([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})")
_DAY_FIRST = re.compile(r"([0-9]{1,2})([-/])([0-9]{1,2})\2([0-9]{4})")
_RATIO = re.compile(r"([0-9]{1,6})(?:\s*[:/]\s*([0-9]{1,6}))?")
#: What a spreadsheet reads as the start of a formula, behind any apostrophes already there.
_FORMULA = re.compile(r"'*[=+\-@\t\r]")


class BadFile(ValueError):
    """A file that cannot be used at all; the message is a sentence for the owner."""


class Unreadable(ValueError):
    """A cell that is not the number asked for; ``kind`` is number, negative or decimals."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


class Record(NamedTuple):
    """One row of the file: its number, its cells by column name (empty ones are "") and whether
    it has more cells than the header has columns."""

    number: int
    cells: dict[str, str]
    extra: bool


class Table(NamedTuple):
    header: tuple[str, ...]
    records: tuple[Record, ...]


def guard(cell: str) -> str:
    """The cell as written to a file: a formula gets an apostrophe in front, which makes it text."""
    return "'" + cell if _FORMULA.match(cell) else cell


def unguard(cell: str) -> str:
    """The cell as read from a file: one apostrophe in front of a formula is taken off."""
    return cell[1:] if cell.startswith("'") and _FORMULA.match(cell) else cell


def column_name(cell: str) -> str:
    return re.sub(r"[\s\-]+", "_", cell.strip().lower())


def read_table(text: str) -> Table:
    """The table in CSV text; ``BadFile`` when there is none."""
    if "\x00" in text:
        raise BadFile(words.IMPORT_UNREADABLE)
    try:
        source = io.StringIO(text.removeprefix(BOM), newline="")
        grid = list(csv.reader(source, strict=True, skipinitialspace=True))
    except csv.Error as error:
        raise BadFile(words.IMPORT_UNREADABLE) from error
    numbered = iter(enumerate(grid, start=1))
    header = None
    for _, cells in numbered:
        if any(cell.strip() for cell in cells):
            header = [column_name(unguard(cell)) for cell in cells]
            break
    if header is None:
        raise BadFile(words.IMPORT_EMPTY)
    named = [name for name in header if name]
    for name in named:
        if named.count(name) > 1:
            raise BadFile(words.import_column_twice(name))
    records = []
    for number, cells in numbered:
        clean = [unguard(cell).strip() for cell in cells]
        if any(clean):
            values = dict.fromkeys(named, "")
            values.update((name, cell) for name, cell in zip(header, clean, strict=False) if name)
            records.append(Record(number, values, any(clean[len(header) :])))
    return Table(tuple(named), tuple(records))


def write_table(header: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    """CSV text: the header, then each row with its cells guarded; cells that need quotes get them
    and lines end as RFC 4180 says."""
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([guard(cell) for cell in row])
    return out.getvalue()


def scaled(text: str, decimals: int) -> int:
    """A plain number as a whole number of 10^-decimals ("1428.25", 2 is 142825).

    Raises ``Unreadable``: ``number`` for text that is not one, ``negative`` for a minus sign and
    ``decimals`` for more decimals than the unit holds (zeros at the end do not count).
    """
    text = text.strip()
    if len(text) > MAX_NUMBER_CHARS or not _PLAIN.fullmatch(text):
        if text.startswith("-") and len(text) <= MAX_NUMBER_CHARS and _PLAIN.fullmatch(text[1:]):
            raise Unreadable("negative")
        raise Unreadable("number")
    whole, _, fraction = text.partition(".")
    fraction = fraction.rstrip("0")
    if len(fraction) > decimals:
        raise Unreadable("decimals")
    return int(whole or "0") * 10**decimals + int(fraction.ljust(decimals, "0") or "0")


def parse_day(text: str) -> int | None:
    """YYYY-MM-DD, DD-MM-YYYY or DD/MM/YYYY as YYYYMMDD; None unless it is a real day."""
    text = text.strip()
    if found := _ISO_DAY.fullmatch(text):
        year, month, number = (int(part) for part in found.groups())
    elif found := _DAY_FIRST.fullmatch(text):
        number, month, year = int(found[1]), int(found[3]), int(found[4])
    else:
        return None
    day = year * 10000 + month * 100 + number
    return day if is_day(day) else None


def parse_ratio(text: str) -> tuple[int, int] | None:
    """A split's ratio, "5:1" or "5/1" or "5" (each becomes 5), as (new, old); None if it is not
    two whole numbers. Whether they make a split is the entry's check."""
    found = _RATIO.fullmatch(text.strip())
    if not found:
        return None
    return int(found[1]), int(found[2]) if found[2] is not None else 1
