"""SUDARSHAN (M14.1, M15.10): the journal and reports pages' bridge to the sudarshan-journal service.

This blueprint is a SUDARSHAN addition to OpenAlgo; the journal itself lives in the
sudarshan-journal service (``sudarshan/journal/``). It keeps no state of its own: every request is
checked here and forwarded, sealed, to the service's local API on 127.0.0.1:5586, whose answer
comes back in the phone's envelope.

Routes, all under /sudarshan/journal/api (design 7.3; the op names are the journal contract's):

    GET    /state                 state            GET    /analytics         analytics
    GET    /trips                 trips            GET    /reports           reports_list
    GET    /trips/<id>            trip_get         GET    /reports/<id>      report_get
    PATCH  /trips/<id>            trip_update      GET    /reports/<id>/file report_file
    GET    /tags                  tags_list        POST   /reports/build     report_build
    POST   /tags                  tag_create       GET    /settings          settings_get
    PATCH  /tags/<id>             tag_update       PUT    /settings          settings_set
    GET    /playbooks             playbooks_list   POST   /reconcile         reconcile_now
    POST   /playbooks             playbook_create
    PATCH  /playbooks/<id>        playbook_update

Each route is checked in this order: an OpenAlgo session (401), the owner himself (403 for any
other session, reading included), CSRF on writes (the app's CSRFProtect), and a limiter on the
class of the op: reads 120 a minute, writes 60, downloads 20, the reconcile 2.

How it forwards:
- First, who listens on the port is looked up (``sudarshan.alerts.loopback``): nothing goes to a
  port another user's program holds, and nobody listening answers at once.
- The body is one Fernet token under ``JOURNAL_API_INFO`` (60 s, once), ``{"op", "nonce",
  "principal": "owner", "args": {...}}``, made by the handoff module's ``seal``, at most 64 KiB.
  It goes through the shared httpx client with an explicit timeout (3 s for a read, 6 s for a
  write or the reconcile, 20 s for a download), no redirect is followed, and a write is never
  retried. Under eventlet that is a green socket wait that yields to the hub; under gthread it
  holds one pool thread for at most that. No thread, scheduler or event loop is added to the app.
- ``args`` come from the ``<id>`` of the route, the query string of a GET (read as the table's
  query column says; the service checks the values) and the JSON body of a write.
- The service's answer becomes ``{"status": "success", "enabled": true, "data": ...}``,
  ``enabled: false`` with the service's sentence when the journal is switched off, or ``{"status":
  "error", "error": {"code", "message"}}``. The page reads the code, never the HTTP status.
- An op the service does not have is "This part of the journal is not ready yet." When the service
  does not answer, the words come from its own ``/health`` (cross-design Y37): switched off, or
  not answering. Whether the journal is on is never read here from a second switch.
- A write that was sent and not answered is "Not confirmed", never a failure.
- A download is the stored file itself: ``report_file`` answers ``{"file_name", "file_mime",
  "file_sha256", "file_b64"}``; the bytes are checked against the sha256 and sent with
  ``Content-Disposition: attachment`` and the stored ASCII name, ``X-Content-Type-Options:
  nosniff`` and only the stored MIME type, one of PDF, CSV or PNG (cross-design Y32).
  ``?format=csv`` asks for a monthly statement's trades as its CSV instead (JB9); that is the one
  value it takes (any other is refused in words and nothing is sent), and the answer must be a
  CSV, ``text/csv`` under a ``.csv`` name, or it is not sent either.
"""

import base64
import binascii
import hashlib
import os
import re
from functools import wraps
from typing import NamedTuple

import httpx
from flask import Blueprint, Response, jsonify, request, session
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from database.user_db import find_user_by_username
from limiter import limiter
from sudarshan.alerts import loopback, report_files
from sudarshan.autologin.config import is_loopback_url
from sudarshan.autologin.handoff import seal
from sudarshan.vault import JOURNAL_API_INFO, fernet_for, pepper_from
from utils.httpx_client import get_httpx_client
from utils.lazy import LazyInit
from utils.logging import get_logger
from utils.session import is_session_valid, revoke_user_tokens
from utils.thread_safe_cache import MISSING, LockedTTLCache

