"""SUDARSHAN (M13.7, B1): the TRINETRA page's bridge to the sudarshan-signals service.

This blueprint is a SUDARSHAN addition to OpenAlgo; the TRINETRA view itself lives in the
sudarshan-signals service (``sudarshan/signals/``). It keeps no state of its own: every request is
checked here and forwarded, sealed, to the service's local API on 127.0.0.1:5588, whose answer
comes back in the page's envelope. The contract is ``sudarshan/signals/contract.py`` and section 8
of ``sudarshan/signals/README.md``; every route comes from its ``OPS`` table:

    GET  /sudarshan/signals/api/state                 state
    GET  /sudarshan/signals/api/timeline?module=&instrument=&before=&after_ms=&limit=   timeline
    GET  /sudarshan/signals/api/changes/<id>          change_get
    GET  /sudarshan/signals/api/performance?module=   performance
    GET  /sudarshan/signals/api/settings              settings_get
    PUT  /sudarshan/signals/api/settings              settings_set (JSON body)

Each route is checked in this order: an OpenAlgo session (401 ``unauthorized``), the owner himself
(403 ``forbidden`` for any other session, reading included: a family viewer never sees
TRINETRA's readings), CSRF on the write (the app's CSRFProtect), and a limiter on the class of the
op: reads 120 a minute, writes 30.

How it forwards: first, who listens on the port is looked up (``sudarshan.alerts.loopback``), so
nothing goes to a port another user's program holds. The body is one Fernet token under
``SIGNALS_API_INFO`` (60 s, once), ``{"op", "nonce", "principal": "owner", "args": {...}}``, at
most 16 KiB. It goes through the shared httpx client with an explicit timeout (3 s for a read, 6 s
for the write), no redirect is followed, and the write is never retried. Under eventlet that is a
green socket wait that yields to the hub; under gthread it holds one pool thread for at most that.
No thread, scheduler or event loop is added to the app.

The page's envelope: ``{"status": "success", "enabled": true, "data": ...}``; switched off or not
set up, ``{"status": "success", "enabled": false, "data": null, "message": ...}`` with the
service's sentence; refused or failed, ``{"status": "error", "error": {"code", "message",
"field"?}}`` with the HTTP status of the code (``contract.ERROR_STATUS``). The page reads the code,
never the status. When the service does not answer, its own ``/health`` says whether the view is
switched off or not set up; otherwise "SUDARSHAN's TRINETRA reader is not answering.", and a write
sent and not answered is "Not confirmed". Whether the view is on is never read here from a second
switch.

The push to the owner's open screens (B2, README section 9): the service posts each update to
``POST /sudarshan/signals/internal/push``, one token sealed for op ``push`` under
``SIGNALS_PUSH_INFO`` (principal ``sudarshan-signals``, at most 32 KiB). No session and no CSRF:
the seal is the authentication, and nginx's headers make it 404. Each item whose event is in
``contract.PUSH_EVENTS`` is emitted to the room ``sudarshan:signals:owner`` only, inside the
request (``extensions.emit_from_any_thread``), and the answer is the room's size. An open app joins
the room with ``sudarshan_signals_join`` after each connect, only with the owner's session.
"""

import os
import re
from functools import wraps
from typing import NamedTuple

import httpx
from flask import Blueprint, jsonify, request, session
from flask_socketio import join_room
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from database.user_db import find_user_by_username
from extensions import emit_from_any_thread, is_owner_session, socketio
from limiter import limiter
from sudarshan.alerts import loopback
from sudarshan.autologin.config import is_loopback_url
from sudarshan.autologin.handoff import (
    SEAL_TTL_S,
    HandoffRejected,
    NonceBook,
    NoncesFull,
    open_sealed,
    seal,
)
from sudarshan.signals import contract
from sudarshan.vault import SIGNALS_API_INFO, SIGNALS_PUSH_INFO, fernet_for, pepper_from
from utils import real_threading
from utils.httpx_client import get_httpx_client
from utils.lazy import LazyInit
from utils.logging import get_logger
from utils.session import is_session_valid, revoke_user_tokens
from utils.thread_safe_cache import MISSING, LockedTTLCache

