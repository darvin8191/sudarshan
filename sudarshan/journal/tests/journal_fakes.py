"""Fakes on 127.0.0.1 for the journal's service tests (the feed tests' pattern): the price feed's
HTTP API, and helpers to seal a request as each caller does. Every value is synthetic."""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from sudarshan.autologin.handoff import seal
from sudarshan.vault import (
    JOURNAL_API_INFO,
    JOURNAL_DELIVERY_INFO,
    JOURNAL_FAMILY_INFO,
    JOURNAL_NUDGE_INFO,
    PORTFOLIO_JOURNAL_API_INFO,
    fernet_for,
)

PEPPER = "p" * 48  # fake, test only
KEYS = {
    "api": fernet_for(PEPPER, JOURNAL_API_INFO),
    "delivery": fernet_for(PEPPER, JOURNAL_DELIVERY_INFO),
    "nudge": fernet_for(PEPPER, JOURNAL_NUDGE_INFO),
    "family": fernet_for(PEPPER, JOURNAL_FAMILY_INFO),
}
PORTFOLIO_KEY = fernet_for(PEPPER, PORTFOLIO_JOURNAL_API_INFO)
PRINCIPALS = {
    "api": "owner",
    "delivery": "sudarshan-alerts",
    "nudge": "sudarshan-alerts",
    "family": "sudarshan-family",
}


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def sealed(op: str, purpose: str = "api", *, now: float | None = None, principal=None,
           **args) -> bytes:  # fmt: skip
    """A request as its caller seals it: the bridge and the desk put the arguments under
    ``args``; the alerts' nudge writes its three fields into the seal itself."""
    who = PRINCIPALS[purpose] if principal is None else principal
    if purpose == "nudge":
        return seal(KEYS["nudge"], op, now=now, principal=who, **args)
    return seal(KEYS[purpose], op, now=now, principal=who, args=args)


class _Server:
    """A ThreadingHTTPServer on a free loopback port, in a daemon thread; ``stop`` closes it."""

    def __init__(self):
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def handler(self) -> type[BaseHTTPRequestHandler]:  # pragma: no cover - overridden
        raise NotImplementedError

    def start(self) -> str:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


class FakeFeed(_Server):
    """sudarshan-feed's loopback API as the journal uses it: /health and /claims/<client>."""

    def __init__(self):
        super().__init__()
        self.started_at = "2026-11-09T09:00:00"
        self.claims: dict[str, dict] = {}
        self.claim_status = 200
        self.calls: list[tuple[str, str, dict]] = []
        self.down = False

    def handler(feed):  # noqa: N805 - a closure over the fake, not a method
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _do(self):
                url = urlsplit(self.path)
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
                feed.calls.append(
                    (self.command, url.path, query | {"_body": body} | {"_h": dict(self.headers)})
                )
                if feed.down:
                    self.connection.close()
                    return
                if url.path == "/health":
                    return self._send(
                        200,
                        {"state": "live", "started_at": feed.started_at,
                         "subscribed": {"total": len(feed.claims)}},
                    )  # fmt: skip
                if url.path.startswith("/claims/"):
                    client = url.path.rpartition("/")[2]
                    if self.command == "DELETE":
                        feed.claims.pop(client, None)
                        return self._send(200, {"ok": True})
                    if feed.claim_status == 200:
                        feed.claims[client] = body.get("keys", {})
                    return self._send(feed.claim_status, {"ok": feed.claim_status == 200})
                return self._send(404, {"error": "not found"})

            do_GET = do_PUT = do_DELETE = do_POST = _do

        return Handler
