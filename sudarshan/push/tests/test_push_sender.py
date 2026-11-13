"""F9: the sender. A fake push service behind httpx's mock transport (no network, no socket): the
request RFC 8030 asks for, nothing of the caller's client in it, each answer mapped to its outcome
once and never retried, and every limit explicit."""

from __future__ import annotations

import asyncio
import email.utils
import os
import re
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from push_testkit import phone_reads, point

from sudarshan.push import (
    HIGH,
    LOW,
    MAX_PAYLOAD,
    Outcome,
    PushResult,
    PushSender,
    Subscription,
    Vapid,
)

PHONE = ec.generate_private_key(ec.SECP256R1())  # the phone's key pair, made here
SECRET = os.urandom(16)
VAPID_KEY = ec.generate_private_key(ec.SECP256R1())  # this service's key pair, made here
VAPID = Vapid(VAPID_KEY, "mailto:ops@example.org")
ENDPOINT = "https://fcm.googleapis.com/fcm/send/fake-registration:APA91bTEST"
WNS = "https://wns2-bl2p.notify.windows.com/w/?token=BQYAAAfake"
NOW = 1_794_650_400.0  # Sat 14 Nov 2026 10:00:00 UTC
PAYLOAD = b'{"v":1,"kind":"test","title":"SUDARSHAN","body":"A test notification"}'


def subscription(endpoint: str = ENDPOINT) -> Subscription:
    return Subscription(endpoint, point(PHONE), SECRET)


class PushService:
    """A fake push service: keeps every request it is sent and answers as it is told."""

    def __init__(self, status=201, headers=None, error=None, delay=0.0):
        self.status, self.headers, self.error, self.delay = status, headers or {}, error, delay
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return httpx.Response(self.status, headers=self.headers, text="")

    def client(self, **options) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self), **options)


async def push(service, payload=PAYLOAD, *, to=None, client=None, deadline_s=5.0, **options):
    """One send through a client the test owns; the sender must leave that client open."""
    own = client or service.client()
    try:
        sender = PushSender(own, VAPID, deadline_s=deadline_s, wall=lambda: NOW)
        result = await sender.send(to or subscription(), payload, **{"ttl": 60, **options})
        assert not own.is_closed
        return result, sender
    finally:
        await own.aclose()


def run(coroutine):
    return asyncio.run(coroutine)


def test_the_request_rfc8030_rfc8291_and_rfc8292_ask_for():
    service = PushService(201)
    result, sender = run(push(service, ttl=21600, urgency=LOW, topic="digest-20261114"))
    assert result == PushResult(
        Outcome.SENT, "Google's notification service took the message.", "Google", 201
    )
    (request,) = service.requests
    assert (request.method, str(request.url)) == ("POST", ENDPOINT)
    headers = request.headers
    assert sorted(headers.keys()) == [
        "authorization", "content-encoding", "content-length", "content-type", "host", "topic", "ttl", "urgency",
    ]  # fmt: skip
    assert (headers["ttl"], headers["urgency"], headers["topic"]) == (
        "21600",
        "low",
        "digest-20261114",
    )
    assert (headers["content-encoding"], headers["content-type"]) == (
        "aes128gcm",
        "application/octet-stream",
    )
    assert int(headers["content-length"]) == len(request.content) == 86 + len(PAYLOAD) + 17
    token, key = re.fullmatch(r"vapid t=(\S+), k=(\S+)", headers["authorization"]).groups()
    claims = jwt.decode(
        token, VAPID_KEY.public_key(), algorithms=["ES256"], audience="https://fcm.googleapis.com"
    )
    assert claims["sub"] == "mailto:ops@example.org" and key == VAPID.public_key
    assert phone_reads(request.content, PHONE, SECRET) == PAYLOAD
    assert request.extensions["timeout"] == {
        "connect": 5.0,
        "read": 10.0,
        "write": 10.0,
        "pool": 5.0,
    }
    assert sender.stats[Outcome.SENT] == 1


def test_urgency_defaults_to_normal_and_topic_is_left_out_when_not_given():
    service = PushService(201)
    run(push(service))
    (request,) = service.requests
    assert request.headers["urgency"] == "normal" and "topic" not in request.headers