logger = get_logger(__name__)

API_PREFIX = contract.API_PREFIX
#: The page's routes are under API_PREFIX and the service's push at contract.PUSH_PATH: each rule
#: is written whole, so the blueprint has no prefix of its own.
sudarshan_signals_bp = Blueprint("sudarshan_signals", __name__)

PRINCIPAL = contract.PRINCIPALS["api"]
LOCAL_HOST = "127.0.0.1"
SERVICE_URL = f"http://{LOCAL_HOST}:{contract.PORT}"
API_MAX_BYTES = contract.SEAL_LIMITS["api"]
CONNECT_TIMEOUT_S = 1.0
HEALTH_TIMEOUT_S = 1.0
MAX_QUERY_TEXT = 200
LIMITS = {cls: f"{count} per minute" for cls, count in contract.LIMITS_PER_MIN.items()}
SCOPES = {cls: f"sudarshan-signals-{cls}" for cls in LIMITS}
ERROR_STATUS = contract.ERROR_STATUS
#: The bridge's own codes: the service's answer of one of them is a fault.
_BRIDGE_ONLY = frozenset({"unauthorized", "forbidden", "rate_limited", "not_confirmed"})
WORDS = contract.WORDS


class OpSpec(NamedTuple):
    """One op of the service's local API, the route that carries it, and its query fields."""

    name: str
    method: str
    route: str
    cls: str
    query: tuple[tuple[str, str], ...] = ()


def _query_of(spec: contract.OpSpec) -> tuple[tuple[str, str], ...]:
    """A GET op's fields as the query readers take them: a whole number or a short text."""
    if spec.method != "GET":
        return ()
    return tuple(
        (f.name, "int" if f.type.startswith("int") else "str")
        for f in contract.parse_fields(spec.req)
        if f.name != "id"
    )


OPS = tuple(OpSpec(s.name, s.method, s.route, s.cls, _query_of(s)) for s in contract.OPS)

_warned = LockedTTLCache(maxsize=8, ttl=600)
_fernet_lazy = LazyInit(
    lambda: fernet_for(pepper_from(os.environ), SIGNALS_API_INFO), "sudarshan-signals-seal"
)


def _fernet():
    return _fernet_lazy.get()


def _service_url() -> str:
    """The service's local API: the setting when it is plain http on this server, else 5588."""
    port = (os.environ.get("SUDARSHAN_SIGNALS_HTTP_PORT") or "").strip()
    fallback = (
        f"http://{LOCAL_HOST}:{port}" if port.isdecimal() and 0 < int(port) < 65536 else SERVICE_URL
    )
    url = (os.environ.get("SUDARSHAN_SIGNALS_API_URL") or fallback).rstrip("/")
    return url if url.startswith("http://") and is_loopback_url(url) else fallback


def _warn_once(kind: str, message: str, *args) -> None:
    with _warned.lock:
        first = _warned.get(kind, MISSING) is MISSING
        if first:
            _warned[kind] = True
    if first:
        logger.warning(message, *args)


def _sentence(value) -> str | None:
    """A sentence the service sent for the owner, or None when it is not a plain one."""
    if not isinstance(value, str) or not 0 < len(value) <= 300 or not value.endswith("."):
        return None
    return value if all(ch.isprintable() for ch in value) else None


# -- answers -----------------------------------------------------------------------------------


def _error(code: str, message: str | None = None, field=None):
    """The page's error envelope: the code it acts on and a sentence for the owner."""
    error = {"code": code, "message": message or WORDS.get(code) or WORDS["failed"]}
    if isinstance(field, str) and field:
        error["field"] = field
    return jsonify({"status": "error", "error": error}), ERROR_STATUS[code]


