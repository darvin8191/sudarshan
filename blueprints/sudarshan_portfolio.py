"""SUDARSHAN (M7.18): the portfolio page's bridge to the sudarshan-portfolio service.

This blueprint is a SUDARSHAN addition to OpenAlgo; the portfolio itself lives in the
sudarshan-portfolio service (``sudarshan/portfolio/README.md``). It keeps no state of its own:
every request is checked here and forwarded, sealed, to the service's local API on 127.0.0.1:5583
(``sudarshan/portfolio/api.py``), whose answer comes back in the phone's envelope.

Every route of the contract's table (``OPS`` in ``sudarshan/portfolio/core/kinds.py``) is built
from that one table, so a later op never means editing this file. Each route is checked in this
order: an OpenAlgo session (401), the owner himself (403 for any other session, reading included),
CSRF on writes, and a limiter on the class of the op: reads 120 a minute, writes 60, imports 10,
and a dry run of an entry 120 (it changes nothing and has a limiter of its own, cross-design R9).

How it forwards:
- First, who listens on the port is looked up (``sudarshan.alerts.loopback``): nothing goes to a
  port another user's program holds, and nobody listening answers at once.
- The body is one Fernet token under ``PORTFOLIO_API_INFO`` (60 s, once), ``{"op", "nonce",
  "principal": "owner", "args": {...}}``, made by the handoff module's own ``seal``. It goes
  through the shared httpx client with an explicit timeout (3 s for a read, 6 s for a write, 15 s
  for an import, 12 s for Upstox), no redirect is followed, and a write is never retried. Under
  eventlet that is a green socket wait that yields to the hub; under gthread it holds one pool
  thread for at most that. No thread, scheduler or event loop is added to the app.
- ``args`` come from three places: the ``<id>`` of the route, the query string of a GET (read as
  the table's *query* column says) and the JSON body (64 KiB). An import is a multipart upload
  whose file is passed on as text, at most 1,200,000 bytes, and the sealed request at most 2 MiB.
- The service's answer is passed on as the contract's ``PortfolioAnswer``: ``{"status":
  "success", "enabled": true, "data": ...}``, ``enabled: false`` when the portfolio is switched
  off, or ``{"status": "error", "error": {"code", "message"}}``. The page reads the code, never
  the HTTP status. Whether the portfolio is on is the service's own word (cross-design R3), never
  a second switch read here.
- A write that was sent and not answered is "Not confirmed", never a failure.
"""

import os
import re
from functools import wraps

import httpx
from flask import Blueprint, jsonify, request, session
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from database.user_db import find_user_by_username
from limiter import limiter
from sudarshan.alerts import loopback
from sudarshan.autologin.config import is_loopback_url
from sudarshan.autologin.handoff import seal
from sudarshan.portfolio.core import kinds
from sudarshan.vault import PORTFOLIO_API_INFO, fernet_for, pepper_from
from utils.httpx_client import get_httpx_client
from utils.lazy import LazyInit
from utils.logging import get_logger
from utils.session import is_session_valid, revoke_user_tokens
from utils.thread_safe_cache import MISSING, LockedTTLCache

logger = get_logger(__name__)

sudarshan_portfolio_bp = Blueprint("sudarshan_portfolio", __name__, url_prefix=kinds.API_PREFIX)

PRINCIPAL = "owner"
SERVICE_PORT = kinds.DEFAULT_HTTP_PORT
SERVICE_URL = f"http://{kinds.LOCAL_HOST}:{SERVICE_PORT}"
CONNECT_TIMEOUT_S = 1.0
#: The most a query value or a name in a list may be, and the most items a list may hold.
MAX_QUERY_TEXT = 200
MAX_QUERY_ITEMS = 100
LIMITS = {
    "read": f"{kinds.LIMITS_PER_MIN['read']} per minute",
    "upstox": f"{kinds.LIMITS_PER_MIN['read']} per minute",  # the upstox ops count as reads
    "write": f"{kinds.LIMITS_PER_MIN['write']} per minute",
    "import": f"{kinds.LIMITS_PER_MIN['import']} per minute",
    "dry_run": f"{kinds.LIMITS_PER_MIN['dry_run']} per minute",
}
#: One scope for a class, so the class is what is limited, not each route of it.
SCOPES = {cls: f"sudarshan-portfolio-{cls}" for cls in LIMITS}
WORDS = {
    **kinds.WORDS,
    "held": "Another program on this server holds the portfolio service's port, so nothing was "
    "sent to it. Stop that program, then restart sudarshan-portfolio.",
    "sealed": "The portfolio service could not confirm that this request came from OpenAlgo. If "
    "OpenAlgo's .env was changed, restart sudarshan-portfolio.",
    "failed": "The portfolio could not do that just now. Your entries are safe; try again in a "
    "minute.",
    "unreadable": "That request could not be read. Open the page again, then repeat it.",
    "too_big": "That is too large for the portfolio. Send less at a time.",
    "not_text": "This file is not text. Save it as a CSV file, then import it again.",
    "no_file": "Choose a file to import.",
}

