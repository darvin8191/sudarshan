"""A tab as the phone reads and arranges it (M7.23, M7.25, M7.30): the tab itself, its view, its
performance, its layout and its saved views, over views.Views, book.Book and layout.py."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from .. import __version__, layout, serialize
from ..store import NotFound, Refused, VersionConflict
from . import op
from .common import OWNER, answer, caller, given, maybe_whole, parts, scope_now, warmed, whole

log = logging.getLogger(__name__)

#: The Upstox row of the dashboard card when the holdings could not be read at all.
UPSTOX_NOT_READ = "Upstox holdings could not be read. Open Portfolio to try again."
#: ...and while the read is still going: Upstox is slow, and the service is waiting for it.
UPSTOX_READING = "Upstox is slow to answer. SUDARSHAN is still reading your account."
#: How long the summary waits for the Upstox part before it answers with the tabs' part alone. The
#: bridge gives a read 3 seconds (core/kinds.TIMEOUTS_S), and the views and the price book have
#: used some of them, so the wait is a third of the whole.
UPSTOX_WAIT_S = 1.0


async def _upstox_row(ctx, scope=OWNER, principal: str = "owner") -> dict[str, Any]:
    """The Upstox row as the reader gives it; a failure is the row saying it was not read. The part
    has passed the scope's mask, so a field the mask took (a member with quantities hidden loses
    the note) is read as absent: the row says what the mask allows, never "not read"."""
    try:
        extra = {} if principal == "owner" else {"principal": principal}
        book = await ctx.ensure_upstox().book(["holdings"], **extra)
        part = serialize.out(scope, book)["holdings"]
        return {
            "state": part.get("state"),
            "read_at": part.get("read_at"),
            "note": part.get("note"),
            "holdings": part.get("data") or [],
        }
    except Exception:
        # The card still shows the owner's own tabs; the Upstox row says it was not read.
        log.exception("The dashboard card could not read the Upstox holdings")
        return {"state": "unavailable", "read_at": None, "note": UPSTOX_NOT_READ, "holdings": []}


async def _upstox_within(ctx, scope=OWNER, principal: str = "owner") -> dict[str, Any]:
    """The Upstox row, or a row that says it is still being read once ``UPSTOX_WAIT_S`` has gone.
    The read itself goes on (the reader keeps its answer for the phone's next ask), so a slow
    Upstox costs the card one line and never the owner's own tabs."""
    # The reads that went on past the wait are held (one at a time) so that nothing drops them
    # half way, and a summary asked while one is out joins it instead of starting another.
    # One read per scope: a family member's row is masked, the owner's is not, and they never
    # share a task (the reader's own memory still makes them one Upstox call).
    key = (scope.principal, scope.tab_ids, scope.mask, principal)
    out: dict = ctx.part("summary_reads", lambda _store: {})
    task = out.get(key)
    if task is None or task.done():
        task = asyncio.ensure_future(_upstox_row(ctx, scope, principal))
        out[key] = task
        task.add_done_callback(
            lambda done, key=key: out.pop(key, None) if out.get(key) is done else None
        )
    try:
        return await asyncio.wait_for(asyncio.shield(task), UPSTOX_WAIT_S)
    except TimeoutError:
        return {"state": "reading", "read_at": None, "note": UPSTOX_READING, "holdings": []}


# ---- state, summary and the tab -----------------------------------------------------------------
@op("state")
async def state(ctx, message):
    _, views = parts(ctx)
    now, scope = ctx.clock(), scope_now()
    return await answer(lambda: views.state(scope, now=now, version=__version__))


@op("summary")
async def summary(ctx, message):
    """The dashboard card (M3.11): the pinned custom tabs with their live-math inputs, and the
    Upstox tab's holdings as the reader last read them (its five seconds of memory, the service's
    own budget), so the card prices every row from the same ticks. When Upstox cannot be read the
    row carries the reader's state and sentence, never a failure of the whole card. A slow Upstox
    is waited for only ``UPSTOX_WAIT_S``: after that the row is state ``reading`` and the phone
    asks ``upstox_book`` (which has its own, longer time) for the figures."""
    await warmed(ctx)
    _, views = parts(ctx)
    now, scope, who = ctx.clock(), scope_now(), caller()
    found = await answer(lambda: views.summary(scope, now=now))
    entries = found.get("entries") if isinstance(found, dict) else None
    if entries and any(e["tab"]["system"] for e in entries):
        row = await _upstox_within(ctx, scope, who.principal)
        for entry in entries:
            if entry["tab"]["system"]:
                entry["upstox"] = row
    return found


@op("tab_create")
async def tab_create(ctx, message):
    book, _ = parts(ctx)
    now = ctx.clock()
    more = given(message, "name_alt", "icon", "colour")
    return await answer(
        lambda: book.tab_create(
            OWNER, message.get("request_id"), message.get("name"), message.get("kind"), now=now,
            pinned=bool(message.get("pinned")), **more,
        ),
        serialize.tab,
    )  # fmt: skip


@op("tab_get")
async def tab_get(ctx, message):
    await warmed(ctx)
    _, views = parts(ctx)
    now, scope = ctx.clock(), scope_now()
    args = given(message, "as_of", "accounts", "classes", "tags", "held")
    args["view"] = maybe_whole(message, "view")
    tab_id = whole(message, "id")
    return await answer(lambda: views.tab_view(scope, tab_id, args, now=now))


@op("tab_update")
async def tab_update(ctx, message):
    book, _ = parts(ctx)
    now = ctx.clock()
    changes = given(
        message, "name", "name_alt", "icon", "colour", "position", "pinned", "archived",
        "manual_old_days",
    )  # fmt: skip
    return await answer(
        lambda: book.tab_update(
            OWNER, whole(message, "id"), whole(message, "version"), changes, now=now
        ),
        serialize.tab,
    )


@op("tab_delete")
async def tab_delete(ctx, message):
    book, _ = parts(ctx)
    now = ctx.clock()
    return await answer(
        lambda: book.tab_delete(
            OWNER,
            whole(message, "id"),
            whole(message, "version"),
            message.get("confirm_name"),
            now=now,
        )
    )


@op("performance")
async def performance(ctx, message):
    await warmed(ctx)
    _, views = parts(ctx)
    now = ctx.clock()
    scope = scope_now()
    return await answer(lambda: views.performance(scope, whole(message, "id"), now=now))


# ---- layout --------------------------------------------------------------------------------------
@op("layout_get")
async def layout_get(ctx, message):
    book, _ = parts(ctx)
    tab_id = whole(message, "id")

    def run(tx):
        tab = tx.get("tab", tab_id)
        if tab is None:
            raise NotFound()
        return layout.layout_state(tx, tab)

    return await answer(lambda: book.store.read(OWNER, run))


@op("layout_set")
async def layout_set(ctx, message):
    book, _ = parts(ctx)
    tab_id, version, now = whole(message, "id"), whole(message, "version"), ctx.clock()

    def run(tx):
        tab = tx.get("tab", tab_id)
        if tab is None:
            raise NotFound()
        wanted = message.get("spec")
        spec = (
            layout.defaults_for(tab["kind"])
            if wanted is None
            else layout.clean_spec(wanted, tab["kind"])
        )
        tx.layout_save(tab["id"], version, spec, now)
        return layout.layout_state(tx, tab)

    return await answer(lambda: book.store.write(OWNER, run))


# ---- saved views -----------------------------------------------------------------------------------
@op("view_list")
async def view_list(ctx, message):
    book, _ = parts(ctx)
    tab_id = whole(message, "id")

    def run(tx):
        if tx.get("tab", tab_id) is None:
            raise NotFound()
        return {"views": layout.views_of(tx, tab_id)}

    return await answer(lambda: book.store.read(OWNER, run))


def _taken(tx, tab_id: int, name: str, except_id: int | None = None) -> bool:
    return any(
        v["name"].casefold() == name.casefold() and v["id"] != except_id
        for v in tx.rows("saved_view", "id", tab_id=tab_id)
    )


@op("view_create")
async def view_create(ctx, message):
    book, _ = parts(ctx)
    tab_id, now = whole(message, "id"), ctx.clock()

    def run(tx):
        tab = tx.get("tab", tab_id)
        if tab is None:
            raise NotFound()
        name = layout.view_name(message.get("name"))
        spec = layout.clean_view(message.get("spec"), tab["kind"])
        same = next(
            (v for v in layout.views_of(tx, tab_id) if v["name"].casefold() == name.casefold()),
            None,
        )
        if same is not None:
            if same["spec"] == spec:  # the same view sent again: the one already made
                return same
            raise Refused(layout.WORDS["name_taken"])
        if tx.count("saved_view", tab_id=tab_id) >= layout.MAX_VIEWS:
            raise Refused(layout.WORDS["views_full"])
        row = tx.insert(
            "saved_view",
            {
                "tab_id": tab_id,
                "name": name,
                "spec": spec,
                "is_default": bool(message.get("is_default")),
            },
            now,
        )
        if row["is_default"]:
            layout.only_default(tx, tab_id, row["id"], now)
        return layout.view_of(row)

    return await answer(lambda: book.store.write(OWNER, run))


@op("view_update")
async def view_update(ctx, message):
    book, _ = parts(ctx)
    view_id, version, now = whole(message, "id"), whole(message, "version"), ctx.clock()

    def run(tx):
        row = layout.one_view(tx, view_id)
        if row["version"] != version:
            raise VersionConflict("version_conflict_item")
        tab = tx.get("tab", row["tab_id"])
        values: dict[str, Any] = {}
        if "name" in message:
            values["name"] = layout.view_name(message["name"])
            if _taken(tx, row["tab_id"], values["name"], view_id):
                raise Refused(layout.WORDS["name_taken"])
        if "spec" in message:
            values["spec"] = layout.clean_view(message["spec"], tab["kind"])
        if "is_default" in message:
            values["is_default"] = bool(message["is_default"])
        changed = tx.change("saved_view", view_id, version, values, now)
        if changed["is_default"]:
            layout.only_default(tx, row["tab_id"], view_id, now)
        return layout.view_of(changed)

    return await answer(lambda: book.store.write(OWNER, run))


@op("view_delete")
async def view_delete(ctx, message):
    book, _ = parts(ctx)
    view_id, version = whole(message, "id"), whole(message, "version")

    def run(tx):
        row = layout.one_view(tx, view_id)
        if row["version"] != version:
            raise VersionConflict("version_conflict_item")
        tx.remove("saved_view", view_id)
        return {"id": view_id}

    return await answer(lambda: book.store.write(OWNER, run))
