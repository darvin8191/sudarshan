"""SUDARSHAN (M20.6, M8.15): OpenAlgo's two doors to the family view's service (P6 B1).

The family view is the sudarshan-family service (``sudarshan/family/``) on 127.0.0.1:5585, run as
its own account ``sudarshan-family``. This blueprint keeps no state but a nonce book, and nothing
in it is reachable by a family member: a family phone carries only the family cookie, which
OpenAlgo never reads, so every route below answers it as it answers a stranger.

1. **The owner bridge**, ``/sudarshan/family/api/<op>``, for the owner's Family page. One route per
   admin op of the family contract (``ADMIN_OPS`` in ``sudarshan/family/contract.py``), named as
   the op: a read (``READS``) is a GET with its fields in the query string, every other op a POST
   with its fields as a JSON object. Checked in this order: an OpenAlgo session (401
   ``signed_out``), the owner himself (403 ``forbidden`` for any other session, reading included),
   CSRF on a POST (403 ``csrf``), then the limiter of the op's class (reads 120 a minute, writes
   30), which counts only what passed the checks, so nobody else can spend the owner's budget. The
   fields are read against the contract (``invalid`` and the field otherwise) and only the op's
   own fields go on. Then the op is forwarded once, never retried:

   - who listens on 5585 is looked up first (``sudarshan.alerts.loopback``): only the account
     ``sudarshan-family`` may hold it (cross-design Y14). No such account, nobody listening,
     another account's program, or a table that cannot be read: nothing is sent;
   - the body is one Fernet token under ``FAMILY_ADMIN_INFO`` (60 s, once), ``{"op", "nonce",
     "principal": "owner", <the op's fields>}`` (the contract's admin door), posted to
     ``/v1/admin/<op>`` through the shared httpx client: 3 s for a read, 6 s for a write, 1 s to
     connect, no redirect followed, the answer read up to 2 MiB within that time. Under eventlet it
     is a green socket wait that yields to the hub; under gthread it holds one pool thread for at
     most that. No thread, timer or event loop is added to the app;
   - the answer is the contract's envelope, ``{"status": "success", "data"}`` (the ``Off`` record
     when the family view is switched off) or ``{"status": "error", "code", "message", "field"?}``
     in the service's own sentence, the bridge's words when it gave none fit to show; never a
     status code, an exception or an endpoint. Two codes are the bridge's own: ``unavailable``
     (the service could not be reached, or could not confirm the request came from here) and
     ``not_confirmed`` (a write sent and not answered: read the page again before repeating it).

2. **The internal route**, ``POST /sudarshan/family/internal/watchlists`` (M8.15): the family
   service reads the lists the owner shares. The contract gives no shape for it, so it is this:
   the body is one Fernet token under ``FAMILY_INTERNAL_INFO`` (60 s, once), ``{"op":
   "watchlists", "nonce", "principal": "sudarshan-family", "list_ids": [1 to 50 distinct ids]}``,
   at most 4 KiB. No session and no CSRF (the service has no browser to forge; the seal is its
   proof). A request carrying any header nginx sets is 404 before anything else; then the length
   (411), the size (413), the seal, op, nonce and principal (403; a full nonce book 503 ``busy``).
   The answer is ``{"status": "success", "data": {"lists": [{"id", "name", "created_at", "rows":
   [{"symbol", "exchange", "instrument_key", "name", "tick_size", "lot_size"}]}], "gone": [id]}}``:
   the owner's lists asked for, in that order, rows in the list's order, each resolved through
   OpenAlgo's master (``get_symbol_info``; null where the master lacks it), and the ids that name
   no list of his. OpenAlgo's own list rows only: never a flag, a note, a section or a follow row
   of Watchlists v2, nor anything else of the owner's. ``created_at`` tells a list from a later
   one OpenAlgo gave the same id.
"""

import json
import math
import os
import re
import time
from datetime import datetime

import httpx
from flask import Blueprint, current_app, jsonify, request, session
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from database.token_db_enhanced import get_symbol_info
from database.user_db import find_user_by_username
from extensions import is_owner_session
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
from sudarshan.family import contract
from sudarshan.vault import FAMILY_ADMIN_INFO, FAMILY_INTERNAL_INFO, fernet_for, pepper_from
from utils import real_threading
from utils.httpx_client import get_httpx_client
from utils.lazy import LazyInit
from utils.logging import get_logger
from utils.session import is_session_valid, revoke_user_tokens
from utils.thread_safe_cache import MISSING, LockedTTLCache

logger = get_logger(__name__)

