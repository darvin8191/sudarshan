"""Finding a listed share in OpenAlgo's master contract, and keeping its identity (M7.24, design 5.1).

The portfolio never loads OpenAlgo's 100,000-symbol cache. It opens ``openalgo.db`` read-only the
way ``sudarshan/alerts/owner.py`` does (``mode=ro`` when the log file is there, ``immutable=1``
when it is not), on its own NullPool engine, and asks ``symtoken`` one question at a time: by
(symbol, exchange), or by instrument key. For NSE_EQ and BSE_EQ the instrument key is
``NSE_EQ|<ISIN>``: the ISIN is the part after the bar.

Every morning after the master contract has downloaded, ``reresolve`` asks again for each listed
asset by its symbol. A changed ISIN (a company that was re-issued one) is written to the asset, the
old one stays an alias so that imports of old files still match, and the change is logged once.
The asset keeps its id: its entries, lots and history do not move (mutation target 26).

This module reads the master contract and the asset table; it does not touch Upstox.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from database.engine_factory import create_db_engine
from sudarshan.alerts.owner import database_urls

from .core.kinds import PRICE_SCALE
from .store import PortfolioStore, Scope, Tx

log = logging.getLogger("sudarshan.portfolio.resolver")

EQUITY_EXCHANGES = ("NSE", "BSE")
#: The exchange whose symbols carry this segment in their instrument key.
SEGMENT = {"NSE": "NSE_EQ", "BSE": "BSE_EQ"}
WORDS = {
    "unavailable": "The list of shares is not available just now. Try again in a minute.",
    "unknown": "No share called {symbol} was found on {exchange}. Check the symbol.",
    "exchange": "Choose NSE or BSE.",
}


class ResolverUnavailable(Exception):
    """OpenAlgo's database could not be read; ``str()`` is a sentence for the owner."""

    def __init__(self) -> None:
        super().__init__(WORDS["unavailable"])


@dataclass(frozen=True, slots=True)
class Listing:
    """One row of the master contract, as the portfolio needs it."""

    symbol: str
    exchange: str
    key: str
    name: str
    isin: str | None
    tick_size_u: int | None

    @property
    def segment(self) -> str:
        return SEGMENT.get(self.exchange, self.key.partition("|")[0])


def isin_of(key: str | None) -> str | None:
    """The ISIN of a cash instrument key (``NSE_EQ|INE002A01018``), else None."""
    segment, _, rest = (key or "").partition("|")
    return rest if segment in SEGMENT.values() and rest else None


def _tick(value) -> int | None:
    """A tick size in rupees (0.05) as price_u; None when there is none."""
    try:
        made = round(float(value) * PRICE_SCALE)
    except (TypeError, ValueError):
        return None
    return made if made > 0 else None


class Resolver:
    """Reads ``symtoken`` read-only; every question opens a connection and closes it."""

    def __init__(self, db_path: Path, engine_factory: Callable[[str], object] = create_db_engine):
        self.db_path = Path(db_path)
        self._factory = engine_factory
        self._engine = None

    # ---- the connection --------------------------------------------------------------------
    def _query(self, sql: str, **params):
        """Rows of one query, trying the two read-only ways to open the file."""
        last: Exception | None = None
        if not self.db_path.is_file():  # opening a missing file would make an empty one
            log.error("the master contract file is not there")
            raise ResolverUnavailable
        for url in database_urls(self.db_path):
            engine = self._factory(url)
            try:
                with engine.connect() as conn:  # type: ignore[attr-defined]
                    return conn.execute(text(sql), params).mappings().all()
            except SQLAlchemyError as exc:
                last = exc
            finally:
                engine.dispose()  # type: ignore[attr-defined]
        log.error("the master contract could not be read: %s", type(last).__name__)
        raise ResolverUnavailable from last

    # ---- questions ---------------------------------------------------------------------------
    def find(self, symbol: str, exchange: str) -> Listing | None:
        """The cash listing of ``symbol`` on NSE or BSE; the plain equity series first."""
        if exchange not in EQUITY_EXCHANGES:
            return None
        rows = self._query(
            "SELECT symbol, exchange, token, name, tick_size, instrumenttype FROM symtoken "
            "WHERE symbol = :symbol AND exchange = :exchange",
            symbol=symbol.strip().upper(),
            exchange=exchange,
        )
        return _pick(rows)

    def by_key(self, key: str) -> Listing | None:
        rows = self._query(
            "SELECT symbol, exchange, token, name, tick_size, instrumenttype FROM symtoken "
            "WHERE token = :key",
            key=key,
        )
        return _pick(rows)

    def by_keys(self, keys: Iterable[str]) -> dict[str, Listing]:
        """The master contract's row for each instrument key it has, in one connection per 500
        keys. Any segment: a holding's, a futures contract's or an option's."""
        wanted = sorted({k for k in keys if k})
        found: dict[str, Listing] = {}
        for start in range(0, len(wanted), 500):
            batch = wanted[start : start + 500]
            names = {f"k{n}": key for n, key in enumerate(batch)}
            marks = ", ".join(f":{name}" for name in names)
            rows = self._query(
                "SELECT symbol, exchange, token, name, tick_size, instrumenttype FROM symtoken "
                f"WHERE token IN ({marks})",
                **names,
            )
            for key in batch:
                listing = _pick([r for r in rows if r["token"] == key])
                if listing is not None:
                    found[key] = listing
        return found

    def has_derivatives(self, symbol: str) -> bool:
        """Whether the stock has futures in the master contract: it then closes by auction."""
        rows = self._query(
            "SELECT 1 AS found FROM symtoken WHERE name = :name AND exchange IN ('NFO', 'BFO') "
            "AND instrumenttype IN ('FUTSTK', 'FUT') LIMIT 1",
            name=symbol.strip().upper(),
        )
        return bool(rows)

    def downloaded_today(self, today_iso: str) -> bool:
        """Whether the broker's master contract was downloaded on the trading day ``today_iso``."""
        try:
            rows = self._query("SELECT download_date FROM master_contract_status")
        except ResolverUnavailable:
            return False
        return any(str(row["download_date"] or "")[:10] == today_iso for row in rows)

    def close(self) -> None:
        """Nothing stays open between questions; kept so a caller can close it like the rest."""


