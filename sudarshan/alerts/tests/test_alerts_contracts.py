"""What this service and its fakes assume about OpenAlgo, read from OpenAlgo's own sources.

The fakes stand in for OpenAlgo's WebSocket proxy, its Telegram path and its REST books. Each
assumption they encode is checked here against the code that ships, without importing it, so an
upstream change to any of them fails a test instead of silently breaking the alerts.
"""

from __future__ import annotations

import ast
from pathlib import Path

from sudarshan.alerts.outbox import QUEUED

REPO = Path(__file__).resolve().parents[3]


def _function(path: str, name: str, cls: str | None = None) -> ast.FunctionDef:
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    scope = tree
    if cls is not None:
        scope = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == cls)
    return next(
        n
        for n in ast.walk(scope)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == name
    )


def _constant_dicts(node: ast.AST) -> list[dict]:
    """Every dict literal under ``node``, keeping its constant keys and constant values."""
    found = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Dict):
            found.append(
                {
                    k.value: (v.value if isinstance(v, ast.Constant) else ...)
                    for k, v in zip(sub.keys, sub.values, strict=True)
                    if isinstance(k, ast.Constant)
                }
            )
    return found


def _calls(node: ast.AST, name: str) -> list[ast.Call]:
    return [
        sub
        for sub in ast.walk(node)
        if isinstance(sub, ast.Call)
        and (getattr(sub.func, "attr", None) == name or getattr(sub.func, "id", None) == name)
    ]


# -- Telegram: POST /api/v1/telegram/notify ------------------------------------------------------


def test_notify_answers_delivered_or_queued_and_nothing_else_as_success():
    post = _function("restx_api/telegram_bot.py", "post", cls="SendNotification")
    successes = {d["message"] for d in _constant_dicts(post) if d.get("status") == "success"}
    assert successes == {"Notification sent successfully", "Notification queued for delivery"}
    assert QUEUED in "Notification queued for delivery".lower()
    assert QUEUED not in "Notification sent successfully".lower()
    source = ast.unparse(post)
    assert "409" in source and "is_bot_active" in source  # the bot stopped: refused, not queued
    assert "404" in source and "get_telegram_user_by_username" in source  # not linked
    assert "wait_for_delivery" in source


def test_queued_notifications_are_never_sent_later():
    """Why "queued" counts as not delivered: nothing in OpenAlgo reads that queue back."""
    callers = [
        path
        for path in REPO.rglob("*.py")
        if "get_pending_notifications(" in path.read_text(encoding="utf-8", errors="ignore")
        and "def get_pending_notifications" not in path.read_text(encoding="utf-8", errors="ignore")
        and "sudarshan" not in path.parts
        and ".venv" not in path.parts
    ]
    assert callers == []


# -- the WebSocket proxy -------------------------------------------------------------------------

PROXY = "websocket_proxy/server.py"


def test_the_proxy_maps_the_client_before_it_connects_the_broker():
    auth = _function(PROXY, "authenticate_client")
    mapped = [
        n.lineno
        for n in ast.walk(auth)
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Subscript)
        and ast.unparse(n.targets[0].value) == "self.user_mapping"
    ]
    broker = [
        c.lineno for c in _calls(auth, "get_broker_name") + _calls(auth, "create_broker_adapter")
    ]
    assert mapped and broker and min(mapped) < min(broker)


def test_the_proxy_refuses_a_bad_key_with_authentication_error():
    auth = _function(PROXY, "authenticate_client")
    codes = [ast.unparse(c.args[1]) for c in _calls(auth, "send_error") if len(c.args) > 1]
    assert "'AUTHENTICATION_ERROR'" in codes


def test_subscribing_to_orders_needs_only_the_mapping():
    subscribe = _function(PROXY, "subscribe_orders_client")
    refusing = [  # the checks that can turn the client away
        ast.unparse(n.test)
        for n in ast.walk(subscribe)
        if isinstance(n, ast.If) and any(isinstance(s, ast.Return) for s in ast.walk(n))
    ]
    assert refusing == ["client_id not in self.user_mapping"]
    assert "self.order_subscribers[user_id].add(client_id)" in ast.unparse(subscribe)


def test_the_proxy_relays_order_updates_as_they_are():
    relay = _function(PROXY, "_handle_order_update")
    assert "self.send_message(client_id, message)" in ast.unparse(relay)
    listener = ast.unparse(_function(PROXY, "zmq_listener"))
    assert "topic_str.endswith('_orders')" in listener


def test_the_order_update_message_carries_every_field_read_here():
    on_update = _function("subscribers/wsproxy_subscriber.py", "on_order_update")
    (payload,) = [d for d in _constant_dicts(on_update) if d.get("type") == "order_update"]
    read_here = {
        "type", "user_id", "mode", "orderid", "symbol", "exchange", "action", "quantity",
        "trigger_price", "pricetype", "product", "order_status", "filled_quantity",
        "average_price", "rejection_reason",
    }  # fmt: skip
    assert read_here <= set(payload)


# -- the Upstox order stream ---------------------------------------------------------------------

ADAPTER = "broker/upstox/streaming/upstox_order_adapter.py"


def test_the_upstox_statuses_the_trigger_rule_is_built_on():
    tree = ast.parse((REPO / ADAPTER).read_text(encoding="utf-8"))
    (status_map,) = [
        ast.literal_eval(n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == "_STATUS_MAP"
    ]
    assert status_map["trigger pending"] == "trigger pending"
    # A modify or a cancel of a stop order passes through "open" (orders.py, the 3 s rule).
    assert status_map["modify pending"] == "open" and status_map["cancel pending"] == "open"
    normalize = ast.unparse(_function(ADAPTER, "normalize"))
    assert "if order_status == 'rejected' else ''" in normalize  # reasons for rejections only


# -- the REST books after a pause ----------------------------------------------------------------


def test_the_books_say_when_they_are_the_sandbox():
    live = _function("services/orderbook_service.py", "get_orderbook_with_auth")
    answers = [d for d in _constant_dicts(live) if d.get("status") == "success"]
    assert answers and all("mode" not in d for d in answers)
    for path, name in (
        ("sandbox/order_manager.py", "get_orderbook"),
        ("sandbox/position_manager.py", "get_tradebook"),
    ):
        dicts = _constant_dicts(_function(path, name))
        assert any(d.get("status") == "success" and d.get("mode") == "analyze" for d in dicts)


def test_the_upstox_books_carry_the_fields_the_resync_reads():
    mapping = "broker/upstox/mapping/order_data.py"
    orders = set().union(*_constant_dicts(_function(mapping, "transform_order_data")))
    assert {"orderid", "order_status", "quantity", "symbol", "exchange", "action", "product",
            "pricetype", "trigger_price"} <= orders  # fmt: skip
    trades = set().union(*_constant_dicts(_function(mapping, "transform_tradebook_data")))
    assert {"orderid", "quantity", "average_price"} <= trades
