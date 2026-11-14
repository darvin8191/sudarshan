"""Web Push for the family view (M10.6; design 2.6; cross-design Y26, Y44).

The family's own VAPID pair (``/etc/sudarshan-family/vapid_private.pem`` and ``vapid_public.txt``,
made once by the deploy's provision step; Y26: no other service holds it) signs every push, through
``sudarshan.push`` (encryption, VAPID, the endpoint check, one request per send).

**Subscriptions**, one per phone (session), from the viewer API:

    GET  /family/api/push/key                  the public key the phone subscribes with
    POST /family/api/push/subscriptions        {endpoint, keys {p256dh, auth}, replaces?}: the
                                               endpoint checked whole (``Subscription.from_json``:
                                               a known push service, https, no IP, no user name)
                                               before anything is stored; ``replaces`` removes the
                                               member's own old one; any other the same phone held
                                               is gone, since a phone holds one
    POST /family/api/push/subscriptions/remove {endpoint}: the member's own only

An endpoint and its keys are never logged and never leave the store but to their push service.

**Sending** (``to_member``, for the outbox): to each phone of the member whose session still signs
them in (the others are forgotten: a phone signed out gets nothing more), with the time left to
live as ``TTL``. A 2xx is sent, and the phone's failures start again at 0; 404 or 410 forgets the
subscription; 413 drops the message for that phone; 429, a 5xx, no answer and any other refusal
count a failure and hold that phone back 10 minutes after the first failure in a row and an hour
after each next one (or as long as the push service asked). Three failures in a row mark the phone
"Notifications may not arrive on this phone" in its device list and tell the owner once
(``push_failing``). The client reaches only the push services' hosts (``PushOnly``), never follows a
redirect and never reads a proxy from the environment.
"""

from __future__ import annotations

import base64
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from sudarshan.push import (
    PUSH_SERVICES,
    InvalidSubscription,
    Outcome,
    PushResult,
    PushSender,
    Subscription,
    Vapid,
    VapidKeyError,
)

from . import audit
from .bot_words import PAGE, Refused
from .errors import Invalid
from .listener import Response

log = logging.getLogger("sudarshan.family")

VAPID_PRIVATE = "vapid_private.pem"
VAPID_PUBLIC = "vapid_public.txt"
#: Failures in a row that mark a phone (design 2.6).
FAILING_AFTER = 3
FIRST_BACKOFF_MS = 10 * 60_000
NEXT_BACKOFF_MS = 60 * 60_000
#: How long a test may wait at the push service for a phone that is away.
TEST_TTL_S = 60
REQUEST_ID_MAX = 64
ENDPOINT_MAX = 2048
RETRY = (Outcome.SLOW_DOWN, Outcome.FAILING)
COUNTED = (Outcome.SLOW_DOWN, Outcome.FAILING, Outcome.REFUSED)


class HideEndpoints(logging.Filter):
    """httpx logs each request's address at INFO, and a push endpoint is a secret (RFC 8030
    section 8.5: whoever holds it can push to the phone): this filter, on httpx's own logger,
    writes ``/<subscription>`` in place of a push service's path, whatever level anyone sets."""

    PATTERN = re.compile(r"https://([a-z0-9.-]+)(?::443)?/[^\s\"']*")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            text = record.getMessage()
        except Exception:  # a record that cannot be formatted is not ours to judge
            return True

        def hide(found: re.Match) -> str:
            host = found.group(1)
            if any(service.serves(host) for service in PUSH_SERVICES):
                return f"https://{host}/<subscription>"
            return found.group(0)

        hidden = self.PATTERN.sub(hide, text)
        if hidden != text:
            record.msg, record.args = hidden, ()
        return True


def hide_endpoints() -> None:
    """Put ``HideEndpoints`` on httpx's logger once (the import of this module does it)."""
    target = logging.getLogger("httpx")
    if not any(isinstance(f, HideEndpoints) for f in target.filters):
        target.addFilter(HideEndpoints())


