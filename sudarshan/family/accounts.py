"""Family sign-in (M2.6, M2.9, M2.10, M2.11; design 2.2): passwords, codes, joins, sign-ins, sessions.

* **Passwords** are argon2id with time cost 3, 64 MiB and one lane, verified on a thread of their
  own (``Hasher``): a verify is slow on purpose (about 0.2 s and 64 MiB here), so it never runs on
  the event loop or the store thread, and one at a time bounds the memory to one verify's. A
  sign-in name nobody has (or a member who has not joined) is verified against a fixed dummy hash
  with the same parameters, so the answer's timing does not tell names apart.
* **The password work is bounded** (review H2: this is the one door strangers reach, on a box that
  also runs the owner's trading): at most ``PASSWORD_SLOTS`` checks are taken at a time, running
  or waiting, counted on the event loop that takes them. A sign-in, join or password change past
  them is answered at once that the view is busy, before any name is looked up (so a busy answer
  says nothing about names), and a check that waited ``PASSWORD_WAIT_S`` without starting is
  dropped unrun with the same answer. A burst is paid for with at most that many checks, whoever
  is still waiting for them; nginx's own limit per address is the outer layer, not this one.
* **Codes** are TOTP (RFC 6238: 6 digits, 30 s, SHA-1, the code apps' default) accepted in a window
  of one step either side, and a step once: the last step accepted is kept per member and a code
  must be for a later one, so a code seen over a shoulder cannot be used again.
* **Lockout** (``lockout.py``, where the whole rule is written; review M2): wrong sign-ins are
  counted per name as typed and per phone the member signed in on before, with a back-off that
  grows from 1 minute to at most 15; a name nobody has is counted in the same table the same way,
  and one network area that fails too often is held back. So a stranger cannot keep a member out
  of their own phone, and the answers, their timing and the counts say nothing of which names
  exist. Each failure and the first lock of a run are audited for a member, and that lock is a
  notice for the owner. A wrong sign-in name, password or code all get the same sentence.
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
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pyotp
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from . import audit, invites, lockout, members, sessions
from .contract import LIMITS, LOCKOUT
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
#: Password checks taken at a time, running or waiting: a burst costs at most this many (H2).
PASSWORD_SLOTS = 8
#: A check that waited this long for the password thread is dropped unrun (the box is too busy).
PASSWORD_WAIT_S = 5.0
_LATE = object()

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


def typed_name(raw: Any) -> str:
    """The name a sign-in is counted against: as the rule reads it, or "" for a name that breaks
    the rule (no member has one, so every such name shares one count)."""
    try:
        return handle_of(raw)
    except Invalid:
        return ""


def device_from(
    user_agent: str | None,
    place: str | None = None,
    *,
    area: str | None = None,
    phone: str | None = None,
) -> Device:
    """The phone a request comes from, named from its browser's User-Agent (Android 15 · Chrome 142),
    with the keyed hashes of its address and network area and the phone cookie it sent."""
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
    label = (" · ".join(part for part in (system, browser) if part) or "Phone")[
        : LIMITS["device_label_max"]
    ]
    return Device(label, label, place, area, phone)


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
    once with the same parameters and never matches anything a person can type.

    ``hash`` and ``verify`` take one of ``slots`` (FamilyError ``busy`` when none is free) and give
    it back when they end; a check still waiting after ``wait_s`` is dropped unrun (``busy`` too).
    The slots are counted only on the event loop that awaits them, so they need no lock."""

    def __init__(
        self,
        hasher: PasswordHasher | None = None,
        *,
        slots: int = PASSWORD_SLOTS,
        wait_s: float = PASSWORD_WAIT_S,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.argon = hasher or PasswordHasher(**ARGON2, type=Type.ID)
        self._dummy: str | None = None
        self.slots, self.wait_s, self._clock = slots, wait_s, clock
        self.taken = 0

    def full(self) -> bool:
        """Every slot is taken: a new check would be refused."""
        return self.taken >= self.slots

    async def _bounded(self, work: Callable[..., Any], *args: Any) -> Any:
        if self.full():
            raise FamilyError("busy")
        self.taken += 1
        deadline = self._clock() + self.wait_s

        def run() -> Any:
            return _LATE if self._clock() > deadline else work(*args)

        try:
            done = await asyncio.wrap_future(_executor().submit(run))
        finally:
            self.taken -= 1
        if done is _LATE:
            raise FamilyError("busy")
        return done

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
        return await self._bounded(self.hash_now, password)

    async def verify(self, stored: str | None, password: str) -> bool:
        return await self._bounded(self.verify_now, stored, password)

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
    #: A wrong attempt was counted (the network area counts it too, unless from the member's phone).
    counted: bool = False
    #: The phone cookie's new value, after a right sign-in, a join or a password change.
    phone: str | None = None


@dataclass(frozen=True)
class Attempt:
    """A sign-in as read before its password is checked: the member the name is (None: nobody, or
    nobody who has joined), the hash to check, the count it is judged on, whether it came from that
    member's own phone, and the end of that count's lock (None: not locked)."""

    member_id: int | None
    password_hash: str | None
    key: str
    phone: bool
    until: int | None


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
        max_members: int = LIMITS["max_members"],
        max_devices: int = LIMITS["max_devices"],
    ):
        self.store, self.clock = store, clock
        self.max_members, self.max_devices = max_members, max_devices
        self.hasher = hasher or Hasher()
        self.notify: Notify = notify or (lambda kind, member, **facts: None)
        self.revoked: Revoked = revoked or (lambda hashes: None)
        self.codes = invites.InviteCodes()
        self.areas = lockout.Areas()

    async def _write(self, decide: Callable[[Tx, int], Outcome]) -> Any:
        """``decide(tx, now)`` in one transaction, then its notices and revokes, then its error."""
        return self._after(await self.store.write(lambda tx: decide(tx, self.clock())))

    def _after(self, outcome: Outcome) -> Outcome:
        """What a committed decision leaves: its revokes, its notices, then its error."""
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
        """A sign-in, judged by the rule ``lockout.py`` writes down: a known name and a name nobody
        has take the same steps (one read, one password check, one write) and the same answers."""
        now = self.clock()
        if self.hasher.full():  # before any name is looked up: busy says nothing about names
            raise FamilyError("busy")
        name = typed_name(handle)
        seen = await self.store.read(lambda tx: _attempt(tx, name, device, now))
        held = None if seen.phone else self.areas.held(device.area, now)
        ends = [at for at in (seen.until, held) if at is not None]
        if ends:  # refused at once: no password is tried and nothing is counted
            raise FamilyError("locked", time=clock_words(max(ends), now))
        typed = password if password_ok(password) else "-"
        good = await self.hasher.verify(seen.password_hash, typed)
        outcome = await self.store.write(
            lambda tx: self._settle(tx, seen, good, code, device, self.clock())
        )
        if outcome.counted and not seen.phone:
            self.areas.failed(device.area, now)
        return self._after(outcome)

    def _settle(self, tx: Tx, seen: Attempt, good: bool, code: Any, device: Device,
                now: int) -> Outcome:  # fmt: skip
        if (until := lockout.locked_until(lockout.count(tx, seen.key), now)) is not None:
            return Outcome(FamilyError("locked", time=clock_words(until, now)))  # meanwhile
        member = tx.member(seen.member_id) if seen.member_id is not None else None
        credential = tx.row("credential", member_id=seen.member_id) if member else None
        if (
            member is None
            or credential is None
            or member["status"] not in ("active", "suspended")
            or credential["password_hash"] != seen.password_hash
        ):  # nobody's name, or no longer the member and the password that were checked
            member = credential = None
        step = None
        if credential is not None and good:
            secret = tx.secrets.open(credential["totp_secret_enc"])  # type: ignore[union-attr]
            step = totp_step(secret, code, now, credential["totp_last_counter"])
        if step is None or member is None or credential is None:
            return _failed(tx, seen, member, "code" if good else "password", device, now)
        member_id = member["id"]
        lockout.clear(tx, seen.key)
        tx.update("credential", {"member_id": member_id},
                  {"totp_last_counter": step, "updated_at": now})  # fmt: skip
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
        detail = {"new_device": new_phone, "known_phone": seen.phone}
        outcome = _signed_in(tx, member, device, now, detail, new_phone, self.max_devices)
        outcome.phone = _phone_cookie(tx, device, member_id, credential["password_changed_at"], now)
        return outcome

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
        if self.hasher.full():
            raise FamilyError("busy")
        now = self.clock()

        def before(tx: Tx) -> None:  # refuse a dead link or a taken name before the slow hash
            _, member = invites.usable(tx, code, now)
            if tx.handle_taken(name, member["id"]):
                raise Invalid("handle_taken", field="handle")

        await self.store.read(before)
        hashed = await self.hasher.hash(password)
        limit = self.max_devices
        return await self._write(
            lambda tx, at: _join(tx, code, name, hashed, totp, device, at, limit)
        )

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

    async def change_password(self, session: Session, current: Any, new: Any, code: Any,
                              device: Device | None = None) -> Outcome:  # fmt: skip
        """A member's own password change. Its wrong attempts are counted against this session
        alone (a stranger cannot reach it, and it never locks a sign-in); a right one gives this
        phone a fresh token, every earlier token of the member being worthless from now."""
        check_password(new)
        if self.hasher.full():
            raise FamilyError("busy")
        now = self.clock()

        def read(tx: Tx) -> tuple[str, dict[str, Any] | None, int | None]:
            key = lockout.password_key(tx.secrets, session.member_id, session.id_hash)  # type: ignore[arg-type]
            row = tx.row("credential", member_id=session.member_id)
            return key, row, lockout.locked_until(lockout.count(tx, key), now)

        key, credential, until = await self.store.read(read)
        if until is not None:
            raise FamilyError("locked", time=clock_words(until, now))
        stored = credential["password_hash"] if credential else None
        typed = current if password_ok(current) else "-"
        good = await self.hasher.verify(stored, typed)
        hashed = await self.hasher.hash(new) if good else ""
        changed_at = credential["password_changed_at"] if credential else None
        here = device or Device(session.row["device_label"], session.row["ua_summary"])

        def decide(tx: Tx, at: int) -> Outcome:
            member = tx.member(session.member_id)
            row = tx.row("credential", member_id=session.member_id)
            if row is None or member is None or row["password_changed_at"] != changed_at:
                return Outcome(FamilyError("signed_out"))  # reset or changed meanwhile
            if (until := lockout.locked_until(lockout.count(tx, key), at)) is not None:
                return Outcome(FamilyError("locked", time=clock_words(until, at)))
            step = None
            if good:
                secret = tx.secrets.open(row["totp_secret_enc"])  # type: ignore[union-attr]
                step = totp_step(secret, code, at, row["totp_last_counter"])
            if step is None:
                seen = Attempt(member["id"], stored, key, True, None)
                wrong = Invalid("password_current", field="current")
                return _failed(tx, seen, member, "code" if good else "password", here, at, wrong)
            tx.update("credential", {"member_id": session.member_id},
                      {"password_hash": hashed, "password_changed_at": at,
                       "totp_last_counter": step, "updated_at": at})  # fmt: skip
            lockout.clear(tx, key)
            done = sessions.revoke_member(tx, session.member_id, at, "password_changed",
                                          keep=session.id_hash)  # fmt: skip
            audit.record(tx, at=at, actor=audit.member_actor(session.member_id),
                         kind="password_changed", member_id=session.member_id,
                         session=session.id_hash, detail={"sessions": len(done)})  # fmt: skip
            return Outcome(revoked=done, phone=_phone_cookie(tx, here, member["id"], at, at))

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
            made = members.create(tx, args, now, self.max_members)
            return Outcome(data=members.card(tx, made, now))

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


