"""Web Push: message encryption, VAPID and a sender, on ``cryptography`` and ``httpx`` alone (P6 F9).

A push message to one phone (RFC 8030, RFC 8291, RFC 8292):

- The payload is encrypted for that phone with the ``aes128gcm`` content coding (RFC 8188) under
  RFC 8291's key schedule: a fresh P-256 key pair and salt for every message, ECDH with the
  phone's ``p256dh`` key, HKDF-SHA-256 with its 16-byte ``auth`` secret, and one record of 4096
  bytes, so a payload holds at most 3993 bytes (refused in words before anything is sent).
- The request carries a VAPID token: an ES256 JWT for the push service's origin, signed with this
  service's own P-256 key (``/etc/sudarshan-family/vapid_private.pem``), and that key's public
  half, the ``applicationServerKey`` the phone subscribed with.
- It is posted to the subscription's endpoint, which must be https on port 443 at one of the push
  services the browsers use (``PUSH_SERVICES``). An endpoint is data from a family member's phone;
  the check, made when a ``Subscription`` is built, is what stops it becoming a request to
  anywhere else.

Nothing here logs an endpoint, a key, a token or a payload (RFC 8030 section 8.5: endpoints in a
log tie a person's devices together); a test reads the names every log call passes. A send is
never retried here: the family service's outbox (F10) decides what each ``PushResult`` means.
"""

from __future__ import annotations

import asyncio
import base64
import enum
import hmac
import ipaddress
import json
import logging
import math
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import NamedTuple

import httpx
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger("sudarshan.push")

#: RFC 8291 section 4: one record of 4096 bytes. Its 86-byte header (salt 16, record size 4, key
#: id length 1, the sender's public key 65), the padding delimiter and the 16-byte tag leave 3993.
RECORD_SIZE = 4096
MAX_PAYLOAD = 3993
KEY_INFO = b"WebPush: info\x00"
CEK_INFO = b"Content-Encoding: aes128gcm\x00"
NONCE_INFO = b"Content-Encoding: nonce\x00"
_X962 = (serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)

VERY_LOW, LOW, NORMAL, HIGH = "very-low", "low", "normal", "high"  # RFC 8030 section 5.3
URGENCIES = (VERY_LOW, LOW, NORMAL, HIGH)
MAX_TTL_S = 28 * 24 * 3600  # Google's notification service keeps a message 4 weeks at most
#: Every phase of a send has its own limit, and the whole send its deadline.
TIMEOUT = httpx.Timeout(10.0, connect=5.0, pool=5.0)
DEADLINE_S = 20.0
MAX_RETRY_AFTER_S = 24 * 3600

#: The VAPID contact when the owner sets none: a placeholder that names nobody (RFC 2606).
DEFAULT_SUBJECT = "mailto:push@example.invalid"
TOKEN_LIFETIME_S = 12 * 3600  # RFC 8292: never more than 24 h ahead
MAX_TOKEN_LIFETIME_S = 24 * 3600
RENEW_BEFORE_S = 3600  # Apple asks for a new token at most once an hour
MAX_AUDIENCES = 32
_JWT_HEADER = b'{"typ":"JWT","alg":"ES256"}'

MAX_ENDPOINT = 2048
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_HOST = re.compile(rf"{_LABEL.pattern}(?:\.{_LABEL.pattern})+")
_PATH = re.compile(r"/[A-Za-z0-9\-._~:/?!$&'()*+,;=%]+")
_ORIGIN = re.compile(rf"https://{_HOST.pattern}")
_B64URL = re.compile(r"[A-Za-z0-9_-]*={0,2}")
_TOPIC = re.compile(r"[A-Za-z0-9_-]{1,32}")  # RFC 8030 section 5.4
_SUBJECT = re.compile(
    r"mailto:[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
    r"|https://[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?:/[A-Za-z0-9._~%/-]*)?"
)


class PushError(ValueError):
    """A push that cannot be made, in words that never hold an endpoint, a key or the payload."""


class PayloadTooLarge(PushError):
    """The payload does not fit one push message."""


class InvalidSubscription(PushError):
    """A phone's subscription that cannot be used: its address or one of its keys."""


