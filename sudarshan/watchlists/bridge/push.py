"""The change push's two ends (M8.4, W5): the owner's room and the hook.

The room is joined with ``contract.EVENT_JOIN`` by an open app whose session is the owner's, as
the alerts room is. The hook is one ``after_request`` that turns a successful change made through
OpenAlgo's own watchlist routes (any device, /trading's panel included) or the screener's internal
watchlist route into a lists bump and a push, and prunes what the overlay held for a list that was
deleted or cleared. It runs on the request's thread and starts nothing; a failure in it is logged
and never changes the answer. Every other request costs one membership test of its blueprint.
"""

from __future__ import annotations

from flask import request
from flask_socketio import join_room

from extensions import is_owner_session, socketio
from sudarshan.watchlists import contract, versions
from sudarshan.watchlists.ops.state import enabled
from sudarshan.watchlists.store import prune
from sudarshan.watchlists.words.base import MESSAGES
from utils.logging import get_logger

logger = get_logger(__name__)

OPENALGO_BLUEPRINT = "watchlist_bp"
SCREENER_BLUEPRINT = "sudarshan_screener_internal"
SCREENER_ENDPOINT = f"{SCREENER_BLUEPRINT}.internal_watchlist"
WATCHED = frozenset({OPENALGO_BLUEPRINT, SCREENER_BLUEPRINT})
REMOVE_LIST = f"{OPENALGO_BLUEPRINT}.remove_list"
CLEAR_LIST = f"{OPENALGO_BLUEPRINT}.clear_list"
#: OpenAlgo's endpoint to what its change tells the phone (``why``).
CHANGES = {
    f"{OPENALGO_BLUEPRINT}.create_list": "lists",
    f"{OPENALGO_BLUEPRINT}.rename_list": "lists",
    REMOVE_LIST: "lists",
    CLEAR_LIST: "items",
    f"{OPENALGO_BLUEPRINT}.add_symbol": "items",
    f"{OPENALGO_BLUEPRINT}.remove_symbol": "items",
    f"{OPENALGO_BLUEPRINT}.order_symbols": "arranged",
}


@socketio.on(contract.EVENT_JOIN)
def join_owner_room(data=None):
    """An open app joins the owner's room: the owner's session only, and only while v2 is on."""
    try:
        if not is_owner_session():
            return {"ok": False, "message": MESSAGES["owner_only"]}
    except Exception:
        logger.exception("Watchlists: the session could not be checked for the room")
        return {"ok": False, "message": MESSAGES["failed"]}
    if not enabled():
        return {"ok": False, "message": MESSAGES["off"]}
    join_room(contract.ROOM)
    return {"ok": True}


def _list_id(response) -> int | None:
    """The list a screener write or a create answered with (``list_id`` or ``data.id``)."""
    body = response.get_json(silent=True)
    if not isinstance(body, dict):
        return None
    found = body.get("list_id")
    if found is None and isinstance(body.get("data"), dict):
        found = body["data"].get("id")
    return found if isinstance(found, int) else None


def _change(response):
    """(why, list id) of a successful change, or None when the request changed nothing."""
    if request.blueprint == SCREENER_BLUEPRINT:
        if request.endpoint != SCREENER_ENDPOINT or request.args.get("op") != "watchlist_apply":
            return None
        body = response.get_json(silent=True)
        if not isinstance(body, dict) or body.get("found") is not True:
            return None
        # the request is sealed, so a create cannot be told from an add: the whole list is refetched
        return "lists", _list_id(response)
    why = CHANGES.get(request.endpoint, "lists")
    listed = (request.view_args or {}).get("watchlist_id")
    return why, listed if isinstance(listed, int) else _list_id(response)


def _prune(endpoint: str, listed: int | None) -> None:
    """The overlay's share of a delete or a clear. Its failure is logged and the push still goes:
    OpenAlgo's lists changed whatever happened here."""
    if listed is None or endpoint not in (REMOVE_LIST, CLEAR_LIST):
        return
    try:
        if endpoint == REMOVE_LIST:
            if prune.drop_list(listed):  # a favourite slot was emptied
                versions.bump_favourites()
        else:
            prune.clear_membership(listed)
    except Exception:
        logger.exception("Watchlists: the overlay of list %s could not be pruned", listed)


def after_watchlist_request(response):
    """Bump the lists version and push for a 2xx change; prune for a delete or a clear."""
    if request.blueprint not in WATCHED:
        return response
    if request.method in ("GET", "HEAD", "OPTIONS") or not 200 <= response.status_code < 300:
        return response
    try:
        if not enabled():
            return response
        change = _change(response)
        if change is None:
            return response
        why, listed = change
        _prune(request.endpoint, listed)
        versions.bump(why, [f"list:{listed}"] if listed is not None else [])
    except Exception:
        logger.exception("Watchlists: the change hook failed; the answer is as OpenAlgo's")
    return response


def install(app) -> None:
    """Register the hook on ``app``, once."""
    if app.extensions.get("sudarshan_watchlists_hook"):
        return
    app.extensions["sudarshan_watchlists_hook"] = True
    app.after_request(after_watchlist_request)
