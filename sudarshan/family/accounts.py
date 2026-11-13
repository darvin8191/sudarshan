"""Family sign-in (M2.6, M2.9, M2.10, M2.11; design 2.2): passwords, codes, joins, sign-ins, sessions.

* **Passwords** are argon2id with time cost 3, 64 MiB and one lane, verified on a thread of their
  own (``Hasher``): a verify is slow on purpose (about 0.2 s and 64 MiB here), so it never runs on
  the event loop or the store thread, and one at a time bounds the memory to one verify's. A
  sign-in name nobody has (or a member who has not joined) is verified against a fixed dummy hash
  with the same parameters, so the answer's timing does not tell names apart.
* **Codes** are TOTP (RFC 6238: 6 digits, 30 s, SHA-1, the code apps' default) accepted in a window
  of one step either side, and a step once: the last step accepted is kept per member and a code
  must be for a later one, so a code seen over a shoulder cannot be used again.
* **Lockout** (``lockout.py``): failures in a row lock the sign-in for 15 minutes, then 1 hour, then
  24 hours; each failure and each lock is audited and a lock is a notice for the owner. A wrong
  sign-in name, password or code all get the same sentence.
* **Sessions** (``sessions.py``) are minted by a sign-in or a join and checked on every request.

The store is the record; everything that decides runs in one store transaction (a failure count
and a code's step are compare-and-set), and what must happen after the commit (the owner's
notices, closing the sockets of revoked sessions) is handed to the two hooks.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hmac
import re
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pyotp
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from . import audit, invites, lockout, members, sessions
from .contract import LIMITS
from .errors import FamilyError, Invalid
from .sessions import Device, Session
from .store import FamilyStore, Tx

#: argon2id as design 2.2 pins it (argon2-cffi's own default has four lanes).
ARGON2 = {"time_cost": 3, "memory_cost": 64 * 1024, "parallelism": 1, "hash_len": 32,
          "salt_len": 16}  # fmt: skip
HANDLE = re.compile(rf"[a-z0-9_]{{{LIMITS['handle_min']},{LIMITS['handle_max']}}}")
TOTP_DIGITS, TOTP_STEP_S, TOTP_WINDOW = 6, 30, 1
TOTP_ISSUER, TOTP_LABEL = "SUDARSHAN", "Family view"
#: A sign-in from a device label the member has not used for this long is a new phone.
NEW_DEVICE_MS = 90 * sessions.DAY_MS
IST = timezone(timedelta(hours=5, minutes=30))
THREAD_PREFIX = "family-argon2"

Notify = Callable[..., None]  # notify(kind, member_row, **facts)
Revoked = Callable[[list[str]], None]  # revoked(session hashes)


# ---- rules -------------------------------------------------------------------------------------
def handle_of(raw: Any) -> str:
    """A sign-in name as typed, in small letters; Invalid when it breaks the rule."""
    handle = raw.strip().lower() if isinstance(raw, str) else ""
    if not HANDLE.fullmatch(handle):
        raise Invalid("handle_rule", field="handle")
    return handle


def password_ok(raw: Any) -> bool:
    return isinstance(raw, str) and LIMITS["password_min"] <= len(raw) <= LIMITS["password_max"]


def check_password(raw: Any, field_name: str = "password") -> str:
    if not password_ok(raw):
        raise Invalid("password_rule", field=field_name)
    return raw


def device_from(user_agent: str | None, place: str | None = None) -> Device:
    """The phone a request comes from, named from its browser's User-Agent (Android 15 · Chrome 142)."""
    ua = user_agent or ""
    system = ""
    if found := re.search(r"Android (\d+(?:\.\d+)?)", ua):
        system = f"Android {found.group(1)}"
    elif re.search(r"iPhone|iPad", ua):
        system = "iPhone" if "iPhone" in ua else "iPad"
    else:
        for name in ("Windows", "Mac", "Linux"):
            if name in ua:
                system = name
                break
    browser = ""
    for pattern, name in (
        (r"SamsungBrowser/(\d+)", "Samsung Internet"),
        (r"Edg\w*/(\d+)", "Edge"),
        (r"Firefox/(\d+)", "Firefox"),
        (r"(?:Chrome|CriOS)/(\d+)", "Chrome"),
    ):
        if found := re.search(pattern, ua):
            browser = f"{name} {found.group(1)}"
            break  # fmt: skip
    label = " · ".join(part for part in (system, browser) if part) or "Phone"
    return Device(label[: LIMITS["device_label_max"]], label[: LIMITS["device_label_max"]], place)


