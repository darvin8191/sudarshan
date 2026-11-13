"""SUDARSHAN (M8.4): the owner's bridge for Watchlists v2, inside OpenAlgo by the written
exception in ``sudarshan/watchlists/README.md`` (no thread, timer, scheduler or event loop here).

Every route is built from ``contract.OPS`` and exists only when a handler for it is found in
``sudarshan/watchlists/ops/``. Each request is checked in this order: an OpenAlgo session
(``session``, in the envelope, never a redirect), the owner himself (``owner_only`` for any other
session, reads included), CSRF on POST, PUT, PATCH and DELETE (a lapsed token is ``session``),
and then the class's limit (``busy``). The limit wraps only what runs after those checks and is
counted per owner, so a request without a session, another sign-in's request or a forged one is
refused before it counts and can never spend the owner's budget (the alerts and portfolio bridges
put their limiters inside their checks the same way). Then the switch ``SUDARSHAN_WATCHLISTS_V2``:
off, ``state`` answers ``{"enabled": false}`` and every other op ``off``, and nothing is opened or
read. Then the record check (the store opens once; the outcome is kept for a few seconds so a
broken file is not reopened on every request), then the request is read against the contract and
the handler runs.

Answers are ``{"status": "ok", "data": ...}`` or ``{"status": "error", "code", "message",
"field"?}`` with ``contract.ERROR_STATUS[code]`` as the HTTP status. Messages are sentences for
the owner; the cause of a failure goes to the error log.
"""

import math
import re
from functools import wraps

from flask import Blueprint, current_app, jsonify, request, session
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from extensions import is_owner_session
from limiter import limiter
from sudarshan.watchlists import bridge, contract, ops, versions
from sudarshan.watchlists.engine import RecordTooNew, RecordUnreadable, ready
from sudarshan.watchlists.ops.state import enabled
from sudarshan.watchlists.words.base import MESSAGES, TOO_BIG
from utils.logging import get_logger
from utils.session import is_session_valid
from utils.thread_safe_cache import MISSING, LockedTTLCache

logger = get_logger(__name__)

sudarshan_watchlists_bp = Blueprint(
    "sudarshan_watchlists", __name__, url_prefix=contract.API_PREFIX
)

MAX_BODY_BYTES = 128 * 1024
CHANGING = ("POST", "PUT", "PATCH", "DELETE")
LIMITS = {cls: f"{n} per minute" for cls, n in contract.LIMITS_PER_MIN.items()}
SCOPES = {cls: f"sudarshan-watchlists-{cls}" for cls in LIMITS}
#: The outcome of the record check for a few seconds: ``None`` is usable, else the owner's words.
_record = LockedTTLCache(maxsize=2, ttl=5)


# -- answers -------------------------------------------------------------------------------------
def _answer(body: dict, status: int = 200):
    response = jsonify(body)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _error(code: str, message: str | None = None, field: str | None = None):
    body = {"status": "error", "code": code, "message": message or MESSAGES[code]}
    if code == "invalid" and field:
        body["field"] = field
    return _answer(body, contract.ERROR_STATUS[code])


class _Bad(Exception):
    def __init__(self, field: str):
        super().__init__(field)
        self.field = field


# -- reading a request against the contract --------------------------------------------------------
def _from_query(raw: str, typ: str, name: str):
    """A query-string value as its type (primitives and enums only)."""
    kind = contract.base_type(typ)
    if kind == "int" and re.fullmatch(r"-?[0-9]{1,15}", raw):
        return int(raw)
    if kind == "number":
        try:
            value = float(raw)
        except ValueError:
            raise _Bad(name) from None
        if math.isfinite(value):
            return value
    if kind == "bool" and raw.lower() in ("true", "false", "1", "0"):
        return raw.lower() in ("true", "1")
    if kind == "string" or kind in contract.ENUMS:
        return raw
    raise _Bad(name)


def _valid(value, typ: str) -> bool:
    """Whether ``value`` is a ``typ`` of the contract (records need their required fields)."""
    if typ.endswith("|null"):
        return value is None or _valid(value, typ[:-5])
    if typ.endswith("[]"):
        return isinstance(value, list) and all(_valid(v, typ[:-2]) for v in value)
    if typ == "string":
        return isinstance(value, str)
    if typ == "bool":
        return isinstance(value, bool)
    if typ in ("int", "number"):
        numeric = isinstance(value, int | float) and not isinstance(value, bool)
        return numeric and math.isfinite(value) and (typ == "number" or isinstance(value, int))
    if typ in contract.ENUMS:
        return value in contract.ENUMS[typ]
    if typ in contract.RECORDS:
        return isinstance(value, dict) and _fields_ok(value, contract.record(typ))
    return False


def _fields_ok(source: dict, fields) -> bool:
    return all(
        (f.name in source and _valid(source[f.name], f.type)) if not f.optional
        else (f.name not in source or _valid(source[f.name], f.type))
        for f in fields
    )  # fmt: skip


def _read(op: contract.OpSpec, route: dict) -> dict:
    """The op's request: its declared fields, from the query string (GET, DELETE) or the JSON
    body, checked against the contract. A route's id must agree with the same field in the body."""
    fields = contract.fields_of(op.req)
    if op.method in ("GET", "DELETE"):
        source = {f.name: _from_query(request.args[f.name], f.type, f.name)
                  for f in fields if f.name in request.args}  # fmt: skip
    else:
        source = request.get_json(silent=True) if request.get_data(cache=True) else {}
        if not isinstance(source, dict):
            raise _Bad("body")
    for name, value in route.items():
        if name in source and source[name] != value:
            raise _Bad(name)
        source[name] = value
    for f in fields:
        if f.name in source and not _valid(source[f.name], f.type):
            raise _Bad(f.name)
        if f.name not in source and not f.optional:
            raise _Bad(f.name)
    return {f.name: source[f.name] for f in fields if f.name in source}


