"""The ``arrange`` op (M8.4, W8): the section order, each symbol's section and the item order of
an own list, decided on the version ``view`` answered.

Two files, never one transaction: the overlay first (one transaction that claims the version, then
writes the section positions and each symbol's section), then OpenAlgo's own item order through
its committed reorder, in section-major order (No section first, then each section's symbols), so
/trading shows the same order flat. A failure between the two leaves the overlay saved and
OpenAlgo's order as it was, and the op says so (``failed``) and names the list as changed, so the
bridge still bumps the lists version and pushes it (the overlay's version moved); a retry names
the new version only if nothing else changed it, otherwise ``conflict`` and the page shows the list
again.

Symbols the list has but the request did not name keep their section and go after the named ones;
symbols the request names that the list no longer has are left out.
"""

from __future__ import annotations

from flask import session

from sudarshan.watchlists import engine
from sudarshan.watchlists.ops import OpError, bumps
from sudarshan.watchlists.ops.sections import Done, _held
from sudarshan.watchlists.store import items as store_items
from sudarshan.watchlists.store import sections as store
from sudarshan.watchlists.words import arrange as words
from sudarshan.watchlists.words import sections as section_words
from sudarshan.watchlists.words import view as view_words
from utils.logging import get_logger

logger = get_logger(__name__)


def _live(list_id: int) -> dict:
    from sudarshan.watchlists.refs import list as listref

    live = listref.read_live(session.get("user") or "", list_id)
    if live is None:
        raise OpError("gone", view_words.LIST_GONE)
    return live


def _plan(live: dict, current: dict, order: list, args: dict) -> tuple[list, dict]:
    """``(item ids in section-major order, {key: section_id})`` for the request."""
    flat = sorted(live["items"], key=lambda i: (i[3], i[0]))
    ids = {(e, y): n for n, e, y, _ in flat}
    final, seen = [], set()
    for it in args["items"]:
        key, sid = (it["exchange"], it["symbol"]), it["section_id"]
        if sid is not None and sid not in order:
            raise OpError("invalid", words.ARRANGE_ITEMS, "items")
        if key in ids and key not in seen:
            seen.add(key)
            final.append((key, sid))
    for _, e, y, _ in flat:
        if (e, y) not in seen:
            sid = current.get((e, y))
            final.append(((e, y), sid if sid in order else None))
    major = [k for sid in (None, *order) for k, got in final if got == sid]
    return [ids[k] for k in major], {k: sid for k, sid in final if sid is not None}


@bumps("arranged", lambda args, data: [f"list:{args['id']}"])
def arrange(args: dict) -> dict:
    held, lid = _held(args["version"]), args["id"]
    live = _live(lid)
    order = list(args["sections"])
    with engine.open_session() as s, s.begin():
        try:
            version = store.claim(s, lid, live["created_at"], held)
        except store.Stale:
            raise OpError("conflict", section_words.CONFLICT) from None
        if len(order) != len(set(order)) or {r.id for r in store.sections_of(s, lid)} != set(order):
            raise OpError("invalid", words.ARRANGE_SECTIONS, "sections")
        item_ids, assigned = _plan(live, store_items.placed(s, lid), order, args)
        store_items.order_sections(s, order)
        store_items.put(s, lid, assigned, replace=True)
    from database import watchlist_db as oa

    try:
        written = oa.reorder_items(session.get("user") or "", lid, item_ids)
    except Exception:
        logger.exception("Watchlists: OpenAlgo's order of list %s could not be written", lid)
        written = False
    finally:
        oa.db_session.remove()
    if not written:  # the overlay's change has committed: the phone is told of it all the same
        raise OpError("failed", words.ARRANGE_PARTIAL, changed=[f"list:{lid}"])
    done = Done(version=str(version), id=lid, words=None)
    done.ref = f"list:{lid}"
    return done


HANDLERS = {"arrange": arrange}
