"""Family sessions (M2.10, design 2.2): minted at a sign-in, checked on every request, revoked.

A session id is 32 random bytes (urlsafe text), handed to the phone once in the cookie and stored
only as its SHA-256 (``id_hash``); the owner and the audit see its first 16 hex. A session lasts 30
days from its sign-in and ends after 14 days unused; a request moves ``last_seen_at`` (and the idle
end) at most once a minute, so a busy screen costs one write a minute, not one a request. A member
has at most 3 live sessions: a new sign-in beyond that revokes the oldest (audited).

A check reads the member row on every request, so a privacy change applies to the next request and
a removed or paused member is refused at once. A session that ended is told apart in the member's
words: expired, signed out on this device, removed by the owner, paused by the owner.

Sessions that ended are kept 90 days (the "new phone" notice compares a sign-in's device with them)
and then forgotten (``prune``). These are functions of one store transaction (``store.Tx``).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Any

from . import audit
from .contract import LIMITS
from .errors import FamilyError, Invalid, NotFound
from .store import Tx

DAY_MS = 86_400_000
ABSOLUTE_MS = LIMITS["session_days"] * DAY_MS
IDLE_MS = LIMITS["idle_days"] * DAY_MS
#: ``last_seen_at`` moves at most this often.
TOUCH_MS = 60_000
#: Sessions that ended are kept this long, then forgotten.
KEEP_ENDED_MS = 90 * DAY_MS
ID_BYTES = 32


@dataclass(frozen=True)
class Device:
    """The phone a sign-in comes from: its label (from the browser), its fixed summary and the keyed
    hash of its address (None when it is not known)."""

    label: str
    summary: str
    place: str | None = None


@dataclass(frozen=True)
class Session:
    """A checked session: its hash, the member row as read now and the session row."""

    id_hash: str
    member: dict[str, Any]
    row: dict[str, Any]

    @property
    def member_id(self) -> int:
        return int(self.member["id"])

    @property
    def device_id(self) -> str:
        return self.id_hash[:16]


def new_id() -> str:
    return secrets.token_urlsafe(ID_BYTES)


def id_hash(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def mint(
    tx: Tx, member_id: int, device: Device, now: int, max_devices: int = LIMITS["max_devices"]
) -> tuple[str, list[str]]:
    """A new session for the member; its id (for the cookie, never stored) and the hashes of the
    sessions revoked to keep within the device limit (oldest first)."""
    revoked = []
    live = tx.sessions_live(member_id, now)
    while len(live) >= max_devices:
        oldest = live.pop(0)
        tx.session_revoke(oldest["id_hash"], now, "device_limit")
        audit.record(tx, at=now, actor="system", kind="session_revoked", member_id=member_id,
                     session=oldest["id_hash"], detail={"why": "device_limit"})  # fmt: skip
        revoked.append(oldest["id_hash"])
    session_id = new_id()
    tx.insert(
        "viewer_session",
        {
            "id_hash": id_hash(session_id),
            "member_id": member_id,
            "device_label": device.label,
            "ua_summary": device.summary,
            "ip_hash": device.place,
            "created_at": now,
            "last_seen_at": now,
            "absolute_expires_at": now + ABSOLUTE_MS,
            "idle_expires_at": now + IDLE_MS,
        },
    )
    return session_id, revoked


def _ended(row: dict[str, Any], member: dict[str, Any] | None) -> FamilyError | None:
    """Why a session no longer signs its member in, in the member's words; None while it does."""
    if member is None or member["status"] == "removed" or row["revoke_reason"] == "removed":
        return FamilyError("signed_out", "removed")
    if member["status"] == "suspended":
        return FamilyError("signed_out", "suspended")
    if row["revoked_at"] is not None:
        return FamilyError("signed_out", "expired" if row["revoke_reason"] == "expired" else None)
    if member["status"] != "active":
        return FamilyError("signed_out")
    return None


