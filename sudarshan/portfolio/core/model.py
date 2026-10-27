"""The ledger's vocabulary: an entry as it reads, lots, positions, what a replay did, the refusal.

(M7.19, M7.20.) An ``Entry`` is one row of a tab's activity list, as integers and nothing else. A
``Lot`` is units bought on one day for one cost; a ``Position`` is one account's lots of one asset,
kept oldest first; a ``Ledger`` is every position of a tab after a replay, with the ``Move`` each
entry made. ``Refusal`` is the ledger saying no, in a sentence for the owner, and ``BadEntry`` is
an entry that is not well formed.
"""

from __future__ import annotations

from bisect import insort
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import NamedTuple

from .days import held_over_year, is_day
from .kinds import ACTIVITY_KINDS, ACTIVITY_STATUSES
from .money import is_int, paise_ok, price_ok, qty_ok, share_of_cost, value_paise
from .words import LABELS, NOUNS, Names, short_of_units

#: A split's ratio is two whole numbers up to this ("each becomes 5" is 5 and 1).
MAX_RATIO = 100_000
INT_FIELDS = (
    "id",
    "seq",
    "account_id",
    "to_account_id",
    "asset_id",
    "qty_u",
    "price_u",
    "amount_paise",
    "charges_paise",
    "tds_paise",
    "value_paise",
    "ratio_num",
    "ratio_den",
    "lot_date",
)
#: What each kind of entry needs, and what else it may carry (README, "Fields by kind"). A field in
#: neither list must be empty. The amount of a purchase, sale or rights entry may come from a price.
FIELDS = {
    "BUY": (("account_id", "asset_id", "qty_u"), ("price_u", "amount_paise", "charges_paise")),
    "SELL": (("account_id", "asset_id", "qty_u"), ("price_u", "amount_paise", "charges_paise")),
    "RIGHTS": (("account_id", "asset_id", "qty_u"), ("price_u", "amount_paise", "charges_paise")),
    "DIVIDEND": (("account_id", "asset_id", "amount_paise"), ("tds_paise",)),
    "INTEREST": (("account_id", "amount_paise"), ("asset_id", "tds_paise")),
    "FEE": (("account_id", "amount_paise"), ("asset_id",)),
    "TRANSFER": (
        ("asset_id", "qty_u"),
        ("account_id", "to_account_id", "amount_paise", "value_paise", "lot_date"),
    ),
    "SPLIT": (("asset_id", "ratio_num", "ratio_den"), ("account_id",)),
    "BONUS": (("account_id", "asset_id", "qty_u"), ()),
}
PRICED = ("BUY", "SELL", "RIGHTS")


class BadEntry(ValueError):
    """An entry that is not well formed, in a sentence for the owner."""


class Refusal(Exception):
    """The ledger says no, in a sentence for the owner.

    ``entry_id`` names the entry that cannot stand (None for one not saved yet), ``reason`` is
    "short" (more units than held) or one of the split and bonus reasons, and ``facts`` holds the
    numbers behind the sentence so that a caller can say it another way (words.knock_on).
    """

    def __init__(self, message: str, *, entry_id: int | None, reason: str, **facts):
        super().__init__(message)
        self.message = message
        self.entry_id = entry_id
        self.reason = reason
        self.facts = facts


def short_of(names: Names, entry: Entry, held: int) -> Refusal:
    """The refusal for an entry that takes more units than its account holds on its day."""
    text = short_of_units(
        names, entry.kind, entry.account_id, entry.asset_id, entry.trade_date, held, entry.qty_u
    )
    return Refusal(
        text,
        entry_id=entry.id,
        reason="short",
        kind=entry.kind,
        day=entry.trade_date,
        account_id=entry.account_id,
        asset_id=entry.asset_id,
        have_qty_u=held,
        want_qty_u=entry.qty_u,
    )


