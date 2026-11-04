"""A tab as the phone reads and arranges it (M7.23, M7.25, M7.30): the tab itself, its view, its
performance, its layout and its saved views, over views.Views, book.Book and layout.py."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .. import __version__, layout, serialize
from ..store import NotFound, Refused, VersionConflict
from . import op
from .common import OWNER, answer, given, maybe_whole, parts, whole


# ---- state, summary and the tab -----------------------------------------------------------------
@op("state")
async def state(ctx, message):
    _, views = parts(ctx)
    now = ctx.clock()
    return await answer(lambda: views.state(OWNER, now=now, version=__version__))


@op("summary")
async def summary(ctx, message):
    _, views = parts(ctx)
    now = ctx.clock()
    return await answer(lambda: views.summary(OWNER, now=now))


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
    _, views = parts(ctx)
    now = ctx.clock()
    args = given(message, "as_of", "accounts", "classes", "tags", "held")
    args["view"] = maybe_whole(message, "view")
    tab_id = whole(message, "id")
    return await answer(lambda: views.tab_view(OWNER, tab_id, args, now=now))


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
    _, views = parts(ctx)
    now = ctx.clock()
    return await answer(lambda: views.performance(OWNER, whole(message, "id"), now=now))


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
