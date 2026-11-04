"""A tab's layout and its saved views (M7.25, design 4.9).

The layout says which widgets a tab shows and in what order, which holding columns, the sort and
the group; a saved view is a name plus filters, sort, group and columns. Both are JSON the owner
sends, so each is read here: a key we do not know is dropped, a value we cannot use is refused in a
sentence. Nothing here knows the clock or a price.

The store has no accessor for ``tab_layout`` (its key is the tab, not an id), so its two rows are
read and written through the transaction's connection, scoped to the principal as every query is.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import insert, select, update

from .core.filters import BadFilter, Filters
from .core.kinds import GROUPS, HOLDING_COLUMNS, LAYOUT_VERSION, NAME_MAX, SORTS, WIDGETS
from .store import Invalid, NotFound, Tx, VersionConflict

WORDS = {
    "spec": "The layout could not be read. Reload the page and change it again.",
    "widgets": "Choose each section of the page from the list, once.",
    "columns": "Choose at least one column, each from the list, once.",
    "sort": "Choose how to sort from the list.",
    "group": "Choose how to group from the list.",
    "name": "Give the view a name.",
    "name_long": f"A view name can have up to {NAME_MAX} characters.",
    "name_taken": "This tab already has a view with that name.",
    "views_full": "A tab can have up to 20 saved views. Delete one you no longer use.",
    "gone": "This view is no longer there; it may have been deleted on another screen.",
}
MAX_VIEWS = 20
#: Kinds of tab whose holdings have no day-to-day price: no Today, no movers.
STILL = ("gold", "cash", "real_estate", "esop", "crypto", "other")


def defaults_for(kind: str) -> dict[str, Any]:
    """What "Reset to the defaults of this kind of tab" gives."""
    still = kind in STILL
    return {
        "v": LAYOUT_VERSION,
        "widgets": [w for w in WIDGETS if not (still and w in ("today", "movers"))],
        "columns": [c for c in HOLDING_COLUMNS if not (still and c == "today")],
        "sort": "value",
        "group": "none",
    }


def _unique(raw: Any, allowed: tuple[str, ...], word: str, *, least: int = 0) -> list[str]:
    if raw.__class__ is not list or len(set(raw)) != len(raw) or len(raw) < least:
        raise Invalid(WORDS[word])
    if not all(isinstance(item, str) and item in allowed for item in raw):
        raise Invalid(WORDS[word])
    return list(raw)


def _choice(raw: Any, allowed: tuple[str, ...], word: str) -> str:
    if not isinstance(raw, str) or raw not in allowed:
        raise Invalid(WORDS[word])
    return raw


def clean_spec(raw: Any, kind: str) -> dict[str, Any]:
    """A layout from what the phone sent; what it leaves out takes the kind's default."""
    if not isinstance(raw, Mapping):
        raise Invalid(WORDS["spec"])
    base = defaults_for(kind)
    return {
        "v": LAYOUT_VERSION,
        "widgets": _unique(raw.get("widgets", base["widgets"]), WIDGETS, "widgets"),
        "columns": _unique(
            raw.get("columns", base["columns"]), HOLDING_COLUMNS, "columns", least=1
        ),
        "sort": _choice(raw.get("sort", base["sort"]), SORTS, "sort"),
        "group": _choice(raw.get("group", base["group"]), GROUPS, "group"),
    }


def clean_view(raw: Any, kind: str) -> dict[str, Any]:
    """A saved view's spec: filters, sort, group and columns."""
    if not isinstance(raw, Mapping):
        raise Invalid(WORDS["spec"])
    base = defaults_for(kind)
    try:
        filters = Filters.of(raw.get("filters") or {})
    except BadFilter as bad:
        raise Invalid(str(bad)) from None
    return {
        "filters": filters.as_dict(),
        "sort": _choice(raw.get("sort", base["sort"]), SORTS, "sort"),
        "group": _choice(raw.get("group", base["group"]), GROUPS, "group"),
        "columns": _unique(
            raw.get("columns", base["columns"]), HOLDING_COLUMNS, "columns", least=1
        ),
    }


def view_name(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise Invalid(WORDS["name"])
    if len(raw.strip()) > NAME_MAX:
        raise Invalid(WORDS["name_long"])
    return raw.strip()


# ---- the layout row (keyed by the tab) --------------------------------------------------------
def layout_row(tx: Tx, tab_id: int) -> dict[str, Any] | None:
    t = tx._t("tab_layout")
    row = tx.conn.execute(select(t).where(t.c.tab_id == tab_id, tx._mine(t))).mappings().first()
    return tx._out(row)


def layout_state(tx: Tx, tab: Mapping[str, Any]) -> dict[str, Any]:
    """types.ts ``LayoutState``: the stored layout (version 0 when none was ever saved)."""
    defaults = defaults_for(tab["kind"])
    row = layout_row(tx, tab["id"])
    if row is None:
        return {"tab_id": tab["id"], "version": 0, "spec": defaults, "defaults": defaults}
    try:
        spec = clean_spec(row["spec"], tab["kind"])
    except Invalid:  # a spec an older release wrote that no longer reads: the defaults
        spec = defaults
    return {"tab_id": tab["id"], "version": row["version"], "spec": spec, "defaults": defaults}


def save_layout(tx: Tx, tab: Mapping[str, Any], version: int, spec: dict, now: int) -> None:
    """Write the layout only from the version it was decided on (0: none saved yet)."""
    t = tx._t("tab_layout")
    row = layout_row(tx, tab["id"])
    if row is None:
        if version != 0:
            raise VersionConflict("version_conflict_item")
        values = tx._in({"spec": spec})
        tx.conn.execute(
            insert(t).values(
                tab_id=tab["id"], principal=tx.who, created_at=now, updated_at=now, version=1,
                **values,
            )
        )  # fmt: skip
        return
    done = tx.conn.execute(
        update(t)
        .where(t.c.tab_id == tab["id"], tx._mine(t), t.c.version == version)
        .values(**tx._in({"spec": spec}), updated_at=now, version=version + 1)
    ).rowcount
    if not done:
        raise VersionConflict("version_conflict_item")


# ---- saved views --------------------------------------------------------------------------------
def view_of(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "tab_id": row["tab_id"],
        "name": row["name"],
        "spec": row["spec"],
        "is_default": bool(row["is_default"]),
        "version": row["version"],
    }


def views_of(tx: Tx, tab_id: int) -> list[dict[str, Any]]:
    return [view_of(r) for r in tx.rows("saved_view", "id", tab_id=tab_id)]


def one_view(tx: Tx, view_id: int) -> dict[str, Any]:
    row = tx.get("saved_view", view_id)
    if row is None:
        raise NotFound(WORDS["gone"])
    return row


def only_default(tx: Tx, tab_id: int, keep: int, now: int) -> None:
    """Of a tab's views exactly one is the default: the others stop being it."""
    for other in tx.rows("saved_view", "id", tab_id=tab_id):
        if other["id"] != keep and other["is_default"]:
            tx.change("saved_view", other["id"], other["version"], {"is_default": False}, now)
