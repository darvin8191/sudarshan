"""What the owner shares with a family member and what each member may see (M7.37; cross-design
Y6, Y24, Y28), over the two tables of schema step 4.

* ``tab_share``: one row per tab and viewer. The viewer is a member id (``"12"``) or a role
  (``"role:child"``); a row of the member's own beats the role's for the same tab. A share has a
  level: ``view`` (the member sees what their privacy allows) or ``restricted`` (no figures at all)
  and may expire; an expired row is ignored, never deleted behind the owner's back.
* ``member_privacy``: the four categories as shown or hidden, beside the member's role. It is the
  floor the family purpose applies; what a caller sends can only add to it (Y6). A member with no
  row sees no figures. P&L needs quantities (Y24, privacy.py).

The ``funds`` category is the funds share: funds are served to a member only while it is shown,
and the Upstox tab is shared with them. Everything here is plain functions over a ``Tx`` (the
caller runs them on the store's thread) plus ``Access``, what a request is allowed to touch.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from . import privacy, serialize
from .core.kinds import PRIVACY_CATEGORIES, SHARE_LEVELS
from .errors import ApiError
from .store import Invalid, NotFound, Scope, Tx, VersionConflict

NOT_SHARED = "Not shared with you."
ROLE = re.compile(r"role:[a-z0-9_-]{1,30}")
MEMBER = re.compile(r"[A-Za-z0-9_-]{1,40}")
MAX_ARG_MASK = 200
MAX_ARG_TABS = 100


def viewer_key(raw: Any) -> str:
    """The viewer a share names: a member id or ``role:<role>``; Invalid for anything else."""
    text = str(raw) if isinstance(raw, int) and not isinstance(raw, bool) else raw
    if isinstance(text, str) and (ROLE.fullmatch(text) or MEMBER.fullmatch(text)):
        return text
    raise Invalid("Choose a family member or a role to share with.")


def member_key(raw: Any) -> str:
    text = viewer_key(raw)
    if text.startswith("role:"):
        raise Invalid("Choose a family member.")
    return text


# ---- the owner's writes and reads ----------------------------------------------------------------
def share_set(
    tx: Tx,
    tab_id: Any,
    viewer: Any,
    level: Any,
    expires_at: Any,
    version: Any,
    now: int,
) -> dict[str, Any]:
    """Share a tab with a member or a role, or change a share. A new share is made with no
    ``version``; changing one names the version it was decided on (VersionConflict otherwise)."""
    if isinstance(tab_id, bool) or not isinstance(tab_id, int):
        raise Invalid("Choose a tab to share.")
    if level not in SHARE_LEVELS:
        raise Invalid("Choose whether the member sees the tab or sees it without figures.")
    if expires_at is not None and (
        isinstance(expires_at, bool) or not isinstance(expires_at, int) or expires_at <= now
    ):
        raise Invalid("The share must end after now.")
    key = viewer_key(viewer)
    if tx.get("tab", tab_id) is None:
        raise NotFound()
    found = tx.rows("tab_share", tab_id=tab_id, viewer=key)
    values = {"level": level, "expires_at": expires_at}
    if not found:
        if version is not None:
            raise VersionConflict()
        return tx.insert("tab_share", {"tab_id": tab_id, "viewer": key, **values}, now)
    if version is None:
        raise VersionConflict()
    return tx.change("tab_share", found[0]["id"], version, values, now)


def share_remove(tx: Tx, tab_id: Any, viewer: Any, version: Any) -> bool:
    found = tx.rows("tab_share", tab_id=tab_id, viewer=viewer_key(viewer))
    if not found:
        raise NotFound()
    if found[0]["version"] != version:
        raise VersionConflict()
    return tx.remove("tab_share", found[0]["id"])


def shares_list(tx: Tx, tab_id: Any = None, viewer: Any = None) -> list[dict[str, Any]]:
    equal: dict[str, Any] = {}
    if tab_id is not None:
        equal["tab_id"] = tab_id
    if viewer is not None:
        equal["viewer"] = viewer_key(viewer)
    return tx.rows("tab_share", "tab_id,viewer", **equal)


def privacy_row(tx: Tx, member: str) -> dict[str, Any] | None:
    found = tx.rows("member_privacy", member=member)
    return found[0] if found else None


def privacy_set(
    tx: Tx, member: Any, role: Any, shown: Mapping[str, Any], version: Any, now: int
) -> dict[str, Any]:
    """Store a member's four categories (and their role). ``pnl`` shown with ``qty`` hidden is
    refused with the owner's sentence (Y24). A first row is made with no ``version``."""
    key = member_key(member)
    if role is not None and not (isinstance(role, str) and re.fullmatch(r"[a-z0-9_-]{1,30}", role)):
        raise Invalid("A role is a short lower case word.")
    if not isinstance(shown, Mapping) or set(shown) != set(PRIVACY_CATEGORIES):
        raise Invalid("Say for each of P&L, quantities, funds and orders whether it is shown.")
    flags = {name: shown[name] for name in PRIVACY_CATEGORIES}
    if not all(isinstance(v, bool) for v in flags.values()):
        raise Invalid("Say for each of P&L, quantities, funds and orders whether it is shown.")
    sentence = privacy.valid_shown(flags)
    if sentence:
        raise Invalid(sentence)
    values = {f"show_{name}": flags[name] for name in PRIVACY_CATEGORIES} | {"role": role}
    values = {k: (int(v) if isinstance(v, bool) else v) for k, v in values.items()}
    found = privacy_row(tx, key)
    if found is None:
        if version is not None:
            raise VersionConflict()
        return tx.insert("member_privacy", {"member": key, **values}, now)
    if version is None:
        raise VersionConflict()
    return tx.change("member_privacy", found["id"], version, values, now)


