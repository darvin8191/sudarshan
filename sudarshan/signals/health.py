"""``GET /health`` of sudarshan-signals (T5): the contract's ``Health``, open on loopback.

States and counts only, never TRINETRA's text: the release installed, the switch (``on``, ``off``,
``not_set_up``), the letterbox (``ok``, ``missing``, ``wrong``, ``unknown``), when the last
accepted document was received, its promised next one, whether the view is fresh now, and today's
refused files and missing documents. The watch and OpenAlgo's bridge read it; it is built from
what the service keeps in memory, so it never waits for SQLite.
"""

from __future__ import annotations

from typing import Any

from sudarshan.ops.release import installed_release
from sudarshan.signals import __version__, words
from sudarshan.signals import contract as c


def document(service: Any) -> dict[str, Any]:
    last = service.last if service.store is not None else None
    switch = service.switch_state
    fresh = None
    if switch == "on" and service.off_reason is None:
        status = words.freshness(_last(last), service.clock())["status"]
        fresh = status == "fresh"
    today = service.today or {}
    return {
        "service": c.SERVICE,
        "release": installed_release(service.settings.deployed_tag_file),
        "version": __version__,
        "state": service.state,
        "signals": switch,
        "letterbox": service.letterbox_state if switch != "off" else "unknown",
        "heard_ms": last.get("received_ms") if last else None,
        "next_by": last.get("next_by") if last else None,
        "fresh": fresh,
        "refused_today": int(today.get("refused", 0)),
        "gaps_today": int(today.get("gaps", 0)),
    }


def _last(last: dict[str, Any] | None) -> words.Last | None:
    """The stored last-document note as the header needs it."""
    if not last:
        return None
    return words.Last(
        published_ms=int(last["published_ms"]),
        fresh_until_ms=int(last["fresh_until_ms"]),
        stopping=bool(last.get("stopping")),
        clock_ok=bool(last.get("clock_ok", True)),
        clock_diff_ms=int(last.get("clock_diff_ms") or 0),
    )
