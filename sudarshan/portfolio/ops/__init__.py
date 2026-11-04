"""The ops of the sealed local API, each registered by its own module (M7.18, design 7.2).

An op is an ``async def (ctx, message)`` that returns the data the bridge passes on (or raises
``api.ApiError``), registered with ``@op("name")`` in a module of its own; ``ctx`` is the
service's ``Desk`` (settings, the store when one is open). The names are the contract's
(``core/kinds.OP_NAMES``); an op nobody has registered yet answers "This part of the portfolio
is not ready yet." So a later part adds its module and **one import line below**, and touches
nothing else that is shared.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from ..core.kinds import OP_NAMES

Handler = Callable[[Any, Mapping[str, Any]], Awaitable[Any]]
REGISTRY: dict[str, Handler] = {}


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


# One import line per part that adds ops, in merge order:
from . import (
    book_ops,  # noqa: E402,F401  (members, accounts, assets, values, entries)
    import_ops,  # noqa: E402,F401  (M7.22: import_preview, import_commit, import_discard, export_csv)
    tab_ops,  # noqa: E402,F401  (state, summary, tabs, performance, layout, views)
)
