"""WSGI app for test_sessions_valid_after.py, under a real gunicorn.

Imported by gunicorn (after eventlet's monkey-patching, for that worker class), with its settings
from the environment the test sets. OpenAlgo's login and logout routes and Socket.IO are bound as
in app.py; test-only routes finish a device's login the way OpenAlgo does (handle_auth_success),
read behind the session check every page and read uses, and hand out a CSRF token. Nothing here
reaches a broker, the ZeroMQ bus or the master contract.
"""

from __future__ import annotations

import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False  # never read an operator's .env
dotenv.main.load_dotenv = dotenv.load_dotenv

from bridge_app import OWNER, make_app, reset_users  # noqa: E402
from flask import session  # noqa: E402

import blueprints.auth as auth_module  # noqa: E402
import database.cache_invalidation as cache_invalidation  # noqa: E402
import services.order_update_service as order_updates  # noqa: E402
import utils.auth_utils as auth_utils  # noqa: E402
import websocket_proxy.broker_factory as broker_factory  # noqa: E402
from database import auth_db  # noqa: E402
from extensions import socketio  # noqa: E402
from utils.session import check_session_validity  # noqa: E402
from utils.socketio_error_handler import init_socketio_error_handling  # noqa: E402

cache_invalidation.publish_all_cache_invalidation = lambda *a, **k: True
broker_factory.cleanup_pools_for_user = lambda *a, **k: 0
order_updates.start_order_update_adapter = lambda *a, **k: None
order_updates.stop_order_update_adapter = lambda *a, **k: None
auth_utils.should_download_master_contract = lambda broker: (False, "test")
auth_utils.try_start_cached_master_contract_load = lambda broker: None

auth_db.init_db()  # as app.py's start does, the new table included
reset_users()
app = make_app()
socketio.init_app(app)
init_socketio_error_handling(socketio)
app.register_blueprint(auth_module.auth_bp)


@app.teardown_appcontext
def remove_auth_session(exception=None):
    auth_db.db_session.remove()


@app.route("/login-for-tests", methods=["GET"])
def login_for_tests():
    session.clear()
    session["user"] = OWNER
    return auth_utils.handle_auth_success("fake-upstox-token", OWNER, "upstox")


@app.route("/read-for-tests", methods=["GET"])
@check_session_validity
def read_for_tests():
    return {"status": "success"}