def _attempt(tx: Tx, name: str, device: Device, now: int) -> Attempt:
    """What a sign-in is judged on before its password is checked: a name nobody has (or nobody
    who has joined) gives the same shape as a member's, with no hash (the dummy is checked)."""
    member = tx.member_by_handle(name) if name else None
    credential = None
    if member is not None and member["status"] in ("active", "suspended"):
        credential = tx.row("credential", member_id=member["id"])
    keys = tx.secrets
    nonce = None
    if member is not None and credential is not None:
        changed_at = credential["password_changed_at"]
        nonce = lockout.known_phone(keys, device.phone, member["id"], changed_at, now)
    key = lockout.phone_key(keys, name, nonce) if nonce else lockout.name_key(keys, name)  # type: ignore[arg-type]
    return Attempt(
        member["id"] if member is not None and credential is not None else None,
        credential["password_hash"] if credential is not None else None,
        key,
        nonce is not None,
        lockout.locked_until(lockout.count(tx, key), now),
    )


def _phone_cookie(tx: Tx, device: Device, member_id: int, changed_at: int, now: int) -> str:
    """The phone cookie after a right sign-in on it: this member's fresh token, the others kept."""
    token = lockout.phone_token(tx.secrets, member_id, changed_at, now)  # type: ignore[arg-type]
    return lockout.with_token(device.phone, token)


