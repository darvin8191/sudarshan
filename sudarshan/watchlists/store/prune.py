"""What the overlay drops when OpenAlgo deletes or clears a list (M8.4, W5).

One transaction in ``db/sudarshan_watchlists.db`` and never one across that file and
``openalgo.db``: OpenAlgo's own delete has already committed, so a failure here leaves overlay
rows for a list that is gone, which every reader ignores (``wl_list.oa_created_at`` no longer
matches) and the next delete of that id prunes. A store that is missing or newer is skipped, and
a missing one is not created by a list's deletion.
"""

from __future__ import annotations

from sqlalchemy import delete

from sudarshan.watchlists import engine, schema
from sudarshan.watchlists.schema import OWNER
from utils.logging import get_logger

logger = get_logger(__name__)


def _usable() -> bool:
    if not engine.store_path().exists():
        logger.info("Watchlists: no store file yet, so nothing to prune")
        return False
    try:
        engine.ready()
    except (engine.RecordTooNew, engine.RecordUnreadable) as exc:
        logger.info("Watchlists: the store is not usable, so nothing was pruned: %s", exc)
        return False
    return True


def drop_list(list_id: int) -> bool:
    """Everything the overlay holds for a deleted list; True when a favourite slot was emptied."""
    if not _usable():
        return False
    ref = f"list:{list_id}"
    with engine.open_session() as s, s.begin():
        for table in (schema.SectionItemRow, schema.SectionRow, schema.FollowRow):
            s.execute(delete(table).where(table.principal == OWNER, table.list_id == list_id))
        s.execute(delete(schema.ListRow).where(
            schema.ListRow.principal == OWNER, schema.ListRow.list_id == list_id))  # fmt: skip
        s.execute(delete(schema.PrefRow).where(
            schema.PrefRow.principal == OWNER, schema.PrefRow.ref == ref))  # fmt: skip
        emptied = s.execute(delete(schema.FavouriteRow).where(
            schema.FavouriteRow.principal == OWNER, schema.FavouriteRow.ref == ref))  # fmt: skip
    return emptied.rowcount > 0


def clear_membership(list_id: int) -> None:
    """A cleared list keeps its sections and follow rows (/trading cannot see them); only the
    symbols' section membership goes."""
    if not _usable():
        return
    item = schema.SectionItemRow
    with engine.open_session() as s, s.begin():
        s.execute(delete(item).where(item.principal == OWNER, item.list_id == list_id))
