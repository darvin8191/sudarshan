"""The owner's book on top of the store (M7.18, M7.20, M7.21): tabs, members, accounts, assets and
entries, every write checked before it is made.

No network, no prices and nothing of OpenAlgo's: only the store and the pure core. Each method
takes the ``Scope`` first and the instant of the call (``now``, epoch ms) by name, so a test needs
no clock, and does its whole job in one store transaction, which is what lets a check and the
write it guards see the same rows.

* Entries. A create, change or delete replays the tab through ``core.ledger`` with the change
  applied, and is refused with the ledger's own sentence (or, when the change would leave a later
  entry without units, the sentence that names that entry). Drafts count nowhere. A date after
  today saves as a draft only.
* ``dry_run`` runs the same check and returns the entry's effect in words, writing nothing.
* ``request_id``: a create that is sent again returns the row the first one made, whatever the
  tab's version has become since.
* The tab's ``version`` is named by every entry write and moves by one with it.
* Tabs: at most 50 not archived (the Upstox tab is not counted); only an archived tab can be
  deleted, and only by typing its name.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from sudarshan.portfolio.core import words
from sudarshan.portfolio.core.days import day_of_instant, is_day
from sudarshan.portfolio.core.kinds import (
    ACCOUNT_KINDS,
    ACTIVITIES_PAGE,
    ACTIVITIES_PAGE_MAX,
    ASSET_CLASSES,
    ASSET_KINDS,
    CHARGE_PARTS,
    HOLDING_KINDS,
    MANUAL_OLD_DAYS_MAX,
    MANUAL_OLD_DAYS_MIN,
    MAX_TABS,
    MCAPS,
    NAME_MAX,
    NOTE_MAX,
    TAB_COLOURS,
    TAB_ICONS,
    TAB_KINDS,
    TAB_NAME_MAX,
    TAG_MAX,
    TAGS_MAX,
)
from sudarshan.portfolio.core.kinds import WORDS as SHARED_WORDS
from sudarshan.portfolio.core.ledger import replay
from sudarshan.portfolio.core.model import BadEntry, Entry, Refusal
from sudarshan.portfolio.core.money import paise_ok, price_ok, step_ok
from sudarshan.portfolio.core.words import Names
from sudarshan.portfolio.store import (
    Invalid,
    NotFound,
    PortfolioStore,
    Refused,
    Scope,
    Tx,
    VersionConflict,
)

WORDS = {
    "request": "This request could not be identified. Reload the page and try again.",
    "archived": "This portfolio is archived. Bring it back before adding or changing entries.",
    "upstox_tab": "The Upstox portfolio is read-only; entries cannot be added to it.",
    "upstox_tab_change": "The Upstox portfolio is read-only; only pinning it can be changed.",
    "upstox_account": "The Upstox account is read-only; choose another account.",
    "upstox_account_change": "The Upstox account is read-only and cannot be changed.",
    "delete_first_archive": "Archive this portfolio first; only an archived one can be deleted.",
    "delete_name": "Type the portfolio's name exactly to delete it.",
    "delete_upstox": "The Upstox portfolio cannot be deleted.",
    "liability": "A loan or liability keeps balances, not entries. Add a balance instead.",
    "loan_tab": "A loan belongs to one of your own portfolios. Open that tab, then add it.",
    "loan_first": "Enter the loan's outstanding balance with a real date.",
    "breakdown": "The charges breakdown must add up to the charges.",
    "draft": "Saved as a draft; it counts nowhere until it is posted.",
    "cursor": "That page of entries is not valid. Open the list again.",
    "unknown_field": "This entry has a field this version does not know.",
}
#: What an entry is allowed to carry (types.ts ``ActivityInput``).
ENTRY_KEYS = frozenset(
    (
        "kind status trade_date account_id to_account_id asset_id qty_u price_u amount_paise "
        "charges_paise charges_detail tds_paise value_paise ratio_num ratio_den lot_date note"
    ).split()
)
#: The columns an entry change replaces as a whole.
ENTRY_COLUMNS = (*sorted(ENTRY_KEYS - {"charges_paise", "tds_paise"}), "charges_paise", "tds_paise")
_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


# ---- small checks --------------------------------------------------------------------------
def _request_id(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise Invalid(WORDS["request"])
    return value


def _text(value: Any, label: str, maximum: int, *, required: bool = True) -> str | None:
    text = value.strip() if isinstance(value, str) else None
    if not text:
        if required:
            raise Invalid(f"Enter {label}.")
        return None
    if len(text) > maximum:
        raise Invalid(f"{label.capitalize()} can have up to {maximum} characters.")
    return text


def _choice(value: Any, allowed: Sequence[str], label: str) -> str:
    if value not in allowed:
        raise Invalid(f"Choose {label} from the list.")
    return value


def _tags(value: Any) -> list[str]:
    tags = [t.strip() for t in value] if isinstance(value, (list, tuple)) else None
    said = f"A tag is 1 to {TAG_MAX} characters with no comma, and at most {TAGS_MAX} tags fit."
    if tags is None or len(tags) > TAGS_MAX:
        raise Invalid(said)
    if any(not t or len(t) > TAG_MAX or "," in t for t in tags):
        raise Invalid(said)
    return list(dict.fromkeys(tags))


def _pick(changes: Mapping[str, Any], allowed: Sequence[str]) -> dict[str, Any]:
    unknown = set(changes) - set(allowed)
    if unknown:
        raise Invalid("That change includes something that cannot be edited here.")
    return dict(changes)


def _load(tx: Tx, table: str, row_id: int, version: int, word: str = "version_conflict_item"):
    """The row, only if it is there and at ``version``; NotFound or VersionConflict otherwise."""
    row = tx.get(table, row_id)
    if row is None:
        raise NotFound()
    if row["version"] != version:
        raise VersionConflict(word)
    return row


def _decimals(asset: Mapping[str, Any]) -> int:
    """How many decimals a quantity of this asset may have: shares 0, fund units 4, grams 3,
    crypto 6."""
    if asset["kind"] == "listed":
        return 0
    if asset["kind"] == "mf":
        return 4
    return 6 if asset["asset_class"] == "crypto" else 3


def _unit(asset: Mapping[str, Any]) -> str:
    if asset["kind"] == "listed":
        return "shares"
    return "units" if asset["kind"] == "mf" else (asset.get("unit_label") or "units")


NEW = -1  # the tab being created, in a layout


def _layout(
    tabs: list[dict],
    *,
    moved: int | None = None,
    place: int | None = None,
    archived: bool | None = None,
    new: bool = False,
) -> dict[int, int]:
    """Every tab's position after a change: 0..n-1, no gaps, no ties. The Upstox tab is 0, then
    the active custom tabs, then the archived ones, each group in its own order. ``place`` is the
    position the ``moved`` tab is asked to take (clamped into its group); without one it keeps its
    place among its group. A ``new`` tab goes last among the active ones."""
    system = [t for t in tabs if t["system"]]
    custom = sorted((t for t in tabs if not t["system"]), key=lambda t: (t["position"], t["id"]))

    def is_archived(t: dict) -> bool:
        return bool(archived) if t["id"] == moved and archived is not None else bool(t["archived"])

    active = [t["id"] for t in custom if not is_archived(t)]
    shelved = [t["id"] for t in custom if is_archived(t)]
    if new:
        active.append(NEW)
    if moved in shelved and place is None:
        shelved.remove(moved)  # a tab just put away joins the end of the archived ones
        shelved.append(moved)
    if moved is not None and place is not None:
        group = shelved if moved in shelved else active
        base = len(system) + (0 if group is active else len(active))
        group.remove(moved)
        group.insert(min(max(place - base, 0), len(group)), moved)
    order = [t["id"] for t in system] + active + shelved
    return {tab_id: index for index, tab_id in enumerate(order)}


def _renumber(tx: Tx, layout: dict[int, int], now: int, *, skip: int) -> None:
    """Write the positions that differ. A tab pushed aside is not changed, so its version stays."""
    for tab in tx.rows("tab", "position,id"):
        want = layout.get(tab["id"])
        if tab["id"] != skip and want is not None and tab["position"] != want:
            tx.reposition(tab["id"], want)


class Book:
    def __init__(self, store: PortfolioStore):
        self.store = store

    # ---- readers ----
    def members(self, scope: Scope) -> list[dict]:
        return self.store.read(scope, lambda tx: tx.rows("member"))

    def accounts(self, scope: Scope) -> list[dict]:
        return self.store.read(scope, lambda tx: tx.rows("account"))

    def tabs(self, scope: Scope) -> list[dict]:
        return self.store.read(scope, lambda tx: tx.rows("tab", "position,id"))

    def assets(self, scope: Scope) -> list[dict]:
        def run(tx: Tx):
            return [_with_aliases(tx, row) for row in tx.rows("asset")]

        return self.store.read(scope, run)

    def asset_find(
        self,
        scope: Scope,
        *,
        isin: str | None = None,
        symbol: str | None = None,
        amfi_code: str | None = None,
    ) -> dict | None:
        """The asset an ISIN or symbol (old or new) belongs to, aliases first (4.3)."""

        def run(tx: Tx):
            for kind, value in (("isin", isin), ("symbol", symbol)):
                alias = tx.find_alias(kind, value.strip().upper()) if value else None
                if alias:
                    return _with_aliases(tx, tx.get("asset", alias["asset_id"]))
            if amfi_code:
                rows = tx.rows("asset", amfi_code=amfi_code.strip())
                return _with_aliases(tx, rows[0]) if rows else None
            return None

        return self.store.read(scope, run)

    def activities_list(
        self,
        scope: Scope,
        tab_id: int,
        *,
        before: str | None = None,
        limit: int = ACTIVITIES_PAGE,
        asset_id: int | None = None,
    ) -> dict:
        """A page of a tab's entries, newest first; ``next`` is the cursor for the page after."""
        try:
            cursor = tuple(int(p) for p in before.split(":")) if before else None
            if cursor is not None and len(cursor) != 3:
                raise ValueError(before)
        except ValueError:
            raise Invalid(WORDS["cursor"]) from None
        size = max(1, min(int(limit), ACTIVITIES_PAGE_MAX))

        def run(tx: Tx):
            if tx.get("tab", tab_id) is None:
                raise NotFound()
            found = tx.entries_page(tab_id, cursor, size + 1, asset_id)
            page = found[:size]
            last = page[-1] if len(found) > size else None
            token = f"{last['trade_date']}:{last['seq']}:{last['id']}" if last else None
            return {"items": page, "next": token}

        return self.store.read(scope, run)

    # ---- tabs ----
    def tab_create(
        self,
        scope: Scope,
        request_id: str,
        name: str,
        kind: str,
        *,
        now: int,
        name_alt: str | None = None,
        icon: str = "briefcase",
        colour: str = "brand",
        pinned: bool = False,
    ) -> dict:
        rid = _request_id(request_id)
        values = {
            "name": _text(name, "a name for the portfolio", TAB_NAME_MAX),
            "name_alt": _text(name_alt, "the second name", TAB_NAME_MAX, required=False),
            "kind": _choice(kind, [k for k in TAB_KINDS if k != "upstox"], "a kind"),
            "icon": _choice(icon, TAB_ICONS, "an icon"),
            "colour": _choice(colour, TAB_COLOURS, "a colour"),
            "pinned": bool(pinned),
        }

        def run(tx: Tx):
            if (again := tx.by_request("tab", rid)) is not None:
                return again
            if tx.count("tab", archived=0, system=0) >= MAX_TABS:
                raise Refused(SHARED_WORDS["tab_limit"], reason="tab_limit")
            layout = _layout(tx.rows("tab", "position,id"), new=True)
            made = tx.insert("tab", {**values, "position": layout[NEW]}, now, rid)
            _renumber(tx, layout, now, skip=NEW)
            return made

        return self.store.write(scope, run)

    def tab_update(
        self, scope: Scope, tab_id: int, version: int, changes: Mapping[str, Any], *, now: int
    ) -> dict:
        wanted = _pick(
            changes,
            "name name_alt icon colour position pinned archived manual_old_days".split(),
        )

        def run(tx: Tx):
            tab = _load(tx, "tab", tab_id, version, "version_conflict")
            if tab["system"] and set(wanted) - {"pinned"}:
                raise Refused(WORDS["upstox_tab_change"])
            values: dict[str, Any] = {}
            for key, value in wanted.items():
                if key == "name":
                    values[key] = _text(value, "a name for the portfolio", TAB_NAME_MAX)
                elif key == "name_alt":
                    values[key] = _text(value, "the second name", TAB_NAME_MAX, required=False)
                elif key == "icon":
                    values[key] = _choice(value, TAB_ICONS, "an icon")
                elif key == "colour":
                    values[key] = _choice(value, TAB_COLOURS, "a colour")
                elif key == "position":
                    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                        raise Invalid("Enter the position as a whole number.")
                    values[key] = value
                elif key == "manual_old_days":
                    if (
                        not isinstance(value, int)
                        or isinstance(value, bool)
                        or not MANUAL_OLD_DAYS_MIN <= value <= MANUAL_OLD_DAYS_MAX
                    ):
                        raise Invalid(
                            f"Enter a number of days from {MANUAL_OLD_DAYS_MIN} to "
                            f"{MANUAL_OLD_DAYS_MAX}."
                        )
                    values[key] = value
                else:
                    values[key] = bool(value)
            if tab["archived"] and values.get("archived") is False:
                if tx.count("tab", archived=0, system=0) >= MAX_TABS:
                    raise Refused(SHARED_WORDS["tab_limit"], reason="tab_limit")
            if "position" in values or "archived" in values:
                # A move renumbers the others in this transaction: positions stay 0..n-1 with no
                # gaps and no ties, the Upstox tab first, archived tabs after the active ones.
                layout = _layout(
                    tx.rows("tab", "position,id"),
                    moved=tab_id,
                    place=values.pop("position", None),
                    archived=values.get("archived"),
                )
                values["position"] = layout[tab_id]
                made = tx.change("tab", tab_id, version, values, now, "version_conflict")
                _renumber(tx, layout, now, skip=tab_id)
                return made
            return tx.change("tab", tab_id, version, values, now, "version_conflict")

        return self.store.write(scope, run)

    def tab_delete(
        self, scope: Scope, tab_id: int, version: int, confirm_name: str, *, now: int
    ) -> dict:
        """An archived tab and everything in it, after its name is typed."""

        def run(tx: Tx):
            tab = _load(tx, "tab", tab_id, version, "version_conflict")
            if tab["system"]:
                raise Refused(WORDS["delete_upstox"])
            if not tab["archived"]:
                raise Refused(WORDS["delete_first_archive"])
            if confirm_name != tab["name"]:
                raise Refused(WORDS["delete_name"])
            tx.remove_tab(tab_id)
            return {"id": tab_id}

        return self.store.write(scope, run)

    # ---- members and accounts ----
    def member_create(
        self, scope: Scope, request_id: str, name: str, *, now: int, relation: str | None = None
    ) -> dict:
        rid = _request_id(request_id)
        values = {
            "name": _text(name, "a name", NAME_MAX),
            "relation": _text(relation, "the relation", NAME_MAX, required=False),
        }

        def run(tx: Tx):
            return tx.by_request("member", rid) or tx.insert("member", values, now, rid)

        return self.store.write(scope, run)

    def member_update(
        self, scope: Scope, member_id: int, version: int, changes: Mapping[str, Any], *, now: int
    ) -> dict:
        wanted = _pick(changes, ["name", "relation"])
        values = {}
        if "name" in wanted:
            values["name"] = _text(wanted["name"], "a name", NAME_MAX)
        if "relation" in wanted:
            values["relation"] = _text(wanted["relation"], "the relation", NAME_MAX, required=False)

        def run(tx: Tx):
            _load(tx, "member", member_id, version)
            return tx.change("member", member_id, version, values, now)

        return self.store.write(scope, run)

    def account_create(
        self,
        scope: Scope,
        request_id: str,
        member_id: int,
        name: str,
        kind: str,
        *,
        now: int,
        broker: str | None = None,
        note: str | None = None,
    ) -> dict:
        rid = _request_id(request_id)
        values = {
            "member_id": member_id,
            "name": _text(name, "a name for the account", NAME_MAX),
            "kind": _choice(kind, [k for k in ACCOUNT_KINDS if k != "upstox"], "a kind"),
            "broker": _text(broker, "the broker", NAME_MAX, required=False),
            "note": _text(note, "the note", NOTE_MAX, required=False),
        }

        def run(tx: Tx):
            if (again := tx.by_request("account", rid)) is not None:
                return again
            if tx.get("member", member_id) is None:
                raise Invalid("Choose whose account this is from the list.")
            return tx.insert("account", values, now, rid)

        return self.store.write(scope, run)

    def account_update(
        self, scope: Scope, account_id: int, version: int, changes: Mapping[str, Any], *, now: int
    ) -> dict:
        wanted = _pick(changes, ["name", "kind", "broker", "note"])
        values: dict[str, Any] = {}
        if "name" in wanted:
            values["name"] = _text(wanted["name"], "a name for the account", NAME_MAX)
        if "kind" in wanted:
            values["kind"] = _choice(
                wanted["kind"], [k for k in ACCOUNT_KINDS if k != "upstox"], "a kind"
            )
        if "broker" in wanted:
            values["broker"] = _text(wanted["broker"], "the broker", NAME_MAX, required=False)
        if "note" in wanted:
            values["note"] = _text(wanted["note"], "the note", NOTE_MAX, required=False)

        def run(tx: Tx):
            account = _load(tx, "account", account_id, version)
            if account["system"]:
                raise Refused(WORDS["upstox_account_change"])
            return tx.change("account", account_id, version, values, now)

        return self.store.write(scope, run)

    # ---- assets ----
    def asset_create(
        self,
        scope: Scope,
        request_id: str,
        kind: str,
        *,
        now: int,
        name: str | None = None,
        symbol: str | None = None,
        exchange: str | None = None,
        isin: str | None = None,
        instrument_key: str | None = None,
        tick_size_u: int | None = None,
        amfi_code: str | None = None,
        unit_label: str | None = None,
        asset_class: str | None = None,
        sector: str | None = None,
        mcap: str | None = None,
        tags: Sequence[str] = (),
        aliases: Sequence[tuple[str, str]] = (),
        first_value: Mapping[str, int] | None = None,
        resolved_at: int | None = None,
        tab_id: int | None = None,
        version: int | None = None,
    ) -> dict:
        """A listed asset (already resolved by the caller), a fund, a manual asset or a
        liability. ISIN, symbol and fund code are also its aliases; ``aliases`` adds old ones.

        A liability (M7.19 V2) belongs to one of the owner's own tabs, ``tab_id``, whose net worth
        it lowers; its ``first_value`` is the outstanding balance on a day, and the tab's version
        moves (from ``version``) so a screen showing the tab learns of it."""
        rid = _request_id(request_id)
        kind = _choice(kind, ASSET_KINDS, "a kind")
        if kind == "liability":
            if not isinstance(tab_id, int) or isinstance(tab_id, bool):
                raise Invalid(WORDS["loan_tab"])
            if first_value is None:
                raise Invalid(WORDS["loan_first"])
        values: dict[str, Any] = {
            "kind": kind,
            "sector": _text(sector, "the sector", NAME_MAX, required=False),
            "mcap": None if mcap is None else _choice(mcap, MCAPS, "a cap size"),
            "tags": _tags(tags),
            "unit_label": _text(unit_label, "the unit", 20, required=False),
            "instrument_key": instrument_key,
            "tick_size_u": tick_size_u,
            "resolved_at": resolved_at,
        }
        found = [(k, str(v).strip().upper()) for k, v in aliases if str(v).strip()]
        if kind == "listed":
            exchange_ = _choice(exchange, ("NSE", "BSE"), "an exchange")
            symbol_ = _text(symbol, "the symbol", NAME_MAX).upper()
            values |= {
                "exchange": exchange_,
                "symbol": symbol_,
                "name": _text(name or symbol_, "a name", NAME_MAX),
                "asset_class": _choice(asset_class or "equity", ASSET_CLASSES, "a kind of asset"),
            }
            found.append(("symbol", symbol_))
        elif kind == "mf":
            code = _text(amfi_code, "the scheme code", 20).strip()
            if not code.isdigit():
                raise Invalid("A scheme code is made of digits.")
            values |= {
                "amfi_code": code,
                "name": _text(name, "a name", NAME_MAX),
                "asset_class": _choice(asset_class or "mf_other", ASSET_CLASSES, "a kind of asset"),
            }
        else:
            values |= {
                "name": _text(name, "a name", NAME_MAX),
                "asset_class": _choice(asset_class or "other", ASSET_CLASSES, "a kind of asset"),
            }
        if isin:
            isin = isin.strip().upper()
            if not _ISIN.match(isin):
                raise Invalid("An ISIN is 12 letters and digits, such as INE002A01018.")
            values["isin"] = isin
            found.append(("isin", isin))
        if first_value is not None and not (
            is_day(first_value.get("day")) and price_ok(first_value.get("price_u"))
        ):
            raise Invalid("Enter the first value with a real date and a price above zero.")

        def run(tx: Tx):
            if (again := tx.by_request("asset", rid)) is not None:
                return _with_aliases(tx, again)
            if kind == "liability":
                tab = (
                    tx.get("tab", tab_id)
                    if version is None
                    else _load(tx, "tab", tab_id, version, "version_conflict")
                )
                if tab is None:
                    raise NotFound()
                if tab["system"] or tab["kind"] == "upstox":
                    raise Refused(WORDS["loan_tab"])
                if tab["archived"]:
                    raise Refused(WORDS["archived"])
            for alias_kind, value in dict.fromkeys(found):
                if tx.find_alias(alias_kind, value):
                    raise Refused(f"{values['name']} is already in your list.", reason="duplicate")
            if values.get("amfi_code") and tx.rows("asset", amfi_code=values["amfi_code"]):
                raise Refused(f"{values['name']} is already in your list.", reason="duplicate")
            if kind in ("manual", "liability") and any(
                a["name"].casefold() == values["name"].casefold()
                for a in tx.rows("asset")
                if a["kind"] in ("manual", "liability")
            ):
                raise Refused(f"You already have an asset called {values['name']}.")
            row = tx.insert("asset", values, now, rid)
            for alias_kind, value in dict.fromkeys(found):
                tx.add_alias(row["id"], alias_kind, value, now)
            if first_value is not None:
                tx.set_price_point(row["id"], first_value["day"], first_value["price_u"], now)
            if kind == "liability":
                tx.link_liability(tab_id, row["id"], now)
                if version is not None:
                    tx.bump_tab(tab_id, version, now)
            return _with_aliases(tx, row)

        return self.store.write(scope, run)

    def asset_update(
        self, scope: Scope, asset_id: int, version: int, changes: Mapping[str, Any], *, now: int
    ) -> dict:
        wanted = _pick(changes, ["name", "asset_class", "sector", "mcap", "tags"])
        values: dict[str, Any] = {}
        for key, value in wanted.items():
            if key == "name":
                values[key] = _text(value, "a name", NAME_MAX)
            elif key == "asset_class":
                values[key] = _choice(value, ASSET_CLASSES, "a kind of asset")
            elif key == "sector":
                values[key] = _text(value, "the sector", NAME_MAX, required=False)
            elif key == "mcap":
                values[key] = None if value is None else _choice(value, MCAPS, "a cap size")
            else:
                values[key] = _tags(value)

        def run(tx: Tx):
            asset = _load(tx, "asset", asset_id, version)
            if "name" in values and asset["kind"] not in ("manual", "liability"):
                raise Refused("Only a manual asset can be renamed here.")
            return _with_aliases(tx, tx.change("asset", asset_id, version, values, now))

        return self.store.write(scope, run)

    def asset_alias_add(
        self, scope: Scope, asset_id: int, kind: str, value: str, *, now: int
    ) -> dict:
        """An old ISIN or symbol the asset had; imports match on it (4.3)."""
        kind = _choice(kind, ("isin", "symbol"), "a kind of alias")
        text = _text(value, "the alias", NAME_MAX).upper()

        def run(tx: Tx):
            asset = tx.get("asset", asset_id)
            if asset is None:
                raise NotFound()
            held = tx.find_alias(kind, text)
            if held and held["asset_id"] != asset_id:
                raise Refused(f"{text} already belongs to another asset.", reason="duplicate")
            if not held:
                tx.add_alias(asset_id, kind, text, now)
            return _with_aliases(tx, asset)

        return self.store.write(scope, run)

    # ---- entries ----
    def activity_create(
        self,
        scope: Scope,
        tab_id: int,
        version: int,
        entry: Mapping[str, Any],
        request_id: str,
        *,
        now: int,
        dry_run: bool = False,
    ) -> dict:
        """A new entry at the end of the tab's order. Returns the entry, its effect in words and
        the tab's new version; with ``dry_run``, the effect only and nothing written."""
        rid = _request_id(request_id)

        def run(tx: Tx):
            if (again := tx.by_request("activity", rid)) is not None:  # a resend
                return {
                    "activity": again,
                    "effect": _effect_of_saved(tx, again),
                    "tab_version": tx.get("tab", again["tab_id"])["version"],
                }
            tab = _writable_tab(tx, tab_id, version)
            fields = _entry_fields(tx, entry, now)
            candidate = Entry.of({**fields, "seq": tx.next_seq(tab_id)})
            ledger = _replay(tx, tab_id, "add", add=candidate)
            effect = _effect(ledger, candidate)
            if dry_run:
                return {"activity": None, "effect": effect, "tab_version": tab["version"]}
            moved = tx.bump_tab(tab_id, version, now)
            values = {**fields, "tab_id": tab_id, "seq": candidate.seq, "source": "manual"}
            row = tx.insert("activity", values, now, rid)
            return {"activity": row, "effect": effect, "tab_version": moved["version"]}

        return self._guard(self.store.write if not dry_run else self.store.read, scope, run)

    def activity_update(
        self,
        scope: Scope,
        activity_id: int,
        version: int,
        entry: Mapping[str, Any],
        *,
        now: int,
        dry_run: bool = False,
    ) -> dict:
        """The entry replaced as a whole, keeping its place in the tab's order."""

        def run(tx: Tx):
            old = tx.get("activity", activity_id)
            if old is None:
                raise NotFound()
            tab = _writable_tab(tx, old["tab_id"], version)
            fields = _entry_fields(tx, entry, now)
            candidate = Entry.of({**fields, "id": activity_id, "seq": old["seq"]})
            ledger = _replay(tx, old["tab_id"], "change", replace=candidate)
            effect = _effect(ledger, candidate)
            if dry_run:
                return {"activity": None, "effect": effect, "tab_version": tab["version"]}
            moved = tx.bump_tab(old["tab_id"], version, now)
            row = tx.change("activity", activity_id, None, fields, now)
            return {"activity": row, "effect": effect, "tab_version": moved["version"]}

        return self._guard(self.store.write if not dry_run else self.store.read, scope, run)

    def activity_delete(self, scope: Scope, activity_id: int, version: int, *, now: int) -> dict:
        """The entry removed, unless a later one needs it: that one is named."""

        def run(tx: Tx):
            old = tx.get("activity", activity_id)
            if old is None:
                raise NotFound()
            _writable_tab(tx, old["tab_id"], version)
            _replay(tx, old["tab_id"], "delete", drop=old)
            moved = tx.bump_tab(old["tab_id"], version, now)
            tx.remove("activity", activity_id)
            return {"id": activity_id, "tab_version": moved["version"]}

        return self.store.write(scope, run)

    @staticmethod
    def _guard(call, scope, run):
        try:
            return call(scope, run)
        except BadEntry as exc:  # the core's sentence for an entry that is not well formed
            raise Invalid(str(exc)) from exc


