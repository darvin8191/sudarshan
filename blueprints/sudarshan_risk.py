"""SUDARSHAN (M5.13, M16.7, M20.7): the risk layer's API: the kill switch and the risk settings.

The layer itself lives in ``sudarshan/guard/``. Owner routes (OpenAlgo session + CSRF, the owner
account only, like the auto-login routes):

    GET  /sudarshan/risk/state        the stage and the kill switch only: every page's banner
    GET  /sudarshan/risk/status       the stage, the kill switch, the settings, the gate's timing
    GET  /sudarshan/risk/preview      what turning it on would cancel, counted fresh (R1)
    POST /sudarshan/risk/killswitch   {"exit_all": bool, "phrase": "..."}: turn it on
    POST /sudarshan/risk/resume       {"phrase": "..."}: turn it off
    GET  /sudarshan/risk/settings     the settings, never the confirm phrase
    PUT  /sudarshan/risk/settings     {"settings": {...}, "phrase": "..."}: loosening needs it
    GET  /sudarshan/risk/audit        what the risk layer did, newest first

For scripts and alerts, which cannot hold a session (API key in the body, no CSRF, 5 a minute):

    POST /sudarshan/risk/api/killswitch  {"apikey": "...", "exit_all": bool}: on only (R8)

A refusal of an order is the gate's (403 with the sentence); these routes answer 400 for a phrase
that does not match, 409 while the switch is still being turned on, 423 before its minute is up
and 503 when the guard is off on this server. ``WRITE_ROUTES`` lists every route that changes
anything, for the family layer's test that a viewer is refused each one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import wraps

from flask import Blueprint, jsonify, request, session

from database.user_db import find_user_by_username
from limiter import limiter
from sudarshan.guard import daily, flag, killswitch, readers, settings_rules, words
from sudarshan.guard import gate as risk_gate
from sudarshan.guard import store as risk_store
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

sudarshan_risk_bp = Blueprint("sudarshan_risk_bp", __name__, url_prefix="/sudarshan/risk")

#: Every route that changes something (method, path), for the family layer's viewer test.
WRITE_ROUTES = (
    ("POST", "/sudarshan/risk/killswitch"),
    ("POST", "/sudarshan/risk/resume"),
    ("PUT", "/sudarshan/risk/settings"),
    ("POST", "/sudarshan/risk/api/killswitch"),
)


def _owner_only(view):
    """The logged-in session must be OpenAlgo's owner (admin) account."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        owner = find_user_by_username()
        if owner is None or session.get("user") != owner.username:
            return jsonify({"status": "error", "message": words.OWNER_ONLY}), 403
        return view(*args, **kwargs)

    return wrapper


@sudarshan_risk_bp.errorhandler(429)
def _rate_limited(error):
    return jsonify({"status": "error", "message": words.TOO_MANY_REQUESTS}), 429


def _body() -> dict:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _exit_all(body: dict) -> bool | None:
    """Exit all only for a JSON true: a script's "false" in quotes must never exit everything."""
    value = body.get("exit_all", False)
    return value if isinstance(value, bool) else None


def _not_understood():
    return jsonify({"status": "error", "message": words.EXIT_ALL_NOT_UNDERSTOOD}), 400


def _session_owner() -> killswitch.Owner:
    return killswitch.owner_for(
        session["user"], surface="app", actor=session["user"], broker=session.get("broker")
    )


def _answer(answer: killswitch.Answer):
    body = {
        "status": "success" if answer.status == 200 else "error",
        "message": answer.message,
        "data": killswitch.public_state(answer.state),
        "activated": answer.activated,  # the page settles its Exit all and cancel holds on it
    }
    return jsonify(body), answer.status


_PIPES = ("live", "sandbox")


def _day_figure(pipe: str, owner: killswitch.Owner):
    """Today's figure in ``pipe``, read for the page (the order path reads its own)."""
    if pipe == "sandbox":
        return readers.sandbox_day(owner.user)
    if not owner.auth_token or owner.broker != "upstox":
        return None
    rows = readers.live_cached_rows(owner.auth_token)
    if rows is None:
        rows = readers.live_rows(owner.auth_token)
    return daily.live_figure(rows)


def _daily_status(owner: killswitch.Owner, limit: float | None) -> dict:
    """The daily loss card: per pipe the guard covers, today's figure, the limit and the lock."""
    shown = {}
    for pipe in _PIPES:
        if not flag.covers(pipe):
            continue
        try:
            figure = _day_figure(pipe, owner)
        except Exception:
            logger.exception(f"Today's figure could not be read for the risk page ({pipe})")
            figure = None
        lock = risk_gate.GATE.day_lock(pipe)
        shown[pipe] = {
            "figure": None if figure is None else round(figure.total, 2),
            "approximate": bool(figure is not None and figure.approximate),
            "as_of": datetime.now(UTC).isoformat(),
            "reached": figure is not None and daily.reached(limit, figure),
            "lock": None
            if lock is None
            else {"at": lock.at.isoformat(), "figure": lock.figure, "limit": lock.loss_limit},
            "unchecked": risk_gate.GATE.day_unchecked(pipe),
        }
    return shown


def _upstox_kill_switch(owner: killswitch.Owner) -> list[dict] | None:
    """Upstox's own kill switch per segment, for the drill's check that it stays off."""
    if not owner.auth_token or owner.broker != "upstox":
        return None
    try:
        rows = readers.upstox_kill_switch(owner.auth_token)
    except Exception:
        logger.exception("Upstox's own kill switch could not be read")
        return None
    return [{**row, "label": words.upstox_segment(row["segment"])} for row in rows]