_ERROR_CODES = frozenset(kinds.ERROR_CODES)
#: Codes only the bridge makes; the service's answer of one of these is a fault, not a message.
_BRIDGE_ONLY = frozenset({"unauthorized", "forbidden", "rate_limited", "not_confirmed"})

_loopback_warned = LockedTTLCache(maxsize=8, ttl=600)
_fernet_lazy = LazyInit(
    lambda: fernet_for(pepper_from(os.environ), PORTFOLIO_API_INFO), "sudarshan-portfolio-seal"
)


def _fernet():
    return _fernet_lazy.get()


def _service_url() -> str:
    """The service's local API: the setting when it is plain http on this server, else 5583."""
    port = (os.environ.get("SUDARSHAN_PORTFOLIO_HTTP_PORT") or "").strip()
    fallback = (
        f"http://{kinds.LOCAL_HOST}:{port}"
        if port.isdecimal() and 0 < int(port) < 65536
        else SERVICE_URL
    )
    url = (os.environ.get("SUDARSHAN_PORTFOLIO_API_URL") or fallback).rstrip("/")
    return url if url.startswith("http://") and is_loopback_url(url) else fallback


def _warn_once(kind: str, message: str, *args) -> None:
    with _loopback_warned.lock:
        first = _loopback_warned.get(kind, MISSING) is MISSING
        if first:
            _loopback_warned[kind] = True
    if first:
        logger.warning(message, *args)


# -- answers -----------------------------------------------------------------------------------


def _error(code: str, message: str | None = None, **extra):
    """The phone's error envelope: the code it acts on and a sentence for the owner."""
    error = {"code": code, "message": message or WORDS.get(code) or WORDS["failed"]}
    error.update({key: value for key, value in extra.items() if value is not None})
    return jsonify({"status": "error", "error": error}), kinds.ERROR_STATUS[code]


def _answer(body: dict):
    """The service's answer as the phone's ``PortfolioAnswer`` with the HTTP status of its code."""
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
            "The portfolio: sudarshan-portfolio refused OpenAlgo's sealed request (%s); "
            "the two may hold different peppers", code,
        )  # fmt: skip
        return _error("unavailable", WORDS["sealed"])
    if code in _ERROR_CODES and code not in _BRIDGE_ONLY:
        return _error(code, message, entry_id=error.get("entry_id"), field=error.get("field"))
    # a transport refusal ('busy', 'size', 'timeout', 'failed', ...): the service's own words
    return _error("unavailable", message or WORDS["failed"])


# -- who may call ------------------------------------------------------------------------------


