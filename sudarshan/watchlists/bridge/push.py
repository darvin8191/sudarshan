"""The change push's two ends (M8.4, W5): the owner's room and the hook.

The room is joined with ``contract.EVENT_JOIN`` by an open app whose session is the owner's, as
the alerts room is. The hook is one ``after_request`` that turns a successful change made through
OpenAlgo's own watchlist routes (any device, /trading's panel included) or the screener's internal
watchlist route into a lists bump and a push, and prunes what the overlay held for a list that was
deleted or cleared (the prune whatever the switch, the push only while v2 is on). An add of a
symbol the list already holds, which OpenAlgo answers as a
success with the row it had, bumps nothing: a look before the add notes that row (one read of the
list's items, for that route only), and the answer naming the same row means nothing changed. It
runs on the request's thread and starts nothing; a failure in it is logged and never changes the
answer. Every other request costs one comparison of its endpoint before and one membership test of
its blueprint after.
"""

from __future__ import annotations

from flask import g, request, session
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
ADD_SYMBOL = f"{OPENALGO_BLUEPRINT}.add_symbol"
#: Where an add notes the item the list already held for the symbol (``g``, this request only).
_HELD = "sudarshan_watchlists_held_item"
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


def _held_item() -> int | None:
    """The id of the item the list already holds for the symbol this add names, or None. Read the
    way OpenAlgo's add reads it (the symbol and exchange trimmed and in capitals, the session's
    user), through its own models; the read's session goes back at once."""
    from database import watchlist_db as oa

    body = request.get_json(silent=True)
    listed = (request.view_args or {}).get("watchlist_id")
    user = session.get("user")
    if not isinstance(body, dict) or not isinstance(listed, int) or not user:
        return None
    symbol, exchange = body.get("symbol"), body.get("exchange")
    if not isinstance(symbol, str) or not isinstance(exchange, str):
        return None
    try:
        found = (oa.db_session.query(oa.WatchlistItem.id)
                 .join(oa.Watchlist, oa.WatchlistItem.watchlist_id == oa.Watchlist.id)
                 .filter(oa.Watchlist.id == listed, oa.Watchlist.user_id == user,
                         oa.WatchlistItem.symbol == symbol.strip().upper(),
                         oa.WatchlistItem.exchange == exchange.strip().upper()).first())  # fmt: skip
    finally:
        oa.db_session.remove()
    return None if found is None else found[0]


def before_watchlist_request():
    """Before OpenAlgo's add route runs (and only then): note the item the list already holds for
    the symbol. OpenAlgo answers an add of a symbol already there as a success with that same row,
    and the hook then bumps nothing. Every other request costs one comparison of its endpoint."""
    if request.endpoint != ADD_SYMBOL or request.method != "POST":
        return None
    try:
        if enabled():
            setattr(g, _HELD, _held_item())
    except Exception:
        logger.exception("Watchlists: the list could not be read before an add; it is pushed")
    return None


def _unchanged(response) -> bool:
    """True for an add that answered the row the list already held: nothing changed."""
    if request.endpoint != ADD_SYMBOL:
        return False
    held = getattr(g, _HELD, None)
    body = response.get_json(silent=True)
    data = body.get("data") if isinstance(body, dict) else None
    return held is not None and isinstance(data, dict) and data.get("id") == held


def _prune(endpoint: str, listed: int | None) -> None:
    """The overlay's share of a delete or a clear. Its failure is logged and the push still goes:
    OpenAlgo's lists changed whatever happened here."""
    if listed is None or endpoint not in (REMOVE_LIST, CLEAR_LIST):
        return
    try:
        if endpoint == REMOVE_LIST:
            prune.drop_list(listed)  # a slot it empties moves the favourites version itself
        else:
            prune.clear_membership(listed)
    except Exception:
        logger.exception("Watchlists: the overlay of list %s could not be pruned", listed)


def after_watchlist_request(response):
    """Prune for a 2xx delete or clear whatever the switch; with v2 on, bump the lists version
    and push for a 2xx change. The overlay follows OpenAlgo's lists even while v2 is off, so a
    list deleted then and a new one given its id in the same second (the guard's ``created_at``
    has one-second resolution) inherit nothing; only the push waits for the switch."""
    if request.blueprint not in WATCHED:
        return response
    if request.method in ("GET", "HEAD", "OPTIONS") or not 200 <= response.status_code < 300:
        return response
    try:
        change = _change(response)
        if change is None:
            return response
        why, listed = change
        _prune(request.endpoint, listed)  # a store that was never made is not made here
        if not enabled() or _unchanged(response):  # off; or a symbol the list already held
            return response
        versions.bump(why, [f"list:{listed}"] if listed is not None else [])
    except Exception:
        logger.exception("Watchlists: the change hook failed; the answer is as OpenAlgo's")
    return response


def install(app) -> None:
    """Register the hook on ``app``, once: the add's look before it, the bump after."""
    if app.extensions.get("sudarshan_watchlists_hook"):
        return
    app.extensions["sudarshan_watchlists_hook"] = True
    app.before_request(before_watchlist_request)
    app.after_request(after_watchlist_request)
