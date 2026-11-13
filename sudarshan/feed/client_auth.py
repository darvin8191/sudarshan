"""Per-client tokens on the feed's write API (FL1, cross-design Y3.3 and Y25.1).

Every ring service that writes to the feed (claims, subscribe, unsubscribe, the REST governor)
proves which service it is with ``Authorization: Bearer <token>``. A token is one line in
``<folder>/<client name>``: 43 characters of urlsafe base64 without padding, written once by the
deploy (folder ``/etc/sudarshan/feed-clients``, root:sudarshan 750, files 640). The folder is the
setting ``SUDARSHAN_FEED_CLIENTS_DIR`` so tests use a temp folder.

Two halves share this file:

* ``auth_headers(name)``: for a client. Reads its own token file, cached and read again when the
  file changes; an absent or unusable file gives no header.
* ``ClientTokens``: for the feed. Reads the folder at start and again when anything in it
  changes (a stat of each file at most once a second, on the request path: no thread). While the
  folder holds no token at all, writes stay open and one WARNING says so; as soon as any token
  exists every write needs its client's own.

The module never logs a token and never puts one in an exception.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import time
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger("sudarshan.feed")

CLIENTS_DIR = "/etc/sudarshan/feed-clients"
ENV_DIR = "SUDARSHAN_FEED_CLIENTS_DIR"
#: A client's name, as in the claims (``sudarshan-alerts``); also what makes a safe file name.
NAME_RE = re.compile(r"[a-z][a-z0-9-]{0,39}")
#: 32 random bytes, urlsafe base64 without padding.
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}")
BEARER = "bearer "
CHECK_EVERY_S = 1.0
RACY_S = 2  # a file this new is read again, whatever its stamp says
MAX_NAMES = 64  # refusal counts kept by name; a flood of made-up names shares one

OPEN_MODE = (
    "no client key files in %s: writes to the price feed are open to every local process "
    "until the deploy creates them"
)


def clients_dir(folder: str | os.PathLike | None = None) -> Path:
    return Path(folder or os.environ.get(ENV_DIR) or CLIENTS_DIR)


def _stamp(path: Path) -> tuple[int, int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def _settled(stamp: tuple[int, int, int] | None) -> bool:
    """A file last changed over RACY_S ago, so a same-looking stamp cannot hide a newer write
    (file systems stamp in coarse ticks: a rewrite in the same tick keeps mtime, size and inode)."""
    return stamp is None or time.time_ns() - stamp[0] > RACY_S * 1_000_000_000


def read_token(path: Path) -> str | None:
    """The token in ``path``: one line of 43 urlsafe base64 characters, else None."""
    try:
        with open(path, encoding="ascii") as handle:
            text = handle.read(256).strip()
    except (OSError, ValueError):
        return None
    return text if TOKEN_RE.fullmatch(text) else None


# ---- the client's half -------------------------------------------------------------------------
_cache: dict[Path, tuple[tuple[int, int, int] | None, str | None]] = {}


def auth_headers(name: str, folder: str | os.PathLike | None = None) -> dict[str, str]:
    """``{"Authorization": "Bearer <token>"}`` for client ``name``, or ``{}`` when it has no
    usable token file (the feed then decides: open while it has no tokens at all)."""
    if not NAME_RE.fullmatch(name or ""):
        return {}
    path = clients_dir(folder) / name
    stamp = _stamp(path)
    cached = _cache.get(path)
    if cached is None or cached[0] != stamp or not _settled(stamp):
        token = read_token(path) if stamp is not None else None
        cached = _cache[path] = (stamp, token)
    return {"Authorization": f"Bearer {cached[1]}"} if cached[1] else {}


def with_auth(name: str, headers: dict[str, str] | None = None) -> dict[str, str]:
    """``headers`` plus the client's own, a caller's own Authorization never replaced."""
    return {**auth_headers(name), **(headers or {})}


# ---- the feed's half ---------------------------------------------------------------------------
class ClientTokens:
    """What the feed knows of its clients' tokens, kept current by ``check`` itself."""

    def __init__(
        self,
        folder: str | os.PathLike,
        clock: Callable[[], float] = time.monotonic,
        every: float = CHECK_EVERY_S,
    ):
        self.folder, self._clock, self._every = Path(folder), clock, every
        self._tokens: dict[str, str] = {}
        self._seen: dict[str, tuple[int, int, int] | None] = {}
        self._checked: float | None = None
        self._open_said = False
        self._bad: set[tuple[str, tuple[int, int, int] | None]] = set()
        self.refused: dict[str, int] = {}

    def names(self) -> list[str]:
        self._refresh()
        return sorted(self._tokens)

    def _listing(self) -> dict[str, tuple[int, int, int] | None]:
        try:
            with os.scandir(self.folder) as entries:
                return {e.name: _stamp(self.folder / e.name) for e in entries if e.is_file()}
        except OSError:
            return {}

    def _refresh(self, force: bool = False) -> None:
        now = self._clock()
        if not force and self._checked is not None and now - self._checked < self._every:
            return
        self._checked = now
        seen = self._listing()
        settled = all(_settled(stamp) for stamp in seen.values())
        if settled and seen == self._seen and (self._tokens or self._open_said):
            return
        self._seen = seen
        tokens = {}
        for name, stamp in sorted(seen.items()):
            if not NAME_RE.fullmatch(name):
                continue
            token = read_token(self.folder / name)
            if token is None:
                if (name, stamp) not in self._bad:
                    self._bad.add((name, stamp))
                    log.warning(
                        "the client key file for %s is not a usable key; it is ignored", name
                    )
                continue
            tokens[name] = token
        if tokens and tokens != self._tokens:
            log.info("feed client keys: %s", ", ".join(sorted(tokens)))
        self._tokens = tokens
        if tokens:
            self._open_said = False
        elif not self._open_said:
            self._open_said = True
            log.warning(OPEN_MODE, self.folder)

    def start(self) -> None:
        """Read the folder now (at start), saying which clients have a key or that none has."""
        self._refresh(force=True)

    def allows(self, client: str, header: str | None) -> bool:
        """Whether a write by ``client`` carrying this Authorization header may go on."""
        self._refresh()
        if not self._tokens:
            return True  # no token anywhere: open, as before the deploy made them
        want = self._tokens.get(client)
        given = (header or "").strip()
        if want is None or given[: len(BEARER)].lower() != BEARER:
            return False
        return hmac.compare_digest(given[len(BEARER) :].strip().encode(), want.encode())

    def refuse(self, client: str) -> None:
        """Count a refusal and log it by client name and count (never the token); the log has the
        first one and then every tenth power, so a loop of bad requests cannot fill it."""
        who = client if NAME_RE.fullmatch(client or "") else "an unnamed client"
        if who not in self.refused and len(self.refused) >= MAX_NAMES:
            who = "an unnamed client"
        count = self.refused[who] = self.refused.get(who, 0) + 1
        if count in (1, 10, 100, 1000) or count % 10000 == 0:
            log.warning("feed write refused for %s: no valid client key (%d so far)", who, count)
