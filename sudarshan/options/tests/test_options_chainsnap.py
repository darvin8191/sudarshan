"""The Upstox option chain read (M11.1, O1): one call, never retried, every failure an outcome.

A fake HTTP client stands in for Upstox throughout; nothing here reaches the network. The token is
synthetic.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from sudarshan.options import chainsnap

TOKEN = "tok-" + "A1b2" * 8  # synthetic, test only


class Reply:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeClient:
    def __init__(self, *replies):
        self.calls, self.replies = [], list(replies)

    def get(self, url, **kw):
        self.calls.append((url, kw))
        nxt = self.replies.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


@pytest.fixture
def upstox(monkeypatch):
    import broker.upstox.api.rate_limiter as limiter

    client = FakeClient()
    monkeypatch.setattr(chainsnap, "get_httpx_client", lambda: client)
    monkeypatch.setattr(limiter, "apply_rate_limit", lambda category: None)
    return client


def read():
    return chainsnap.fetch(TOKEN, "NSE_INDEX|Nifty 50", "2026-11-17")


def test_one_call_with_the_token_in_the_header_only_and_explicit_timeouts(upstox):
    upstox.replies.append(Reply(200, {"status": "success", "data": []}))
    out = read()
    assert out == chainsnap.Outcome("ok", {"status": "success", "data": []})
    ((url, kw),) = upstox.calls
    assert url == "https://api.upstox.com/v2/option/chain"
    assert kw["params"] == {"instrument_key": "NSE_INDEX|Nifty 50", "expiry_date": "2026-11-17"}
    assert kw["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in url and TOKEN not in repr(kw["params"])
    assert kw["timeout"].connect == 1.0 and kw["timeout"].read == 8.0
    assert kw["follow_redirects"] is False


@pytest.mark.parametrize(
    ("reply", "kind"),
    [
        (Reply(401, {"status": "error"}), "token"),
        (Reply(403, None), "token"),
        (Reply(429, {"status": "error"}), "wait"),
        (Reply(500, None), "failed"),
        (Reply(200, None), "failed"),
        (Reply(200, ["not", "a", "dict"]), "failed"),
        (Reply(200, {"status": "error", "errors": [{"errorCode": "UDAPI100011"}]}), "failed"),
        (httpx.ReadTimeout("slow"), "failed"),
        (httpx.ConnectError("down"), "failed"),
    ],
)
def test_every_failure_is_an_outcome_and_nothing_is_tried_again(upstox, reply, kind):
    upstox.replies.append(reply)
    assert read().kind == kind
    assert len(upstox.calls) == 1


def test_the_plugin_s_limiter_asking_to_wait_makes_no_call(upstox, monkeypatch):
    import broker.upstox.api.rate_limiter as limiter
    from utils.broker_backpressure import BrokerBusyError

    def busy(category):
        raise BrokerBusyError("busy")

    monkeypatch.setattr(limiter, "apply_rate_limit", busy)
    assert read().kind == "wait"
    assert upstox.calls == []


def test_the_token_is_never_in_a_log_line(upstox, caplog):
    upstox.replies += [httpx.ReadTimeout(f"timed out with {TOKEN}"), Reply(500, {"t": TOKEN})]
    with caplog.at_level(logging.DEBUG):
        read()
        read()
    assert caplog.records, "the failures are logged"
    assert TOKEN not in caplog.text


def test_a_read_whose_answer_breaks_is_sent_once_through_the_shared_client(monkeypatch):
    """The shared client's own transport sends a GET whose answer broke once more. The chain read
    promises one call, so it says so to that transport. The real client class is used here, with
    its sockets replaced: the fake client above would never show a second send."""
    import broker.upstox.api.rate_limiter as limiter
    from utils import httpx_client

    sent = []

    def broken(self, request):
        sent.append(request.url.path)
        raise httpx.RemoteProtocolError("peer closed", request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", broken)
    monkeypatch.setattr(limiter, "apply_rate_limit", lambda category: None)
    client = httpx_client._BrokerClient()
    monkeypatch.setattr(chainsnap, "get_httpx_client", lambda: client)
    try:
        assert read() == chainsnap.Outcome("failed")
    finally:
        client.close()
    assert sent == [chainsnap.PATH]
