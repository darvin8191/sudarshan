"""OpenAlgo's /api/v1/telegram/notify-file (M15.7, JC2): one report file to the owner's chat.

The route is loaded from restx_api/telegram_bot.py without the restx_api package (its __init__
imports every namespace, some of which open databases and start threads at import), put on a
fresh Flask-RESTX Api, and driven with a mocked Telegram: the multipart POST that
``send_file_sync`` makes is caught by an httpx MockTransport and read back with werkzeug. Every
key, token, chat and name is a fake.
"""

from __future__ import annotations

import ast
import importlib.util
import io
import re
import sys
import time
import types
from pathlib import Path

import httpx
import pytest
from flask import Flask
from flask_restx import Api
from werkzeug.wrappers import Request

import services.telegram_alert_service as alert_service
from limiter import limiter
from sudarshan.alerts import report_files
from sudarshan.tests.fdwatch import FdWatch

ROOT = Path(__file__).resolve().parents[3]
API_KEY = "test-api-key-" + "2" * 40  # never a real key  # pragma: allowlist secret
TOKEN = "123456:test-bot-token-fake"  # never a real token  # pragma: allowlist secret
OWNER = "owner"
CHAT = 424242
PATH = "/api/v1/telegram/notify-file"
PDF = b"%PDF-1.7\n1 0 obj\n<< >>\nendobj\ntrailer\n%%EOF\n"
PNG = report_files.PNG_SIGNATURE + b"\x00\x00\x00\rIHDR" + b"\x00" * 17
CSV = b"date,symbol,net\n2026-11-09,NIFTY,812.50\n"


def _load_routes() -> types.ModuleType:
    """restx_api/telegram_bot.py, loaded under a stand-in parent package that is removed again."""
    names = ("restx_api", "restx_api.telegram_bot")
    saved = {name: sys.modules.get(name) for name in names}
    parent = types.ModuleType("restx_api")
    parent.__path__ = [str(ROOT / "restx_api")]
    sys.modules["restx_api"] = parent
    try:
        spec = importlib.util.spec_from_file_location(
            "restx_api.telegram_bot", ROOT / "restx_api" / "telegram_bot.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for name, before in saved.items():
            if before is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = before
    return module


ROUTES = _load_routes()


class Telegram:
    """The Bot API as send_file_sync meets it: every request kept, each answered by ``status``."""

    def __init__(self, status: int = 200):
        self.status = status
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = request.read()
        parsed = Request.from_values(
            input_stream=io.BytesIO(body),
            content_length=len(body),
            content_type=request.headers["content-type"],
            method="POST",
        )
        self.requests.append(
            {
                "path": request.url.path,
                "form": dict(parsed.form),
                "files": {
                    field: (upload.filename, upload.mimetype, upload.read())
                    for field, upload in parsed.files.items()
                },
            }
        )
        payload = {"ok": self.status == 200}
        if self.status != 200:
            payload["description"] = "Bad Request: chat not found"
        return httpx.Response(self.status, json=payload)


@pytest.fixture
def telegram(monkeypatch):
    fake = Telegram()
    client = httpx.Client(transport=httpx.MockTransport(fake.handler))
    monkeypatch.setattr(alert_service, "_http_client", client)
    yield fake
    client.close()


@pytest.fixture
def linked(monkeypatch):
    """A valid key, a running bot and the owner linked to a chat."""
    monkeypatch.setattr(ROUTES, "verify_api_key", lambda key: key == API_KEY)
    monkeypatch.setattr(
        ROUTES, "get_telegram_user_by_username",
        lambda name: {"telegram_id": CHAT} if name == OWNER else None,
    )  # fmt: skip
    monkeypatch.setattr(ROUTES.telegram_alert, "is_bot_active", lambda: True)
    monkeypatch.setattr(ROUTES.telegram_alert, "_get_bot_token", lambda: TOKEN)


def make_app(*, rate_limits: bool = False) -> Flask:
    app = Flask("notify-file-tests")
    app.config.update(TESTING=True, RATELIMIT_ENABLED=rate_limits)
    limiter.init_app(app)
    if rate_limits:
        limiter.reset()
    Api(app).add_namespace(ROUTES.api, path="/api/v1/telegram")
    return app


@pytest.fixture
def client(linked, telegram):
    return make_app().test_client()


def post(client, data: bytes = PDF, name: str = "SUDARSHAN_Day-report_20261109.pdf", **fields):
    form = {"apikey": API_KEY, "username": OWNER, "caption": "Day report", **fields}
    form = {key: value for key, value in form.items() if value is not None}
    form["file"] = (io.BytesIO(data), name)
    return client.post(PATH, data=form, content_type="multipart/form-data")


def refused(response, status: int, code: str) -> None:
    assert response.status_code == status, response.get_json()
    assert response.get_json() == {
        "status": "error", "code": code, "message": report_files.WORDS[code],
    }  # fmt: skip


# -- the rules of a file -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "kind"),
    [("a.pdf", "pdf"), ("A_b-1.2.CSV", "csv"), ("chart.png", "png"), ("x" * 76 + ".pdf", "pdf")],
)
def test_names_the_route_takes(name, kind):
    assert report_files.kind_of(name).extension == kind


