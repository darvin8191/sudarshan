"""Goals (phase P7, lane R: M7.34).

``goals_list``, ``goal_create`` and ``goal_update`` each answer ``GoalsList`` (types.ts): every
goal with what it counts now, how far that is, what to add each month and the months left
(``core/goals.py``). A goal counts the value now of the custom tabs it links, or an amount typed
by hand, never both. All three wait for ``SUDARSHAN_PORTFOLIO_GOALS`` (``ops.switched``) and are
the owner's alone: none is a family op.

A tab that has since been deleted counts as nothing and stays linked until the owner edits the
goal; an archived tab still counts (it still holds what it held).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..core import goals
from ..core.days import day_of_instant, is_day
from ..core.filters import Filters
from ..core.kinds import GOALS_MAX, MAX_PAISE, NAME_MAX
from ..store import Invalid, NotFound, Tx, VersionConflict
from . import op
from .common import answer, parts, scope_now, warmed, whole

RATE_MAX_BP = 5000
FIELDS = ("name", "name_alt", "target_paise", "target_day", "rate_bp", "tab_ids", "fixed_paise")
SAYS = {
    "request": "The request was incomplete. Reload the page.",
    "name": "Enter a name for the goal.",
    "name_long": f"A goal's name can have up to {NAME_MAX} characters.",
    "target": "Enter a target amount above zero.",
    "day": "Choose a target date after today.",
    "rate": "The assumed return must be between 0% and 50% a year.",
    "tabs": "Choose the tabs this goal counts from your own tabs.",
    "fixed": "The amount typed for this goal cannot be below zero.",
    "either": "Choose the tabs this goal counts, or type an amount: one of the two, not both.",
    "member": "Choose whose goal this is from your members.",
    "full": f"You can keep up to {GOALS_MAX} goals for one member. Archive one to add another.",
    "unknown": "That change includes something that cannot be edited here.",
}


def _int(value: Any, low: int, high: int) -> bool:
    return value.__class__ is int and low <= value <= high


def _name(value: Any, *, required: bool) -> str | None:
    text = value.strip() if isinstance(value, str) else None
    if not text:
        if required or value not in (None, ""):
            raise Invalid(SAYS["name"])
        return None
    if len(text) > NAME_MAX:
        raise Invalid(SAYS["name_long"])
    return text


def checked(message: Mapping[str, Any], today: int, *, old_day: int | None = None) -> dict:
    """The fields a request carries, each as it is kept, or the owner's sentence for the first
    that cannot be. ``old_day`` is the target day the goal already has: it may stay, even past."""
    found: dict[str, Any] = {}
    if "name" in message:
        found["name"] = _name(message["name"], required=True)
    if "name_alt" in message:
        found["name_alt"] = _name(message["name_alt"], required=False)
    if "target_paise" in message:
        if not _int(message["target_paise"], 1, MAX_PAISE):
            raise Invalid(SAYS["target"])
        found["target_paise"] = message["target_paise"]
    if "target_day" in message:
        day = message["target_day"]
        if not is_day(day) or (day <= today and day != old_day):
            raise Invalid(SAYS["day"])
        found["target_day"] = day
    if "rate_bp" in message:
        if not _int(message["rate_bp"], 0, RATE_MAX_BP):
            raise Invalid(SAYS["rate"])
        found["rate_bp"] = message["rate_bp"]
    if "tab_ids" in message:
        ids = message["tab_ids"]
        if not isinstance(ids, list) or any(not _int(each, 1, MAX_PAISE) for each in ids):
            raise Invalid(SAYS["tabs"])
        found["tab_ids"] = list(dict.fromkeys(ids))
    if "fixed_paise" in message:
        fixed = message["fixed_paise"]
        if fixed is not None and not _int(fixed, 0, MAX_PAISE):
            raise Invalid(SAYS["fixed"])
        found["fixed_paise"] = fixed
    return found


def _custom_ids(tx: Tx) -> set[int]:
    return {tab["id"] for tab in tx.rows("tab") if not (tab["system"] or tab["kind"] == "upstox")}


def _whole(tx: Tx, row: Mapping[str, Any], linked_before: tuple[int, ...] = ()) -> None:
    """A goal as it would be kept counts tabs or a typed amount, one of the two, and every tab
    newly linked is one of the owner's own."""
    tab_ids, fixed = row.get("tab_ids") or [], row.get("fixed_paise")
    if bool(tab_ids) == (fixed is not None):
        raise Invalid(SAYS["either"])
    if set(tab_ids) - set(linked_before) - _custom_ids(tx):
        raise Invalid(SAYS["tabs"])


