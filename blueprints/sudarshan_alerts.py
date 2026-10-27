"""SUDARSHAN (M10): the Alerts page's bridge to market alerts in sudarshan-alerts.

This blueprint is a SUDARSHAN addition to OpenAlgo; market alerts themselves run in the
sudarshan-alerts service (``sudarshan/alerts/README.md``, "Market alerts"). It keeps no state of
its own: every request is checked here and forwarded, sealed, to the service's local API on
127.0.0.1:5582 (``sudarshan/alerts/market/api.py``), whose answer comes back as it is.

Owner routes (OpenAlgo session, CSRF, the owner himself, and a limiter on each):
    GET    /sudarshan/alerts/api/state
    POST   /sudarshan/alerts/api/alerts                {"alert": {...}}
    PATCH  /sudarshan/alerts/api/alerts/<id>           {"version", "action", "alert"}
    DELETE /sudarshan/alerts/api/alerts/<id>
    GET    /sudarshan/alerts/api/events?before=&limit=&alert=
    GET    /sudarshan/alerts/api/events/since?after=&limit=
    GET    /sudarshan/alerts/api/prefs                 PUT {"telegram": bool, "app": bool}
    POST   /sudarshan/alerts/api/test

The service's route (no session and no CSRF cookie: its body is sealed under ALERTS_PUSH_INFO,
which only a process that can read OpenAlgo's .env can make; 404 to anything that came through
nginx; no rate limit, as at the auto-login's hand-off):
    POST   /sudarshan/alerts/internal/push             {"items": [{"event", "data"}]} sealed

The owner's room (M10.7): each open app of the owner emits ``sudarshan_alerts_join`` and joins
``sudarshan:alerts:owner`` when its session is the owner's; a push's items are emitted to that room
only, never broadcast, and the answer is how many screens are in it, which the service records as
the app delivery ("Shown on 2 open screens", "No app was open").

How it forwards:
- A new or edited alert gets its instrument key, tick size and expiry from the master contract
  (``get_symbol_info``), never from the page, and only with an Upstox login: the feed is
  Upstox's.
- Each body is one Fernet token under ``ALERTS_API_INFO`` (60 s, once), posted through the
  shared httpx client with an explicit timeout: 3 s, 6 s for a change, 12 s for the test. Under
  eventlet that is a green socket wait that yields to the hub; under gthread it holds one pool
  thread for at most that. No thread, scheduler or event loop is added to the app.
- First, who listens on the port is looked up (``sudarshan.alerts.loopback``): nothing goes to a
  port another user's program holds, and nobody listening answers at once.
- Whether market alerts are on is the service's own ``/health`` (cross-design R3), never a
  second switch read here.
"""

import os
import re
import time
from datetime import datetime
from functools import wraps

import httpx
from flask import Blueprint, jsonify, request, session
from flask_socketio import join_room

from database.token_db_enhanced import get_symbol_info
from database.user_db import find_user_by_username
from extensions import emit_from_any_thread, socketio
from limiter import limiter
from sudarshan.alerts import loopback
from sudarshan.alerts.market.api import off_state
from sudarshan.autologin.config import is_loopback_url
from sudarshan.autologin.handoff import (
    SEAL_TTL_S,
    HandoffRejected,
    NonceBook,
    NoncesFull,
    open_sealed,
    seal,
)
from sudarshan.feed.subscriptions import KEY_RE
from sudarshan.vault import ALERTS_API_INFO, ALERTS_PUSH_INFO, fernet_for, pepper_from
from utils import real_threading
from utils.httpx_client import get_httpx_client
from utils.lazy import LazyInit
from utils.logging import get_logger
from utils.session import check_session_validity, is_session_valid
from utils.thread_safe_cache import MISSING, LockedTTLCache

logger = get_logger(__name__)

sudarshan_alerts_bp = Blueprint("sudarshan_alerts", __name__, url_prefix="/sudarshan/alerts")

BROKER = "upstox"
SERVICE_URL = "http://127.0.0.1:5582"
PRINCIPAL = "owner"
CONNECT_TIMEOUT_S = 1.0
HEALTH_TIMEOUT_S = 3.0
#: Per op: a change may wait for a claim at the feed (5 s), the test for Telegram (10 s).
TIMEOUTS = {
    "state": 3.0, "create": 6.0, "update": 6.0, "delete": 3.0, "events": 3.0,
    "events_since": 3.0, "prefs_get": 3.0, "prefs_set": 3.0, "test": 12.0,
}  # fmt: skip
#: What the page may set; the instrument's key, tick size and expiry come from the master.
DRAFT = ("kind", "params", "frequency", "candle", "cooldown_s", "ends", "note", "channels",
         "price")  # fmt: skip
