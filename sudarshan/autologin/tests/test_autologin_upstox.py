"""The Upstox TOTP token call: request shape, answers, error codes. No network (MockTransport)."""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from autologin_fakes import FAKE_API_KEY, FAKE_PIN, FAKE_TOKEN, error_payload, ok_payload

from sudarshan.autologin.messages import MESSAGES, paused_message
from sudarshan.autologin.upstox import (
    RATE_LIMIT_COOLDOWN_S,
    TOKEN_PATH,
    check_token,
    request_token,
)

BASE = "https://api.upstox.com"
CODE = "081804"


def _client(handler) -> tuple[httpx.Client, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(transport)), seen


def _answer(status, payload):
    def handler(request):
        if isinstance(payload, bytes):
            return httpx.Response(status, content=payload)
        return httpx.Response(status, json=payload)

    return handler


def test_request_shape_and_top_level_token():
    client, seen = _client(_answer(200, ok_payload()))
    result = request_token(client, BASE, FAKE_API_KEY, CODE, FAKE_PIN)
    assert result.ok and result.token == FAKE_TOKEN and result.code == "ok"
    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == BASE + TOKEN_PATH == f"{BASE}/v2/totp-login/authorization/token"
    assert request.headers["x-api-key"] == FAKE_API_KEY
    assert request.headers["content-type"] == "application/json"
    assert request.headers["accept"] == "application/json"
    assert json.loads(request.content) == {"totp": CODE, "pin": FAKE_PIN}


def test_token_nested_under_data_is_accepted():
    client, _ = _client(_answer(200, {"status": "success", "data": ok_payload()}))
    result = request_token(client, BASE, FAKE_API_KEY, CODE, FAKE_PIN)
    assert result.ok and result.token == FAKE_TOKEN


def test_token_never_in_repr():
    client, _ = _client(_answer(200, ok_payload()))
    assert FAKE_TOKEN not in repr(request_token(client, BASE, FAKE_API_KEY, CODE, FAKE_PIN))


@pytest.mark.parametrize(
    ("upstox_code", "status", "code", "pause", "cooldown"),
    [
        ("UDAPI100097", 401, "invalid_credentials", True, 0),
        ("UDAPI100098", 400, "totp_not_enabled", True, 0),
        ("UDAPI100099", 429, "rate_limited", False, RATE_LIMIT_COOLDOWN_S),
        ("UDAPI100101", 403, "not_individual_app", True, 0),
    ],
)
def test_documented_error_codes(upstox_code, status, code, pause, cooldown):
    client, _ = _client(_answer(status, error_payload(upstox_code)))
    result = request_token(client, BASE, FAKE_API_KEY, CODE, FAKE_PIN)
    assert not result.ok and result.token is None
    assert (result.code, result.pause, result.cooldown_s) == (code, pause, cooldown)
    assert result.upstox_codes == (upstox_code,)
    assert code in MESSAGES


def test_snake_case_error_code_and_200_with_error():
    payload = {"status": "error", "errors": [{"error_code": "UDAPI100097", "message": "x"}]}
    client, _ = _client(_answer(200, payload))
    assert request_token(client, BASE, FAKE_API_KEY, CODE, FAKE_PIN).code == "invalid_credentials"


@pytest.mark.parametrize(
    ("status", "payload", "code", "pause"),
    [
        (429, b"slow down", "rate_limited", False),
        (500, b"<html>oops</html>", "upstox_unavailable", False),
        (503, error_payload("UDAPI100500"), "upstox_unavailable", False),
        (400, error_payload("UDAPI100016"), "upstox_refused", True),
        (401, {}, "upstox_refused", True),
        (200, {"status": "success"}, "unexpected_response", False),
        (200, b"not json", "unexpected_response", False),
        (302, b"", "unexpected_response", False),
    ],
)
def test_other_answers(status, payload, code, pause):
    client, _ = _client(_answer(status, payload))
    result = request_token(client, BASE, FAKE_API_KEY, CODE, FAKE_PIN)
    assert (result.ok, result.code, result.pause) == (False, code, pause)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 451])