def _session_required(view):
    """No OpenAlgo session is 401 in the phone's envelope, never a redirect to the login page."""

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
    on every route, reading included (a family viewer never holds the owner's portfolio)."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        owner = find_user_by_username()
        if owner is None or session.get("user") != owner.username:
            return _error("forbidden")
        return view(*args, **kwargs)

    return wrapper


@sudarshan_portfolio_bp.errorhandler(429)
def _rate_limited(error):
    return _error("rate_limited")


@sudarshan_portfolio_bp.errorhandler(RequestEntityTooLarge)
def _too_large(error):
    upload = request.endpoint == f"{sudarshan_portfolio_bp.name}.import_preview"
    return _error("bad_request", WORDS["import_too_big"] if upload else WORDS["too_big"])


@sudarshan_portfolio_bp.errorhandler(Exception)
def _failed(error):
    """Nothing here ends in OpenAlgo's HTML error page: the page gets the envelope."""
    if isinstance(error, HTTPException):
        return error
    logger.exception("The portfolio: the bridge failed on a request")
    return _error("unavailable", WORDS["failed"])


# -- reading the request -----------------------------------------------------------------------


class _Unreadable(Exception):
    def __init__(self, message: str | None = None):
        super().__init__(message or WORDS["unreadable"])


def _text(raw: str) -> str:
    if not raw or len(raw) > MAX_QUERY_TEXT:
        raise _Unreadable()
    return raw


def _int(raw: str) -> int:
    if not re.fullmatch(r"-?[0-9]{1,15}", raw.strip()):
        raise _Unreadable()
    return int(raw)


def _day(raw: str) -> int:
    if not re.fullmatch(r"[0-9]{8}", raw.strip()):
        raise _Unreadable()
    return int(raw)


def _items(read, raw: str) -> list:
    parts = [part for part in raw.split(",") if part.strip()]
    if len(parts) > MAX_QUERY_ITEMS:
        raise _Unreadable()
    return [read(part.strip()) for part in parts]


QUERY_READERS = {
    "int": _int,
    "day": _day,
    "str": _text,
    "ints": lambda raw: _items(_int, raw),
    "strs": lambda raw: _items(_text, raw),
}


def _json_body() -> dict:
    """The request's JSON as a dict (none is {}), read only up to the API's 64 KiB."""
    if (request.content_length or 0) > kinds.API_MAX_BYTES:
        raise RequestEntityTooLarge()
    request.max_content_length = kinds.API_MAX_BYTES  # a body with no length is cut here too
    if not request.get_data(cache=True):
        return {}
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise _Unreadable()
    return body


def _upload() -> dict:
    """An import's arguments: the form's fields and the file as UTF-8 text, sized before sealing."""
    if (request.content_length or 0) > kinds.IMPORT_MAX_BYTES:
        raise RequestEntityTooLarge()
    request.max_content_length = kinds.IMPORT_MAX_BYTES
    file = request.files.get("file")
    if file is None:
        raise _Unreadable(WORDS["no_file"])
    raw = file.stream.read(kinds.IMPORT_MAX_FILE_BYTES + 1)
    if len(raw) > kinds.IMPORT_MAX_FILE_BYTES:
        raise _Unreadable(WORDS["import_too_big"])
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _Unreadable(WORDS["not_text"]) from None
    fields: dict = {"filename": (file.filename or "import.csv")[:200], "text": text}
    for name, value in request.form.items():
        if name in ("format", "account_id", "version"):
            fields[name] = _int(value) if name != "format" else _text(value)
    return fields


def _args(spec: kinds.OpSpec, ident: int | None) -> dict:
    if spec.upload:
        args = _upload()
    elif spec.method == "GET":
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


def _listener():
    """None when a request may go to the service's port; else the answer to give instead."""
    held = loopback.holder(_service_url())
    if held == loopback.NOBODY:
        _warn_once("nobody", "The portfolio: nothing listens on sudarshan-portfolio's port")
        return _error("unavailable", WORDS["not_answering"])
    if held == loopback.OTHER:
        _warn_once("other", "The portfolio: %s; nothing was sent", loopback.refusal(_service_url()))
        return _error("unavailable", WORDS["held"])
    return None  # this user's own program, or no way to tell (no /proc): the seal still holds


def _timeout_for(spec: kinds.OpSpec) -> float:
    return float(kinds.TIMEOUTS_S[spec.cls if spec.cls in kinds.TIMEOUTS_S else "read"])


def _call(spec: kinds.OpSpec, args: dict):
    """One sealed op at the service; its answer in the phone's envelope, or the bridge's own."""
    refused = _listener()
    if refused is not None:
        return refused
    blob = seal(_fernet(), spec.name, principal=PRINCIPAL, args=args)
    if len(blob) > (kinds.IMPORT_MAX_BYTES if spec.upload else kinds.API_MAX_BYTES):
        return _error("bad_request", WORDS["import_too_big"] if spec.upload else WORDS["too_big"])
    write = spec.cls in ("write", "import")
    try:
        answer = get_httpx_client().post(
            f"{_service_url()}/v1/{spec.name}",
            content=blob,
            headers={"Content-Type": "application/octet-stream", "Accept": "application/json"},
            timeout=httpx.Timeout(_timeout_for(spec), connect=CONNECT_TIMEOUT_S),
            follow_redirects=False,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
        _warn_once(
            "call", "The portfolio: sudarshan-portfolio did not take a %s request", spec.name
        )
        return _error("unavailable", WORDS["not_answering"])  # never sent
    except httpx.HTTPError:
        _warn_once(
            "call", "The portfolio: sudarshan-portfolio did not answer a %s request", spec.name
        )
        if write:  # it may have been done: never say it was not
            return _error("not_confirmed")
        return _error("unavailable", WORDS["not_answering"])
    try:
        body = answer.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or body.get("status") not in ("success", "error"):
        logger.error(
            "The portfolio: sudarshan-portfolio answered %s without its envelope", spec.name
        )
        if write:
            return _error("not_confirmed")
        return _error("unavailable", WORDS["not_answering"])
    return _answer(body)


def _dry_run() -> bool:
    """A dry run of an entry: it changes nothing and has a limiter of its own."""
    if (request.endpoint or "").rpartition(".")[2] not in kinds.DRY_RUN_OPS:
        return False
    if (request.content_length or 0) > kinds.API_MAX_BYTES:
        return False
    body = request.get_json(silent=True)
    return isinstance(body, dict) and body.get("dry_run") is True


def _register(spec: kinds.OpSpec) -> None:
    def view(**kwargs):
        try:
            args = _args(spec, kwargs.get("id"))
        except _Unreadable as unreadable:
            return _error("bad_request", str(unreadable))
        return _call(spec, args)

    view.__name__ = spec.name
    guarded = view
    if spec.name in kinds.DRY_RUN_OPS:
        guarded = limiter.shared_limit(
            LIMITS["write"], scope=SCOPES["write"], exempt_when=_dry_run
        )(guarded)
        guarded = limiter.shared_limit(
            LIMITS["dry_run"], scope=SCOPES["dry_run"], exempt_when=lambda: not _dry_run()
        )(guarded)
    else:
        guarded = limiter.shared_limit(LIMITS[spec.cls], scope=SCOPES[spec.cls])(guarded)
    guarded = _owner_only(guarded)
    guarded = _session_required(guarded)
    rule = spec.route.replace("<id>", "<int:id>")
    sudarshan_portfolio_bp.add_url_rule(rule, endpoint=spec.name, view_func=guarded,
                                        methods=[spec.method])  # fmt: skip


for _spec in kinds.OPS:
    _register(_spec)
