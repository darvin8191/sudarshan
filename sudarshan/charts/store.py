"""The chart documents' store (M4.18, T7a): one engine, a read and a revision-checked write.

Inside OpenAlgo by the watchlists exception's mechanics (``README.md`` here): no thread, timer,
scheduler or event loop; one engine for the process through ``database.engine_factory`` (SQLite,
NullPool: a fresh connection per operation, closed at once), built on first use and published
through ``LazyInit``; every operation in its own ``with open_session() as s:``, so there is no
``scoped_session`` for the app's teardown to remove. Nothing runs at import.

A file written by a newer release is refused before anything in it is touched (``RecordTooNew``
carries the owner's words; the cause goes to ``log/errors.jsonl``), and the devices keep their
own copies meanwhile.
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from database.engine_factory import create_db_engine
from sudarshan.charts import schema, words
from utils.lazy import LazyInit
from utils.logging import get_logger

logger = get_logger(__name__)

#: How many documents of each kind the server keeps for the owner: a new key past it is refused,
#: and the device keeps that one to itself. Drawings are per instrument, and every option
#: contract is an instrument, so theirs is the long one.
LIMITS = {"drawings": 2000, "phone_set": 8, "workspace": 8, "template": 100}
#: The engine's own bounds on a document (openalgo-charts' workspace tier): values, and nesting
#: below the top.
MAX_NODES = 100_000
MAX_DEPTH = 32
#: The phone's indicator set holds a few studies; a set this long is not one the phone wrote.
MAX_SET_STUDIES = 8
#: The workspace tier's own limit on saved layouts, and on indicator templates.
MAX_CATALOGUE = 100
#: A key as the screens build it: URI-encoded parts, joined by colons.
KEY = re.compile(r"[A-Za-z0-9._~%:!*'()-]+")


class RecordTooNew(Exception):
    """The file's schema is newer than this code's. ``str()`` is the owner's sentence."""

    def __init__(self, found: int | None = None):
        super().__init__(words.RECORD_NEWER)
        self.found = found


class RecordUnreadable(Exception):
    """The file could not be opened or read. ``str()`` is the owner's sentence."""

    def __init__(self):
        super().__init__(words.RECORD_UNREADABLE)


class Invalid(Exception):
    """A request field is not what it must be; ``field`` names it."""

    def __init__(self, field: str):
        super().__init__(field)
        self.field = field


class TooBig(Exception):
    """The body is past ``schema.MAX_BODY_BYTES``."""


class Full(Exception):
    """A new key would pass its kind's limit."""


class Stale(Exception):
    """The write named a revision the document no longer has; ``current`` is the document now
    (None when there is none), for the device to show."""

    def __init__(self, current: Doc | None):
        super().__init__("stale")
        self.current = current


@dataclass(frozen=True)
class Doc:
    kind: str
    key: str
    revision: int
    updated_ms: int
    #: The stored JSON text; None when the caller already holds this revision, or after a write.
    body: str | None = None

    def to_json(self) -> str:
        """The document as JSON text, its body put in as stored (it is JSON already, written by
        ``check_body``), so a large one is never parsed and written out again."""
        head = json.dumps({"kind": self.kind, "key": self.key, "revision": self.revision,
                           "updated_ms": self.updated_ms})  # fmt: skip
        return head if self.body is None else f'{head[:-1]}, "body": {self.body}}}'


