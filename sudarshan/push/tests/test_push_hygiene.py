"""F9: what the push library never does. It never logs an endpoint, a key, a token or a payload
(the names its log calls pass are read here, as the family modules' own log test reads theirs,
and every outcome is run with the log at DEBUG); it never makes or closes an HTTP client of its
own; and it leaves no descriptor or unread answer behind (fd-audit, measured)."""

from __future__ import annotations

import ast
import asyncio
import logging
import os
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from push_testkit import b64url, point

from sudarshan.push import MAX_PAYLOAD, Outcome, PushSender, Subscription, Vapid

PACKAGE = Path(__file__).resolve().parents[1]
SOURCES = sorted(PACKAGE.glob("*.py"))
LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
#: Never passed to a log call: what this package holds, and the family modules' own list.
FORBIDDEN = {
    "endpoint", "p256dh", "auth", "auth_secret", "secret", "token", "signed", "jwt",
    "authorization", "headers", "request", "url", "payload", "data", "body", "plaintext",
    "sealed", "subscription", "keys", "key", "private_key", "sender_key", "pem", "salt",
    "cek", "nonce", "ikm", "prk", "prk_key", "ecdh_secret", "claims", "subject",
    "handle", "name", "chat_id", "code", "password", "totp",
}  # fmt: skip


def log_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in LOG_METHODS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in {"log", "logger", "logging"}
        ):
            yield node


def offences(source: str) -> list[str]:
    """Each log call that passes a forbidden name, any exception text, or a traceback."""
    found = []
    for call in log_calls(ast.parse(source)):
        if call.func.attr == "exception" or any(k.arg == "exc_info" for k in call.keywords):
            found.append(f"line {call.lineno}: a traceback can carry an address")
        for argument in [*call.args, *(k.value for k in call.keywords)]:
            for node in ast.walk(argument):
                named = node.id if isinstance(node, ast.Name) else getattr(node, "attr", None)
                if named in FORBIDDEN:
                    found.append(f"line {call.lineno}: passes {named}")
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "str":
                    found.append(f"line {call.lineno}: passes an exception's text")
    return found


def test_the_checker_finds_what_it_looks_for():
    planted = (
        "log.debug('sent to %s', endpoint)\n"
        "log.info('x %s', result.payload)\n"
        "logger.warning(f'{subscription}')\n"
        "log.debug('failed: %s', str(exc))\n"
        "log.error('failed', exc_info=True)\n"
        "log.exception('failed')\n"
        "log.debug('fine %s', company)\n"
    )
    assert len(offences(planted)) == 6


def test_no_log_call_in_the_package_passes_a_secret_or_a_traceback():
    calls = 0
    for path in SOURCES:
        source = path.read_text(encoding="utf-8")
        calls += len(list(log_calls(ast.parse(source))))
        assert offences(source) == [], path.name
    assert calls >= 2  # the package does log, so the check above is not empty


def test_the_package_never_makes_an_http_client_of_its_own():
    for path in SOURCES:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                called = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                assert called not in {"AsyncClient", "Client", "aclose", "close"}, path.name


# ---- Run every outcome with the log at DEBUG and look for anything that must not be there

PHONE = ec.generate_private_key(ec.SECP256R1())
SECRET = os.urandom(16)
ENDPOINT = "https://fcm.googleapis.com/fcm/send/REGISTRATION-TOKEN-7f3a:APA91bSECRETPART"
CONTACT = "mailto:private-contact@example.org"
WORDS = "PAYLOAD-WORDS: the owner's position figures"
OUTCOMES = [
    (201, None),
    (410, None),
    (413, None),
    (429, None),
    (503, None),
    (403, None),
    (None, httpx.ConnectError(f"cannot reach {ENDPOINT}")),
    (None, httpx.ReadTimeout(f"no answer from {ENDPOINT}")),
]


def answering(status, error, seen):
    async def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if error is not None:
            raise error
        return httpx.Response(status, headers={"Retry-After": "5"})

    return answer


def test_no_log_line_holds_an_endpoint_a_key_a_token_or_the_payload(caplog):
    caplog.set_level(logging.DEBUG, logger="sudarshan.push")
    vapid = Vapid(ec.generate_private_key(ec.SECP256R1()), CONTACT)
    seen: list[httpx.Request] = []
    outcomes = set()

    async def every():
        for status, error in OUTCOMES:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(answering(status, error, seen))
            ) as client:
                sender = PushSender(client, vapid)
                to = Subscription(ENDPOINT, point(PHONE), SECRET)
                outcomes.add((await sender.send(to, WORDS, ttl=60)).outcome)
                outcomes.add((await sender.send(to, "x" * (MAX_PAYLOAD + 1), ttl=60)).outcome)

    asyncio.run(every())
    assert outcomes == set(Outcome)
    secrets = {
        ENDPOINT,
        "REGISTRATION-TOKEN-7f3a",
        "APA91bSECRETPART",
        "/fcm/send/",
        b64url(point(PHONE)),
        b64url(SECRET),
        "private-contact",
        "PAYLOAD-WORDS",
        "position figures",
        *(request.headers["authorization"].split("t=")[1].split(",")[0] for request in seen),
        *(request.headers["authorization"].split(".")[2][:20] for request in seen),
    }
    lines = [
        " ".join([record.getMessage(), record.exc_text or "", str(record.args)])
        for record in caplog.records
        if record.name.startswith("sudarshan.push")
    ]
    assert len(lines) >= len(OUTCOMES)  # one line per send at DEBUG: the check is not empty
    for line in lines:
        for secret in secrets:
            assert secret not in line, (secret, line)


# ---- fd-audit, measured: descriptors flat and every answer read to its end


class CountedStream(httpx.AsyncByteStream):
    closed = 0

    async def __aiter__(self):
        yield b'{"ok": true}'

    async def aclose(self) -> None:
        CountedStream.closed += 1


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="counts /proc/self/fd (Linux)")
def test_descriptors_stay_flat_over_many_sends_and_key_loads(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    private = tmp_path / "vapid_private.pem"
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    public = tmp_path / "vapid_public.txt"
    public.write_text(b64url(point(key)) + "\n")
    statuses = [201, 404, 413, 429, 500, 403]

    async def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            statuses[len(request.content) % len(statuses)], stream=CountedStream()
        )

    async def rounds(count: int) -> int:
        sent = 0
        async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
            sender = PushSender(client, Vapid.from_files(private, public))
            to = Subscription(ENDPOINT, point(PHONE), SECRET)
            for number in range(count):
                await sender.send(to, "x" * (number % 50), ttl=60)
                sent += 1
            assert not client.is_closed
        return sent

    asyncio.run(rounds(20))  # warm up whatever the first use opens once
    CountedStream.closed = 0
    before = len(os.listdir("/proc/self/fd"))
    sent = asyncio.run(rounds(300))
    for _ in range(100):
        Vapid.from_files(private, public)
    after = len(os.listdir("/proc/self/fd"))
    assert after == before
    assert CountedStream.closed == sent == 300  # every answer was read to its end and closed
