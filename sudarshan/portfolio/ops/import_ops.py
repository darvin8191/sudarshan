"""Importing and exporting a tab (M7.22): import_preview, import_commit, import_discard and
export_csv, over importer.Importer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..core.days import day_of_instant
from ..importer import Importer
from . import op
from .common import OWNER, maybe_whole, need, whole, work


def _importer(ctx) -> Importer:
    return ctx.part("importer", lambda store: Importer(store, ctx.ensure_resolver()))


@op("import_preview")
async def import_preview(ctx, message: Mapping[str, Any]) -> Any:
    need(ctx)
    importer, now = _importer(ctx), ctx.clock()
    return await work(
        lambda: importer.preview(
            OWNER,
            whole(message, "id"),
            message.get("format"),
            message.get("filename"),
            message.get("text"),
            maybe_whole(message, "account_id"),
            now=now,
        )
    )


@op("import_commit")
async def import_commit(ctx, message: Mapping[str, Any]) -> Any:
    need(ctx)
    importer, now = _importer(ctx), ctx.clock()
    return await work(
        lambda: importer.commit(OWNER, whole(message, "id"), whole(message, "version"), now=now)
    )


@op("import_discard")
async def import_discard(ctx, message: Mapping[str, Any]) -> Any:
    need(ctx)
    importer, now = _importer(ctx), ctx.clock()
    return await work(
        lambda: importer.discard(
            OWNER, whole(message, "id"), maybe_whole(message, "version"), now=now
        )
    )


@op("export_csv")
async def export_csv(ctx, message: Mapping[str, Any]) -> Any:
    need(ctx)
    importer, now = _importer(ctx), ctx.clock()
    return await work(
        lambda: importer.export(OWNER, whole(message, "id"), today=day_of_instant(now))
    )
