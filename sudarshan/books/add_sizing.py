"""The mark that asks for an add to be sized from the fills (M12.1, A7).

A smart order that only adds (from flat, or further the same way) is sized from Upstox's
positions row alone, with no order book read. The row can show a fill a minute late, so an entry
sent again inside that time reads the row as it was: after the strategy's own exit the row still
shows the old position and a new entry answers "already matched"; before the row shows a first
entry, a second one goes beside it.

The Automation intake asks for more: while its call runs, an order that only adds is sized as one
that reduces is, from the rows corrected by the order book's fills (``sudarshan.books.fills``),
and when that cannot be worked out nothing is sent. No other caller is changed, and an order
without the mark still makes no order book read.

The mark is a context variable, never a field of the order: nothing an alert, an API caller or a
stored row carries can set it, and no event, log line or journal entry has to have it taken out.
It lives for the one call inside ``sized_from_fills()`` on the thread (or green thread) that made
it. This module performs no I/O.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

_ASKED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "sudarshan_add_sized_from_fills", default=False
)


@contextmanager
def sized_from_fills() -> Iterator[None]:
    """While this is held, a smart order placed by this caller that only adds is sized from the
    positions rows corrected by the order book's fills."""
    token = _ASKED.set(True)
    try:
        yield
    finally:
        _ASKED.reset(token)


def asked() -> bool:
    """Whether the caller placing this smart order asked for an add sized from the fills."""
    return _ASKED.get()
