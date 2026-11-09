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
  httpx client of its own (it never reads a proxy from the environment: the service is on this
  server) with an explicit timeout: 3 s, 6 s for a change, 12 s for the test. Under
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
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from database.token_db_enhanced import get_symbol_info
from database.user_db import find_user_by_username
from extensions import emit_from_any_thread, is_owner_session, socketio
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
from utils.lazy import LazyInit
from utils.logging import get_logger
from utils.session import check_session_validity
from utils.thread_safe_cache import MISSING, LockedTTLCache

logger = get_logger(__name__)

sudarshan_alerts_bp = Blueprint("sudarshan_alerts", __name__, url_prefix="/sudarshan/alerts")

BROKER = "upstox"
SERVICE_PORT = 5582
SERVICE_URL = f"http://127.0.0.1:{SERVICE_PORT}"
#: The largest body a route reads: an alert is a few hundred bytes (the service takes 64 KiB).
MAX_BODY_BYTES = 64 * 1024
PRINCIPAL = "owner"
CONNECT_TIMEOUT_S = 1.0
HEALTH_TIMEOUT_S = 3.0
#: Per op: a change may wait for a claim at the feed (5 s), the test for Telegram (10 s).
TIMEOUTS = {
    "state": 3.0, "create": 6.0, "update": 6.0, "delete": 6.0, "events": 3.0,
    "events_since": 3.0, "prefs_get": 3.0, "prefs_set": 6.0, "test": 12.0,
}  # fmt: skip
#: The codes of the contract (section 1) the service may answer with; any other (a transport
#: refusal such as 'size' or 'timeout') is the bridge's 500 'failed', in a sentence.
CODES = frozenset({
    "invalid", "instrument", "draft", "gone", "conflict", "refused", "limit", "feed", "off",
    "broker", "starting", "stopping", "stopped", "record", "not_answering", "held", "sealed",
    "busy", "failed",
})  # fmt: skip
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
    "failed": "Market alerts could not do that. Your alerts are kept. Try again in a minute.",
}

#: Every open app of the owner joins it (M10.7); nothing is broadcast.
ROOM = "sudarshan:alerts:owner"
NAMESPACE = "/"
PUSH_EVENTS = frozenset(
    {"sudarshan_market_alert", "sudarshan_market_alerts_state", "sudarshan_market_alert_delivery"}
)
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

# The service is on this server: its calls never go through a proxy the environment names, and
# no redirect is followed.
_loopback_client = LazyInit(
    lambda: httpx.Client(trust_env=False, follow_redirects=False,
                         limits=httpx.Limits(max_connections=16, max_keepalive_connections=4)),
    "sudarshan-alerts-loopback",
)  # fmt: skip


def _loopback() -> httpx.Client:
    return _loopback_client.get()


_fernet_lazy = LazyInit(
    lambda: fernet_for(pepper_from(os.environ), ALERTS_API_INFO), "sudarshan-alerts-seal"
)
_push_fernet_lazy = LazyInit(
    lambda: fernet_for(pepper_from(os.environ), ALERTS_PUSH_INFO), "sudarshan-alerts-push"
)
# The service sends a request when items wait, one after the other, each as the last was answered
# (batches of up to 50 items, at least 50 ms apart when only a few wait). The book holds 2 x 60 s
# of 40 a second (X9) and answers 503 past it rather than forget a nonce that could still be
# replayed.
_push_nonces = NonceBook(2 * SEAL_TTL_S * 40, lock=real_threading.RLock())
# Which problems have been logged in the last 10 minutes: one line each, not one per request.
_warned = LockedTTLCache(maxsize=8, ttl=600)


def _fernet():
    return _fernet_lazy.get()


def _push_fernet():
    return _push_fernet_lazy.get()