@dataclass(frozen=True, slots=True)
class Entry:
    """One row of a tab's ledger, the contract's names (types.ts ``Activity``) and nothing more."""

    kind: str
    trade_date: int
    id: int | None = None
    seq: int = 0
    status: str = "posted"
    account_id: int | None = None
    to_account_id: int | None = None
    asset_id: int | None = None
    qty_u: int | None = None
    price_u: int | None = None
    amount_paise: int | None = None
    charges_paise: int = 0
    tds_paise: int = 0
    value_paise: int | None = None
    ratio_num: int | None = None
    ratio_den: int | None = None
    lot_date: int | None = None

    @property
    def amount(self) -> int | None:
        """The gross amount as entered, else quantity x price; None when neither is given."""
        if self.amount_paise is not None:
            return self.amount_paise
        if self.qty_u is not None and self.price_u is not None:
            return value_paise(self.qty_u, self.price_u)
        return None

    @classmethod
    def of(cls, row: Mapping) -> Entry:
        """An entry from an activity row or the phone's input; fields it does not know are left."""
        values = {}
        for name in INT_FIELDS:
            value = row.get(name)
            if value is not None:
                if not is_int(value):
                    raise BadEntry(f"Enter {LABELS.get(name, 'that')} as a whole number.")
                values[name] = value
        entry = cls(
            kind=row.get("kind"),
            trade_date=row.get("trade_date"),
            status=row.get("status") or "posted",
            **values,
        )
        check(entry)
        return entry


def check(entry: Entry) -> None:
    """Raise ``BadEntry`` unless the entry has what its kind needs, nothing it does not take, and
    numbers in range."""
    kind = entry.kind
    if kind not in ACTIVITY_KINDS:
        raise BadEntry("Choose what kind of entry this is.")
    if entry.status not in ACTIVITY_STATUSES:
        raise BadEntry("An entry is posted or a draft.")
    if not is_day(entry.trade_date):
        raise BadEntry("Enter a valid date.")
    noun = NOUNS[kind]
    needs, may = FIELDS[kind]
    for name in needs:
        if getattr(entry, name) is None:
            raise BadEntry(f"A {noun} needs {LABELS[name]}.")
    if kind in PRICED and entry.amount is None:
        raise BadEntry(f"A {noun} needs an amount or a price.")
    for name in INT_FIELDS[2:]:
        value = getattr(entry, name)
        if name not in needs and name not in may and value not in (None, 0):
            raise BadEntry(f"A {noun} does not take {LABELS[name]}.")
    if entry.qty_u is not None and not qty_ok(entry.qty_u):
        raise BadEntry("The quantity must be above zero and at most 100 crore units.")
    if entry.price_u is not None and not price_ok(entry.price_u):
        raise BadEntry("That price is not valid.")
    for name in ("amount_paise", "charges_paise", "tds_paise", "value_paise"):
        if getattr(entry, name) is not None and not paise_ok(getattr(entry, name)):
            raise BadEntry("An amount must be zero or more, and not absurdly large.")
    if entry.tds_paise > (entry.amount_paise or 0):
        raise BadEntry("TDS cannot be more than the amount.")
    if kind == "SPLIT":
        num, den = entry.ratio_num, entry.ratio_den
        if not (is_int(num) and is_int(den) and 0 < num <= MAX_RATIO and 0 < den <= MAX_RATIO):
            raise BadEntry("A split needs a ratio of whole numbers above zero.")
        if num == den:
            raise BadEntry("A split must change the number of shares.")
    if kind == "TRANSFER":
        _check_transfer(entry)


def _check_transfer(entry: Entry) -> None:
    source, target = entry.account_id, entry.to_account_id
    if source is None and target is None:
        raise BadEntry("A transfer needs an account to leave, an account to reach, or both.")
    if source == target:
        raise BadEntry("A transfer needs two different accounts.")
    if source is None:
        if entry.amount_paise is None:
            raise BadEntry("A transfer from outside needs the cost of the shares.")
        if entry.lot_date is not None and not (
            is_day(entry.lot_date) and entry.lot_date <= entry.trade_date
        ):
            raise BadEntry("The original purchase date must be a real day, not after the transfer.")
        return
    if entry.lot_date is not None:
        raise BadEntry("Only a transfer from outside takes an original purchase date.")
    if entry.amount_paise is not None:
        raise BadEntry("Shares that move keep their own cost; a transfer takes no amount.")
    if target is not None and entry.value_paise is not None:
        raise BadEntry("A transfer between accounts takes no value.")


class Piece(NamedTuple):
    """Part of a lot that a sale or a transfer took: its acquisition day, units and cost."""

    day: int
    qty_u: int
    cost_paise: int


@dataclass(slots=True)
class Lot:
    """Units bought on one day; ``cost_paise`` is the cost of what remains."""

    day: int
    order: int
    qty_u: int
    cost_paise: int


