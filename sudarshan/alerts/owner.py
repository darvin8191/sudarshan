"""The owner's OpenAlgo username and API key, read from openalgo.db with OpenAlgo's own functions.

The same calls OpenAlgo makes for its TradingView page: ``user_db.find_user_by_username()`` (the
admin user of this single-user install), ``auth_db.get_api_key()`` and
``auth_db.get_api_key_for_tradingview()``, which decrypts the stored key with the Fernet key
OpenAlgo derives from ``API_KEY_PEPPER`` and ``FERNET_SALT`` in its .env.

Read-only, and without write access to OpenAlgo's ``db/`` directory (the unit may write only its
state directory). openalgo.db is in WAL mode, and SQLite can open such a database read-only only
when its ``-wal`` file exists (another connection is open) or can be created. So each read
chooses the connection for the state the file is in:

  -wal present   ``mode=ro``: reads the main file plus the log, the newest data.
  -wal absent    ``immutable=1``: the last connection to close wrote everything back into the main
                 file and removed the log, so the main file alone is complete; no locks or side
                 files are needed.

If the file changes state between the check and the read, that attempt fails and the other kind
is tried. OpenAlgo's module-level sessions are bound to a throw-away engine for the read and set
back afterwards. The key is never logged.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from .config import ConfigError, read_openalgo_env, sqlite_path

log = logging.getLogger("sudarshan.alerts")


class OwnerError(Exception):
    """No usable key now. ``kind``: no_user, no_key, unreadable_key or database."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class Owner:
    username: str
    api_key: str = field(repr=False)


def database_urls(db_path: Path) -> list[str]:
    """The two read-only ways to open ``db_path``, the one that fits its current state first."""
    target = quote(str(db_path))
    read_only = f"sqlite:///file:{target}?mode=ro&uri=true"
    immutable = f"sqlite:///file:{target}?immutable=1&uri=true"
    if Path(f"{db_path}-wal").exists():
        return [read_only, immutable]
    return [immutable, read_only]


def prepare_openalgo_env(env_file: Path, app_dir: Path) -> Path:
    """Set what OpenAlgo's database modules read at import; returns openalgo.db's path.

    Call once, before anything imports ``database.*``. The pepper and the salt come from
    OpenAlgo's .env; ``DATABASE_URL`` is replaced by a read-only one, so even OpenAlgo's own
    module engine cannot write. Its logging module writes an error file at import: it goes to
    this process's private temporary directory, never to OpenAlgo's log directory.
    """
    values = read_openalgo_env(env_file)
    for name in ("API_KEY_PEPPER", "FERNET_SALT"):
        if values.get(name):
            os.environ.setdefault(name, values[name])
    if len(os.environ.get("API_KEY_PEPPER", "")) < 32:
        raise ConfigError(f"API_KEY_PEPPER is missing from {env_file}")
    db_path = sqlite_path(values.get("DATABASE_URL") or "sqlite:///db/openalgo.db", app_dir)
    if db_path is None:
        raise ConfigError("OpenAlgo's DATABASE_URL is not SQLite; order alerts need SQLite")
    os.environ["DATABASE_URL"] = database_urls(db_path)[0]
    os.environ["LOG_DIR"] = str(Path(tempfile.gettempdir()) / "sudarshan-alerts-openalgo-log")
    os.environ["LOG_TO_FILE"] = "False"
    return db_path


def load_openalgo_modules() -> None:
    """Import OpenAlgo's two database modules, taking only what prepare_openalgo_env set.

    OpenAlgo's logging module loads all of OpenAlgo's .env into the environment when it is
    imported (broker secrets included). This process needs three values from it, so that load is
    skipped here. The import also replaces the root log handlers: call setup_logging() after it.
    """
    import dotenv

    load = dotenv.load_dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: False
    try:
        from database import auth_db, user_db  # noqa: F401
    finally:
        dotenv.load_dotenv = load


@contextlib.contextmanager
def _bound(engine, *sessions):
    """OpenAlgo's module-level sessions on ``engine`` for one read, then as they were."""
    before = [session.session_factory.kw.get("bind") for session in sessions]
    for session in sessions:
        session.remove()
        session.configure(bind=engine)
    try:
        yield
    finally:
        for session, bind in zip(sessions, before, strict=True):
            session.remove()
            session.configure(bind=bind)


#: OpenAlgo's loggers inside the read: a read cut short by a write-back is tried again here, and
#: their error tracebacks for it ("malformed", "error decrypting") would only alarm.
OPENALGO_LOGGERS = ("database.auth_db", "database.user_db")


@contextlib.contextmanager
def _quiet_openalgo():
    loggers = [logging.getLogger(name) for name in OPENALGO_LOGGERS]
    levels = [logger.level for logger in loggers]
    for logger in loggers:
        logger.setLevel(logging.CRITICAL + 1)
    try:
        yield
    finally:
        for logger, level in zip(loggers, levels, strict=True):
            logger.setLevel(level)


def _read_with(url: str) -> Owner:
    from database import auth_db, user_db
    from database.engine_factory import create_db_engine

    engine = create_db_engine(url)  # NullPool: one connection per query, closed at once
    try:
        with _quiet_openalgo(), _bound(engine, user_db.db_session, auth_db.db_session):
            admin = user_db.find_user_by_username()
            if admin is None:
                raise OwnerError("no_user")
            username = admin.username
            has_key = auth_db.get_api_key(username)
            if has_key is None:  # the read failed inside OpenAlgo's function
                raise OwnerError("database")
            if not has_key:
                raise OwnerError("no_key")
            api_key = auth_db.get_api_key_for_tradingview(username)
            if not api_key:
                raise OwnerError("unreadable_key")
            return Owner(username, api_key)
    finally:
        engine.dispose()


def _file_state(db_path: Path) -> tuple:
    """Size and modification time of the file and of its write-ahead log (None if absent)."""

    def stat(path: Path):
        try:
            found = path.stat()
        except FileNotFoundError:
            return None
        return found.st_size, found.st_mtime_ns

    return stat(db_path), stat(Path(f"{db_path}-wal"))


def read_owner(db_path: Path, *, again: bool = True) -> Owner:
    """The owner and his key. Raises OwnerError when there is none to use now.

    "No user" and "no key" are answers. A failed read, or a key that does not decrypt (a read cut
    across a write-back, or a changed pepper), is tried once more the other way. A read of the
    main file alone (``immutable=1``, no locks) during which the file or its log changed is done
    once more, the way that fits the file then.
    """
    from sqlalchemy.exc import SQLAlchemyError

    failure = OwnerError("database")
    for url in database_urls(db_path):
        lock_free = "immutable=1" in url
        before = _file_state(db_path)
        try:
            owner = _read_with(url)
        except OwnerError as exc:
            if exc.kind in ("no_user", "no_key") and _file_state(db_path) == before:
                raise
            failure = exc
            continue
        except SQLAlchemyError:
            log.debug("a read of openalgo.db did not complete; trying the other way")
            failure = OwnerError("database")
            continue
        if lock_free and again and _file_state(db_path) != before:
            log.debug("openalgo.db changed during the read; reading it once more")
            return read_owner(db_path, again=False)
        return owner
    raise failure