@pytest.mark.parametrize(
    "name",
    [
        "रिपोर्ट.pdf",  # not ASCII
        "रपट_२०२६.pdf",  # Devanagari letters and digits: word characters, not ASCII ones
        "café.pdf",
        "１２.pdf",  # fullwidth digits
        "repórt.pdf",  # a combining mark
        "report.pdf\n",  # a newline after the extension: an anchored match with $ lets it in
        "../report.pdf",
        "report one.pdf",
        "x" * 77 + ".pdf",  # 81 characters
        "",
    ],
)
def test_names_the_route_refuses(name):
    assert report_files.kind_of(name) is None
    assert report_files.problem(name, PDF) == "name"


@pytest.mark.parametrize("name", ["report.txt", "report", ".pdf", "report.pdf.exe"])
def test_kinds_other_than_pdf_csv_and_png_are_refused(name):
    assert report_files.problem(name, PDF) == "kind"


def test_the_content_must_be_what_the_name_says():
    assert report_files.problem("chart.png", PDF) == "content"
    assert report_files.problem("statement.pdf", PNG) == "content"
    assert report_files.problem("statement.csv", b"date,net\n\x00\x01") == "content"
    assert report_files.problem("statement.csv", b"\xff\xfe\xfa") == "content"
    for name, data in (("a.pdf", PDF), ("a.png", PNG), ("a.csv", CSV)):
        assert report_files.problem(name, data) is None


def test_sizes_and_captions():
    assert report_files.problem("a.pdf", b"") == "empty"
    assert report_files.problem("a.pdf", PDF + b"0" * (report_files.MAX_BYTES - len(PDF))) is None
    big = PDF + b"0" * (report_files.MAX_BYTES - len(PDF) + 1)
    assert report_files.problem("a.pdf", big) == "size"
    assert report_files.problem("a.pdf", PDF, "c" * 1024) is None
    assert report_files.problem("a.pdf", PDF, "c" * 1025) == "caption"


def test_every_refusal_is_a_sentence_without_a_code_or_a_path():
    for code, words in report_files.WORDS.items():
        assert words[0].isupper() and words.endswith("."), code
        assert "/api" not in words and "HTTP" not in words, code
        assert re.search(r"\b[45][0-9]{2}\b", words) is None, code  # no status code


# -- refused in words, nothing sent ------------------------------------------------------------


@pytest.mark.parametrize("name", ["दिन-रिपोर्ट.pdf", "रपट_२०२६.pdf", "café.pdf"])
def test_a_non_ascii_name_is_refused_in_words(client, telegram, name):
    refused(post(client, name=name), 400, "name")
    assert telegram.requests == []


def test_a_file_over_10_mib_is_refused_in_words(client, telegram):
    big = PDF + b"0" * (report_files.MAX_BYTES - len(PDF) + 1)
    refused(post(client, data=big), 413, "size")
    assert telegram.requests == []