@sudarshan_risk_bp.route("/state", methods=["GET"])
@check_session_validity
@_owner_only
@limiter.limit("120 per minute")
def state():
    """Every page's banner and chip: no broker call, no settings."""
    killswitch.recover()
    return jsonify(
        {
            "status": "success",
            "data": {
                "stage": flag.stage(),
                "kill_switch": killswitch.public_state(risk_gate.GATE.state()),
                "daily": {
                    pipe: {"locked": risk_gate.GATE.day_lock(pipe) is not None}
                    for pipe in _PIPES
                    if flag.covers(pipe)
                },
            },
        }
    )


@sudarshan_risk_bp.route("/status", methods=["GET"])
@check_session_validity
@_owner_only
@limiter.limit("120 per minute")
def status():
    killswitch.recover()
    try:
        saved = risk_store.STORE.settings()
        settings = settings_rules.public(saved)
    except Exception:
        logger.exception("The risk settings could not be read for the status")
        saved = settings = None
    owner = _session_owner()
    limit = saved.daily_loss_limit if saved is not None else None
    return jsonify(
        {
            "status": "success",
            "data": {
                "stage": flag.stage(),
                "kill_switch": killswitch.public_state(risk_gate.GATE.state()),
                "settings": settings,
                "gate": {
                    **risk_gate.GATE.timing(),
                    "last_problem": risk_gate.GATE.last_problem(),
                },
                "daily": _daily_status(owner, limit),
                "upstox_kill_switch": _upstox_kill_switch(owner),
                "broker": session.get("broker"),
            },
        }
    )


@sudarshan_risk_bp.route("/preview", methods=["GET"])
@check_session_validity
@_owner_only
@limiter.limit("30 per minute")
def preview():
    try:
        counts = killswitch.preview(_session_owner())
    except Exception:
        logger.exception("The kill switch preview could not be counted")
        counts = None
    return jsonify(
        {"status": "success", "data": {"counts": counts, "message": words.ks_preview(counts)}}
    )


@sudarshan_risk_bp.route("/killswitch", methods=["POST"])
@check_session_validity
@_owner_only
@limiter.limit("10 per minute")
def turn_on():
    body = _body()
    exit_all = _exit_all(body)
    if exit_all is None:
        return _not_understood()
    answer = killswitch.activate(_session_owner(), exit_all=exit_all, phrase=body.get("phrase"))
    return _answer(answer)


@sudarshan_risk_bp.route("/resume", methods=["POST"])
@check_session_validity
@_owner_only
@limiter.limit("10 per minute")
def resume():
    return _answer(killswitch.resume(_session_owner(), phrase=_body().get("phrase")))


@sudarshan_risk_bp.route("/settings", methods=["GET"])
@check_session_validity
@_owner_only
@limiter.limit("60 per minute")
def get_settings():
    try:
        settings = risk_store.STORE.settings()
    except Exception:
        logger.exception("The risk settings could not be read")
        return jsonify({"status": "error", "message": words.KS_SETTINGS_UNREADABLE}), 503
    return jsonify({"status": "success", "data": settings_rules.public(settings)})


@sudarshan_risk_bp.route("/settings", methods=["PUT"])
@check_session_validity
@_owner_only
@limiter.limit("20 per minute")
def put_settings():
    body = _body()
    status, message, saved = settings_rules.change(
        body.get("settings"), phrase=body.get("phrase"), by=session["user"]
    )
    data = settings_rules.public(saved) if saved is not None else None
    return jsonify(
        {"status": "success" if status == 200 else "error", "message": message, "data": data}
    ), status


@sudarshan_risk_bp.route("/audit", methods=["GET"])
@check_session_validity
@_owner_only
@limiter.limit("60 per minute")
def audit():
    try:
        limit = min(max(int(request.args.get("limit", 50)), 1), 500)
    except ValueError:
        limit = 50
    risk_gate.GATE.flush_notes()  # refusals still counted in memory are written first
    try:
        rows = risk_store.STORE.history(limit)
    except Exception:
        logger.exception("The risk audit could not be read")
        return jsonify({"status": "error", "message": words.AUDIT_UNREADABLE}), 503
    return jsonify(
        {
            "status": "success",
            "data": [
                {
                    "at": row.at.isoformat(),
                    "last_at": row.last_at.isoformat(),
                    "kind": row.kind,
                    "pipe": row.pipe,
                    "surface": row.surface,
                    "count": row.count,
                    "summary": row.summary,
                }
                for row in rows
            ],
        }
    )


@sudarshan_risk_bp.route("/api/killswitch", methods=["POST"])
@limiter.limit("5 per minute")
def api_turn_on():
    """Scripts and alerts may stop trading, never start it again (amendment R8)."""
    from database.auth_db import verify_api_key

    body = _body()
    api_key = body.get("apikey")
    username = verify_api_key(api_key) if isinstance(api_key, str) and api_key else None
    if not username:
        return jsonify({"status": "error", "message": words.API_KEY_NOT_VALID}), 403
    exit_all = _exit_all(body)
    if exit_all is None:
        return _not_understood()
    owner = killswitch.owner_for(username, surface="api", actor="api key", api_key=api_key)
    return _answer(killswitch.activate(owner, exit_all=exit_all, phrase_needed=False))


@sudarshan_risk_bp.record_once
def _exempt_the_api_route_from_csrf(state):
    """Scripts and alerts hold no browser session to forge; the API key authenticates them."""
    csrf = state.app.extensions.get("csrf") or getattr(state.app, "csrf", None)
    if csrf is not None:
        csrf.exempt(api_turn_on)
