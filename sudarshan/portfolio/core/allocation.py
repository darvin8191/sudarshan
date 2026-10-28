"""How a tab's value divides: allocation by class, sector, cap size, account, member and tag (M7.33).

A bar for each class, sector, cap size, account, member or tag says how much of the tab's value
sits there and what share of the whole that is (design 4.8). It adds up the same lines the holdings
list does, at the same prices: a line is worth its units at the price, or its cost while it has
none (``valuation.line_value``), so the bars of class, sector, cap size, account and member add up
to the tab's value to the paisa, and a holding without a price does not make its class look empty.

* A share is the bar's value over the tab's value, rounded on its own to four decimals, half away
  from zero. Shares are not adjusted to add up to 100: each is the number it says it is.
* A holding with several tags is in the bar of each with all its value, so the tag bars add up to
  more than the tab. A holding without a sector, cap size or tag is in a bar of its own, with the
  key ``""``, so that a bar that is tapped can always be matched to its holdings.
* ``holdings`` is how many different assets are in the bar: the same share held in two accounts is
  one holding in a class and in a member's bar and one in each of the two accounts' bars.
* Largest value first; bars of equal value by label, then key, so that the order never depends on
  the order the holdings arrived in.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import NamedTuple

from . import words
from .kinds import ALLOCATION_BY, ASSET_CLASS_LABELS, ASSET_CLASSES, MCAPS
from .money import format_pct, pct
from .valuation import Row, line_value

#: AMFI's headline for a scheme (the words before the dash inside the brackets) and the class it is.
_MF_CLASSES = {"equity scheme": "mf_equity", "debt scheme": "mf_debt", "hybrid scheme": "mf_hybrid"}


def mf_class(header: str | None) -> str:
    """The class of a mutual fund from AMFI's section header.

    "Open Ended Schemes(Equity Scheme - Large Cap Fund)" is MF equity, a debt scheme MF debt and a
    hybrid scheme MF hybrid. Anything else (a solution oriented scheme, an index fund, a fund of
    funds, a header without brackets) is MF other: a guess would put it in a bar it does not
    belong to, and the owner can set the class of an asset himself.
    """
    text = header if isinstance(header, str) else ""
    start, stop = text.find("("), text.rfind(")")
    if start < 0 or stop < start:
        return "mf_other"
    kind = text[start + 1 : stop].split("-", 1)[0]
    return _MF_CLASSES.get(" ".join(kind.lower().split()), "mf_other")


@dataclass(frozen=True, slots=True)
class Facts:
    """What the bars and the filters need to know of an asset (types.ts ``AssetRef``)."""

    asset_id: int
    asset_class: str
    sector: str | None = None
    mcap: str | None = None
    tags: tuple[str, ...] = ()

    @classmethod
    def of(cls, asset: Mapping) -> Facts:
        """From an ``AssetRef`` or an ``Asset``; a blank sector or cap size is none, and a tag
        written twice is one. A class or a cap size the contract does not have is refused."""
        asset_class, mcap = asset["asset_class"], asset.get("mcap") or None
        if asset_class not in ASSET_CLASSES or (mcap is not None and mcap not in MCAPS):
            raise ValueError(f"no such class or cap size: {asset_class!r}, {mcap!r}")
        tags = tuple(dict.fromkeys(asset.get("tags") or ()))
        return cls(asset["id"], asset_class, asset.get("sector") or None, mcap, tags)


class Owner(NamedTuple):
    """An account as the bars of accounts and members see it."""

    name: str
    member_id: int


class Holding(NamedTuple):
    """One asset with the row that prices it (types.ts ``HoldingRow``)."""

    facts: Facts
    row: Row

    @classmethod
    def of(cls, row: Mapping) -> Holding:
        return cls(Facts.of(row["asset"]), Row.of(row))


@dataclass(frozen=True, slots=True)
class AllocationRow:
    """One bar (types.ts ``AllocationRow``)."""

    key: str
    label: str
    value_paise: int
    pct: str
    holdings: int

    def as_dict(self) -> dict:
        return asdict(self)


def _bars(
    by: str, facts: Facts, account: int, accounts: Mapping[int, Owner], members: Mapping[int, str]
) -> list[tuple[str, str]]:
    """The ``(key, label)`` of each bar a line is in."""
    if by == "class":
        return [(facts.asset_class, ASSET_CLASS_LABELS[facts.asset_class])]
    if by == "sector":
        return [(facts.sector, facts.sector)] if facts.sector else [("", words.NO_SECTOR)]
    if by == "mcap":
        return [(facts.mcap, words.MCAP_LABELS[facts.mcap])] if facts.mcap else [("", words.NO_CAP)]
    if by == "account":
        return [(str(account), accounts[account].name)]
    if by == "member":
        member = accounts[account].member_id
        return [(str(member), members[member])]
    return [(tag, tag) for tag in facts.tags] or [("", words.NO_TAG)]


def allocate(
    by: str,
    holdings: Iterable[tuple[Facts, Row]],
    accounts: Mapping[int, Owner],
    members: Mapping[int, str],
) -> list[AllocationRow]:
    """The bars of one kind, largest first.

    ``holdings`` are the assets of the tab with their rows (a row nobody holds is skipped);
    ``accounts`` and ``members`` name every account and member a held line points to.
    """
    if by not in ALLOCATION_BY:
        raise ValueError(f"no allocation by {by!r}")
    total = 0
    bars: dict[str, list] = {}
    for facts, row in holdings:
        for line in row.lines:
            if not line.qty_u:
                continue
            value = line_value(line, row.price_u)
            total += value
            for key, label in _bars(by, facts, line.account_id, accounts, members):
                bar = bars.setdefault(key, [label, 0, set()])
                bar[1] += value
                bar[2].add(facts.asset_id)
    found = [
        AllocationRow(key, label, value, pct(value, total) if total else format_pct(0), len(assets))
        for key, (label, value, assets) in bars.items()
    ]
    found.sort(key=lambda bar: (-bar.value_paise, bar.label, bar.key))
    return found


def allocate_all(
    holdings: Iterable[tuple[Facts, Row]],
    accounts: Mapping[int, Owner],
    members: Mapping[int, str],
) -> dict[str, list[dict]]:
    """Every kind of bar, as the tab view carries them (types.ts ``TabView.allocation``)."""
    held = list(holdings)
    return {
        by: [bar.as_dict() for bar in allocate(by, held, accounts, members)] for by in ALLOCATION_BY
    }