# ---- helpers on rows -----------------------------------------------------------------------
def _with_aliases(tx: Tx, asset: dict) -> dict:
    return {
        **asset,
        "aliases": [{"kind": a["kind"], "value": a["value"]} for a in tx.aliases(asset["id"])],
    }


def _writable_tab(tx: Tx, tab_id: int, version: int) -> dict:
    tab = tx.get("tab", tab_id)
    if tab is None:
        raise NotFound()
    if tab["version"] != version:
        raise VersionConflict("version_conflict")
    if tab["system"]:
        raise Refused(WORDS["upstox_tab"])
    if tab["archived"]:
        raise Refused(WORDS["archived"])
    return tab


def _entry_fields(tx: Tx, entry: Mapping[str, Any], now: int) -> dict[str, Any]:
    """The columns of an entry from what the owner sent, checked against the tab's world
    (accounts, assets, quantity step, the date); BadEntry for what the core refuses."""
    if set(entry) - ENTRY_KEYS:
        raise Invalid(WORDS["unknown_field"])
    fields = {key: entry.get(key) for key in ENTRY_COLUMNS}
    fields["status"] = entry.get("status") or "posted"
    detail = fields["charges_detail"]
    if detail is not None:
        allowed = {f"{part}_paise" for part in CHARGE_PARTS}
        if (
            not isinstance(detail, dict)
            or set(detail) - allowed
            or not all(paise_ok(v) for v in detail.values())
        ):
            raise Invalid(WORDS["breakdown"])
        total = sum(detail.values())
        if fields["charges_paise"] in (None, 0):
            fields["charges_paise"] = total
        elif fields["charges_paise"] != total:
            raise Invalid(WORDS["breakdown"])
    fields["charges_paise"] = fields["charges_paise"] or 0
    fields["tds_paise"] = fields["tds_paise"] or 0
    note = fields["note"]
    fields["note"] = _text(note, "the note", NOTE_MAX, required=False) if note else None
    checked = Entry.of(fields)  # well formed for its kind
    for key in ("account_id", "to_account_id"):
        if getattr(checked, key) is not None:
            account = tx.get("account", getattr(checked, key))
            if account is None:
                raise Invalid("Choose the account from the list.")
            if account["system"]:
                raise Refused(WORDS["upstox_account"])
    if checked.asset_id is not None:
        asset = tx.get("asset", checked.asset_id)
        if asset is None:
            raise Invalid("Choose the holding from the list.")
        if asset["kind"] not in HOLDING_KINDS:
            raise Invalid(WORDS["liability"])
        steps = _decimals(asset)
        if checked.qty_u is not None and not step_ok(checked.qty_u, steps):
            raise Invalid(
                SHARED_WORDS["whole_shares"]
                if steps == 0
                else f"A quantity of {asset['name']} can have up to {steps} decimals."
            )
    if checked.status == "posted" and checked.trade_date > day_of_instant(now):
        raise Invalid(SHARED_WORDS["future_date"])
    return fields