sudarshan_family_bp = Blueprint("sudarshan_family", __name__, url_prefix="/sudarshan/family")
API_PREFIX = "/sudarshan/family/api"
INTERNAL_PATH = "/sudarshan/family/internal/watchlists"

SERVICE_URL = f"http://127.0.0.1:{contract.PORT}"
READS = frozenset({"state", "member_list", "device_list", "audit_list", "share_list"})
ADMIN_MAX_BYTES = contract.LIMITS["admin_bytes"]
ANSWER_MAX_BYTES = 2 * 1024 * 1024
CONNECT_TIMEOUT_S = 1.0
TIMEOUTS_S = {"read": 3.0, "write": 6.0}
LIMITS = {"read": "120 per minute", "write": "30 per minute"}
#: One scope for a class, so the class is what is limited, not each route of it.
SCOPES = {cls: f"sudarshan-family-{cls}" for cls in LIMITS}
MAX_INT = 2**53 - 1
INTERNAL_OP, INTERNAL_PRINCIPAL = "watchlists", contract.SERVICE
INTERNAL_MAX_BYTES = 4 * 1024
MAX_LIST_IDS = 50
#: Set by nginx on every proxied location: the family service calls 127.0.0.1:5000 directly.
PROXY_HEADERS = (
    "X-Forwarded-For",
    "X-Real-IP",
    "Forwarded",
    "X-Forwarded-Proto",
    "X-Forwarded-Host",
)
#: Where the listener check looks (a test points it at tables of its own).
TCP_TABLES = loopback.TCP_TABLES
#: The contract's codes, and the bridge's own two.
STATUS = {**contract.ERROR_STATUS, "unavailable": 503, "not_confirmed": 504}
#: What gives a message away as written for a developer: never shown to the owner.
_TECHNICAL = re.compile(r"Traceback|\w+(?:Error|Exception)\b|HTTP/?[\d\s]|://")
#: Codes of the viewer door: from the admin door they are a fault, never passed on (one of them
#: would send the owner to the login page).
_VIEWER_CODES = frozenset({"signin_failed", "signed_out", "csrf", "locked", "code_mismatch"})
WORDS = {
    "signed_out": "Your session has ended. Log in again.",
    "forbidden": "Only the account owner can manage the family view.",
    "csrf": "This page is out of date. Reload it and try again.",
    "busy": "Too many requests. Wait a minute, then try again.",
    "invalid": "That request could not be read. Open the page again, then repeat it.",
    "too_large": "That is too large to send to the family view.",
    "not_ready": "This part of the family view is not ready yet.",
    "not_set_up": "The family view is not set up on this server yet. Run its one-time set-up "
    "first (the family view's set-up in sudarshan/README.md).",
    "not_answering": "The family service is not answering. Nothing was changed; try again in a "
    "minute.",
    "held": "Another program on this server holds the family service's port, so nothing was sent "
    "to it. Stop that program, then restart sudarshan-family.",
    "unchecked": "It cannot be checked which program holds the family service's port, so nothing "
    "was sent to it.",
    "sealed": "The family service could not confirm that this request came from OpenAlgo. The "
    "deploy makes the family's keys again at its next run; try again after it.",
    "not_confirmed": "Not confirmed: open the Family page again to see whether it was done.",
    "failed": "The family view could not do that just now. Try again in a minute.",
}

_warned = LockedTTLCache(maxsize=16, ttl=600)
_admin_lazy = LazyInit(
    lambda: fernet_for(pepper_from(os.environ), FAMILY_ADMIN_INFO), "sudarshan-family-admin-seal"
)
_internal_lazy = LazyInit(
    lambda: fernet_for(pepper_from(os.environ), FAMILY_INTERNAL_INFO),
    "sudarshan-family-internal-seal",
)
#: The family service asks when a viewer opens a list and once a minute while one is open; the
#: book holds 2 x 60 s of 8 a second and answers busy past it rather than forget a live nonce.
_nonces = NonceBook(2 * SEAL_TTL_S * 8, lock=real_threading.RLock())


def _warn_once(kind: str, message: str, *args) -> None:
    with _warned.lock:
        first = _warned.get(kind, MISSING) is MISSING
        if first:
            _warned[kind] = True
    if first:
        logger.warning(message, *args)


def _service_url() -> str:
    """The family service: 127.0.0.1:5585, or a test's loopback stand-in, never another host."""
    url = (os.environ.get("SUDARSHAN_FAMILY_API_URL") or SERVICE_URL).rstrip("/")
    return url if url.startswith("http://") and is_loopback_url(url) else SERVICE_URL


