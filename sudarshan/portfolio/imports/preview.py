"""What an import would do to a tab (M7.22, design 4.11): the preview every format shares.

A format (generic.py, later the Zerodha tradebook) reads a file into **drafts**, one per row: the
entry the row means, in the contract's names, or the sentence for why the row cannot be read.
``finish`` does the rest, the same for every format:

1. Each draft is made an entry and checked for form (``model.Entry.of``), and a quantity is held to
   the decimals its holding takes (shares are whole numbers).
2. Each row gets its **duplicate key**: the format's own id when it has one (a broker's trade id),
   else a hash of what the trade is (kind, accounts, day, holding, quantity, amount and ratio)
   plus the occurrence number of that content in the file. Two identical trades in one file are
   two keys, so both import; the same file read again gives the same keys, so nothing imports
   twice. A key the tab already holds, or one an earlier row of the file has, is a duplicate.
3. The rows that are left go through the ledger together with the tab's entries (``fit``), holding
   by holding, in the order the ledger applies them whatever order the file is in: a row the
   ledger refuses (a sale of shares the account does not hold that day) is left out with the
   ledger's own sentence, and the rows after it are judged without it. Should a row make an entry
   already in the tab impossible, none of that holding's rows go in, and each says why.

The answer is a ``Preview``: a ``Row`` for each row of the file, new, dup or bad, with the words
for it, the normalised entry it means and its key. Nothing here reads a file, a clock or a store:
what the tab holds, what a symbol means and what today is arrive in a ``Context``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import NamedTuple

from ..core import words
from ..core.kinds import IMPORT_PREVIEW_ROWS
from ..core.ledger import HANDLERS, ordered
from ..core.model import BadEntry, Entry, Ledger, Refusal
from ..core.money import step_ok
from ..core.words import Names
from .csvutil import parse_day

STATUSES = ("new", "dup", "bad")


class RowProblem(ValueError):
    """A row that cannot be read; the message is the sentence, without the row's number."""


class Draft(NamedTuple):
    """One row of a file as a format read it: ``entry`` (the contract's ActivityInput) or the
    ``problem`` in a sentence that names the row; the format's own ``key``, if it has one; and a
    ``remark`` (a sentence without the row's number) for a row that is used but deserves a note."""

    row: int
    entry: dict | None = None
    problem: str | None = None
    key: str | None = None
    remark: str | None = None


@dataclass(frozen=True, slots=True)
class Row:
    """One row of the preview (types.ts ``ImportRow``, with the key the service keeps)."""

    row: int
    status: str
    words: str | None
    entry: dict | None
    key: str | None

    def wire(self) -> dict:
        return {"row": self.row, "status": self.status, "words": self.words, "entry": self.entry}


@dataclass(frozen=True, slots=True)
class Preview:
    """The rows of a file, and the columns of its header that the format does not use."""

    rows: tuple[Row, ...]
    unused: tuple[str, ...] = ()

    def count(self, status: str) -> int:
        return sum(1 for row in self.rows if row.status == status)

    @property
    def summary(self) -> str:
        """The counts in a line: 42 new, 3 already in this tab, 2 with problems. A column the
        format has no use for is named after them, so that a misspelt one is not a mystery."""
        line = words.import_summary(self.count("new"), self.count("dup"), self.count("bad"))
        if not self.unused:
            return line
        stop = "" if line.endswith(".") else "."
        return f"{line}{stop} {words.import_unused(self.unused)}"

    def additions(self) -> list[Row]:
        """The rows a commit would add, in file order."""
        return [row for row in self.rows if row.status == "new"]

    def listing(self, limit: int = IMPORT_PREVIEW_ROWS) -> tuple[list[dict], bool]:
        """The rows the phone is shown, problems first, then the others in file order, at most
        ``limit`` of them; and whether there were more."""
        shown = [row for row in self.rows if row.status == "bad"]
        shown += [row for row in self.rows if row.status != "bad"]
        return [row.wire() for row in shown[:limit]], len(shown) > limit


def _plain(name: str) -> str:
    return " ".join(name.lower().split())