def shown_of(row: Mapping[str, Any] | None) -> dict[str, bool] | None:
    """The row's categories as ``{category: shown}``; None when the member has no row."""
    if row is None:
        return None
    return {name: bool(row[f"show_{name}"]) for name in PRIVACY_CATEGORIES}


# ---- the family purpose --------------------------------------------------------------------------
@dataclass(frozen=True)
class Access:
    """What one family request may touch: the member, their shared tabs (with levels), the
    categories hidden from them and the scope built from all of it.

    ``scope`` is the base: every shared tab, and the stored floor plus what the caller added. A
    ``restricted`` share shows no figures of that tab, and only of that tab (L6): an answer about
    some tabs takes its scope from ``scope_about``, and ``by_tab`` masks each part of an answer
    that names a restricted tab."""

    member: str
    scope: Scope
    shares: tuple[dict[str, Any], ...]
    hidden: frozenset[str]
    upstox_tab_id: int | None

    @property
    def restricted(self) -> frozenset[int]:
        """The shared tabs whose share is ``restricted``."""
        return frozenset(s["tab_id"] for s in self.shares if s["level"] == "restricted")

    def scope_about(self, tab_ids: Iterable[int] | None = None) -> Scope:
        """The scope of an answer about ``tab_ids`` (None: every shared tab): narrowed to them,
        and with no figures at all when one of them is restricted."""
        shared = self.scope.tab_ids or frozenset()
        ids = shared if tab_ids is None else frozenset(tab_ids) & shared
        cut = serialize.VIEWER_MASK if ids & self.restricted else frozenset()
        return Scope(self.scope.principal, ids, self.scope.mask | cut)

    def by_tab(self, data: Any) -> Any:
        """``data`` with every part that names a restricted tab (a mapping whose ``tab`` is that
        tab: a summary entry, a tab's view) left without figures, at any depth."""
        restricted = self.restricted
        if not restricted:
            return data

        def walk(node: Any) -> Any:
            if isinstance(node, Mapping):
                tab = node.get("tab")
                if isinstance(tab, Mapping) and tab.get("id") in restricted:
                    return serialize.masked(node, serialize.VIEWER_MASK)
                return {key: walk(value) for key, value in node.items()}
            if isinstance(node, (list, tuple)):
                return [walk(item) for item in node]
            return node

        return walk(data)

    @property
    def funds_shown(self) -> bool:
        return "funds" not in self.hidden

    @property
    def orders_shown(self) -> bool:
        return "orders" not in self.hidden

    def need_upstox_tab(self) -> None:
        """The Upstox account is read only for a member it is shared with."""
        if self.upstox_tab_id is None or self.upstox_tab_id not in (self.scope.tab_ids or ()):
            raise ApiError(403, "forbidden", NOT_SHARED)

    def book_parts(self, asked: list[str] | None) -> list[str]:
        """The parts of the Upstox book this member may read. Left unasked, the ones they may;
        asked for one they may not (funds without a funds share, orders when hidden), refused."""
        from .core.kinds import UPSTOX_PAGE_PARTS, UPSTOX_PARTS

        self.need_upstox_tab()
        allowed = [
            p
            for p in UPSTOX_PARTS
            if (p != "funds" or self.funds_shown)
            and (p not in ("orders", "trades") or self.orders_shown)
        ]
        if asked is None:
            return [p for p in allowed if p in UPSTOX_PAGE_PARTS or p in ("orders", "trades")]
        if not all(p in allowed for p in asked):
            raise ApiError(403, "forbidden", NOT_SHARED)
        return asked


