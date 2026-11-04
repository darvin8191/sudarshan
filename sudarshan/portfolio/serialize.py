"""The shapes the phone is sent, and the one place a viewer's mask is applied (design 6.5).

* ``member``, ``account``, ``tab``, ``asset`` and ``activity`` pick the contract's fields out of a
  stored row (types.ts), so a column the contract does not name (the principal, a request id, an
  import key) never leaves the service.
* ``out`` is the single door every op answers through. It removes from the answer whatever the
  scope's mask names: with the owner's scope (an empty mask) it returns the answer as it is. A mask
  entry is a field name, or ``*suffix`` for every field that ends so. ``VIEWER_MASK`` is what a
  family viewer would carry: values, quantities, prices, gains, percentages and funds go;
  symbols, names and the direction of an entry (``kind``) stay. v1 builds only the owner, and the
  test with a synthetic viewer holds the rest of the design to this.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .store import Scope

#: What a viewer never sees. Keys ending in these (paise, scaled quantity and price, percentages)
#: and these names (lines hold each account's quantity; the series and the flows are values).
VIEWER_MASK = frozenset(
    {
        "*_paise",
        "*_u",
        "*_pct",
        "pct",
        "lines",
        "series",
        "benchmarks",
        "xirr",
        "twr",
        "by_fy",
        "income",
        "price",
        "fresh",
        "totals",
        "fixed",
        "allocation",
        "liabilities",
        "words",
        "note",
        "effect",
        "ratio_num",
        "ratio_den",
        "charges_detail",
    }
)


def hidden(key: str, mask: frozenset[str]) -> bool:
    return key in mask or any(m.startswith("*") and key.endswith(m[1:]) for m in mask)


def masked(data: Any, mask: frozenset[str]) -> Any:
    """``data`` without the fields the mask names, at any depth."""
    if not mask:
        return data
    if isinstance(data, Mapping):
        return {k: masked(v, mask) for k, v in data.items() if not hidden(k, mask)}
    if isinstance(data, (list, tuple)):
        return [masked(item, mask) for item in data]
    return data


def out(scope: Scope, data: Any) -> Any:
    """What the scope may see of an answer; every op ends here."""
    return masked(data, scope.mask)


# ---- the contract's rows -------------------------------------------------------------------------
def _pick(row: Mapping[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    return {name: row.get(name) for name in names}


def member(row: Mapping[str, Any]) -> dict[str, Any]:
    return _pick(row, ("id", "name", "relation", "version"))


def account(row: Mapping[str, Any]) -> dict[str, Any]:
    found = _pick(row, ("id", "member_id", "name", "kind", "broker", "note", "version"))
    return {**found, "system": bool(row["system"])}


def tab(row: Mapping[str, Any]) -> dict[str, Any]:
    found = _pick(
        row,
        (
            "id",
            "name",
            "name_alt",
            "kind",
            "icon",
            "colour",
            "position",
            "manual_old_days",
            "version",
        ),
    )
    return {
        **found,
        "pinned": bool(row["pinned"]),
        "archived": bool(row["archived"]),
        "system": bool(row["system"]),
    }


ASSET = (
    "id kind name exchange symbol isin amfi_code unit_label asset_class sector mcap tick_size_u "
    "resolved_at version"
).split()
ASSET_REF = "id kind name exchange symbol isin unit_label asset_class sector mcap".split()


def asset(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **_pick(row, tuple(ASSET)),
        "tags": list(row.get("tags") or []),
        "aliases": list(row.get("aliases") or []),
    }


def asset_ref(row: Mapping[str, Any]) -> dict[str, Any]:
    return {**_pick(row, tuple(ASSET_REF)), "tags": list(row.get("tags") or [])}


ACTIVITY = (
    "id tab_id seq kind status source trade_date account_id to_account_id asset_id qty_u price_u "
    "amount_paise charges_paise charges_detail tds_paise value_paise ratio_num ratio_den lot_date "
    "note batch_id created_at updated_at"
).split()


def activity(row: Mapping[str, Any]) -> dict[str, Any]:
    return _pick(row, tuple(ACTIVITY))
