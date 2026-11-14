"""What each privacy category hides, read from the one table (cross-design Y24).

``sudarshan/family/privacy_fields.json`` maps the four categories of a family member's privacy
(``pnl``, ``qty``, ``funds``, ``orders``) to field names and ``*suffix`` patterns, the form
``serialize.hidden`` reads. The portfolio's own viewer mask (``serialize.VIEWER_MASK``) is built
from it here, and the family service and the phone read the same file, so the lists cannot drift.

Three rules live here and nowhere else:

* **P&L is offered only with quantities** (Y24). A P&L worked out between reads is
  (price - average) x quantity, so with quantities hidden it would give them away: ``qty`` hidden
  forces ``pnl`` hidden.
* **P&L hidden hides what it is worked out from** (review H1). Beside a quantity, what a holding
  cost (its average, the amount invested), what it is worth now, a trade's price and the day's
  change in rupees give the P&L away by one subtraction. So while ``pnl`` is hidden, the table's
  ``cost`` list is hidden too, whatever ``qty`` says. This puts the overall P&L out of reach; it
  does not hide a day's change or a holding's value from a member shown quantities, who has both
  from the quantity and the never-hidden price and previous close (v0.12.0 review M4: the
  table's ``pnl_hidden_note`` tells the owner so where he chooses).
* **No row, no figures.** A member the owner has not set a privacy row for sees nothing but
  names, symbols and directions: every category is hidden until the owner chooses.

No I/O after the file is read once at import.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path

from .core.kinds import PRIVACY_CATEGORIES

FIELDS_FILE = Path(__file__).resolve().parents[1] / "family" / "privacy_fields.json"


def load(path: Path = FIELDS_FILE) -> dict[str, frozenset[str]]:
    """The categories of ``path`` as sets of entries. Raises ValueError for a file that does not
    have exactly the four categories, each a list of non-empty strings."""
    table = json.loads(path.read_text(encoding="utf-8"))
    found = table.get("categories")
    if not isinstance(found, Mapping) or set(found) != set(PRIVACY_CATEGORIES):
        raise ValueError(f"{path.name} must list exactly {', '.join(PRIVACY_CATEGORIES)}")
    out = {}
    for name, entries in found.items():
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"{path.name}: {name} must be a non-empty list")
        if not all(isinstance(e, str) and e.strip("*") for e in entries):
            raise ValueError(f"{path.name}: {name} has an entry that is not a field name")
        out[name] = frozenset(entries)
    return out


def load_cost(path: Path = FIELDS_FILE) -> frozenset[str]:
    """The table's ``cost`` list: what is hidden whenever P&L is. Raises ValueError for a file
    whose ``cost`` is missing or is not a non-empty list of field names."""
    found = json.loads(path.read_text(encoding="utf-8")).get("cost")
    if not isinstance(found, list) or not found:
        raise ValueError(f"{path.name} must list the cost fields hidden with P&L")
    if not all(isinstance(e, str) and e.strip("*") for e in found):
        raise ValueError(f"{path.name}: cost has an entry that is not a field name")
    return frozenset(found)


CATEGORIES: dict[str, frozenset[str]] = load()
COST: frozenset[str] = load_cost()


def json_never_hidden(path: Path = FIELDS_FILE) -> tuple[str, ...]:
    """The fields the table says are never hidden (the live price's anchors), for the tests that
    hold the lists to it."""
    return tuple(json.loads(path.read_text(encoding="utf-8")).get("never_hidden", ()))


def _is_freshness(value: object) -> bool:
    """``core.freshness.Freshness.as_dict()``: a state and a sentence, and nothing else."""
    return (
        isinstance(value, Mapping)
        and set(value) <= {"state", "words"}
        and isinstance(value.get("state"), str)
        and (value.get("words") is None or isinstance(value.get("words"), str))
    )


#: The shape a kept-whole field must have to be passed on whole; any other shape is looked inside
#: like every other field, so a figure put there by mistake is still removed.
WHOLE_SHAPES = {"fresh": _is_freshness}


def load_whole(path: Path = FIELDS_FILE) -> frozenset[str]:
    """The table's ``kept_whole`` list: never-hidden fields passed on with what is inside them
    (``fresh``: how old a holding's price is, which a member not shown quantities still needs,
    because an old number is never shown as current). ValueError when one is not also never
    hidden, or is one this module does not know the shape of."""
    table = json.loads(path.read_text(encoding="utf-8"))
    found = table.get("kept_whole", [])
    if not isinstance(found, list) or not all(isinstance(e, str) and e for e in found):
        raise ValueError(f"{path.name}: kept_whole must be a list of field names")
    if not set(found) <= set(table.get("never_hidden", [])) or not set(found) <= set(WHOLE_SHAPES):
        raise ValueError(f"{path.name}: kept_whole names a field that is not never hidden")
    return frozenset(found)


KEPT_WHOLE: frozenset[str] = load_whole()


def kept_whole(key: object, value: object) -> bool:
    """Whether the field ``key`` is passed on with everything inside it (the age of a price)."""
    return key in KEPT_WHOLE and WHOLE_SHAPES[key](value)


def hidden_categories(shown: Mapping[str, bool] | None) -> frozenset[str]:
    """The categories a member does not see. ``shown`` maps each category to whether it is shown;
    None (no row) or a category left out is hidden. ``qty`` hidden hides ``pnl`` (Y24)."""
    shown = shown or {}
    hidden = {c for c in PRIVACY_CATEGORIES if shown.get(c) is not True}
    if "qty" in hidden:
        hidden.add("pnl")
    return frozenset(hidden)


def mask_of(hidden: Iterable[str]) -> frozenset[str]:
    """The fields the hidden categories remove: the union of their lists, and the ``cost`` list
    whenever ``pnl`` is one of them."""
    out: set[str] = set()
    names = set(hidden)
    for name in names:
        out |= CATEGORIES[name]
    if "pnl" in names:
        out |= COST
    return frozenset(out)


def floor(shown: Mapping[str, bool] | None) -> frozenset[str]:
    """A member's stored floor: what the portfolio hides from them whatever the caller asks."""
    return mask_of(hidden_categories(shown))


def viewer_mask() -> frozenset[str]:
    """A viewer who is shown no figures: everything ``pnl`` and ``qty`` hide."""
    return mask_of(("pnl", "qty"))


def valid_shown(shown: Mapping[str, bool]) -> str | None:
    """The owner's sentence when a choice cannot be kept, else None (Y24)."""
    if shown.get("pnl") is True and shown.get("qty") is not True:
        return (
            "P&L that moves with prices is worked out from quantities. Show quantities to show P&L."
        )
    return None
