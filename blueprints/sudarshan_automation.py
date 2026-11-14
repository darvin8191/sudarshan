"""SUDARSHAN (M12.1, M12.2): the Automation routes.

The layer itself lives in ``sudarshan/automation/``. One public route:

    POST /sudarshan/tv/<token>     one TradingView alert for the strategy whose link this is

and the owner's routes for the Automation page (OpenAlgo session, the owner account only, CSRF on
every write, like the risk layer's routes), all under ``/sudarshan/automation/api``:

    GET  /list      every strategy with its state, its last alert and the stage in words
    GET  /one       ?id=: one strategy and its alert messages
    POST /create    a new strategy (Sandbox, Stopped); the answer carries its link, once
    PUT  /update    {"id", "version", "changes"}: an edit
    POST /state     {"id", "version", "state", "phrase"?}: Active, exits_only or stopped
    POST /mode      {"id", "version", "mode", "phrase"?}: sandbox or live
    POST /token     {"id", "version"}: a new link; the answer carries it, once
    GET  /alerts    ?strategy=&before=: the log, newest first, 200 a page
    POST /check     {"id", "message"}: what one alert's text would do; nothing is sent
    GET  /others    the other automation on this server, read only

No answer but ``/create`` and ``/token`` ever carries a link's token, and none carries the API
key. ``WRITE_ROUTES`` lists every route that changes anything, for the family layer's test that
a viewer is refused each one. A refusal is a sentence with 400 (the form, the phrase), 404 (the
strategy is gone) or 409 (changed elsewhere, the contract is taken, an alert is being handled).

The public route is unauthenticated and CSRF-exempt by design: the token in the link is the credential,
because TradingView can set neither a header nor a signature. Every decision is made in
``sudarshan/automation/intake.py``; this view only reads the request and turns the outcome into
an answer. With ``SUDARSHAN_AUTOMATION`` off (the default) it answers exactly as it does for a
link nobody made, opens no database and imports no order code.

An unknown link is answered here, never by ``abort(404)``: the app's own 404 handler counts
toward an address's ban, and a scanner must not be able to get TradingView's address banned.
"""

from __future__ import annotations

from functools import wraps

from flask import Blueprint, jsonify, request, session

from database.user_db import find_user_by_username
from limiter import limiter
from sudarshan.automation import contract, flag, words
from utils.ip_helper import get_real_ip
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

sudarshan_automation_bp = Blueprint("sudarshan_automation_bp", __name__)

PUBLIC_RULE = f"{contract.PUBLIC_PREFIX}/<token>"
UNKNOWN_BODY = {"status": "error", "message": "Not found"}

# By caller address: the only key that can slow someone walking the token space (each guess
# carries another token). The limit per link is kept by the intake, keyed by the strategy it
# found, so no token and no guess ever becomes a key that stays in memory.
_caller_limit = limiter.shared_limit(
    f"{contract.CALLER_PER_MINUTE} per minute", scope="sudarshan_tv_caller", key_func=get_real_ip
)


@sudarshan_automation_bp.errorhandler(429)
def _rate_limited(error):
    said = (
        "Too many alerts. Try again in a minute."
        if request.path.startswith(contract.PUBLIC_PREFIX + "/")
        else words.TOO_MANY_REQUESTS
    )
    return jsonify({"status": "error", "message": said}), 429


@sudarshan_automation_bp.record_once
def _exempt_the_alert_route(state):
    """TradingView cannot carry a CSRF token; the link's token is the credential."""
    csrf = getattr(state.app, "csrf", None)
    if csrf is not None:
        csrf.exempt(alert)


@sudarshan_automation_bp.route(PUBLIC_RULE, methods=["POST"])
@_caller_limit
def alert(token):
    """Take one alert and hand it to the intake."""
    # Refused from the header, before the body is read: an unauthenticated caller must not make
    # the worker read whatever it chose to send.
    declared = request.content_length
    if declared is not None and declared > contract.MAX_BODY:
        return jsonify({"status": "error", "message": "The alert message is too large."}), 413
    if flag.stage() == flag.OFF:
        return jsonify(UNKNOWN_BODY), 404  # as for a link nobody made; nothing else is touched
    from sudarshan.automation import intake

    # At most one byte past the limit is ever read: a body sent with no declared length (chunked)
    # passes the check above, and must not be read whole to learn that it is too large.
    body = request.stream.read(contract.MAX_BODY + 1)
    outcome = intake.handle(token, body, get_real_ip())
    return jsonify(outcome.body), outcome.status


# ---- the owner's routes -------------------------------------------------------------------------

API = contract.API_PREFIX
#: Every route that changes something (method, path), for the family layer's viewer test. Check
#: changes nothing, but it is a POST and is listed with them.
WRITE_ROUTES = tuple(
    (method, API + path)
    for method, path, _takes, _answers, kind in contract.ROUTES
    if method != "GET"
)
_READS = f"{contract.READS_PER_MINUTE} per minute"
_WRITES = f"{contract.WRITES_PER_MINUTE} per minute"
SURFACE = "the app"


