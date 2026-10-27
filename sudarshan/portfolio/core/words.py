"""The owner's words for dates, money and quantities, and for what the ledger says (M7.20, M7.26).

Every sentence is built from integers, with Indian digit grouping (1,42,825.00) as the phone's
en-IN numbers have, and says what is wrong and what to do about it, never a code. The words are
here and not in the engines so that one rule has one sentence: the ledger, the dry run, the import
preview and the entry form all use these.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .kinds import PRICE_SCALE, QTY_SCALE
from .money import unit_price_u

RUPEE = chr(0x20B9)
#: Between the parts of one line ("... at 1,510.00 · gain 1,234.00").
DOT = " " + chr(0xB7) + " "
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

#: What each kind of entry is called in a sentence.
NOUNS = {
    "BUY": "purchase",
    "SELL": "sale",
    "RIGHTS": "rights purchase",
    "DIVIDEND": "dividend",
    "INTEREST": "interest entry",
    "FEE": "fee",
    "TRANSFER": "transfer",
    "SPLIT": "split",
    "BONUS": "bonus",
}
#: What a field is called when a form is missing it or has too much of it.
LABELS = {
    "account_id": "an account",
    "to_account_id": "a second account",
    "asset_id": "an asset",
    "qty_u": "a quantity",
    "price_u": "a price",
    "amount_paise": "an amount",
    "charges_paise": "charges",
    "tds_paise": "TDS",
    "value_paise": "a value",
    "ratio_num": "a ratio",
    "ratio_den": "a ratio",
    "lot_date": "an original purchase date",
}


def group_in(number: int) -> str:
    """Digits in the Indian grouping: 1428250 -> "14,28,250"."""
    text = str(abs(number))
    if len(text) > 3:
        head, tail = text[:-3], text[-3:]
        groups = []
        while head:
            groups.append(head[-2:])
            head = head[:-2]
        text = ",".join([*reversed(groups), tail])
    return f"-{text}" if number < 0 else text


def date(day: int) -> str:
    """20250312 -> "12 Mar 2025"."""
    return f"{day % 100} {MONTHS[day // 100 % 100 - 1]} {day // 10000}"


def rupees(paise: int) -> str:
    """142825 -> "₹1,428.25"; a loss is "-₹500.00"."""
    whole, part = divmod(abs(paise), 100)
    return f"{'-' if paise < 0 else ''}{RUPEE}{group_in(whole)}.{part:02d}"


def price(price_u: int) -> str:
    """A price with two decimals, or the four it needs: "₹1,428.25", "₹45.6789"."""
    whole, part = divmod(abs(price_u), PRICE_SCALE)
    decimals = f"{part:04d}".rstrip("0").ljust(2, "0")
    return f"{'-' if price_u < 0 else ''}{RUPEE}{group_in(whole)}.{decimals}"


def quantity(qty_u: int) -> str:
    """10 shares -> "10"; 109.459 fund units -> "109.459"."""
    whole, part = divmod(abs(qty_u), QTY_SCALE)
    decimals = f"{part:06d}".rstrip("0")
    sign = "-" if qty_u < 0 else ""
    return f"{sign}{group_in(whole)}" + (f".{decimals}" if decimals else "")


@dataclass(frozen=True, slots=True)
class Names:
    """What the owner calls the accounts and holdings a sentence names, by id.

    ``units`` says what one unit of a holding is ("shares" unless it is "units" or "grams").
    """

    accounts: Mapping[int, str] = field(default_factory=dict)
    assets: Mapping[int, str] = field(default_factory=dict)
    units: Mapping[int, str] = field(default_factory=dict)

    def account(self, account_id: int | None) -> str:
        return self.accounts.get(account_id) or "The account"

    def asset(self, asset_id: int | None) -> str:
        return self.assets.get(asset_id) or "this holding"

    def unit(self, asset_id: int | None) -> str:
        return self.units.get(asset_id) or "shares"


def short_of_units(
    names: Names,
    kind: str,
    account_id: int,
    asset_id: int,
    day: int,
    have_qty_u: int,
    want_qty_u: int,
) -> str:
    """A sale or a transfer of more than the account holds that day."""
    holds = "no" if have_qty_u == 0 else quantity(have_qty_u)
    what = "a sale" if kind == "SELL" else "a transfer"
    return (
        f"{names.account(account_id)} holds {holds} {names.asset(asset_id)} on {date(day)}, "
        f"so {what} of {quantity(want_qty_u)} cannot be saved. Add the missing purchase first."
    )


def not_held(names: Names, kind: str, account_id: int | None, asset_id: int, day: int) -> str:
    """A split or a bonus on a holding that the account does not hold on that day."""
    who = "No account holds" if account_id is None else f"{names.account(account_id)} holds no"
    return (
        f"{who} {names.asset(asset_id)} on {date(day)}, so a {NOUNS[kind]} cannot be saved. "
        "Check the date, or add the missing purchase first."
    )


def fraction_left(names: Names, account_id: int, asset_id: int, left_qty_u: int) -> str:
    """A split that would leave part of a share (or less than the asset's smallest unit)."""
    return (
        f"This split would leave {quantity(left_qty_u)} {names.unit(asset_id)} in "
        f"{names.account(account_id)}. Record the sale of the fraction first, then the split."
    )


#: What the XIRR line says when it has no figure (design 4.7, X4): nothing was paid in or nothing is
#: worth anything; or the money in and out has no single yearly rate (it paid in, took out at a
#: profit and then lost the lot).
XIRR_NEEDS_A_PURCHASE = "Return needs at least one purchase and a value."
XIRR_NO_RATE = "These purchases and sales do not give a single yearly return."
#: The time-weighted return waits for closing prices for two days.
TWR_NEEDS_PRICES = "TWR needs closing prices for at least two days."


def xirr_later(day: int) -> str:
    """The XIRR is a year of money away: "XIRR appears after one year (from 1 May 2027)."."""
    return f"XIRR appears after one year (from {date(day)})."


def twr_since(day: int) -> str:
    """The label of a time-weighted return: "TWR since 3 Nov 2026"."""
    return f"TWR since {date(day)}"


def knock_on(action: str, changed_kind: str, failing_kind: str, failing_day: int, unit: str) -> str:
    """Why a change is refused because of a later entry it would leave without units.

    ``action`` is "delete", "change" or "add" (a new entry that a later one cannot stand with).
    """
    verb = {"delete": "Deleting", "change": "Changing", "add": "Saving"}[action]
    fix = "Delete or change" if action == "delete" else "Change or delete"
    later = NOUNS[failing_kind]
    return (
        f"{verb} this {NOUNS[changed_kind]} would leave the {later} on {date(failing_day)} "
        f"without {unit}. {fix} that {later} first."
    )


def _lots(pieces) -> str:
    """The lots a sale or a transfer took: "From 10 Jan 2025: 10 at ₹1,428.25; from ..."."""
    return "; ".join(
        f"{'from' if n else 'From'} {date(piece.day)}: {quantity(piece.qty_u)} at "
        f"{price(unit_price_u(piece.cost_paise, piece.qty_u))}"
        for n, piece in enumerate(pieces)
    )


def describe(move) -> str:
    """What an entry did, in a line: the dry run's answer and the activity list's detail.

    ``move`` is a model.Move; only its attributes are read.
    """
    kind, entry = move.kind, move.entry
    if kind in ("BUY", "RIGHTS"):
        text = f"Cost {rupees(move.cost_paise)}"
        return text + (
            f" with {rupees(entry.charges_paise)} charges" if entry.charges_paise else ""
        )
    if kind == "SELL":
        gain = move.gain_paise
        if gain:
            result = f"gain {rupees(gain)}" if gain > 0 else f"loss {rupees(-gain)}"
        else:
            result = "no gain or loss"
        return _lots(move.used) + DOT + result
    if kind in ("DIVIDEND", "INTEREST"):
        text = f"Income {rupees(move.paise)}"
        return text + (f" before {rupees(entry.tds_paise)} TDS" if entry.tds_paise else "")
    if kind == "FEE":
        return f"Fee {rupees(move.paise)}"
    if kind == "SPLIT":
        after = sum(piece.qty_u for piece in move.used)
        num, den = entry.ratio_num, entry.ratio_den
        ratio = f"Each share becomes {num}" if den == 1 else f"{num} new for every {den} old"
        became = f"{quantity(after - move.qty_u)} become {quantity(after)}"
        return f"{ratio}{DOT}{became}{DOT}cost and dates kept"
    if kind == "BONUS":
        return f"Bonus {quantity(entry.qty_u)} at no cost"
    if entry.account_id is None:  # shares brought in from outside
        text = f"Cost {rupees(move.cost_paise)}"
        if entry.lot_date and entry.lot_date != entry.trade_date:
            text += f"{DOT}bought {date(entry.lot_date)}"
        if entry.value_paise is not None and entry.value_paise != move.cost_paise:
            text += f"{DOT}valued at {rupees(entry.value_paise)}"
        return text
    if entry.to_account_id is None:  # shares taken out
        return _lots(move.used) + DOT + "left at cost, no gain or loss"
    return _lots(move.used) + DOT + "cost and dates kept"
