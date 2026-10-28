"""Which holdings a view shows: account, kind of asset, tag, held over or under a year, as of a day
(M7.35).

A filter narrows the lines the holdings list, the totals and the bars are made from. Choices of one
kind are alternatives and different kinds all have to hold: "accounts 1 and 2, kinds equity, tag
income" is the equity holdings tagged income in either account.

* ``accounts`` keeps the lines of those accounts, ``classes`` the assets of those kinds, ``tags``
  the assets that carry at least one of those tags. Nothing chosen is everything.
* ``held`` works on the lots, not the holding: "over" keeps the units in lots bought more than a
  year before the day (days.held_over_year: the first anniversary itself is not yet more than a
  year) with the cost of exactly those lots, "under" the rest. A holding that has some of each is
  in both views, in part.
* ``as_of`` is a day: the caller replays the entries up to it and prices the lines at that day's
  closing prices. It is the day the lots are judged on, too.
* What a day's trades did (Today) is not worked out for lots or for a past day. A selection with
  ``today_off`` set says so, and its rows go out with ``today_mode`` none.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import NamedTuple

from . import words
from .allocation import Facts
from .days import held_over_year, is_day
from .kinds import ASSET_CLASSES, HELD_FILTERS, TAG_MAX, TAGS_MAX
from .model import Position
from .money import is_int
from .valuation import Line


class BadFilter(ValueError):
    """A filter that cannot be read; the message is for the trader."""


def _items(raw: Mapping, name: str) -> list:
    found = raw.get(name)
    if found is None:
        return []
    if found.__class__ not in (list, tuple):
        raise BadFilter(words.FILTER_SHAPE)
    return list(found)


@dataclass(frozen=True, slots=True)
class Filters:
    """The choices of a view (types.ts ``Filters``), in one order whoever wrote them."""

    accounts: tuple[int, ...] = ()
    classes: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    held: str | None = None
    as_of: int | None = None

    @classmethod
    def of(cls, raw: Mapping) -> Filters:
        """From a request or a saved view; a key it does not know is dropped, a value it cannot
        use is refused. Accounts are sorted, classes in the order of the kinds, tags sorted.

        The first thing wrong is the one said, taking accounts, kinds, tags, held and the day in
        that order."""
        if not isinstance(raw, Mapping):
            raise BadFilter(words.FILTER_SHAPE)
        accounts = _items(raw, "accounts")
        if not all(is_int(account) and account > 0 for account in accounts):
            raise BadFilter(words.FILTER_ACCOUNT)
        classes = _items(raw, "classes")
        if not all(kind in ASSET_CLASSES for kind in classes):
            raise BadFilter(words.FILTER_CLASS)
        tags = _items(raw, "tags")
        if not all(isinstance(tag, str) for tag in tags):
            raise BadFilter(words.FILTER_SHAPE)
        tags = {tag.strip() for tag in tags}
        if len(tags) > TAGS_MAX or any(not tag or len(tag) > TAG_MAX or "," in tag for tag in tags):
            raise BadFilter(words.filter_tags(TAG_MAX, TAGS_MAX))
        held, as_of = raw.get("held"), raw.get("as_of")
        if held is not None and held not in HELD_FILTERS:
            raise BadFilter(words.FILTER_HELD)
        if as_of is not None and not is_day(as_of):
            raise BadFilter(words.FILTER_DAY)
        return cls(
            tuple(sorted(set(accounts))),
            tuple(kind for kind in ASSET_CLASSES if kind in classes),
            tuple(sorted(tags)),
            held,
            as_of,
        )

    def as_dict(self) -> dict:
        return {
            "accounts": list(self.accounts),
            "classes": list(self.classes),
            "tags": list(self.tags),
            "held": self.held,
            "as_of": self.as_of,
        }

    @property
    def empty(self) -> bool:
        return not (self.accounts or self.classes or self.tags or self.held or self.as_of)

    @property
    def today_off(self) -> bool:
        """Today is not worked out for lots or for a day gone by."""
        return self.held is not None or self.as_of is not None

    def account_ok(self, account_id: int) -> bool:
        return not self.accounts or account_id in self.accounts

    def asset_ok(self, facts: Facts) -> bool:
        if self.classes and facts.asset_class not in self.classes:
            return False
        return not self.tags or any(tag in facts.tags for tag in self.tags)


class Selection(NamedTuple):
    """The lines a filter leaves, by asset, and whether Today means anything for them."""

    lines: dict[int, list[Line]]
    today_off: bool


def _lots(held: str, line: Line, position: Position | None, on: int) -> Line | None:
    """The line made of the lots held over (or under) a year on ``on``; None when none is.

    Its units and cost are those of the lots. It is as if they had been held since the open and
    nothing had been traded: Today is off for it, and this is the quietest thing to hand on.
    """
    if position is None:
        return None
    over = held == "over"
    chosen = [lot for lot in position.lots if held_over_year(lot.day, on) == over]
    qty_u = sum(lot.qty_u for lot in chosen)
    if not qty_u:
        return None
    cost = sum(lot.cost_paise for lot in chosen)
    return Line(line.account_id, qty_u, qty_u, cost, 0, 0, qty_u if over else 0)


def select(
    filters: Filters,
    lines: Mapping[int, Sequence[Line]],
    positions: Mapping[tuple[int | None, int | None], Position],
    facts: Mapping[int, Facts],
    today: int,
) -> Selection:
    """The lines (by asset, as daylines.lines_of gives them) that pass the filters.

    ``positions`` are the replayed ledger's, for the lots; ``facts`` name the class and tags of
    each asset (only read when a kind or a tag is chosen); ``today`` is the day the lots are
    judged on unless the filters give another.
    """
    on = today if filters.as_of is None else filters.as_of
    kept: dict[int, list[Line]] = {}
    for asset, found in lines.items():
        if (filters.classes or filters.tags) and not filters.asset_ok(facts[asset]):
            continue
        mine = [line for line in found if filters.account_ok(line.account_id)]
        if filters.held is not None:
            lots = (
                _lots(filters.held, line, positions.get((line.account_id, asset)), on)
                for line in mine
            )
            mine = [line for line in lots if line is not None]
        if mine:
            kept[asset] = mine
    return Selection(kept, filters.today_off)
