"""The owner's side of the family accounts (M2.6, M20.6; design 6.5): members, their privacy, removal.

A member is made by the owner (display name, role, privacy, language), joins through an invite and
is then ``active``; the owner may pause (``suspended``) and resume them, reset their sign-in (a new
invite replaces their password and code app) or remove them. A pause holds until the owner resumes
it (review M4): a reset of a paused member keeps them ``suspended``, with no password until they
use the new link, and joining through it keeps them paused; a resume of a member who has not used
it yet makes them ``invited``, so that ``active`` always means a member who can sign in. Removal keeps the row for the audit
(status ``removed``, the sign-in name freed) and deletes the credential, the push subscriptions and
the Telegram link; the member's sessions are revoked as ``removed`` so a phone still open is told
why. At most four members are not removed (``LIMITS``).

Privacy is four switches, each shown or hidden (design 3.1); P&L may be shown only while quantities
are (cross-design Y24: P&L between reads is worked out from them). A change is audited with its
before and after, and applies to the member's next request. Names never enter the audit.

These are functions of one store transaction (``store.Tx``).
"""

from __future__ import annotations

from typing import Any

from . import audit, invites, lockout, sessions
from .contract import CATEGORIES, LANGUAGES, LIMITS, PRIVACY_DEFAULTS, ROLES, SET_STATUSES
from .errors import Invalid, LimitReached, NotFound, VersionConflict
from .store import Tx


def clean_name(raw: Any) -> str:
    text = " ".join(raw.split()) if isinstance(raw, str) else ""
    if not 1 <= len(text) <= LIMITS["display_name_max"] or not text.isprintable():
        raise Invalid("name_rule", field="display_name")
    return text


def clean_privacy(raw: Any, role: str) -> dict[str, bool]:
    """Four switches; the role's defaults when none were sent; Y24 refused in the owner's words."""
    if raw is None:
        return dict(PRIVACY_DEFAULTS[role])
    if not isinstance(raw, dict) or set(raw) != set(CATEGORIES):
        raise Invalid("invalid", field="privacy")
    if not all(isinstance(raw[c], bool) for c in CATEGORIES):
        raise Invalid("invalid", field="privacy")
    if raw["pnl"] and not raw["qty"]:
        raise Invalid("privacy_rule", field="privacy")
    return {c: raw[c] for c in CATEGORIES}


def _choice(raw: Any, allowed: tuple[str, ...], field: str) -> str:
    if raw not in allowed:
        raise Invalid("invalid", field=field)
    return raw


def live(tx: Tx, member_id: Any) -> dict[str, Any]:
    """A member who is not removed; NotFound otherwise."""
    found = tx.member(member_id) if isinstance(member_id, int) else None
    if found is None or found["status"] == "removed":
        raise NotFound("member_gone")
    return found


def card(tx: Tx, member: dict[str, Any], now: int) -> dict[str, Any]:
    """A member as the owner's page shows them (the contract's ``MemberCard``)."""
    live_sessions = tx.sessions_live(member["id"], now)
    link = tx.row("telegram_link", member_id=member["id"])
    return {
        "id": member["id"],
        "handle": member["handle"],
        "display_name": member["display_name"],
        "role": member["role"],
        "status": member["status"],
        "privacy": member["privacy"],
        "language": member["language"],
        "devices": len(live_sessions),
        "last_seen_at": max((s["last_seen_at"] for s in live_sessions), default=None),
        "invite_expires_at": invites.open_end(tx, member["id"], now),
        # Sign-ins from phones the member has not used, held back by wrong attempts (lockout.py).
        "locked_until": lockout.name_locked_until(tx, member["handle"], now),
        "telegram_linked": bool(link and link.get("chat_id_enc")),
        "created_at": member["created_at"],
        "version": member["version"],
    }


def cards(tx: Tx, now: int) -> list[dict[str, Any]]:
    return [card(tx, m, now) for m in tx.members_live()]


def create(
    tx: Tx, args: dict[str, Any], now: int, max_members: int = LIMITS["max_members"]
) -> dict[str, Any]:
    request_id = args.get("request_id")
    if request_id is not None:
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 64:
            raise Invalid("invalid", field="request_id")
        again = tx.row("member", request_id=request_id)
        if again is not None:
            return again  # the create was retried: the member it made
    name = clean_name(args.get("display_name"))
    role = _choice(args.get("role"), ROLES, "role")
    privacy = clean_privacy(args.get("privacy"), role)
    language = _choice(args.get("language", "en"), LANGUAGES, "language")
    if len(tx.members_live()) >= max_members:
        raise LimitReached("member_limit")
    made = tx.member_add(
        {"display_name": name, "role": role, "status": "invited", "privacy": privacy,
         "language": language, "request_id": request_id},
        now,
    )  # fmt: skip
    audit.record(tx, at=now, actor=audit.OWNER, kind="member_created", member_id=made["id"],
                 detail={"role": role, "privacy": privacy, "language": language})  # fmt: skip
    return made


