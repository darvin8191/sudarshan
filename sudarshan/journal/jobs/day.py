"""The day's close at 15:45 (JB5, M15.2's figures; cross-design Y22.1 and Y22.4).

At the day's last close plus 5 minutes the journal writes the live account's ``day_close`` row
from what it has then: the fills recorded live (no read of its own: the close check at 16:05 is
the read that corrects the day) and Upstox's short-term positions through the portfolio's journal
purpose. The day figure goes through ``sudarshan/guard/daily.py``'s row logic (an import, never a
copy): the figure the daily loss limit reads, gross, delivery left out, and Upstox's two figures
checked against a row's own trades and prices where the row carries them.

    realised, unrealised   guard.daily.live_figure over the positions rows, to paise half up;
                           positions not read: the journal's trips closed today (realised),
                           unrealised not known, marked approximate
    trades, won, lost      live trips closed today, by their net after the charges estimate
    charges estimate       core/charges over today's live fills, per executed order
    fills_live             today's fills recorded live (the close check's are counted apart)
    carried overnight      positions still open in a carry product
    kill switch, lock      db/sudarshan_risk.db, read-only (cross-design Y30)
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import sys
import tempfile
from collections.abc import Iterable, Mapping
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from ..capture import upstox_product
from ..config import setup_logging
from ..core import charges as charges_core
from ..core import kinds, metrics
from ..openalgo_ro import RecordsUnavailable, records_for
from ..portfolio import PortfolioUnavailable, link_for
from ..risk_ro import risk_for
from ..schedule import register_job
from ..store import Scope

log = logging.getLogger("sudarshan.journal")

OWNER = Scope.owner()
QTY_SCALE = 1_000_000
#: Upstox's intraday products: what is still open in one of them at 15:45 is squared off by the
#: broker, never carried.
INTRADAY = frozenset({"I", "CO", "MIS"})
GUARD_MODULE = "sudarshan.guard.daily"
#: OpenAlgo's logging module writes an error file when it is imported: it goes to this process's
#: private temporary directory (the unit has PrivateTmp), never to OpenAlgo's log directory.
OPENALGO_LOG_DIR = Path(tempfile.gettempdir()) / "sudarshan-journal-openalgo-log"


def guard_daily(log_level: str = "INFO") -> Any:
    """``sudarshan/guard/daily.py`` (cross-design Y22.4: an import, not a copy).

    Importing it brings OpenAlgo's logging module, which at import would load all of OpenAlgo's
    .env into this process and replace its log handlers: the .env load is skipped (the alerts'
    ``without_openalgo_dotenv``), its error file goes to a private temporary directory (unless
    LOG_DIR names one), and this service's own logging is set up again after it."""
    module = sys.modules.get(GUARD_MODULE)
    if module is not None:
        return module
    from sudarshan.alerts.owner import without_openalgo_dotenv

    os.environ["LOG_TO_FILE"] = "False"
    os.environ.setdefault("LOG_DIR", str(OPENALGO_LOG_DIR))
    with without_openalgo_dotenv():
        module = importlib.import_module(GUARD_MODULE)
    setup_logging({"SUDARSHAN_JOURNAL_LOG_LEVEL": log_level})
    return module


def _rupees(value: Any, scale: int) -> float | None:
    if value is None or isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(Decimal(value) / scale)