logger = get_logger(__name__)

API_PREFIX = "/sudarshan/journal/api"
sudarshan_journal_bp = Blueprint("sudarshan_journal", __name__, url_prefix=API_PREFIX)

PRINCIPAL = "owner"
LOCAL_HOST = "127.0.0.1"
SERVICE_PORT = 5586
SERVICE_URL = f"http://{LOCAL_HOST}:{SERVICE_PORT}"
#: One sealed request (design 7.2), and the most the service's answer to a download may be.
API_MAX_BYTES = 64 * 1024
DOWNLOAD_MAX_BYTES = 8 * 1024 * 1024 + 64 * 1024
CONNECT_TIMEOUT_S = 1.0
#: The health check only words a failure; it must not double the wait.
HEALTH_TIMEOUT_S = 1.0
MAX_QUERY_TEXT = 200
LIMITS_PER_MIN = {"read": 120, "write": 60, "download": 20, "reconcile": 2}
TIMEOUTS_S = {"read": 3.0, "write": 6.0, "download": 20.0, "reconcile": 6.0}
LIMITS = {cls: f"{count} per minute" for cls, count in LIMITS_PER_MIN.items()}
#: One scope for a class, so the class is what is limited, not each route of it.
SCOPES = {cls: f"sudarshan-journal-{cls}" for cls in LIMITS}


class OpSpec(NamedTuple):
    """One op of the journal's local API and the route that carries it."""

    name: str
    method: str
    route: str
    cls: str
    query: tuple[tuple[str, str], ...] = ()


_TRIPS_QUERY = (
    ("mode", "str"), ("status", "str"), ("before", "int"), ("limit", "int"),
    ("strategy", "str"), ("tag", "int"), ("playbook", "int"), ("from", "int"), ("to", "int"),
)  # fmt: skip
_ANALYTICS_QUERY = (
    ("mode", "str"), ("strategy", "str"), ("tag", "int"), ("playbook", "int"), ("from", "int"),
    ("to", "int"), ("expiry", "str"), ("period", "str"),
)  # fmt: skip
OPS = (
    OpSpec("state", "GET", "/state", "read"),
    OpSpec("trips", "GET", "/trips", "read", _TRIPS_QUERY),
    OpSpec("trip_get", "GET", "/trips/<id>", "read"),
    OpSpec("trip_update", "PATCH", "/trips/<id>", "write"),
    OpSpec("tags_list", "GET", "/tags", "read"),
    OpSpec("tag_create", "POST", "/tags", "write"),
    OpSpec("tag_update", "PATCH", "/tags/<id>", "write"),
    OpSpec("playbooks_list", "GET", "/playbooks", "read"),
    OpSpec("playbook_create", "POST", "/playbooks", "write"),
    OpSpec("playbook_update", "PATCH", "/playbooks/<id>", "write"),
    OpSpec("analytics", "GET", "/analytics", "read", _ANALYTICS_QUERY),
    OpSpec("reports_list", "GET", "/reports", "read", (("kind", "str"), ("limit", "int"))),
    OpSpec("report_get", "GET", "/reports/<id>", "read"),
    OpSpec("report_file", "GET", "/reports/<id>/file", "download", (("format", "format"),)),
    OpSpec("report_build", "POST", "/reports/build", "write"),
    OpSpec("settings_get", "GET", "/settings", "read"),
    OpSpec("settings_set", "PUT", "/settings", "write"),
    OpSpec("reconcile_now", "POST", "/reconcile", "reconcile"),
)

