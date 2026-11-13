"""The ``view`` op (M8.4, W6): one ref's rows, sections, follow rows and preference. A read.

The ref's kind picks the reader found in ``refs/``; a kind that is not built, a ranking the page
does not offer and a ref that is not ``<kind>:<id>`` are ``invalid`` with field ``ref``.
A list that does not exist is ``gone``. Prices are never in the answer: the page streams them.
"""

from __future__ import annotations

from flask import session

from sudarshan.watchlists import refs
from sudarshan.watchlists.ops import OpError


def view(args: dict) -> dict:
    found = refs.reader_of(args["ref"])
    if found is None:
        raise OpError("invalid", field="ref")
    reader, ident = found
    return reader.view(ident, session.get("user") or "")


HANDLERS = {"view": view}