def guard_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The portfolio's position rows (paise, ``price_u``, ``qty_u``) in the shape the guard reads:
    rupees under Upstox's own names. The day's buy and sell values pass through when the
    portfolio carries them (``day_buy_value_paise``); without them the guard keeps Upstox's two
    figures, as it does for a row its formula cannot work out."""
    out = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        out.append({
            "instrument_token": row.get("instrument_key"),
            "product": row.get("product"),
            "quantity": _rupees(row.get("qty_u"), QTY_SCALE),
            "overnight_quantity": _rupees(row.get("overnight_qty_u"), QTY_SCALE),
            "last_price": _rupees(row.get("last_price_u"), kinds.PRICE_SCALE),
            "close_price": _rupees(row.get("close_price_u"), kinds.PRICE_SCALE),
            "multiplier": row.get("multiplier"),
            "realised": _rupees(row.get("realised_paise"), 100),
            "unrealised": _rupees(row.get("unrealised_paise"), 100),
            "pnl": _rupees(row.get("pnl_paise"), 100),
            "day_buy_value": _rupees(row.get("day_buy_value_paise"), 100),
            "day_sell_value": _rupees(row.get("day_sell_value_paise"), 100),
        })  # fmt: skip
    return out


def to_paise(rupees: float) -> int:
    return int((Decimal(repr(rupees)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def carried(rows: Iterable[Mapping[str, Any]], listings: Mapping[str, Any]) -> list[dict]:
    """Positions still open in a carry product: what goes overnight (the brief reads it back)."""
    out = []
    for row in rows:
        qty_u, product = row.get("qty_u"), str(row.get("product") or "").upper()
        if not isinstance(qty_u, int) or not qty_u or product in INTRADAY:
            continue
        key = str(row.get("instrument_key") or "")
        listing = listings.get(key)
        exchange = listing.exchange if listing else ""
        out.append({
            "symbol": listing.symbol if listing else str(row.get("name") or key),
            "exchange": exchange,
            "product": upstox_product(product, exchange) or product,
            "qty": int(Decimal(qty_u) / QTY_SCALE),
        })  # fmt: skip
    return sorted(out, key=lambda p: (p["exchange"], p["symbol"]))


def day_charges(fills: Iterable[Mapping[str, Any]], refs: Mapping[str, Mapping[str, Any]],
                plan: str) -> int:  # fmt: skip
    """The charges estimate of the day's executed orders (core/charges: brokerage per order)."""
    rows = [
        {"mode": "live", "order_id": f["order_id"], "day": f["day"], "action": f["action"],
         "qty": f["qty"], "price_u": f["price_u"], "exchange": f["exchange"],
         "symbol": f["symbol"], "product": f["product"],
         "surface": (refs.get(f["order_id"]) or {}).get("surface")}
        for f in fills
    ]  # fmt: skip
    return int(charges_core.estimate(rows, plan)["parts"]["total_paise"]) if rows else 0


async def _thread(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


async def close_day(service: Any, today: int, now: int, late: bool) -> str:
    """Write today's ``day_close`` for the live account; returns the job log's line."""
    try:
        answer = await link_for(service).positions()
    except PortfolioUnavailable as exc:
        answer = {"state": "unavailable", "note": str(exc)}
    rows = answer.get("data") if answer.get("state") == "ok" else None
    rows = [r for r in rows if isinstance(r, Mapping)] if isinstance(rows, list) else None
    listings: Mapping[str, Any] = {}
    if rows:
        try:
            listings = await _thread(
                lambda: records_for(service).instruments(r.get("instrument_key") for r in rows)
            )
        except RecordsUnavailable:
            listings = {}
    risk = await _thread(lambda: risk_for(service).state(today))
    figure = None
    if rows is not None:
        figure = guard_daily(service.settings.log_level).live_figure(guard_rows(rows))

    def write(tx):
        trips = tx.trips_page("live", status="closed", from_day=today, to_day=today, limit=100_000)
        block = metrics.compute([t for t in trips if t["net_paise"] is not None])
        fills = [f for f in tx.fills_of("live", day=today) if f["source"] != "withdrawn"]
        refs = tx.order_refs("live", {f["order_id"] for f in fills})
        values = {
            "trades_closed": block["trips"], "wins": block["wins"], "losses": block["losses"],
            "flats": block["flats"],
            "charges_est_paise": day_charges(fills, refs, tx.settings()["charges_plan"]),
            "fills_live": sum(1 for f in fills if f["source"] == "stream_read"),
            "positions_overnight": carried(rows or [], listings),
            "kill_switch": None if not risk.read else ("on" if risk.kill_switch else "off"),
            "daily_lock": risk.lock_clock() if risk.read else None,
        }  # fmt: skip
        if figure is not None:
            values.update(realised_paise=to_paise(figure.realised),
                          unrealised_paise=to_paise(figure.unrealised),
                          approximate=bool(figure.approximate),
                          read_at=answer.get("read_at") or now)  # fmt: skip
        else:
            values.update(realised_paise=sum(int(t["gross_paise"] or 0) for t in trips),
                          unrealised_paise=None, approximate=True, read_at=None)  # fmt: skip
        tx.day_close_put("live", today, values, now)
        return values

    values = await _thread(lambda: service.store.write(OWNER, write))
    positions = "read" if figure is not None else "not read"
    return (
        f"positions {positions}, {values['trades_closed']} trades closed, "
        f"{values['fills_live']} fills recorded live, "
        f"{len(values['positions_overnight'])} carried"
    )


def register_jobs() -> None:
    register_job("eod", close_day)
