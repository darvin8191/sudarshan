"""Universes (M9.2, design 2.1): which instruments a screen looks at, from OpenAlgo's master contract.

The master contract is ``symtoken`` in ``openalgo.db``, filled by Upstox's daily download: for
Upstox, ``token`` is the instrument key (``NSE_EQ|INE002A01018``, ``NSE_FO|49081``),
``instrumenttype`` is ``EQ``, ``FUT``, ``CE``, ``PE`` or ``INDEX``, ``expiry`` reads ``30-OCT-25``
and an OpenAlgo derivative symbol is ``<underlying><DDMMMYY>[strike]<FUT|CE|PE>``. The file is
opened read-only, the way the alerts and the portfolio open it (``sudarshan.alerts.owner.
database_urls``), one question per connection, never through OpenAlgo's own session.

The builders are pure over the rows they are given, so a test builds them from an openalgo.db made
with OpenAlgo's model and a fake day. Each member carries ``cas`` (a cash stock that closes by
auction today, X6) from ``sessions.closes_by_auction``.

Universes:

- ``fno_stocks``: the cash listing of every stock with a future (the F&O list; CAS today).
- ``fno_futures`` and ``index_futures``: the nearest expiry not yet past, per underlying.
- ``indices``: the index keys the screens know (``catalog.INDEX_NAMES``).
- ``index_list``: a typed list shipped with the release (``universes/<id>.txt``, one symbol a
  line; S7: no download), mapped to cash listings; a name the master lacks is reported, not
  silently dropped.
- ``options``: the nearest expiry of NIFTY or BANKNIFTY, N strikes each side of the money for a
  given underlying price (the runtime passes today's; ``option_window`` is the pure part).
- ``watchlist``: the owner's OpenAlgo list, mapped by (symbol, exchange).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from database.engine_factory import create_db_engine
from sudarshan.alerts.market.sessions import INDEX_UNDERLYINGS, closes_by_auction

from . import catalog as cat
from .runs import Member

log = logging.getLogger("sudarshan.screener")

LISTS_DIR = Path(__file__).resolve().parent / "universes"
EXCHANGE_OF = {
    "NSE": "NSE_EQ",
    "BSE": "BSE_EQ",
    "NFO": "NSE_FO",
    "BFO": "BSE_FO",
    "NSE_INDEX": "NSE_INDEX",
}
#: The index keys a screen may look at, by the name the screens use (catalog.INDEX_NAMES).
INDEX_SYMBOLS = {
    "NIFTY": "NIFTY",
    "BANKNIFTY": "BANKNIFTY",
    "FINNIFTY": "FINNIFTY",
    "MIDCPNIFTY": "MIDCPNIFTY",
}
_EXPIRY = re.compile(
    r"^(?P<under>[A-Z0-9&-]+?)(?P<expiry>\d{2}[A-Z]{3}\d{2})(?P<rest>.*)(?P<kind>FUT|CE|PE)$"
)


class MasterUnavailable(RuntimeError):
    WORDS = "Today's master contract is not available yet. The screens wait for it."

    def __init__(self) -> None:
        super().__init__(self.WORDS)


@dataclass(frozen=True, slots=True)
class Listing:
    """One row of the master contract, as the universes need it."""

    symbol: str
    exchange: str
    key: str
    kind: str  # EQ, FUT, CE, PE, INDEX
    name: str = ""
    expiry: date | None = None
    strike: int | None = None  # paise
    lot: int | None = None
    tick_size: int | None = None  # paise

    @property
    def segment(self) -> str:
        return self.key.partition("|")[0] or EXCHANGE_OF.get(self.exchange, self.exchange)

    @property
    def underlying(self) -> str | None:
        """``RELIANCE`` for ``RELIANCE30OCT25FUT`` or ``NIFTY25NOV2625900CE``; None for cash."""
        found = _EXPIRY.match(self.symbol)
        return found.group("under") if found else None


def _date(text_value) -> date | None:
    if not text_value:
        return None
    try:
        return datetime.strptime(str(text_value).strip().upper(), "%d-%b-%y").date()
    except ValueError:
        return None


def _paise(value) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number * 100) if number == number else None


def listing_of(row: Mapping) -> Listing:
    return Listing(
        symbol=str(row["symbol"]).upper(),
        exchange=str(row["exchange"]),
        key=str(row["token"]),
        kind=str(row.get("instrumenttype") or "").upper(),
        name=str(row.get("name") or ""),
        expiry=_date(row.get("expiry")),
        strike=_paise(row.get("strike"))
        if row.get("strike") not in (None, "", 0, 0.0, -1)
        else None,
        lot=int(row["lotsize"]) if row.get("lotsize") else None,
        tick_size=_paise(row.get("tick_size")),
    )


class Master:
    """Reads ``symtoken`` read-only; every question opens a connection and closes it."""

    def __init__(self, db_path: Path, engine_factory: Callable[[str], object] = create_db_engine):
        self.db_path = Path(db_path)
        self._factory = engine_factory

    def _query(self, sql: str, **params) -> list[Mapping]:
        from sudarshan.alerts.owner import database_urls

        if not self.db_path.is_file():
            raise MasterUnavailable
        last: Exception | None = None
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
        raise MasterUnavailable from last

    _COLUMNS = "symbol, exchange, token, name, expiry, strike, lotsize, instrumenttype, tick_size"

    def rows(self, exchange: str, kinds: Sequence[str] | None = None) -> list[Listing]:
        sql = f"SELECT {self._COLUMNS} FROM symtoken WHERE exchange = :exchange"
        if kinds:
            sql += " AND instrumenttype IN (" + ", ".join(f":k{i}" for i in range(len(kinds))) + ")"
        params = {"exchange": exchange, **{f"k{i}": k for i, k in enumerate(kinds or ())}}
        return [listing_of(r) for r in self._query(sql, **params)]

    def cash(self, exchange: str = "NSE") -> dict[str, Listing]:
        """The cash listings by symbol; a symbol listed twice keeps its EQ row."""
        out: dict[str, Listing] = {}
        for row in self.rows(exchange):
            if row.kind in ("EQ", "") and (row.symbol not in out or row.kind == "EQ"):
                out[row.symbol] = row
        return out

    def ready(self) -> bool:
        try:
            return bool(self._query("SELECT 1 FROM symtoken LIMIT 1"))
        except MasterUnavailable:
            return False


# ---- builders (pure over listings) ----------------------------------------------------------------
def _member(listing: Listing, *, cas: bool = False) -> Member:
    return Member(listing.key, listing.symbol, listing.exchange, listing.segment, cas)


def nearest(futures: Iterable[Listing], today: date) -> dict[str, Listing]:
    """Per underlying, the future whose expiry is the first not before today."""
    best: dict[str, Listing] = {}
    for row in futures:
        under = row.underlying
        if under is None or row.expiry is None or row.expiry < today:
            continue
        have = best.get(under)
        if have is None or row.expiry < have.expiry:  # type: ignore[operator]
            best[under] = row
    return best


def fno_stocks(
    cash: Mapping[str, Listing], futures: Iterable[Listing], today: date
) -> list[Member]:
    """The cash listing of every stock with a future (any expiry), CAS marked."""
    names = sorted(
        {
            row.underlying
            for row in futures
            if row.underlying and row.underlying not in INDEX_UNDERLYINGS
        }
    )
    cas = closes_by_auction("NSE_EQ", today, derivatives=True)
    return [_member(cash[name], cas=cas) for name in names if name in cash]


def futures_of(futures: Iterable[Listing], today: date, *, index: bool) -> list[Member]:
    picked = nearest(futures, today)
    return [
        _member(row)
        for under, row in sorted(picked.items())
        if (under in INDEX_UNDERLYINGS) is index
    ]


def indices(index_rows: Iterable[Listing]) -> list[Member]:
    wanted = set(INDEX_SYMBOLS.values())
    return [
        _member(row) for row in sorted(index_rows, key=lambda r: r.symbol) if row.symbol in wanted
    ]


def read_list(list_id: str, directory: Path = LISTS_DIR) -> list[str]:
    """The typed list ``<id>.txt``: one symbol a line, ``#`` comments, blank lines ignored."""
    path = directory / f"{list_id}.txt"
    if not path.is_file():
        raise FileNotFoundError(path)
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text_line = line.split("#", 1)[0].strip().upper()
        if text_line:
            names.append(text_line)
    return names


