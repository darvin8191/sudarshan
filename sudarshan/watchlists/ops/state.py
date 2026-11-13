"""The ``state`` op and the switch (M8.4). The first thing the app asks.

``enabled()`` reads ``SUDARSHAN_WATCHLISTS_V2`` at call time, as the risk guard reads its switch.
``state`` answers what needs nothing stored: the switch, the limits and the flag names
(the defaults are computed here and never stored; a renamed flag is read from the store).
"""

from __future__ import annotations

import os

from sudarshan.watchlists import contract, engine, versions
from utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_FLAG_NAMES = ("Watch", "Breakout", "Results", "Long term", "Avoid")
_ON = ("on", "true", "1", "yes")


def enabled() -> bool:
    """True when the owner has switched Watchlists v2 on."""
    raw = os.environ.get(contract.SWITCH_ENV) or ""
    return raw.strip().strip("'\"").lower() in _ON


def flag_names() -> list[dict]:
    """The five flags, renamed ones from the store; the defaults when it cannot be read."""
    from sudarshan.watchlists.store import symbols

    try:
        with engine.open_session() as s:
            return symbols.flag_names(s)
    except Exception:
        logger.exception("Watchlists: the flag names could not be read")
        return [{"mark": n, "name": w} for n, w in enumerate(DEFAULT_FLAG_NAMES, 1)]


def state(args: dict) -> dict:
    got = versions.snapshot()
    answer = {
        "enabled": True,
        "limits": dict(contract.LIMITS),
        "flag_names": flag_names(),
        # favourites: W10. ranked and alerts availability: W22.
    }
    if len(got) == len(contract.record("Versions")):  # a store that cannot be read: left out
        answer["versions"] = got
    return answer


HANDLERS = {"state": state}
