"""SUDARSHAN (M11.1): the option chain snapshot for the phone's `/options` screen.

    GET /sudarshan/options/api/chain?exchange=NFO&underlying=NIFTY&expiry=2026-11-17

answers ``{"status": "success", "data": {"state", "words", "chain"}}``: one expiry's put/call
chain read from Upstox with the owner's daily token (``sudarshan/options/snapshot.py``: 5 s cache
and a single flight per chain, never retried), every leg named by the OpenAlgo symbol the proxy
subscribes. ``state`` is ``ok``, ``signin`` (today's Upstox login is needed), ``wait`` (Upstox
asked us to slow down), ``unavailable`` (no usable answer) or ``none`` (no such chain; MCX is not
offered), each with its sentence in ``words``.

Read-only: it places, changes and cancels nothing, and writes nothing. Each request is checked in
this order: an OpenAlgo session (401), the owner himself (403; a family phone has no OpenAlgo
session at all and is refused at the first), then the limit, counted per owner so a refused caller
never spends it. No thread, timer, scheduler or event loop here.
"""

from __future__ import annotations

import re
from datetime import date

from flask import Blueprint, jsonify, request, session

from extensions import is_owner_session
from limiter import limiter
from sudarshan.options import snapshot, words
from utils.logging import get_logger
from utils.session import is_session_valid

logger = get_logger(__name__)

sudarshan_options_bp = Blueprint("sudarshan_options", __name__, url_prefix="/sudarshan/options/api")

LIMIT = "90 per minute"
_NAME = re.compile(r"[A-Z0-9&_-]{1,40}")
EXCHANGES = ("NFO", "BFO", "MCX")


def _answer(body: dict, status: int = 200):
    response = jsonify(body)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _refuse(message: str, status: int):
    return _answer({"status": "error", "message": message}, status)


def _owner_key() -> str:
    """The limiter's key: only a request that passed the session and owner checks reaches it."""
    return f"owner:{session.get('user') or ''}"


def _iso_day(text: str) -> str | None:
    try:
        return date.fromisoformat(text).isoformat() if len(text) == 10 else None
    except ValueError:
        return None


@limiter.limit(LIMIT, key_func=_owner_key)
def _chain():
    exchange = (request.args.get("exchange") or "").strip().upper()
    underlying = (request.args.get("underlying") or "").strip().upper()
    expiry = _iso_day((request.args.get("expiry") or "").strip())
    if exchange not in EXCHANGES or not _NAME.fullmatch(underlying) or expiry is None:
        return _refuse(words.PICK_AGAIN, 400)
    try:
        data = snapshot.answer(exchange, underlying, expiry, session.get("user") or "",
                               session.get("broker"))  # fmt: skip
    except Exception:
        logger.exception("Options: the chain of %s %s could not be read", underlying, expiry)
        data = {"state": "unavailable", "words": words.FAILED, "chain": None}
    return _answer({"status": "success", "data": data})


@sudarshan_options_bp.route("/chain", methods=["GET"])
def chain():
    try:
        if not is_session_valid():
            return _refuse(words.SESSION, 401)
        if not is_owner_session():
            return _refuse(words.OWNER_ONLY, 403)
    except Exception:
        logger.exception("Options: the session could not be checked")
        return _refuse(words.SESSION, 401)
    return _chain()


@sudarshan_options_bp.errorhandler(429)
def busy(error):
    return _refuse(words.BUSY, 429)