def _family_uid() -> int | None:
    """The uid of the account ``sudarshan-family``; None where it does not exist."""
    try:
        import pwd
    except ImportError:  # not a Unix server: the family view runs only on one
        return None
    try:
        return pwd.getpwnam(contract.ACCOUNT).pw_uid
    except KeyError:
        return None


def _sentence(value) -> str | None:
    """A sentence the service wrote for a person, or None when it is not a plain one."""
    if not isinstance(value, str) or not 0 < len(value) <= 300 or not value.endswith("."):
        return None
    return value if value.isprintable() and not _TECHNICAL.search(value) else None


# -- answers -------------------------------------------------------------------------------------


def _answer(body: dict, status: int = 200):
    response = jsonify(body)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"  # members, invite links: never kept
    return response


def _error(code: str, message: str | None = None, field=None, status: int | None = None):
    body = {"status": "error", "code": code, "message": message or WORDS.get(code, WORDS["failed"])}
    if isinstance(field, str) and field:
        body["field"] = field
    return _answer(body, status or STATUS[code])


def _relay(status: int, body: dict):
    """The service's answer as the page's envelope."""
    if body.get("status") == "success":
        return _answer({"status": "success", "data": body.get("data")})
    code, message = body.get("code"), _sentence(body.get("message"))
    bare = body.get("message") in (None, "") or str(body.get("message")).lower() == "not found"
    if status == 501 or code == "not_ready" or (status == 404 and bare):
        return _error("not_ready", message)  # an op this service does not have yet
    if code == "forbidden":
        logger.error("Family: sudarshan-family could not open OpenAlgo's seal; peppers differ?")
        return _error("unavailable", message or WORDS["sealed"])
    if code in contract.ERROR_STATUS and code not in _VIEWER_CODES:
        return _error(code, message, body.get("field"))
    return _error("unavailable", message or WORDS["failed"])


# -- reading the request against the contract ----------------------------------------------------


class _Bad(Exception):
    def __init__(self, field: str):
        super().__init__(field)
        self.field = field


def _clean(value, typ: str, name: str):
    """``value`` as a ``typ`` of the contract, a record cut to its own fields; else ``_Bad``."""
    if typ.endswith("|null"):
        return None if value is None else _clean(value, typ[:-5], name)
    if typ.endswith("[]"):
        if not isinstance(value, list):
            raise _Bad(name)
        return [_clean(item, typ[:-2], name) for item in value]
    if typ in contract.RECORDS:
        if not isinstance(value, dict):
            raise _Bad(name)
        return _clean_fields(value, contract.record(typ), name)
    if typ == "string":
        ok = isinstance(value, str)
    elif typ == "bool":
        ok = isinstance(value, bool)
    elif typ in ("int", "number"):
        ok = type(value) is int or (typ == "number" and type(value) is float)
        ok = ok and math.isfinite(value) and abs(value) <= MAX_INT
    else:
        ok = typ == "json" or value in contract.ENUMS.get(typ, ())
    if not ok:
        raise _Bad(name)
    return value


def _clean_fields(source: dict, fields, name: str | None = None) -> dict:
    out = {}
    for field in fields:
        where = name or field.name
        if field.name in source:
            out[field.name] = _clean(source[field.name], field.type, where)
        elif not field.optional:
            raise _Bad(where)
    return out


def _from_query(raw: str, typ: str, name: str):
    base = contract.base_type(typ)
    if base == "int":
        if not re.fullmatch(r"-?[0-9]{1,15}", raw):
            raise _Bad(name)
        return int(raw)
    return raw  # a string or an enum, checked by _clean


def _fields(op: contract.SealedOp) -> dict:
    """The op's own fields from the query string (a read) or the JSON body (a write)."""
    fields = contract.parse_fields(op.req)
    if op.name in READS:
        source = {f.name: _from_query(request.args[f.name], f.type, f.name)
                  for f in fields if request.args.get(f.name, "") != ""}  # fmt: skip
        return _clean_fields(source, fields)
    if (request.content_length or 0) > ADMIN_MAX_BYTES:
        raise RequestEntityTooLarge()
    request.max_content_length = ADMIN_MAX_BYTES  # a body with no length is cut here too
    source = request.get_json(silent=True) if request.get_data(cache=True) else {}
    if not isinstance(source, dict):
        raise _Bad("body")
    return _clean_fields(source, fields)


# -- the forward -----------------------------------------------------------------------------------


class _Unsent(Exception):
    """The service took no request: nothing was done."""


class _Unanswered(Exception):
    """The request went out and no usable answer came back."""


