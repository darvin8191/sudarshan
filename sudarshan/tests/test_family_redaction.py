"""Review L8, OpenAlgo's half: a family invite link carries its one-time code in the path, and
OpenAlgo's traffic log (logs.db), its error log and its 404 and CSRF lines keep paths. The shared
redactor (utils/url_redaction.py) masks the code on both paths that name it, the shell route
/family/join/<code> and the family API route /family/api/join/<code>, so the code never reaches
logs.db. nginx's access log leaves both paths out (lane FO). The code here is synthetic.
"""

from __future__ import annotations

import json

import pytest
from flask import Flask

from utils import traffic_logger
from utils.url_redaction import redact_url_credentials

CODE = "Inv1teC0deSentinel-_x9Q"


@pytest.mark.parametrize(
    ("path", "kept"),
    [
        (f"/family/join/{CODE}", "/family/join/<redacted>"),
        (f"/family/api/join/{CODE}", "/family/api/join/<redacted>"),
        (f"/family/join/{CODE}?from=share", "/family/join/<redacted>?from=share"),
        (f"GET /family/api/join/{CODE} HTTP/1.1", "GET /family/api/join/<redacted> HTTP/1.1"),
    ],
)
def test_the_redactor_masks_an_invite_code(path, kept):
    assert redact_url_credentials(path) == kept
    assert CODE not in redact_url_credentials(f"https://sudarshan.example{path}")


@pytest.mark.parametrize(
    "path",
    ["/family/join", "/family/join/", "/family/positions", "/family/api/me", "/family-members",
     "/family/joined/abc", "/family/api/login"],
)  # fmt: skip
def test_a_path_that_names_no_invite_code_is_left_as_it_is(path):
    assert redact_url_credentials(path) == path


def test_the_traffic_log_never_writes_an_invite_code(monkeypatch):
    """The payload the traffic logger hands its logs.db writer, through the real middleware."""
    written: list[dict] = []
    monkeypatch.setattr(traffic_logger._traffic_log_executor, "submit",
                        lambda _write, payload: written.append(payload))  # fmt: skip
    app = Flask("sudarshan-family-redaction")

    @app.route("/family/<path:rest>")
    def shell(rest):
        return "the family shell"

    app.wsgi_app = traffic_logger.TrafficLoggerMiddleware(app.wsgi_app)
    client = app.test_client()
    for path in (f"/family/join/{CODE}", f"/family/api/join/{CODE}"):
        assert client.get(path).status_code == 200
    assert [p["path"] for p in written] == [
        "/family/join/<redacted>",
        "/family/api/join/<redacted>",
    ]
    assert CODE not in json.dumps(written, default=str)
