"""FIFO per account and asset, replayed in the day's own order (M7.26).

A tab's ledger is its posted entries replayed from the start, never a running total, so that an
edit or a delete can be checked by replaying the tab without it (README, "Ledger"). Within one day
the order is not the order the owner typed:

    0  splits and bonuses       ex-date, before trading
    1  acquisitions             purchases, rights, transfers in from outside
    2  transfers between accounts of the tab
    3  disposals                sales, transfers out to outside
    4  dividends, interest, fees

and inside a group the entry's ``seq`` (an import keeps file order). So a sale typed before the
purchase of the same day is valid, and shares moved to another account that day can be sold there.
FIFO takes the oldest acquisition first; the lot keeps its day through a transfer, so it counts as
bought when it was bought. Sales and cost are exact integers (money.share_of_cost).

Purchases, rights, sales, dividends, interest and fees are here; splits, bonus shares and
transfers are in ledger_actions.py and join the same table of handlers.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .ledger_actions import ACTIONS
from .model import Entry, Ledger, Move, check, short_of
from .words import Names

GROUP_SPLITS, GROUP_ACQUIRE, GROUP_MOVE, GROUP_DISPOSE, GROUP_INCOME = range(5)


def group_of(entry: Entry) -> int:
    """Where in its day an entry is applied."""
    kind = entry.kind
    if kind in ("SPLIT", "BONUS"):
        return GROUP_SPLITS
    if kind in ("BUY", "RIGHTS"):
        return GROUP_ACQUIRE
    if kind == "TRANSFER":
        if entry.account_id is None:
            return GROUP_ACQUIRE
        return GROUP_DISPOSE if entry.to_account_id is None else GROUP_MOVE
    return GROUP_DISPOSE if kind == "SELL" else GROUP_INCOME


def ordered(entries: Iterable[Entry], as_of: int | None = None) -> list[Entry]:
    """The posted entries up to ``as_of`` (a day, or None for all) in the order they are applied."""
    live = (e for e in entries if e.status == "posted" and (as_of is None or e.trade_date <= as_of))
    return sorted(live, key=lambda e: (e.trade_date, group_of(e), e.seq, e.id or 0))


def _buy(ledger: Ledger, entry: Entry) -> None:
    """A purchase or a rights issue: a new lot costing the amount and the charges."""
    cost = entry.amount + entry.charges_paise
    ledger.position(entry.account_id, entry.asset_id).add(
        entry.trade_date, ledger.next_order(), entry.qty_u, cost
    )
    ledger.journal.append(
        Move(entry, entry.account_id, entry.asset_id, entry.qty_u, cost, paise=entry.amount)
    )


def _sell(ledger: Ledger, entry: Entry) -> None:
    """A sale: the oldest lots of this account go first; the gain is what is left of the proceeds
    after the charges and the cost of those lots."""
    position = ledger.position(entry.account_id, entry.asset_id)
    held = position.qty_u
    if held < entry.qty_u:
        raise short_of(ledger.names, entry, held)
    pieces = position.take(entry.qty_u)
    cost = sum(piece.cost_paise for piece in pieces)
    gain = entry.amount - entry.charges_paise - cost
    position.realised_paise += gain
    ledger.journal.append(
        Move(
            entry,
            entry.account_id,
            entry.asset_id,
            -entry.qty_u,
            -cost,
            gain,
            entry.amount,
            tuple(pieces),
        )
    )


def _income(ledger: Ledger, entry: Entry) -> None:
    """A dividend or interest: counted gross, whatever was withheld; no lots change."""
    ledger.position(entry.account_id, entry.asset_id).income_paise += entry.amount_paise
    ledger.journal.append(Move(entry, entry.account_id, entry.asset_id, paise=entry.amount_paise))


def _fee(ledger: Ledger, entry: Entry) -> None:
    ledger.position(entry.account_id, entry.asset_id).fees_paise += entry.amount_paise
    ledger.journal.append(Move(entry, entry.account_id, entry.asset_id, paise=entry.amount_paise))


HANDLERS = {
    "BUY": _buy,
    "RIGHTS": _buy,
    "SELL": _sell,
    "DIVIDEND": _income,
    "INTEREST": _income,
    "FEE": _fee,
    **ACTIONS,
}


def replay(
    entries: Iterable[Entry],
    *,
    as_of: int | None = None,
    names: Names | None = None,
    decimals: Mapping[int, int] | None = None,
) -> Ledger:
    """The tab's ledger after its posted entries up to ``as_of``.

    Raises ``Refusal`` for the first entry that cannot stand (a sale of units the account does not
    hold on its day, a split that would leave a fraction), naming it, and ``BadEntry`` for one
    that is not well formed. Drafts count nowhere. ``names`` only gives the sentences the owner's
    words for accounts and holdings. ``decimals`` says how many decimals a quantity of an asset
    may have (0 for shares, which is also what an asset left out gets), so that a split cannot
    leave part of a share.
    """
    ledger = Ledger(names, decimals)
    for entry in ordered(entries, as_of):
        check(entry)
        HANDLERS[entry.kind](ledger, entry)
    return ledger