def index_list(
    names: Sequence[str], cash: Mapping[str, Listing], today: date, *, fno: set[str]
) -> tuple[list[Member], list[str]]:
    """(members, names the master lacks). A member is CAS when it has derivatives today."""
    members, missing = [], []
    for name in names:
        row = cash.get(name)
        if row is None:
            missing.append(name)
            continue
        members.append(
            _member(row, cas=closes_by_auction("NSE_EQ", today, derivatives=name in fno))
        )
    return members, missing


def option_window(
    options: Iterable[Listing], underlying: str, ltp: int, today: date, strikes: int = 10
) -> list[Member]:
    """The nearest expiry's CE and PE, ``strikes`` each side of the strike nearest ``ltp`` (paise)."""
    rows = [
        r
        for r in options
        if r.underlying == underlying and r.expiry and r.expiry >= today and r.strike
    ]
    if not rows:
        return []
    expiry = min(r.expiry for r in rows)  # type: ignore[type-var]
    chain = [r for r in rows if r.expiry == expiry]
    levels = sorted({r.strike for r in chain})  # type: ignore[arg-type]
    at = min(range(len(levels)), key=lambda i: (abs(levels[i] - ltp), i))
    window = set(levels[max(0, at - strikes) : at + strikes + 1])
    picked = sorted((r for r in chain if r.strike in window), key=lambda r: (r.strike, r.kind))
    return [_member(r) for r in picked]


