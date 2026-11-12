"""The op handlers of Watchlists v2, found by discovery (M8.4).

Each module of this package exposes ``HANDLERS = {op_name: fn}``; ``fn(args) -> data`` takes the
op's checked request fields and returns the record the contract names (a handler that cannot do
what was asked raises ``OpError``). Only ops with a handler are routed, so a later change adds a
module and no edit elsewhere. Handlers run on the request's thread, open a session per operation
and start no thread, timer or loop.
"""

from __future__ import annotations

import importlib
import pkgutil

from sudarshan.watchlists import contract


class OpError(Exception):
    """An op's refusal: a code of the contract, the owner's sentence, ``field`` for ``invalid``."""

    def __init__(self, code: str, message: str | None = None, field: str | None = None):
        super().__init__(message or code)
        if code not in contract.ERROR_CODES:
            raise ValueError(f"not an error code of the contract: {code}")
        self.code, self.message, self.field = code, message, field


def bumps(why: str, refs=None):
    """Declare that an op changes what a list, a section or a flag list holds: the bridge bumps
    the lists version once and pushes ``why`` after the handler returns (so after its commit),
    never for a refusal. ``refs(args, data)`` names the refs changed (none: the whole of lists)."""

    def mark(handler):
        handler.bump = (why, refs)
        return handler

    return mark


def collect() -> list[tuple[str, object, str]]:
    """Every ``(op, handler, module)`` the modules declare, duplicates and unknown ops included."""
    found = []
    for info in pkgutil.iter_modules(__path__):
        module = importlib.import_module(f"{__name__}.{info.name}")
        found += [(op, fn, info.name) for op, fn in getattr(module, "HANDLERS", {}).items()]
    return found


def discover() -> dict[str, object]:
    """The handler of each op that has a row and exactly one handler (a test names the rest)."""
    found = collect()
    names = [op for op, _, _ in found]
    return {op: fn for op, fn, _ in found if op in contract.OP_NAMES and names.count(op) == 1}