# -- the engine ------------------------------------------------------------------------------------
_engine: LazyInit = LazyInit(lambda: create_db_engine(f"sqlite:///{store_path()}"), "charts-engine")
_factory: LazyInit = LazyInit(
    lambda: sessionmaker(bind=_engine.get(), expire_on_commit=False), "charts-sessions"
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
    """Make the store usable, once. Raises ``RecordTooNew`` or ``RecordUnreadable``; nothing is
    marked ready then, so the next call looks again (the owner may have fixed it)."""
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
            logger.error("Chart record refused: %s has schema %s and this release writes %s",
                         path.name, exc.found, schema.SCHEMA_VERSION)  # fmt: skip
        raise
    except Exception as exc:
        logger.exception("Chart record could not be opened: %s", path.name)
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


def now_ms() -> int:
    return int(time.time() * 1000)


# -- what a request may carry ------------------------------------------------------------------
def check_key(key: object) -> str:
    if not isinstance(key, str) or len(key) > schema.MAX_KEY_CHARS or not KEY.fullmatch(key):
        raise Invalid("key")
    return key


def _whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _study(entry: object) -> bool:
    return (isinstance(entry, dict) and isinstance(entry.get("id"), str)
            and 1 <= len(entry["id"]) <= 120 and isinstance(entry.get("settings"), dict))  # fmt: skip


def _shape_ok(kind: str, body: object) -> bool:
    """The top of each kind's document as the engine (or, for the phone's set, the phone) writes
    it. The devices check the whole of it with the engine's own parsers when they read it."""
    if not isinstance(body, dict):
        return False
    if kind == "drawings":
        return _whole(body.get("version")) and isinstance(body.get("drawings"), list)
    if kind == "phone_set":
        studies = body.get("studies")
        return (body.get("version") == 1 and isinstance(studies, list)
                and len(studies) <= MAX_SET_STUDIES and all(map(_study, studies)))  # fmt: skip
    if kind == "workspace":
        lists = (body.get("workspaces"), body.get("templates"))
        return (body.get("version") == 1 and _whole(body.get("revision"))
                and all(isinstance(v, list) and len(v) <= MAX_CATALOGUE for v in lists))  # fmt: skip
    return _whole(body.get("version"))  # template: no screen writes one yet


def _bounded(value: object) -> None:
    """The engine's bounds, walked without recursion so a deep nesting costs a list, not the
    stack: at most ``MAX_NODES`` values, ``MAX_DEPTH`` levels below the top, finite numbers."""
    stack, nodes = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise Invalid("body")
        if isinstance(item, dict):
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise Invalid("body")


def check_body(kind: str, body: object) -> str:
    """The body as the compact JSON text it is stored as, once its bounds and its kind's shape
    hold. Raises ``Invalid("body")`` or ``TooBig``."""
    _bounded(body)
    if not _shape_ok(kind, body):
        raise Invalid("body")
    try:
        stored = json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise Invalid("body") from None
    if len(stored.encode("utf-8")) > schema.MAX_BODY_BYTES:
        raise TooBig()
    return stored


# -- the two operations ----------------------------------------------------------------------------
def _where(user: str, kind: str, key: str) -> tuple:
    d = schema.ChartDoc
    return (d.user == user, d.kind == kind, d.key == key)


def _current(s: Session, user: str, kind: str, key: str) -> Doc | None:
    d = schema.ChartDoc
    row = s.execute(
        select(d.revision, d.updated_ms, d.body).where(*_where(user, kind, key))
    ).first()
    return None if row is None else Doc(kind, key, row.revision, row.updated_ms, row.body)


def read(user: str, kind: str, key: str, have: int | None = None) -> Doc | None:
    """The document, or None; its body left out when ``have`` is its revision. One transaction,
    so the revision and the body read are one version."""
    d = schema.ChartDoc
    with open_session() as s, s.begin():
        row = s.execute(select(d.revision, d.updated_ms).where(*_where(user, kind, key))).first()
        if row is None:
            return None
        if have is not None and row.revision == have:
            return Doc(kind, key, row.revision, row.updated_ms)
        body = s.execute(select(d.body).where(*_where(user, kind, key))).scalar_one()
        return Doc(kind, key, row.revision, row.updated_ms, body)


def write(user: str, kind: str, key: str, base: int, body: str, at: int | None = None) -> Doc:
    """Replace the document at revision ``base`` (0: the device knows of none) with ``body``; the
    new revision and time, without the body. Raises ``Stale`` with the document as it is now when
    ``base`` is not its revision, and ``Full`` when a new key would pass its kind's limit.

    One transaction whose first statement is an UPDATE, which takes SQLite's write lock at once:
    the compare-and-set itself when ``base`` names a revision, else a guarded no-op that also says
    whether the key exists. What is read after it cannot move before the commit.
    """
    d = schema.ChartDoc
    at = now_ms() if at is None else at
    revision = max(base + 1, at)
    where = _where(user, kind, key)
    adding = False
    try:
        with open_session() as s, s.begin():
            if base > 0:
                done = s.execute(update(d).where(*where, d.revision == base).values(
                    revision=revision, body=body, updated_ms=at))  # fmt: skip
                if done.rowcount != 1:
                    raise Stale(_current(s, user, kind, key))
                return Doc(kind, key, revision, at)
            if s.execute(update(d).where(*where).values(revision=d.revision)).rowcount:
                raise Stale(_current(s, user, kind, key))
            mine = select(func.count()).select_from(d).where(d.user == user, d.kind == kind)
            if s.scalar(mine) >= LIMITS[kind]:
                raise Full()
            adding = True
            s.add(d(user=user, kind=kind, key=key, revision=revision, body=body, updated_ms=at))
    except IntegrityError:
        if not adding:
            raise  # a check the file holds refused the change
        with open_session() as s:  # another writer made the key first, past the lock above
            current = _current(s, user, kind, key)
        if current is None:
            raise  # not the key: a check the file holds refused the row
        raise Stale(current) from None
    return Doc(kind, key, revision, at)