def clock_words(at_ms: int, now_ms: int) -> str:
    """An instant as the member reads it in India: 19:05, or 19:05 on 16 Nov for another day."""
    at = datetime.fromtimestamp(at_ms / 1000, UTC).astimezone(IST)
    now = datetime.fromtimestamp(now_ms / 1000, UTC).astimezone(IST)
    text = at.strftime("%H:%M")
    return text if at.date() == now.date() else f"{text} on {at.day} {at.strftime('%b')}"


# ---- codes ---------------------------------------------------------------------------------------
def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=TOTP_LABEL, issuer_name=TOTP_ISSUER)


def totp_step(secret: str, code: Any, now_ms: int, last_step: int) -> int | None:
    """The step a code is for, when it is one step either side of now and later than
    ``last_step`` (a step is accepted once); None otherwise."""
    if not isinstance(code, str):
        return None
    code = code.strip().replace(" ", "")
    if len(code) != TOTP_DIGITS or not code.isdigit():
        return None
    totp = pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_STEP_S)
    now_step = now_ms // 1000 // TOTP_STEP_S
    for step in range(now_step - TOTP_WINDOW, now_step + TOTP_WINDOW + 1):
        if step > last_step and hmac.compare_digest(totp.generate_otp(step), code):
            return step
    return None


# ---- passwords -----------------------------------------------------------------------------------
_pool_lock = threading.Lock()
_pool: concurrent.futures.ThreadPoolExecutor | None = None


def _executor() -> concurrent.futures.ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix=THREAD_PREFIX)
        return _pool


def stop_hasher() -> None:
    """End the password thread after the work it has; the next call makes a new one."""
    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.shutdown(wait=True)


class Hasher:
    """argon2id on the password thread. ``verify(None, ...)`` checks the dummy hash, which is made
    once with the same parameters and never matches anything a person can type."""

    def __init__(self, hasher: PasswordHasher | None = None):
        self.argon = hasher or PasswordHasher(**ARGON2, type=Type.ID)
        self._dummy: str | None = None

    def hash_now(self, password: str) -> str:
        return self.argon.hash(password)

    def dummy(self) -> str:
        if self._dummy is None:
            self._dummy = self.argon.hash(secrets.token_urlsafe(32))
        return self._dummy

    def verify_now(self, stored: str | None, password: str) -> bool:
        try:
            return self.argon.verify(stored or self.dummy(), password) and stored is not None
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    async def hash(self, password: str) -> str:
        return await asyncio.wrap_future(_executor().submit(self.hash_now, password))

    async def verify(self, stored: str | None, password: str) -> bool:
        return await asyncio.wrap_future(_executor().submit(self.verify_now, stored, password))

    async def warm(self) -> None:
        """Make the dummy hash now, so that the first unknown name costs one verify too."""
        await asyncio.wrap_future(_executor().submit(self.dummy))


# ---- the accounts ----------------------------------------------------------------------------------
@dataclass
class Outcome:
    """What a decision made in a transaction leaves for after the commit."""

    error: FamilyError | None = None
    session_id: str | None = None
    session: Session | None = None
    notices: list[tuple[str, dict[str, Any], dict[str, Any]]] = field(default_factory=list)
    revoked: list[str] = field(default_factory=list)
    data: Any = None