def test_a_body_over_the_limit_is_refused_before_it_is_read(client, telegram, monkeypatch):
    monkeypatch.setattr(report_files, "MAX_BODY_BYTES", 1024)
    read = []
    monkeypatch.setattr(ROUTES, "verify_api_key", lambda key: read.append(key) or True)
    refused(post(client, data=PDF + b"0" * 4096), 413, "size")
    assert read == [] and telegram.requests == []  # the form was never parsed


@pytest.mark.parametrize("key", [None, "", "test-api-key-wrong"])
def test_a_missing_or_wrong_key_is_refused_in_words(client, telegram, key):
    refused(post(client, apikey=key), 401, "key")
    assert telegram.requests == []


def test_the_key_may_come_in_the_header_as_at_notify(client, telegram):
    form = {"username": OWNER, "wait_for_delivery": "true", "file": (io.BytesIO(PDF), "a.pdf")}
    response = client.post(
        PATH, data=form, content_type="multipart/form-data", headers={"X-API-KEY": API_KEY}
    )
    assert response.status_code == 200 and len(telegram.requests) == 1


def test_a_stopped_bot_is_refused_as_notify_refuses(client, telegram, monkeypatch):
    monkeypatch.setattr(ROUTES.telegram_alert, "is_bot_active", lambda: False)
    refused(post(client), 409, "bot_stopped")
    assert telegram.requests == []


def test_a_user_not_linked_and_a_missing_username_are_refused_in_words(client, telegram):
    refused(post(client, username="someone-else"), 404, "not_linked")
    refused(post(client, username=None), 400, "username")
    assert telegram.requests == []


def test_no_file_an_empty_file_a_wrong_kind_and_a_long_caption(client, telegram):
    none = client.post(
        PATH, data={"apikey": API_KEY, "username": OWNER}, content_type="multipart/form-data"
    )
    refused(none, 400, "no_file")
    refused(post(client, data=b""), 400, "empty")
    refused(post(client, name="report.html"), 400, "kind")
    refused(post(client, name="chart.png"), 400, "content")
    refused(post(client, caption="c" * 1025), 400, "caption")
    assert telegram.requests == []


# -- sent with the right method ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "data", "method", "field", "mime"),
    [
        ("SUDARSHAN_Monthly-statement_202611.pdf", PDF, "sendDocument", "document",
         "application/pdf"),
        ("SUDARSHAN_Monthly-statement_202611.csv", CSV, "sendDocument", "document", "text/csv"),
        ("SUDARSHAN_Week-in-review_2026W46.png", PNG, "sendPhoto", "photo", "image/png"),
    ],
)  # fmt: skip
def test_each_kind_goes_to_its_method_with_its_bytes(
    client, telegram, name, data, method, field, mime
):
    response = post(client, data=data, name=name, wait_for_delivery="true")
    assert response.status_code == 200
    assert response.get_json() == {"status": "success", "message": "File sent successfully"}
    (sent,) = telegram.requests
    assert sent["path"] == f"/bot{TOKEN}/{method}"
    assert sent["form"] == {"chat_id": str(CHAT), "caption": "Day report"}
    assert sent["files"] == {field: (name, mime, data)}


def _week_in_review_png() -> bytes:
    """The week in review's file as the journal renders it (JB9: its equity curve, as a PNG)."""
    from sudarshan.journal.core import equity
    from sudarshan.journal.render.png import render_equity_png

    curve = equity.equity_curve([], [20261109, 20261110, 20261111, 20261112, 20261113], None)
    return render_equity_png(curve, "Week in review", "Mon 9 Nov - Fri 13 Nov 2026")