def _owner_only(view):
    """The logged-in session must be OpenAlgo's owner (admin) account."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        owner = find_user_by_username()
        if owner is None or session.get("user") != owner.username:
            return jsonify({"status": "error", "message": words.OWNER_ONLY}), 403
        return view(*args, **kwargs)

    return wrapper


def _body() -> dict:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _whole(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _refusal(sentence: str, status: int = 400, **more):
    return jsonify({"status": "error", "message": sentence, **more}), status


def _control():
    from sudarshan.automation import control, store

    return control, store.shared(), control.Desk()


def _owned(change):
    """Run one change and answer it; a refusal is its sentence."""
    from sudarshan.automation import control

    try:
        return change()
    except control.Refused as refused:
        return _refusal(str(refused), refused.status)
    except Exception:
        logger.exception("Automation: a change could not be made")
        return _refusal("That could not be saved. Nothing was changed. Try again.", 500)


def _one(strategy, store_, desk, **more):
    from sudarshan.automation import control, messages

    now_ms = int(desk.now() * 1000)
    return jsonify(
        {
            "status": "success",
            "strategy": control.view(strategy, store_, now_ms),
            "messages": messages.for_strategy(strategy),
            **more,
        }
    )


@sudarshan_automation_bp.route(f"{API}/list", methods=["GET"])
@check_session_validity
@_owner_only
@limiter.limit(_READS)
def api_list():
    control, store_, desk = _control()
    return jsonify(control.listing(desk=desk, store=store_))


@sudarshan_automation_bp.route(f"{API}/one", methods=["GET"])
@check_session_validity
@_owner_only
@limiter.limit(_READS)
def api_one():
    _control_, store_, desk = _control()
    strategy = store_.strategy(request.args.get("id", type=int) or 0)
    if strategy is None:
        return _refusal(words.NOT_FOUND, 404)
    return _one(strategy, store_, desk)


@sudarshan_automation_bp.route(f"{API}/create", methods=["POST"])
@check_session_validity
@_owner_only
@limiter.limit(_WRITES)
def api_create():
    control, store_, desk = _control()

    def change():
        strategy, token = control.create(_body(), desk=desk, store=store_)
        return jsonify(control.created(strategy, token, store_, int(desk.now() * 1000)))

    return _owned(change)


@sudarshan_automation_bp.route(f"{API}/update", methods=["PUT"])
@check_session_validity
@_owner_only
@limiter.limit(_WRITES)
def api_update():
    control, store_, desk = _control()
    body = _body()
    sid, version = _whole(body.get("id")), _whole(body.get("version"))
    if sid is None or version is None:
        return _refusal(words.CHANGED_ELSEWHERE)
    return _owned(
        lambda: _one(
            control.update(sid, version, body.get("changes"), desk=desk, store=store_), store_, desk
        )
    )


@sudarshan_automation_bp.route(f"{API}/state", methods=["POST"])
@check_session_validity
@_owner_only
@limiter.limit(_WRITES)
def api_state():
    control, store_, desk = _control()
    body = _body()
    sid, version = _whole(body.get("id")), _whole(body.get("version"))
    if sid is None or version is None:
        return _refusal(words.CHANGED_ELSEWHERE)
    return _owned(
        lambda: _one(
            control.switch(
                sid, version, str(body.get("state")), SURFACE,
                phrase=body.get("phrase"), desk=desk, store=store_,
            ),
            store_,
            desk,
        )
    )  # fmt: skip


@sudarshan_automation_bp.route(f"{API}/mode", methods=["POST"])
@check_session_validity
@_owner_only
@limiter.limit(_WRITES)
def api_mode():
    control, store_, desk = _control()
    body = _body()
    sid, version = _whole(body.get("id")), _whole(body.get("version"))
    if sid is None or version is None:
        return _refusal(words.CHANGED_ELSEWHERE)
    return _owned(
        lambda: _one(
            control.set_mode(
                sid, version, str(body.get("mode")), SURFACE,
                phrase=body.get("phrase"), desk=desk, store=store_,
            ),
            store_,
            desk,
        )
    )  # fmt: skip


@sudarshan_automation_bp.route(f"{API}/token", methods=["POST"])
@check_session_validity
@_owner_only
@limiter.limit(_WRITES)
def api_token():
    control, store_, desk = _control()
    body = _body()
    sid, version = _whole(body.get("id")), _whole(body.get("version"))
    if sid is None or version is None:
        return _refusal(words.CHANGED_ELSEWHERE)

    def change():
        strategy, token = control.new_link(sid, version, SURFACE, desk=desk, store=store_)
        return jsonify(control.created(strategy, token, store_, int(desk.now() * 1000)))

    return _owned(change)


@sudarshan_automation_bp.route(f"{API}/alerts", methods=["GET"])
@check_session_validity
@_owner_only
@limiter.limit(_READS)
def api_alerts():
    control, store_, desk = _control()
    sid = request.args.get("strategy", type=int)
    before = request.args.get("before", type=int)
    rows = store_.alerts(sid, before=before, limit=contract.ALERTS_PAGE + 1)
    names = {strategy.id: strategy.name for strategy in store_.strategies()}
    now_ms = int(desk.now() * 1000)
    lines = [
        control.alert_line(row, names.get(row["strategy_id"], "This strategy"), now_ms)
        for row in rows[: contract.ALERTS_PAGE]
    ]
    return jsonify({"status": "success", "alerts": lines, "more": len(rows) > contract.ALERTS_PAGE})


@sudarshan_automation_bp.route(f"{API}/check", methods=["POST"])
@check_session_validity
@_owner_only
@limiter.limit(_READS)
def api_check():
    from sudarshan.automation import intake

    body = _body()
    sid, message = _whole(body.get("id")), body.get("message")
    if sid is None or not isinstance(message, str) or len(message.encode()) > contract.MAX_BODY:
        return _refusal("Paste one alert message, as TradingView would send it.")
    return jsonify({"status": "success", **intake.check(sid, message)})


@sudarshan_automation_bp.route(f"{API}/others", methods=["GET"])
@check_session_validity
@_owner_only
@limiter.limit(_READS)
def api_others():
    _control_, _store, desk = _control()
    return jsonify({"status": "success", "others": desk.others()})