def _service_url() -> str:
    """The service's local API: the setting when it is plain http on this server, else 5582."""
    port = (os.environ.get("SUDARSHAN_ALERTS_HTTP_PORT") or "").strip()
    fallback = (
        f"http://127.0.0.1:{port}" if port.isdecimal() and 0 < int(port) < 65536 else SERVICE_URL
    )
    url = (os.environ.get("SUDARSHAN_ALERTS_API_URL") or fallback).rstrip("/")
    return url if url.startswith("http://") and is_loopback_url(url) else fallback


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
        answer = _loopback().get(
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
        answer = _loopback().post(
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
    if body.get("status") == "error" and body.get("code") not in CODES:
        logger.error("Market alerts: sudarshan-alerts answered %s with a refusal (%s) that the "
                     "page has no word for", op, body.get("code"))  # fmt: skip
        return _error("failed", 500)
    return jsonify(body), answer.status_code


def _json_body():
    """The request's JSON as a dict (else {}), read only up to MAX_BODY_BYTES: a longer one is
    the 'draft' refusal before any of it is parsed."""
    if (request.content_length or 0) > MAX_BODY_BYTES:
        raise RequestEntityTooLarge()
    request.max_content_length = MAX_BODY_BYTES  # a body with no length is cut here too
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


@sudarshan_alerts_bp.errorhandler(RequestEntityTooLarge)
def _too_large(error):
    return _error("draft", 400)


@sudarshan_alerts_bp.errorhandler(Exception)
def _failed(error):
    """Nothing here ends in OpenAlgo's HTML error page: the page gets the envelope."""
    if isinstance(error, HTTPException):
        return error
    logger.exception("Market alerts: the bridge failed on a request")
    return _error("failed", 500)


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
    fields, refused = _enriched(_json_body().get("alert"))
    if refused is not None:
        return refused
    return _call("create", {"alert": fields})


@sudarshan_alerts_bp.route("/api/alerts/<int:alert_id>", methods=["PATCH"])
@check_session_validity
@_owner_only
@limiter.limit("30 per minute")
def change_alert(alert_id: int):
    body = _json_body()
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
    body = _json_body()
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


#: The namespaces a socket can hold (python-socketio refuses any other): the app's and /market.
SOCKET_NAMESPACES = (NAMESPACE, "/market")


def _sockets() -> list[tuple[str, str, str]]:
    """Every socket now, as (namespace, sid, eio_sid), a copy (a disconnect edits the rooms).
    Only the owner's sessions connect (SUDARSHAN SIO1), so these are all his, the room's
    included; the room None is python-socketio's room of every sid in a namespace."""
    manager = getattr(getattr(socketio, "server", None), "manager", None)
    if manager is None:
        return []
    found = []
    for namespace in SOCKET_NAMESPACES:
        # get_participants copies the room in one step, so a connect meanwhile cannot break it.
        found += [(namespace, sid, eio_sid)
                  for sid, eio_sid in manager.get_participants(namespace, None)]  # fmt: skip
    return found


def _disconnect(sockets: list[tuple[str, str]]) -> None:
    for namespace, sid in sockets:
        try:
            socketio.server.disconnect(sid, namespace=namespace)
        except Exception:
            logger.exception("Could not close a socket at the end of a session")


def drop_owner_room() -> None:
    """Close every Socket.IO connection, /market's and the owner's room's included, when the
    owner's session ends (logout, a forced logout, the daily expiry or revoke), so each reconnect
    meets the connect check (SIO1) again. That refuses a cookie the session's end cleared or
    expired. It does not refuse a copy of a cookie the server never saw cleared, such as another
    device's after a logout elsewhere or a password change: that cookie stays valid until the
    daily expiry, for every HTTP route as well (review HIGH-2, tracked apart). Never raises, and
    does nothing when there is no socket. Under eventlet from a real thread the close is handed to
    the hub (utils.real_threading.run_on_hub); everywhere else it runs inline."""
    try:
        sockets = [(namespace, sid) for namespace, sid, _ in _sockets()]
        if sockets:
            real_threading.run_on_hub(_disconnect, sockets, timeout=3)
    except Exception:
        logger.exception("Could not close the owner's sockets")


def drop_sockets_from_before(boundary: datetime) -> None:
    """Close every socket whose session logged in before ``boundary``, the daily rollover that has
    just ended it (review MEDIUM-1). utils.session.revoke_user_tokens calls this where a login
    after the rollover is on record (another device, or the morning's auto-login) and so leaves the
    shared token alone: without it a socket admitted yesterday stays open and hears today's orders.
    The login time is the one each connection's session carried when it connected (Flask-SocketIO
    keeps that copy); a socket without a readable one is closed too. Never raises."""
    try:
        environ = getattr(getattr(socketio, "server", None), "environ", None) or {}
        stale = []
        for namespace, sid, eio_sid in _sockets():
            saved = (environ.get(eio_sid) or {}).get("saved_session") or {}
            try:
                if datetime.fromisoformat(saved.get("login_time")) >= boundary:
                    continue
            except (TypeError, ValueError):
                pass  # no login time, or one that cannot be compared: not a session to keep
            stale.append((namespace, sid))
        if stale:
            real_threading.run_on_hub(_disconnect, stale, timeout=3)
    except Exception:
        logger.exception("Could not close the sockets opened before the daily rollover")


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
    """An open app joins the owner's room, if its session is the owner's: the check every connect
    makes (SIO1, extensions.is_owner_session), made again because a socket admitted before the
    daily expiry may ask after it, with the session it connected with."""
    if not is_owner_session():
        return {"ok": False, "message": WORDS["owner_only"]}
    join_room(ROOM)
    return {"ok": True}


@sudarshan_alerts_bp.record_once
def _exempt_the_push_from_csrf(state):
    """The service has no browser session to forge; its push is authenticated by the seal."""
    csrf = state.app.extensions.get("csrf") or getattr(state.app, "csrf", None)
    if csrf is not None:
        csrf.exempt(internal_push)