def watchlist_members(
    items: Iterable[Mapping],
    masters: Mapping[str, Mapping[str, Listing]],
    today: date,
    *,
    fno: set[str],
) -> tuple[list[Member], list[str]]:
    """OpenAlgo list items (symbol, exchange) mapped to listings; the unknown ones named."""
    members, missing = [], []
    for item in items:
        symbol, exchange = str(item["symbol"]).upper(), str(item["exchange"]).upper()
        row = masters.get(exchange, {}).get(symbol)
        if row is None:
            missing.append(f"{exchange}:{symbol}")
            continue
        cas = closes_by_auction(row.segment, today, derivatives=symbol in fno)
        members.append(_member(row, cas=cas))
    return members, missing


def member_rows(
    members: Iterable[Member], listings: Mapping[str, Listing] | None = None
) -> list[dict]:
    """The store's ``universe_member`` rows."""
    out = []
    for member in members:
        listing = (listings or {}).get(member.key)
        out.append(
            {
                "key": member.key,
                "symbol": member.symbol,
                "exchange": member.exchange,
                "segment": member.segment,
                "tick_size": listing.tick_size if listing else None,
                "lot": listing.lot if listing else None,
                "expiry": int(listing.expiry.strftime("%Y%m%d"))
                if listing and listing.expiry
                else None,
                "strike": listing.strike if listing else None,
                "opt_type": listing.kind if listing and listing.kind in ("CE", "PE") else None,
                "cas": member.cas,
            }
        )
    return out


__all__ = [
    "INDEX_SYMBOLS",
    "LISTS_DIR",
    "Listing",
    "Master",
    "MasterUnavailable",
    "fno_stocks",
    "futures_of",
    "index_list",
    "indices",
    "listing_of",
    "member_rows",
    "nearest",
    "option_window",
    "read_list",
    "watchlist_members",
]