class EndpointRefused(InvalidSubscription):
    """The address fails the endpoint check, so nothing is ever sent to it."""


class VapidKeyError(PushError):
    """This service's own VAPID key cannot be used."""


@dataclass(frozen=True)
class PushService:
    """A push service a browser hands out endpoints at. ``wildcard``: one DNS label in front of
    ``host`` (wns2-bl2p.notify.windows.com). ``slow_down``: the answers that mean "send less"."""

    name: str
    host: str
    wildcard: bool = False
    slow_down: frozenset[int] = frozenset({429})

    def serves(self, host: str) -> bool:
        if not self.wildcard:
            return host == self.host
        label, dot, parent = host.partition(".")
        return bool(dot) and parent == self.host and _LABEL.fullmatch(label) is not None


#: Which and why: README.md, "The endpoint check".
PUSH_SERVICES = (
    PushService("Google", "fcm.googleapis.com"),  # Chrome, Android and computers
    PushService("Mozilla", "updates.push.services.mozilla.com"),  # Firefox
    PushService("Apple", "push.apple.com", wildcard=True),  # Safari, iOS home-screen apps
    PushService("Microsoft", "notify.windows.com", wildcard=True, slow_down=frozenset({406, 429})),
)


class Endpoint(NamedTuple):
    """A checked endpoint: ``origin`` is the VAPID audience (``https://`` and the host)."""

    origin: str
    host: str
    service: PushService


def _refused(why: str) -> EndpointRefused:
    return EndpointRefused(f"The notification address {why}, so nothing is sent to it.")


def check_endpoint(endpoint: object) -> Endpoint:
    """The endpoint's push service, or ``EndpointRefused``: https only, port 443 only, no IP
    literal, no user name, no fragment, a lowercase host name served by ``PUSH_SERVICES``, read the
    same way by httpx, which sends it."""
    if not isinstance(endpoint, str) or not endpoint:
        raise EndpointRefused("The phone sent no notification address.")
    if len(endpoint) > MAX_ENDPOINT:
        raise _refused("is longer than any notification service makes")
    if not endpoint.isascii() or any(c <= " " or c in "\x7f\\" for c in endpoint):
        raise _refused("holds spaces or characters no notification service uses")
    if not endpoint.startswith("https://"):
        raise _refused("is not a secure (https) address")
    if "#" in endpoint:
        raise _refused("carries a part after '#', which no notification service uses")
    rest = endpoint[len("https://") :]
    cut = min([i for i in (rest.find("/"), rest.find("?")) if i >= 0] or [len(rest)])
    authority, path = rest[:cut], rest[cut:]
    if "@" in authority:
        raise _refused("carries a user name, which no notification service uses")
    host, colon, port = authority.partition(":")
    if authority.startswith("[") or _is_ip(host):
        raise _refused("is a numeric address, not a notification service's name")
    if colon and port != "443":
        raise _refused("names a port other than the standard secure one")
    if not _HOST.fullmatch(host):
        raise _refused("does not name a host the way notification services do")
    if not _PATH.fullmatch(path):
        raise _refused("has no path a notification service makes")
    service = next((s for s in PUSH_SERVICES if s.serves(host)), None)
    if service is None:
        known = ", ".join(s.name for s in PUSH_SERVICES)
        raise _refused(f"is not at one of the known notification services ({known})")
    try:
        seen = httpx.URL(endpoint)  # as the sender will read it
    except httpx.InvalidURL:
        raise _refused("is not read the same way by the sender") from None
    if (seen.scheme, seen.host, seen.port, seen.userinfo) != ("https", host, None, b""):
        raise _refused("is not read the same way by the sender")
    return Endpoint(f"https://{host}", host, service)


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return host.rpartition(".")[2].isdigit()  # 127.1 or 2130706433: numeric to a resolver


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _raw(value: object) -> bytes | None:
    """Bytes as given, or urlsafe base64 text (padded or not) decoded; None for anything else."""
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value)
    if not isinstance(value, str) or not _B64URL.fullmatch(value):
        return None
    bare = value.rstrip("=")
    if len(bare) % 4 == 1:
        return None
    return base64.urlsafe_b64decode(bare + "=" * (-len(bare) % 4))


