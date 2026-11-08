"""The ops of the sealed local API, each registered by the module that builds it (M9.1, 6.3).

An op is an ``async def (ctx, args)`` that returns the data the bridge passes on (or raises
``ApiError``), registered with ``@op("name")``; ``ctx`` is the service (its settings, its open
store, its clock). The names are the contract's (``contract.OP_NAMES``); an op nobody has
registered yet answers "This part of the screener is not ready yet." A later row adds its module
and one import line at the bottom, and touches nothing else that is shared (S17).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from .contract import ERROR_STATUS, OP_NAMES

Handler = Callable[[Any, Mapping[str, Any]], Awaitable[Any]]
REGISTRY: dict[str, Handler] = {}


class ApiError(Exception):
    """An answer that is not success: ``code`` is the contract's, ``message`` the owner's sentence,
    ``field`` the line of the screen it is about (or None)."""

    def __init__(self, status: int, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.field = status, code, message, field

    @classmethod
    def of(cls, code: str, message: str, field: str | None = None) -> ApiError:
        return cls(ERROR_STATUS.get(code, 500), code, message, field)

    def body(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.field:
            error["field"] = self.field
        return {"status": "error", "error": error}


def op(name: str, registry: dict[str, Handler] | None = None) -> Callable[[Handler], Handler]:
    """Register the function as the op ``name`` (a contract name, registered once)."""
    book = REGISTRY if registry is None else registry

    def register(handler: Handler) -> Handler:
        if name not in OP_NAMES:
            raise ValueError(f"{name} is not an op of the contract")
        if name in book:
            raise ValueError(f"the op {name} is registered twice")
        book[name] = handler
        return handler

    return register


# One import line per row that adds ops, in merge order (each module registers at import):
from . import (
    screen_ops,  # noqa: E402,F401  (M9.1: state, create, update, delete, preview, results, runs, run, runs_since, membership, membership_update, presets, universes, today)
)
