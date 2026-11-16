"""SUDARSHAN (M4.18, T7a): chart documents across devices, inside OpenAlgo by the watchlists
exception's mechanics (``sudarshan/charts/README.md``; no thread, timer, scheduler or event loop).

    GET /sudarshan/charts/api/state
    GET /sudarshan/charts/api/doc?kind=drawings&key=NSE:INFY[&have=<revision>]
    PUT /sudarshan/charts/api/doc   {"kind", "key", "base", "body"}

Each request is checked in this order: an OpenAlgo session (``session``, 401, in the envelope,
never a redirect), the owner himself (``owner_only``, 403, reads included; a family phone has no
OpenAlgo session at all and is refused at the first), CSRF on the PUT (a lapsed token is
``session``), and then its class's limit (``busy``), counted per owner, so a refused caller never
spends it. Then the switch ``SUDARSHAN_CHARTS_SYNC``: off, ``state`` answers ``{"enabled": false}``
and the two document routes ``off``, and nothing is opened or read. Then the record check, then
the request. A body past ``MAX_REQUEST_BYTES`` is refused from its length, before it is read.

Answers are ``{"status": "ok", "data": ...}`` or ``{"status": "error", "code", "message",
"field"?}``. A write names the revision it replaces (``base``, 0 for a document the device knows
of none); one the document no longer has is ``stale`` (409) and carries the document as it is
now in ``doc`` (null when there is none), so the device can show it. ``have`` on a read leaves
the body out when the device already holds that revision. Messages are sentences for the owner;
the cause of a failure goes to the error log.
"""

from __future__ import annotations

import json
import os
import re

from flask import Blueprint, Response, current_app, jsonify, request, session
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from extensions import is_owner_session
from limiter import limiter
from sudarshan.charts import schema, store
from sudarshan.charts.words import MESSAGES
from utils.logging import get_logger
from utils.session import is_session_valid
from utils.thread_safe_cache import MISSING, LockedTTLCache

logger = get_logger(__name__)

sudarshan_charts_bp = Blueprint("sudarshan_charts", __name__, url_prefix="/sudarshan/charts/api")

SWITCH_ENV = "SUDARSHAN_CHARTS_SYNC"
_ON = ("on", "true", "1", "yes")
#: The largest body a write may carry, with room for the few fields around it.
MAX_REQUEST_BYTES = schema.MAX_BODY_BYTES + 64 * 1024
LIMITS = {"read": "300 per minute", "write": "120 per minute"}
ERROR_STATUS = {
    "invalid": 400, "session": 401, "owner_only": 403, "stale": 409, "too_big": 413,
    "limit": 422, "busy": 429, "failed": 500, "off": 503, "record": 503,
}  # fmt: skip
_REVISION = re.compile(r"[0-9]{1,16}")
#: The outcome of the record check for a few seconds: ``None`` is usable, else the owner's words.
_record = LockedTTLCache(maxsize=2, ttl=5)


def enabled() -> bool:
    """True when the owner has switched chart documents on the server on (read at call time)."""
    raw = os.environ.get(SWITCH_ENV) or ""
    return raw.strip().strip("'\"").lower() in _ON


# -- answers -------------------------------------------------------------------------------------
def _sent(text: str, status: int = 200) -> Response:
    response = Response(text, status=status, mimetype="application/json")
    response.headers["Cache-Control"] = "no-store"
    return response


def _answer(body: dict, status: int = 200) -> Response:
    response = jsonify(body)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _error(code: str, message: str | None = None, field: str | None = None) -> Response:
    body = {"status": "error", "code": code, "message": message or MESSAGES[code]}
    if code == "invalid" and field:
        body["field"] = field
    return _answer(body, ERROR_STATUS[code])


def _doc_text(doc: store.Doc | None) -> str:
    return "null" if doc is None else doc.to_json()


def _stale(current: store.Doc | None) -> Response:
    head = json.dumps({"status": "error", "code": "stale", "message": MESSAGES["stale"]})
    return _sent(f'{head[:-1]}, "doc": {_doc_text(current)}}}', ERROR_STATUS["stale"])


# -- the checks, in order ------------------------------------------------------------------------
def _owner_key() -> str:
    """The limiter's key: only a request that passed the session, owner and CSRF checks reaches
    the limiter, so this is always the owner, from whichever device."""
    return f"owner:{session.get('user') or ''}"


def _checked(limited):
    """The checks a request meets before it counts against its class: an OpenAlgo session, the
    owner, CSRF on a change. Only then is ``limited`` (the class's limit around the view) called."""

    def view():
        try:
            if not is_session_valid():
                return _error("session")
            if not is_owner_session():
                return _error("owner_only")
        except Exception:
            logger.exception("Charts: the session could not be checked")
            return _error("failed")
        if request.method != "GET" and current_app.config.get("WTF_CSRF_ENABLED", True):
            csrf = current_app.extensions.get("csrf")
            try:
                if csrf is None:
                    raise CSRFError()
                csrf.protect()
            except CSRFError:
                return _error("session")
        return limited()

    return view