WORDS = {
    "owner_only": "Only the account owner can manage market alerts.",
    "not_answering": "The alert service is not answering. Your alerts are kept; checking "
    "resumes when it is back.",
    "held": "Another program on this server holds the alert service's port, so nothing was sent "
    "to it. Stop that program, then restart sudarshan-alerts.",
    "sealed": "The alert service could not confirm that this request came from OpenAlgo. If "
    "OpenAlgo's .env was changed, restart sudarshan-alerts.",
    "broker": "Market alerts work with an Upstox login only.",
    "instrument": "Pick the instrument again from the search.",
    "draft": "The alert could not be read. Open the form again, then save.",
    "busy": "Too many requests. Wait a minute, then try again.",
}

#: Every open app of the owner joins it (M10.7); nothing is broadcast.
ROOM = "sudarshan:alerts:owner"
NAMESPACE = "/"
PUSH_EVENTS = frozenset({"sudarshan_market_alert", "sudarshan_market_alerts_state"})
#: The largest sealed push this route takes, and its items (cross-design X9).
MAX_PUSH_BYTES = 128 * 1024
MAX_PUSH_ITEMS = 50
#: Set by nginx on every proxied location: a request carrying any of them did not come from this
#: server's sudarshan-alerts, which calls 127.0.0.1:5000 directly.
PROXY_HEADERS = (
    "X-Forwarded-For",
    "X-Real-IP",
    "Forwarded",
    "X-Forwarded-Proto",
    "X-Forwarded-Host",
)

_fernet_lazy = LazyInit(
    lambda: fernet_for(pepper_from(os.environ), ALERTS_API_INFO), "sudarshan-alerts-seal"
)
_push_fernet_lazy = LazyInit(
    lambda: fernet_for(pepper_from(os.environ), ALERTS_PUSH_INFO), "sudarshan-alerts-push"
)
# Pushes come at most every 50 ms: the book holds 2 x 60 s of twice that (X9) and answers 503
# past it rather than forget a nonce that could still be replayed.
_push_nonces = NonceBook(2 * SEAL_TTL_S * 40, lock=real_threading.RLock())
# Which problems have been logged in the last 10 minutes: one line each, not one per request.
_warned = LockedTTLCache(maxsize=8, ttl=600)


def _fernet():
    return _fernet_lazy.get()


def _push_fernet():
    return _push_fernet_lazy.get()


def _service_url() -> str:
    """The service's local API: the setting when it is plain http on this server, else 5582."""
    url = (os.environ.get("SUDARSHAN_ALERTS_API_URL") or SERVICE_URL).rstrip("/")
    return url if url.startswith("http://") and is_loopback_url(url) else SERVICE_URL


def _configured_broker() -> str:
    """The broker this OpenAlgo logs in to, read the way the login page reads it."""
    match = re.search(r"/([^/]+)/callback$", os.getenv("REDIRECT_URL", "") or "")
    return match.group(1).lower() if match else ""


def _warn_once(kind: str, message: str, *args) -> None:
    with _warned.lock:
        first = _warned.get(kind, MISSING) is MISSING
        if first:
            _warned[kind] = True
    if first:
        logger.warning(message, *args)


def _error(code: str, status: int, field: str | None = None):
    body = {"status": "error", "code": code, "message": WORDS[code]}
    if field is not None:
        body["field"] = field
    return jsonify(body), status


def _owner_only(view):
    """The logged-in session must be OpenAlgo's owner (admin) account; anyone else is refused
    on every route, reading included (a family viewer never holds the owner's alerts)."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        owner = find_user_by_username()
        if owner is None or session.get("user") != owner.username:
            return _error("owner_only", 403)
        return view(*args, **kwargs)

    return wrapper


@sudarshan_alerts_bp.errorhandler(429)
def _rate_limited(error):
    return _error("busy", 429)


# -- the service ----------------------------------------------------------------------------


def _listener():
    """None when a request may go to the service's port; else the answer to give instead."""
    held = loopback.holder(_service_url())
    if held == loopback.NOBODY:
        _warn_once("nobody", "Market alerts: nothing listens on sudarshan-alerts' port")
        return _error("not_answering", 503)
    if held == loopback.OTHER:
        _warn_once("other", "Market alerts: %s; nothing was sent", loopback.refusal(_service_url()))
        return _error("held", 503)
    return None  # this user's own program, or no way to tell (no /proc): the seal still holds


