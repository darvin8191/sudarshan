"""The catalogue of built-in lists, found by discovery (M8.8, W12).

Pure: no Flask, no OpenAlgo, no I/O, no clock, no logging. A source module of this package
(``master_lists.py``, and ``typed_lists.py`` of W13) is found with ``pkgutil.iter_modules`` and names
its lists in ``ENTRIES``, so a new source adds a file and edits no shared line. Each ``Entry``
carries the list's id (the ``<id>`` of ``preset:<id>``), its name, its kind (``master``: derived
from today's master; ``typed``: a file a person typed, W13), the rule or file it comes from and two
pure functions:

``prepare(index) -> Prepared``
    Everything about the list that holds until OpenAlgo publishes the next master: references to
    the master's own rows (never copies) and the names the master lacks. The caller keeps it with
    the master's generation and drops it with it.

``members(prepared, now_ms, closes) -> Built``
    The rows now. A list that does not depend on the clock returns what was prepared; index
    futures pick the first future whose expiry close is still ahead (3.7's roll), ``closes`` being
    the calendar's close in epoch ms for an expiry date (``dynamic.close_ms``: 15:30 IST when
    there is none).

``merge`` makes the ``View`` record. Built-in lists are locked, have no sections and no follow
rows, and carry version "0": nothing is written against them.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from typing import Any, NamedTuple

from sudarshan.watchlists import contract
from sudarshan.watchlists import view as merge_view
from sudarshan.watchlists.words import presets as words


class Prepared(NamedTuple):
    """A list prepared against one master generation: references only."""

    rows: tuple = ()  # the master's own rows, in the list's order
    missing: tuple[str, ...] = ()  # ``EXCHANGE:SYMBOL`` of names the master lacks
    futures: tuple = ()  # index futures: per underlying, the (iso, row) pairs, soonest first
    as_of: str | None = None  # a typed list's own date; None: the master's (a typed one: unknown)
    notes: tuple[str, ...] = ()  # a typed file's format problems, in words

    def expiries(self) -> set[str]:
        """Every expiry date (ISO) the list might roll between: what the calendar may be asked."""
        return {iso for _, listed in self.futures for iso, _ in listed}


class Built(NamedTuple):
    """The rows a list holds now."""

    rows: tuple
    missing: tuple[str, ...] = ()
    #: Epoch ms at which a row would be a different contract (index futures); None for the rest.
    valid_until: int | None = None
    notes: tuple[str, ...] = ()  # a typed file's format problems, in words


class Entry(NamedTuple):
    id: str
    name: str
    kind: str  # master, typed
    rule: str  # the rule (master) or the file (typed) the list comes from
    prepare: Callable[[Any], Prepared]
    members: Callable[[Prepared, int, dict], Built]
    review: bool = False  # a typed list that is checked at each index review
    #: A typed list is offered only while its file is there; None: always offered.
    offered: Callable[[], bool] | None = None
    #: What identifies the file's content now (path, modification time, size), or None when there
    #: is none. A prepared list is kept with the master's generation *and* this stamp, so a file
    #: changed on disk is read again at the next call without a restart.
    stamp: Callable[[], object] | None = None

    @property
    def ref(self) -> str:
        return f"preset:{self.id}"


#: The source modules, listed once at import (they are pure and small, so imported with it).
_MODULES = tuple(
    importlib.import_module(f"{__name__}.{info.name}") for info in pkgutil.iter_modules(__path__)
)


def _collect() -> tuple[Entry, ...]:
    found: list[tuple[int, int, Entry]] = []
    for module in _MODULES:
        order = getattr(module, "ORDER", 100)
        found += [(order, n, e) for n, e in enumerate(getattr(module, "ENTRIES", ()))]
    found.sort(key=lambda t: t[:2])
    return tuple(e for _, _, e in found)


ALL: tuple[Entry, ...] = _collect()
BY_ID = {e.id: e for e in ALL}


def catalogue() -> tuple[Entry, ...]:
    """The lists offered now, in catalogue order: every master-derived list, and each typed list
    whose file is there (W13). Asked at each call, so a file added or removed shows at once."""
    return tuple(e for e in ALL if e.offered is None or e.offered())


def __getattr__(name: str):
    """``presets.CATALOGUE`` is the lists offered now (a typed list's file may come and go)."""
    if name == "CATALOGUE":
        return catalogue()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def entry(ident: str) -> Entry | None:
    """The list ``preset:<ident>`` when it is offered, else None."""
    found = BY_ID.get(ident)
    return found if found is not None and (found.offered is None or found.offered()) else None


def lines(e: Entry, built: Built, as_of: str | None) -> list[str]:
    """The words over the rows: the lock, where the list comes from, the window and the names it
    could not find."""
    if e.kind == "typed":  # the lock and the file's own date, in one line
        out = [words.typed_locked_line(e.name, len(built.rows), as_of)]
    else:
        out = [words.locked_line(e.name, len(built.rows)), words.source_line(e.rule, as_of)]
    if len(built.rows) > contract.LIMITS["list_items"]:
        out.append(words.WINDOW)
    if built.missing:
        out.append(words.missing_line([k.partition(":")[2] for k in built.missing], e.review))
    return out + list(built.notes)


def merge(
    e: Entry,
    built: Built,
    *,
    as_of: str | None,
    marks: dict,
    isin: Callable[[Any], str | None],
    pref: dict | None = None,
) -> dict:
    """The ``View`` record of ``preset:<id>``. ``marks`` is ``{(exchange, symbol): (flag,
    has_note)}`` of the owner's flags and notes; the note itself is never read."""
    rows = []
    for row in built.rows:
        flag, noted = marks.get((row.exchange, row.symbol), (None, False))
        rows.append({
            "item": None,
            "contract": merge_view.contract(row.exchange, row.symbol, row, isin(row)),
            "section_id": None,
            "flag": flag,
            "has_note": bool(noted),
            "expired": False,  # the list is made from today's master
            "metric": None,
        })  # fmt: skip
    return {
        "ref": e.ref,
        "kind": "preset",
        "name": e.name,
        "version": "0",
        "locked": True,
        "as_of": as_of,
        "lines": lines(e, built, as_of),
        "total": len(rows),
        "missing": list(built.missing),
        "sections": [],
        "rows": rows,
        "follow": [],
        "pref": merge_view._pref(pref),
    }
