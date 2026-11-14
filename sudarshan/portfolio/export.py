"""A tab's exports, one file per view (M7.40; phase P7, lane X).

Nothing here works out a figure of its own. A sheet is made from the answers the phone is shown
(``Views.tab_view`` and ``Views.performance``, whole tab, no saved filter) and from the core's
own sums for one account's line of a holding (``valuation.line_value``, ``money.pct``,
``money.unit_price_u``), so a file can never say something the screen does not.

A ``Sheet`` holds typed cells, and each format writes a cell its own way:

* CSV: UTF-8 with a byte-order mark, lines ending CR LF. The first two rows are the header
  (the brand in both scripts and the view; the tab's name, its second-language name and "As of"),
  then the figures and one block per table. A number is plain (digits, a decimal point, a minus
  in front of a loss; no commas, no rupee sign), a day is YYYY-MM-DD, and any text a spreadsheet
  would run as a formula gets an apostrophe in front (``csvutil.guard``).
* The entries (view ``activity``) are the import format itself, with no header rows, so the file
  reads back into a tab as the same ledger.

File names are ASCII: ``SUDARSHAN_Portfolio_<tab-slug>_<view>_<YYYY-MM-DD>.csv``, the slug from
the tab's English name (letters, digits and hyphens, at most 24), else ``tab<id>``.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple

from .core import money, valuation
from .core import words as W
from .core.days import clock_of_instant, day_of_instant
from .core.kinds import ALLOCATION_BY, EXPORT_VIEWS
from .imports.csvutil import BOM, guard

BRAND = "SUDARSHAN / सुदर्शन"
VIEW_TITLES = {
    "holdings": "Holdings",
    "activity": "Entries",
    "performance": "Performance",
    "allocation": "Allocation",
    "income": "Income",
}
ALLOCATION_TITLES = {
    "class": "By class",
    "sector": "By sector",
    "mcap": "By cap size",
    "account": "By account",
    "member": "By family member",
    "tag": "By tag",
}
WORDS = {
    "view": "Choose what to export: holdings, entries, performance, allocation or income.",
    "upstox": (
        "The Upstox tab is read from Upstox and has no export of its own. Download its trades "
        "and its profit and loss from Reports."
    ),
    "unpriced_one": "1 holding has no price yet and is counted at its cost.",
    "unpriced": "{count} holdings have no price yet and are counted at their cost.",
    "whole_tab": "The whole tab, with no filter.",
    "not_yet": "Not shown yet",
}
SLUG_MAX = 24
_NOT_SLUG = re.compile(r"[^A-Za-z0-9]+")


# ---- cells --------------------------------------------------------------------------------------
class Amount(NamedTuple):
    """Money that is no gain or loss (a value, a cost, an income), in paise."""

    paise: int | None


class Gain(NamedTuple):
    """A gain or a loss in paise: on paper it carries its sign and its word."""

    paise: int | None


class Qty(NamedTuple):
    qty_u: int | None


class Price(NamedTuple):
    price_u: int | None


class Pct(NamedTuple):
    """A percentage as the contract writes it (four decimals); ``signed`` for a return."""

    text: str | None
    signed: bool = False


class Day(NamedTuple):
    day: int | None


Cell = str | Amount | Gain | Qty | Price | Pct | Day | None


@dataclass(frozen=True)
class Col:
    """A column: its title, its share of a page's width, its alignment, and whether paper has
    room for it (the CSV always does)."""

    title: str
    share: float = 1.0
    align: str = "r"
    paper: bool = True


@dataclass(frozen=True)
class Section:
    """One table. ``on`` says where it is written: both, csv or paper."""

    title: str
    cols: tuple[Col, ...]
    rows: tuple[tuple[Cell, ...], ...]
    total: tuple[Cell, ...] | None = None
    on: str = "both"
    empty: str = "Nothing to show."


@dataclass(frozen=True)
class Sheet:
    """One view of one tab at one instant."""

    view: str
    tab: Mapping[str, Any]
    at: int  # epoch ms: when the figures were read
    figures: tuple[tuple[str, Cell], ...]
    sections: tuple[Section, ...]
    notes: tuple[str, ...] = ()

    @property
    def title(self) -> str:
        return VIEW_TITLES[self.view]

    @property
    def as_of(self) -> str:
        return f"As of {W.date(day_of_instant(self.at))}, {clock_of_instant(self.at)[:5]} IST"


# ---- names ----------------------------------------------------------------------------------------
def view_of(value: Any) -> str:
    """The view asked for, or the sentence that names the choices."""
    if value not in EXPORT_VIEWS:
        raise ValueError(WORDS["view"])
    return value


def slug(tab: Mapping[str, Any]) -> str:
    text = _NOT_SLUG.sub("-", str(tab.get("name") or "")).strip("-")[:SLUG_MAX].strip("-")
    return text or f"tab{tab['id']}"


def file_name(tab: Mapping[str, Any], view: str, at: int, ext: str) -> str:
    day = day_of_instant(at)
    stamp = f"{day // 10000:04d}-{day // 100 % 100:02d}-{day % 100:02d}"
    return f"SUDARSHAN_Portfolio_{slug(tab)}_{view}_{stamp}.{ext}"


# ---- the sheets -----------------------------------------------------------------------------------
_LABEL, _VALUE, _NOTE = Col("Figure", 2.2, "l"), Col("Value", 1.6), Col("Note", 3.0, "l")


def holdings(tab_view: Mapping[str, Any], accounts: Mapping[int, str]) -> Sheet:
    """One row per account's line of each holding, largest value first, as the Holdings list."""
    totals, fixed = tab_view["totals"], tab_view["fixed"]
    lines = []
    for row in tab_view["rows"]:
        asset, price = row["asset"], row["price"]["price_u"]
        for raw in row["lines"]:
            line = valuation.Line.of(raw)
            if not line.qty_u:
                continue
            value = valuation.line_value(line, price)
            gain = value - line.cost_paise
            lines.append((
                asset["name"], asset["symbol"] or "", asset["exchange"] or "",
                asset["isin"] or "", asset["asset_class"] or "",
                accounts.get(line.account_id, ""), Qty(line.qty_u),
                Price(money.unit_price_u(line.cost_paise, line.qty_u)), Amount(line.cost_paise),
                Price(price), Day(row["price"]["day"]), Amount(value), Gain(gain),
                Pct(money.pct(gain, line.cost_paise) if price is not None else None, True),
                Pct(money.pct(value, totals["value_paise"])),
                row["fresh"]["words"] or "",
            ))  # fmt: skip
    lines.sort(key=lambda r: (-r[11].paise, r[0], r[5]))
    cols = (
        Col("Holding", 2.6, "l"), Col("Symbol", paper=False), Col("Exchange", paper=False),
        Col("ISIN", paper=False), Col("Class", paper=False), Col("Account", 1.4, "l"),
        Col("Quantity", 1.1), Col("Average cost", paper=False), Col("Cost", 1.5),
        Col("Price", 1.3), Col("Price date", paper=False), Col("Value", 1.5), Col("Gain", 1.5),
        Col("Gain %", paper=False), Col("Weight %", paper=False), Col("Note", paper=False),
    )  # fmt: skip
    total = ("Total", "", "", "", "", "", None, None, Amount(totals["cost_paise"]), None, None,
             Amount(totals["value_paise"]), Gain(totals["unrealised_paise"]), None, None, "")  # fmt: skip
    sums = [
        ("Realised gain", Gain(fixed["realised_paise"]), ""),
        ("Income", Amount(fixed["income_paise"]), ""),
        ("Fees", Amount(fixed["fees_paise"]), ""),
        ("Absolute return %", Pct(totals["absolute_return_pct"], True), ""),
    ]
    sections = [Section("Holdings", cols, tuple(lines), total, empty="No holdings in this tab.")]
    loans = tab_view["liabilities"]
    if loans:
        owed = fixed["liabilities_paise"]
        sums += [("Loans outstanding", Amount(owed), ""),
                 ("Value after loans", Amount(totals["value_paise"] - owed), "")]  # fmt: skip
        sections.append(Section(
            "Loans",
            (Col("Loan", 2.6, "l"), Col("Outstanding", 1.5), Col("Balance of", 1.3),
             Col("Note", 3.0, "l")),
            tuple((each["name"], Amount(each["outstanding_paise"]), Day(each["day"]),
                   each["words"] or "") for each in loans),
        ))  # fmt: skip
    sections.insert(1, Section("Totals", (_LABEL, _VALUE, _NOTE), tuple(sums)))
    notes = [WORDS["whole_tab"]]
    if totals["unpriced"]:
        many = WORDS["unpriced"].format(count=totals["unpriced"])
        notes.append(WORDS["unpriced_one"] if totals["unpriced"] == 1 else many)
    notes += [f"{note['name']}: {note['words']}" for note in tab_view["notes"]]
    figures = (
        ("Value", Amount(totals["value_paise"])), ("Cost", Amount(totals["cost_paise"])),
        ("Unrealised gain", Gain(totals["unrealised_paise"])),
        ("Overall gain", Gain(totals["overall_gain_paise"])),
    )  # fmt: skip
    return Sheet("holdings", tab_view["tab"], tab_view["read_at"], figures, tuple(sections),
                 tuple(notes))  # fmt: skip