class Accounts:
    """The family accounts, for the viewer API (a member's own) and the admin ops (the owner's)."""

    def __init__(
        self,
        store: FamilyStore,
        *,
        clock: Callable[[], int],
        hasher: Hasher | None = None,
        notify: Notify | None = None,
        revoked: Revoked | None = None,
    ):
        self.store, self.clock = store, clock
        self.hasher = hasher or Hasher()
        self.notify: Notify = notify or (lambda kind, member, **facts: None)
        self.revoked: Revoked = revoked or (lambda hashes: None)
        self.codes = invites.InviteCodes()

    async def _write(self, decide: Callable[[Tx, int], Outcome]) -> Any:
        """``decide(tx, now)`` in one transaction, then its notices and revokes, then its error."""
        outcome = await self.store.write(lambda tx: decide(tx, self.clock()))
        if outcome.revoked:
            self.revoked(outcome.revoked)
        for kind, member, facts in outcome.notices:
            self.notify(kind, member, **facts)
        if outcome.error is not None:
            raise outcome.error
        return outcome

    # ---- a member's own ----
    async def check(self, session_id: str | None) -> Session:
        """The session a cookie names; FamilyError ``signed_out`` with the member's sentence after
        the expiry, if that is what it found, has been recorded."""
        found = await self.store.write(lambda tx: sessions.verdict(tx, session_id, self.clock()))
        if isinstance(found, FamilyError):
            raise found
        return found

    async def login(self, handle: Any, password: Any, code: Any, device: Device) -> Outcome:
        now = self.clock()
        try:
            name = handle_of(handle)
        except Invalid:
            name = None
        found = await self.store.read(lambda tx: _account(tx, name)) if name else None
        credential = found[1] if found else None
        until = lockout.locked_until(credential, now)
        if until is not None:
            raise FamilyError("locked", time=clock_words(until, now))
        typed = password if password_ok(password) else "-"
        good = await self.hasher.verify(credential["password_hash"] if credential else None, typed)
        if not credential:
            raise FamilyError("signin_failed")
        member_id = found[0]["id"]  # type: ignore[index]
        return await self._write(lambda tx, at: self._settle(tx, member_id, good, code, device, at))

    def _settle(self, tx: Tx, member_id: int, good: bool, code: Any, device: Device,
                now: int) -> Outcome:  # fmt: skip
        member, credential = tx.member(member_id), tx.row("credential", member_id=member_id)
        if member is None or credential is None or member["status"] not in ("active", "suspended"):
            return Outcome(FamilyError("signin_failed"))  # it changed while the password was read
        until = lockout.locked_until(credential, now)
        if until is not None:
            return Outcome(FamilyError("locked", time=clock_words(until, now)))
        step = None
        if good:
            secret = tx.secrets.open(credential["totp_secret_enc"])  # type: ignore[union-attr]
            step = totp_step(secret, code, now, credential["totp_last_counter"])
        if step is None:
            return _failed(tx, member, credential, "code" if good else "password", device, now)
        tx.update("credential", {"member_id": member_id},
                  {"failed_count": 0, "locked_until": None, "lock_level": 0,
                   "totp_last_counter": step, "updated_at": now})  # fmt: skip
        if member["status"] == "suspended":
            audit.record(
                tx,
                at=now,
                actor=audit.member_actor(member_id),
                kind="login_failed",
                member_id=member_id,
                ip_hash=device.place,
                detail={"part": "suspended"},
            )
            return Outcome(FamilyError("signed_out", "suspended"))
        new_phone = not tx.label_seen(member_id, device.label, now - NEW_DEVICE_MS)
        return _signed_in(tx, member, device, now, {"new_device": new_phone}, new_phone)

    async def join_info(self, code: Any) -> dict[str, Any]:
        now = self.clock()

        def read(tx: Tx) -> dict[str, Any]:
            invite, member = invites.usable(tx, code, now)
            secret = tx.secrets.open(invite["totp_secret_enc"])  # type: ignore[union-attr]
            return {
                "display_name": member["display_name"],
                "otpauth_uri": totp_uri(secret),
                "totp_key": secret,
                "expires_at": invite["expires_at"],
                "rules": {k: LIMITS[k] for k in ("handle_min", "handle_max", "password_min")},
            }

        return await self.store.read(read)

    async def join(self, code: Any, handle: Any, password: Any, totp: Any,
                   device: Device) -> Outcome:  # fmt: skip
        name = handle_of(handle)
        check_password(password)
        now = self.clock()

        def before(tx: Tx) -> None:  # refuse a dead link or a taken name before the slow hash
            _, member = invites.usable(tx, code, now)
            if tx.handle_taken(name, member["id"]):
                raise Invalid("handle_taken", field="handle")

        await self.store.read(before)
        hashed = await self.hasher.hash(password)
        return await self._write(lambda tx, at: _join(tx, code, name, hashed, totp, device, at))

    async def logout(self, session: Session, everywhere: bool = False) -> Outcome:
        def decide(tx: Tx, now: int) -> Outcome:
            actor = audit.member_actor(session.member_id)
            if everywhere:
                done = sessions.revoke_member(tx, session.member_id, now, "logout_all")
                audit.record(tx, at=now, actor=actor, kind="logout_all",
                             member_id=session.member_id, session=session.id_hash,
                             detail={"sessions": len(done)})  # fmt: skip
                return Outcome(revoked=done)
            sessions.revoke(tx, session.id_hash, now, "logout")
            audit.record(tx, at=now, actor=actor, kind="logout", member_id=session.member_id,
                         session=session.id_hash)  # fmt: skip
            return Outcome(revoked=[session.id_hash])

        return await self._write(decide)

    async def change_password(self, session: Session, current: Any, new: Any,
                              code: Any) -> Outcome:  # fmt: skip
        check_password(new)
        now = self.clock()
        credential = await self.store.read(
            lambda tx: tx.row("credential", member_id=session.member_id)
        )
        until = lockout.locked_until(credential, now)
        if until is not None:
            raise FamilyError("locked", time=clock_words(until, now))
        typed = current if password_ok(current) else "-"
        good = await self.hasher.verify(credential["password_hash"] if credential else None, typed)
        hashed = await self.hasher.hash(new) if good else ""
        changed_at = credential["password_changed_at"] if credential else None

        def decide(tx: Tx, at: int) -> Outcome:
            member = tx.member(session.member_id)
            row = tx.row("credential", member_id=session.member_id)
            if row is None or member is None or row["password_changed_at"] != changed_at:
                return Outcome(FamilyError("signed_out"))  # reset or changed meanwhile
            if (until := lockout.locked_until(row, at)) is not None:
                return Outcome(FamilyError("locked", time=clock_words(until, at)))
            step = None
            if good:
                secret = tx.secrets.open(row["totp_secret_enc"])  # type: ignore[union-attr]
                step = totp_step(secret, code, at, row["totp_last_counter"])
            if step is None:
                device = Device(session.row["device_label"], session.row["ua_summary"])
                wrong = Invalid("password_current", field="current")
                return _failed(tx, member, row, "code" if good else "password", device, at, wrong)
            tx.update("credential", {"member_id": session.member_id},
                      {"password_hash": hashed, "password_changed_at": at, "failed_count": 0,
                       "locked_until": None, "lock_level": 0, "totp_last_counter": step,
                       "updated_at": at})  # fmt: skip
            done = sessions.revoke_member(tx, session.member_id, at, "password_changed",
                                          keep=session.id_hash)  # fmt: skip
            audit.record(tx, at=at, actor=audit.member_actor(session.member_id),
                         kind="password_changed", member_id=session.member_id,
                         session=session.id_hash, detail={"sessions": len(done)})  # fmt: skip
            return Outcome(revoked=done)

        return await self._write(decide)

    async def devices(self, session: Session) -> list[dict[str, Any]]:
        return await self.store.read(
            lambda tx: sessions.devices(tx, session.member_id, self.clock(), session.id_hash)
        )

    async def rename(self, session: Session, device_id: Any, label: Any) -> None:
        await self.store.write(
            lambda tx: sessions.rename(tx, session.member_id, device_id, label, self.clock())
        )

    async def sign_out_device(self, session: Session, device_id: Any) -> Outcome:
        def decide(tx: Tx, now: int) -> Outcome:
            row = sessions.find(tx, session.member_id, device_id, now)
            sessions.revoke(tx, row["id_hash"], now, "member")
            audit.record(tx, at=now, actor=audit.member_actor(session.member_id),
                         kind="session_revoked", member_id=session.member_id,
                         session=row["id_hash"], detail={"why": "member"})  # fmt: skip
            return Outcome(revoked=[row["id_hash"]])

        return await self._write(decide)

    # ---- the owner's ----
    async def member_list(self) -> list[dict[str, Any]]:
        return await self.store.read(lambda tx: members.cards(tx, self.clock()))

    async def member_create(self, args: dict[str, Any]) -> dict[str, Any]:
        def decide(tx: Tx, now: int) -> Outcome:
            return Outcome(data=members.card(tx, members.create(tx, args, now), now))

        return (await self._write(decide)).data

    async def member_update(self, args: dict[str, Any]) -> dict[str, Any]:
        def decide(tx: Tx, now: int) -> Outcome:
            return Outcome(data=members.card(tx, members.update(tx, args, now), now))

        return (await self._write(decide)).data

    async def member_status(self, args: dict[str, Any]) -> dict[str, Any]:
        def decide(tx: Tx, now: int) -> Outcome:
            after, done = members.set_status(tx, args, now)
            return Outcome(data=members.card(tx, after, now), revoked=done)

        return (await self._write(decide)).data

    async def member_remove(self, args: dict[str, Any]) -> None:
        def decide(tx: Tx, now: int) -> Outcome:
            before, done = members.remove(tx, args, now)
            return Outcome(revoked=done, notices=[("member_removed", before, {})])

        await self._write(decide)

    async def invite_create(self, args: dict[str, Any]) -> dict[str, Any]:
        member_id, request_id = args.get("member_id"), _request_id(args)
        now = self.clock()
        if isinstance(member_id, int) and (kept := self.codes.get(request_id, member_id, now)):
            return invites.answer(member_id, *kept)

        def decide(tx: Tx, at: int) -> Outcome:
            member = members.live(tx, member_id)
            code, ends = invites.create(tx, member["id"], new_totp_secret(), at, request_id)
            return Outcome(data=(member["id"], code, ends))

        made = (await self._write(decide)).data
        self.codes.put(request_id, *made)
        return invites.answer(*made)

    async def invite_revoke(self, args: dict[str, Any]) -> None:
        def decide(tx: Tx, now: int) -> Outcome:
            invites.revoke_open(tx, members.live(tx, args.get("member_id"))["id"], now)
            return Outcome()

        await self._write(decide)

    async def signin_reset(self, args: dict[str, Any]) -> dict[str, Any]:
        member_id, request_id = args.get("member_id"), _request_id(args)
        if isinstance(member_id, int) and (
            kept := self.codes.get(request_id, member_id, self.clock())
        ):
            return invites.answer(member_id, *kept)  # a retry of a reset already made

        def decide(tx: Tx, now: int) -> Outcome:
            after, done = members.reset_signin(tx, args, now)
            code, ends = invites.create(tx, after["id"], new_totp_secret(), now, request_id)
            return Outcome(data=(after["id"], code, ends), revoked=done)

        made = (await self._write(decide)).data
        self.codes.put(request_id, *made)
        return invites.answer(*made)

    async def device_list(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        def read(tx: Tx) -> list[dict[str, Any]]:
            member = members.live(tx, args.get("member_id"))
            return sessions.devices(tx, member["id"], self.clock())

        return await self.store.read(read)

    async def device_revoke(self, args: dict[str, Any]) -> None:
        def decide(tx: Tx, now: int) -> Outcome:
            member = members.live(tx, args.get("member_id"))
            row = sessions.find(tx, member["id"], args.get("id"), now)
            sessions.revoke(tx, row["id_hash"], now, "owner")
            audit.record(tx, at=now, actor=audit.OWNER, kind="session_revoked",
                         member_id=member["id"], session=row["id_hash"],
                         detail={"why": "owner"})  # fmt: skip
            return Outcome(revoked=[row["id_hash"]])

        await self._write(decide)


def _request_id(args: dict[str, Any]) -> str | None:
    value = args.get("request_id")
    if value is not None and not (isinstance(value, str) and 1 <= len(value) <= 64):
        raise Invalid("invalid", field="request_id")
    return value


def _account(tx: Tx, handle: str) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
    member = tx.member_by_handle(handle)
    if member is None or member["status"] == "removed":
        return None
    return member, tx.row("credential", member_id=member["id"])


def _failed(tx: Tx, member: dict[str, Any], credential: dict[str, Any], part: str,
            device: Device, now: int, wrong: FamilyError | None = None) -> Outcome:  # fmt: skip
    """One more failure in a row: counted, audited and, at a threshold, a lock and a notice.
    ``wrong`` is the answer below a threshold (the one sentence for any wrong part)."""
    failures = int(credential["failed_count"]) + 1
    values: dict[str, Any] = {"failed_count": failures, "updated_at": now}
    actor = audit.member_actor(member["id"])
    audit.record(tx, at=now, actor=actor, kind="login_failed", member_id=member["id"],
                 ip_hash=device.place, detail={"part": part, "failures": failures})  # fmt: skip
    seconds = lockout.lock_seconds(failures)
    if seconds is None:
        tx.update("credential", {"member_id": member["id"]}, values)
        return Outcome(wrong or FamilyError("signin_failed"))
    until = now + seconds * 1000
    values |= {"locked_until": until, "lock_level": lockout.level(failures)}
    tx.update("credential", {"member_id": member["id"]}, values)
    audit.record(
        tx,
        at=now,
        actor="system",
        kind="lockout",
        member_id=member["id"],
        ip_hash=device.place,
        detail={"failures": failures, "until": until},
    )
    facts = {"attempts": str(failures), "until": str(until // 1000), "device": device.label}
    return Outcome(FamilyError("locked", time=clock_words(until, now)),
                   notices=[("lockout", member, facts)])  # fmt: skip


def _signed_in(tx: Tx, member: dict[str, Any], device: Device, now: int, detail: dict[str, Any],
               new_phone: bool) -> Outcome:  # fmt: skip
    session_id, revoked = sessions.mint(tx, member["id"], device, now)
    hashed = sessions.id_hash(session_id)
    audit.record(tx, at=now, actor=audit.member_actor(member["id"]), kind="login_ok",
                 member_id=member["id"], session=hashed, ip_hash=device.place,
                 detail={"device": device.label, **detail})  # fmt: skip
    session = sessions.check(tx, session_id, now)
    notices = [("new_device", member, {"device": device.label})] if new_phone else []
    return Outcome(session_id=session_id, session=session, notices=notices, revoked=revoked)


def _join(tx: Tx, code: Any, handle: str, hashed: str, totp: Any, device: Device,
          now: int) -> Outcome:  # fmt: skip
    invite, member = invites.usable(tx, code, now)
    if tx.handle_taken(handle, member["id"]):
        raise Invalid("handle_taken", field="handle")
    secret = tx.secrets.open(invite["totp_secret_enc"])  # type: ignore[union-attr]
    step = totp_step(secret, totp, now, 0)
    if step is None:
        raise FamilyError("code_mismatch", field="totp")
    tx.delete("credential", member_id=member["id"])
    tx.insert("credential", {"member_id": member["id"], "password_hash": hashed,
                             "totp_secret_enc": tx.secrets.seal(secret),  # type: ignore[union-attr]
                             "totp_last_counter": step, "password_changed_at": now,
                             "updated_at": now})  # fmt: skip
    invites.mark_used(tx, invite["id"], now)
    joined = tx.member_change(member["id"], member["version"],
                              {"status": "active", "handle": handle}, now)  # fmt: skip
    audit.record(
        tx,
        at=now,
        actor=audit.member_actor(member["id"]),
        kind="invite_used",
        member_id=member["id"],
        ip_hash=device.place,
        detail={"invite": invite["id"]},
    )
    outcome = _signed_in(tx, joined, device, now, {"joined": True}, new_phone=False)
    outcome.notices.append(("invite_used", joined, {}))
    return outcome
