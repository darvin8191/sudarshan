"""The nightly read of Upstox's corporate actions (phase P7, lane C: M7.36).

Registered by the phase's contract (P0) with no job yet: C2 adds the ``corporate_actions`` job
here (``kinds.JOB_NAMES``), and ``timers.gate_switched`` keeps it from running while
``SUDARSHAN_PORTFOLIO_INSIGHTS`` is off.
"""

from __future__ import annotations


def register_jobs() -> None:
    """Nothing to register until C2 lands."""