def _record_outcome() -> str | None:
    """None when the store can be used, else the owner's words (kept for a few seconds, so a
    broken file is not reopened on every request)."""
    outcome = _record.get("record", MISSING)
    if outcome is MISSING:
        try:
            store.ready()
            outcome = None
        except (store.RecordTooNew, store.RecordUnreadable) as exc:
            outcome = str(exc)  # the cause is already in the error log
        _record["record"] = outcome
    return outcome


def _served(view):
    """The switch and the record check around a document route, and the store's refusals."""

    def inner():
        if not enabled():
            return _error("off")
        words = _record_outcome()
        if words is not None:
            return _error("record", words)
        try:
            return view()
        except store.Invalid as bad:
            return _error("invalid", field=bad.field)
        except store.TooBig:
            return _error("too_big")
        except store.Full:
            return _error("limit")
        except store.Stale as stale:
            return _stale(stale.current)
        except (store.RecordTooNew, store.RecordUnreadable) as exc:
            _record["record"] = str(exc)
            return _error("record", str(exc))

    return inner


# -- the routes ----------------------------------------------------------------------------------
def _state():
    """The switch and the limits; it opens nothing, so the phone can ask on every visit."""
    if not enabled():
        return _answer({"status": "ok", "data": {"enabled": False}})
    data = {"enabled": True, "max_bytes": schema.MAX_BODY_BYTES, "limits": dict(store.LIMITS)}
    return _answer({"status": "ok", "data": data})


def _kind(value: object) -> str:
    if value not in schema.KINDS:
        raise store.Invalid("kind")
    return value


def _doc_get():
    kind = _kind(request.args.get("kind"))
    key = store.check_key(request.args.get("key"))
    have = request.args.get("have")
    if have is not None and not _REVISION.fullmatch(have):
        raise store.Invalid("have")
    doc = store.read(session.get("user") or "", kind, key, None if have is None else int(have))
    return _sent(f'{{"status": "ok", "data": {{"doc": {_doc_text(doc)}}}}}')


def _doc_put():
    try:  # the length was bounded before this read; a nesting too deep for the parser is refused
        payload = json.loads(request.get_data(cache=False) or b"null")
    except (ValueError, RecursionError):
        raise store.Invalid("body") from None
    if not isinstance(payload, dict):
        raise store.Invalid("body")
    kind = _kind(payload.get("kind"))
    key = store.check_key(payload.get("key"))
    base = payload.get("base")
    if isinstance(base, bool) or not isinstance(base, int) or not 0 <= base < 2**53:
        raise store.Invalid("base")
    if "body" not in payload:
        raise store.Invalid("body")
    text = store.check_body(kind, payload["body"])
    doc = store.write(session.get("user") or "", kind, key, base, text)
    return _sent(f'{{"status": "ok", "data": {{"doc": {doc.to_json()}}}}}')


def _route(rule: str, endpoint: str, method: str, view, cls: str) -> None:
    view.__name__ = f"sudarshan_charts_{endpoint}"  # one name per route for the limiter
    limited = limiter.shared_limit(LIMITS[cls], scope=f"sudarshan-charts-{cls}",
                                   key_func=_owner_key)(view)  # fmt: skip
    checked = _checked(limited)
    checked.__name__ = f"sudarshan_charts_{endpoint}_checked"
    sudarshan_charts_bp.add_url_rule(rule, endpoint=endpoint, view_func=checked, methods=[method])


_route("/state", "state", "GET", _state, "read")
_route("/doc", "doc_get", "GET", _served(_doc_get), "read")
_route("/doc", "doc_put", "PUT", _served(_doc_put), "write")


@sudarshan_charts_bp.errorhandler(429)
def busy(error):
    return _error("busy")


@sudarshan_charts_bp.errorhandler(RequestEntityTooLarge)
def too_big(error):
    return _error("too_big")


@sudarshan_charts_bp.errorhandler(CSRFError)
def lapsed(error):
    return _error("session")


@sudarshan_charts_bp.errorhandler(Exception)
def failed(error):
    """Nothing here ends in OpenAlgo's error page: the app gets the envelope."""
    if isinstance(error, HTTPException):
        return error
    logger.exception("Charts: the bridge failed on a request")
    return _error("failed")


@sudarshan_charts_bp.before_request
def bounded():
    """A body past the bridge's size is refused from its length, before it is read."""
    request.max_content_length = MAX_REQUEST_BYTES


@sudarshan_charts_bp.record_once
def install(state):
    """CSRF is checked in the view, after the session and the owner; the app's own check, which
    runs first, is let go for this blueprint."""
    csrf = state.app.extensions.get("csrf") or getattr(state.app, "csrf", None)
    if csrf is not None:
        csrf.exempt(sudarshan_charts_bp)