def test_nothing_of_the_callers_client_reaches_the_push_service():
    service = PushService(201)
    client = service.client(
        base_url="http://127.0.0.1:5583",
        headers={"X-Feed-Token": "for-the-feed-only", "Authorization": "Bearer loopback-only"},
        cookies={"session": "a-cookie"},
        auth=("someone", "a-password"),
        follow_redirects=True,
    )

    async def both():
        await client.get(ENDPOINT)  # the client's own requests carry all of it, so the test can see
        return await push(service, client=client)

    run(both())
    control, request = service.requests
    assert control.headers["x-feed-token"] == "for-the-feed-only" and "cookie" in control.headers
    assert str(request.url) == ENDPOINT
    for header in ("x-feed-token", "cookie", "user-agent", "accept", "accept-encoding"):
        assert header not in request.headers
    assert request.headers["authorization"].startswith("vapid t=")


def test_a_redirect_is_never_followed():
    service = PushService(307, headers={"Location": "http://127.0.0.1:5000/api/v1/placeorder"})
    client = service.client(follow_redirects=True)
    result, _ = run(push(service, client=client))
    assert len(service.requests) == 1
    assert (result.outcome, result.status) == (Outcome.REFUSED, 307)


HTTP_DATE_120_S_LATER = email.utils.formatdate(NOW + 120, usegmt=True)

ANSWERS = [
    # status, answer headers, outcome, retry_after, words
    (201, {}, Outcome.SENT, None, "Google's notification service took the message."),
    (202, {}, Outcome.SENT, None, "Google's notification service took the message."),
    (200, {}, Outcome.SENT, None, "Google's notification service took the message."),
    (404, {}, Outcome.GONE, None, "Google says this phone's notifications have ended: they were turned off on the phone or expired."),
    (410, {}, Outcome.GONE, None, "Google says this phone's notifications have ended: they were turned off on the phone or expired."),
    (413, {}, Outcome.TOO_LARGE, None, "Google's notification service refused the message as too large, so it was not sent."),
    (429, {"Retry-After": "120"}, Outcome.SLOW_DOWN, 120, "Google's notification service asked for fewer messages; send again in 120 seconds."),
    (429, {"Retry-After": HTTP_DATE_120_S_LATER}, Outcome.SLOW_DOWN, 120, "send again in 120 seconds."),
    (429, {"Retry-After": "0"}, Outcome.SLOW_DOWN, 0, "send again now."),
    (429, {"Retry-After": "99999999"}, Outcome.SLOW_DOWN, 86400, "send again in 86,400 seconds."),
    (429, {"Retry-After": "soon"}, Outcome.SLOW_DOWN, None, "send again later."),
    (429, {}, Outcome.SLOW_DOWN, None, "send again later."),
    (500, {}, Outcome.FAILING, None, "Google's notification service is having trouble; try again later."),
    (502, {}, Outcome.FAILING, None, "Google's notification service is having trouble; try again later."),
    (503, {"Retry-After": "30"}, Outcome.FAILING, 30, "Google's notification service is having trouble; try again in 30 seconds."),
    (400, {}, Outcome.REFUSED, None, "Google's notification service refused the message."),
    (401, {}, Outcome.REFUSED, None, "Google's notification service did not accept this server's signature."),
    (403, {}, Outcome.REFUSED, None, "Google's notification service did not accept this server's signature."),
    (406, {"Retry-After": "60"}, Outcome.REFUSED, None, "Google's notification service refused the message."),
    (418, {}, Outcome.REFUSED, None, "Google's notification service refused the message."),
    (307, {}, Outcome.REFUSED, None, "Google's notification service refused the message."),
]  # fmt: skip


@pytest.mark.parametrize("status, headers, outcome, retry_after, words", ANSWERS)
def test_each_answer_maps_to_one_outcome_and_is_tried_once(
    status, headers, outcome, retry_after, words
):
    service = PushService(status, headers)
    result, sender = run(push(service))
    assert (result.outcome, result.status, result.retry_after, result.service) == (
        outcome,
        status,
        retry_after,
        "Google",
    )
    assert result.reason.endswith(words)
    assert len(service.requests) == 1
    assert sender.stats[outcome] == 1 and sum(sender.stats.values()) == 1


def test_microsofts_service_says_slow_down_with_406():
    service = PushService(406, {"Retry-After": "60"})
    result, _ = run(push(service, to=subscription(WNS)))
    assert (result.outcome, result.retry_after, result.service) == (
        Outcome.SLOW_DOWN,
        60,
        "Microsoft",
    )
    (request,) = service.requests
    assert str(request.url) == WNS
    token = request.headers["authorization"].split(" ")[1].removeprefix("t=").rstrip(",")
    assert jwt.decode(
        token,
        VAPID_KEY.public_key(),
        algorithms=["ES256"],
        audience="https://wns2-bl2p.notify.windows.com",
    )