class Position:
    """One account's lots of one asset, oldest first, and what the account has earned from it.

    With no asset it holds only an account's own income and fees. FIFO order is the acquisition
    day, and for lots of one day the order they were made in; a transfer keeps the day.
    """

    __slots__ = (
        "account_id",
        "asset_id",
        "lots",
        "qty_u",
        "cost_paise",
        "realised_paise",
        "income_paise",
        "fees_paise",
    )

    def __init__(self, account_id: int | None, asset_id: int | None):
        self.account_id = account_id
        self.asset_id = asset_id
        self.lots: list[Lot] = []
        #: The units and cost of ``lots``, kept in step by ``add`` and ``take`` (the only ways a
        #: lot changes), so that a sale does not add up every lot to know what is held.
        self.qty_u = 0
        self.cost_paise = 0
        self.realised_paise = 0
        self.income_paise = 0
        self.fees_paise = 0

    def add(self, day: int, order: int, qty_u: int, cost_paise: int) -> None:
        insort(self.lots, Lot(day, order, qty_u, cost_paise), key=lambda lot: (lot.day, lot.order))
        self.qty_u += qty_u
        self.cost_paise += cost_paise

    def take(self, qty_u: int) -> list[Piece]:
        """Remove ``qty_u`` from the oldest lots; the caller has checked the position holds them.

        A whole lot goes at its remaining cost; a part of one at ``share_of_cost``.
        """
        pieces = []
        while qty_u:
            lot = self.lots[0]
            part = min(qty_u, lot.qty_u)
            cost = share_of_cost(lot.cost_paise, part, lot.qty_u)
            pieces.append(Piece(lot.day, part, cost))
            lot.qty_u -= part
            lot.cost_paise -= cost
            self.qty_u -= part
            self.cost_paise -= cost
            qty_u -= part
            if not lot.qty_u:
                self.lots.pop(0)
        return pieces

    def rescale(self, quantities: list[int]) -> None:
        """Give each lot, oldest first, a new quantity (a split); cost and day stay."""
        for lot, qty_u in zip(self.lots, quantities, strict=True):
            lot.qty_u = qty_u
        self.qty_u = sum(quantities)

    def over_year_qty_u(self, on: int) -> int:
        """Units in lots held for more than a year on ``on``."""
        return sum(lot.qty_u for lot in self.lots if held_over_year(lot.day, on))


class Move(NamedTuple):
    """What one entry did to one account's position.

    ``qty_u`` and ``cost_paise`` are the change to the position (a sale is negative); ``paise`` is
    the money of the entry (a trade's gross amount, an income, a fee, the value of shares moved to
    or from outside; None where no money moves); ``used`` is the lots a sale or a transfer took,
    with the cost given to each, or for a split the lots as they stand after it. A transfer between
    two accounts makes two moves, one in each account.
    """

    entry: Entry
    account_id: int | None
    asset_id: int | None
    qty_u: int = 0
    cost_paise: int = 0
    gain_paise: int = 0
    paise: int | None = None
    used: tuple[Piece, ...] = ()

    @property
    def kind(self) -> str:
        return self.entry.kind

    @property
    def day(self) -> int:
        return self.entry.trade_date


class Ledger:
    """Every position of a tab after a replay, and the moves that made them, in order."""

    __slots__ = ("names", "decimals", "positions", "journal", "_orders")

    def __init__(self, names: Names | None = None, decimals: Mapping[int, int] | None = None):
        self.names = names or Names()
        self.decimals = decimals or {}
        self.positions: dict[tuple[int | None, int | None], Position] = {}
        self.journal: list[Move] = []
        self._orders = 0

    def position(self, account_id: int | None, asset_id: int | None) -> Position:
        key = (account_id, asset_id)
        if key not in self.positions:
            self.positions[key] = Position(account_id, asset_id)
        return self.positions[key]

    def decimals_of(self, asset_id: int | None) -> int:
        """The decimals a quantity of this asset may have; shares unless told otherwise."""
        return self.decimals.get(asset_id, 0)

    def next_order(self) -> int:
        """The next number to tell lots of one day apart; the order they were made in."""
        self._orders += 1
        return self._orders

    def holdings(self) -> Iterator[Position]:
        """The positions that hold units."""
        return (position for position in self.positions.values() if position.qty_u)

    @property
    def realised_paise(self) -> int:
        return sum(position.realised_paise for position in self.positions.values())

    @property
    def income_paise(self) -> int:
        return sum(position.income_paise for position in self.positions.values())

    @property
    def fees_paise(self) -> int:
        return sum(position.fees_paise for position in self.positions.values())