def args_tabs(raw: Any) -> frozenset[int] | None:
    if raw is None:
        return None
    ok = isinstance(raw, list) and len(raw) <= MAX_ARG_TABS
    if not ok or not all(isinstance(t, int) and not isinstance(t, bool) for t in raw):
        raise ApiError(400, "bad_request", "The request was incomplete. Reload the page.")
    return frozenset(raw)


def args_mask(raw: Any) -> frozenset[str]:
    if raw is None:
        return frozenset()
    ok = isinstance(raw, list) and len(raw) <= MAX_ARG_MASK
    if not ok or not all(isinstance(m, str) and 0 < len(m) <= 64 for m in raw):
        raise ApiError(400, "bad_request", "The request was incomplete. Reload the page.")
    return frozenset(raw)


def access_for(tx: Tx, member: str, now: int, tabs: Any = None, mask: Any = None) -> Access:
    """The member's access, from the stored shares and privacy (Y6).

    * tabs: the shared tabs (own rows beat the role's), unexpired, intersected with ``tabs`` when
      the caller names some;
    * mask: the stored floor, plus what the caller adds. A ``restricted`` share's mask goes with
      its own tab only (``Access.scope_about`` and ``Access.by_tab``).
    """
    row = privacy_row(tx, member)
    rows = tx.rows("tab_share", viewer=member)
    if row is not None and row.get("role"):
        rows += tx.rows("tab_share", viewer=f"role:{row['role']}")
    own = {r["tab_id"] for r in rows if r["viewer"] == member}
    shares: dict[int, dict[str, Any]] = {}
    for share in rows:
        if share["expires_at"] is not None and share["expires_at"] <= now:
            continue
        if share["viewer"] != member and share["tab_id"] in own:
            continue  # the member's own row, even an expired one, is the one that counts
        shares[share["tab_id"]] = share
    wanted = args_tabs(tabs)
    shared = frozenset(shares) if wanted is None else frozenset(shares) & wanted
    hidden = privacy.hidden_categories(shown_of(row))
    floor = privacy.mask_of(hidden)
    scope = Scope("owner", shared, floor | args_mask(mask))
    system = tx.rows("tab", system=1)
    upstox_tab = system[0]["id"] if system else None
    return Access(
        member=member,
        scope=scope,
        shares=tuple(shares[t] for t in sorted(shared)),
        hidden=hidden,
        upstox_tab_id=upstox_tab,
    )
