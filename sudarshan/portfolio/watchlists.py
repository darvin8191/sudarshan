"""The shares on the owner's watchlists, for the corporate-action calendar (GA8.1: M7.36 for the
watchlists' symbols, P7 decision 15).

* **Through the watchlists' sealed route, never their database.** ``POST <OpenAlgo>/sudarshan/
  watchlists/internal/read?op=own_members``: one Fernet token under ``WATCHLISTS_INTERNAL_INFO``,
  the route's own purpose (``sudarshan/watchlists/bridge/internal.py``), sent only while OpenAlgo's
  port is held by this server's own user (``alerts.loopback``), over a client that lives for the
  one read.
* **Once a night**, at the start of the corporate-actions run (23:45, or its catch-up before
  08:00). A list is not market data, and the events of a share added in the day cannot be read
  from Upstox before that night anyway. The phone never starts a read.
* **Shares only.** A row whose Upstox instrument key carries an ISIN (``NSE_EQ|INE...``) is a
  share; futures, options and indices have no corporate actions of their own and are left out.
  A share on both exchanges, or on several lists, is one share, under its NSE symbol.
* **Kept as one record** (``report_cache``, kind ``watchlists``; no schema change): the shares and
  when they were read, so the calendar marks their events and says "as of" without asking again.
  A night that cannot read the lists keeps the last shares and says so; Watchlists v2 switched off
  empties them and says so.
* **Inside the corporate actions' own budget.** The shares join the night's rotation after
  everything the owner holds, has traded or keeps in a tab (``corpactions.CorpActions._plan``):
  the same reader, ``Budget`` and 150 reads a night, so a long watchlist takes longer to come round
  and never adds a call.
* The family purpose never reaches any of this: ``calendar`` is an owner op.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from sudarshan.alerts import loopback
from sudarshan.autologin.handoff import seal

from .core.days import add_days, day_of_instant
from .core.words import date as day_words
from .corpactions import ISIN, due_day, isin_of_key
from .store import Scope, Tx

log = logging.getLogger("sudarshan.portfolio.watchlists")

OWNER = Scope.owner()
PATH = "/sudarshan/watchlists/internal/read"
OP = "own_members"
#: The one ``report_cache`` record that keeps the last read.
KIND, FY, SEGMENT = "watchlists", 0, "lists"
TIMEOUT_S, CONNECT_S = 10.0, 1.0
#: The exchange whose symbol names a share listed on both.
EXCHANGES = ("NSE", "BSE")

FIRST = (
    "SUDARSHAN reads the shares on your watchlists for the first time tonight after 23:45. Their "
    "dividends, bonuses and splits show here after that."
)
OFF = (
    "Watchlists v2 is switched off on this server, so the calendar shows the events of what you "
    "hold only."
)
NOT_READ = "SUDARSHAN could not read your watchlists {night}."
KEPT = "The shares shown are from its last read."
NONE_KEPT = "None of their shares is shown yet."
AGAIN = "It tries again {when} after 23:45."


class Unreadable(RuntimeError):
    """The route could not be asked, or its answer could not be read: keep what was kept."""


async def ask(base_url: str, fernet: Any, client: httpx.AsyncClient) -> tuple[str, list]:
    """``("ok", rows)`` or ``("off", [])`` (Watchlists v2 switched off); Unreadable otherwise."""
    url = base_url.rstrip("/") + PATH
    refused = loopback.refusal(url)  # the sealed request goes only to our own user's port
    if refused:
        raise Unreadable(refused)
    try:
        answer = await client.post(
            url, params={"op": OP}, content=seal(fernet, OP),
            headers={"Content-Type": "application/octet-stream", "Accept": "application/json"},
            timeout=httpx.Timeout(TIMEOUT_S, connect=CONNECT_S),
        )  # fmt: skip
        body = answer.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise Unreadable(type(exc).__name__) from exc
    if not isinstance(body, dict):
        raise Unreadable("the answer is not an object")
    if body.get("status") == "error" and body.get("code") == "off":
        return "off", []
    data = body.get("data")
    rows = data.get("rows") if isinstance(data, dict) else None
    if answer.status_code != 200 or body.get("status") != "ok" or not isinstance(rows, list):
        raise Unreadable(f"answered {answer.status_code} ({body.get('code')})")
    return "ok", rows


def shares_of(rows: list) -> list[dict[str, str]]:
    """The shares among the route's rows: one per ISIN, NSE's symbol before BSE's, by symbol."""
    found: dict[str, dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        isin, symbol, exchange = (isin_of_key(row.get("instrument_key")), row.get("symbol"),
                                  row.get("exchange"))  # fmt: skip
        if isin is None or not isinstance(symbol, str) or not isinstance(exchange, str):
            continue
        have = found.get(isin)
        if have is None or _rank(exchange) < _rank(have["exchange"]):
            found[isin] = {"isin": isin, "symbol": symbol, "exchange": exchange}
    return sorted(found.values(), key=lambda share: (share["symbol"], share["isin"]))


def _rank(exchange: str) -> int:
    return EXCHANGES.index(exchange) if exchange in EXCHANGES else len(EXCHANGES)


def keep(tx: Tx, state: str, shares: list[dict[str, str]], now: int) -> None:
    """Write the night's outcome. ``read_at`` is always the time of the last read that worked: a
    failed night keeps it and the shares; Watchlists v2 switched off empties the shares."""
    old = tx.report_get(KIND, FY, SEGMENT)
    read_at = now if state == "ok" else (old or {}).get("read_at")
    if state == "failed":
        shares = ((old or {}).get("payload") or {}).get("shares") or []
    payload = {"shares": shares, "tried_at": now}
    tx.report_put(KIND, FY, SEGMENT, state, read_at, None, payload, now)


async def refresh(service: Any, now: int, client: httpx.AsyncClient | None = None) -> str:
    """The night's read of the watchlists' shares. Never raises: a failure keeps the last shares.
    Returns the job log's words (counts only, never a symbol)."""
    fernet = getattr(service, "watch_fernet", None)
    if fernet is None or service.store is None:
        return "watchlists not read: no key"
    url = service.settings.openalgo_url
    try:
        if client is None:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as own:
                state, rows = await ask(url, fernet, own)
        else:
            state, rows = await ask(url, fernet, client)
    except Unreadable as exc:
        log.warning("the watchlists could not be read (%s); the last shares read are kept", exc)
        state, rows = "failed", []
    except Exception:
        log.exception("the watchlists could not be read; the last shares read are kept")
        state, rows = "failed", []
    found = shares_of(rows)
    store = service.store
    await asyncio.get_running_loop().run_in_executor(
        None, lambda: store.write(OWNER, lambda tx: keep(tx, state, found, now))
    )
    if state == "ok":
        return f"watchlists: {len(found)} shares"
    return "watchlists: switched off" if state == "off" else "watchlists: not read, last kept"


def shares(tx: Tx) -> dict[str, dict[str, str]]:
    """The watchlists' shares as last read, by ISIN: none before the first read or while
    Watchlists v2 is switched off."""
    record = tx.report_get(KIND, FY, SEGMENT)
    found = ((record or {}).get("payload") or {}).get("shares") or []
    return {
        s["isin"]: s for s in found if isinstance(s, dict) and ISIN.fullmatch(str(s.get("isin")))
    }


def status(tx: Tx, today: int) -> dict[str, Any]:
    """What the calendar says of the watchlists (types.ts ``WatchlistsRead``): ``state`` is
    ``never``, ``ok``, ``off`` or ``failed``; ``read_at`` when the shares shown were read (their
    "as of"); ``share_count``; ``words`` in place of them when not ``ok``."""
    record = tx.report_get(KIND, FY, SEGMENT)
    if record is None:
        return {"state": "never", "read_at": None, "share_count": 0, "words": FIRST}
    count = len(shares(tx))
    state, words = record["state"], None
    if state == "off":
        words = OFF
    elif state != "ok":
        tried = ((record.get("payload") or {}).get("tried_at")) or record["updated_at"]
        night = due_day(tried)
        night = day_of_instant(tried) if night is None else night
        if night >= today:
            said, when = "tonight", "tomorrow night"
        elif add_days(night, 1) == today:
            said, when = "last night", "tonight"
        else:
            said, when = f"on {day_words(night)}", "tonight"
        words = " ".join((NOT_READ.format(night=said), KEPT if count else NONE_KEPT,
                          AGAIN.format(when=when)))  # fmt: skip
    return {"state": state, "read_at": record["read_at"], "share_count": count, "words": words}


__all__ = ["KIND", "OP", "PATH", "Unreadable", "ask", "keep", "refresh", "shares", "shares_of",
           "status"]  # fmt: skip
