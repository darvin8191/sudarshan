"""SUDARSHAN (M12.1, M12.2): the Automation routes.

The layer itself lives in ``sudarshan/automation/``. One public route:

    POST /sudarshan/tv/<token>     one TradingView alert for the strategy whose link this is

It is unauthenticated and CSRF-exempt by design: the token in the link is the credential,
because TradingView can set neither a header nor a signature. Every decision is made in
``sudarshan/automation/intake.py``; this view only reads the request and turns the outcome into
an answer. With ``SUDARSHAN_AUTOMATION`` off (the default) it answers exactly as it does for a
link nobody made, opens no database and imports no order code.

An unknown link is answered here, never by ``abort(404)``: the app's own 404 handler counts
toward an address's ban, and a scanner must not be able to get TradingView's address banned.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from limiter import limiter
from sudarshan.automation import contract, flag
from utils.ip_helper import get_real_ip
from utils.logging import get_logger

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
    return jsonify({"status": "error", "message": "Too many alerts. Try again in a minute."}), 429


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

    outcome = intake.handle(token, request.get_data(cache=False), get_real_ip())
    return jsonify(outcome.body), outcome.status