def listed(views, tx: Tx, now: int) -> dict[str, Any]:
    """``GoalsList`` (types.ts): archived goals too, the phone folds them away."""
    today = day_of_instant(now)
    tabs = {tab["id"]: tab for tab in tx.rows("tab")}
    worth: dict[int, int] = {}

    def value_of(tab_id: int) -> int:
        if tab_id not in worth:
            tab = tabs.get(tab_id)
            live = None if tab is None else views._live(tx, tab, Filters(), today, now)
            worth[tab_id] = 0 if live is None else live.totals["value_paise"]
        return worth[tab_id]

    found = []
    for row in tx.rows("goal"):
        fixed = row["fixed_paise"]
        value = fixed if fixed is not None else sum(value_of(each) for each in row["tab_ids"])
        found.append(
            {
                **{key: row[key] for key in ("id", "member_id", *FIELDS)},
                "archived": bool(row["archived"]),
                "version": row["version"],
                **goals.figures(
                    row["target_paise"], value, today, row["target_day"], row["rate_bp"]
                ),
            }
        )
    return {"as_of": now, "goals": found}


def _room(tx: Tx, member_id: int, but: int | None = None) -> None:
    kept = [g for g in tx.rows("goal", member_id=member_id) if not g["archived"] and g["id"] != but]
    if len(kept) >= GOALS_MAX:
        raise Invalid(SAYS["full"])


@op("goals_list")
async def goals_list(ctx, message):
    await warmed(ctx)
    _, views = parts(ctx)
    now, scope = ctx.clock(), scope_now()
    return await answer(lambda: views.store.read(scope, lambda tx: listed(views, tx, now)))


@op("goal_create")
async def goal_create(ctx, message):
    await warmed(ctx)
    _, views = parts(ctx)
    now, scope = ctx.clock(), scope_now()

    def run(tx: Tx):
        rid = message.get("request_id")
        if not isinstance(rid, str) or not 1 <= len(rid) <= 64:
            raise Invalid(SAYS["request"])
        if tx.by_request("goal", rid) is None:
            values = checked(message, day_of_instant(now))
            for must, said in (("name", "name"), ("target_paise", "target"), ("target_day", "day")):
                if must not in values:
                    raise Invalid(SAYS[said])
            members = tx.rows("member")
            member_id = message.get("member_id", members[0]["id"] if members else None)
            if member_id not in {member["id"] for member in members}:
                raise Invalid(SAYS["member"])
            row = {"name_alt": None, "rate_bp": goals.DEFAULT_RATE_BP, "tab_ids": [],
                   "fixed_paise": None, **values, "member_id": member_id, "archived": 0}  # fmt: skip
            _whole(tx, row)
            _room(tx, member_id)
            tx.insert("goal", row, now, rid)
        return listed(views, tx, now)

    return await answer(lambda: views.store.write(scope, run))


@op("goal_update")
async def goal_update(ctx, message):
    await warmed(ctx)
    _, views = parts(ctx)
    now, scope = ctx.clock(), scope_now()
    goal_id, version = whole(message, "id"), whole(message, "version")

    def run(tx: Tx):
        old = tx.get("goal", goal_id)
        if old is None:
            raise NotFound()
        if old["version"] != version:
            raise VersionConflict("version_conflict_item")
        values = checked(message, day_of_instant(now), old_day=old["target_day"])
        if "archived" in message:
            if message["archived"].__class__ is not bool:
                raise Invalid(SAYS["unknown"])
            values["archived"] = int(message["archived"])
        new = {**old, **values}
        _whole(tx, new, tuple(old["tab_ids"]))
        if old["archived"] and not new["archived"]:
            _room(tx, old["member_id"], goal_id)
        tx.change("goal", goal_id, version, values, now)
        return listed(views, tx, now)

    return await answer(lambda: views.store.write(scope, run))
