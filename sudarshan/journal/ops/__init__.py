"""The ops of the journal's sealed local API, each registered by its own module (JB2, design 7.2).

An op is an ``async def (ctx, args)`` that returns the data the caller is given (or raises
``errors.ApiError``), registered with ``@op("name")`` in a module of its own; ``ctx`` is the
service (its settings, its open store, its clock). The names are the contract's
(``core/kinds.OP_NAMES``); an op nobody has registered yet answers "This part of the journal is not
ready yet." A later part adds its module and one import line below, and touches nothing else that
is shared.
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
            raise ValueError(f"{name} is not an op of the journal's contract")
        if name in book:
            raise ValueError(f"the op {name} is registered twice")
        book[name] = handler
        return handler

    return register


# One import line per part that adds ops, in merge order:
from . import (  # noqa: E402
    analytics_ops,  # noqa: F401  (JB10: analytics)
    charges_ops,  # noqa: F401  (P7 J4: charges_reconcile)
    nudge_ops,  # noqa: F401  (JB3': nudge)
    playbook_ops,  # noqa: F401  (JB4: playbooks_list, playbook_create, playbook_update)
    report_ops,  # noqa: F401  (JB7: reports_list, report_get, report_file, report_build, delivery)
    service_ops,  # noqa: F401  (JB7: reconcile_now, family_day)
    settings_ops,  # noqa: F401  (JB7: settings_get, settings_set)
    state_ops,  # noqa: F401  (JB2: state)
    tag_ops,  # noqa: F401  (JB4: tags_list, tag_create, tag_update)
    trip_ops,  # noqa: F401  (JB4: trips, trip_get, trip_update)
)