@dataclass(frozen=True)
class Context:
    """Everything an import needs to know that is not in the file.

    * ``today``: the day, as YYYYMMDD in IST; a row dated after it is refused.
    * ``resolve(symbol, exchange, isin)``: the id of the holding the row names, or None. It
      compares without regard to case and matches aliases first; the cells arrive as written.
    * ``accounts``: id to name, for every account a row may name; a name is read without regard to
      case and spacing. ``default_account`` takes the rows that name none.
    * ``existing``: the tab's entries (``model.Entry``, saved ones with their ``id`` and ``seq``);
      ``keys``: the duplicate key of each entry an earlier import added, and the day it did.
    * ``names`` and ``decimals``: what the sentences call accounts and holdings, and how many
      decimals a quantity of each holding may have (whole shares unless said otherwise).
    """

    today: int
    resolve: Callable[[str, str, str], int | None]
    accounts: Mapping[int, str]
    default_account: int | None = None
    existing: Sequence[Entry] = ()
    keys: Mapping[str, int] = field(default_factory=dict)
    names: Names = field(default_factory=Names)
    decimals: Mapping[int, int] = field(default_factory=dict)
    _named: dict = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        named: dict[str, list[int]] = {}
        for account_id, name in self.accounts.items():
            named.setdefault(_plain(name), []).append(account_id)
        object.__setattr__(self, "_named", named)

    def day(self, text: str) -> int:
        """A date cell as a day up to today."""
        found = parse_day(text)
        if found is None:
            raise RowProblem(words.row_date(text))
        if found > self.today:
            raise RowProblem(words.row_future(found))
        return found

    def account(self, name: str, *, default: bool = True) -> int | None:
        """The account a cell names. A cell left empty is the default account, or None when
        ``default`` is False (a split's every account, a transfer's missing second account)."""
        if not name:
            if not default:
                return None
            if self.default_account is None:
                raise RowProblem(words.ROW_NO_ACCOUNT)
            return self.default_account
        found = self._named.get(_plain(name), [])
        if not found:
            raise RowProblem(words.row_account(name))
        if len(found) > 1:
            raise RowProblem(words.row_account_twice(name))
        return found[0]

    def asset(self, symbol: str, exchange: str, isin: str, *, required: bool = True) -> int | None:
        """The holding the cells name, or None when none is named and none is needed."""
        if not (symbol or isin):
            if required:
                raise RowProblem(words.ROW_NO_ASSET)
            return None
        found = self.resolve(symbol, exchange, isin)
        if found is None:
            raise RowProblem(words.row_symbol(symbol) if symbol else words.row_isin(isin))
        return found


def entry_dict(kind: str, trade_date: int, account_id: int | None, **fields) -> dict:
    """The entry a row means in the contract's names, leaving out what the row does not give."""
    found = {"kind": kind, "trade_date": trade_date, "account_id": account_id}
    found.update((name, value) for name, value in fields.items() if value is not None)
    return found


def content_hash(entry: Entry) -> str:
    """What a trade is, as 32 hex digits: kind, the account it leaves and the account it reaches,
    the day, the holding, the quantity, the amount (quantity times price when none is given) and
    the split ratio. What a trade is not (charges, TDS, price, note, original purchase date,
    value) is left out, so a file read again with a corrected charge is the same trade.

    The duplicate key of a trade without an id of its own is ``prefix:hash:occurrence``. Keys are
    stored with the entries they added: this encoding must never change.
    """
    parts = (
        entry.kind,
        entry.account_id,
        entry.to_account_id,
        entry.trade_date,
        entry.asset_id,
        entry.qty_u,
        entry.amount,
        entry.ratio_num,
        entry.ratio_den,
    )
    basis = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(basis.encode("ascii")).hexdigest()[:32]


