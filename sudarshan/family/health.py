"""The family service's health document, served as GET /health on 127.0.0.1:5585 (R14).

    curl -s 127.0.0.1:5585/health | python3 -m json.tool

Which release is installed (the deploy tool's record; ``version`` is this service's code), whether
the service is up and what it is doing, and ``family_state``: the switch, the store's version,
counts of members, live sessions and open sockets, the jobs. Counts and states only: never a name,
a sign-in name, an endpoint, an id or a key (a test reads the document for anything like one).
"""

from __future__ import annotations

from typing import Any

from sudarshan.ops.release import installed_release

from . import __version__
from .contract import SERVICE


def document(service: Any, counts: dict[str, int]) -> dict[str, Any]:
    """The document, from the service's memory and the counts the caller read from the store."""
    state = service.state
    family: dict[str, Any] = {
        "enabled": service.enabled,
        "state": state,
        "store": "open" if service.store is not None else "closed",
        "store_version": service.store.version if service.store is not None else None,
        "members": counts.get("members", 0),
        "sessions": counts.get("sessions", 0),
        "sockets": len(service.registry),
        "sealed": dict(service.doors.counts),
        "notices": dict(service.notices.counts),
        "copy": dict(service.copy_state),
        "jobs": {
            job: ("running" if not task.done() else "ended") for job, task in service.jobs.items()
        },  # fmt: skip
    }
    if service.off_error is not None and service.settings.enabled:
        family["reason"] = str(service.off_error)
    return {
        "service": SERVICE,
        "release": installed_release(service.settings.deployed_tag_file),
        "version": __version__,
        "state": "running" if state != "stopping" else "stopping",
        "family_state": family,
    }