def allocation(tab_view: Mapping[str, Any]) -> Sheet:
    """The six allocation tables of the tab, each as its bars say."""
    cols = (Col("Name", 2.6, "l"), Col("Value", 1.5), Col("Share %", 1.0), Col("Holdings", 1.0))
    sections = tuple(
        Section(
            ALLOCATION_TITLES[by], cols,
            tuple((bar["label"], Amount(bar["value_paise"]), Pct(bar["pct"]), str(bar["holdings"]))
                  for bar in tab_view["allocation"][by]),
            empty="No holdings in this tab.",
        )
        for by in ALLOCATION_BY
    )  # fmt: skip
    figures = (("Value", Amount(tab_view["totals"]["value_paise"])),
               ("Holdings", str(len(tab_view["rows"]))))  # fmt: skip
    return Sheet("allocation", tab_view["tab"], tab_view["read_at"], figures, sections,
                 (WORDS["whole_tab"],))  # fmt: skip


# ---- CSV ------------------------------------------------------------------------------------------
def _plain(value: int | None, decimals: int, least: int) -> str:
    """A whole number of 10^-decimals as plain text, its sign kept: -105025, 2 is "-1050.25"."""
    if value is None:
        return ""
    whole, part = divmod(abs(value), 10**decimals)
    digits = f"{part:0{decimals}d}".rstrip("0").ljust(least, "0")
    return ("-" if value < 0 else "") + (f"{whole}.{digits}" if digits else str(whole))