def fit(
    existing: Sequence[Entry],
    incoming: Sequence[Entry],
    names: Names | None = None,
    decimals: Mapping[int, int] | None = None,
) -> dict[int, str]:
    """The sentence for each incoming entry that cannot join the tab; the others are not in it.

    ``incoming`` carry ids that are theirs alone (the preview uses minus the row) and a ``seq``
    above every ``existing`` one, so that the ledger applies them after what is there on the
    same day, in file order. Each holding is replayed on its own, which is all a refusal depends
    on: an entry the ledger refuses is left out (its own sentence) and the rest go on without it.
    An entry of the tab that no longer stands, with the incoming rows in, means those rows cannot
    be told from the cause: none of the holding's rows go in, and each says so.
    """
    held: dict[int, list[Entry]] = {}
    for entry in existing:
        if entry.asset_id is not None:
            held.setdefault(entry.asset_id, []).append(entry)
    coming: dict[int, list[Entry]] = {}
    for entry in incoming:
        if entry.asset_id is not None:
            coming.setdefault(entry.asset_id, []).append(entry)
    verdict: dict[int, str] = {}
    for asset, rows in coming.items():
        ours = {entry.id for entry in rows}
        ledger = Ledger(names, decimals)
        clash = None
        for entry in ordered([*held.get(asset, ()), *rows]):
            try:
                HANDLERS[entry.kind](ledger, entry)
            except Refusal as refusal:
                if entry.id in ours:
                    verdict[entry.id] = refusal.message
                    continue
                clash = refusal
                break
        if clash is not None:
            who = ledger.names
            sentence = words.import_clash(
                who.asset(asset),
                clash.facts["kind"],
                clash.facts["day"],
                clash.reason,
                who.unit(asset),
            )
            for entry in rows:
                verdict.setdefault(entry.id, sentence)
    return verdict


def finish(drafts: Sequence[Draft], ctx: Context, prefix: str = "generic") -> Preview:
    """The preview of a file's drafts (see the module's account). ``prefix`` names the format in
    the keys of rows that have none of their own."""
    rows: dict[int, Row] = {}

    def refuse(draft: Draft, sentence: str, key: str | None = None) -> None:
        rows[draft.row] = Row(draft.row, "bad", sentence, draft.entry, key)

    readable: list[tuple[Draft, Entry]] = []
    for draft in drafts:
        if draft.problem is not None:
            refuse(draft, draft.problem)
            continue
        try:
            entry = Entry.of(draft.entry)
        except BadEntry as bad:
            refuse(draft, words.row_says(draft.row, str(bad)))
            continue
        asset = entry.asset_id
        if entry.qty_u is not None and asset is not None:
            most = ctx.decimals.get(asset, 0)
            if not step_ok(entry.qty_u, most):
                refuse(
                    draft, words.row_says(draft.row, words.row_step(ctx.names.asset(asset), most))
                )
                continue
        readable.append((draft, entry))

    occurrences: dict[str, int] = {}
    first: dict[str, int] = {}
    candidates: list[tuple[Draft, Entry, str]] = []
    for draft, entry in readable:
        key = draft.key
        if key is None:
            digest = content_hash(entry)
            occurrences[digest] = occurrences.get(digest, 0) + 1
            key = f"{prefix}:{digest}:{occurrences[digest]}"
        if key in ctx.keys:
            said = words.row_dup(draft.row, ctx.keys[key], ctx.today)
            rows[draft.row] = Row(draft.row, "dup", said, draft.entry, key)
        elif key in first:
            said = words.row_repeat(draft.row, first[key])
            rows[draft.row] = Row(draft.row, "dup", said, draft.entry, key)
        else:
            first[key] = draft.row
            candidates.append((draft, entry, key))

    start = 1 + max((entry.seq for entry in ctx.existing), default=0)
    incoming = [
        replace(entry, id=-draft.row, seq=start + place)
        for place, (draft, entry, _) in enumerate(candidates)
    ]
    verdict = fit(ctx.existing, incoming, ctx.names, ctx.decimals)
    for draft, _, key in candidates:
        sentence = verdict.get(-draft.row)
        if sentence is not None:
            refuse(draft, words.row_says(draft.row, sentence), key)
        else:
            said = None if draft.remark is None else words.row_says(draft.row, draft.remark)
            rows[draft.row] = Row(draft.row, "new", said, draft.entry, key)
    return Preview(tuple(rows[number] for number in sorted(rows)))
