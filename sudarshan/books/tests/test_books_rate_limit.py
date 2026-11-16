"""A rate-limited exit or cancel answers the app's own refusal, not a page (v0.13.0 verify N4).

The review read `app.py`, whose handler redirects a limited request outside `/api/` to the
rate-limit page; the phone would follow that to a page under 200 and hold the exit as "no
answer". The orders blueprint has a handler of its own, which wins for its routes, so the
answer was already JSON with 429. These pin that (with `app.py`'s own handler registered beside
it, cut out of the file, so the precedence is the real one) and the sentence a trader reads.
The limit is raised by a hook before the view, as the limiter does: nothing is sent.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest
from flask import Blueprint, Flask, abort, request

from sudarshan.books import rate_limit

ROOT = Path(__file__).resolve().parents[3]
SENTENCE = "Too many tries. Wait a moment, then try again."
ORDER_WRITES = (
    "/close_position", "/close_all_positions", "/cancel_order", "/cancel_all_orders",
    "/modify_order", "/modify_gtt_order", "/cancel_gtt_order",
)  # fmt: skip


def apps_handler():
    """`rate_limit_exceeded` as `app.py` writes it."""
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    (found,) = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "rate_limit_exceeded"
    ]
    found.decorator_list = []
    scope: dict = {"logger": logging.getLogger("test-app")}
    exec(compile(ast.Module([found], []), "app.py", "exec"), scope)  # noqa: S102
    return scope["rate_limit_exceeded"]


@pytest.fixture
def client():
    import blueprints.orders as orders

    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(orders.orders_bp)
    other = Blueprint("other_bp", __name__)
    other.add_url_rule("/api/v1/placeorder", "api", lambda: "sent", methods=["POST"])
    other.add_url_rule("/some/page", "page", lambda: "shown")
    app.register_blueprint(other)
    app.register_error_handler(429, apps_handler())
    reached = []

    @app.before_request
    def limited():
        reached.append(request.path)
        abort(429)

    made = app.test_client()
    made.reached = reached
    return made


@pytest.mark.parametrize("path", ORDER_WRITES)
def test_a_limited_order_write_answers_the_apps_own_refusal_with_429(client, path):
    answer = client.post(path, json={"symbol": "SBIN", "exchange": "NSE", "product": "CNC"})
    assert client.reached == [path], "the route exists and the limit answered before its view"
    assert answer.status_code == 429
    assert answer.headers.get("Location") is None
    assert answer.get_json() == {"status": "error", "message": SENTENCE}


def test_the_api_keeps_its_own_answer_and_a_page_its_redirect(client):
    api = client.post("/api/v1/placeorder", json={})
    assert api.status_code == 429
    assert api.get_json() == {
        "status": "error",
        "message": "Rate limit exceeded. Please slow down your requests.",
        "retry_after": 60,
    }
    page = client.get("/some/page")
    assert page.status_code == 302 and page.headers["Location"] == "/rate-limited"


def test_the_sentence_is_a_traders():
    assert rate_limit.TOO_MANY == SENTENCE
    assert not any(ch.isdigit() for ch in SENTENCE), "a sentence, no status code"
    assert "limit" not in SENTENCE.lower() and "request" not in SENTENCE.lower()
