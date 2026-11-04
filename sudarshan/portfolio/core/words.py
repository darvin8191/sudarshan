"""The owner's words for dates, money and quantities, and for what the ledger says (M7.20, M7.26).

Every sentence is built from integers, with Indian digit grouping (1,42,825.00) as the phone's
en-IN numbers have, and says what is wrong and what to do about it, never a code. The words are
here and not in the engines so that one rule has one sentence: the ledger, the dry run, the import
preview and the entry form all use these.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .kinds import IMPORT_ACTIONS, PRICE_SCALE, QTY_SCALE, WORDS
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


def date_short(day: int, today: int) -> str:
    """A day without its year when it is this year's: "28 Oct", or "28 Oct 2025"."""
    text = date(day)
    return text.rpartition(" ")[0] if day // 10000 == today // 10000 else text


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


#: The daily value chart waits for closing prices (design 5.6).
SERIES_WAITING = "Daily history starts once every holding here has a closing price."


def series_from(day: int) -> str:
    """Where the daily history begins when it cannot begin with the tab (design 5.6)."""
    return f"Daily history from {date(day)}, the first day every holding here had a closing price."


#: What goes with every index comparison, and what goes with one that is only approximate (design
#: 4.7): the index is a price index, and it may not have been able to pay every withdrawal.
DIVIDENDS_NOT_INCLUDED = "Dividends not included"
INDEX_COULD_NOT_PAY = (
    "The index could not have paid every withdrawal; the comparison is approximate."
)


def index_starts(label: str, day: int) -> str:
    """The index has no closing price for the day of some entry or for the start of the TWR."""
    return f"{label} prices start {date(day)}; this comparison needs every entry after that."


def index_missing(label: str) -> str:
    """No closing price of the index has been downloaded yet."""
    return f"{label} prices are not available yet; the comparison appears when they are."


#: The names of the bars that hold what has no sector, no cap size or no tag (design 4.8), and the
#: cap sizes an asset can carry.
MCAP_LABELS = {"large": "Large cap", "mid": "Mid cap", "small": "Small cap"}
NO_SECTOR = "No sector set"
NO_CAP = "No cap size set"
NO_TAG = "No tag"

#: A filter that cannot be read (design 4.8). The phone offers only choices, so these are for a
#: view saved by an older release or a request made by hand.
FILTER_SHAPE = (
    "This filter cannot be read. Choose accounts, kinds of asset and tags from the lists."
)
FILTER_ACCOUNT = "Choose the accounts from the list."
FILTER_CLASS = "Choose the kinds of asset from the list."
FILTER_HELD = "Choose held over a year or held under a year."
FILTER_DAY = "Enter a valid date."


def filter_tags(longest: int, most: int) -> str:
    return (
        f"A tag is 1 to {longest} characters with no comma, and a filter takes at most {most} tags."
    )


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


# ---- importing a file of entries (design 4.11) --------------------------------------------------
#: A file that cannot be read at all, and what a row says when it cannot be used. Every sentence
#: names the row (the header is row 1) and what to change, and never a code or a cell's whole
#: text: ``shown`` keeps what a cell echoes short.
IMPORT_EMPTY = "The file is empty. Choose a CSV file whose first row names the columns."
IMPORT_UNREADABLE = (
    "This file could not be read as a CSV table. "
    "Save it as CSV (UTF-8) from your spreadsheet and try again."
)
ROW_CELLS = (
    "This row has more columns than the first row. A note with a comma needs quotes around it."
)
ROW_NO_ACCOUNT = "No account is named. Choose an account for the rows that do not name one."
ROW_NO_SECOND_ACCOUNT = (
    "A transfer between two accounts needs the second account in the to_account column."
)
ROW_ONE_ACCOUNT = (
    "A transfer from outside or to outside takes one account. Leave the to_account column empty."
)
ROW_NO_ASSET = "No share or fund is named. Fill in the symbol or the ISIN."


def shown(text: str, longest: int = 30) -> str:
    """What a sentence echoes of a cell: one line, and cut short if it is long."""
    flat = " ".join(text.split())
    return flat if len(flat) <= longest else flat[: longest - 3] + "..."


def _list(names: list[str]) -> str:
    """Names quoted and joined: 'date'; 'date' and 'action'; 'a', 'b' and 'c'."""
    quoted = [f"'{name}'" for name in names]
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " and " + quoted[-1]


def import_columns(missing: list[str]) -> str:
    """The first row does not name a column the format cannot do without."""
    verb = "column is" if len(missing) == 1 else "columns are"
    return (
        f"The first row of the file must name its columns, and the {_list(missing)} {verb} needed."
    )


