"""What every op module shares: running store work off the event loop, and the owner's words for
a refusal (design 7.2). An op never shows a status or a class name; the sentence comes from the
store or the ledger and the code from this table."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from typing import Any

from ..errors import ApiError
from ..store import Invalid, NotFound, Refused, Scope, StoreError, VersionConflict

log = logging.getLogger(__name__)

OWNER = Scope.owner()


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