@pytest.mark.parametrize(
    "error, words",
    [
        (httpx.ConnectError("refused"), "could not be reached; try again later."),
        (httpx.RemoteProtocolError("cut off"), "could not be reached; try again later."),
        (httpx.ConnectTimeout("slow"), "did not answer in time; try again later."),
        (httpx.ReadTimeout("slow"), "did not answer in time; try again later."),
        (OSError("network is unreachable"), "could not be reached; try again later."),
    ],
)
def test_no_answer_is_failing_and_is_tried_once(error, words):
    service = PushService(error=error)
    result, _ = run(push(service))
    assert (result.outcome, result.status, result.retry_after) == (Outcome.FAILING, None, None)
    assert result.reason == f"Google's notification service {words}"
    assert len(service.requests) == 1


def test_the_whole_send_has_a_deadline():
    service = PushService(201, delay=5.0)
    began = time.monotonic()
    result, _ = run(push(service, deadline_s=0.05))
    assert time.monotonic() - began < 2.0
    assert (result.outcome, result.status) == (Outcome.FAILING, None)
    assert result.reason == "Google's notification service did not answer in time; try again later."
    assert len(service.requests) == 1


def test_a_payload_over_the_limit_is_refused_before_any_request():
    service = PushService(201)
    result, sender = run(push(service, b"x" * (MAX_PAYLOAD + 1)))
    assert (result.outcome, result.status) == (Outcome.TOO_LARGE, None)
    assert result.reason == (
        "The notification is 3,994 bytes and one push message carries at most 3,993, so it was not sent."
    )
    assert service.requests == [] and sender.stats[Outcome.TOO_LARGE] == 1
    result, _ = run(push(service, b"x" * MAX_PAYLOAD))
    assert result.outcome is Outcome.SENT and len(service.requests[0].content) == 4096


@pytest.mark.parametrize(
    "options, words",
    [
        ({"ttl": -1}, "time to live is 0 to 24,19,200 seconds"),
        ({"ttl": 28 * 24 * 3600 + 1}, "time to live"),
        ({"ttl": 21600.0}, "time to live"),
        ({"ttl": True}, "time to live"),
        ({"ttl": "60"}, "time to live"),
        ({"urgency": "urgent"}, "urgency is one of very-low, low, normal, high"),
        ({"urgency": "HIGH"}, "urgency is one of"),
        ({"topic": "a" * 33}, "topic is 1 to 32 letters"),
        ({"topic": "digest 2026"}, "topic is 1 to 32 letters"),
        ({"topic": "digest/2026"}, "topic is 1 to 32 letters"),
        ({"topic": ""}, "topic is 1 to 32 letters"),
    ],
)
def test_a_callers_mistake_is_refused_before_any_request(options, words):
    service = PushService(201)
    with pytest.raises(ValueError, match=words):
        run(push(service, **options))
    assert service.requests == []


def test_a_push_goes_to_a_checked_subscription_only():
    service = PushService(201)
    with pytest.raises(TypeError, match="sent to a Subscription"):
        run(push(service, to={"endpoint": ENDPOINT, "keys": {}}))
    assert service.requests == []


@pytest.mark.parametrize(
    "topic", ["digest-20261114", "security-new_device", "security-member_removed", "a" * 32]
)
def test_the_topics_the_family_service_will_use_are_taken(topic):
    service = PushService(201)
    result, _ = run(push(service, topic=topic, urgency=HIGH))
    assert result.outcome is Outcome.SENT and service.requests[0].headers["topic"] == topic


def test_sends_side_by_side_share_one_token_per_push_service():
    service = PushService(201)

    async def many():
        async with service.client() as client:
            sender = PushSender(client, Vapid(VAPID_KEY), wall=lambda: NOW)
            others = [subscription(), subscription(WNS)] * 10
            return await asyncio.gather(*(sender.send(s, PAYLOAD, ttl=60) for s in others)), sender

    results, sender = run(many())
    assert [r.outcome for r in results] == [Outcome.SENT] * 20 and sender.stats[Outcome.SENT] == 20
    tokens = {(str(r.url.host), r.headers["authorization"]) for r in service.requests}
    assert len(tokens) == 2
    assert len({phone_reads(r.content, PHONE, SECRET) for r in service.requests}) == 1
    assert len({r.content for r in service.requests}) == 20  # each its own key pair and salt
