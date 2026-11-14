"""Whether a holding's sheet may offer Protect: ``SUDARSHAN_GTT_PROTECT`` (M5.6, G1 and G2).

Protect is one SINGLE sell GTT on a cash holding, sent by the page through OpenAlgo's own GTT
service, so the order passes the risk gate like any other. This is only the page's switch:

- ``off`` (the default): no Protect on any screen, and the Holdings page does not read the GTT book.
- ``sandbox``: Protect shows only while the app is in Analyze mode, and a Protect request goes to
  the sandbox whatever the app's mode is when it arrives (``protect_check.sandbox_only``).
- ``on``: in Live too.

It is read on every call. Any other value reads as ``off``, with one warning in the log. It is
served to the page with the session facts (blueprints/auth.py). No order code reads it directly:
the one rule that does is ``protect_check.sandbox_only``, which can only move a Protect to the
sandbox.
"""

from __future__ import annotations

import os

from utils.logging import get_logger

logger = get_logger(__name__)

ENV = "SUDARSHAN_GTT_PROTECT"
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
        logger.warning(f"{ENV} has a value it does not know; Protect stays off")
    return OFF
