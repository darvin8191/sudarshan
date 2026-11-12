"""The ``state`` op and the switch (M8.4). The first thing the app asks.

``enabled()`` reads ``SUDARSHAN_WATCHLISTS_V2`` at call time, as the risk guard reads its switch.
``state`` answers what needs nothing stored: the switch, the limits and the default flag names
(computed here, never stored; a renamed flag is read from the store in W10).
"""

from __future__ import annotations

import os

from sudarshan.watchlists import contract, versions

DEFAULT_FLAG_NAMES = ("Watch", "Breakout", "Results", "Long term", "Avoid")
_ON = ("on", "true", "1", "yes")


def enabled() -> bool:
    """True when the owner has switched Watchlists v2 on."""
    raw = os.environ.get(contract.SWITCH_ENV) or ""
    return raw.strip().strip("'\"").lower() in _ON


def state(args: dict) -> dict:
    return {
        "enabled": True,
        "limits": dict(contract.LIMITS),
        "flag_names": [{"mark": n, "name": w} for n, w in enumerate(DEFAULT_FLAG_NAMES, 1)],
        "versions": versions.snapshot(),
        # favourites: W10. ranked and alerts availability: W22.
    }


HANDLERS = {"state": state}