#: The codes the page acts on. ``not_confirmed``, ``unauthorized``, ``forbidden`` and
#: ``rate_limited`` are the bridge's own: the service's answer of one of them is a fault.
ERROR_STATUS = {
    "not_ready": 501,
    "bad_request": 400,
    "not_found": 404,
    "version_conflict": 409,
    "refused": 422,
    "unavailable": 503,
    "not_confirmed": 504,
    "unauthorized": 401,
    "forbidden": 403,
    "rate_limited": 429,
}
_BRIDGE_ONLY = frozenset({"unauthorized", "forbidden", "rate_limited", "not_confirmed"})
WORDS = {
    "off": "The journal is switched off on the server.",
    "not_ready": "This part of the journal is not ready yet.",
    "not_answering": (
        "The journal service is not answering. Your trades are safe; try again in a minute."
    ),
    "not_confirmed": "Not confirmed: open the journal again to see whether it was saved.",
    "held": "Another program on this server holds the journal service's port, so nothing was "
    "sent to it. Stop that program, then restart sudarshan-journal.",
    "sealed": "The journal service could not confirm that this request came from OpenAlgo. If "
    "OpenAlgo's .env was changed, restart sudarshan-journal.",
    "failed": "The journal could not do that just now. Your trades are safe; try again in a "
    "minute.",
    "unreadable": "That request could not be read. Open the page again, then repeat it.",
    "too_big": "That is too large for the journal. Send less at a time.",
    "no_file": "This report has no file to download. Open it to read its text.",
    "no_csv": "This report has no CSV to download. Open it to read its text.",
    "format": "That download is not one the Reports page offers. Open the Reports page again, "
    "then choose Download or Download CSV.",
    "bad_file": "This report's file could not be read. Its text is still on the Reports page; "
    "try the download again later.",
    "forbidden": "Only the account owner can use the journal.",
    "unauthorized": "Your session has ended. Log in again.",
    "rate_limited": "Too many requests. Wait a minute, then try again.",
}

_warned = LockedTTLCache(maxsize=8, ttl=600)
_fernet_lazy = LazyInit(
    lambda: fernet_for(pepper_from(os.environ), JOURNAL_API_INFO), "sudarshan-journal-seal"
)


def _fernet():
    return _fernet_lazy.get()


def _service_url() -> str:
    """The service's local API: the setting when it is plain http on this server, else 5586."""
    port = (os.environ.get("SUDARSHAN_JOURNAL_HTTP_PORT") or "").strip()
    fallback = (
        f"http://{LOCAL_HOST}:{port}" if port.isdecimal() and 0 < int(port) < 65536 else SERVICE_URL
    )
    url = (os.environ.get("SUDARSHAN_JOURNAL_API_URL") or fallback).rstrip("/")
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
    """The phone's error envelope: the code it acts on and a sentence for the owner."""
    error = {"code": code, "message": message or WORDS.get(code) or WORDS["failed"]}
    if isinstance(field, str) and field:
        error["field"] = field
    return jsonify({"status": "error", "error": error}), ERROR_STATUS[code]


def _off(message=None):
    """The journal switched off is not an error: the page says so with the service's words."""
    words = _sentence(message) or WORDS["off"]
    return jsonify({"status": "success", "enabled": False, "data": None, "message": words}), 200


def _unknown_op(status: int, code, message) -> bool:
    """The API's own answer to an op it has no route for (a bare "not found", never a sentence),
    as against a record that is not there, which the service says in words."""
    bare = message is None or message.strip().lower() == "not found"
    return status == 404 and code in (None, "not_found") and bare