def _off(message=None):
    """The view switched off (or not set up) is not an error: the page shows the sentence."""
    words = _sentence(message) or WORDS["off"]
    return jsonify({"status": "success", "enabled": False, "data": None, "message": words}), 200


def _unknown_op(status: int, code, message) -> bool:
    bare = message is None or message.strip().lower() == "not found"
    return status == 404 and code in (None, "not_found") and bare


def _answer(status: int, body: dict):
    """The service's answer as the page's envelope, with the HTTP status of its code."""
    if body.get("status") == "success":
        data = body.get("data")
        if isinstance(data, dict) and data.get("enabled") is False:
            return _off(data.get("message"))
        return jsonify({"status": "success", "enabled": True, "data": data}), 200
    code, message = body.get("code"), body.get("message")
    message = message if isinstance(message, str) and message else None
    if status == 501 or code == "not_ready" or _unknown_op(status, code, message):
        return _error("not_ready")
    if code in ("forbidden", "principal"):
        logger.error(
            "The TRINETRA view: sudarshan-signals refused OpenAlgo's sealed request (%s); the two "
            "may hold different peppers", code,
        )  # fmt: skip
        return _error("unavailable", WORDS["sealed"])
    if code in ERROR_STATUS and code not in _BRIDGE_ONLY:
        return _error(code, _sentence(message), body.get("field"))
    return _error("unavailable", _sentence(message) or WORDS["failed"])


# -- who may call ------------------------------------------------------------------------------


def _session_required(view):
    """No OpenAlgo session is 401 in the page's envelope, never a redirect to the login page."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_session_valid():
            revoke_user_tokens()
            session.clear()
            return _error("unauthorized")
        return view(*args, **kwargs)

    return wrapper


def _owner_only(view):
    """The logged-in session must be OpenAlgo's owner (admin) account; anyone else is refused
    on every route, reading included."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        owner = find_user_by_username()
        if owner is None or session.get("user") != owner.username:
            return _error("forbidden")
        return view(*args, **kwargs)

    return wrapper


@sudarshan_signals_bp.errorhandler(429)
def _rate_limited(error):
    return _error("rate_limited")


@sudarshan_signals_bp.errorhandler(RequestEntityTooLarge)
def _too_large(error):
    return _error("bad_request", WORDS["too_big"])


@sudarshan_signals_bp.errorhandler(Exception)
def _failed(error):
    """Nothing here ends in OpenAlgo's HTML error page: the page gets the envelope."""
    if isinstance(error, HTTPException):
        return error
    logger.exception("The TRINETRA view: the bridge failed on a request")
    return _error("unavailable", WORDS["failed"])


# -- reading the request -----------------------------------------------------------------------


class _Unreadable(Exception):
    """A request the bridge does not send on."""


def _text(raw: str) -> str:
    if not raw or len(raw) > MAX_QUERY_TEXT:
        raise _Unreadable()
    return raw


def _int(raw: str) -> int:
    if not re.fullmatch(r"-?[0-9]{1,15}", raw.strip()):
        raise _Unreadable()
    return int(raw)


QUERY_READERS = {"int": _int, "str": _text}


def _json_body() -> dict:
    if (request.content_length or 0) > API_MAX_BYTES:
        raise RequestEntityTooLarge()
    request.max_content_length = API_MAX_BYTES
    if not request.get_data(cache=True):
        return {}
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise _Unreadable()
    return body


def _args(spec: OpSpec, ident: int | None) -> dict:
    if spec.method == "GET":
        args = {}
        for name, kind in spec.query:
            raw = request.args.get(name)
            if raw is not None and raw != "":
                args[name] = QUERY_READERS[kind](raw)
    else:
        args = _json_body()
    if ident is not None:
        args["id"] = ident  # the route's number is the target, whatever the body says
    return args


# -- the service -------------------------------------------------------------------------------


class _Reply(NamedTuple):
    status: int
    body: dict


