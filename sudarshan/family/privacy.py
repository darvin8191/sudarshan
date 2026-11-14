"""What a family member is not shown, applied by the family service on the way out (M2.8; design 3,
2.4; cross-design Y6, Y24).

The one table is ``privacy_fields.json`` beside this file: each of the four categories (``pnl``,
``qty``, ``funds``, ``orders``) names the fields it hides, by name or by a ``*suffix`` pattern, at
any depth of an answer. The portfolio service reads the same file (``sudarshan/portfolio/
privacy.py``), and so does the phone, so the three lists cannot drift.

The mask is decided twice and the stricter wins (Y6):

1. the portfolio applies the floor it stores for the member, united with the mask this service
   sends in the seal (``wire``): a sent mask can only add to the floor;
2. this service applies the member's mask again to everything the portfolio answered, before it
   leaves for the phone (``masked``), so a field the portfolio let through is still removed.

Three rules live here, as in the portfolio's copy: a category the member's row does not say is
shown is hidden (a member with no choices sees no figures); quantities hidden hide P&L too (Y24: a
P&L that moves with ticks is worked out from the quantity, so it would give the quantity away);
and P&L hidden hides the table's ``cost`` list too, whatever quantities say (the family review's
H1: beside a quantity, an average, an amount invested, a value or a trade's price gives the P&L
away by one subtraction). A field the table names as never hidden (the live price and its
anchors, and ``fresh``, how old a holding's price is) is never removed; what is inside it is
masked like anything else, except a ``kept_whole`` field of the shape its maker gives it
(``fresh``: a state and a sentence of dates and ages, no amount). No I/O after the file is read
once at import.

What "P&L hidden" does not hide (v0.12.0 review M4): with quantities shown, the quantity and the
never-hidden live price and previous close give a day's change and a holding's value by one
multiplication. Only the overall P&L, which needs the cost, is out of reach. The table's
``pnl_hidden_note`` (``PNL_HIDDEN_NOTE``) is the sentence the owner reads under the P&L switch;
hiding quantities is what hides those two as well.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .contract import BOOK_PARTS, CATEGORIES

FIELDS_FILE = Path(__file__).with_name("privacy_fields.json")
#: The book parts each category withholds whole: a hidden part is never asked of the portfolio.
PARTS_OF = {"funds": ("funds",), "orders": ("orders", "trades")}
#: The portfolio's limits on a sent mask (``sharing.args_mask``): entries and their length.
WIRE_MAX = 200
WIRE_ENTRY_MAX = 64


def _names(entries: Any, what: str, name: str) -> frozenset[str]:
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{name}: {what} must be a list of field names")
    if not all(isinstance(e, str) and e.strip("*") and len(e) <= WIRE_ENTRY_MAX for e in entries):
        raise ValueError(f"{name}: {what} has an entry that is not a field name")
    return frozenset(entries)


def load(
    path: Path = FIELDS_FILE,
) -> tuple[dict[str, frozenset[str]], frozenset[str], frozenset[str]]:
    """The categories of ``path`` as sets of entries, its ``cost`` list (hidden whenever P&L is)
    and the fields it says are never hidden. ValueError for a table without exactly the four
    categories, or without a cost list, each a list of field names."""
    table = json.loads(path.read_text(encoding="utf-8"))
    found = table.get("categories")
    if not isinstance(found, Mapping) or set(found) != set(CATEGORIES):
        raise ValueError(f"{path.name} must list exactly {', '.join(CATEGORIES)}")
    out = {name: _names(found[name], name, path.name) for name in CATEGORIES}
    cost = _names(table.get("cost"), "cost", path.name)
    never = table.get("never_hidden", [])
    if not isinstance(never, list) or not all(isinstance(e, str) and e for e in never):
        raise ValueError(f"{path.name}: never_hidden must be a list of field names")
    return out, cost, frozenset(never)


TABLE, COST, NEVER_HIDDEN = load()


def load_whole(path: Path = FIELDS_FILE) -> frozenset[str]:
    """The table's ``kept_whole`` list: never-hidden fields passed on with what is inside them
    (``fresh``, how old a holding's price is). ValueError when one is not also never hidden, or is
    one this module does not know the shape of: a name added to the table alone opens nothing."""
    table = json.loads(path.read_text(encoding="utf-8"))
    found = table.get("kept_whole", [])
    if not isinstance(found, list) or not all(isinstance(e, str) and e for e in found):
        raise ValueError(f"{path.name}: kept_whole must be a list of field names")
    if not set(found) <= set(table.get("never_hidden", [])) or not set(found) <= set(WHOLE_SHAPES):
        raise ValueError(f"{path.name}: kept_whole names a field that is not never hidden")
    return frozenset(found)


def _is_freshness(value: Any) -> bool:
    """``Freshness.as_dict()`` of the portfolio: a state and a sentence, and nothing else."""
    return (
        isinstance(value, Mapping)
        and set(value) <= {"state", "words"}
        and isinstance(value.get("state"), str)
        and (value.get("words") is None or isinstance(value.get("words"), str))
    )


#: The shape each kept-whole field must have to be passed on whole; any other shape is looked
#: inside like every other field, so a figure put there by mistake is still removed.
WHOLE_SHAPES = {"fresh": _is_freshness}
KEPT_WHOLE = load_whole()


def kept_whole(key: Any, value: Any) -> bool:
    """Whether the field ``key`` is passed on with everything inside it (the age of a price)."""
    return key in KEPT_WHOLE and WHOLE_SHAPES[key](value)


def load_note(path: Path = FIELDS_FILE) -> str:
    """The table's ``pnl_hidden_note``: what the owner is told where he hides P&L from a member
    who is shown quantities (review M4 of v0.12.0). ValueError when it is not one plain line."""
    note = json.loads(path.read_text(encoding="utf-8")).get("pnl_hidden_note")
    if not isinstance(note, str) or not 0 < len(note) <= 200 or not note.endswith("."):
        raise ValueError(
            f"{path.name}: pnl_hidden_note must be a sentence of at most 200 characters"
        )
    return note


#: The owner's sentence under the P&L switch while quantities are shown (the Family page's words).
PNL_HIDDEN_NOTE = load_note()


def hidden_categories(privacy: Mapping[str, Any] | None) -> tuple[str, ...]:
    """The categories a member is not shown, in the contract's order. A category is shown only
    when the row says ``True``; quantities hidden hide P&L too (Y24)."""
    privacy = privacy if isinstance(privacy, Mapping) else {}
    hidden = {name for name in CATEGORIES if privacy.get(name) is not True}
    if "qty" in hidden:
        hidden.add("pnl")
    return tuple(name for name in CATEGORIES if name in hidden)


def mask_of(hidden: Iterable[str]) -> frozenset[str]:
    """The fields the hidden categories remove: the union of their lists, and the ``cost`` list
    whenever ``pnl`` is one of them (H1)."""
    names = set(hidden)
    out: set[str] = set().union(*(TABLE[name] for name in names))
    if "pnl" in names:
        out |= COST
    return frozenset(out)


def mask_for(member: Mapping[str, Any]) -> frozenset[str]:
    """A member's mask, from their row as read now (a change applies to the next request)."""
    return mask_of(hidden_categories(member.get("privacy")))


def parts_shown(hidden: Iterable[str]) -> tuple[str, ...]:
    """The book parts a member with these categories hidden may be shown."""
    withheld = {part for name in hidden for part in PARTS_OF.get(name, ())}
    return tuple(part for part in BOOK_PARTS if part not in withheld)


def is_hidden(key: Any, mask: frozenset[str]) -> bool:
    """Whether ``mask`` removes the field ``key``: by name, or by a ``*suffix`` pattern."""
    if not isinstance(key, str) or key in NEVER_HIDDEN:
        return False
    return key in mask or any(m.startswith("*") and key.endswith(m[1:]) for m in mask)


def masked(data: Any, mask: frozenset[str]) -> Any:
    """``data`` without the fields ``mask`` removes, at any depth (a new structure; ``data`` is
    left as it was). Removed means absent, never 0 or null: the phone reads an absent field as
    hidden and a null one as not known."""
    if not mask:
        return data
    if isinstance(data, Mapping):
        return {
            k: v if kept_whole(k, v) else masked(v, mask)
            for k, v in data.items()
            if not is_hidden(k, mask)
        }
    if isinstance(data, (list, tuple)):
        return [masked(item, mask) for item in data]
    return data


def wire(mask: frozenset[str]) -> list[str]:
    """The mask as the seal carries it to the portfolio: sorted, within its limits."""
    entries = sorted(mask)
    if len(entries) > WIRE_MAX:
        raise ValueError("a mask of more than 200 fields cannot be sent to the portfolio")
    return entries
