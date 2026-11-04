"""The Zerodha tradebook CSV (M7.22, design 4.11, PA8).

One file from Zerodha Console, equity segment only in v1: a purchase or a sale a row, at the price
it traded, with Zerodha's own ``trade_id``. The trade id is the duplicate key, so a file can be
imported again, and the several files a long history needs (Zerodha gives at most 365 days to a
download) can overlap, without a trade entering twice.

A tradebook has no charges, so a trade is entered with none (the owner can add them to the entry
afterwards); its date is the trade date, and rows keep the file's order within a day.

Privacy (P21): the file is read here, in memory, on the server; only the normalised rows are kept
(by the service, as a preview) and nothing of the file is quoted in a sentence beyond the short
cell that is wrong. The tests use rows typed from the column list below, never a real file.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from ..core import words
from ..core.kinds import IMPORT_MAX_FILE_BYTES, IMPORT_MAX_ROWS, WORDS
from .csvutil import BadFile, Unreadable, read_table, scaled
from .preview import Context, Draft, Preview, RowProblem, entry_dict, finish

#: The columns of the tradebook, as ``column_name`` reads a header cell (lower case, spaces and
#: hyphens as underscores): name, whether the format cannot do without it.
#:
#: WHAT IS CONFIRMED: only "Trade Date" and "Order Execution Time" were seen, in a third party's
#: description of the Console download; the other eleven names follow Zerodha's own habit of
#: naming the fields of its trades API and are NOT yet checked against a real file. Check the
#: header row of the owner's own download against this table before the feature is switched on;
#: a column that is spelt differently there is a one-line change here (add the spelling to
#: ``ALIASES``), and nothing else reads these names.
COLUMNS = {
    "symbol": True,
    "isin": False,
    "trade_date": True,
    "exchange": False,
    "segment": False,
    "series": False,
    "trade_type": True,
    "auction": False,
    "quantity": True,
    "price": True,
    "trade_id": True,
    "order_id": False,
    "order_execution_time": False,
}
#: Other spellings of a column that are accepted (read the same way as a header cell).
ALIASES = {
    "tradingsymbol": "symbol",
    "trade_time": "order_execution_time",
}
#: The segment value of cash-market trades. A tradebook with no segment column is taken as it.
EQUITY = "EQ"
KEY_PREFIX = "zerodha"


def _canonical(table_header: tuple[str, ...]) -> dict[str, str]:
    """Header name in the file to the column it means."""
    found = {}
    for name in table_header:
        target = ALIASES.get(name, name)
        if target in COLUMNS:
            found.setdefault(target, name)
    return found


def _number(text: str, label: str, decimals: int) -> int:
    if not text:
        raise RowProblem(words.row_number(label, text))
    try:
        return scaled(text, decimals)
    except Unreadable as bad:
        if bad.kind == "negative":
            raise RowProblem(words.row_negative(label, text)) from None
        if bad.kind == "decimals":
            raise RowProblem(words.row_decimals(label, text, decimals)) from None
        raise RowProblem(words.row_number(label, text)) from None


def _read(cells: Mapping[str, str], ctx: Context) -> tuple[dict, str]:
    """The entry a row means and its trade id; ``RowProblem`` for the first thing wrong."""
    segment = cells["segment"]
    if segment and segment.upper() != EQUITY:
        raise RowProblem(words.row_segment(segment))
    trade_id = cells["trade_id"]
    if not trade_id:
        raise RowProblem(words.ROW_NO_TRADE_ID)
    day = ctx.day(cells["trade_date"])
    kind = {"buy": "BUY", "sell": "SELL"}.get(cells["trade_type"].lower())
    if kind is None:
        raise RowProblem(words.row_trade_type(cells["trade_type"]))
    asset = ctx.asset(cells["symbol"], cells["exchange"], cells["isin"])
    qty = _number(cells["quantity"], "quantity", 6)
    price = _number(cells["price"], "price", 4)
    account = ctx.account("")
    entry = entry_dict(kind, day, account, asset_id=asset, qty_u=qty, price_u=price)
    return entry, f"{KEY_PREFIX}:{trade_id}"


def preview(text: str, ctx: Context) -> Preview:
    """What importing this tradebook would do to the tab. ``BadFile`` (a sentence for the owner)
    when the file as a whole cannot be used: too big, no table, not a tradebook's header."""
    if len(text.encode("utf-8", "replace")) > IMPORT_MAX_FILE_BYTES:
        raise BadFile(WORDS["import_too_big"])
    table = read_table(text)
    named = _canonical(table.header)
    missing = [name for name, needed in COLUMNS.items() if needed and name not in named]
    if missing:
        raise BadFile(words.import_not_tradebook(missing))
    if len(table.records) > IMPORT_MAX_ROWS:
        raise BadFile(words.import_too_many_rows(IMPORT_MAX_ROWS))
    drafts = []
    for record in table.records:
        cells = {name: record.cells.get(named.get(name, ""), "") for name in COLUMNS}
        try:
            if record.extra:
                raise RowProblem(words.ROW_CELLS)
            entry, key = _read(cells, ctx)
        except RowProblem as problem:
            drafts.append(Draft(record.number, problem=words.row_says(record.number, str(problem))))
        else:
            drafts.append(Draft(record.number, entry, key=key))
    known = set(COLUMNS) | set(ALIASES)
    unused = tuple(name for name in table.header if name not in known)
    return replace(finish(drafts, ctx, KEY_PREFIX), unused=unused)
