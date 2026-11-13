"""Family invites (M2.9, design 2.2 and 6.3): a one-time link the owner carries himself.

``invite_create`` makes a 20-byte code (urlsafe text) and stores only its SHA-256, an end two hours
on, and a TOTP secret made now and sealed with the store key. The owner copies or shares the link
``/family/join/<code>`` himself; the server never sends it anywhere. A member has at most one unused
invite: a new one revokes the one before (audited). An invite works once: joining marks it used in
the same transaction that makes the member's credential and first session.

The code is never kept, so a create the owner's bridge retries (the same ``request_id``) gets the
same link back only from this process's memory (``InviteCodes``, a few entries, each dropped when
its invite ends); after a restart a retry makes a new link, which revokes the first.

These are functions of one store transaction (``store.Tx``).
"""

from __future__ import annotations

import hashlib
import secrets
import threading
from typing import Any

from . import audit
from .contract import LIMITS
from .errors import FamilyError, Invalid, NotFound
from .store import Tx

CODE_BYTES = 20
TTL_MS = LIMITS["invite_hours"] * 3_600_000
PATH = "/family/join/"


def code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def answer(member_id: int, code: str, expires_at: int) -> dict[str, Any]:
    """An invite as the owner's page reads it (the contract's ``Invite``)."""
    return {"member_id": member_id, "code": code, "path": PATH + code, "expires_at": expires_at}


class InviteCodes:
    """The codes of invites made by this process, by request id, while their invites last: what
    makes a retried create give the same link. At most ``size`` entries, the oldest dropped."""

    def __init__(self, size: int = 16):
        self.size = size
        self._lock = threading.Lock()
        self._codes: dict[str, tuple[int, str, int]] = {}

    def get(self, request_id: str | None, member_id: int, now: int) -> tuple[str, int] | None:
        if not request_id:
            return None
        with self._lock:
            found = self._codes.get(request_id)
            if found is None or found[0] != member_id or found[2] <= now:
                return None
            return found[1], found[2]

    def put(self, request_id: str | None, member_id: int, code: str, expires_at: int) -> None:
        if not request_id:
            return
        with self._lock:
            self._codes.pop(request_id, None)
            self._codes[request_id] = (member_id, code, expires_at)
            while len(self._codes) > self.size:
                self._codes.pop(next(iter(self._codes)))

    def __len__(self) -> int:
        return len(self._codes)


def revoke_open(tx: Tx, member_id: int, now: int, actor: str = audit.OWNER) -> int:
    """Revoke the member's unused invite, if any (audited); how many were revoked."""
    t = tx.t("invite")
    found = tx.conn.execute(
        t.select().where(t.c.member_id == member_id, t.c.used_at.is_(None),
                         t.c.revoked_at.is_(None))
    ).mappings().all()  # fmt: skip
    for row in found:
        tx.update("invite", {"id": row["id"]}, {"revoked_at": now})
        audit.record(tx, at=now, actor=actor, kind="invite_revoked", member_id=member_id,
                     detail={"invite": row["id"]})  # fmt: skip
    return len(found)


def create(
    tx: Tx, member_id: int, totp_secret: str, now: int, request_id: str | None = None
) -> tuple[str, int]:
    """A new invite for a member who has not joined; its code and end. The member's unused invite
    before it is revoked. NotFound for a member who is gone, Invalid for one who has joined."""
    member = tx.member(member_id)
    if member is None or member["status"] == "removed":
        raise NotFound("member_gone")
    if member["status"] != "invited":
        raise Invalid("already_joined", field="member_id")
    revoke_open(tx, member_id, now)
    code = secrets.token_urlsafe(CODE_BYTES)
    expires_at = now + TTL_MS
    new = tx.insert(
        "invite",
        {
            "member_id": member_id,
            "code_hash": code_hash(code),
            "totp_secret_enc": tx.secrets.seal(totp_secret),  # type: ignore[union-attr]
            "expires_at": expires_at,
            "request_id": request_id,
            "created_at": now,
        },
    )
    audit.record(tx, at=now, actor=audit.OWNER, kind="invite_created", member_id=member_id,
                 detail={"invite": new, "expires_at": expires_at})  # fmt: skip
    return code, expires_at


def usable(tx: Tx, code: Any, now: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """The invite a code names and its member, while it can still be used; ``invite_gone``
    otherwise (unknown, used, revoked, past its end, or its member joined or left)."""
    if not isinstance(code, str) or not 10 <= len(code) <= 100:
        raise FamilyError("invite_gone")
    row = tx.row("invite", code_hash=code_hash(code))
    if row is None or row["used_at"] is not None or row["revoked_at"] is not None:
        raise FamilyError("invite_gone")
    member = tx.member(row["member_id"])
    if now >= row["expires_at"] or member is None or member["status"] != "invited":
        raise FamilyError("invite_gone")
    return row, member


def mark_used(tx: Tx, invite_id: int, now: int) -> None:
    tx.update("invite", {"id": invite_id}, {"used_at": now})


def open_end(tx: Tx, member_id: int, now: int) -> int | None:
    """When the member's unused invite ends, while it lasts; None when there is none."""
    t = tx.t("invite")
    found = tx.conn.execute(
        t.select().where(t.c.member_id == member_id, t.c.used_at.is_(None),
                         t.c.revoked_at.is_(None), t.c.expires_at > now)
    ).mappings().first()  # fmt: skip
    return int(found["expires_at"]) if found is not None else None