def _listener(url: str):
    """None when the request may go to the family service's port; else the answer to give."""
    uid = _family_uid()
    if uid is None:
        _warn_once("account", "Family: there is no account sudarshan-family; nothing was sent")
        return _error("unavailable", WORDS["not_set_up"])
    held = loopback.holder(url, TCP_TABLES, expected_uid=uid)
    if held == loopback.OURS:
        return None
    reason = loopback.refusal(url, TCP_TABLES, expected_uid=uid)
    _warn_once(held, "Family: %s; nothing was sent", reason)
    words = {loopback.NOBODY: "not_answering", loopback.OTHER: "held"}.get(held, "unchecked")
    return _error("unavailable", WORDS[words])


def _post(url: str, blob: bytes, cls: str) -> tuple[int, bytes]:
    """One sealed POST: the status and the body read within the time and size allowed."""
    deadline = time.monotonic() + CONNECT_TIMEOUT_S + TIMEOUTS_S[cls]
    try:
        with get_httpx_client().stream(
            "POST", url, content=blob, follow_redirects=False,
            headers={"Content-Type": "application/octet-stream", "Accept": "application/json"},
            timeout=httpx.Timeout(TIMEOUTS_S[cls], connect=CONNECT_TIMEOUT_S),
        ) as answer:  # fmt: skip
            raw = bytearray()
            for chunk in answer.iter_bytes():
                raw += chunk
                if len(raw) > ANSWER_MAX_BYTES or time.monotonic() > deadline:
                    raise _Unanswered()
            return answer.status_code, bytes(raw)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
        raise _Unsent() from exc
    except httpx.HTTPError as exc:
        raise _Unanswered() from exc


def _send(op: contract.SealedOp, fields: dict):
    """The op once at the family service; its answer in the page's envelope."""
    url = _service_url()
    refused = _listener(url)
    if refused is not None:
        return refused
    cls = "read" if op.name in READS else "write"
    blob = seal(_admin_lazy.get(), op.name, principal=contract.ADMIN_PRINCIPAL, **fields)
    if len(blob) > ADMIN_MAX_BYTES:
        return _error("too_large")
    status, body = 0, None
    try:
        status, raw = _post(f"{url}{contract.ADMIN_PREFIX}{op.name}", blob, cls)
        body = json.loads(raw)
    except _Unsent:
        _warn_once("unsent", "Family: sudarshan-family did not take a %s request", op.name)
        return _error("unavailable", WORDS["not_answering"])
    except (_Unanswered, ValueError):
        pass
    if not isinstance(body, dict) or body.get("status") not in ("success", "error"):
        _warn_once("unanswered", "Family: sudarshan-family gave no answer to %s", op.name)
        if cls == "write":  # it may have been done: never say it was not
            return _error("not_confirmed")
        return _error("unavailable", WORDS["not_answering"])
    return _relay(status, body)


# -- who may call ----------------------------------------------------------------------------------


def _checked(op: contract.SealedOp, limited):
    """An OpenAlgo session, the owner, CSRF on a write; only then ``limited`` (the op, counted)."""

    def view():
        try:
            if not is_session_valid():
                revoke_user_tokens()
                session.clear()
                return _error("signed_out")
            if not is_owner_session():
                return _error("forbidden")
        except Exception:
            logger.exception("Family: the session could not be checked")
            return _error("failed")
        if op.name not in READS and current_app.config.get("WTF_CSRF_ENABLED", True):
            csrf = current_app.extensions.get("csrf")
            try:
                if csrf is None:
                    raise CSRFError()
                csrf.protect()
            except CSRFError:
                return _error("csrf")
        return limited()

    return view


#: Every view of this blueprint: the app's own CSRF check is let go for each (see the end).
_VIEWS = []


def _register(op: contract.SealedOp) -> None:
    def served():
        try:
            fields = _fields(op)
        except _Bad as bad:
            return _error("invalid", field=bad.field)
        return _send(op, fields)

    served.__name__ = f"family_{op.name}"  # one name per op for the limiter
    cls = "read" if op.name in READS else "write"
    limited = limiter.shared_limit(LIMITS[cls], scope=SCOPES[cls])(served)
    view = _checked(op, limited)
    view.__name__ = op.name
    method = "GET" if cls == "read" else "POST"
    sudarshan_family_bp.add_url_rule(f"/api/{op.name}", endpoint=op.name, view_func=view,
                                     methods=[method])  # fmt: skip
    _VIEWS.append(view)


for _op in contract.ADMIN_OPS:
    _register(_op)


@sudarshan_family_bp.errorhandler(429)
def _rate_limited(error):
    return _error("busy", status=429)


