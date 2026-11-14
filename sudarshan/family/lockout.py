"""When a family sign-in is refused for wrong attempts, and which phones a member signed in on (M2.11,
design 2.2; review M2). No I/O but the store transaction it is given and its own memory.

The rule, as a stranger meets it and as a member does:

* **Counted per name, and per phone.** Wrong sign-ins are counted against the sign-in name as it
  was typed. An attempt from a phone this member signed in on before (one that holds their phone
  token, below) counts against that phone alone; every other attempt counts against the name's
  common count. So a stranger who knows a member's sign-in name can hold back sign-ins from phones
  the member has not used, never from the member's own phone: there the right password and code
  still sign in.
* **A back-off that grows but stays short.** The 5th failure in a row locks for 1 minute, and each
  failure after it locks again for twice as long as the last, up to 15 minutes (``LOCKOUT``).
  While a count is locked an attempt against it is refused at once: no password is tried and it
  counts for nothing. A right sign-in clears its count; a count is forgotten a day after its last
  failure and its lock (``prune``); the owner clears a member's counts (``clear_member``). The
  owner is told at the first lock of a run, not at every lock after it.
* **A name nobody has is counted the same way**, in the same table, under the same keyed hash of
  the name typed, with the same thresholds: the same sentence, a 429 at the same failure, one
  password check of the same cost and one write each time. Nothing an outsider can see tells a
  member's name from any other. Names that break the naming rule share one count, which no
  member's name can be in.
* **One network area** (an IPv4 address, or the /64 of an IPv6 one, which a phone or a home holds)
  may fail 20 sign-ins in 10 minutes from phones not known for the names typed (``AREA_LIMIT``);
  past that its sign-ins from such phones are refused until the oldest of those failures is 10
  minutes old. ``Areas`` keeps this in memory (a restart forgets it). nginx's own limit per address
  is an outer layer; this one holds without it.

**The phone token.** After a right sign-in, a join or a password change the phone gets the HttpOnly
cookie ``__Host-sudarshan_family_phone`` for 90 days: one token for each member who signed in on it,
four at most (a shared tablet). A token is the member's id, when it was given, a random part, and a
keyed hash of those and of when the member's password was last set: a password change or a reset
makes every earlier token of that member worthless, and nobody can make one without the store key.
A token that is another member's, out of date or altered counts as no token. A phone's own count is
keyed by the token's random part, which a new sign-in replaces, so a right sign-in starts it afresh.
"""

from __future__ import annotations

import hmac
import ipaddress
import re
import secrets
from collections import OrderedDict, deque
from typing import Any

from sqlalchemy import and_, delete, or_

from .contract import AREA_LIMIT, LIMITS, LOCKOUT, PHONE_DAYS
from .store import Secrets, Tx

TABLE = "signin_lock"
#: A count is forgotten this long after its last failure and after its lock ended.
FORGET_MS = 86_400_000
PHONE_MS = PHONE_DAYS * 86_400_000
#: The network areas kept in memory at most; the one that failed longest ago goes first.
MAX_AREAS = 4096
#: A phone cookie longer than this is not read at all (four tokens are about 300 characters).
MAX_COOKIE = 600
_TOKEN = re.compile(r"[0-9]{1,10}\.[0-9]{1,12}\.[A-Za-z0-9_-]{16}\.[0-9a-f]{32}")


# ---- the back-off -----------------------------------------------------------------------------------
def lock_seconds(failures: int) -> int | None:
    """How long the ``failures``-th failure in a row locks its count, in seconds; None: no lock."""
    after = LOCKOUT["after"]
    if failures < after:
        return None
    return min(LOCKOUT["first_s"] << min(failures - after, 16), LOCKOUT["max_s"])


def locked_until(row: dict[str, Any] | None, now: int) -> int | None:
    """The end of a count's lock (epoch ms) while it holds, else None."""
    until = (row or {}).get("locked_until")
    return int(until) if until is not None and int(until) > now else None


# ---- the counts ---------------------------------------------------------------------------------------
def name_key(keys: Secrets, name: str) -> str:
    """The common count of a name as typed ("" stands for every name that breaks the rule)."""
    return keys.keyed("signin", name)


def phone_key(keys: Secrets, name: str, nonce: str) -> str:
    """The count of a name from one of its member's own phones."""
    return keys.keyed("signin", name, "phone", nonce)


def password_key(keys: Secrets, member_id: int, session_hash: str) -> str:
    """The count of wrong current passwords in one signed-in session (a password change)."""
    return keys.keyed("password", str(member_id), session_hash)


def count(tx: Tx, key: str) -> dict[str, Any] | None:
    return tx.row(TABLE, key=key)


def failed(tx: Tx, key: str, member_id: int | None, now: int) -> tuple[int, int | None]:
    """One more failure on a count: how many in a row now, and the end of the lock it made."""
    row = count(tx, key)
    failures = (int(row["failures"]) if row else 0) + 1
    seconds = lock_seconds(failures)
    until = now + seconds * 1000 if seconds is not None else None
    values = {"member_id": member_id, "failures": failures, "locked_until": until, "failed_at": now}
    if row is None:
        tx.insert(TABLE, {"key": key, **values})
    else:
        tx.update(TABLE, {"key": key}, values)
    return failures, until


