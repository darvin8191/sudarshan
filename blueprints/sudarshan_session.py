"""SUDARSHAN (v0.8.1 review M1, M2, M3): an instrument's own session facts for the chart.

    GET /sudarshan/session/instrument?symbol=NIFTY08DEC2625900CE&exchange=NFO

answers ``{"status": "success", "data": {"day", "stops_at", "closing"}}`` for today in India
(``sudarshan/instrument_session.py``): when an expiring contract stops trading on its expiry day,
and the window after the cash close in which a closing print belongs to the day's last candle.
Read-only, the logged-in session only; it changes nothing, so it is no write route.
"""

from __future__ import annotations

import time

from flask import Blueprint, jsonify, request

from sudarshan import instrument_session
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

sudarshan_session_bp = Blueprint("sudarshan_session_bp", __name__, url_prefix="/sudarshan/session")

PICK_AGAIN = "Pick the instrument again: its symbol or exchange did not come with the request."
NOT_READ = "The trading hours of this instrument could not be read. The chart keeps the exchange's."


def _now_ms() -> int:
    return int(time.time() * 1000)


@sudarshan_session_bp.route("/instrument", methods=["GET"])
@check_session_validity
def instrument():
    symbol = (request.args.get("symbol") or "").strip()
    exchange = (request.args.get("exchange") or "").strip()
    if not symbol or not exchange or len(symbol) > 60 or len(exchange) > 16:
        return jsonify({"status": "error", "message": PICK_AGAIN}), 400
    try:
        data = instrument_session.facts(symbol, exchange, _now_ms())
    except Exception:
        logger.exception("the session facts of %s on %s could not be read", symbol, exchange)
        return jsonify({"status": "error", "message": NOT_READ}), 503
    return jsonify({"status": "success", "data": data})
