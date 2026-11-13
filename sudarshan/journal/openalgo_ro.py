"""OpenAlgo's own records, read-only (JB3', design 3.3 as amended by Y17, Y29, Y30).

Four files, each opened the way ``sudarshan/alerts/owner.py`` opens openalgo.db: ``mode=ro`` when
its write-ahead log is there, ``immutable=1`` when it is not, on a NullPool engine made for one
question and disposed after it. A file that is not there is never made (SQLite would make an
empty one): the reader says it is unavailable.

    openalgo.db  ``order_logs`` (live placements: the strategy label and the order's type),
                 ``analyzer_logs`` (sandbox placements), ``symtoken`` (the instrument behind an
                 instrument key, its expiry; the index expiries of a day)
    latency.db   ``order_latency``: round trip and total latency by order id (M12.12); no broker
                 answer is kept there (Y29)
    sandbox.db   ``sandbox_trades``: the sandbox's fills (Y17), never read through the API

Only these columns are copied out of a placement: strategy, symbol, exchange, action, quantity,
price type, product, price and trigger price, never the API key field. Nothing is written to any
of these files, and nothing read here is logged at INFO.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from database.engine_factory import create_db_engine
from sudarshan import regimes
from sudarshan.alerts.owner import database_urls

from .core import kinds
from .core.money import IST, to_date, to_day

log = logging.getLogger("sudarshan.journal.records")

#: OpenAlgo api types that place an order (a modify or a cancel names one, never places it).
NOT_PLACEMENTS = frozenset(
    {"modifyorder", "cancelorder", "cancelallorder", "modifygttorder", "cancelgttorder",
     "gttorderbook", "orderstatus", "orderbook", "tradebook", "positionbook"}
)  # fmt: skip
#: The label of an order with no strategy in its placement (design 11.1, decision 8).
MANUAL = "Manual"
UPSTOX_APP = "Upstox app"
FO_EXCHANGES = ("NFO", "BFO")
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
BATCH = 400


class RecordsUnavailable(Exception):
    """One of OpenAlgo's files could not be read now (missing, or not readable)."""


@dataclass(frozen=True)
class Placement:
    """What OpenAlgo's log says about the placement of one order."""

    order_id: str
    api_type: str
    strategy: str
    placed_ms: int | None
    pricetype: str | None = None
    product: str | None = None
    limit_price_u: int | None = None
    trigger_price_u: int | None = None


@dataclass(frozen=True)
class Listing:
    """One instrument of the master contract, as the journal needs it."""

    key: str
    symbol: str
    exchange: str
    expiry_day: int | None
    lotsize: int | None
    instrumenttype: str | None


def strategy_label(value: Any) -> str:
    """A placement's strategy as the journal shows it: at most 40 characters, "Manual" when
    there is none."""
    if not isinstance(value, str) or not value.strip():
        return MANUAL
    return " ".join(value.split())[: kinds.LIMITS["strategy_name"]]


def price_u(value: Any) -> int | None:
    """A price in rupees (a number or its text) as price_u, half away from zero; None if none."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        found = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not found.is_finite():
        return None
    return int((found * kinds.PRICE_SCALE).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def instant_ms(value: Any) -> int | None:
    """OpenAlgo's stored IST wall time (``2026-11-09 10:01:00.123456``, with or without its
    offset) as epoch milliseconds; None when it is not one."""
    if not isinstance(value, str) or len(value) < 19:
        return None
    try:
        stamp = datetime.fromisoformat(value.strip().replace(" ", "T")[:26])
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=IST)
    return int(stamp.timestamp() * 1000)


def expiry_day(value: Any) -> int | None:
    """The master's ``13-NOV-26`` as 20261113; None for anything else."""
    if not isinstance(value, str):
        return None
    parts = value.strip().upper().split("-")
    if len(parts) != 3 or parts[1] not in MONTHS or not (parts[0].isdigit() and parts[2].isdigit()):
        return None
    year = int(parts[2]) + (2000 if len(parts[2]) == 2 else 0)
    try:
        return to_day(date(year, MONTHS.index(parts[1]) + 1, int(parts[0])))
    except ValueError:
        return None


def master_expiry(day: int) -> str:
    """20261113 as the master writes an expiry: ``13-NOV-26``."""
    d = to_date(day)
    return f"{d.day:02d}-{MONTHS[d.month - 1]}-{d.year % 100:02d}"