def _phone_key(value: object) -> bytes:
    raw = _raw(value)
    try:
        if raw is not None and len(raw) == 65 and raw[0] == 4:
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)  # on the curve
            return raw
    except ValueError:
        pass
    raise InvalidSubscription(
        "This phone's notification key is not usable. Turn notifications off and on again on the "
        "phone."
    )


def _phone_secret(value: object) -> bytes:
    raw = _raw(value)
    if raw is None or len(raw) != 16:
        raise InvalidSubscription(
            "This phone's notification secret is not usable. Turn notifications off and on again "
            "on the phone."
        )
    return raw


@dataclass(frozen=True, repr=False)
class Subscription:
    """A phone's push subscription, checked whole when it is built: the endpoint (``target`` is
    its check), the phone's P-256 key ``p256dh`` (65 bytes) and its ``auth`` secret (16 bytes).
    The keys may be given as bytes or as the urlsafe base64 text the browser sends. Its repr names
    the push service only, so a subscription in a log or a traceback gives nothing away."""

    endpoint: str
    p256dh: bytes
    auth: bytes
    target: Endpoint = field(init=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "target", check_endpoint(self.endpoint))
        object.__setattr__(self, "p256dh", _phone_key(self.p256dh))
        object.__setattr__(self, "auth", _phone_secret(self.auth))

    @classmethod
    def from_json(cls, data: object) -> Subscription:
        """What the browser's ``PushSubscription.toJSON()`` gives:
        ``{"endpoint": ..., "keys": {"p256dh": ..., "auth": ...}}``."""
        keys = data.get("keys") if isinstance(data, Mapping) else None
        if not isinstance(keys, Mapping):
            raise InvalidSubscription("The phone did not send a notification subscription.")
        return cls(data.get("endpoint"), keys.get("p256dh"), keys.get("auth"))

    def __repr__(self) -> str:
        return f"Subscription(service={self.target.service.name!r})"


class KeySchedule(NamedTuple):
    """RFC 8291 section 3.4's values, named as the RFC names them (its Appendix A pins each)."""

    ecdh_secret: bytes
    as_public: bytes
    prk_key: bytes
    key_info: bytes
    ikm: bytes
    prk: bytes
    cek: bytes
    nonce: bytes


def _hmac(key: bytes, message: bytes) -> bytes:
    return hmac.digest(key, message, "sha256")


def _p256(key: object) -> bool:
    return isinstance(key, ec.EllipticCurvePrivateKey) and isinstance(key.curve, ec.SECP256R1)


def key_schedule(
    sender_key: ec.EllipticCurvePrivateKey, ua_public: bytes, auth_secret: bytes, salt: bytes
) -> KeySchedule:
    """The application server's side of RFC 8291 section 3.4, step by step as the RFC writes it:
    HKDF is spelt out in HMAC-SHA-256 steps, so every intermediate value can meet the RFC's."""
    as_public = sender_key.public_key().public_bytes(*_X962)
    phone = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ecdh_secret = sender_key.exchange(ec.ECDH(), phone)
    prk_key = _hmac(auth_secret, ecdh_secret)
    key_info = KEY_INFO + ua_public + as_public
    ikm = _hmac(prk_key, key_info + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, CEK_INFO + b"\x01")[:16]
    nonce = _hmac(prk, NONCE_INFO + b"\x01")[:12]
    return KeySchedule(ecdh_secret, as_public, prk_key, key_info, ikm, prk, cek, nonce)


@dataclass(frozen=True)
class Encrypted:
    """An encrypted push message: the body to post and the headers that describe it."""

    body: bytes = field(repr=False)
    headers: dict[str, str] = field(
        default_factory=lambda: {
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
        }
    )