def _pick(rows) -> Listing | None:
    if not rows:
        return None
    ordered = sorted(
        rows, key=lambda r: (r["instrumenttype"] not in ("EQ", None), r["token"] or "")
    )
    row = ordered[0]
    key = row["token"] or ""
    return Listing(
        symbol=row["symbol"],
        exchange=row["exchange"],
        key=key,
        name=row["name"] or row["symbol"],
        isin=isin_of(key),
        tick_size_u=_tick(row["tick_size"]),
    )


# ---- the morning re-resolve ------------------------------------------------------------------------
def reresolve(
    store: PortfolioStore, resolver: Resolver, *, now: int, scope: Scope | None = None
) -> list[str]:
    """Ask the master contract again for every listed asset. A changed ISIN or instrument key is
    written to the asset (same id), the new ISIN is added as an alias, and the change is logged
    once. Returns the sentences logged. Blocks: run it on a thread, not on the event loop."""
    scope = scope or Scope.owner()
    assets = store.read(scope, lambda tx: tx.rows("asset", kind="listed"))
    found: dict[int, Listing | None] = {}
    for asset in assets:
        try:
            found[asset["id"]] = resolver.find(asset["symbol"] or "", asset["exchange"] or "")
        except ResolverUnavailable:
            log.error("the morning check of share identities was skipped: %s", WORDS["unavailable"])
            return []
    said: list[str] = []

    def run(tx: Tx):
        for asset in tx.rows("asset", kind="listed"):
            listing = found.get(asset["id"])
            if listing is None or listing.isin is None:
                continue
            changes = {}
            if asset["instrument_key"] != listing.key:
                changes["instrument_key"] = listing.key
            if asset["isin"] != listing.isin:
                owner = tx.find_alias("isin", listing.isin)
                if owner is not None and owner["asset_id"] != asset["id"]:
                    said.append(
                        f"{asset['name']}: its new ISIN already belongs to another holding; "
                        "left as it is."
                    )
                    continue
                changes["isin"] = listing.isin
            if not changes:
                continue
            tx.change("asset", asset["id"], asset["version"], changes | {"resolved_at": now}, now)
            if "isin" in changes:
                tx.add_alias(asset["id"], "isin", listing.isin, now)
                said.append(
                    f"{asset['name']} has a new ISIN; the old one is kept so that old files still match."
                )

    store.write(scope, run)
    for line in said:
        log.info("%s", line)
    return said


# ---- the morning job -----------------------------------------------------------------------------
RESOLVE_AT_MIN = 9 * 60  # 09:00 IST, after the morning master contract download
CHECK_EVERY_S = 60.0


def resolve_due(calendar, resolver: Resolver, now: int, last_done: int | None) -> bool:
    """Whether the morning check should run now: a trading day, from 09:00, not yet done today,
    and OpenAlgo's master contract downloaded today (P24)."""
    from datetime import datetime

    from sudarshan.autologin.schedule import IST

    local = datetime.fromtimestamp(now / 1000, IST)
    day = local.date()
    key = day.year * 10000 + day.month * 100 + day.day
    if last_done == key or not calendar.is_trading_day(day):
        return False
    if local.hour * 60 + local.minute < RESOLVE_AT_MIN:
        return False
    return resolver.downloaded_today(day.isoformat())


def register_job() -> None:
    """Register the morning check as the job "resolve" (the entry point calls this once)."""
    import asyncio
    from functools import partial

    from sudarshan.autologin.schedule import TradingCalendar

    from .service import job

    @job("resolve")
    async def resolve_job(service) -> None:
        resolver = service.ensure_resolver()
        if resolver is None:
            log.warning(
                "The morning check of share identities is off: OpenAlgo's database is unknown."
            )
            return
        calendar = TradingCalendar(
            service.settings.holidays_file, service.settings.extra_days_file, logger=log
        )
        loop, last = asyncio.get_running_loop(), None
        while True:
            now = service.clock()
            due = await loop.run_in_executor(None, resolve_due, calendar, resolver, now, last)
            if due:
                await loop.run_in_executor(
                    None, partial(reresolve, service.store, resolver, now=now)
                )
                last = day_key(now)
            await asyncio.sleep(CHECK_EVERY_S)


def day_key(now: int) -> int:
    from .core.days import day_of_instant

    return day_of_instant(now)