def _json(raw: Any) -> Any:
    if isinstance(raw, dict | list):
        return raw
    try:
        return json.loads(raw) if isinstance(raw, str | bytes) else None
    except ValueError:
        return None


def _order_ids(response: Any) -> list[str]:
    """Every order id a placement's answer names: one, or one per leg of a basket, a split or a
    multi-leg options order."""
    if not isinstance(response, dict):
        return []
    found = []
    single = response.get("orderid")
    if isinstance(single, str | int) and str(single).strip():
        found.append(str(single).strip())
    for name in ("results", "data", "orders"):
        rows = response.get(name)
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and isinstance(row.get("orderid"), str | int):
                    oid = str(row["orderid"]).strip()
                    if oid:
                        found.append(oid)
    return found


def placements_of(rows: Iterable[Mapping[str, Any]]) -> dict[str, Placement]:
    """Placements by order id from log rows (``api_type``, ``request_data``, ``response_data``,
    ``created_at``), the earliest row of an order kept."""
    out: dict[str, Placement] = {}
    for row in rows:
        api_type = str(row.get("api_type") or "")
        if api_type in NOT_PLACEMENTS:
            continue
        request, response = _json(row.get("request_data")), _json(row.get("response_data"))
        if not isinstance(request, dict):
            request = {}
        legs = request.get("orders") if isinstance(request.get("orders"), list) else None
        placed = instant_ms(row.get("created_at"))
        for n, order_id in enumerate(_order_ids(response)):
            if order_id in out:
                continue
            fields = request
            if legs and len(legs) > n and isinstance(legs[n], dict) and not request.get("symbol"):
                fields = {**request, **legs[n]}
            out[order_id] = Placement(
                order_id=order_id,
                api_type=api_type,
                strategy=strategy_label(request.get("strategy")),
                placed_ms=placed,
                pricetype=_upper(fields.get("pricetype")),
                product=_upper(fields.get("product")),
                limit_price_u=price_u(fields.get("price")) or None,
                trigger_price_u=price_u(fields.get("trigger_price")) or None,
            )
    return out


def _upper(value: Any) -> str | None:
    return value.strip().upper()[:16] if isinstance(value, str) and value.strip() else None


class _ReadOnly:
    """Questions to one SQLite file, each on an engine made for it and disposed after it."""

    def __init__(self, path: Path | None, name: str,
                 engine_factory: Callable[[str], Any] = create_db_engine):  # fmt: skip
        self.path, self.name, self._factory = path, name, engine_factory

    def query(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        if self.path is None or not Path(self.path).is_file():
            raise RecordsUnavailable(self.name)
        last: Exception | None = None
        for url in database_urls(Path(self.path)):
            engine = self._factory(url)
            try:
                with engine.connect() as conn:
                    return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]
            except SQLAlchemyError as exc:
                last = exc
            finally:
                engine.dispose()
        log.warning("%s could not be read just now (%s)", self.name, type(last).__name__)
        raise RecordsUnavailable(self.name)


def _bind(names: str, values: list) -> tuple[str, dict[str, Any]]:
    """``:p0, :p1, ...`` for an IN list, and the parameters."""
    params = {f"{names}{i}": v for i, v in enumerate(values)}
    return ", ".join(f":{k}" for k in params), params


