"""What the existing ops need of built-in lists (M8.8, W12): the source of a copy, the lock.

A built-in list (``preset:<id>``) is locked: no add, remove or arrange. The ops that write take a
list's number, not a ref, so none of them can be pointed at a built-in list; the one write that
names one is ``copy``, as its source, and a ``move`` would take symbols out of it, which is
refused here in words. Sort, flags, notes and copy work (``pref`` and ``mark`` already accept a
``preset:<id>`` ref and keep their rows by ref and by symbol).

``copy_source`` answers what ``ops/copy.py`` reads from a source: the symbols picked, in the
order the page sent them (``keys``; the page's order is the order shown, which the server cannot
know for a sort by price or change), or the whole list in the list's own order when no ``keys``
are sent. A list holds 250 symbols: over that the first 250 are copied and the answer says so.
"""

from __future__ import annotations

from sudarshan.watchlists import contract, refs
from sudarshan.watchlists.ops import OpError
from sudarshan.watchlists.words import arrange as arrange_words
from sudarshan.watchlists.words import presets as words

#: The ``src`` of a copy whose source is a built-in list: no own list has a negative id.
BUILTIN = -1


def is_preset(spec: dict) -> bool:
    parts = refs.split(spec.get("ref", ""))
    return parts is not None and parts[0] == "preset"


def refuse_locked(field: str = "ref") -> OpError:
    """The refusal for a change asked of a built-in list."""
    return OpError("invalid", words.LOCKED, field)


def copy_source(spec: dict) -> tuple[int, dict, list, bool]:
    """``(BUILTIN, {name, items, note}, picked keys, False)``, as ``copy._source`` answers."""
    from sudarshan.watchlists.refs import preset as reader

    _, ident = refs.split(spec["ref"])
    if "section_id" in spec:
        raise OpError("invalid", words.NO_SECTION, "from")
    e = reader.entry_of(ident)
    built, _ = reader.built_now(e)
    held = {(r.exchange, r.symbol) for r in built.rows}
    if "keys" in spec:
        picked = []
        for text in spec["keys"]:
            exchange, _, symbol = text.partition(":")
            if (exchange, symbol) not in held:
                raise OpError("invalid", field="from")
            picked.append((exchange, symbol))
        picked = list(dict.fromkeys(picked))
        if not picked:
            raise OpError("invalid", arrange_words.PICK_NOTHING, "from")
    else:
        picked = [(r.exchange, r.symbol) for r in built.rows]
    cap, total, note = contract.LIMITS["list_items"], len(picked), None
    if total > cap:
        picked, note = picked[:cap], words.truncated(cap, total)
    return BUILTIN, {"name": e.name, "items": [], "note": note}, picked, False
