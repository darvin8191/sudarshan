"""SUDARSHAN (M9.1): the /screener page's bridge to the sudarshan-screener service.

This blueprint is a SUDARSHAN addition to OpenAlgo; the screener itself lives in the
sudarshan-screener service (``sudarshan/screener/README.md``). It keeps no state of its own:
every owner request is checked here and forwarded, sealed, to the service's local API on
127.0.0.1:5584 (``sudarshan/screener/api.py``), whose answer comes back in the page's envelope.

Every route of the contract's table (``OPS`` in ``sudarshan/screener/contract.py``) is built from
that one table, so a later op never means editing this file. Each route is checked in this order:
an OpenAlgo session (401), the owner himself (403 for any other session, reading included: a
family viewer never sees the owner's screens), CSRF on writes, and a limiter on the class of the
op: reads 120 a minute, writes 60, previews 6 (6.3).

How it forwards: first, who listens on the port is looked up (``sudarshan.alerts.loopback``), so
nothing goes to a port another user's program holds. The body is one Fernet token under
``SCREENER_API_INFO`` (60 s, once), ``{"op", "nonce", "principal": "owner", "args": {...}}``. It
goes through the shared httpx client with an explicit timeout (3 s for a read, 6 s for a write or
a preview), no redirect is followed, and a write is never retried. Under eventlet that is a green
socket wait that yields to the hub; under gthread it holds one pool thread for at most that. No
thread, scheduler or event loop is added to the app.

The service's two internal routes come the other way, sealed under ``SCREENER_INTERNAL_INFO``,
CSRF-exempt and refused with 404 when nginx forwarded them (the service calls 127.0.0.1:5000
directly): ``/internal/watchlist`` reads or changes the owner's OpenAlgo watchlists for the target
list (M9.17) and emits ``sudarshan_screener_lists``; ``/internal/push`` emits a run's summary to
the owner's room. Every open app of the owner joins that room with ``sudarshan_screener_join``;
nothing is broadcast.
"""

import os
import re
from functools import wraps

import httpx
from flask import Blueprint, jsonify, request, session
from flask_socketio import join_room
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from database import watchlist_db
from database.user_db import find_user_by_username
from extensions import emit_from_any_thread, socketio
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
from sudarshan.screener import contract
from sudarshan.vault import SCREENER_API_INFO, SCREENER_INTERNAL_INFO, fernet_for, pepper_from
from utils import real_threading
from utils.httpx_client import get_httpx_client
from utils.lazy import LazyInit
from utils.logging import get_logger
from utils.session import is_session_valid, revoke_user_tokens
from utils.thread_safe_cache import MISSING, LockedTTLCache

logger = get_logger(__name__)

sudarshan_screener_bp = Blueprint("sudarshan_screener", __name__, url_prefix=contract.API_PREFIX)
sudarshan_screener_internal_bp = Blueprint(
    "sudarshan_screener_internal", __name__, url_prefix=contract.INTERNAL_PREFIX
)

PRINCIPAL = "owner"
SERVICE_URL = f"http://{contract.LOCAL_HOST}:{contract.DEFAULT_HTTP_PORT}"
CONNECT_TIMEOUT_S = 1.0
MAX_QUERY_TEXT = 200
LIMITS = {cls: f"{n} per minute" for cls, n in contract.LIMITS_PER_MIN.items()}
SCOPES = {cls: f"sudarshan-screener-{cls}" for cls in LIMITS}
#: Every open app of the owner joins it (4.5); nothing is broadcast.
ROOM = "sudarshan:screener:owner"
NAMESPACE = "/"
#: Derived from the contract, so a new push event never needs an edit here (M9.16).
PUSH_EVENTS = frozenset(contract.PUSH_EVENTS)
#: Set by nginx on every proxied location: a request carrying any of them did not come from this
#: server's sudarshan-screener, which calls 127.0.0.1:5000 directly.
PROXY_HEADERS = (
    "X-Forwarded-For",
    "X-Real-IP",
    "Forwarded",
    "X-Forwarded-Proto",
    "X-Forwarded-Host",
)
MAX_INTERNAL_BYTES = contract.INTERNAL_MAX_BYTES
MAX_LIST_CHANGES = 300
WORDS = {
    **contract.WORDS,
    "held": "Another program on this server holds the screener service's port, so nothing was "
    "sent to it. Stop that program, then restart sudarshan-screener.",
    "sealed": "The screener could not confirm that this request came from OpenAlgo. If "
    "OpenAlgo's .env was changed, restart sudarshan-screener.",
    "failed": "The screener could not do that just now. Your screens are kept; try again in a minute.",
}
_ERROR_CODES = frozenset(contract.ERROR_CODES)
_BRIDGE_ONLY = frozenset({"unauthorized", "forbidden", "rate_limited", "not_confirmed"})

