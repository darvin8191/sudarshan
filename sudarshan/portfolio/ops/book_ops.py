"""The owner's books: members, accounts, assets, a manual value and the entries of a tab (M7.18),
over book.Book. Each op reads its arguments, calls one Book method and answers in the contract's
shape; the rules (versions, refusals, the ledger's check) are Book's."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from .. import serialize
from ..core.days import day_of_instant, is_day
from ..core.money import price_ok
from ..errors import ApiError
from ..resolver import WORDS as RESOLVER_WORDS
from ..resolver import ResolverUnavailable
from ..store import Invalid, NotFound, Refused
from . import op
from .common import OWNER, answer, given, maybe_whole, parts, whole

WORDS = {
    "manual_only": "Only a value you keep by hand can be set here. A share or a fund is priced for you.",
    "value": "Enter the value with a real date and a price above zero.",
    "future": "A value cannot be dated after today.",
    "balance": "Enter the outstanding balance with a real date; 0 once the loan is paid off.",
    "entry": "The entry was incomplete. Reload the page and enter it again.",
}


def _text(message: Mapping[str, Any], name: str) -> Any:
    return message.get(name)


def _entry(message: Mapping[str, Any]) -> Mapping[str, Any]:
    entry = message.get("entry")
    if not isinstance(entry, Mapping):
        raise Invalid(WORDS["entry"])
    return entry


# ---- members and accounts -----------------------------------------------------------------------
@op("member_create")
async def member_create(ctx, message):
    book, _ = parts(ctx)
    now = ctx.clock()
    return await answer(
        lambda: book.member_create(
            OWNER, message.get("request_id"), message.get("name"), now=now,
            relation=message.get("relation"),
        ),
        serialize.member,
    )  # fmt: skip


@op("member_update")
async def member_update(ctx, message):
    book, _ = parts(ctx)
    now = ctx.clock()
    return await answer(
        lambda: book.member_update(
            OWNER, whole(message, "id"), whole(message, "version"),
            given(message, "name", "relation"), now=now,
        ),
        serialize.member,
    )  # fmt: skip


@op("account_create")
async def account_create(ctx, message):
    book, _ = parts(ctx)
    now = ctx.clock()
    return await answer(
        lambda: book.account_create(
            OWNER, message.get("request_id"), whole(message, "member_id"), message.get("name"),
            message.get("kind"), now=now, broker=message.get("broker"), note=message.get("note"),
        ),
        serialize.account,
    )  # fmt: skip


@op("account_update")
async def account_update(ctx, message):
    book, _ = parts(ctx)
    now = ctx.clock()
    return await answer(
        lambda: book.account_update(
            OWNER, whole(message, "id"), whole(message, "version"),
            given(message, "name", "kind", "broker", "note"), now=now,
        ),
        serialize.account,
    )  # fmt: skip


# ---- assets and a manual value --------------------------------------------------------------------
@op("asset_create")
async def asset_create(ctx, message):
    book, _ = parts(ctx)
    now = ctx.clock()
    more = given(
        message, "name", "symbol", "exchange", "isin", "amfi_code", "unit_label", "asset_class",
        "sector", "mcap", "first_value",
    )  # fmt: skip
    if message.get("tags") is not None:
        more["tags"] = message["tags"]
    if message.get("kind") == "liability":  # M7.19 V2: a loan belongs to a tab
        more["tab_id"] = maybe_whole(message, "tab_id")
        more["version"] = maybe_whole(message, "version")
    if message.get("kind") == "listed":
        more = await _resolved(ctx, more, now)
    return await answer(
        lambda: book.asset_create(
            OWNER, message.get("request_id"), message.get("kind"), now=now, **more
        ),
        serialize.asset,
    )


async def _resolved(ctx, more: dict, now: int) -> dict:
    """A listed share is what the master contract says it is: its instrument key, ISIN, name and
    tick size come from there, and a symbol it does not know is refused in words."""
    resolver = ctx.ensure_resolver()
    if resolver is None:
        raise ApiError(503, "unavailable", RESOLVER_WORDS["unavailable"])
    symbol, exchange = more.get("symbol"), more.get("exchange")
    if not isinstance(symbol, str) or exchange not in ("NSE", "BSE"):
        raise ApiError(400, "bad_request", RESOLVER_WORDS["exchange"])

    def look():
        try:
            return resolver.find(symbol, exchange)
        except ResolverUnavailable as exc:
            raise ApiError(503, "unavailable", str(exc)) from None

    found = await asyncio.get_running_loop().run_in_executor(None, look)
    if found is None:
        raise ApiError(
            404,
            "not_found",
            RESOLVER_WORDS["unknown"].format(symbol=symbol.upper(), exchange=exchange),
        )
    return {
        **more,
        "symbol": found.symbol,
        "exchange": found.exchange,
        "name": more.get("name") or found.name,
        "isin": found.isin,
        "instrument_key": found.key,
        "tick_size_u": found.tick_size_u,
        "resolved_at": now,
    }


@op("asset_update")
async def asset_update(ctx, message):
    book, _ = parts(ctx)
    now = ctx.clock()
    return await answer(
        lambda: book.asset_update(
            OWNER, whole(message, "id"), whole(message, "version"),
            given(message, "name", "asset_class", "sector", "mcap", "tags"), now=now,
        ),
        serialize.asset,
    )  # fmt: skip


@op("price_point_set")
async def price_point_set(ctx, message):
    """The owner's own value of a manual asset (or the balance of a loan) on a day; the last one
    entered for a day stands."""
    book, _ = parts(ctx)
    asset_id, day, price_u, now = (
        whole(message, "id"),
        message.get("day"),
        message.get("price_u"),
        ctx.clock(),
    )

    tab_id = maybe_whole(message, "tab_id")  # M7.19 V2: links a loan to the tab it is set from

    def run(tx):
        asset = tx.get("asset", asset_id)
        if asset is None:
            raise NotFound()
        if asset["kind"] not in ("manual", "liability"):
            raise Refused(WORDS["manual_only"])
        # A loan's balance may reach 0 (paid off); a value kept by hand is above zero.
        loan = asset["kind"] == "liability"
        if not (is_day(day) and price_ok(price_u) and (price_u > 0 or loan)):
            raise Invalid(WORDS["balance"] if loan else WORDS["value"])
        if day > day_of_instant(now):
            raise Invalid(WORDS["future"])
        tx.set_price_point(asset_id, day, price_u, now)
        if asset["kind"] == "liability" and tab_id is not None:
            tab = tx.get("tab", tab_id)
            if tab is not None and not tab["system"] and tab["kind"] != "upstox":
                tx.link_liability(tab_id, asset_id, now)
        return {"asset_id": asset_id, "day": day, "price_u": price_u}

    return await answer(lambda: book.store.write(OWNER, run))


# ---- the entries of a tab -------------------------------------------------------------------------
def _saved(found: dict) -> dict:
    return {
        **found,
        "activity": None if found["activity"] is None else serialize.activity(found["activity"]),
    }


@op("activities_list")
async def activities_list(ctx, message):
    book, _ = parts(ctx)
    return await answer(
        lambda: book.activities_list(
            OWNER, whole(message, "id"), before=message.get("before"),
            limit=message.get("limit") or 50, asset_id=maybe_whole(message, "asset"),
        ),
        lambda page: {"items": [serialize.activity(a) for a in page["items"]], "next": page["next"]},
    )  # fmt: skip


@op("activity_create")
async def activity_create(ctx, message):
    book, _ = parts(ctx)
    now = ctx.clock()
    return await answer(
        lambda: book.activity_create(
            OWNER, whole(message, "id"), whole(message, "version"), _entry(message),
            message.get("request_id"), now=now, dry_run=bool(message.get("dry_run")),
        ),
        _saved,
    )  # fmt: skip


@op("activity_update")
async def activity_update(ctx, message):
    book, _ = parts(ctx)
    now = ctx.clock()
    return await answer(
        lambda: book.activity_update(
            OWNER, whole(message, "id"), whole(message, "version"), _entry(message),
            now=now, dry_run=bool(message.get("dry_run")),
        ),
        _saved,
    )  # fmt: skip


@op("activity_delete")
async def activity_delete(ctx, message):
    book, _ = parts(ctx)
    now = ctx.clock()
    return await answer(
        lambda: book.activity_delete(
            OWNER, whole(message, "id"), whole(message, "version"), now=now
        )
    )
