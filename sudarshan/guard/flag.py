"""Which orders the risk guard checks: ``SUDARSHAN_RISK_GUARD`` (M5.13).

- ``off`` (the default): nothing is checked. The gate's decorators return the wrapped
  function's own result after one environment lookup, with no other call.
- ``sandbox``: only sandbox orders are checked (the owner's Analyzer sessions); the Live
  account is untouched.
- ``on``: Live and sandbox orders.

It is read on every call. Any other value reads as ``off``, with one warning in the log.
"""

from __future__ import annotations

import os

from utils.logging import get_logger

logger = get_logger(__name__)

ENV = "SUDARSHAN_RISK_GUARD"
OFF, SANDBOX, ON = "off", "sandbox", "on"
STAGES = (OFF, SANDBOX, ON)

_warned_about: str | None = None


def stage() -> str:
    """The stage set in the environment: ``off``, ``sandbox`` or ``on``."""
    global _warned_about
    raw = os.environ.get(ENV)
    if raw is None:
        return OFF
    value = raw.strip().strip("'\"").lower()
    if value in STAGES:
        return value
    if value and raw != _warned_about:
        _warned_about = raw
        logger.warning(f"{ENV} has a value it does not know; the risk guard stays off")
    return OFF


def covers(pipe: str) -> bool:
    """Whether orders in ``pipe`` ("live" or "sandbox") are checked now."""
    current = stage()
    if pipe == "live":
        return current == ON
    if pipe == "sandbox":
        return current in (SANDBOX, ON)
    return False