@sudarshan_family_bp.errorhandler(RequestEntityTooLarge)
def _too_large(error):
    return _error("too_large")


@sudarshan_family_bp.errorhandler(Exception)
def _failed(error):
    """Nothing here ends in OpenAlgo's HTML error page: the page gets the envelope."""
    if isinstance(error, HTTPException):
        return error
    logger.exception("Family: the bridge failed on a request")
    return _error("failed")


# -- the internal route (M8.15) ------------------------------------------------------------------

INTERNAL_WORDS = {
    "not_found": "Not found.",
    "invalid": "The request did not name between 1 and 50 list ids.",
    "too_large": "The request is too large.",
    "forbidden": "The request was not sealed by this server's family service.",
    "busy": "Too many requests in the last two minutes.",
    "failed": "The lists could not be read just now.",
}


def _refused(status: int, code: str):
    return _answer({"status": "error", "code": code, "message": INTERNAL_WORDS[code]}, status)


def _list_ids(value) -> list[int] | None:
    if not isinstance(value, list) or not 0 < len(value) <= MAX_LIST_IDS:
        return None
    ids = [i for i in value if type(i) is int and 0 < i <= 2**31 - 1]
    return ids if len(ids) == len(value) == len(set(ids)) else None


def _row(symbol: str, exchange: str) -> dict:
    info = get_symbol_info(symbol, exchange)
    return {"symbol": symbol, "exchange": exchange,
            "instrument_key": getattr(info, "token", None) or None,
            "name": getattr(info, "name", None) or None,
            "tick_size": getattr(info, "tick_size", None),
            "lot_size": getattr(info, "lotsize", None)}  # fmt: skip


def shared_lists(ids: list[int]) -> dict:
    """The owner's lists ``ids`` as the route answers them (see the module's note)."""
    from database import watchlist_db as oa

    owner = find_user_by_username()
    if owner is None:
        raise LookupError("this instance has no owner account")
    try:
        found = {
            w.id: (w.name, w.created_at, [(i.symbol, i.exchange) for i in w.items])
            for w in oa.db_session.query(oa.Watchlist)
            .filter(oa.Watchlist.user_id == owner.username, oa.Watchlist.id.in_(ids))
            .all()
        }
    finally:
        oa.db_session.remove()  # a read: the connection goes back at once
    lists = []
    for lid in (i for i in ids if i in found):
        name, made, items = found[lid]
        made = made.isoformat(sep=" ") if isinstance(made, datetime) else made
        lists.append({"id": lid, "name": name, "created_at": made or None,
                      "rows": [_row(symbol, exchange) for symbol, exchange in items]})  # fmt: skip
    return {"lists": lists, "gone": [i for i in ids if i not in found]}


@sudarshan_family_bp.route("/internal/watchlists", methods=["POST"])
def internal_watchlists():
    if any(header in request.headers for header in PROXY_HEADERS):
        return _refused(404, "not_found")
    length = request.content_length
    if length is None:
        return _answer({"status": "error", "code": "invalid", "message": "Length required."}, 411)
    if length > INTERNAL_MAX_BYTES:
        return _refused(413, "too_large")
    try:
        message = open_sealed(
            _internal_lazy.get(), request.get_data(cache=False), op=INTERNAL_OP, seen=_nonces,
            max_bytes=INTERNAL_MAX_BYTES,
        )  # fmt: skip
    except NoncesFull:
        _warn_once("busy", "Family: too many internal requests in the last two minutes")
        return _refused(503, "busy")
    except HandoffRejected as exc:
        _warn_once("refused", "Family: refused an internal request not sealed here (%s)", exc)
        return _refused(403, "forbidden")
    if message.get("principal") != INTERNAL_PRINCIPAL:
        _warn_once("principal", "Family: refused an internal request from another principal")
        return _refused(403, "forbidden")
    ids = _list_ids(message.get("list_ids"))
    if ids is None:
        return _refused(400, "invalid")
    try:
        return _answer({"status": "success", "data": shared_lists(ids)})
    except Exception:
        logger.exception("Family: the shared lists could not be read")
        return _refused(500, "failed")


@sudarshan_family_bp.record_once
def _csrf_in_the_view(state):
    """CSRF is checked in each owner view after the session and the owner; the internal route is
    sealed. So the app's own check, which runs first, is let go for these views only."""
    csrf = state.app.extensions.get("csrf") or getattr(state.app, "csrf", None)
    if csrf is not None:
        for view in (*_VIEWS, internal_watchlists):
            csrf.exempt(view)