def update(tx: Tx, args: dict[str, Any], now: int) -> dict[str, Any]:
    member = live(tx, args.get("id"))
    role = _choice(args.get("role", member["role"]), ROLES, "role")
    values: dict[str, Any] = {}
    if "display_name" in args:
        values["display_name"] = clean_name(args["display_name"])
    if role != member["role"]:
        values["role"] = role
    if "language" in args:
        values["language"] = _choice(args["language"], LANGUAGES, "language")
    if "privacy" in args:
        values["privacy"] = clean_privacy(args["privacy"], role)
    changed = {k: v for k, v in values.items() if v != member[k]}
    if not changed:  # nothing to write; the version must still be the one decided on
        if args.get("version") != member["version"] or isinstance(args.get("version"), bool):
            raise VersionConflict()
        return member
    after = tx.member_change(member["id"], args.get("version"), changed, now)
    if "privacy" in changed:
        audit.record(
            tx,
            at=now,
            actor=audit.OWNER,
            kind="privacy_changed",
            member_id=member["id"],
            detail={"before": member["privacy"], "after": changed["privacy"]},
        )
    rest = sorted(k for k in changed if k != "privacy")
    if rest:
        audit.record(tx, at=now, actor=audit.OWNER, kind="member_updated", member_id=member["id"],
                     detail={"fields": rest})  # fmt: skip
    return after


def set_status(tx: Tx, args: dict[str, Any], now: int) -> tuple[dict[str, Any], list[str]]:
    """Pause or resume a member who has joined; the row after and the sessions a pause revoked."""
    member = live(tx, args.get("id"))
    status = _choice(args.get("status"), SET_STATUSES, "status")
    if member["status"] not in SET_STATUSES:
        raise Invalid("invalid", field="status")  # not joined yet: nothing to pause
    stored = status
    if status == "active" and tx.row("credential", member_id=member["id"]) is None:
        stored = "invited"  # paused and reset, the new link not used yet: it still works
    after = tx.member_change(member["id"], args.get("version"), {"status": stored}, now)
    revoked: list[str] = []
    if status != member["status"]:
        if status == "suspended":
            revoked = sessions.revoke_member(tx, member["id"], now, "suspended")
        kind = "member_suspended" if status == "suspended" else "member_resumed"
        audit.record(tx, at=now, actor=audit.OWNER, kind=kind, member_id=member["id"],
                     detail={"sessions": len(revoked)})  # fmt: skip
    return after, revoked


def remove(tx: Tx, args: dict[str, Any], now: int) -> tuple[dict[str, Any], list[str]]:
    """Remove a member; the row as it was and the sessions revoked."""
    member = live(tx, args.get("id"))
    tx.member_change(member["id"], args.get("version"), {"status": "removed", "handle": None}, now)
    revoked = sessions.revoke_member(tx, member["id"], now, "removed")
    invites.revoke_open(tx, member["id"], now)
    lockout.clear_member(tx, member)  # the name is free: a later member starts with no count
    for table in ("credential", "push_subscription", "telegram_link"):
        tx.delete(table, member_id=member["id"])
    audit.record(tx, at=now, actor=audit.OWNER, kind="member_removed", member_id=member["id"],
                 detail={"sessions": len(revoked)})  # fmt: skip
    return member, revoked


def reset_signin(tx: Tx, args: dict[str, Any], now: int) -> tuple[dict[str, Any], list[str]]:
    """Revoke a joined member's password, code app and sessions, and make them invited again, or
    keep them paused when they are (the caller makes the new invite in the same transaction); the
    row after and the sessions revoked."""
    member = live(tx, args.get("member_id"))
    if member["status"] not in SET_STATUSES:
        raise Invalid("invalid", field="member_id")
    status = "suspended" if member["status"] == "suspended" else "invited"  # a pause holds (M4)
    after = tx.member_change(member["id"], args.get("version"), {"status": status}, now)
    revoked = sessions.revoke_member(tx, member["id"], now, "signin_reset")
    tx.delete("credential", member_id=member["id"])
    lockout.clear_member(tx, member)  # a fresh start: no count from before the reset holds
    audit.record(tx, at=now, actor=audit.OWNER, kind="signin_reset", member_id=member["id"],
                 detail={"sessions": len(revoked)})  # fmt: skip
    return after, revoked