hide_endpoints()


class PushOnly(httpx.AsyncBaseTransport):
    """A transport that sends only to a push service's host, over https on 443, with no user name
    in the address: the family's push client cannot reach anything else."""

    def __init__(self, inner: httpx.AsyncBaseTransport | None = None):
        self.inner = inner if inner is not None else httpx.AsyncHTTPTransport(retries=0)

    @staticmethod
    def permits(url: httpx.URL) -> bool:
        return (url.scheme == "https" and url.port in (None, 443) and not url.userinfo
                and any(service.serves(url.host) for service in PUSH_SERVICES))  # fmt: skip

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not self.permits(request.url):
            raise HostRefused("the family's push client may not reach that address")
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()


class HostRefused(Exception):
    """A push to a place that is not a push service: raised before anything is sent."""


def make_http(inner: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=PushOnly(inner), trust_env=False, follow_redirects=False)


def load_vapid(etc_dir: Path, subject: str) -> Vapid | None:
    """The family's VAPID pair; None (said once in words) when the provision step has not made
    it or it cannot be used: notifications are then off and the pages say so."""
    try:
        options = {"subject": subject} if subject else {}
        return Vapid.from_files(etc_dir / VAPID_PRIVATE, etc_dir / VAPID_PUBLIC, **options)
    except (VapidKeyError, ValueError) as exc:
        log.warning("family notifications are off: %s Run the deploy's family provision step.", exc)
        return None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class Delivery:
    """What became of one message to a member's phones: ``sent`` (to ``sent`` of ``phones``),
    ``none`` (no phone has notifications on), ``later`` (try at ``retry_at``) or ``refused``."""

    state: str
    sent: int = 0
    phones: int = 0
    retry_at: int | None = None


def _live(tx, session_hash: str | None, now: int) -> bool:
    row = tx.row("viewer_session", id_hash=session_hash) if session_hash else None
    return bool(row and row["revoked_at"] is None and row["absolute_expires_at"] > now
                and row["idle_expires_at"] > now)  # fmt: skip