def _names(tx: Tx) -> tuple[Names, dict[int, int]]:
    assets = tx.rows("asset")
    names = Names(
        {a["id"]: a["name"] for a in tx.rows("account")},
        {a["id"]: a["name"] for a in assets},
        {a["id"]: _unit(a) for a in assets},
    )
    return names, {a["id"]: _decimals(a) for a in assets if a["kind"] in HOLDING_KINDS}


def _replay(
    tx: Tx,
    tab_id: int,
    action: str,
    *,
    add: Entry | None = None,
    replace: Entry | None = None,
    drop: dict | None = None,
):
    """The tab's ledger with the change applied, or Refused in the ledger's words.

    A refusal of the changed entry itself keeps the ledger's sentence. One of another entry
    (the change would leave a later sale without units) is told as words.knock_on does, naming it.
    """
    entries = []
    for row in tx.entries(tab_id):
        if drop is not None and row["id"] == drop["id"]:
            continue
        entries.append(
            replace if replace is not None and row["id"] == replace.id else Entry.of(row)
        )
    if add is not None:
        entries.append(add)
    names, decimals = _names(tx)
    changed = add or replace
    changed_id = replace.id if replace is not None else None
    try:
        return replay(entries, names=names, decimals=decimals)
    except Refusal as refusal:
        if refusal.entry_id == changed_id:  # the entry itself (a new one has no id)
            raise Refused(
                refusal.message, entry_id=refusal.entry_id, reason=refusal.reason, **refusal.facts
            ) from refusal
        facts = refusal.facts
        kind = changed.kind if changed is not None else drop["kind"]
        unit = names.unit(facts.get("asset_id"))
        text = words.knock_on(action, kind, facts["kind"], facts["day"], unit)
        raise Refused(text, entry_id=refusal.entry_id, reason=refusal.reason) from refusal


def _effect(ledger, entry: Entry) -> dict[str, Any]:
    """What the entry does, in words (a draft does nothing), with its cost and gain."""
    move = next((m for m in ledger.journal if m.entry is entry), None)
    if move is None:
        return {"words": WORDS["draft"], "cost_paise": None, "gain_paise": None}
    buys = entry.kind in ("BUY", "RIGHTS") or (
        entry.kind == "TRANSFER" and entry.account_id is None
    )
    return {
        "words": words.describe(move),
        "cost_paise": move.cost_paise if buys else None,
        "gain_paise": move.gain_paise if entry.kind == "SELL" else None,
    }


def _effect_of_saved(tx: Tx, row: dict) -> dict[str, Any]:
    """The effect of an entry already saved (a resent create), by replaying its tab."""
    entry = Entry.of(row)
    try:
        return _effect(_replay(tx, row["tab_id"], "change", replace=entry), entry)
    except Refused:
        return {"words": "", "cost_paise": None, "gain_paise": None}
