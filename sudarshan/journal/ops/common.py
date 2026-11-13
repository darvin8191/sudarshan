"""What every op module shares: who is asking, running store work off the event loop, and the
owner's words for a refusal (JB2, design 7.2). An op never shows a status or a class name; the
sentence comes from the store or the op, the code from this table."""

from __future__ import annotations

import asyncio
import contextvars
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..errors import ApiError
from ..store import Invalid, NotFound, Refused, Scope, StoreError, VersionConflict

log = logging.getLogger("sudarshan.journal")

OWNER = Scope.owner()
ASK = "The request was incomplete. Reload the page and try again."
UNAVAILABLE = (
    "Your trade journal is not available just now. Your trades are safe; try again in a minute."
)


@dataclass(frozen=True)
class Caller:
    """Who an op answers: the purpose its seal opened under and the principal inside the seal.
    Every purpose of this release reads the owner's journal; the family purpose is held to its
    one counts-only op."""

    purpose: str = "api"
    principal: str = "owner"
    scope: Scope = OWNER


#: Set by the API around one op; an op never sets it. Unset means the owner's bridge.
CALLER: contextvars.ContextVar[Caller] = contextvars.ContextVar("journal_caller")


def caller() -> Caller:
    return CALLER.get(Caller())


async def work(call: Callable[[], Any]) -> Any:
    """Run store work on a thread (it waits on the store's writer) and map its refusals."""
    try:
        return await asyncio.get_running_loop().run_in_executor(None, call)
    except NotFound as exc:
        raise ApiError(404, "not_found", str(exc)) from None
    except VersionConflict as exc:
        raise ApiError(409, "version_conflict", str(exc)) from None
    except Refused as exc:
        raise ApiError(422, "refused", str(exc), exc.field) from None
    except Invalid as exc:
        raise ApiError(400, "bad_request", str(exc), exc.field) from None
    except StoreError as exc:
        log.error("the journal's record failed: %s", exc.cause or exc)
        raise ApiError(503, "unavailable", str(exc)) from None


def need(ctx: Any) -> Any:
    """The open store, or the owner's sentence when there is none."""
    if getattr(ctx, "store", None) is None:
        raise ApiError(503, "unavailable", UNAVAILABLE)
    return ctx.store


def whole(args: Mapping[str, Any], name: str, *, low: int | None = None,
          high: int | None = None, default: Any = ...) -> Any:  # fmt: skip
    """A whole-number argument (never a bool), within bounds; ``default`` when it may be absent."""
    value = args.get(name)
    if value is None and default is not ...:
        return default
    if (
        value.__class__ is not int
        or (low is not None and value < low)
        or (high is not None and value > high)
    ):
        raise ApiError(400, "bad_request", ASK, name)
    return value


def text(args: Mapping[str, Any], name: str, *, limit: int, default: Any = ...) -> Any:
    """A text argument of at most ``limit`` characters, stripped; ``default`` when absent."""
    value = args.get(name)
    if value is None and default is not ...:
        return default
    if not isinstance(value, str) or len(value) > limit:
        raise ApiError(400, "bad_request", ASK, name)
    return value.strip()


def choice(args: Mapping[str, Any], name: str, allowed: tuple[str, ...], default: Any = ...) -> Any:
    """One of ``allowed``; ``default`` when absent."""
    value = args.get(name)
    if value is None and default is not ...:
        return default
    if value not in allowed:
        raise ApiError(400, "bad_request", ASK, name)
    return value


def flag(args: Mapping[str, Any], name: str, default: Any = ...) -> Any:
    """A true or false argument (never a number); ``default`` when absent."""
    value = args.get(name)
    if value is None and default is not ...:
        return default
    if not isinstance(value, bool):
        raise ApiError(400, "bad_request", ASK, name)
    return value