class Push:
    """The family's push channel: its routes and its sends. ``sender`` exists while the channels'
    job runs (it owns the HTTP client)."""

    def __init__(self, service: Any, vapid: Vapid | None):
        self.service, self.vapid = service, vapid
        self.clock: Callable[[], int] = service.clock
        self.sender: PushSender | None = None
        self.counts = {"subscribed": 0, "removed": 0, "forgotten": 0, "sent": 0, "failed": 0,
                       "marked": 0}  # fmt: skip

    def start(self, http: httpx.AsyncClient) -> None:
        self.sender = PushSender(http, self.vapid) if self.vapid is not None else None

    def stop(self) -> None:
        self.sender = None

    def health(self) -> dict[str, Any]:
        """Counts only: no endpoint, key or member."""
        stats = {k.value: v for k, v in self.sender.stats.items()} if self.sender else {}
        return {"keys": self.vapid is not None, **self.counts, "outcomes": stats}

    # ---- the viewer routes -------------------------------------------------------------------
    async def route_push_key(self, request: Any, args: dict, session: Any) -> Response:
        if self.vapid is None:
            raise Refused("not_ready", PAGE["push_not_set_up"])
        return Response.ok({"key": self.vapid.public_key})

    async def route_push_subscribe(self, request: Any, args: dict, session: Any) -> Response:
        try:
            checked = Subscription.from_json({"endpoint": args.get("endpoint"),
                                              "keys": args.get("keys")})  # fmt: skip
        except InvalidSubscription as exc:
            raise Refused("invalid", str(exc), field="endpoint") from None
        replaces, request_id = args.get("replaces"), args.get("request_id")
        if replaces is not None and not (
            isinstance(replaces, str) and len(replaces) <= ENDPOINT_MAX
        ):
            raise Invalid(field="replaces")
        if request_id is not None and not (isinstance(request_id, str)
                                           and 1 <= len(request_id) <= REQUEST_ID_MAX):  # fmt: skip
            raise Invalid(field="request_id")
        member_id, now = session.member_id, self.clock()
        values = {"member_id": member_id, "session_hash": session.id_hash,
                  "p256dh": _b64(checked.p256dh), "auth": _b64(checked.auth),
                  "ua_summary": session.row.get("ua_summary"), "request_id": request_id}  # fmt: skip

        def put(tx) -> bool:
            if replaces and replaces != checked.endpoint:
                tx.delete("push_subscription", endpoint=replaces, member_id=member_id)
            found = tx.row("push_subscription", endpoint=checked.endpoint)
            if found is not None and found["member_id"] != member_id:
                tx.delete("push_subscription", id=found["id"])  # this phone now signs in another
                found = None
            if found is None:
                tx.insert("push_subscription", {**values, "endpoint": checked.endpoint,
                                                "created_at": now})  # fmt: skip
            else:
                tx.update("push_subscription", {"id": found["id"]}, values)
            for other in tx.rows("push_subscription", session_hash=session.id_hash):
                if other["endpoint"] != checked.endpoint:  # a phone holds one subscription
                    tx.delete("push_subscription", id=other["id"])
            if found is None:
                audit.record(tx, at=now, actor=audit.member_actor(member_id), kind="push_subscribed",
                             member_id=member_id, session=session.id_hash)  # fmt: skip
            return found is None

        if await self.service.store.write(put):
            self.counts["subscribed"] += 1
        return Response.ok({"ok": True, "words": None})

    async def route_push_unsubscribe(self, request: Any, args: dict, session: Any) -> Response:
        endpoint = args.get("endpoint")
        if not isinstance(endpoint, str) or not 1 <= len(endpoint) <= ENDPOINT_MAX:
            raise Invalid(field="endpoint")
        member_id, now = session.member_id, self.clock()

        def drop(tx) -> bool:
            if not tx.delete("push_subscription", endpoint=endpoint, member_id=member_id):
                return False
            audit.record(tx, at=now, actor=audit.member_actor(member_id), kind="push_removed",
                         member_id=member_id, session=session.id_hash)  # fmt: skip
            return True

        if await self.service.store.write(drop):
            self.counts["removed"] += 1
        return Response.ok({"ok": True, "words": None})

    # ---- sending -----------------------------------------------------------------------------
    async def to_member(self, member_id: int, body: bytes, *, expiry: int, urgency: str,
                        topic: str | None) -> Delivery:  # fmt: skip
        """One message to every phone of the member that may get it now."""
        now = self.clock()

        def read(tx) -> tuple[list[dict], list[dict]]:
            rows = tx.rows("push_subscription", "id", member_id=member_id)
            return [r for r in rows if _live(tx, r["session_hash"], now)], [
                r for r in rows if not _live(tx, r["session_hash"], now)]  # fmt: skip

        live, ended = await self.service.store.read(read)
        if ended:
            await self._forget([r["id"] for r in ended])
        if not live:
            return Delivery("none")
        ready = [r for r in live if not (r["backoff_until"] and r["backoff_until"] > now)]
        retry = [r["backoff_until"] for r in live if r not in ready]
        if self.sender is None:
            return Delivery("later", phones=len(live), retry_at=now + FIRST_BACKOFF_MS)
        sent = 0
        for row in ready:
            ttl = max(0, (expiry - now) // 1000)
            result = await self._send(row, body, ttl=ttl, urgency=urgency, topic=topic)
            if result is None:
                continue
            if result.outcome == Outcome.SENT:
                sent += 1
            elif result.outcome in RETRY:
                retry.append(await self._backoff_of(row["id"]))
        if sent:
            return Delivery("sent", sent=sent, phones=len(live))
        found = [at for at in retry if at]
        if found:
            return Delivery("later", phones=len(live), retry_at=min(found))
        return Delivery("refused", phones=len(live))

    async def to_session(self, member_id: int, session_hash: str, body: bytes) -> PushResult | str:
        """A test to the phone asking: its result, or ``off`` (no subscription on this phone)
        or ``no_server`` (notifications are not set up here)."""
        if self.sender is None:
            return "no_server"

        def read(tx) -> dict | None:
            return tx.row("push_subscription", session_hash=session_hash, member_id=member_id)

        row = await self.service.store.read(read)
        if row is None:
            return "off"
        result = await self._send(row, body, ttl=TEST_TTL_S, urgency="normal", topic="test")
        return "off" if result is None else result

    async def _send(self, row: dict, body: bytes, **how: Any) -> PushResult | None:
        """One push to one phone, and what it means for that phone; None when the stored
        subscription no longer passes the check (it is forgotten)."""
        try:
            subscription = Subscription(row["endpoint"], row["p256dh"], row["auth"])
        except InvalidSubscription:
            await self._forget([row["id"]])
            return None
        sender = self.sender
        if sender is None:  # the channels' job is stopping
            return None
        result = await sender.send(subscription, body, **how)
        self.counts["sent" if result.outcome == Outcome.SENT else "failed"] += 1
        await self._settle(row["id"], result)
        return result

    async def _settle(self, sub_id: int, result: PushResult) -> None:
        now = self.clock()

        def settle(tx) -> tuple[dict, dict | None] | None:
            row = tx.row("push_subscription", id=sub_id)
            if row is None:
                return None
            if result.outcome == Outcome.SENT:
                tx.update("push_subscription", {"id": sub_id},
                          {"failures": 0, "backoff_until": None, "last_ok_at": now})  # fmt: skip
            elif result.outcome == Outcome.GONE:
                tx.delete("push_subscription", id=sub_id)
                audit.record(tx, at=now, actor=audit.SYSTEM, kind="push_removed",
                             member_id=row["member_id"], session=row["session_hash"],
                             detail={"why": "ended"})  # fmt: skip
            elif result.outcome in COUNTED:
                failures = int(row["failures"]) + 1
                wait = (result.retry_after * 1000 if result.retry_after
                        else FIRST_BACKOFF_MS if failures == 1 else NEXT_BACKOFF_MS)  # fmt: skip
                tx.update("push_subscription", {"id": sub_id},
                          {"failures": failures, "backoff_until": now + wait})  # fmt: skip
                if failures == FAILING_AFTER:
                    session = tx.row("viewer_session", id_hash=row["session_hash"])
                    return tx.member(row["member_id"]) or {}, session
            return None

        marked = await self.service.store.write(settle)
        if marked is not None:
            member, session = marked
            self.counts["marked"] += 1
            label = (session or {}).get("device_label") or "a phone"
            self.service.notices("push_failing", member, device=label, attempts=str(FAILING_AFTER))

    async def _backoff_of(self, sub_id: int) -> int | None:
        row = await self.service.store.read(lambda tx: tx.row("push_subscription", id=sub_id))
        return row["backoff_until"] if row else None

    async def forget_ended(self) -> int:
        """Forget every subscription whose phone no longer signs its member in (the outbox's
        hourly tidy: a member who is sent nothing keeps no signed-out phone)."""
        now = self.clock()

        def ended(tx) -> list[int]:
            return [r["id"] for r in tx.rows("push_subscription")
                    if not _live(tx, r["session_hash"], now)]  # fmt: skip

        found = await self.service.store.read(ended)
        if found:
            await self._forget(found)
        return len(found)

    async def _forget(self, ids: list[int]) -> None:
        def drop(tx) -> int:
            return sum(tx.delete("push_subscription", id=sub_id) for sub_id in ids)

        self.counts["forgotten"] += await self.service.store.write(drop)