@pytest.mark.parametrize(
    "payload",
    [b"", {}, error_payload("UDAPI199999"), error_payload("UDAPI100099")],
    ids=["empty", "no-code", "unknown-code", "rate-limit-code"],
)
def test_every_refusal_except_too_many_requests_pauses(status, payload):
    """Until Upstox's codes are seen live, a refused key and PIN are never sent again."""
    client, _ = _client(_answer(status, payload))
    result = request_token(client, BASE, FAKE_API_KEY, CODE, FAKE_PIN)
    assert not result.ok and result.pause is True
    assert result.code in MESSAGES
    # A pause lasts until the owner saves again, so its cause never promises "a few minutes".
    assert result.code != "rate_limited"
    assert "few minutes" not in paused_message(result.code)


def test_a_refusal_with_the_too_many_requests_code_names_it():
    client, _ = _client(_answer(400, error_payload("UDAPI100099")))
    result = request_token(client, BASE, FAKE_API_KEY, CODE, FAKE_PIN)
    assert (result.code, result.pause, result.cooldown_s) == ("too_many_attempts", True, 0)
    assert paused_message(result.code) == (
        "Upstox refused the login because of too many attempts. Auto-login is paused until you "
        "save the TOTP key or the PIN again on the Auto login page."
    )


@pytest.mark.parametrize(
    ("status", "payload"),
    [(429, b""), (429, error_payload("UDAPI100099")), (502, b""), (504, {})],
)
def test_too_many_requests_and_server_trouble_do_not_pause(status, payload):
    client, _ = _client(_answer(status, payload))
    result = request_token(client, BASE, FAKE_API_KEY, CODE, FAKE_PIN)
    assert not result.ok and result.pause is False
    assert result.cooldown_s == (RATE_LIMIT_COOLDOWN_S if status == 429 else 0)


@pytest.mark.parametrize(
    "error", [httpx.ConnectTimeout("t"), httpx.ReadTimeout("t"), httpx.ConnectError("refused")]
)
def test_network_problems_never_raise(error):
    def handler(request):
        raise error

    client, _ = _client(handler)
    result = request_token(client, BASE, FAKE_API_KEY, CODE, FAKE_PIN)
    assert (result.ok, result.code) == (False, "network")


def test_error_body_is_never_logged(caplog):
    """An error answer can echo the PIN (``invalidValue``); only codes may reach the log."""
    caplog.set_level(logging.DEBUG)
    for status, payload in [
        (401, error_payload("UDAPI100097")),
        (400, error_payload("UDAPI199999")),
        (500, error_payload("UDAPI100500")),
    ]:
        client, _ = _client(_answer(status, payload))
        request_token(client, BASE, FAKE_API_KEY, CODE, FAKE_PIN)
    assert FAKE_PIN not in caplog.text
    assert CODE not in caplog.text
    assert FAKE_API_KEY not in caplog.text
    assert "UDAPI100097" in caplog.text  # the code itself is useful and harmless


# -- the read-only check of the stored token ------------------------------------------------------


def test_token_check_is_one_read_only_call():
    client, seen = _client(_answer(200, {"status": "success", "data": {}}))
    assert check_token(client, BASE, FAKE_TOKEN) == "works"
    (request,) = seen
    assert request.method == "GET" and request.content == b""
    assert str(request.url) == f"{BASE}/v3/user/get-funds-and-margin"
    assert request.headers["authorization"] == f"Bearer {FAKE_TOKEN}"
    assert request.headers["api-version"] == "3.0"


@pytest.mark.parametrize(
    ("status", "verdict"),
    [(401, "dead"), (403, "unknown"), (429, "unknown"), (500, "unknown"), (503, "unknown")],
)
def test_token_check_verdicts(status, verdict, caplog):
    caplog.set_level(logging.DEBUG)
    client, _ = _client(_answer(status, error_payload("UDAPI100050", "Invalid token")))
    assert check_token(client, BASE, FAKE_TOKEN) == verdict
    assert FAKE_TOKEN not in caplog.text


@pytest.mark.parametrize("error", [httpx.ConnectTimeout("t"), httpx.ReadTimeout("t")])
def test_token_check_never_guesses_on_a_network_problem(error):
    def handler(request):
        raise error

    client, _ = _client(handler)
    assert check_token(client, BASE, FAKE_TOKEN) == "unknown"
