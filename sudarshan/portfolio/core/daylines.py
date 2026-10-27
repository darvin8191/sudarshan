"""One day's holding lines, from the replayed ledger (M7.24).

``lines_of`` turns a tab's ledger, replayed up to ``today``, into what the screen prices: for each
asset a line per account that holds it or traded it today, with the quantity now and at the open,
the cost, and today's money in and out (valuation.py ``Line``). The rules are the README's:

* The open is after today's splits and bonus shares (they take effect before trading, and the
  exchange's previous close is already adjusted for them) and before today's trades.
* Money in and out are today's acquisitions and disposals at their gross trade amounts, charges
  left out. Shares brought in from or taken out to outside count at the value entered.
* Shares moved between two accounts of the tab count in one and out of the other at the previous
  close, so the tab's Today does not change and no gain moves between the accounts. With no
  previous close they count as nothing, and the holding's Today is not known anyway.
* Dividends, interest and fees never count.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping

from .ledger import GROUP_ACQUIRE, group_of
from .model import Entry, Ledger
from .money import value_paise
from .valuation import Line


def _between_accounts(entry: Entry) -> bool:
    return (
        entry.kind == "TRANSFER"
        and entry.account_id is not None
        and entry.to_account_id is not None
    )


def lines_of(
    ledger: Ledger, today: int, prev_close_u: Mapping[int, int | None]
) -> dict[int, list[Line]]:
    """Asset id to its lines, lowest account first; an asset nobody holds or traded today is left out.

    ``prev_close_u`` is each asset's previous close, only used to value a transfer between two
    accounts made today.
    """
    after_open: defaultdict = defaultdict(int)  # units added or taken after the open
    money_in: defaultdict = defaultdict(int)
    money_out: defaultdict = defaultdict(int)
    for move in ledger.journal:
        entry = move.entry
        if move.day != today or move.asset_id is None or group_of(entry) < GROUP_ACQUIRE:
            continue
        if not move.qty_u:  # income and fees move no units
            continue
        key = (move.account_id, move.asset_id)
        after_open[key] += move.qty_u
        if _between_accounts(entry):
            close = prev_close_u.get(move.asset_id)
            paise = 0 if close is None else value_paise(abs(move.qty_u), close)
        else:
            paise = move.paise
        (money_in if move.qty_u > 0 else money_out)[key] += paise
    held = sorted(
        (item for item in ledger.positions.items() if item[0][1] is not None),
        key=lambda item: (item[0][1], item[0][0]),
    )
    lines: dict[int, list[Line]] = {}
    for (account, asset), position in held:
        key = (account, asset)
        qty_u = position.qty_u
        opened = qty_u - after_open[key]
        if not (qty_u or opened or money_in[key] or money_out[key]):
            continue  # sold out on an earlier day
        lines.setdefault(asset, []).append(
            Line(
                account,
                qty_u,
                opened,
                position.cost_paise,
                money_in[key],
                money_out[key],
                position.over_year_qty_u(today),
            )
        )
    return lines