def encrypt(
    payload: bytes | str,
    p256dh: bytes | str,
    auth: bytes | str,
    *,
    sender_key: ec.EllipticCurvePrivateKey | None = None,
    salt: bytes | None = None,
) -> Encrypted:
    """``payload`` (text is sent as UTF-8) encrypted for the phone whose subscription keys these
    are, in one ``aes128gcm`` record. A fresh sender key pair and salt are made for every call
    unless given (the RFC's vectors give both). More than 3993 bytes: ``PayloadTooLarge``."""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    elif not isinstance(payload, bytes | bytearray | memoryview):
        raise TypeError("A push payload is bytes or text.")
    data = bytes(payload)
    if len(data) > MAX_PAYLOAD:
        raise PayloadTooLarge(
            f"The notification is {_grouped(len(data))} bytes and one push message carries at "
            f"most {_grouped(MAX_PAYLOAD)}, so it was not sent."
        )
    ua_public, auth_secret = _phone_key(p256dh), _phone_secret(auth)
    if sender_key is None:
        sender_key = ec.generate_private_key(ec.SECP256R1())
    elif not _p256(sender_key):
        raise ValueError("The sender key of a push message is a P-256 private key.")
    salt = os.urandom(16) if salt is None else bytes(salt)
    if len(salt) != 16:
        raise ValueError("The salt of a push message is 16 bytes.")
    keys = key_schedule(sender_key, ua_public, auth_secret, salt)
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(keys.as_public)]) + keys.as_public
    return Encrypted(header + AESGCM(keys.cek).encrypt(keys.nonce, data + b"\x02", None))


def check_subject(subject: object) -> str:
    """The VAPID ``sub`` contact: a ``mailto:`` or an ``https:`` address, or ``ValueError``."""
    if not isinstance(subject, str) or len(subject) > 200 or not _SUBJECT.fullmatch(subject):
        raise ValueError(
            "The contact given to the notification services must be a mailto: or an https: "
            f"address, for example {DEFAULT_SUBJECT}."
        )
    return subject


class Vapid:
    """This service's VAPID key pair (RFC 8292) and its tokens, one per push service's origin,
    each kept until less than an hour of it is left. ``public_key`` is the uncompressed point as
    urlsafe base64 without padding: the 87 characters the page subscribes with."""

    def __init__(
        self,
        private_key: ec.EllipticCurvePrivateKey,
        subject: str = DEFAULT_SUBJECT,
        *,
        lifetime_s: int = TOKEN_LIFETIME_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not _p256(private_key):
            raise VapidKeyError("The notification signing key is not a P-256 private key.")
        if not RENEW_BEFORE_S < lifetime_s <= MAX_TOKEN_LIFETIME_S:
            raise ValueError("A notification token lasts more than an hour and 24 hours at most.")
        self._key, self._lifetime, self._clock = private_key, int(lifetime_s), clock
        self.subject = check_subject(subject)
        self.public_key = _b64url(private_key.public_key().public_bytes(*_X962))
        self._tokens: dict[str, tuple[str, int]] = {}

    @classmethod
    def from_files(
        cls,
        private_path: str | os.PathLike,
        public_path: str | os.PathLike | None = None,
        subject: str = DEFAULT_SUBJECT,
        **options,
    ) -> Vapid:
        """The pair the deploy's provision step makes: the private key in PEM (SEC1, as ``openssl
        ecparam -genkey -noout`` writes it; PKCS 8 too) and, when given, the public key file,
        which must belong to it."""
        private_path = Path(private_path)
        try:
            key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
        except OSError as exc:
            why = exc.strerror or "it cannot be opened"
            raise VapidKeyError(f"The notification signing key {private_path}: {why}.") from None
        except (ValueError, TypeError, UnsupportedAlgorithm):
            raise VapidKeyError(
                f"{private_path} does not hold an unprotected private key in PEM form."
            ) from None
        if not _p256(key):
            raise VapidKeyError(
                f"{private_path} holds a key that is not on the P-256 curve, the only one "
                "notification services take."
            )
        vapid = cls(key, subject, **options)
        if public_path is not None:
            public_path = Path(public_path)
            try:
                written = public_path.read_text(encoding="ascii").strip()
            except (OSError, UnicodeDecodeError):
                raise VapidKeyError(
                    f"The public notification key {public_path} cannot be read."
                ) from None
            if written != vapid.public_key:
                raise VapidKeyError(
                    f"{public_path} does not belong to {private_path}, so every notification would "
                    f"be refused. Remove {public_path} as root; the next deploy run makes it again "
                    "from the private key."
                )
        return vapid

    def token(self, audience: str) -> str:
        """The ES256 JWT for one push service's origin (``https://`` and its host)."""
        if not isinstance(audience, str) or not _ORIGIN.fullmatch(audience):
            raise ValueError("A notification token is made for a push service's https origin.")
        now = int(self._clock())
        held = self._tokens.get(audience)
        if held is not None and held[1] - now > RENEW_BEFORE_S:
            return held[0]
        expires = now + self._lifetime
        claims = json.dumps(
            {"aud": audience, "exp": expires, "sub": self.subject}, separators=(",", ":")
        )
        signing_input = f"{_b64url(_JWT_HEADER)}.{_b64url(claims.encode('ascii'))}"
        der = self._key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)  # JOSE wants the two numbers side by side, not DER
        signed = f"{signing_input}.{_b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
        if audience not in self._tokens and len(self._tokens) >= MAX_AUDIENCES:
            del self._tokens[min(self._tokens, key=lambda origin: self._tokens[origin][1])]
        self._tokens[audience] = (signed, expires)
        return signed

    def authorization(self, audience: str) -> str:
        """The ``Authorization`` header's value (RFC 8292 section 3)."""
        return f"vapid t={self.token(audience)}, k={self.public_key}"

    def __repr__(self) -> str:
        return f"Vapid(public_key={self.public_key[:8]}..., tokens={len(self._tokens)})"