def _answer(status: int, body: dict):
    """The service's answer as the phone's envelope, with the HTTP status of its code."""
    if body.get("status") == "success":
        data = body.get("data")
        if isinstance(data, dict) and data.get("enabled") is False:
            return _off(data.get("message"))
        return jsonify({"status": "success", "enabled": True, "data": data}), 200
    error = body.get("error") if isinstance(body.get("error"), dict) else body
    code, message = error.get("code"), error.get("message")
    message = message if isinstance(message, str) and message else None
    if status == 501 or code == "not_ready" or _unknown_op(status, code, message):
        return _error("not_ready")
    if code in ("forbidden", "principal"):
        logger.error(
            "The journal: sudarshan-journal refused OpenAlgo's sealed request (%s); the two may "
            "hold different peppers", code,
        )  # fmt: skip
        return _error("unavailable", WORDS["sealed"])
    if code in ERROR_STATUS and code not in _BRIDGE_ONLY:
        return _error(code, message, error.get("field"))
    # a transport refusal ('busy', 'size', 'timeout', 'failed', ...): the service's own words
    # when they are a sentence for the owner, never a transport's ("method not allowed")
    return _error("unavailable", _sentence(message) or WORDS["failed"])


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
    on every route, reading included (a family viewer never holds the owner's journal)."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        owner = find_user_by_username()
        if owner is None or session.get("user") != owner.username:
            return _error("forbidden")
        return view(*args, **kwargs)

    return wrapper


@sudarshan_journal_bp.errorhandler(429)
def _rate_limited(error):
    return _error("rate_limited")


@sudarshan_journal_bp.errorhandler(RequestEntityTooLarge)
def _too_large(error):
    return _error("bad_request", WORDS["too_big"])


@sudarshan_journal_bp.errorhandler(Exception)
def _failed(error):
    """Nothing here ends in OpenAlgo's HTML error page: the page gets the envelope."""
    if isinstance(error, HTTPException):
        return error
    logger.exception("The journal: the bridge failed on a request")
    return _error("unavailable", WORDS["failed"])


# -- reading the request -----------------------------------------------------------------------


class _Unreadable(Exception):
    """A request the bridge does not send on; ``words`` when the usual sentence would not say why."""

    def __init__(self, words: str | None = None):
        super().__init__(words)
        self.words = words


def _text(raw: str) -> str:
    if not raw or len(raw) > MAX_QUERY_TEXT:
        raise _Unreadable()
    return raw


def _int(raw: str) -> int:
    if not re.fullmatch(r"-?[0-9]{1,15}", raw.strip()):
        raise _Unreadable()
    return int(raw)


def _format(raw: str) -> str:
    """The one other form of a report's file the page may ask for: a monthly statement's CSV."""
    if raw != "csv":
        raise _Unreadable(WORDS["format"])
    return raw


QUERY_READERS = {"int": _int, "str": _text, "format": _format}


def _json_body() -> dict:
    """The request's JSON as a dict (none is {}), read only up to the API's 64 KiB."""
    if (request.content_length or 0) > API_MAX_BYTES:
        raise RequestEntityTooLarge()
    request.max_content_length = API_MAX_BYTES  # a body with no length is cut here too
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
    """The service's answer: its HTTP status and its envelope."""

    status: int
    body: dict


def _listener():
    """None when a request may go to the service's port; else the answer to give instead."""
    held = loopback.holder(_service_url())
    if held == loopback.NOBODY:
        _warn_once("nobody", "The journal: nothing listens on sudarshan-journal's port")
        return _error("unavailable", WORDS["not_answering"])
    if held == loopback.OTHER:
        _warn_once("other", "The journal: %s; nothing was sent", loopback.refusal(_service_url()))
        return _error("unavailable", WORDS["held"])
    return None  # this user's own program, or no way to tell (no /proc): the seal still holds


def _health_off() -> str | None:
    """The service's own sentence when its /health says the journal is switched off (Y37), else
    None (its health answers and the journal is on, or it does not answer either)."""
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
    state = health.get("journal_state") if isinstance(health, dict) else None
    if (
        answer.status_code != 200
        or not isinstance(state, dict)
        or state.get("enabled") is not False
    ):
        return None
    return _sentence(state.get("reason")) or WORDS["off"]


def _not_answered(spec: OpSpec, sent: bool):
    """The service took no request, or gave no usable answer: what the page is told."""
    off = _health_off() if sent else None
    if off is not None:
        return _off(off)
    if sent and spec.cls in ("write", "reconcile"):  # it may have been done: never say it was not
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
            f"{_service_url()}/v1/{spec.name}",
            content=blob,
            headers={"Content-Type": "application/octet-stream", "Accept": "application/json"},
            timeout=httpx.Timeout(TIMEOUTS_S[spec.cls], connect=CONNECT_TIMEOUT_S),
            follow_redirects=False,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
        _warn_once("call", "The journal: sudarshan-journal did not take a %s request", spec.name)
        return _not_answered(spec, sent=False)
    except httpx.HTTPError:
        _warn_once("call", "The journal: sudarshan-journal did not answer a %s request", spec.name)
        return _not_answered(spec, sent=True)
    if spec.cls == "download" and len(answer.content) > DOWNLOAD_MAX_BYTES:
        logger.error("The journal: a report's file came back larger than a download may be")
        return _error("unavailable", WORDS["bad_file"])
    try:
        body = answer.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or body.get("status") not in ("success", "error"):
        logger.error("The journal: sudarshan-journal answered %s without its envelope", spec.name)
        return _not_answered(spec, sent=True)
    return _Reply(answer.status_code, body)


def _file(data, csv: bool = False):
    """The stored file as a download, or the envelope saying why it cannot be one. ``csv``: the
    page asked for the CSV, so only a CSV may go."""
    if not isinstance(data, dict):
        return _error("unavailable", WORDS["bad_file"])
    name, mime, encoded = data.get("file_name"), data.get("file_mime"), data.get("file_b64")
    if name is None and not encoded:
        return _error("not_found", WORDS["no_csv" if csv else "no_file"])
    kind = report_files.kind_of(name)
    if (
        kind is None
        or mime != kind.mime
        or (csv and kind.extension != "csv")
        or not isinstance(encoded, str)
        or not encoded
    ):
        logger.error("The journal: a report's file came back in a form it cannot be sent in")
        return _error("unavailable", WORDS["bad_file"])
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raw = b""
    expected = data.get("file_sha256")
    digest = hashlib.sha256(raw).hexdigest()
    # a file goes only with the record to check it against: none sent is a refusal (review L6)
    if not raw or not isinstance(expected, str) or digest != expected.strip().lower():
        logger.error("The journal: a report's file did not match its record; it was not sent")
        return _error("unavailable", WORDS["bad_file"])
    response = Response(raw, status=200, content_type=mime)  # exactly the stored type
    response.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


def _call(spec: OpSpec, args: dict):
    """One op, its answer in the phone's envelope (a download: the file itself)."""
    reply = _send(spec, args)
    if not isinstance(reply, _Reply):
        return reply
    if spec.cls == "download" and reply.body.get("status") == "success":
        data = reply.body.get("data")
        if isinstance(data, dict) and data.get("enabled") is False:
            return _off(data.get("message"))
        return _file(data, csv=args.get("format") == "csv")
    return _answer(reply.status, reply.body)


def _register(spec: OpSpec) -> None:
    def view(**kwargs):
        try:
            args = _args(spec, kwargs.get("id"))
        except _Unreadable as unreadable:
            return _error("bad_request", unreadable.words or WORDS["unreadable"])
        return _call(spec, args)

    view.__name__ = spec.name
    guarded = limiter.shared_limit(LIMITS[spec.cls], scope=SCOPES[spec.cls])(view)
    guarded = _owner_only(guarded)
    guarded = _session_required(guarded)
    rule = spec.route.replace("<id>", "<int:id>")
    sudarshan_journal_bp.add_url_rule(rule, endpoint=spec.name, view_func=guarded,
                                      methods=[spec.method])  # fmt: skip


for _spec in OPS:
    _register(_spec)
