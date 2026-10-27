"""Splits, bonus shares and transfers in the ledger (M7.20).

The entries that reshape or move lots without a sale (ledger.py holds the rest):

    SPLIT     every lot of the asset, in one account or in every account that holds it, is
              multiplied by num/den. Cost and day stay, so the cost of a unit changes and the
              cost of the holding does not. A split that would leave part of a share (or less than
              the asset's smallest unit) is refused.
    BONUS     a new lot at nil cost, dated the allotment, in an account that holds the asset.
    TRANSFER  between two accounts the lots move with their cost and day (a lot keeps counting as
              bought when it was bought, and goes into the new account's queue by that day). From
              outside it opens a lot at the cost entered, dated its first purchase if the owner
              knows it. To outside the lots leave at their cost with no gain or loss; the value
              entered only says what to count as money out.

All of them refuse what the account does not hold, in the owner's words.
"""

from __future__ import annotations

from .model import Entry, Ledger, Move, Piece, Position, Refusal, short_of
from .money import round_div, step_ok
from .words import fraction_left, not_held


def _not_held(ledger: Ledger, entry: Entry) -> Refusal:
    text = not_held(ledger.names, entry.kind, entry.account_id, entry.asset_id, entry.trade_date)
    return Refusal(
        text,
        entry_id=entry.id,
        reason="not_held",
        kind=entry.kind,
        day=entry.trade_date,
        account_id=entry.account_id,
        asset_id=entry.asset_id,
    )


def _fraction(ledger: Ledger, entry: Entry, position: Position, left_qty_u: int) -> Refusal:
    text = fraction_left(ledger.names, position.account_id, position.asset_id, left_qty_u)
    return Refusal(
        text,
        entry_id=entry.id,
        reason="fraction",
        kind=entry.kind,
        day=entry.trade_date,
        account_id=position.account_id,
        asset_id=position.asset_id,
        have_qty_u=position.qty_u,
        left_qty_u=left_qty_u,
        ratio_num=entry.ratio_num,
        ratio_den=entry.ratio_den,
    )


def _lots_after_split(position: Position, num: int, den: int) -> list[int]:
    """The quantity of each lot, oldest first, once the position is multiplied by num/den.

    Rounded on the running total (the cumulative quantity times num/den, less what the earlier
    lots took), so that the lots add up to the position's new quantity exactly: 5 + 5 shares
    consolidated 1 for 10 are two lots of half a share that make one share.
    """
    quantities, before, after = [], 0, 0
    for lot in position.lots:
        before += lot.qty_u
        target = round_div(before * num, den)
        quantities.append(target - after)
        after = target
    return quantities


def _split(ledger: Ledger, entry: Entry) -> None:
    num, den = entry.ratio_num, entry.ratio_den
    if entry.account_id is None:
        held = sorted(
            (p for p in ledger.positions.values() if p.asset_id == entry.asset_id and p.qty_u),
            key=lambda p: p.account_id,
        )
    else:
        position = ledger.position(entry.account_id, entry.asset_id)
        held = [position] if position.qty_u else []
    if not held:
        raise _not_held(ledger, entry)
    decimals = ledger.decimals_of(entry.asset_id)
    plans = []
    for position in held:  # decide for every account before changing any
        quantities = _lots_after_split(position, num, den)
        left = round_div(position.qty_u * num, den)
        if position.qty_u * num % den or not step_ok(left, decimals) or min(quantities) <= 0:
            raise _fraction(ledger, entry, position, left)
        plans.append((position, quantities))
    for position, quantities in plans:
        before = position.qty_u
        position.rescale(quantities)
        lots = tuple(Piece(lot.day, lot.qty_u, lot.cost_paise) for lot in position.lots)
        ledger.journal.append(
            Move(entry, position.account_id, position.asset_id, position.qty_u - before, used=lots)
        )


def _bonus(ledger: Ledger, entry: Entry) -> None:
    position = ledger.position(entry.account_id, entry.asset_id)
    if not position.qty_u:
        raise _not_held(ledger, entry)
    position.add(entry.trade_date, ledger.next_order(), entry.qty_u, 0)
    ledger.journal.append(Move(entry, entry.account_id, entry.asset_id, entry.qty_u))


def _transfer_in(ledger: Ledger, entry: Entry) -> None:
    """Shares brought in from outside: one lot at the cost entered, dated its first purchase."""
    cost = entry.amount_paise
    day = entry.lot_date or entry.trade_date
    ledger.position(entry.to_account_id, entry.asset_id).add(
        day, ledger.next_order(), entry.qty_u, cost
    )
    value = cost if entry.value_paise is None else entry.value_paise
    ledger.journal.append(
        Move(entry, entry.to_account_id, entry.asset_id, entry.qty_u, cost, paise=value)
    )


def _transfer_out(ledger: Ledger, entry: Entry) -> None:
    """Shares taken out to outside: the oldest lots leave at cost, with no gain or loss."""
    position = ledger.position(entry.account_id, entry.asset_id)
    if position.qty_u < entry.qty_u:
        raise short_of(ledger.names, entry, position.qty_u)
    pieces = position.take(entry.qty_u)
    cost = sum(piece.cost_paise for piece in pieces)
    value = cost if entry.value_paise is None else entry.value_paise
    ledger.journal.append(
        Move(
            entry,
            entry.account_id,
            entry.asset_id,
            -entry.qty_u,
            -cost,
            paise=value,
            used=tuple(pieces),
        )
    )


def _transfer_between(ledger: Ledger, entry: Entry) -> None:
    """Shares from one account to another: the oldest lots move with their cost and day."""
    source = ledger.position(entry.account_id, entry.asset_id)
    if source.qty_u < entry.qty_u:
        raise short_of(ledger.names, entry, source.qty_u)
    pieces = tuple(source.take(entry.qty_u))
    target = ledger.position(entry.to_account_id, entry.asset_id)
    for piece in pieces:
        target.add(piece.day, ledger.next_order(), piece.qty_u, piece.cost_paise)
    cost = sum(piece.cost_paise for piece in pieces)
    ledger.journal.append(
        Move(entry, entry.account_id, entry.asset_id, -entry.qty_u, -cost, used=pieces)
    )
    ledger.journal.append(
        Move(entry, entry.to_account_id, entry.asset_id, entry.qty_u, cost, used=pieces)
    )


def _transfer(ledger: Ledger, entry: Entry) -> None:
    if entry.account_id is None:
        _transfer_in(ledger, entry)
    elif entry.to_account_id is None:
        _transfer_out(ledger, entry)
    else:
        _transfer_between(ledger, entry)


ACTIONS = {"SPLIT": _split, "BONUS": _bonus, "TRANSFER": _transfer}
