"""WSGI app for test_alerts_bridge_push.py and test_socketio_owner.py, under a real gunicorn.

Imported by gunicorn (after eventlet's monkey-patching, for that worker class), with its settings
from the environment the test sets. Socket.IO is bound as app.py binds it, with app.py's default
error handler and the /market namespace (imported, as app.py imports it). Test-only routes sign a
session in (as the owner, or as anyone else; dated before the daily expiry, or stopped before the
broker step), hand out a CSRF token, and publish an order on the event bus as the order service
does, with the Socket.IO subscriber subscribed as app.py subscribes it.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False  # never read an operator's .env
dotenv.main.load_dotenv = dotenv.load_dotenv

import pytz  # noqa: E402
from bridge_app import OWNER, make_app, reset_users  # noqa: E402
from flask import request, session  # noqa: E402

import blueprints.websocket_example  # noqa: E402,F401  (the /market namespace)
from events.order_events import OrderPlacedEvent  # noqa: E402
from extensions import socketio  # noqa: E402
from subscribers import socketio_subscriber  # noqa: E402
from utils.event_bus import bus  # noqa: E402
from utils.socketio_error_handler import init_socketio_error_handling  # noqa: E402

reset_users()
app = make_app()
socketio.init_app(app)
init_socketio_error_handling(socketio)
bus.subscribe("order.placed", socketio_subscriber.on_order_placed, "socketio:order_placed")


@app.route("/login-for-tests", methods=["GET"])
def login_for_tests():
    """half=1: the password step only; expired=1: logged in the day before (the test that asks
    sets the daily expiry to 00:00, so that is past it at any hour)."""
    session.clear()
    session["user"] = request.args.get("user", OWNER)
    if request.args.get("half"):
        return {"status": "success"}
    at = datetime.now(pytz.timezone("Asia/Kolkata"))
    if request.args.get("expired"):
        at -= timedelta(days=1)
    session["logged_in"] = True
    session["login_time"] = at.isoformat()
    return {"status": "success"}


@app.route("/order-event-for-tests", methods=["GET"])
def order_event_for_tests():
    bus.publish(OrderPlacedEvent(
        mode="live", strategy="sio1", symbol="SBIN", exchange="NSE", action="BUY", quantity=1,
        pricetype="MARKET", product="MIS", orderid=request.args.get("orderid", "SIO1-1"),
    ))  # fmt: skip
    return {"status": "success"}
