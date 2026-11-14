"""Tax lots (M7.26): first in, first out per taxpayer, account and ISIN, intraday taken out first.

One replay over everything known: opening lots on their acquisition day, then that day's accepted
bonus and split, then the day's trades by ``seq``. Of one day's trading in one share, the lesser
of what was bought and sold is intraday (the earliest buys against the earliest sells) and never
touches a lot; the rest of the buys open lots and the rest of the sells take the oldest lots. A
sale with no lot behind it comes out with no cost (never a cost of zero). The rules are the
portfolio ledger's own (cost from what remains of a lot, a split on the running total); a test in
the portfolio holds the two together.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from . import rules, words
from .classify import DELIVERY, EQUITY_MF, head_of, intraday_qty
from .model import Action, Disposal, Lot, Realised, Trade
from .units import day_text, round_div, share_of

WHOLE = 10**6


class LotBook(NamedTuple):
    """What a replay leaves: ``fractions`` are (taxpayer, account, isin, day, kind, qty_u held)."""

    disposals: tuple[Disposal, ...]
    intraday: tuple[Realised, ...]
    open_lots: tuple[Lot, ...]
    notes: tuple[str, ...]
    fractions: tuple[tuple, ...]


@dataclass(slots=True)
class _Held:
    day: int
    qty_u: int
    cost_paise: int
    source: str
    basis_qty_u: int


@dataclass(slots=True)
class _Rest:
    """What is left of one trade as parts of it are used."""

    trade: Trade
    qty_u: int
    amount: int
    charges: int | None

    def take(self, part: int) -> tuple[int, int | None]:
        amount = share_of(self.amount, part, self.qty_u)
        charges = None if self.charges is None else share_of(self.charges, part, self.qty_u)
        self.amount -= amount
        self.charges = None if charges is None else self.charges - charges
        self.qty_u -= part
        return amount, charges


def canonical(isin: str, aliases: dict[str, str]) -> str:
    """The ISIN a share goes by now, following old-to-new names (a loop ends where it began)."""
    seen = {isin}
    while aliases.get(isin) not in (None, *seen):
        isin = aliases[isin]
        seen.add(isin)
    return isin


def replay(trades, opening=(), actions=(), all_delivery=frozenset(), aliases=None) -> LotBook:
    """Replay share and equity-fund trades into lots. ``all_delivery`` holds the (taxpayer,
    account, isin, day) the owner marked as delivery; trades of other heads are passed over."""
    names = dict(aliases or {})
    names.update({a.instrument: a.new_isin for a in actions if a.accepted and a.new_isin})
    events: list[tuple] = [(lot.acquired_day, 0, 0, lot) for lot in opening]
    events += [(action.ex_day, 1, 0, action) for action in actions]
    days: dict[tuple, list[Trade]] = {}
    for trade in trades:
        if head_of(trade) in (DELIVERY, EQUITY_MF):
            key = (
                trade.day,
                2,
                (trade.taxpayer, trade.account, canonical(trade.instrument, names)),
            )
            days.setdefault(key, []).append(trade)
    events += [(*key, sorted(group, key=lambda t: t.seq)) for key, group in days.items()]
    events.sort(key=lambda event: event[:3] if event[1] == 2 else event[:2])
    book: dict[tuple, list[_Held]] = {}
    out = LotBook([], [], [], [], [])
    for day, rank, key, what in events:
        if rank == 0:
            scope = (what.taxpayer, what.account, canonical(what.instrument, names))
            book.setdefault(scope, []).append(
                _Held(day, what.qty_u, what.cost_paise, what.source, what.qty_u)
            )
        elif rank == 1:
            _act(book, what, canonical(what.instrument, names), out)
        else:
            _trade_day(book.setdefault(key, []), key, day, what, key + (day,) in all_delivery, out)
    for (taxpayer, account, isin), lots in sorted(book.items()):
        out.open_lots.extend(
            Lot(taxpayer, account, isin, h.day, h.qty_u, h.cost_paise, h.source, h.basis_qty_u)
            for h in lots
        )
    return LotBook(*(tuple(part) for part in out))


def _act(book: dict, action: Action, isin: str, out: LotBook) -> None:
    """A bonus or a split on every account that holds the share on the ex-date."""
    fmv_day = rules.at(rules.GRANDFATHER, action.ex_day)["fmv_day"]
    for scope, lots in sorted(book.items()):
        held = sum(lot.qty_u for lot in lots)
        if scope[2] != isin or not held:
            continue
        if not action.accepted:
            out.notes.append(words.action_unconfirmed(action.kind, isin, day_text(action.ex_day)))
            continue
        if action.kind == "SPLIT":
            before = after = 0
            for lot in lots:  # on the running total, so the lots add up to the new holding
                before += lot.qty_u
                target = round_div(before * action.num, action.den)
                lot.qty_u, after = target - after, target
                if action.ex_day <= fmv_day:
                    lot.basis_qty_u = lot.qty_u
        elif action.kind == "BONUS":
            extra = round_div(held * action.num, action.den)
            lots.append(_Held(action.ex_day, extra, 0, "bonus", extra))
        exact = held * action.num % action.den == 0
        if held % WHOLE == 0 and (not exact or any(lot.qty_u % WHOLE for lot in lots)):
            total = sum(lot.qty_u for lot in lots)
            out.fractions.append((*scope, action.ex_day, action.kind, total))
            out.notes.append(words.fraction_left(action.kind, isin, day_text(action.ex_day)))


def _trade_day(lots: list[_Held], scope: tuple, day: int, trades, delivery: bool, out) -> None:
    rests = [_Rest(t, t.qty_u, t.amount_paise, t.charges_ex_stt_paise) for t in trades]
    buys = [r for r in rests if r.trade.side == "BUY"]
    sells = [r for r in rests if r.trade.side == "SELL" and r.trade.kind_hint != "buyback"]
    share = all(r.trade.segment == "EQ" for r in rests)
    quantity = intraday_qty(
        sum(r.qty_u for r in buys), sum(r.qty_u for r in sells), delivery or not share
    )
    if quantity:
        bought, sold = _first(buys, quantity), _first(sells, quantity)
        out.intraday.append(
            Realised(*scope, "speculative", day, day, quantity, bought, sold, sold - bought)
        )
    for rest in rests:
        if not rest.qty_u:
            continue
        if rest.trade.side == "BUY":
            cost = rest.amount + (rest.charges or 0)
            lots.append(_Held(day, rest.qty_u, cost, rest.trade.source, rest.qty_u))
            continue
        hint, seq = rest.trade.kind_hint, rest.trade.seq
        while rest.qty_u and lots:
            lot = lots[0]
            part = min(rest.qty_u, lot.qty_u)
            cost = share_of(lot.cost_paise, part, lot.qty_u)
            basis = share_of(lot.basis_qty_u, part, lot.qty_u)
            sale, charges = rest.take(part)
            out.disposals.append(
                Disposal(
                    *scope, day, seq, part, sale, lot.day, cost, basis, lot.source, charges, hint
                )
            )
            lot.qty_u, lot.cost_paise = lot.qty_u - part, lot.cost_paise - cost
            lot.basis_qty_u -= basis
            if not lot.qty_u:
                lots.pop(0)
        if rest.qty_u:  # no purchase record: listed with its sale value, never costed at zero
            part = rest.qty_u
            sale, charges = rest.take(part)
            out.disposals.append(
                Disposal(*scope, day, seq, part, sale, None, None, 0, None, charges, hint)
            )


def _first(rests: list[_Rest], quantity: int) -> int:
    """The value of the first ``quantity`` units of these trades, which are used up."""
    value = 0
    for rest in rests:
        part = min(quantity, rest.qty_u)
        if part:
            value += rest.take(part)[0]
            quantity -= part
    return value