@pytest.mark.parametrize(
    ("kind", "period", "ext", "method", "field", "mime"),
    [
        ("weekly", "2026W46", "png", "sendPhoto", "photo", "image/png"),
        ("monthly", "202611", "pdf", "sendDocument", "document", "application/pdf"),
    ],
)  # fmt: skip
def test_the_report_desk_sends_the_weeks_chart_as_a_photo_and_a_pdf_as_a_document(
    client, telegram, monkeypatch, tmp_path, kind, period, ext, method, field, mime
):
    """The journal's report as JB9 keeps it, through the report desk itself (its sender, the
    multipart it builds) and this route: the week's equity-curve PNG reaches Telegram by
    sendPhoto and the month's PDF by sendDocument, each under the journal's own name and type."""
    import asyncio
    import base64
    import hashlib
    from datetime import datetime

    from sudarshan.alerts import outbox, reports_desk
    from sudarshan.alerts.owner import Owner
    from sudarshan.alerts.store import Store
    from sudarshan.autologin.messages import BRAND
    from sudarshan.autologin.schedule import IST
    from sudarshan.journal.render.doc import pdf_file_name
    from sudarshan.ops.events import record

    data = _week_in_review_png() if ext == "png" else PDF
    name = pdf_file_name(kind, period, ext)
    sha = hashlib.sha256(data).hexdigest()
    spool = tmp_path / "journal-events.jsonl"
    assert record(
        spool,
        kind="report",
        event_id="journal-report-9",
        at=time.time(),
        run="journal-report-9",
        src="journal",
        report="9",
        report_kind=kind,
        sha256=sha,
    )
    told: list[dict] = []

    class Journal:  # the journal's three delivery ops, answering as JB9's rows do
        async def call(self, op, args):
            if op == "report_get":
                return "ok", {"id": 9, "kind": kind, "text": f"The {kind} report."}
            if op == "report_file":
                return "ok", {"file_name": name, "file_mime": mime, "file_sha256": sha,
                              "file_b64": base64.b64encode(data).decode()}  # fmt: skip
            told.append(args)
            return "ok", {"id": 9, "version": 2}

    async def text_sent(text):
        return outbox.SENT, ""

    def through_the_route(request: httpx.Request) -> httpx.Response:
        answer = client.post(request.url.path, data=request.read(),
                             content_type=request.headers["content-type"])  # fmt: skip
        return httpx.Response(answer.status_code, content=answer.get_data(),
                              headers={"content-type": "application/json"})  # fmt: skip

    monkeypatch.setattr(reports_desk.loopback, "refusal", lambda url, tables=None: None)
    monkeypatch.setattr(outbox, "MIN_GAP_S", 0.01)

    async def deliver():
        store = Store(tmp_path / "alerts.db")
        owner = Owner(OWNER, API_KEY)
        async with httpx.AsyncClient(transport=httpx.MockTransport(through_the_route)) as http:
            files = reports_desk.OpenAlgoFiles("http://127.0.0.1:5000", http, lambda: owner,
                                               lambda: owner)  # fmt: skip
            desk = reports_desk.ReportDesk(spool, store, Journal(), text_sent, files, lambda: True,
                                           lambda: datetime.now(IST), outbox.Pacer())  # fmt: skip
            await desk.sweep()
        store.close()

    asyncio.run(deliver())
    (sent,) = telegram.requests
    assert sent["path"] == f"/bot{TOKEN}/{method}"
    assert sent["files"] == {field: (name, mime, data)}
    assert sent["form"] == {
        "chat_id": str(CHAT),
        "caption": f"{BRAND}: {reports_desk.TITLES[kind]}",
    }
    assert told == [{"report_id": 9, "channel": "telegram", "status": "sent", "reason": None}]


def test_send_file_sync_alone_posts_multipart_and_answers_true(telegram, monkeypatch):
    service = alert_service.TelegramAlertService()
    monkeypatch.setattr(service, "_get_bot_token", lambda: TOKEN)
    assert service.send_file_sync(CHAT, "a.png", PNG) is True
    (sent,) = telegram.requests
    assert sent["path"].endswith("/sendPhoto") and sent["form"] == {"chat_id": str(CHAT)}


def test_send_file_sync_has_its_30_second_timeout(monkeypatch):
    seen = {}

    class Recorder:
        def post(self, url, **kwargs):
            seen.update(kwargs)
            return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(alert_service, "_http_client", Recorder())
    service = alert_service.TelegramAlertService()
    monkeypatch.setattr(service, "_get_bot_token", lambda: TOKEN)
    assert service.send_file_sync(CHAT, "a.pdf", PDF) is True
    assert seen["timeout"] == 30.0


