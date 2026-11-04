"""The generic CSV: the file the platform reads and writes itself (M7.22, design 4.11).

UTF-8, a header row, then one entry a row. The columns, in the order an export writes them:

    date, action, symbol, exchange, isin, quantity, price, amount, charges, account, tds, ratio,
    note, to_account, lot_date, value

The first thirteen are the design's. The last three carry what a transfer between accounts, an
opening holding and a transfer to outside need so that an export read back is the same ledger
(vector I7); a file without them is read as it always was. Only ``date`` and ``action`` have to be
there; a row needs the cells its action needs, and the entry's own checks say what is missing.

* ``action`` is BUY, SELL, DIVIDEND, INTEREST, FEE, SPLIT, BONUS, RIGHTS, TRANSFER_IN (shares
  from outside, into ``account``, at ``amount`` of cost, with ``lot_date`` the day they were first
  bought and ``value`` the value that day), TRANSFER_OUT (shares from ``account`` to outside, at
  ``value``) or TRANSFER (from ``account`` to ``to_account``).
* ``date`` is YYYY-MM-DD, DD-MM-YYYY or DD/MM/YYYY, day first. A day after today is refused.
* ``symbol``, ``exchange`` and ``isin`` name the holding (an interest or fee row may name none);
  ``account`` names the account, or is empty for the one chosen for the import (a split with none
  is every account's). ``ratio`` is the split: 5:1 (each share becomes 5), 1:10 (one new for
  every ten old).
* Numbers are plain. ``amount`` is what counts: a row that gives an amount and a quantity and price
  that differ by more than one rupee is used at its amount and says so.
* Cells are read left to right and the first thing wrong in a row is the one said.

An export writes the posted entries in the order the ledger applies them, so that reading the file
back in file order gives the same ledger; drafts are not exported, and nor is a charges breakdown
(the total is).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import NamedTuple

from ..core import words
from ..core.kinds import IMPORT_MAX_FILE_BYTES, IMPORT_MAX_ROWS, NOTE_MAX, WORDS
from ..core.ledger import ordered
from ..core.model import Entry
from ..core.money import value_paise
from .csvutil import BadFile, Unreadable, parse_day, parse_ratio, read_table, scaled, write_table
from .preview import Context, Draft, Preview, RowProblem, entry_dict, finish

COLUMNS = (
    "date",
    "action",
    "symbol",
    "exchange",
    "isin",
    "quantity",
    "price",
    "amount",
    "charges",
    "account",
    "tds",
    "ratio",
    "note",
)
EXTRA_COLUMNS = ("to_account", "lot_date", "value")
HEADER = COLUMNS + EXTRA_COLUMNS
REQUIRED = ("date", "action")
#: The entry kind each action makes. A transfer is one kind with three shapes.
KIND_OF = {
    "BUY": "BUY",
    "SELL": "SELL",
    "DIVIDEND": "DIVIDEND",
    "INTEREST": "INTEREST",
    "FEE": "FEE",
    "SPLIT": "SPLIT",
    "BONUS": "BONUS",
    "RIGHTS": "RIGHTS",
    "TRANSFER_IN": "TRANSFER",
    "TRANSFER_OUT": "TRANSFER",
    "TRANSFER": "TRANSFER",
}
#: Entries that may name no holding.
NO_ASSET_NEEDED = ("INTEREST", "FEE")
#: A rupee, in paise: an amount that is further than this from quantity times price is said.
AMOUNT_TOLERANCE = 100


class AssetRef(NamedTuple):
    """How the file names a holding: what the instrument list calls it, as the resolver reads it."""

    symbol: str
    exchange: str = ""
    isin: str = ""


class ExportFile(NamedTuple):
    """The download (types.ts ``ExportFile``)."""

    filename: str
    text: str

    def wire(self) -> dict:
        return {"filename": self.filename, "text": self.text}


def _number(text: str, label: str, decimals: int) -> int | None:
    """A cell as a whole number of 10^-decimals; None when the cell is empty."""
    if not text:
        return None
    try:
        return scaled(text, decimals)
    except Unreadable as bad:
        if bad.kind == "negative":
            raise RowProblem(words.row_negative(label, text)) from None
        if bad.kind == "decimals":
            raise RowProblem(words.row_decimals(label, text, decimals)) from None
        raise RowProblem(words.row_number(label, text)) from None


def _action(text: str) -> str:
    found = re.sub(r"[\s\-]+", "_", text.strip().upper())
    if found not in KIND_OF:
        raise RowProblem(words.row_action(text))
    return found


def _read(cells: Mapping[str, str], ctx: Context) -> tuple[dict, str | None]:
    """The entry a row means and a remark on it; ``RowProblem`` for the first thing wrong."""
    day = ctx.day(cells["date"])
    action = _action(cells["action"])
    kind = KIND_OF[action]
    asset = ctx.asset(
        cells["symbol"], cells["exchange"], cells["isin"], required=action not in NO_ASSET_NEEDED
    )
    qty = _number(cells["quantity"], "quantity", 6)
    price = _number(cells["price"], "price", 4)
    amount = _number(cells["amount"], "amount", 2)
    charges = _number(cells["charges"], "charges", 2)
    account = ctx.account(cells["account"], default=kind != "SPLIT")
    tds = _number(cells["tds"], "TDS", 2)
    ratio = None
    if cells["ratio"] and (ratio := parse_ratio(cells["ratio"])) is None:
        raise RowProblem(words.row_ratio(cells["ratio"]))
    if len(cells["note"]) > NOTE_MAX:
        raise RowProblem(words.row_note(NOTE_MAX))
    second = ctx.account(cells["to_account"], default=False)
    lot_date = None
    if cells["lot_date"] and (lot_date := parse_day(cells["lot_date"])) is None:
        raise RowProblem(words.row_date(cells["lot_date"]))
    value = _number(cells["value"], "value", 2)

    if action in ("TRANSFER_IN", "TRANSFER_OUT"):
        if second is not None:
            raise RowProblem(words.ROW_ONE_ACCOUNT)
        source, target = (None, account) if action == "TRANSFER_IN" else (account, None)
    elif action == "TRANSFER":
        if second is None:
            raise RowProblem(words.ROW_NO_SECOND_ACCOUNT)
        source, target = account, second
    else:
        source, target = account, second
    worth = value_paise(qty, price) if qty is not None and price is not None else None
    if action == "TRANSFER_IN" and amount is None:
        amount = worth  # shares brought in cost what their quantity at the price says
    remark = None
    if amount is not None and worth is not None and abs(amount - worth) > AMOUNT_TOLERANCE:
        remark = words.row_amount(amount, worth)
    entry = entry_dict(
        kind,
        day,
        source,
        to_account_id=target,
        asset_id=asset,
        qty_u=qty,
        price_u=price,
        amount_paise=amount,
        charges_paise=charges or None,
        tds_paise=tds or None,
        value_paise=value,
        ratio_num=None if ratio is None else ratio[0],
        ratio_den=None if ratio is None else ratio[1],
        lot_date=lot_date,
        note=cells["note"] or None,
    )
    return entry, remark


def _draft(number: int, cells: Mapping[str, str], extra: bool, ctx: Context) -> Draft:
    try:
        if extra:
            raise RowProblem(words.ROW_CELLS)
        entry, remark = _read({name: cells.get(name, "") for name in HEADER}, ctx)
    except RowProblem as problem:
        return Draft(number, problem=words.row_says(number, str(problem)))
    return Draft(number, entry, remark=remark)


def preview(text: str, ctx: Context) -> Preview:
    """What importing this file would do to the tab. ``BadFile`` (a sentence for the owner) when
    the file as a whole cannot be used: too big, no table, no ``date`` or ``action`` column."""
    if len(text.encode("utf-8", "replace")) > IMPORT_MAX_FILE_BYTES:
        raise BadFile(WORDS["import_too_big"])
    table = read_table(text)
    missing = [name for name in REQUIRED if name not in table.header]
    if missing:
        raise BadFile(words.import_columns(missing))
    if len(table.records) > IMPORT_MAX_ROWS:
        raise BadFile(words.import_too_many_rows(IMPORT_MAX_ROWS))
    drafts = [_draft(rec.number, rec.cells, rec.extra, ctx) for rec in table.records]
    unused = tuple(name for name in table.header if name not in HEADER)
    return replace(finish(drafts, ctx, "generic"), unused=unused)


# ---- export ---------------------------------------------------------------------------------


def _day_text(day: int) -> str:
    return f"{day // 10000:04d}-{day // 100 % 100:02d}-{day % 100:02d}"


def _decimal(value: int | None, decimals: int, least: int) -> str:
    """A whole number of 10^-decimals as plain text with at least ``least`` decimals: 142825 and
    2, 2 is "1428.25"; 10_000_000 and 6, 0 is "10"."""
    if value is None:
        return ""
    whole, part = divmod(value, 10**decimals)
    digits = f"{part:0{decimals}d}".rstrip("0").ljust(least, "0")
    return f"{whole}.{digits}" if digits else str(whole)


def _row_of(
    entry: Entry, note: str | None, accounts: Mapping[int, str], assets: Mapping[int, AssetRef]
) -> list[str]:
    action, first, second = entry.kind, entry.account_id, entry.to_account_id
    if action == "TRANSFER":
        if first is None:  # the shares come in from outside to the account in ``to_account_id``
            action, first, second = "TRANSFER_IN", second, None
        elif second is None:
            action = "TRANSFER_OUT"
    ref = assets[entry.asset_id] if entry.asset_id is not None else AssetRef("")
    ratio = f"{entry.ratio_num}:{entry.ratio_den}" if entry.ratio_num else ""
    return [
        _day_text(entry.trade_date),
        action,
        ref.symbol,
        ref.exchange,
        ref.isin,
        _decimal(entry.qty_u, 6, 0),
        _decimal(entry.price_u, 4, 2),
        _decimal(entry.amount_paise, 2, 2),
        _decimal(entry.charges_paise or None, 2, 2),
        "" if first is None else accounts[first],
        _decimal(entry.tds_paise or None, 2, 2),
        ratio,
        note or "",
        "" if second is None else accounts[second],
        "" if entry.lot_date is None else _day_text(entry.lot_date),
        _decimal(entry.value_paise, 2, 2),
    ]


def export(
    rows: Sequence[Mapping],
    accounts: Mapping[int, str],
    assets: Mapping[int, AssetRef],
    tab_id: int,
    today: int,
) -> ExportFile:
    """A tab's entries as a generic CSV file named for the tab and the day.

    ``rows`` are the tab's activity rows (types.ts ``Activity``), each with its ``id``; only the
    posted ones are written, in the order the ledger applies them. ``accounts`` and ``assets`` name
    every account and holding they point to, as the importing tab will find them.
    """
    notes = {row["id"]: row.get("note") for row in rows}
    entries = ordered(Entry.of(row) for row in rows)
    lines = [_row_of(entry, notes[entry.id], accounts, assets) for entry in entries]
    return ExportFile(
        f"SUDARSHAN_Portfolio_{tab_id}_{_day_text(today)}.csv", write_table(HEADER, lines)
    )
