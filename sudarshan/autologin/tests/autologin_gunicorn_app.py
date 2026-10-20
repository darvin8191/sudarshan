"""WSGI app for test_autologin_gunicorn.py: the real blueprint under a real gunicorn worker.

Imported by gunicorn (after eventlet's monkey-patching, for that worker class). Settings come
from the environment the test sets; everything that would leave the machine is replaced, as in
autologin_app.stub_outside_world, and two test-only routes are added (login, CSRF token).
"""

from __future__ import annotations

import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False  # never read an operator's .env
dotenv.main.load_dotenv = dotenv.load_dotenv

from autologin_app import OWNER, add_owner, make_app  # noqa: E402

import blueprints.sudarshan_autologin as bp_module  # noqa: E402
import database.cache_invalidation as cache_invalidation  # noqa: E402
import services.order_update_service as order_updates  # noqa: E402
import services.telegram_alert_service as telegram_alerts  # noqa: E402
import websocket_proxy.broker_factory as broker_factory  # noqa: E402
from database import auth_db, user_db  # noqa: E402


def _quiet(*args, **kwargs):
    return True


cache_invalidation.publish_all_cache_invalidation = _quiet
broker_factory.cleanup_pools_for_user = lambda *a, **k: 0
order_updates.start_order_update_adapter = _quiet
order_updates.stop_order_update_adapter = _quiet
bp_module.should_download_master_contract = lambda broker: (False, "test")
bp_module.try_start_cached_master_contract_load = _quiet
bp_module.try_start_master_contract_download = _quiet
telegram_alerts.TelegramAlertService.is_bot_active = lambda self: False


class _NoSocket:
    def emit(self, *args, **kwargs):
        return None


bp_module.socketio = _NoSocket()

auth_db.Base.metadata.create_all(auth_db.engine)
user_db.Base.metadata.create_all(user_db.engine)
if user_db.find_user_by_username() is None:
    add_owner()
auth_db.db_session.remove()

app = make_app()


@app.route("/login-for-tests", methods=["GET"])
def login_for_tests():
    from datetime import datetime

    import pytz
    from flask import session

    session["user"] = OWNER
    session["logged_in"] = True
    session["login_time"] = datetime.now(pytz.timezone("Asia/Kolkata")).isoformat()
    return {"status": "success"}
