"""Where the automation's alerts may send orders: ``SUDARSHAN_AUTOMATION`` (M12.11).

- ``off`` (the default): the alert route answers exactly as it does for a link nobody made, the
  store is not opened and no order code is imported.
- ``sandbox``: every strategy sends to the sandbox, whatever the app's mode and whatever the
  strategy's own mode says. Live cannot be chosen.
- ``on``: a strategy may be Live, and only while the risk guard (``SUDARSHAN_RISK_GUARD``) is
  ``on`` too.

It is read on every call. Any other value reads as ``off``, with one warning in the log.
"""

from __future__ import annotations

import os

from utils.logging import get_logger

logger = get_logger(__name__)

ENV = "SUDARSHAN_AUTOMATION"
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
        logger.warning(f"{ENV} has a value it does not know; automation stays off")
    return OFF