# -- the checks, in order ----------------------------------------------------------------------
def _record_outcome() -> str | None:
    """None when the store can be used, else the owner's words (kept for a few seconds)."""
    outcome = _record.get("record", MISSING)
    if outcome is MISSING:
        try:
            ready()
            outcome = None
        except (RecordTooNew, RecordUnreadable) as exc:
            outcome = str(exc)  # the cause is already in the error log
        _record["record"] = outcome
    return outcome


def _owner_key() -> str:
    """The limiter's key: the session's user. Only a request that passed the session, owner and
    CSRF checks reaches the limiter, so this is always the owner, from whichever device."""
    return f"owner:{session.get('user') or ''}"


def _checked(limited):
    """The checks a request meets before it counts against its class: an OpenAlgo session, the
    owner, CSRF on a change. Only then is ``limited`` (the class's limit around the op) called."""

    def view(**route):
        try:
            if not is_session_valid():
                return _error("session")
            if not is_owner_session():
                return _error("owner_only")
        except Exception:
            logger.exception("Watchlists: the session could not be checked")
            return _error("failed")
        if request.method in CHANGING and current_app.config.get("WTF_CSRF_ENABLED", True):
            csrf = current_app.extensions.get("csrf")
            try:
                if csrf is None:
                    raise CSRFError()
                csrf.protect()
            except CSRFError:
                return _error("session")
        return limited(**route)

    return view


def _served(op: contract.OpSpec, handler):
    """What a request meets once it passed the checks and its class's limit: the switch, the
    record check, the request read against the contract, the handler and its bump. A write that
    bumped answers ``lists_version``, the lists version of the push it sent, so the phone can
    skip that push. A write that refuses after committing part of its change names those refs
    (``OpError.changed``) and is bumped and pushed for them, once, before the refusal is
    answered."""

    @wraps(handler)
    def view(**route):
        if not enabled():
            if op.name == "state":
                return _answer({"status": "ok", "data": {"enabled": False}})
            return _error("off")
        words = _record_outcome()
        if words is not None:
            return _error("record", words)
        declared = getattr(handler, "bump", None)
        try:
            args = _read(op, route)
            data = handler(args)
            if declared is not None:  # a write op: the change is committed, tell the phone
                why, refs = declared
                changed = refs(args, data) if refs else ()
                if changed is not ops.UNCHANGED:  # a write that changed nothing tells nobody
                    pushed = versions.bump(why, changed)
                    if isinstance(data, dict):  # the phone knows its own write's push by it
                        data["lists_version"] = pushed
            return _answer({"status": "ok", "data": data})
        except _Bad as bad:
            return _error("invalid", field=bad.field)
        except ops.OpError as refused:
            if refused.changed:  # part of the change committed before the refusal: say so once
                versions.bump(declared[0] if declared else "lists", refused.changed)
            return _error(refused.code, refused.message, refused.field)
        except (RecordTooNew, RecordUnreadable) as exc:
            _record["record"] = str(exc)
            return _error("record", str(exc))

    return view


# -- the routes ----------------------------------------------------------------------------------
def attach(bp: Blueprint, handlers: dict) -> Blueprint:
    """Build the routes of ``contract.OPS`` that have a handler on ``bp``, with its answers for
    the refusals no view sees (a test attaches its own handlers to a blueprint of its own)."""
    for op in contract.OPS:
        if op.name not in handlers:
            continue  # not built yet: no route, so a request is the app's own 404
        served = _served(op, handlers[op.name])
        served.__name__ = f"{bp.name}_{op.name}"  # one name per blueprint and op for the limiter
        limited = limiter.shared_limit(LIMITS[op.cls], scope=SCOPES[op.cls],
                                       key_func=_owner_key)(served)  # fmt: skip
        view = _checked(limited)
        view.__name__ = f"{bp.name}_{op.name}_checked"
        bp.add_url_rule(
            op.route.replace("<id>", "<int:id>"), endpoint=op.name, view_func=view,
            methods=[op.method],
        )  # fmt: skip

    @bp.errorhandler(429)
    def busy(error):
        return _error("busy")

    @bp.errorhandler(RequestEntityTooLarge)
    def too_big(error):
        return _error("invalid", TOO_BIG, "body")

    @bp.errorhandler(CSRFError)
    def lapsed(error):
        return _error("session")

    @bp.errorhandler(Exception)
    def failed(error):
        """Nothing here ends in OpenAlgo's error page: the app gets the envelope."""
        if isinstance(error, HTTPException):
            return error
        logger.exception("Watchlists: the bridge failed on a request")
        return _error("failed")

    @bp.before_request
    def bounded():
        """A body past the bridge's size is refused before it is read."""
        request.max_content_length = MAX_BODY_BYTES

    @bp.record_once
    def install(state):
        """CSRF is checked in the view, after the session and the owner; the app's own check,
        which runs first, is let go for this blueprint. Bridge parts then install themselves."""
        csrf = state.app.extensions.get("csrf") or getattr(state.app, "csrf", None)
        if csrf is not None:
            csrf.exempt(bp)
        bridge.install_all(state.app)

    return bp


attach(sudarshan_watchlists_bp, ops.discover())
