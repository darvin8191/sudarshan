"""The family service's notices for the owner (M2.11; design 2.6, decision 16).

A sign-in locked after wrong attempts, a sign-in from a new phone, an invite used, a member
removed, a store a newer release wrote (and, from the lanes that send them, notifications that
keep failing and a bot token Telegram refused): each is one line in the export folder's spool
(``ops.events.FAMILY_SPOOL``, src ``family``), written 0640 so that ``sudarshan-alerts``, in the
folder's group, can read it; the alerts' notice desk words it and sends it to the owner's Telegram
once per id. This service holds no owner channel of its own.

A member is never named: no display name ever enters a line. The sign-in name goes in only when the
owner turned that on (``SUDARSHAN_FAMILY_NAME_MEMBERS=on``), with ``name_members`` = 1 beside it;
otherwise the line says nothing of who, and the owner reads "a family member". The other facts are
the ones the desk words: the device label, the number of attempts, when a lock ends (epoch
seconds). A notice is a convenience: one that cannot be written is logged and dropped, never the
reason a sign-in fails.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sudarshan.ops import events

log = logging.getLogger("sudarshan.family")

#: The kinds this service records (the desk has words for each).
KINDS = frozenset(
    {"lockout", "new_device", "invite_used", "member_removed", "push_failing",
     "bot_token_rejected", "store_newer"}
)  # fmt: skip
#: The facts a line may carry besides who: the device label, the attempts, a lock's end.
FACTS = frozenset({"device", "attempts", "until"})
SPOOL_MODE = 0o640
WARN_EVERY_S = 600.0


class Notices:
    """``notices(kind, member, **facts)``: the Accounts' notify hook, and the service's own."""

    def __init__(
        self,
        spool: Path = events.FAMILY_SPOOL,
        *,
        name_members: bool = False,
        clock: Callable[[], int] | None = None,
    ):
        self.spool, self.name_members = Path(spool), name_members
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.counts = {"written": 0, "failed": 0}
        self._warned = -1e9

    def __call__(
        self, kind: str, member: dict[str, Any] | None = None, *, key: str | None = None,
        **facts: str,
    ) -> bool:  # fmt: skip
        """Record one notice; True when it is in the spool. ``key`` makes the id stable (a fact
        that holds across restarts is recorded once)."""
        if kind not in KINDS:
            raise ValueError(f"not a family notice: {kind}")
        at = self.clock()
        line = {k: str(v) for k, v in facts.items() if k in FACTS}
        handle = (member or {}).get("handle")
        if self.name_members and isinstance(handle, str) and handle:
            line |= {"handle": handle, "name_members": "1"}
        ident = f"family-{kind}-{key}" if key else f"family-{kind}-{at}-{secrets.token_hex(3)}"
        written = events.record(self.spool, kind=kind, event_id=ident, at=at / 1000, run=ident,
                                src="family", file_mode=SPOOL_MODE, **line)  # fmt: skip
        self.counts["written" if written else "failed"] += 1
        if not written and time.monotonic() - self._warned >= WARN_EVERY_S:
            self._warned = time.monotonic()
            log.warning("a family notice (%s) could not be written to the export folder; the "
                        "owner is not told of it", kind)  # fmt: skip
        return written