def test_send_file_sync_without_a_token_or_with_a_bad_name_sends_nothing(telegram, monkeypatch):
    service = alert_service.TelegramAlertService()
    monkeypatch.setattr(service, "_get_bot_token", lambda: None)
    assert service.send_file_sync(CHAT, "a.pdf", PDF) is False
    monkeypatch.setattr(service, "_get_bot_token", lambda: TOKEN)
    assert service.send_file_sync(CHAT, "a.exe", PDF) is False
    assert telegram.requests == []


def test_telegram_refusing_the_file_is_told_and_nothing_is_queued(client, telegram, monkeypatch):
    queued = []
    monkeypatch.setattr(alert_service, "add_notification", lambda *a, **k: queued.append(a))
    telegram.status = 400
    refused(post(client, wait_for_delivery="true"), 502, "not_taken")
    assert len(telegram.requests) == 1 and queued == []


def test_telegram_not_answering_is_the_same_refusal(linked, monkeypatch):
    def timeout(request):
        raise httpx.ReadTimeout("timed out", request=request)

    monkeypatch.setattr(
        alert_service, "_http_client", httpx.Client(transport=httpx.MockTransport(timeout))
    )
    refused(post(make_app().test_client(), wait_for_delivery="true"), 502, "not_taken")


def test_without_wait_for_delivery_the_file_goes_on_the_alert_executor(client, telegram):
    response = post(client)
    assert response.status_code == 200
    assert response.get_json() == {"status": "success", "message": "File queued for delivery"}
    deadline = time.monotonic() + 5
    while not telegram.requests and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(telegram.requests) == 1  # sent by one of the existing telegram_alert threads


# -- its own limiter ---------------------------------------------------------------------------


def test_the_route_has_its_own_limit_of_30_a_minute(linked, telegram):
    app = make_app(rate_limits=True)
    client = app.test_client()
    for _ in range(30):  # /notify's own limit is spent; /notify-file's is not touched by it
        client.post("/api/v1/telegram/notify", json={"apikey": "wrong"})
    assert client.post("/api/v1/telegram/notify", json={"apikey": "wrong"}).status_code == 429
    codes = [post(client, wait_for_delivery="true").status_code for _ in range(31)]
    assert codes[:30] == [200] * 30 and codes[30] == 429
    assert len(telegram.requests) == 30


# -- descriptors -------------------------------------------------------------------------------


def test_large_uploads_leave_no_descriptor_behind(client, telegram):
    """Above 500 KB werkzeug spools an upload to a temporary file: each one must close with its
    request, on the path that sends and on the path that refuses after the form was read."""
    big = PDF + b"0" * 600_000
    assert post(client, data=big, wait_for_delivery="true").status_code == 200  # warm-up
    watch = FdWatch()
    for _ in range(60):
        assert post(client, data=big, wait_for_delivery="true").status_code == 200
    for _ in range(20):
        assert post(client, data=big, name="bad name.pdf").status_code == 400
    watch.assert_clean()
    assert len(telegram.requests) == 61


# -- what the route never does -----------------------------------------------------------------


def test_the_route_reads_no_file_from_disk_and_builds_no_loop_or_thread():
    tree = ast.parse((ROOT / "restx_api" / "telegram_bot.py").read_text(encoding="utf-8"))
    route = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "SendFileNotification"
    )  # fmt: skip
    called = {ast.unparse(node.func) for node in ast.walk(route) if isinstance(node, ast.Call)}
    assert not {"open", "send_file", "Path", "os.open", "io.open"} & called
    source = ast.unparse(route)
    for word in ("asyncio", "Thread(", "ThreadPoolExecutor(", "Lock("):
        assert word not in source
    assert "alert_executor.submit" in source and "send_file_sync" in source


def test_notify_keeps_its_one_gated_handler():
    """Upstream's contract test finds /notify by its docstring; this route must not match it."""
    tree = ast.parse((ROOT / "restx_api" / "telegram_bot.py").read_text(encoding="utf-8"))
    handlers = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and "notification to a specific user" in (ast.get_docstring(node) or "")
    ]  # fmt: skip
    assert len(handlers) == 1
