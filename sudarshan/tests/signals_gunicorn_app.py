"""WSGI app for test_signals_push_route.py, under a real gunicorn.

Imported by gunicorn (after eventlet's monkey-patching, for that worker class), with its settings
from the environment the test sets. Socket.IO is bound as app.py binds it, with app.py's default
error handler. A test-only route signs a session in, as the owner or as anyone else.
"""

from __future__ import annotations

from datetime import datetime

import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False  # never read an operator's .env
dotenv.main.load_dotenv = dotenv.load_dotenv

import pytz  # noqa: E402
from flask import request, session  # noqa: E402
from signals_bridge_app import OWNER, make_app, reset_users  # noqa: E402

from extensions import socketio  # noqa: E402
from utils.socketio_error_handler import init_socketio_error_handling  # noqa: E402

reset_users()
app = make_app()
socketio.init_app(app)
init_socketio_error_handling(socketio)


@app.route("/login-for-tests", methods=["GET"])
def login_for_tests():
    session.clear()
    session["user"] = request.args.get("user", OWNER)
    session["logged_in"] = True
    session["login_time"] = datetime.now(pytz.timezone("Asia/Kolkata")).isoformat()
    return {"status": "success"}
