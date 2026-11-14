"""SUDARSHAN (M20.8, M17.9): the System page's API: the server's state in one place, and
"Disconnect the assistant".

This blueprint is a SUDARSHAN addition to OpenAlgo. Owner routes (OpenAlgo session + CSRF, the
owner account only, like the risk and auto-login routes):

    GET  /sudarshan/system/api/status                 what the watch last saw, and the assistant
    POST /sudarshan/system/api/assistant/disconnect   end every AI connection now

The status is file reads and one settings read, nothing else: ``report.json``, which
``sudarshan-watch`` writes every 2 minutes (``sudarshan/ops/watch``), the restore drill's
``drill.json``, the assistant's switch and locks from this process's own environment
(``sudarshan/assistant/locks.py``) and the owner's session count from OpenAlgo's own table. It
opens no connection, starts nothing and holds no lock, so it costs the worker a few
milliseconds under eventlet and under gthread alike. Each file is read without waiting and
without following a link (``sudarshan/ops/files.py``); one that is missing or cannot be used is
said as "not yet", never guessed.

Disconnect is ``sudarshan.assistant.cutoff.end_all_access``: every access token issued up to
now is refused at its next call, and every refresh token is revoked. It is how the owner ends
AI access while the risk guard's kill switch is at stage ``sandbox``, where it leaves the
assistant alone. Nothing brings the tokens back: the owner connects and approves again.
``WRITE_ROUTES`` lists the route that changes something, for the family layer's test.
"""

from __future__ import annotations

import json
import os
import time
from functools import wraps
from pathlib import Path

from flask import Blueprint, current_app, jsonify, session

from database.auth_db import get_active_sessions
from database.user_db import find_user_by_username
from limiter import limiter
from sudarshan.assistant import cutoff, locks
from sudarshan.ops.files import read_regular
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

sudarshan_system_bp = Blueprint("sudarshan_system", __name__, url_prefix="/sudarshan/system")

#: Every route that changes something (method, path), for the family layer's viewer test.
WRITE_ROUTES = (("POST", "/sudarshan/system/api/assistant/disconnect"),)

WATCH_DIR = "/var/lib/sudarshan-watch"
BACKUP_DIR = "/var/lib/sudarshan-backup"
MAX_REPORT = 1 << 18
MAX_DRILL = 1 << 14
#: The watch looks every 2 minutes; a report older than this is said to be old.
STALE_S = 600
REPORT_KEYS = ("v", "at", "release", "heartbeat", "checks")
CHECK_KEYS = ("name", "level", "told", "since", "facts")
DRILL_KEYS = ("at", "outcome", "item", "name", "words", "archive", "databases", "release")

OWNER_ONLY = "Only the owner's account can open the System page."
TOO_MANY = "Too many requests. Wait a minute, then try again."
DISCONNECTED = (
    "Every AI connection is ended. To use the assistant again, connect it again in Claude and "
    "approve it."
)
DISCONNECT_FAILED = (
    "The assistant's access could not be fully ended. Turn on the kill switch on the Risk "
    "page now: it ends the assistant's access too."
)


def _owner_only(view):
    """The logged-in session must be OpenAlgo's owner (admin) account."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        owner = find_user_by_username()
        if owner is None or session.get("user") != owner.username:
            return jsonify({"status": "error", "message": OWNER_ONLY}), 403
        return view(*args, **kwargs)

    return wrapper


@sudarshan_system_bp.errorhandler(429)
def _rate_limited(error):
    return jsonify({"status": "error", "message": TOO_MANY}), 429


def _folder(env_name: str, default: str) -> Path:
    return Path((os.environ.get(env_name) or default).split(":")[0])


def _document(path: Path, limit: int) -> dict | None:
    raw = read_regular(path, limit)
    try:
        found = json.loads(raw) if raw else None
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def watch_report(now: float) -> dict | None:
    """The watch's last report with only the fields the page shows, and how old it is; None
    when there is none yet or it cannot be used."""
    found = _document(_folder("SUDARSHAN_WATCH_STATE_DIR", WATCH_DIR) / "report.json", MAX_REPORT)
    if not found or not isinstance(found.get("checks"), list):
        return None
    at = found.get("at")
    if isinstance(at, bool) or not isinstance(at, int | float):
        return None
    report = {key: found.get(key) for key in REPORT_KEYS}
    report["checks"] = [
        {key: check.get(key) for key in CHECK_KEYS}
        for check in found["checks"]
        if isinstance(check, dict) and isinstance(check.get("name"), str)
    ]
    report["age_s"] = max(0, int(now - at))
    report["stale"] = report["age_s"] > STALE_S
    return report


def drill_result() -> dict | None:
    found = _document(_folder("SUDARSHAN_BACKUP_STATE_DIR", BACKUP_DIR) / "drill.json", MAX_DRILL)
    if not found or found.get("outcome") not in ("passed", "failed"):
        return None
    return {key: found.get(key) for key in DRILL_KEYS}


def assistant_state() -> dict:
    """The assistant's switch and locks, whether its routes are up in this process, and the
    address the owner types into Claude. No secret is in any of it."""
    state = locks.state(os.environ)
    public = (os.environ.get("MCP_PUBLIC_URL") or "").strip().rstrip("/")
    state["registered"] = "mcp_http_bp" in current_app.blueprints
    state["address"] = f"{public}/mcp" if public and state["running"] else ""
    return state


@sudarshan_system_bp.route("/api/status", methods=["GET"])
@check_session_validity
@_owner_only
@limiter.limit("60 per minute")
def status():
    try:
        sessions = len(get_active_sessions(session["user"]) or [])
    except Exception:
        logger.exception("The owner's sessions could not be counted for the System page")
        sessions = None
    response = jsonify(
        {
            "status": "success",
            "data": {
                "watch": watch_report(time.time()),
                "drill": drill_result(),
                "assistant": assistant_state(),
                "sessions": sessions,
            },
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@sudarshan_system_bp.route("/api/assistant/disconnect", methods=["POST"])
@check_session_validity
@_owner_only
@limiter.limit("5 per minute")
def disconnect():
    done = cutoff.end_all_access("sudarshan_disconnect")
    ok = bool(done["cutoff"]) and done["revoked"] is not None
    logger.info(f"Disconnect the assistant: cutoff={done['cutoff']} revoked={done['revoked']}")
    body = {
        "status": "success" if ok else "error",
        "message": DISCONNECTED if ok else DISCONNECT_FAILED,
        "data": {"ended": ok, "revoked": done["revoked"]},
    }
    return jsonify(body), 200 if ok else 500