_warned = LockedTTLCache(maxsize=8, ttl=600)
#: The lists this route made, by the create's key (the request's ``ref`` and the name asked for):
#: a create tried again after its answer was lost answers the list already made, never a second
#: "Screener: X (2)" (release review HIGH-5). An hour outlasts the service's 30 minutes of retries.
_made = LockedTTLCache(maxsize=64, ttl=3600)
_fernet_lazy = LazyInit(
    lambda: fernet_for(pepper_from(os.environ), SCREENER_API_INFO), "sudarshan-screener-seal"
)
_internal_fernet_lazy = LazyInit(
    lambda: fernet_for(pepper_from(os.environ), SCREENER_INTERNAL_INFO),
    "sudarshan-screener-internal",
)
#: The service sends a sync or a push as the last was answered: the book holds 2 x 60 s of 8 a
#: second (X9) and answers 503 past it rather than forget a nonce that could still be replayed.
_internal_nonces = NonceBook(2 * SEAL_TTL_S * 8, lock=real_threading.RLock())


def _fernet():
    return _fernet_lazy.get()


def _internal_fernet():
    return _internal_fernet_lazy.get()


def _service_url() -> str:
    """The service's local API: the setting when it is plain http on this server, else 5584."""
    port = (os.environ.get("SUDARSHAN_SCREENER_HTTP_PORT") or "").strip()
    fallback = (
        f"http://{contract.LOCAL_HOST}:{port}"
        if port.isdecimal() and 0 < int(port) < 65536
        else SERVICE_URL
    )
    url = (os.environ.get("SUDARSHAN_SCREENER_API_URL") or fallback).rstrip("/")
    return url if url.startswith("http://") and is_loopback_url(url) else fallback


def _warn_once(kind: str, message: str, *args) -> None:
    with _warned.lock:
        first = _warned.get(kind, MISSING) is MISSING
        if first:
            _warned[kind] = True
    if first:
        logger.warning(message, *args)


# -- answers -----------------------------------------------------------------------------------
def _error(code: str, message: str | None = None, **extra):
    """The page's error envelope: the code it acts on and a sentence for the owner."""
    error = {"code": code, "message": message or WORDS.get(code) or WORDS["failed"]}
    error.update({key: value for key, value in extra.items() if value is not None})
    return jsonify({"status": "error", "error": error}), contract.ERROR_STATUS[code]


def _answer(body: dict):
    """The service's answer as the page's envelope with the HTTP status of its code."""
    if body.get("status") == "success":
        data = body.get("data")
        if isinstance(data, dict) and data.get("enabled") is False:
            message = data.get("message")
            return jsonify({
                "status": "success", "enabled": False, "data": None,
                "message": message if isinstance(message, str) and message else WORDS["off"],
            }), 200  # fmt: skip
        return jsonify({"status": "success", "enabled": True, "data": data}), 200
    error = body.get("error") if isinstance(body.get("error"), dict) else body
    code, message = error.get("code"), error.get("message")
    if not isinstance(message, str) or not message:
        message = None
    if code in ("forbidden", "principal"):
        logger.error(
            "The screener: sudarshan-screener refused OpenAlgo's sealed request (%s); the two may "
            "hold different peppers", code,
        )  # fmt: skip
        return _error("unavailable", WORDS["sealed"])
    if code in _ERROR_CODES and code not in _BRIDGE_ONLY:
        return _error(code, message, field=error.get("field"))
    return _error("unavailable", message or WORDS["failed"])


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


