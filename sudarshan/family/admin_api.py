"""The family service's two sealed doors (design 2.1, 2.3): the owner's admin ops and the nudge.

    POST /v1/admin/<op>   OpenAlgo's owner bridge: one Fernet token sealed for <op> under the admin
                          purpose (``seal-admin.key``), principal ``owner``
    POST /v1/nudge        sudarshan-alerts: one token sealed for ``nudge`` under the nudge purpose
                          (``seal-nudge.key``), principal ``sudarshan-alerts``

The browser never reaches either: nginx forwards only /family/api and /family/ws, and the listener
answers 404 to a sealed door that carries a forwarded header. A seal is checked with the shared
``sudarshan.autologin.handoff.open_sealed``: it lives 60 s, its nonce is accepted once (a
``NonceBook`` that answers busy when full rather than forget a nonce still valid, X9), and the op it
was sealed for must be the op of the path. A key the other door uses never opens this one. The
fields of an op are the seal's own beside ``op``, ``nonce`` and ``principal``.

Switched off, a seal is still checked first, and then every op answers ``{"enabled": false,
"message": ...}`` (the contract's ``Off``) and does nothing. An op of the contract that no lane has
built yet answers ``not_ready``. A refusal is logged once in ten minutes, never with what it carried.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from cryptography.fernet import Fernet

from sudarshan.autologin.handoff import (
    SEAL_TTL_S,
    HandoffRejected,
    NonceBook,
    NoncesFull,
    open_sealed,
)

from .contract import (
    ADMIN_OP_NAMES,
    ADMIN_PREFIX,
    ADMIN_PRINCIPAL,
    LIMITS,
    NUDGE_KINDS,
    NUDGE_OPS,
    NUDGE_PRINCIPAL,
)
from .errors import FamilyError
from .listener import Request, Response

log = logging.getLogger("sudarshan.family")

#: X9: a book holds 2 x 60 s x the door's peak a second (the owner's page, the alerts' one a second).
ADMIN_NONCES = 2 * SEAL_TTL_S * 8
NUDGE_NONCES = 2 * SEAL_TTL_S * 2
SEAL_FIELDS = ("op", "nonce", "principal")
NUDGE_OP = NUDGE_OPS[0].name
#: A refusal is a warning at most this often (a flood of them is one line).
WARN_EVERY_S = 600.0

AdminOp = Callable[[dict[str, Any]], Awaitable[Any]]
Nudged = Callable[[str, bool, int], bool]


class SealedDoors:
    """``admin`` and ``nudge`` answer a request the listener has checked; ``ops`` maps an admin op
    of the contract to what does it (``None``: not built yet)."""

    def __init__(
        self,
        *,
        admin_key: Fernet,
        nudge_key: Fernet,
        ops: Mapping[str, AdminOp],
        nudged: Nudged,
        enabled: Callable[[], bool],
        off_words: Callable[[], str],
        clock: Callable[[], float] = time.time,
    ):
        self.admin_key, self.nudge_key = admin_key, nudge_key
        self.ops, self.nudged, self.enabled, self.off_words = ops, nudged, enabled, off_words
        self.clock = clock
        self.admin_nonces = NonceBook(ADMIN_NONCES)
        self.nudge_nonces = NonceBook(NUDGE_NONCES)
        self.counts = {"admin": 0, "nudges": 0, "refused": 0}
        self._warned: dict[str, float] = {}

    def _open(self, key: Fernet, nonces: NonceBook, op: str, body: bytes, cap: int,
              principal: str) -> dict[str, Any]:  # fmt: skip
        """The message, once the seal is good for this door, op and principal."""
        try:
            message = open_sealed(key, body, op=op, seen=nonces, now=self.clock(), max_bytes=cap)
        except NoncesFull:
            self._warn("busy", "the family service is refusing sealed requests: too many in two "
                       "minutes")  # fmt: skip
            raise FamilyError("busy") from None
        except HandoffRejected as exc:
            self._refused(op, str(exc))
            raise FamilyError("forbidden") from None
        if message.get("principal") != principal:
            self._refused(op, "principal")
            raise FamilyError("forbidden")
        return message

    async def admin(self, request: Request) -> Response:
        op = request.path[len(ADMIN_PREFIX) :] if request.path.startswith(ADMIN_PREFIX) else ""
        if op not in ADMIN_OP_NAMES:
            raise FamilyError("not_found")
        if request.method != "POST":
            raise FamilyError("not_found")
        message = self._open(self.admin_key, self.admin_nonces, op, request.body,
                             LIMITS["admin_bytes"], ADMIN_PRINCIPAL)  # fmt: skip
        if not self.enabled():
            return Response.ok({"enabled": False, "message": self.off_words()})
        handler = self.ops.get(op)
        if handler is None:
            raise FamilyError("not_ready")
        self.counts["admin"] += 1
        args = {k: v for k, v in message.items() if k not in SEAL_FIELDS}
        return Response.ok(await handler(args))

    async def nudge(self, request: Request) -> Response:
        if request.method != "POST":
            raise FamilyError("not_found")
        message = self._open(self.nudge_key, self.nudge_nonces, NUDGE_OP, request.body,
                             LIMITS["nudge_bytes"], NUDGE_PRINCIPAL)  # fmt: skip
        kind, sandbox, at = message.get("kind"), message.get("sandbox"), message.get("at")
        if kind not in NUDGE_KINDS or not isinstance(sandbox, bool):
            raise FamilyError("invalid")
        if not isinstance(at, int) or isinstance(at, bool):
            raise FamilyError("invalid")
        if not self.enabled():
            return Response.ok({"enabled": False, "message": self.off_words()})
        self.counts["nudges"] += 1
        return Response.ok({"taken": bool(self.nudged(kind, sandbox, at))})

    def _refused(self, op: str, reason: str) -> None:
        self.counts["refused"] += 1
        log.debug("the family service refused a sealed %s request (%s)", op, reason)
        self._warn(
            "refused",
            "the family service refused a sealed request that was not sealed "
            f"for it by this server ({reason}); more go to the debug log for 10 minutes",
        )

    def _warn(self, kind: str, text: str) -> None:
        now = time.monotonic()
        if now - self._warned.get(kind, -1e9) >= WARN_EVERY_S:
            self._warned[kind] = now
            log.warning(text)
