"""The health document of sudarshan-alerts, served as GET /health on 127.0.0.1:5582 (M18.1, M6.8).

    curl -s 127.0.0.1:5582/health | python3 -m json.tool

What a person at a terminal, or a later check, wants first: which release is installed (``release``,
read from the deploy tool's record; ``version`` is this service's own code and is not the release),
whether the service is up and what it is doing, whether the owner's key was found, and what has gone
to Telegram. Nothing secret is on it: no key, no user name, no message text.

The market alerts' local API (market/api.py) is the one listener on 5582: its /health returns this
document with the market fields added (``market_alerts``, ``market_state``, ``alerts_active``,
``instruments``). The names here are fixed; the market fields must not reuse them.

``report_desk`` (M15.7) is the report desk's own block (``ReportDesk.health``): on or off and why,
the reports waiting and when the last one went to Telegram. Without a desk it says why there is
none (order alerts switched off, ``SUDARSHAN_JOURNAL=off`` in alerts.env, no key to seal with), or
``starting`` before the service has built it. The journal reads it for the Reports page.
"""

from __future__ import annotations

from sudarshan.ops.release import installed_release

from . import __version__
from .reports_desk import OFF, STARTING


def health_document(service) -> dict:
    """The health document: plain facts from memory and one small file, nothing secret.

    Safe to call from anywhere in the service's process (the page below, the market alerts' API):
    synchronous, it changes nothing, and its only I/O is reading the deploy tool's record of the
    installed release. The values are copies, so a caller may add to or change the result. The
    names are fixed; a caller adding its own fields must not reuse them (the market alerts name
    theirs ``market_state`` and so on).
    """
    outbox = service.outbox
    return {
        "service": "sudarshan-alerts",
        "release": installed_release(service.settings.deployed_tag_file),
        "version": __version__,
        "state": service.state,
        "detail": service._detail,
        "window": service._window_text(),
        "owner_ready": service.owner is not None,
        "owner_problem": service._owner_problem,
        "stats": dict(service.stats),
        "telegram": dict(outbox.stats) if outbox is not None else None,
        "report_desk": report_desk_block(service),
    }


def report_desk_block(service) -> dict:
    """The report desk's block: its own when it runs, else why there is none (a copy)."""
    desk = getattr(service, "reports", None)
    if desk is not None:
        return desk.health()
    why = getattr(service, "reports_off", None)
    return {
        "state": OFF if why else STARTING,
        "on": False,
        "reason": why,
        "waiting": None,
        "last_delivery_ms": None,
    }