def csv_cell(cell: Cell) -> str:
    if cell is None:
        return ""
    if isinstance(cell, str):
        return guard(cell)
    if isinstance(cell, (Amount, Gain)):
        return _plain(cell.paise, 2, 2)
    if isinstance(cell, Qty):
        return _plain(cell.qty_u, 6, 0)
    if isinstance(cell, Price):
        return _plain(cell.price_u, 4, 2)
    if isinstance(cell, Pct):
        return cell.text or ""
    day = cell.day
    return "" if day is None else f"{day // 10000:04d}-{day // 100 % 100:02d}-{day % 100:02d}"


def csv_rows(sheet: Sheet) -> list[list[str]]:
    """The file as rows of written cells: the two header rows, the figures, then each table."""
    tab = sheet.tab
    rows: list[Sequence[Cell]] = [
        (BRAND, sheet.title),
        (tab["name"], tab.get("name_alt") or "", sheet.as_of),
        (),
        *sheet.figures,
    ]
    for section in sheet.sections:
        if section.on == "paper":
            continue
        rows += [(), (section.title,), tuple(col.title for col in section.cols), *section.rows]
        if not section.rows:
            rows.append((section.empty,))
        if section.total:
            rows.append(section.total)
    if sheet.notes:
        rows += [(), ("Notes",), *((note,) for note in sheet.notes)]
    return [[csv_cell(cell) for cell in row] for row in rows]


def csv_text(sheet: Sheet) -> str:
    out = io.StringIO(newline="")
    csv.writer(out, lineterminator="\r\n").writerows(csv_rows(sheet))
    return BOM + out.getvalue()


def csv_file(sheet: Sheet) -> dict[str, str]:
    """types.ts ``ExportFile``."""
    return {"filename": file_name(sheet.tab, sheet.view, sheet.at, "csv"), "text": csv_text(sheet)}


def entries_file(tab: Mapping[str, Any], text: str, at: int) -> dict[str, str]:
    """The entries in the import format (``generic.export``'s text), named as every export is."""
    return {"filename": file_name(tab, "activity", at, "csv"), "text": BOM + text}
