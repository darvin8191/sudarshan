"""The ``state`` op and the switch (M8.4). The first thing the app asks.

``enabled()`` reads ``SUDARSHAN_WATCHLISTS_V2`` at call time, as the risk guard reads its switch.
``state`` answers the switch, the limits, the default flag names (computed here, never stored),
the versions and the favourites resolved (``ops/lists.py``, W10).
"""

from __future__ import annotations

import os

from flask import session

from sudarshan.watchlists import contract, versions

DEFAULT_FLAG_NAMES = ("Watch", "Breakout", "Results", "Long term", "Avoid")
_ON = ("on", "true", "1", "yes")


def enabled() -> bool:
    """True when the owner has switched Watchlists v2 on."""
    raw = os.environ.get(contract.SWITCH_ENV) or ""
    return raw.strip().strip("'\"").lower() in _ON


def state(args: dict) -> dict:
    got = versions.snapshot()
    answer = {
        "enabled": True,
        "limits": dict(contract.LIMITS),
        "flag_names": [{"mark": n, "name": w} for n, w in enumerate(DEFAULT_FLAG_NAMES, 1)],
        # ranked and alerts availability: W22.
    }
    from sudarshan.watchlists.ops import lists  # it imports this module for the flag names

    answer["favourites"] = lists.favourites_of(session.get("user") or "")
    if len(got) == len(contract.record("Versions")):  # a store that cannot be read: left out
        answer["versions"] = got
    return answer


HANDLERS = {"state": state}