def _is_owner() -> bool:
    owner = find_user_by_username()
    return owner is not None and session.get("user") == owner.username


def _owner_only(view):
    """The logged-in session must be OpenAlgo's owner (admin) account; anyone else is refused
    on every route, reading included."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not _is_owner():
            return _error("forbidden")
        return view(*args, **kwargs)

    return wrapper


@sudarshan_screener_bp.errorhandler(429)
def _rate_limited(error):
    if (request.endpoint or "").rpartition(".")[2] == "preview":
        return _error("rate_limited", WORDS["preview_limited"])
    return _error("rate_limited")


@sudarshan_screener_bp.errorhandler(RequestEntityTooLarge)
def _too_large(error):
    return _error("bad_request", WORDS["too_big"])


@sudarshan_screener_bp.errorhandler(Exception)
def _failed(error):
    """Nothing here ends in OpenAlgo's HTML error page: the page gets the envelope."""
    if isinstance(error, HTTPException):
        return error
    logger.exception("The screener: the bridge failed on a request")
    return _error("unavailable", WORDS["failed"])


# -- reading the request -----------------------------------------------------------------------
class _Unreadable(Exception):
    def __init__(self, message: str | None = None):
        super().__init__(message or WORDS["unreadable"])


def _int(raw: str) -> int:
    if not re.fullmatch(r"[0-9]{1,15}", raw.strip()):
        raise _Unreadable()
    return int(raw)


def _text(raw: str) -> str:
    if not raw or len(raw) > MAX_QUERY_TEXT:
        raise _Unreadable()
    return raw


def _flag(raw: str) -> bool:
    return raw.strip().lower() in ("1", "true", "yes", "on")


QUERY_READERS = {"int": _int, "str": _text, "flag": _flag}


def _json_body() -> dict:
    """The request's JSON as a dict (none is {}), read only up to the API's 64 KiB."""
    if (request.content_length or 0) > contract.API_MAX_BYTES:
        raise RequestEntityTooLarge()
    request.max_content_length = contract.API_MAX_BYTES
    if not request.get_data(cache=True):
        return {}
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise _Unreadable()
    return body


def _args(spec: contract.OpSpec, ident: int | None) -> dict:
    args: dict = {}
    if spec.method in ("GET", "DELETE"):
        for name, kind in spec.query:
            raw = request.args.get(name)
            if raw is not None and raw != "":
                args[name] = QUERY_READERS[kind](raw)
    if spec.method != "GET" and (request.content_length or 0) > 0:
        body = _json_body()  # a DELETE may carry its flags as JSON too (the page does)
        for name, kind in spec.query:
            if name in body and name not in args:
                args[name] = bool(body[name]) if kind == "flag" else body[name]
        if spec.method != "DELETE":
            args.update(body)
    if ident is not None:
        args["id"] = ident  # the route's number is the target, whatever the body says
    return args


# -- the service -------------------------------------------------------------------------------
def _listener():
    """None when a request may go to the service's port; else the answer to give instead."""
    held = loopback.holder(_service_url())
    if held == loopback.NOBODY:
        _warn_once("nobody", "The screener: nothing listens on sudarshan-screener's port")
        return _error("unavailable", WORDS["not_answering"])
    if held == loopback.OTHER:
        _warn_once("other", "The screener: %s; nothing was sent", loopback.refusal(_service_url()))
        return _error("unavailable", WORDS["held"])
    return None


