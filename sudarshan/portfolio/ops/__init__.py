"""The ops of the sealed local API, each registered by its own module (M7.18, design 7.2).

An op is an ``async def (ctx, message)`` that returns the data the bridge passes on (or raises
``api.ApiError``), registered with ``@op("name")`` in a module of its own; ``ctx`` is the
service's ``Desk`` (settings, the store when one is open). The names are the contract's
(``core/kinds.OP_NAMES``); an op nobody has registered yet answers "This part of the portfolio
is not ready yet." So a later part adds its module and **one import line below**, and touches
nothing else that is shared.

An op of phase P7 waits for its switch (``kinds.SWITCH_OF``): registered here, it answers "not
ready" while the switch is off, whatever its module does, so no module has to remember to ask.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from functools import wraps
from typing import Any

from ..config import switch_on
from ..core.kinds import SERVICE_OP_NAMES, SWITCH_OF, WORDS
from ..errors import ApiError

Handler = Callable[[Any, Mapping[str, Any]], Awaitable[Any]]
REGISTRY: dict[str, Handler] = {}


def switched(name: str, handler: Handler) -> Handler:
    """``handler`` as it runs behind its switch: "not ready" while ``SWITCH_OF[name]`` is off."""
    switch = SWITCH_OF.get(name)
    if switch is None:
        return handler

    @wraps(handler)
    async def gated(ctx: Any, message: Mapping[str, Any]) -> Any:
        if not switch_on(ctx, switch):
            raise ApiError(501, "not_ready", WORDS["not_ready"])
        return await handler(ctx, message)

    return gated


def op(name: str, registry: dict[str, Handler] | None = None) -> Callable[[Handler], Handler]:
    """Register the function as the op ``name`` (a contract name, registered once)."""
    book = REGISTRY if registry is None else registry

    def register(handler: Handler) -> Handler:
        if name not in SERVICE_OP_NAMES:
            raise ValueError(f"{name} is not an op of the contract")
        if name in book:
            raise ValueError(f"the op {name} is registered twice")
        book[name] = switched(name, handler)
        return handler

    return register


# One import line per part that adds ops, in merge order:
from . import (
    book_ops,  # noqa: E402,F401  (members, accounts, assets, values, entries)
    ca_ops,  # noqa: E402,F401  (P7 lane C: M7.13, M7.29, M7.36; registers with C3 to C5)
    export_ops,  # noqa: E402,F401  (P7 lane X: M7.40; registers with X1, X2)
    goal_ops,  # noqa: E402,F401  (P7 lane R: M7.34; registers with R6)
    import_ops,  # noqa: E402,F401  (M7.22: import_preview, import_commit, import_discard, export_csv)
    journal_ops,  # noqa: E402,F401  (PJ: the journal purpose's account reads)
    mf_ops,  # noqa: E402,F401  (M7.24: mf_search)
    risk_ops,  # noqa: E402,F401  (P7 lane R: M7.32; registers with R4)
    share_ops,  # noqa: E402,F401  (M7.37: shares, member privacy, shares_for_viewer)
    tab_ops,  # noqa: E402,F401  (state, summary, tabs, performance, layout, views)
    tax_ops,  # noqa: E402,F401  (P7 lane P: M7.6, M7.7, M7.27; registers with P3, P4)
    upstox_ops,  # noqa: E402,F401  (M7.1-M7.4: upstox_book, upstox_history)
)
