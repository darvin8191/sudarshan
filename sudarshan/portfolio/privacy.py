"""What each privacy category hides, read from the one table (cross-design Y24).

``sudarshan/family/privacy_fields.json`` maps the four categories of a family member's privacy
(``pnl``, ``qty``, ``funds``, ``orders``) to field names and ``*suffix`` patterns, the form
``serialize.hidden`` reads. The portfolio's own viewer mask (``serialize.VIEWER_MASK``) is built
from it here, and the family service and the phone read the same file, so the lists cannot drift.

Two rules live here and nowhere else:

* **P&L is offered only with quantities** (Y24). A P&L worked out between reads is
  (price - average) x quantity, so with quantities hidden it would give them away: ``qty`` hidden
  forces ``pnl`` hidden.
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


CATEGORIES: dict[str, frozenset[str]] = load()


def json_never_hidden(path: Path = FIELDS_FILE) -> tuple[str, ...]:
    """The fields the table says are never hidden (the live price's anchors), for the tests that
    hold the lists to it."""
    return tuple(json.loads(path.read_text(encoding="utf-8")).get("never_hidden", ()))


def hidden_categories(shown: Mapping[str, bool] | None) -> frozenset[str]:
    """The categories a member does not see. ``shown`` maps each category to whether it is shown;
    None (no row) or a category left out is hidden. ``qty`` hidden hides ``pnl`` (Y24)."""
    shown = shown or {}
    hidden = {c for c in PRIVACY_CATEGORIES if shown.get(c) is not True}
    if "qty" in hidden:
        hidden.add("pnl")
    return frozenset(hidden)


def mask_of(hidden: Iterable[str]) -> frozenset[str]:
    """The fields the hidden categories remove, the union of their lists."""
    out: set[str] = set()
    for name in hidden:
        out |= CATEGORIES[name]
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
        return "Live P&L is worked out from quantities. Show quantities to show P&L."
    return None