def import_column_twice(name: str) -> str:
    return f"The column '{name}' appears twice in the first row. Keep one of them."


def import_too_many_rows(most: int) -> str:
    return (
        f"This file has more than {group_in(most)} rows. "
        "Download it in shorter date ranges and import each part."
    )


def row_says(number: int, sentence: str) -> str:
    """A sentence about a row of the file, led by its number (the header is row 1)."""
    return f"Row {number}: {sentence}"


def row_date(text: str) -> str:
    return (
        f"The date '{shown(text)}' is not a real date. "
        "Write it as 2026-03-10, 10-03-2026 or 10/03/2026 (day first)."
    )


def row_future(day: int) -> str:
    return f"{date(day)} is after today. {WORDS['future_date']}"


def row_action(text: str) -> str:
    allowed = ", ".join(IMPORT_ACTIONS[:-1]) + " or " + IMPORT_ACTIONS[-1]
    return f"The action '{shown(text)}' is not one this file can use. Use {allowed}."


def row_number(label: str, text: str) -> str:
    return (
        f"The {label} '{shown(text)}' is not a number. "
        "Write it with digits and a decimal point only, without commas or a currency sign."
    )


def row_negative(label: str, text: str) -> str:
    hint = " The action says whether it is a purchase or a sale." if label == "quantity" else ""
    return f"The {label} '{shown(text)}' cannot be negative.{hint}"


def row_decimals(label: str, text: str, most: int) -> str:
    return f"The {label} '{shown(text)}' has more than {most} decimals."


def row_ratio(text: str) -> str:
    return (
        f"The ratio '{shown(text)}' must be two whole numbers such as 5:1 (each share becomes 5) "
        "or 1:10 (one new for every ten old)."
    )


def row_note(most: int) -> str:
    return f"The note is longer than {most} characters. Shorten it."


def row_account(name: str) -> str:
    return (
        f"There is no account called '{shown(name)}' in this portfolio. "
        "Check the name, or add the account first."
    )


def row_account_twice(name: str) -> str:
    return f"More than one account is called '{shown(name)}'. Rename one of them first."


def row_symbol(symbol: str) -> str:
    return f"{shown(symbol)} is not in today's instrument list. Check the symbol."


def row_isin(isin: str) -> str:
    return f"The ISIN {shown(isin)} is not in today's instrument list. Check the ISIN."


def row_step(asset: str, decimals: int) -> str:
    """A quantity with more decimals than the holding takes."""
    if decimals == 0:
        return WORDS["whole_shares"]
    return f"A quantity of {asset} takes at most {decimals} decimals."


def row_amount(amount_paise: int, computed_paise: int) -> str:
    """A note on a row that gives an amount and a quantity and price that do not agree."""
    return (
        f"The amount {rupees(amount_paise)} is used, "
        f"not quantity times price ({rupees(computed_paise)})."
    )


def row_dup(number: int, imported: int, today: int) -> str:
    return f"Row {number} is already in this tab (imported {date_short(imported, today)})."


def row_repeat(number: int, first: int) -> str:
    return f"Row {number} is the same trade as row {first}, which is in this file too."


def import_clash(asset: str, kind: str, day: int, reason: str, unit: str) -> str:
    """The rows of one holding cannot join the tab: an entry already there would no longer stand.

    ``kind`` and ``day`` name that entry; ``reason`` is the refusal's (short, not_held, fraction).
    """
    breaks = (
        f"make the {NOUNS[kind]} on {date(day)} leave part of a share"
        if reason == "fraction"
        else f"leave the {NOUNS[kind]} on {date(day)} without {unit}"
    )
    return (
        f"The rows for {asset} do not fit the entries already in this tab: they would {breaks}. "
        "None of them is imported. Check whether the same trades are already in this tab, "
        "or fix that entry first."
    )


def import_summary(new: int, dup: int, bad: int) -> str:
    """The counts in a line (42 new, 3 already in this tab, 2 with problems), zeros left out."""
    parts = []
    if new:
        parts.append(f"{group_in(new)} new")
    if dup:
        parts.append(f"{group_in(dup)} already in this tab")
    if bad:
        parts.append("1 with a problem" if bad == 1 else f"{group_in(bad)} with problems")
    return ", ".join(parts) or "The file has no rows."


def import_unused(names: Sequence[str]) -> str:
    """The columns of the file that nothing is read from (a misspelt name is the usual cause)."""
    return f"Not used: {', '.join(shown(name, 20) for name in names)}."
