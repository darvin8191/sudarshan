"""Whether the request being answered is an /api/v1 call whose API key is missing or wrong (G5).

Anyone who reaches /api/v1 could add rows to the owner's order log. OpenAlgo checks a request's
fields before its key and logs a request its fields refuse as a failed order, so a body without a
key, or with a wrong one, was recorded as one; in analyze mode the owner's app also announced it
with a sound. A refused key is not an order. OpenAlgo's order events (events/base.py, which the
event bus then hands to no subscriber) and its direct log writes (database/apilog_db.py) ask this
in the request's own thread, so a request refused for its key writes no row and reaches no screen.
Every request with a valid key, and every order placed from inside OpenAlgo (a strategy, the
app's own order ticket, a background job), is logged as before.
"""

from __future__ import annotations

from flask import g, has_request_context, request

from utils.logging import get_logger

logger = get_logger(__name__)

#: Where OpenAlgo's REST API answers: only its requests carry a key to judge.
API_PREFIX = "/api/v1/"
_ANSWER = "sudarshan_refused_api_key"


def presented_key() -> str | None:
    """The API key the request carries wherever OpenAlgo's API reads one: the JSON body's
    ``apikey``, a form field of that name, the ``X-API-KEY`` header or the ``apikey`` query
    parameter. None when it carries none."""
    body = request.get_json(silent=True)
    key = body.get("apikey") if isinstance(body, dict) else None
    key = (
        key
        or request.form.get("apikey")
        or request.headers.get("X-API-KEY")
        or request.args.get("apikey")
    )
    return key if isinstance(key, str) and key else None


def refused_api_key() -> bool:
    """True when this thread is answering an /api/v1 request whose API key is missing or does not
    verify (OpenAlgo's own check, cached); False otherwise, outside a request included. Worked out
    once per request. Never raises: when it cannot tell, it answers False, and the request is
    logged as before."""
    try:
        if not has_request_context() or not request.path.startswith(API_PREFIX):
            return False
        answer = g.get(_ANSWER)
        if answer is None:
            key = presented_key()
            if key is None:
                answer = True
            else:
                from database.auth_db import verify_api_key

                answer = verify_api_key(key) is None
            setattr(g, _ANSWER, answer)
        return answer
    except Exception:
        logger.exception("Could not tell whether a request's API key was refused; it is logged")
        return False
