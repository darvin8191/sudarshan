"""Ending the assistant's access at once (M17.9).

Revoking refresh tokens (upstream's ``revoke_all_tokens``) stops a client from getting a new
access token, but the one it holds stays good until it expires, up to 15 minutes later.
``end_all_access`` also records a moment, "valid after", in the risk store
(``risk_state.mcp_valid_after``), and ``blueprints/mcp_http.py`` checks every access token against
it through ``checked``: a token issued at or before that moment is refused at its next call, with
the answer that sends the client back to sign in.

Who calls ``end_all_access``: the kill switch (``sudarshan/guard/killswitch.py``), the "revoke
every token" button of the remote MCP admin page, and every ending of the owner's sessions (a
logout, a password change or reset: ``utils/session.end_every_session``), because the consent was
given by a session that has now ended. Turning the kill switch off brings nothing back: the owner
connects and consents again.

The moment is read through a one-second cache (a call a second at most reads the file) and its
writer drops the entry after its commit. A moment that cannot be read refuses every token. Tokens
carry their issue time in whole seconds, so a token issued in the same second as the moment is
refused too. No thread, no loop; the cache's lock is a real one (``utils.thread_safe_cache``).
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable

from utils.logging import get_logger
from utils.thread_safe_cache import LockedTTLCache

logger = get_logger(__name__)

CACHE_TTL_S = 1.0
_KEY = "mcp_valid_after"
_cache = LockedTTLCache(maxsize=1, ttl=CACHE_TTL_S)
#: Moves each time access is ended in this process, so an open stream can see it without a read.
_ended = 0
_ended_numbers = itertools.count(1)  # next() is one step, so two enders never share a number


def _store():
    from sudarshan.guard import store

    return store.STORE


def valid_after_ms() -> int | None:
    """The moment (milliseconds since 1970) before which no access token is good, or None when
    access was never ended. Cached for a second. Raises when the risk store cannot be read."""
    return _cache.get_or_load(_KEY, lambda: _store().mcp_valid_after())


def refuses(claims: dict) -> bool:
    """Whether an access token with these claims was issued before access was last ended.

    True too when the moment cannot be read, or when one is set and the token does not say when
    it was issued: a doubt refuses.
    """
    try:
        after = valid_after_ms()
    except Exception:
        logger.exception("The assistant's cutoff could not be read; its token is refused")
        return True
    if after is None:
        return False
    issued = claims.get("iat") if isinstance(claims, dict) else None
    if isinstance(issued, bool) or not isinstance(issued, int | float):
        return True
    return issued * 1000 <= after


def checked(verify: Callable[[str], dict], error: type[Exception]) -> Callable[[str], dict]:
    """``verify`` (upstream's access-token check) followed by the cutoff: a token from before
    the cutoff raises ``error("invalid_token")``, as an expired one does."""

    def verify_then_cutoff(token_str: str) -> dict:
        claims = verify(token_str)
        if refuses(claims):
            raise error("invalid_token")
        return claims

    return verify_then_cutoff


def ended_count() -> int:
    """How many times access was ended in this process. An open stream compares it with the
    count at its start and closes when it has moved; no file is read."""
    return _ended


def set_now(now: Callable[[], float] = time.time) -> int:
    """Record that every access token issued up to now is no longer good; returns the moment."""
    global _ended
    try:
        moment = _store().set_mcp_valid_after(int(now() * 1000))
    finally:
        _cache.invalidate(_KEY)  # after the commit, never before
    _ended = next(_ended_numbers)
    return moment


def end_all_access(reason: str, *, revoke: bool = True) -> dict:
    """End every AI connection now: the cutoff first (the record each call reads), then every
    refresh token. Each half is tried whatever the other did.

    Returns ``{"cutoff": bool, "revoked": int | None}``; ``revoked`` is None when the tokens could
    not be revoked, or ``revoke`` is False (the caller has just revoked them). Never raises.
    """
    done: dict = {"cutoff": False, "revoked": None}
    try:
        set_now()
        done["cutoff"] = True
    except Exception:
        logger.exception("The assistant's cutoff could not be recorded")
    if revoke:
        try:
            done["revoked"] = _revoke(reason)
        except Exception:
            logger.exception("The assistant's sign-ins could not be revoked")
    return done


def _revoke(reason: str) -> int:
    """Revoke every refresh token (upstream's own function). 0 where remote MCP was never
    switched on, so its tables were never made: there is nothing to revoke."""
    from sqlalchemy.exc import OperationalError

    from database import oauth_db

    try:
        return int(oauth_db.revoke_all_tokens(reason))
    except OperationalError as exc:
        if "no such table" in str(exc).lower():
            return 0
        raise
    finally:
        # Its own session, which no teardown of this caller's request may know: ended here on
        # every path, so a failed statement leaves nothing open.
        oauth_db.db_session.remove()