class Outcome(enum.StrEnum):
    """What became of one push, and what the caller does about it (README.md)."""

    SENT = "sent"  # any 2xx (201, 202; Microsoft's service answers 200)
    GONE = "gone"  # 404, 410: delete the subscription
    TOO_LARGE = "too_large"  # 413, or refused here before sending: drop the message
    SLOW_DOWN = "slow_down"  # 429 (and 406 from Microsoft): wait, retry_after when given
    FAILING = "failing"  # 5xx, a timeout, no connection: try later
    REFUSED = "refused"  # any other answer: the message is not taken


@dataclass(frozen=True)
class PushResult:
    """One push's outcome. ``reason`` is a sentence; ``status`` the push service's answer (None:
    there was none); ``retry_after`` the seconds it asked for, at most a day."""

    outcome: Outcome
    reason: str
    service: str
    status: int | None = None
    retry_after: int | None = None


def parse_retry_after(value: str | None, now: float) -> int | None:
    """Seconds from a ``Retry-After`` header (seconds or an HTTP date), at most a day; None when
    it is absent or unreadable."""
    value = (value or "").strip()
    if re.fullmatch(r"[0-9]{1,12}", value):
        seconds = int(value)
    else:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = math.ceil(when.timestamp() - now)
    return max(0, min(seconds, MAX_RETRY_AFTER_S))


def judge_answer(
    status: int, retry_after: str | None, service: PushService, now: float
) -> PushResult:
    """The push service's answer as a ``PushResult``."""
    company = service.name
    if 200 <= status < 300:
        why = f"{company}'s notification service took the message."
        return PushResult(Outcome.SENT, why, company, status)
    if status in (404, 410):
        why = (
            f"{company} says this phone's notifications have ended: they were turned off on the "
            "phone or expired."
        )
        return PushResult(Outcome.GONE, why, company, status)
    if status == 413:
        why = f"{company}'s notification service refused the message as too large, so it was not sent."
        return PushResult(Outcome.TOO_LARGE, why, company, status)
    wait = parse_retry_after(retry_after, now)
    when = "later" if wait is None else "now" if wait == 0 else f"in {_grouped(wait)} seconds"
    if status in service.slow_down:
        why = f"{company}'s notification service asked for fewer messages; send again {when}."
        return PushResult(Outcome.SLOW_DOWN, why, company, status, wait)
    if 500 <= status < 600:
        why = f"{company}'s notification service is having trouble; try again {when}."
        return PushResult(Outcome.FAILING, why, company, status, wait)
    if status in (401, 403):
        why = f"{company}'s notification service did not accept this server's signature."
        return PushResult(Outcome.REFUSED, why, company, status)
    why = f"{company}'s notification service refused the message."
    return PushResult(Outcome.REFUSED, why, company, status)