class OpenAlgoRecords:
    """OpenAlgo's openalgo.db, latency.db and sandbox.db, read-only."""

    def __init__(
        self,
        openalgo_db: Path | None,
        latency_db: Path | None = None,
        sandbox_db: Path | None = None,
        *,
        engine_factory: Callable[[str], Any] = create_db_engine,
    ):
        self.main = _ReadOnly(openalgo_db, "openalgo.db", engine_factory)
        self.latency_file = _ReadOnly(latency_db, "latency.db", engine_factory)
        self.sandbox_file = _ReadOnly(sandbox_db, "sandbox.db", engine_factory)

    # ---- placements and latency -------------------------------------------------------------
    def placements(self, mode: str, since_day: int) -> dict[str, Placement]:
        """Placements logged on or after ``since_day``: ``order_logs`` for live orders,
        ``analyzer_logs`` for the sandbox's."""
        table = "order_logs" if mode == "live" else "analyzer_logs"
        d = to_date(since_day)
        rows = self.main.query(
            f"SELECT api_type, request_data, response_data, created_at FROM {table} "
            "WHERE created_at >= :since ORDER BY id",
            since=f"{d.isoformat()} 00:00:00",
        )
        return placements_of(rows)

    def latency(self, order_ids: Iterable[str]) -> dict[str, tuple[int | None, int | None]]:
        """(round trip, total) in whole milliseconds by order id, the newest row of an order."""
        out: dict[str, tuple[int | None, int | None]] = {}
        ids = sorted(set(order_ids))
        for start in range(0, len(ids), BATCH):
            marks, params = _bind("o", ids[start : start + BATCH])
            for row in self.latency_file.query(
                "SELECT order_id, rtt_ms, total_latency_ms FROM order_latency "
                f"WHERE order_id IN ({marks}) ORDER BY id",
                **params,
            ):
                out[str(row["order_id"])] = (_ms(row["rtt_ms"]), _ms(row["total_latency_ms"]))
        return out

    # ---- the master contract ----------------------------------------------------------------
    def instruments(self, keys: Iterable[str]) -> dict[str, Listing]:
        """The master's row for each instrument key it lists."""
        out: dict[str, Listing] = {}
        wanted = sorted({k for k in keys if k})
        for start in range(0, len(wanted), BATCH):
            marks, params = _bind("k", wanted[start : start + BATCH])
            for row in self.main.query(
                "SELECT token, symbol, exchange, expiry, lotsize, instrumenttype FROM symtoken "
                f"WHERE token IN ({marks})",
                **params,
            ):
                out[row["token"]] = _listing(row)
        return out

    def instrument_by_symbol(
        self, pairs: Iterable[tuple[str, str]]
    ) -> dict[tuple[str, str], Listing]:
        """The master's row for each (symbol, exchange), as the sandbox names its instruments."""
        out: dict[tuple[str, str], Listing] = {}
        for symbol, exchange in sorted(set(pairs)):
            rows = self.main.query(
                "SELECT token, symbol, exchange, expiry, lotsize, instrumenttype FROM symtoken "
                "WHERE symbol = :symbol AND exchange = :exchange LIMIT 1",
                symbol=symbol,
                exchange=exchange,
            )
            if rows:
                out[(symbol, exchange)] = _listing(rows[0])
        return out

    def index_expiry_days(self, days: Iterable[int]) -> set[int]:
        """The days among ``days`` on which an index option of the master expires (design 5.2:
        the index expiry split). An index is told by its whole underlying (``regimes``)."""
        wanted = sorted(set(days))
        if not wanted:
            return set()
        marks, params = _bind("e", [master_expiry(d) for d in wanted])
        rows = self.main.query(
            "SELECT expiry, MIN(symbol) AS symbol FROM symtoken "
            "WHERE exchange IN ('NFO', 'BFO') AND instrumenttype IN ('CE', 'PE') "
            f"AND expiry IN ({marks}) GROUP BY expiry, name",
            **params,
        )
        found = set()
        for row in rows:
            day = expiry_day(row["expiry"])
            if day is not None and regimes.index_derivative(str(row["symbol"] or "")):
                found.add(day)
        return found

    # ---- the sandbox --------------------------------------------------------------------------
    def sandbox_trades(self, day: int) -> list[dict[str, Any]]:
        """The sandbox's fills of one IST day, oldest first."""
        d = to_date(day)
        nxt = d + timedelta(days=1)
        return self.sandbox_file.query(
            "SELECT tradeid, orderid, symbol, exchange, action, quantity, price, product, "
            "strategy, trade_timestamp FROM sandbox_trades "
            "WHERE trade_timestamp >= :start AND trade_timestamp < :end ORDER BY id",
            start=f"{d.isoformat()} 00:00:00",
            end=f"{nxt.isoformat()} 00:00:00",
        )


def _ms(value: Any) -> int | None:
    try:
        return round(float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _listing(row: Mapping[str, Any]) -> Listing:
    lot = row.get("lotsize")
    return Listing(
        key=str(row["token"]),
        symbol=str(row["symbol"]),
        exchange=str(row["exchange"]),
        expiry_day=expiry_day(row.get("expiry")),
        lotsize=int(lot) if isinstance(lot, int | float) and lot else None,
        instrumenttype=str(row["instrumenttype"]) if row.get("instrumenttype") else None,
    )


def records_for(service: Any) -> OpenAlgoRecords:
    """The service's reader of OpenAlgo's records, built on first use."""
    s = service.settings
    return service.part(
        "records", lambda svc: OpenAlgoRecords(s.openalgo_db, s.latency_db, s.sandbox_db)
    )