def clear(tx: Tx, key: str) -> None:
    tx.delete(TABLE, key=key)


def clear_member(tx: Tx, member: dict[str, Any]) -> int:
    """Every count of a member: their name's common count and their phones'; how many."""
    t = tx.t(TABLE)
    keys = [name_key(tx.secrets, member["handle"])] if member.get("handle") and tx.secrets else []
    where = or_(t.c.member_id == member["id"], t.c.key.in_(keys))
    return int(tx.conn.execute(delete(t).where(where)).rowcount)


def name_locked_until(tx: Tx, handle: str | None, now: int) -> int | None:
    """The end of the lock on a member's name for phones they have not used, while it holds."""
    if not handle or tx.secrets is None:
        return None
    return locked_until(count(tx, name_key(tx.secrets, handle)), now)


def prune(tx: Tx, now: int) -> int:
    """Forget the counts whose last failure and lock ended more than a day ago; how many."""
    t, old = tx.t(TABLE), now - FORGET_MS
    ended = or_(t.c.locked_until.is_(None), t.c.locked_until < old)
    return int(tx.conn.execute(delete(t).where(and_(t.c.failed_at < old, ended))).rowcount)


# ---- the network areas ----------------------------------------------------------------------------
def area_of(address: str) -> str:
    """An address's network area: an IPv4 address as it is, an IPv6 one by its /64 (one phone or
    one home holds a /64), an IPv4 address carried in IPv6 as the IPv4 one."""
    text = address.strip()
    try:
        ip = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return text
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return f"{ipaddress.ip_network(f'{ip}/64', strict=False).network_address}/64"
    return str(ip)


class Areas:
    """The recent failures of each network area from phones not known for the names typed, in
    memory, on the event loop that asks (the area is the keyed hash ``Device.area`` carries)."""

    def __init__(
        self,
        failures: int = AREA_LIMIT["failures"],
        window_ms: int = AREA_LIMIT["window_s"] * 1000,
        size: int = MAX_AREAS,
    ):
        self.failures, self.window_ms, self.size = failures, window_ms, size
        self._seen: OrderedDict[str, deque[int]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._seen)

    def _recent(self, area: str, now: int) -> deque[int] | None:
        found = self._seen.get(area)
        while found and found[0] <= now - self.window_ms:
            found.popleft()
        if found is not None and not found:
            del self._seen[area]
            return None
        return found

    def held(self, area: str | None, now: int) -> int | None:
        """When the area may try again while it is refused; None while it may."""
        found = self._recent(area, now) if area else None
        if found is None or len(found) < self.failures:
            return None
        return found[0] + self.window_ms

    def failed(self, area: str | None, now: int) -> None:
        if not area:
            return
        found = self._recent(area, now)
        if found is None:
            found = self._seen[area] = deque(maxlen=self.failures)
        found.append(now)
        self._seen.move_to_end(area)
        while len(self._seen) > self.size:
            self._seen.popitem(last=False)


# ---- the phone tokens -------------------------------------------------------------------------------
def _mac(keys: Secrets, member_id: int, changed_at: int, issued: int, nonce: str) -> str:
    return keys.keyed("phone", str(member_id), str(changed_at), str(issued), nonce)[:32]


def phone_token(keys: Secrets, member_id: int, changed_at: int, now: int) -> str:
    """A new token for this member on this phone (``changed_at``: when their password was set)."""
    nonce, issued = secrets.token_urlsafe(12), now // 1000
    return f"{member_id}.{issued}.{nonce}.{_mac(keys, member_id, changed_at, issued, nonce)}"


def tokens_of(cookie: str | None) -> list[str]:
    """The well-formed tokens a phone cookie holds, four at most."""
    if not cookie or len(cookie) > MAX_COOKIE:
        return []
    return [t for t in cookie.split("~") if _TOKEN.fullmatch(t)][: LIMITS["max_members"]]


def known_phone(
    keys: Secrets | None, cookie: str | None, member_id: int, changed_at: int, now: int
) -> str | None:
    """The random part of this member's token on this phone while it holds; None otherwise."""
    if keys is None:
        return None
    for token in tokens_of(cookie):
        owner, issued, nonce, mac = token.split(".")
        if int(owner) != member_id or not now - PHONE_MS < int(issued) * 1000 <= now + 60_000:
            continue
        if hmac.compare_digest(mac, _mac(keys, member_id, changed_at, int(issued), nonce)):
            return nonce
    return None


def with_token(cookie: str | None, token: str) -> str:
    """The phone cookie with this member's new token in place of their old one (four at most)."""
    owner = token.split(".", 1)[0]
    kept = [t for t in tokens_of(cookie) if t.split(".", 1)[0] != owner]
    return "~".join([token, *kept][: LIMITS["max_members"]])