class PushSender:
    """Sends one push at a time through an ``httpx.AsyncClient`` the caller owns and closes.

    The request is built here and sent as it is: none of the client's default headers, cookies,
    authentication or base URL reach a push service, and a redirect is never followed. Every phase
    has its limit (``timeout``) and the whole send ``deadline_s``. One request per ``send``, never
    a second: what happens next is the outbox's decision. ``stats`` counts the outcomes.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        vapid: Vapid,
        *,
        timeout: httpx.Timeout = TIMEOUT,
        deadline_s: float = DEADLINE_S,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self.client = client
        self.vapid = vapid
        self.timeout = timeout
        self.deadline_s = deadline_s
        self.wall = wall
        self.stats: dict[Outcome, int] = dict.fromkeys(Outcome, 0)

    async def send(
        self,
        subscription: Subscription,
        payload: bytes | str,
        *,
        ttl: int,
        urgency: str = NORMAL,
        topic: str | None = None,
    ) -> PushResult:
        """Encrypt ``payload`` for the phone and post it once. ``ttl`` is how long, in seconds,
        the push service may hold it for a phone that is away; ``topic`` (32 characters of
        urlsafe base64 at most) replaces a waiting message with the same topic."""
        if not isinstance(subscription, Subscription):
            raise TypeError("A push is sent to a Subscription.")
        if not isinstance(ttl, int) or isinstance(ttl, bool) or not 0 <= ttl <= MAX_TTL_S:
            raise ValueError(f"A push's time to live is 0 to {_grouped(MAX_TTL_S)} seconds.")
        if urgency not in URGENCIES:
            raise ValueError(f"A push's urgency is one of {', '.join(URGENCIES)}.")
        if topic is not None and not (isinstance(topic, str) and _TOPIC.fullmatch(topic)):
            raise ValueError("A push's topic is 1 to 32 letters, digits, '-' or '_'.")
        target = subscription.target
        company = target.service.name
        try:
            sealed = encrypt(payload, subscription.p256dh, subscription.auth)
        except PayloadTooLarge as exc:
            return self._done(PushResult(Outcome.TOO_LARGE, str(exc), company))
        headers = {
            **sealed.headers,
            "TTL": str(ttl),
            "Urgency": urgency,
            "Authorization": self.vapid.authorization(target.origin),
        }
        if topic is not None:
            headers["Topic"] = topic
        request = httpx.Request(
            "POST",
            subscription.endpoint,
            content=sealed.body,
            headers=headers,
            extensions={"timeout": self.timeout.as_dict()},
        )
        url = request.url
        if (url.scheme, url.host, url.port, url.userinfo) != ("https", target.host, None, b""):
            raise _refused("is not read the same way by the sender")
        try:
            async with asyncio.timeout(self.deadline_s):
                answer = await self.client.send(request, auth=None, follow_redirects=False)
        except (httpx.TimeoutException, TimeoutError):
            why = f"{company}'s notification service did not answer in time; try again later."
            return self._done(PushResult(Outcome.FAILING, why, company))
        except (httpx.HTTPError, OSError) as exc:  # its text can hold the address: only its kind
            log.debug("A push to %s's service failed with %s", company, type(exc))
            why = f"{company}'s notification service could not be reached; try again later."
            return self._done(PushResult(Outcome.FAILING, why, company))
        verdict = judge_answer(
            answer.status_code, answer.headers.get("Retry-After"), target.service, self.wall()
        )
        return self._done(verdict)

    def _done(self, result: PushResult) -> PushResult:
        self.stats[result.outcome] += 1
        log.debug(
            "Push to %s's service: %s (answer %s)",
            result.service,
            result.outcome.value,
            result.status,
        )
        return result


def _grouped(number: int) -> str:
    """Indian digit grouping, as the app writes numbers: 3,993; 1,00,000; 24,19,200."""
    head, tail = str(number)[:-3], str(number)[-3:]
    groups = []
    while len(head) > 2:
        head, groups = head[:-2], [head[-2:], *groups]
    return ",".join([part for part in (head, *groups) if part] + [tail])
