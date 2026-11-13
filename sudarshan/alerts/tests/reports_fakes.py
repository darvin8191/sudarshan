"""Test helpers for the report desk (JC3): OpenAlgo's two Telegram routes and the journal's local
API, each on 127.0.0.1 in the test's own event loop. Every key, name and figure is a fake.

The journal (sudarshan-journal) is built by another lane at the same time, so its API here is a
stand-in: it opens each request with the real ``JOURNAL_DELIVERY_INFO`` key and ``open_sealed``
(60 s, once, the op in the path must be the op inside), and answers from ``reports``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import time

from werkzeug.wrappers import Request

from sudarshan.autologin.handoff import HandoffRejected, NonceBook, open_sealed
from sudarshan.vault import JOURNAL_DELIVERY_INFO, fernet_for

API_KEY = "test-api-key-" + "3" * 40  # never a real key  # pragma: allowlist secret
OWNER = "owner"
PDF = b"%PDF-1.7\n1 0 obj\n<< >>\nendobj\ntrailer\n%%EOF\n"
DAY_TEXT = (
    "सुदर्शन · SUDARSHAN\nMon 9 Nov · Day report\n"
    "Realised +₹4,312.50 · Unrealised −₹600.00\nTrades 6 · won 3, lost 2, 1 flat\n"
    "Open the full report: SUDARSHAN > Reports"
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def report_record(report_id: int, text: str = DAY_TEXT, data: bytes | None = PDF,
                  name: str = "SUDARSHAN_Day-report_20261109.pdf") -> dict:  # fmt: skip
    """What the journal keeps for one report: its text and, when it has one, its file."""
    record = {"id": report_id, "kind": "eod", "text": text}
    if data is not None:
        record.update(file_name=name, file_mime="application/pdf", file_bytes=data)
    return record


class _Server:
    """One request per connection on 127.0.0.1:<free port>; ``answer(path, headers, body)``."""

    async def __aenter__(self):
        self._writers: set = set()
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        deadline = time.monotonic() + 2
        while self._writers and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        self._server.close()
        for writer in list(self._writers):
            writer.transport.abort()
        await asyncio.wait_for(self._server.wait_closed(), 5)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def _serve(self, reader, writer) -> None:
        self._writers.add(writer)
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            request_line, *lines = head.decode("latin-1").split("\r\n")
            headers = {}
            for line in lines:
                name, _, value = line.partition(":")
                headers[name.strip().lower()] = value.strip()
            body = await reader.readexactly(int(headers.get("content-length") or 0))
            status, answer = await self.answer(request_line.split(" ")[1], headers, body)
            data = json.dumps(answer).encode()
            writer.write(
                f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode()
                + data
            )
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            self._writers.discard(writer)


class FakeOpenAlgo(_Server):
    """OpenAlgo's ``/api/v1/telegram/notify`` (JSON) and ``/notify-file`` (multipart).

    Texts are kept in ``texts``, files as dicts in ``files`` (the form and the file), and each is
    answered from its own queue (``text_answers``, ``file_answers``) or the default success.
    ``order`` records which route was called, in turn. ``file_route=False`` is an older OpenAlgo:
    /notify-file answers the app's own 404 for an unknown path.
    """

    TEXT_SENT = (200, {"status": "success", "message": "Notification sent successfully"})
    FILE_SENT = (200, {"status": "success", "message": "File sent successfully"})

    def __init__(self):
        self.texts: list[dict] = []
        self.files: list[dict] = []
        self.order: list[str] = []
        self.text_answers: list[tuple[int, dict]] = []
        self.file_answers: list[tuple[int, dict]] = []
        self.file_route = True

    async def answer(self, path: str, headers: dict, body: bytes):
        if path == "/api/v1/telegram/notify":
            self.order.append("text")
            self.texts.append(json.loads(body))
            return self.text_answers.pop(0) if self.text_answers else self.TEXT_SENT
        if path == "/api/v1/telegram/notify-file" and self.file_route:
            self.order.append("file")
            parsed = Request.from_values(
                input_stream=io.BytesIO(body), content_length=len(body),
                content_type=headers["content-type"], method="POST",
            )  # fmt: skip
            upload = parsed.files["file"]
            self.files.append(
                {
                    "form": dict(parsed.form),
                    "name": upload.filename,
                    "mime": upload.mimetype,
                    "data": upload.read(),
                }  # fmt: skip
            )
            return self.file_answers.pop(0) if self.file_answers else self.FILE_SENT
        return 404, {"status": "error", "message": "Not found", "path": path}


class FakeJournalApi(_Server):
    """The journal's local API as the report desk meets it: the delivery purpose's three ops.

    ``reports`` maps a report id to ``report_record(...)``; ``deliveries`` keeps each delivery
    call's args; ``calls`` every op in turn; ``answers[op]`` overrides an op with a
    ``(status, body)`` pair. A request that does not open under the delivery key, names another
    principal or another op answers 403, as the service does.
    """

    def __init__(self):
        self.fernet = fernet_for(os.environ["API_KEY_PEPPER"], JOURNAL_DELIVERY_INFO)
        self.nonces = NonceBook(1024)
        self.reports: dict[int, dict] = {}
        self.deliveries: list[dict] = []
        self.calls: list[tuple[str, dict]] = []
        self.principals: list[str] = []
        self.answers: dict[str, tuple[int, dict]] = {}
        self.refused: list[str] = []

    async def answer(self, path: str, headers: dict, body: bytes):
        op = path.removeprefix("/v1/")
        if op not in ("report_get", "report_file", "delivery"):
            return 403, {"status": "error", "code": "forbidden", "message": "Forbidden."}
        try:
            message = open_sealed(self.fernet, body, op=op, seen=self.nonces,
                                  max_bytes=16 * 1024)  # fmt: skip
        except HandoffRejected as exc:
            self.refused.append(str(exc))
            return 403, {"status": "error", "code": "forbidden", "message": "Forbidden."}
        self.principals.append(message.get("principal"))
        args = message.get("args") or {}
        self.calls.append((op, args))
        if op in self.answers:
            return self.answers[op]
        if op == "delivery":
            self.deliveries.append(args)
            return 200, {"status": "success", "data": {"id": args.get("report_id"), "version": 2}}
        record = self.reports.get(args.get("id"))
        if record is None:
            return 404, {"status": "error", "code": "not_found",
                         "message": "This report is not in the archive."}  # fmt: skip
        if op == "report_get":
            return 200, {"status": "success", "data": {
                "id": record["id"], "kind": record["kind"], "text": record["text"],
                "file_name": record.get("file_name"),
            }}  # fmt: skip
        data = record.get("file_bytes")
        return 200, {"status": "success", "data": {
            "id": record["id"], "file_name": record.get("file_name"),
            "file_mime": record.get("file_mime"),
            "file_sha256": sha(data) if data is not None else None,
            "file_b64": base64.b64encode(data).decode() if data is not None else None,
        }}  # fmt: skip
