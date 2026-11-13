"""The watchlist store's engine and sessions (M8.4). Nothing here runs at import.

One engine for the process, built on first use through ``database.engine_factory`` (SQLite,
NullPool: a fresh connection per operation, closed at once, so no descriptor accumulates) and
published through ``LazyInit``, whose factory only constructs. Preparing the file (the newer-file
check, creating what is missing) is database work and runs on first use outside any lock; it is
idempotent, so two first uses at once, or the migration beside the app, do no harm.

A file written by a newer release is refused before anything in it is touched: ``RecordTooNew``
carries the owner's words, and the cause goes to ``log/errors.jsonl``. OpenAlgo's own lists and the
v0.6.0 page do not depend on this file and keep working.

Every operation is ``with open_session() as s:``. There is no ``scoped_session`` to register in
the app's teardown, and no thread, timer or loop here (the exception's tests scan for them).
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from database.engine_factory import create_db_engine
from sudarshan.watchlists import schema
from sudarshan.watchlists.words import store as words
from utils.lazy import LazyInit
from utils.logging import get_logger

logger = get_logger(__name__)


class RecordTooNew(Exception):
    """The file's schema is newer than this code's. ``str()`` is the owner's sentence."""

    def __init__(self, found: int | None = None):
        super().__init__(words.RECORD_NEWER)
        self.found = found


class RecordUnreadable(Exception):
    """The file could not be opened or read. ``str()`` is the owner's sentence."""

    def __init__(self):
        super().__init__(words.RECORD_UNREADABLE)


_engine: LazyInit = LazyInit(
    lambda: create_db_engine(f"sqlite:///{store_path()}"), "watchlists-engine"
)
_factory: LazyInit = LazyInit(
    lambda: sessionmaker(bind=_engine.get(), expire_on_commit=False), "watchlists-sessions"
)
_ready = False
_refusal_logged = False


def store_path() -> Path:
    """The file this process uses, read at call time so a test can point it elsewhere."""
    return schema.default_path()


def prepare(engine) -> None:
    """Refuse a newer file, else create what is missing. Idempotent; never changes a value."""
    found = schema.stored_schema(engine)
    if found is not None and found > schema.SCHEMA_VERSION:
        raise RecordTooNew(found)
    schema.create_missing(engine)


def ready() -> None:
    """Make the store usable, once. Raises ``RecordTooNew`` or ``RecordUnreadable``; the engine
    stays unpublished-as-ready, so the next call looks again (the owner may have fixed it)."""
    global _ready, _refusal_logged
    if _ready:
        return
    path = store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        prepare(_engine.get())
    except RecordTooNew as exc:
        if not _refusal_logged:
            _refusal_logged = True
            logger.error(
                "Watchlist record refused: %s has schema %s and this release writes %s",
                path.name,
                exc.found,
                schema.SCHEMA_VERSION,
            )
        raise
    except Exception as exc:
        logger.exception("Watchlist record could not be opened: %s", path.name)
        raise RecordUnreadable() from exc
    _ready = True


def open_session() -> Session:
    """A session on the store, prepared on first use. Use it as ``with open_session() as s:``."""
    ready()
    return _factory.get()()


def dispose() -> None:
    """Close the engine and forget it (tests; the app keeps one for its life)."""
    global _ready, _refusal_logged
    _ready = False
    _refusal_logged = False
    _factory.reset()
    _engine.reset(lambda engine: engine.dispose())