def _listener():
    """None when a request may go to the service's port; else the answer to give instead."""
    held = loopback.holder(_service_url())
    if held == loopback.NOBODY:
        _warn_once("nobody", "The TRINETRA view: nothing listens on sudarshan-signals' port")
        return _error("unavailable", WORDS["not_answering"])
    if held == loopback.OTHER:
        _warn_once("other", "The TRINETRA view: %s; nothing was sent",
                   loopback.refusal(_service_url()))  # fmt: skip
        return _error("unavailable", WORDS["held"])
    return None


def _health_off() -> str | None:
    """The sentence when the service's /health says the view is switched off or not set up;
    None when it is on, or does not answer either."""
    try:
        answer = get_httpx_client().get(
            f"{_service_url()}/health",
            headers={"Accept": "application/json"},
            timeout=httpx.Timeout(HEALTH_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
            follow_redirects=False,
        )
        health = answer.json()
    except (httpx.HTTPError, ValueError):
        return None
    if answer.status_code != 200 or not isinstance(health, dict):
        return None
    return {"off": WORDS["off"], "not_set_up": WORDS["not_set_up"]}.get(health.get("signals"))


def _not_answered(spec: OpSpec, sent: bool):
    off = _health_off() if sent else None
    if off is not None:
        return _off(off)
    if sent and spec.cls == "write":  # it may have been done: never say it was not
        return _error("not_confirmed")
    return _error("unavailable", WORDS["not_answering"])


def _send(spec: OpSpec, args: dict):
    """One sealed op at the service: its _Reply, or the bridge's own answer (a response)."""
    refused = _listener()
    if refused is not None:
        return refused
    blob = seal(_fernet(), spec.name, principal=PRINCIPAL, args=args)
    if len(blob) > API_MAX_BYTES:
        return _error("bad_request", WORDS["too_big"])
    try:
        answer = get_httpx_client().post(
            f"{_service_url()}{contract.OPS_PREFIX}{spec.name}",
            content=blob,
            headers={"Content-Type": "application/octet-stream", "Accept": "application/json"},
            timeout=httpx.Timeout(contract.TIMEOUTS_S[spec.cls], connect=CONNECT_TIMEOUT_S),
            follow_redirects=False,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
        _warn_once("call", "The TRINETRA view: sudarshan-signals did not take a %s request",
                   spec.name)  # fmt: skip
        return _not_answered(spec, sent=False)
    except httpx.HTTPError:
        _warn_once("call", "The TRINETRA view: sudarshan-signals did not answer a %s request",
                   spec.name)  # fmt: skip
        return _not_answered(spec, sent=True)
    try:
        body = answer.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or body.get("status") not in ("success", "error"):
        logger.error("The TRINETRA view: sudarshan-signals answered %s without its envelope",
                     spec.name)  # fmt: skip
        return _not_answered(spec, sent=True)
    return _Reply(answer.status_code, body)


def _call(spec: OpSpec, args: dict):
    reply = _send(spec, args)
    if not isinstance(reply, _Reply):
        return reply
    return _answer(reply.status, reply.body)


def _register(spec: OpSpec) -> None:
    def view(**kwargs):
        try:
            args = _args(spec, kwargs.get("id"))
        except _Unreadable:
            return _error("bad_request", WORDS["bad_request"])
        return _call(spec, args)

    view.__name__ = spec.name
    guarded = limiter.shared_limit(LIMITS[spec.cls], scope=SCOPES[spec.cls])(view)
    guarded = _owner_only(guarded)
    guarded = _session_required(guarded)
    rule = API_PREFIX + spec.route.replace("<id>", "<int:id>")
    sudarshan_signals_bp.add_url_rule(rule, endpoint=spec.name, view_func=guarded,
                                      methods=[spec.method])  # fmt: skip


for _spec in OPS:
    _register(_spec)


# -- the service's push and the owner's room (B2) ----------------------------------------------

NAMESPACE = "/"
PUSH_MAX_BYTES = contract.SEAL_LIMITS["push"]
#: The service sends one item a request; anything past this is not its push.
MAX_PUSH_ITEMS = 8
#: Set by nginx on every proxied location: a request carrying any of them did not come from this
#: server's sudarshan-signals, which calls 127.0.0.1:5000 directly.
PROXY_HEADERS = ("X-Forwarded-For", "X-Real-IP", "Forwarded", "X-Forwarded-Proto",
                 "X-Forwarded-Host")  # fmt: skip

_push_fernet_lazy = LazyInit(
    lambda: fernet_for(pepper_from(os.environ), SIGNALS_PUSH_INFO), "sudarshan-signals-push"
)
# One push per update, folded per burst: a few a second at most. The book holds 2 x 60 s of 4 a
# second and answers 503 past it rather than forget a nonce that could still be replayed.
_push_nonces = NonceBook(2 * SEAL_TTL_S * 4, lock=real_threading.RLock())


def _push_fernet():
    return _push_fernet_lazy.get()


def _screens() -> int:
    """How many open apps are in the owner's room (one worker, no message queue: exact)."""
    rooms = getattr(getattr(getattr(socketio, "server", None), "manager", None), "rooms", None)
    if not isinstance(rooms, dict):
        return 0
    members = rooms.get(NAMESPACE, {}).get(contract.ROOM)
    try:
        return len(members) if members is not None else 0
    except TypeError:
        return 0


def _refused(status: int, message: str):
    return jsonify({"status": "error", "message": message}), status


@sudarshan_signals_bp.route(contract.PUSH_PATH, methods=["POST"])
def internal_push():
    """sudarshan-signals' update for the owner's open screens: emitted to his room only, inside
    this request (safe in every runtime); the answer is the room's size. Never retried."""
    if any(header in request.headers for header in PROXY_HEADERS):
        return _refused(404, "Not found")
    length = request.content_length
    if length is None:
        return _refused(411, "Length required")
    if length > PUSH_MAX_BYTES:
        return _refused(413, "Too large")
    try:
        message = open_sealed(_push_fernet(), request.get_data(cache=False), op=contract.PUSH_OP,
                              seen=_push_nonces, max_bytes=PUSH_MAX_BYTES)  # fmt: skip
    except NoncesFull:
        _warn_once("push-busy", "The TRINETRA view: too many pushes in the last two minutes")
        return _refused(503, "Busy")
    except HandoffRejected as exc:
        _warn_once("push-refused", "The TRINETRA view: refused a push that was not sealed by this "
                   "server's sudarshan-signals (%s)", exc)  # fmt: skip
        return _refused(403, "Forbidden")
    if message.get("principal") != contract.PRINCIPALS["push"]:
        return _refused(403, "Forbidden")
    items = message.get("items")
    if not isinstance(items, list) or len(items) > MAX_PUSH_ITEMS:
        return _refused(400, "Bad push")
    screens = _screens()
    for item in items:
        if not isinstance(item, dict):
            continue
        event, data = item.get("event"), item.get("data")
        if event in contract.PUSH_EVENTS and isinstance(data, dict):
            emit_from_any_thread(event, data, to=contract.ROOM, namespace=NAMESPACE)
    return jsonify({"status": "success", "screens": screens})


@socketio.on(contract.JOIN_EVENT)
def _join_owner_room(data=None):
    """An open app joins the owner's room if its session is the owner's: the check every connect
    makes (SIO1), made again because a socket admitted before the daily expiry may ask after it.
    A check that raises is a refusal."""
    try:
        owner = is_owner_session()
    except Exception:
        logger.exception("The TRINETRA view: the owner check failed when a screen asked to join")
        owner = False
    if not owner:
        return {"ok": False, "message": WORDS["forbidden"]}
    join_room(contract.ROOM)
    return {"ok": True}


@sudarshan_signals_bp.record_once
def _exempt_the_push_from_csrf(state):
    """The service has no browser session to forge; its push is authenticated by the seal."""
    csrf = state.app.extensions.get("csrf") or getattr(state.app, "csrf", None)
    if csrf is not None:
        csrf.exempt(internal_push)
