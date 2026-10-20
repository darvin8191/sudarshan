"""Fakes shared by the auto-login tests. Every value here is invented."""

from __future__ import annotations

import json
import threading
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sudarshan.autologin.handoff import LinkError
from sudarshan.autologin.schedule import IST

FAKE_TOTP_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # RFC 6238's test key, base32
FAKE_PIN = "246813"
FAKE_API_KEY = "fake-upstox-api-key-0000"
FAKE_TOKEN = "fake-access-token-" + "z" * 24
OLD_TOKEN = "fake-old-access-token-" + "y" * 20

#: Monday 19 Oct 2026, 08:35:10 IST: a trading day, inside the window, mid TOTP step.
MONDAY_0835 = datetime(2026, 10, 19, 8, 35, 10, tzinfo=IST)


def ok_payload(token: str = FAKE_TOKEN) -> dict:
    """Shaped like Upstox's Get Token answer (fields at the top level), with fake values."""
    return {
        "email": "owner@example.invalid",
        "exchanges": ["NSE", "NFO"],
        "products": ["D", "I"],
        "broker": "UPSTOX",
        "user_id": "FAKE01",
        "user_name": "Fake Owner",
        "order_types": ["MARKET", "LIMIT"],
        "user_type": "individual",
        "poa": False,
        "is_active": True,
        "access_token": token,
        "extended_token": None,
    }


def error_payload(code: str, message: str = "error") -> dict:
    return {
        "status": "error",
        "errors": [
            {
                "errorCode": code,
                "message": message,
                "propertyPath": None,
                "invalidValue": FAKE_PIN,  # an echo like this must never reach a log
                "error_code": code,
            }
        ],
    }


class FakeUpstox:
    """The TOTP token endpoint on 127.0.0.1, recording every request it receives."""

    def __init__(self):
        self.requests: list[dict] = []
        self.answers: deque[tuple[int, object]] = deque()
        self.default: tuple[int, object] = (200, ok_payload())
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 (http.server naming)
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = None
                fake.requests.append(
                    {
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "json": body,
                    }
                )
                status, payload = fake.answers.popleft() if fake.answers else fake.default
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):  # keep test output quiet
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self) -> FakeUpstox:
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class FakeLink:
    """Stands in for OpenAlgoLink: answers preflight/report and records what was asked."""

    def __init__(self, *, fresh: bool = False, broker: str = "upstox", user: bool = True):
        self.preflight_answer = {
            "status": "success",
            "user": user,
            "broker": broker,
            "fresh_session": fresh,
        }
        self.report_answers: deque = deque()
        self.preflight_errors: deque[LinkError] = deque()  # raised once each, in order
        self.preflight_error: LinkError | None = None  # then this one on every call
        self.calls: list[tuple[str, dict]] = []

    def preflight(self) -> dict:
        self.calls.append(("preflight", {}))
        if self.preflight_errors:
            raise self.preflight_errors.popleft()
        if self.preflight_error:
            raise self.preflight_error
        return dict(self.preflight_answer)

    def report(self, **fields) -> dict:
        self.calls.append(("report", fields))
        answer = self.report_answers.popleft() if self.report_answers else None
        if isinstance(answer, LinkError):
            raise answer
        if answer is None:
            answer = {"status": "success", "applied": fields.get("outcome") == "ok"}
        return answer

    def reports(self, outcome: str | None = None) -> list[dict]:
        return [
            fields
            for op, fields in self.calls
            if op == "report" and (outcome is None or fields.get("outcome") == outcome)
        ]


class FakeClock:
    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at

    def advance(self, **delta) -> None:
        from datetime import timedelta

        self.at = self.at + timedelta(**delta)