def verdict(tx: Tx, session_id: str | None, now: int) -> Session | FamilyError:
    """The session a cookie names, checked now, or why it does not sign its member in (a
    ``signed_out`` refusal with the member's sentence). Call in a write transaction and let it
    commit: a session found expired is marked so (audited once), and a used one is touched."""
    if not session_id or len(session_id) > 200:
        return FamilyError("signed_out")
    return judge(tx, id_hash(session_id), now)


def judge(tx: Tx, hashed: str, now: int) -> Session | FamilyError:
    """``verdict`` for a session known by its hash (an open socket's): the same checks, the same
    marks."""
    row = tx.row("viewer_session", id_hash=hashed)
    if row is None:
        return FamilyError("signed_out")
    member = tx.member(row["member_id"])
    refused = _ended(row, member)
    if refused is None and (now >= row["absolute_expires_at"] or now >= row["idle_expires_at"]):
        tx.session_revoke(hashed, now, "expired")
        audit.record(tx, at=now, actor="system", kind="session_expired",
                     member_id=row["member_id"], session=hashed)  # fmt: skip
        refused = FamilyError("signed_out", "expired")
    if refused is not None:
        return refused
    if now - row["last_seen_at"] >= TOUCH_MS:
        values = {"last_seen_at": now, "idle_expires_at": now + IDLE_MS}
        tx.update("viewer_session", {"id_hash": hashed}, values)
        row = {**row, **values}
    return Session(hashed, member, row)  # type: ignore[arg-type]


def check(tx: Tx, session_id: str | None, now: int) -> Session:
    """``verdict``, raising the refusal (for a caller whose transaction ends there anyway)."""
    found = verdict(tx, session_id, now)
    if isinstance(found, FamilyError):
        raise found
    return found


def revoke(tx: Tx, hashed: str, now: int, reason: str) -> bool:
    return tx.session_revoke(hashed, now, reason)


def revoke_member(
    tx: Tx, member_id: int, now: int, reason: str, keep: str | None = None
) -> list[str]:
    """Revoke every live session of the member but ``keep``; the hashes revoked."""
    done = []
    for row in tx.sessions_live(member_id, now):
        if row["id_hash"] != keep and tx.session_revoke(row["id_hash"], now, reason):
            done.append(row["id_hash"])
    return done


def find(tx: Tx, member_id: int, device_id: Any, now: int) -> dict[str, Any]:
    """A live session of the member by the 16 hex the device list shows; NotFound otherwise."""
    if not isinstance(device_id, str) or len(device_id) != 16:
        raise NotFound()
    for row in tx.sessions_live(member_id, now):
        if row["id_hash"][:16] == device_id.lower():
            return row
    raise NotFound()


def rename(tx: Tx, member_id: int, device_id: Any, label: Any, now: int) -> None:
    row = find(tx, member_id, device_id, now)
    clean = label.strip() if isinstance(label, str) else ""
    if not 1 <= len(clean) <= LIMITS["device_label_max"] or not clean.isprintable():
        raise Invalid("label_rule", field="label")
    tx.update("viewer_session", {"id_hash": row["id_hash"]}, {"device_label": clean})
    audit.record(tx, at=now, actor=audit.member_actor(member_id), kind="device_renamed",
                 member_id=member_id, session=row["id_hash"])  # fmt: skip


def device(row: dict[str, Any], current: str | None = None) -> dict[str, Any]:
    """A session row as the device list shows it (the contract's ``Device``)."""
    return {
        "id": row["id_hash"][:16],
        "label": row["device_label"],
        "created_at": row["created_at"],
        "last_seen_at": row["last_seen_at"],
        "place": row["ip_hash"][:8] if row["ip_hash"] else None,
        "current": row["id_hash"] == current,
        "notifications": None,
    }


def devices(tx: Tx, member_id: int, now: int, current: str | None = None) -> list[dict[str, Any]]:
    """The member's live devices, the one asking first, then the most recently used."""
    rows = tx.sessions_live(member_id, now)
    rows.sort(key=lambda r: (r["id_hash"] != current, -r["last_seen_at"]))
    return [device(r, current) for r in rows]


def prune(tx: Tx, now: int) -> int:
    return tx.sessions_prune(now - KEEP_ENDED_MS)