def _call(spec: contract.OpSpec, args: dict):
    """One sealed op at the service; its answer in the page's envelope, or the bridge's own."""
    refused = _listener()
    if refused is not None:
        return refused
    blob = seal(_fernet(), spec.name, principal=PRINCIPAL, args=args)
    if len(blob) > contract.API_MAX_BYTES:
        return _error("bad_request", WORDS["too_big"])
    write = spec.cls == "write"
    try:
        answer = get_httpx_client().post(
            f"{_service_url()}/v1/{spec.name}",
            content=blob,
            headers={"Content-Type": "application/octet-stream", "Accept": "application/json"},
            timeout=httpx.Timeout(float(contract.TIMEOUTS_S[spec.cls]), connect=CONNECT_TIMEOUT_S),
            follow_redirects=False,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
        _warn_once("call", "The screener: sudarshan-screener did not take a %s request", spec.name)
        return _error("unavailable", WORDS["not_answering"])  # never sent
    except httpx.HTTPError:
        _warn_once(
            "call", "The screener: sudarshan-screener did not answer a %s request", spec.name
        )
        if write:  # it may have been done: never say it was not
            return _error("not_confirmed")
        return _error("unavailable", WORDS["not_answering"])
    try:
        body = answer.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or body.get("status") not in ("success", "error"):
        logger.error("The screener: sudarshan-screener answered %s without its envelope", spec.name)
        return _error("not_confirmed") if write else _error("unavailable", WORDS["not_answering"])
    return _answer(body)


def _register(spec: contract.OpSpec) -> None:
    def view(**kwargs):
        try:
            args = _args(spec, kwargs.get("id"))
        except _Unreadable as unreadable:
            return _error("bad_request", str(unreadable))
        return _call(spec, args)

    view.__name__ = spec.name
    guarded = limiter.shared_limit(LIMITS[spec.cls], scope=SCOPES[spec.cls])(view)
    guarded = _owner_only(guarded)
    guarded = _session_required(guarded)
    rule = spec.route.replace("<id>", "<int:id>")
    sudarshan_screener_bp.add_url_rule(
        rule, endpoint=spec.name, view_func=guarded, methods=[spec.method]
    )


for _spec in contract.OPS:
    _register(_spec)


# -- the service's internal routes: the target watchlist and the push ------------------------------
def _refused(status: int, message: str):
    return jsonify({"status": "error", "message": message}), status


def _open_internal(op: str):
    """The sealed request of the service, or the answer to give instead."""
    if any(header in request.headers for header in PROXY_HEADERS):
        return None, _refused(404, "Not found")
    length = request.content_length
    if length is None:
        return None, _refused(411, "Length required")
    if length > MAX_INTERNAL_BYTES:
        return None, _refused(413, "Too large")
    try:
        message = open_sealed(
            _internal_fernet(), request.get_data(cache=False), op=op, seen=_internal_nonces,
            max_bytes=MAX_INTERNAL_BYTES,
        )  # fmt: skip
    except NoncesFull:
        _warn_once(
            "internal-busy", "The screener: too many internal requests in the last two minutes"
        )
        return None, _refused(503, "Busy")
    except HandoffRejected as exc:
        _warn_once(
            "internal-refused",
            "The screener: refused an internal request that was not sealed by this server's "
            "sudarshan-screener (%s)", exc,
        )  # fmt: skip
        return None, _refused(403, "Forbidden")
    args = message.get("args")
    return (args if isinstance(args, dict) else {}), None


def _listing(found: dict) -> dict:
    return {
        "list_id": found["id"],
        "name": found["name"],
        "items": [
            {"id": item["id"], "symbol": item["symbol"], "exchange": item["exchange"]}
            for item in found.get("items", [])
        ],
    }


def _owner_id() -> str | None:
    owner = find_user_by_username()
    return owner.username if owner is not None else None


@sudarshan_screener_internal_bp.route("/watchlist", methods=["POST"])
def internal_watchlist():
    """``watchlist_read`` answers one list as OpenAlgo holds it (or ``found: false``);
    ``watchlist_apply`` makes the list when ``create_name`` is given (once per ``ref`` and name:
    the same create again answers the list it made), adds and removes items (an add that exists
    and a remove of an item already gone are no-ops), emits ``sudarshan_screener_lists`` and
    answers the list. Both run as the owner."""
    op = (request.args.get("op") or "").strip()
    if op not in ("watchlist_read", "watchlist_apply"):
        return _refused(404, "Not found")
    args, refusal = _open_internal(op)
    if refusal is not None:
        return refusal
    user_id = _owner_id()
    if user_id is None:
        return _refused(503, "No owner account yet")
    lists = {found["id"]: found for found in watchlist_db.get_watchlists(user_id)}
    if op == "watchlist_read":
        list_id = args.get("list_id")
        found = lists.get(list_id) if isinstance(list_id, int) else None
        if found is None:
            return jsonify({"status": "success", "found": False})
        return jsonify({"status": "success", "found": True, **_listing(found)})
    list_id, create_name = args.get("list_id"), args.get("create_name")
    add, remove = args.get("add") or [], args.get("remove") or []
    if (
        not isinstance(add, list)
        or not isinstance(remove, list)
        or len(add) + len(remove) > MAX_LIST_CHANGES
    ):
        return _refused(400, "Bad request")
    if list_id is None:
        if not isinstance(create_name, str) or not create_name.strip():
            return _refused(400, "Bad request")
        asked = create_name.strip()[:64]
        ref = args.get("ref")
        key = (ref, asked) if isinstance(ref, str) and ref else None
        earlier = _made.get(key) if key is not None else None
        if earlier in lists:
            list_id = earlier  # the same create again: its list, not another one
        else:
            name = _free_name(asked, {found["name"] for found in lists.values()})
            made = watchlist_db.create_watchlist(user_id, name)
            if made is None:
                return _refused(503, "The list could not be made")
            list_id = made["id"]
            if key is not None:
                _made[key] = list_id
    elif not isinstance(list_id, int) or list_id not in lists:
        return jsonify({"status": "success", "found": False})
    for item in add:
        if (
            isinstance(item, dict)
            and isinstance(item.get("symbol"), str)
            and isinstance(item.get("exchange"), str)
        ):
            watchlist_db.add_item(user_id, list_id, item["symbol"], item["exchange"])
    for item_id in remove:
        if isinstance(item_id, int):
            watchlist_db.remove_item(user_id, list_id, item_id)
    after = next((f for f in watchlist_db.get_watchlists(user_id) if f["id"] == list_id), None)
    if after is None:
        return jsonify({"status": "success", "found": False})
    if add or remove or create_name:
        emit_from_any_thread(
            contract.EVENT_LISTS, {"list_id": list_id}, to=ROOM, namespace=NAMESPACE
        )
    return jsonify({"status": "success", "found": True, **_listing(after)})


def _free_name(name: str, taken: set[str]) -> str:
    """ "Screener: EMA cross", then " (2)", " (3)" on a clash (4.2)."""
    if name not in taken:
        return name
    for n in range(2, 100):
        candidate = f"{name[: 64 - len(f' ({n})')]} ({n})"
        if candidate not in taken:
            return candidate
    return name


@sudarshan_screener_internal_bp.route("/push", methods=["POST"])
def internal_push():
    """A run's summary (or a state change) for the open apps: emitted to the owner's room only,
    inside this request (extensions.emit_from_any_thread); the answer is the room's size."""
    args, refusal = _open_internal("push")
    if refusal is not None:
        return refusal
    items = args.get("items")
    if not isinstance(items, list) or len(items) > 50:
        return _refused(400, "Bad push")
    for item in items:
        if not isinstance(item, dict):
            continue
        event, data = item.get("event"), item.get("data")
        if event in PUSH_EVENTS and isinstance(data, dict):
            emit_from_any_thread(event, data, to=ROOM, namespace=NAMESPACE)
    return jsonify({"status": "success", "screens": room_size()})


def room_size() -> int:
    """How many open apps are in the owner's room (one worker, no message queue)."""
    server = getattr(socketio, "server", None)
    rooms = getattr(getattr(server, "manager", None), "rooms", None)
    if not isinstance(rooms, dict):
        return 0
    members = rooms.get(NAMESPACE, {}).get(ROOM)
    return len(members) if members else 0


@socketio.on("sudarshan_screener_join")
def _join_owner_room(data=None):
    """An open app joins the owner's room, if its session is the owner's."""
    if not is_session_valid() or not _is_owner():
        return {"ok": False, "message": WORDS["forbidden"]}
    join_room(ROOM)
    return {"ok": True}


@sudarshan_screener_internal_bp.record_once
def _exempt_internal_routes_from_csrf(state):
    """The service has no browser session to forge; its requests are authenticated by the seal."""
    csrf = state.app.extensions.get("csrf") or getattr(state.app, "csrf", None)
    if csrf is not None:
        csrf.exempt(internal_watchlist)
        csrf.exempt(internal_push)