def _failed(tx: Tx, seen: Attempt, member: dict[str, Any] | None, part: str, device: Device,
            now: int, wrong: FamilyError | None = None) -> Outcome:  # fmt: skip
    """One more wrong attempt on its count. A member's is audited, and the first lock of a run is
    audited and told to the owner; a name nobody has is counted the same way and nothing else.
    ``wrong`` is the answer below a lock (by default the one sentence for any wrong part)."""
    member_id = member["id"] if member is not None else None
    failures, until = lockout.failed(tx, seen.key, member_id, now)
    outcome = Outcome(counted=True)
    if member is not None:
        audit.record(tx, at=now, actor=audit.member_actor(member["id"]), kind="login_failed",
                     member_id=member["id"], ip_hash=device.place,
                     detail={"part": part, "failures": failures,
                             "known_phone": seen.phone})  # fmt: skip
    if until is None:
        outcome.error = wrong or FamilyError("signin_failed")
        return outcome
    outcome.error = FamilyError("locked", time=clock_words(until, now))
    if member is not None and failures == LOCKOUT["after"]:
        audit.record(tx, at=now, actor=audit.SYSTEM, kind="lockout", member_id=member["id"],
                     ip_hash=device.place, detail={"failures": failures, "until": until,
                                                   "known_phone": seen.phone})  # fmt: skip
        facts = {"attempts": str(failures), "until": str(until // 1000), "device": device.label}
        outcome.notices.append(("lockout", member, facts))
    return outcome


def _signed_in(tx: Tx, member: dict[str, Any], device: Device, now: int, detail: dict[str, Any],
               new_phone: bool, max_devices: int) -> Outcome:  # fmt: skip
    session_id, revoked = sessions.mint(tx, member["id"], device, now, max_devices)
    hashed = sessions.id_hash(session_id)
    audit.record(tx, at=now, actor=audit.member_actor(member["id"]), kind="login_ok",
                 member_id=member["id"], session=hashed, ip_hash=device.place,
                 detail={"device": device.label, **detail})  # fmt: skip
    session = sessions.check(tx, session_id, now)
    notices = [("new_device", member, {"device": device.label})] if new_phone else []
    return Outcome(session_id=session_id, session=session, notices=notices, revoked=revoked)


def _join(tx: Tx, code: Any, handle: str, hashed: str, totp: Any, device: Device, now: int,
          max_devices: int) -> Outcome:  # fmt: skip
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
    lockout.clear(tx, lockout.name_key(tx.secrets, handle))  # type: ignore[arg-type]
    audit.record(
        tx,
        at=now,
        actor=audit.member_actor(member["id"]),
        kind="invite_used",
        member_id=member["id"],
        ip_hash=device.place,
        detail={"invite": invite["id"]},
    )
    outcome = _signed_in(tx, joined, device, now, {"joined": True}, False, max_devices)
    outcome.notices.append(("invite_used", joined, {}))
    outcome.phone = _phone_cookie(tx, device, member["id"], now, now)
    return outcome
