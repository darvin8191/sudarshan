"""What every op module shares: running store work off the event loop, and the owner's words for
a refusal (design 7.2). An op never shows a status or a class name; the sentence comes from the
store or the ledger and the code from this table."""

from __future__ import annotations

import asyncio
import contextvars
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .. import serialize
from ..errors import ApiError
from ..store import Invalid, NotFound, Refused, Scope, StoreError, VersionConflict

log = logging.getLogger(__name__)

OWNER = Scope.owner()


@dataclass(frozen=True)
class Caller:
    """Who an op answers: the purpose its seal opened under, the principal inside the seal, and
    the scope the rows and the mask come from. The family purpose builds the scope from the
    member's shares and privacy (sharing.py); the owner's and the journal's are the owner's."""

    purpose: str = "owner"
    principal: str = "owner"
    scope: Scope = OWNER
    #: Family only: what the member may see of the account (sharing.Access).
    access: Any = field(default=None, compare=False)


#: Set by the API around one op; an op never sets it. Unset means the owner.
CALLER: contextvars.ContextVar[Caller] = contextvars.ContextVar("portfolio_caller")


def caller() -> Caller:
    return CALLER.get(Caller())


def scope_now() -> Scope:
    """The scope of the request being answered. Read it on the loop, before handing work to a
    thread: a thread started by ``run_in_executor`` does not carry the request's context."""
    return caller().scope


async def work(call: Callable[[], Any]) -> Any:
    """Run store work on a thread (it waits on the writer) and map its refusals to the API's."""
    try:
        return await asyncio.get_running_loop().run_in_executor(None, call)
    except NotFound as exc:
        raise ApiError(404, "not_found", str(exc)) from None
    except VersionConflict as exc:
        raise ApiError(409, "version_conflict", str(exc)) from None
    except Refused as exc:
        raise ApiError(422, "refused", str(exc), entry_id=exc.entry_id) from None
    except Invalid as exc:
        raise ApiError(400, "bad_request", str(exc)) from None
    except StoreError as exc:
        log.error("the portfolio's record failed: %s", exc.cause or exc)
        raise ApiError(503, "unavailable", str(exc)) from None


def need(ctx: Any) -> Any:
    """The open store, or the owner's sentence when there is none."""
    if ctx.store is None:
        raise ApiError(
            503, "unavailable", "Your portfolio is not available just now. Try again in a minute."
        )
    return ctx.store


def whole(message: Mapping[str, Any], name: str) -> int:
    """A whole-number argument, or the sentence that asks for it."""
    value = message.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError(400, "bad_request", "The request was incomplete. Reload the page.", name)
    return value


def maybe_whole(message: Mapping[str, Any], name: str) -> int | None:
    return None if message.get(name) is None else whole(message, name)


def parts(ctx: Any):
    """``(book, views)`` for the open store, built once for it."""
    from ..book import Book
    from ..views import Views

    need(ctx)
    return ctx.part("book", Book), ctx.part(
        "views", lambda store: Views(store, ctx.ensure_prices())
    )


async def warmed(ctx: Any) -> None:
    """Bring the price book's feed data into memory before a read that values holdings."""
    book = ctx.ensure_prices()
    if hasattr(book, "warm"):
        try:
            bounded = getattr(book, "warm_for_read", None) or book.warm
            await bounded(need(ctx), ctx.clock())
        except Exception:  # prices degrade to the last closing record; the read goes on
            log.exception("the price book could not be warmed")


async def answer(call: Callable[[], Any], shape: Callable[[Any], Any] = lambda data: data) -> Any:
    """Run store work, shape what it returns for the phone and pass it through the scope's mask:
    every op ends here, so the mask is applied in one place."""
    scope = scope_now()
    return serialize.out(scope, shape(await work(call)))


def given(message: Mapping[str, Any], *names: str) -> dict[str, Any]:
    """The named arguments the request carries (an argument left out stays out)."""
    return {name: message[name] for name in names if name in message}
