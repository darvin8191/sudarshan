"""The worker's side of the hand-off: what each OpenAlgo answer means to it. No network."""

from __future__ import annotations

import httpx
import pytest

from sudarshan.autologin.handoff import LinkError, OpenAlgoLink
from sudarshan.vault import HANDOFF_INFO, fernet_for

PEPPER = "a1" * 32  # fake


def _link(handler) -> OpenAlgoLink:
    client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    return OpenAlgoLink("http://127.0.0.1:5000", fernet_for(PEPPER, HANDOFF_INFO), client)


def _answer(status: int, body=None, content: bytes | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if content is not None:
            return httpx.Response(status, content=content)
        return httpx.Response(status, json=body)

    return handler


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (429, "busy"),  # retry shortly
        (401, "rejected"),
        (403, "rejected"),
        (404, "rejected"),  # the route refused a request with forwarding headers
        (500, "error"),
        (502, "error"),
    ],
)
def test_status_meanings(status, kind):
    with pytest.raises(LinkError) as caught:
        _link(_answer(status, {"status": "error", "message": "x"})).preflight()
    assert (caught.value.kind, caught.value.status) == (kind, status)


def test_a_store_failure_answer_is_returned_not_raised():
    answer = {"status": "error", "applied": False, "message": "could not store"}
    assert _link(_answer(500, answer)).report(outcome="ok", token="t") == answer


@pytest.mark.parametrize("content", [b"<html>not json</html>", b"[1, 2]"])
def test_an_answer_that_is_not_a_json_object_is_an_error(content):
    with pytest.raises(LinkError) as caught:
        _link(_answer(200, content=content)).preflight()
    assert caught.value.kind == "error"


def test_connection_refused_is_unreachable():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LinkError) as caught:
        _link(refuse).preflight()
    assert (caught.value.kind, caught.value.status) == ("unreachable", None)


def test_each_call_is_one_sealed_post_without_proxy_headers():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"status": "success"})

    _link(handler).preflight()
    (request,) = seen
    assert request.method == "POST" and request.url.path.endswith("/internal/preflight")
    assert int(request.headers["content-length"]) == len(request.content) > 0
    for header in ("x-forwarded-for", "x-real-ip", "forwarded"):
        assert header not in request.headers