def _health():
    """The service's own word on whether market alerts are on (cross-design R3): its /health,
    or the answer to give when it does not reply."""
    refused = _listener()
    if refused is not None:
        return None, refused
    try:
        answer = get_httpx_client().get(
            f"{_service_url()}/health",
            headers={"Accept": "application/json"},
            timeout=httpx.Timeout(HEALTH_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
        )
        health = answer.json()
    except (httpx.HTTPError, ValueError):
        _warn_once("health", "Market alerts: sudarshan-alerts did not answer its health check")
        return None, _error("not_answering", 503)
    if answer.status_code != 200 or not isinstance(health, dict):
        return None, _error("not_answering", 503)
    return health, None


def _call(op: str, fields: dict | None = None):
    """One sealed op at the service; its answer as it came, or the bridge's own in words."""
    refused = _listener()
    if refused is not None:
        return refused
    blob = seal(_fernet(), op, principal=PRINCIPAL, **(fields or {}))
    try:
        answer = get_httpx_client().post(
            f"{_service_url()}/v1/{op}",
            content=blob,
            headers={"Content-Type": "application/octet-stream", "Accept": "application/json"},
            timeout=httpx.Timeout(TIMEOUTS[op], connect=CONNECT_TIMEOUT_S),
        )
        body = answer.json()
    except (httpx.HTTPError, ValueError):
        _warn_once("call", "Market alerts: sudarshan-alerts did not answer a %s request", op)
        return _error("not_answering", 503)
    if not isinstance(body, dict) or body.get("status") not in ("success", "error"):
        logger.error("Market alerts: sudarshan-alerts answered %s without its envelope", op)
        return _error("not_answering", 503)
    if answer.status_code == 403 and body.get("code") in ("forbidden", "principal"):
        logger.error("Market alerts: sudarshan-alerts refused OpenAlgo's sealed request (%s); "
                     "the two may hold different peppers", body.get("code"))  # fmt: skip
        return _error("sealed", 503)
    return jsonify(body), answer.status_code


def _enriched(draft):
    """(fields, None) for a draft with its instrument taken from the master contract, or
    (None, the answer) when it cannot be."""
    if not isinstance(draft, dict):
        return None, _error("draft", 400)
    if _configured_broker() != BROKER:
        return None, _error("broker", 409)
    symbol, exchange = draft.get("symbol"), draft.get("exchange")
    if not isinstance(symbol, str) or not isinstance(exchange, str):
        return None, _error("instrument", 400, "instrument")
    info = get_symbol_info(symbol.strip(), exchange.strip()) if 0 < len(symbol) <= 60 else None
    token = getattr(info, "token", None)
    if info is None or not isinstance(token, str) or not KEY_RE.fullmatch(token):
        return None, _error("instrument", 400, "instrument")
    fields = {name: draft[name] for name in DRAFT if name in draft}
    fields.update(
        symbol=info.symbol,
        exchange=info.exchange,
        instrument_key=token,
        tick_size=str(info.tick_size) if info.tick_size else None,
        expiry=_iso_expiry(info.expiry),
    )
    return fields, None


def _iso_expiry(value) -> str | None:
    """'28-NOV-26' (the master's form) -> '2026-11-28'; None for none or one it cannot read."""
    if not value:
        return None
    for form in ("%d-%b-%y", "%d-%b-%Y"):
        try:
            return datetime.strptime(str(value).strip(), form).date().isoformat()
        except ValueError:
            continue
    return None


# -- owner routes -----------------------------------------------------------------------------


@sudarshan_alerts_bp.route("/api/state", methods=["GET"])
@check_session_validity
@_owner_only
@limiter.limit("120 per minute")
def state():
    health, refused = _health()
    if refused is not None:
        return refused
    if health.get("market_alerts") == "off":
        return jsonify({"status": "success", "data": off_state(int(time.time() * 1000))})
    return _call("state")


@sudarshan_alerts_bp.route("/api/alerts", methods=["POST"])
@check_session_validity
@_owner_only
@limiter.limit("30 per minute")
def create_alert():
    body = request.get_json(silent=True)
    fields, refused = _enriched(body.get("alert") if isinstance(body, dict) else None)
    if refused is not None:
        return refused
    return _call("create", {"alert": fields})


@sudarshan_alerts_bp.route("/api/alerts/<int:alert_id>", methods=["PATCH"])
@check_session_validity
@_owner_only
@limiter.limit("30 per minute")
def change_alert(alert_id: int):
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    fields = {"id": alert_id, "version": body.get("version"), "action": body.get("action")}
    if body.get("action") == "edit":
        fields["alert"], refused = _enriched(body.get("alert"))
        if refused is not None:
            return refused
    return _call("update", fields)


@sudarshan_alerts_bp.route("/api/alerts/<int:alert_id>", methods=["DELETE"])
@check_session_validity
@_owner_only
@limiter.limit("30 per minute")
def delete_alert(alert_id: int):
    return _call("delete", {"id": alert_id})


@sudarshan_alerts_bp.route("/api/events", methods=["GET"])
@check_session_validity
@_owner_only
@limiter.limit("120 per minute")
def events():
    args = request.args
    return _call("events", {"before": args.get("before", type=int),
                            "limit": args.get("limit", type=int),
                            "alert": args.get("alert", type=int)})  # fmt: skip


@sudarshan_alerts_bp.route("/api/events/since", methods=["GET"])
@check_session_validity
@_owner_only
@limiter.limit("120 per minute")
def events_since():
    args = request.args
    return _call("events_since", {"after": args.get("after", type=int),
                                  "limit": args.get("limit", type=int)})  # fmt: skip


@sudarshan_alerts_bp.route("/api/prefs", methods=["GET"])
@check_session_validity
@_owner_only
@limiter.limit("120 per minute")
def prefs():
    return _call("prefs_get")


@sudarshan_alerts_bp.route("/api/prefs", methods=["PUT"])
@check_session_validity
@_owner_only
@limiter.limit("30 per minute")
def set_prefs():
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    return _call("prefs_set", {"telegram": body.get("telegram"), "app": body.get("app")})


@sudarshan_alerts_bp.route("/api/test", methods=["POST"])
@check_session_validity
@_owner_only
@limiter.limit("6 per minute")
def send_test():
    return _call("test")


# -- the service's push and the owner's room (M10.7) ------------------------------------------


def _screens() -> int:
    """How many open apps are in the owner's room. One worker and no message queue, so every
    client of this instance is in this process's manager and the count is exact (as
    services/strategy_module/broadcast.py reads it)."""
    server = getattr(socketio, "server", None)
    rooms = getattr(getattr(server, "manager", None), "rooms", None)
    if not isinstance(rooms, dict):
        return 0
    members = rooms.get(NAMESPACE, {}).get(ROOM)
    try:
        return len(members) if members is not None else 0
    except TypeError:
        return 0


def _refused(status: int, message: str):
    return jsonify({"status": "error", "message": message}), status


@sudarshan_alerts_bp.route("/internal/push", methods=["POST"])
def internal_push():
    """sudarshan-alerts' items for the open apps: each emitted to the owner's room only, inside
    this request, which is safe in every runtime (extensions.emit_from_any_thread); the answer is
    the room's size. Never retried by the service: a late toast is no use."""
    if any(header in request.headers for header in PROXY_HEADERS):
        return _refused(404, "Not found")
    length = request.content_length
    if length is None:
        return _refused(411, "Length required")
    if length > MAX_PUSH_BYTES:
        return _refused(413, "Too large")
    try:
        message = open_sealed(_push_fernet(), request.get_data(cache=False), op="push",
                              seen=_push_nonces, max_bytes=MAX_PUSH_BYTES)  # fmt: skip
    except NoncesFull:
        _warn_once("push-busy", "Market alerts: too many pushes in the last two minutes")
        return _refused(503, "Busy")
    except HandoffRejected as exc:
        _warn_once("push-refused", "Market alerts: refused a push that was not sealed by this "
                   "server's sudarshan-alerts (%s)", exc)  # fmt: skip
        return _refused(403, "Forbidden")
    items = message.get("items")
    if not isinstance(items, list) or len(items) > MAX_PUSH_ITEMS:
        return _refused(400, "Bad push")
    screens = _screens()
    for item in items:
        if not isinstance(item, dict):
            continue
        event, data = item.get("event"), item.get("data")
        if event in PUSH_EVENTS and isinstance(data, dict):
            emit_from_any_thread(event, data, to=ROOM, namespace=NAMESPACE)
    return jsonify({"status": "success", "screens": screens})


@socketio.on("sudarshan_alerts_join")
def _join_owner_room(data=None):
    """An open app joins the owner's room, if its session is the owner's (the default namespace
    has no connect-time check, so the join is where it is made)."""
    owner = find_user_by_username()
    if not is_session_valid() or owner is None or session.get("user") != owner.username:
        return {"ok": False, "message": WORDS["owner_only"]}
    join_room(ROOM)
    return {"ok": True}


@sudarshan_alerts_bp.record_once
def _exempt_the_push_from_csrf(state):
    """The service has no browser session to forge; its push is authenticated by the seal."""
    csrf = state.app.extensions.get("csrf") or getattr(state.app, "csrf", None)
    if csrf is not None:
        csrf.exempt(internal_push)
