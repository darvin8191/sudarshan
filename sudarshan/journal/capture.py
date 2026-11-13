"""Recording fills and labelling their orders (JB3', M14.1 and M12.12; design 3.1-3.3 as amended).

**Live fills** come from Upstox's trades for the day, read through the portfolio's journal
purpose (Y4): one row per execution with its trade id, order id and exchange time. Each becomes a
journal fill: the instrument named as OpenAlgo names it (``symtoken`` by instrument key; the
segment's exchange and Upstox's own symbol while the master has no row), the product as OpenAlgo
writes it (Upstox's I is MIS; D is NRML on a derivatives exchange and CNC in cash), the quantity
in units and the price in ``price_u``. A row whose product or quantity the journal does not model
(a fraction of a unit, an unknown product) is left out and counted.

**Sandbox fills** come from ``db/sandbox.db`` read-only (Y17), never from the API: a trade id
``sb:<tradeid>``, the sandbox's own symbol and product, and its strategy.

**Labels** (design 3.3): an order's placement is found in OpenAlgo's log by its order id
(``order_logs`` live, ``analyzer_logs`` sandbox) and gives the strategy (at most 40 characters,
"Manual" when the placement has none), the type and the prices; latency.db adds its round trip
(M12.12). A live order with no placement 60 seconds after its first fill was placed outside
OpenAlgo: "Upstox app" (mutation target 23), and the portfolio's order history gives its type and
time. A sandbox order is never one of those: without a placement it takes its trade's strategy.

Reads of one mode never overlap (one lock each). What a read wrote is handed to the hooks the
later parts add (the day replay, the feed's claims). Logs carry counts only.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sudarshan.portfolio.core.kinds import QTY_SCALE

from .core import kinds
from .core.money import ist_day, u_to_paise
from .openalgo_ro import (
    MANUAL,
    UPSTOX_APP,
    Listing,
    Placement,
    RecordsUnavailable,
    instant_ms,
    price_u,
    records_for,
    strategy_label,
)
from .portfolio import PortfolioUnavailable, link_for
from .store import Scope

log = logging.getLogger("sudarshan.journal.capture")

OWNER = Scope.owner()
#: Upstox's segment of an instrument key, as OpenAlgo names the exchange (its master's own map).
SEGMENT_EXCHANGE = {
    "NSE_EQ": "NSE", "NSE_FO": "NFO", "NCD_FO": "CDS", "BSE_EQ": "BSE", "BSE_FO": "BFO",
    "BCD_FO": "BCD", "MCX_FO": "MCX", "NSE_INDEX": "NSE_INDEX", "BSE_INDEX": "BSE_INDEX",
}  # fmt: skip
DERIVATIVE_EXCHANGES = frozenset({"NFO", "BFO", "MCX", "CDS", "BCD"})
#: How far back a placement may have been logged: an after-market order or a GTT fills days later.
PLACEMENT_DAYS = 7
OUTSIDE_WAIT_MS = kinds.OUTSIDE_ORDER_WAIT_S * 1000
#: Orders asked of the portfolio's order history in one pass (an order from the Upstox app).
HISTORY_PER_PASS = 5
Hook = Callable[[str, set[int]], Awaitable[None]]


def upstox_product(product: str, exchange: str) -> str | None:
    """Upstox's product as OpenAlgo writes it, or None for one the journal does not model."""
    p = str(product or "").strip().upper()
    if p in ("I", "CO", "MIS"):
        return "MIS"
    if p in ("NRML", "CNC"):
        return p
    if p == "MTF":
        return "CNC"
    if p == "D":
        return "NRML" if exchange in DERIVATIVE_EXCHANGES else "CNC"
    return None


def fill_from_trade(
    row: Mapping[str, Any], listing: Listing | None, source: str, read_ms: int
) -> dict[str, Any] | None:
    """One of the portfolio's ``trades_day`` rows as a journal fill; None when the journal does
    not model it."""
    key = str(row.get("instrument_key") or "")
    segment = key.partition("|")[0]
    exchange = (
        listing.exchange
        if listing
        else SEGMENT_EXCHANGE.get(segment) or str(row.get("exchange") or "")
    )
    symbol = listing.symbol if listing else str(row.get("symbol") or key)
    product = upstox_product(row.get("product"), exchange)
    qty_u, price = row.get("qty_u"), row.get("price_u")
    action = str(row.get("transaction_type") or "").upper()
    if (
        product is None
        or not exchange
        or action not in kinds.ACTIONS
        or not isinstance(qty_u, int)
        or not isinstance(price, int)
        or qty_u <= 0
        or qty_u % QTY_SCALE
        or price < 0
        or not row.get("trade_id")
        or not row.get("order_id")
    ):
        return None
    qty = qty_u // QTY_SCALE
    at = row.get("exchange_at") or row.get("placed_at") or read_ms
    return {
        "mode": "live",
        "day": ist_day(at),
        "trade_id": str(row["trade_id"]),
        "order_id": str(row["order_id"]),
        "exchange": exchange,
        "symbol": symbol,
        "instrument_key": key,
        "product": product,
        "action": action,
        "qty": qty,
        "price_u": price,
        "value_paise": u_to_paise(qty * price),
        "exch_ts_ms": int(at),
        "order_ts_ms": row.get("placed_at"),
        "source": source,
    }


def fill_from_sandbox(
    row: Mapping[str, Any], listing: Listing | None, source: str = "sandbox_book"
) -> dict[str, Any] | None:
    """One ``sandbox_trades`` row as a journal fill (mode sandbox); None when it is unusable."""
    at = instant_ms(row.get("trade_timestamp"))
    price = price_u(row.get("price"))
    product = str(row.get("product") or "").strip().upper()
    action = str(row.get("action") or "").strip().upper()
    try:
        qty = int(row.get("quantity") or 0)
    except (TypeError, ValueError):
        qty = 0
    if (
        at is None
        or price is None
        or qty <= 0
        or product not in (*kinds.INTRADAY_PRODUCTS, *kinds.CARRY_PRODUCTS)
        or action not in kinds.ACTIONS
        or not row.get("tradeid")
        or not row.get("orderid")
    ):
        return None
    return {
        "mode": "sandbox",
        "day": ist_day(at),
        "trade_id": f"sb:{row['tradeid']}",
        "order_id": str(row["orderid"]),
        "exchange": str(row.get("exchange") or ""),
        "symbol": str(row.get("symbol") or ""),
        "instrument_key": listing.key if listing else "",
        "product": product,
        "action": action,
        "qty": qty,
        "price_u": price,
        "value_paise": u_to_paise(qty * price),
        "exch_ts_ms": at,
        "order_ts_ms": None,
        "source": source,
    }


def label_of(
    mode: str,
    ref: Mapping[str, Any],
    placement: Placement | None,
    now: int,
    sandbox_strategy: str | None = None,
) -> dict[str, Any] | None:
    """The label an order takes now, or None while it stays unknown (a live order within 60 s of
    its first fill and no placement yet)."""
    if placement is not None:
        return {
            "strategy": placement.strategy,
            "surface": "api",
            "api_type": placement.api_type,
            "pricetype": placement.pricetype,
            "product": placement.product,
            "limit_price_u": placement.limit_price_u,
            "trigger_price_u": placement.trigger_price_u,
            "placed_ms": placement.placed_ms,
        }
    if mode == "sandbox":
        return {"strategy": strategy_label(sandbox_strategy), "surface": "api"}
    if now - int(ref["first_fill_ms"]) >= OUTSIDE_WAIT_MS:
        return {"strategy": UPSTOX_APP, "surface": "upstox_app"}
    return None


def history_label(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """What the portfolio's order history adds to an order from the Upstox app: its type, its
    first time and its prices."""
    found = [r for r in rows if isinstance(r, Mapping)]
    if not found:
        return {}
    times = [r["placed_at"] for r in found if isinstance(r.get("placed_at"), int)]
    first = found[0]
    out: dict[str, Any] = {
        "pricetype": str(first.get("order_type") or "")[:16] or None,
        "placed_ms": min(times) if times else None,
        "status": str(found[-1].get("status") or "")[:40] or None,
    }
    if isinstance(first.get("price_u"), int) and first["price_u"] > 0:
        out["limit_price_u"] = first["price_u"]
    if isinstance(first.get("trigger_price_u"), int) and first["trigger_price_u"] > 0:
        out["trigger_price_u"] = first["trigger_price_u"]
    return {k: v for k, v in out.items() if v is not None}


@dataclass
class Outcome:
    """What one read did, in counts: rows read, fills new, fixed and back, rows left out, the
    day's fills found withdrawn; ``state`` ok, or not_read with the reason in words."""

    state: str = "ok"
    words: str | None = None
    read: int = 0
    new: int = 0
    fixed: int = 0
    back: int = 0
    skipped: int = 0
    withdrawn: int = 0
    days: set[int] = field(default_factory=set)

    def line(self) -> str:
        if self.state != "ok":
            return f"not read: {self.words}"
        return (
            f"{self.read} rows, {self.new} new, {self.fixed} corrected, {self.back} back, "
            f"{self.skipped} left out, {self.withdrawn} withdrawn"
        )


class Capture:
    """The service's recorder of fills (a part: ``capture_for``)."""

    def __init__(self, service: Any):
        self.service = service
        self.locks = {mode: asyncio.Lock() for mode in kinds.MODES}
        #: Called after a read wrote fills or labelled orders: ``hook(mode, days)``. The day
        #: replay comes first; the feed's claims (JB6) add theirs.
        self.hooks: list[Hook] = [self._replay]
        self.last: dict[str, Outcome] = {}
        #: The strategy each sandbox order's trades carry, from the last read of sandbox.db.
        self._sandbox_strategy: dict[str, Any] = {}

    async def _replay(self, mode: str, days: set[int]) -> None:
        """The first hook: pair the touched days again (JB4, ``book.replay``)."""
        from .book import replay

        await replay(self.service, mode, days)

    async def _thread(self, fn: Callable[[], Any]) -> Any:
        return await asyncio.get_running_loop().run_in_executor(None, fn)

    # ---- live --------------------------------------------------------------------------------
    async def read_live(self, source: str = "stream_read", withdraw: bool = False) -> Outcome:
        """One read of the day's trades, recorded. ``withdraw`` (the close check and the 22:00
        read): a fill of today that Upstox's whole list no longer holds is marked withdrawn."""
        async with self.locks["live"]:
            out = await self._read_live(source, withdraw)
            self.last["live"] = out
            return out

    async def _read_live(self, source: str, withdraw: bool) -> Outcome:
        svc = self.service
        try:
            answer = await link_for(svc).trades_day()
        except PortfolioUnavailable as exc:
            return Outcome("not_read", str(exc))
        if answer.get("state") != "ok" or not isinstance(answer.get("data"), list):
            return Outcome("not_read", str(answer.get("note") or "Upstox could not be read."))
        rows = [r for r in answer["data"] if isinstance(r, Mapping)]
        now = svc.clock()
        records = records_for(svc)
        try:
            listings = await self._thread(
                lambda: records.instruments(r.get("instrument_key") for r in rows)
            )
        except RecordsUnavailable:
            listings = {}
        fills = [
            fill_from_trade(r, listings.get(r.get("instrument_key")), source, now) for r in rows
        ]
        kept = [f for f in fills if f is not None]
        out = Outcome(read=len(rows), skipped=len(fills) - len(kept))
        if out.skipped:
            log.warning("%d of the day's trades were left out: the journal does not model their "
                        "product or quantity", out.skipped)  # fmt: skip
        today = ist_day(now)
        listed = [str(r.get("trade_id")) for r in rows if r.get("trade_id")]
        await self._write("live", kept, now, out, withdraw_day=today if withdraw else None,
                          listed=listed)  # fmt: skip
        await self.relabel("live")
        log.info("live fills read (%s): %s", source, out.line())
        return out

    # ---- sandbox -----------------------------------------------------------------------------
    async def read_sandbox(self, source: str = "sandbox_book") -> Outcome:
        """One read of today's sandbox fills from sandbox.db, recorded."""
        async with self.locks["sandbox"]:
            out = await self._read_sandbox(source)
            self.last["sandbox"] = out
            return out

    async def _read_sandbox(self, source: str) -> Outcome:
        svc = self.service
        now = svc.clock()
        records = records_for(svc)
        try:
            rows = await self._thread(lambda: records.sandbox_trades(ist_day(now)))
        except RecordsUnavailable:
            return Outcome("not_read", "The sandbox's book could not be read just now.")
        pairs = {(str(r.get("symbol")), str(r.get("exchange"))) for r in rows}
        try:
            listings = await self._thread(lambda: records.instrument_by_symbol(pairs))
        except RecordsUnavailable:
            listings = {}
        fills = [
            fill_from_sandbox(
                r, listings.get((str(r.get("symbol")), str(r.get("exchange")))), source
            )
            for r in rows
        ]
        kept = [f for f in fills if f is not None]
        self._sandbox_strategy = {
            str(r.get("orderid")): r.get("strategy") for r in rows if r.get("orderid")
        }
        out = Outcome(read=len(rows), skipped=len(fills) - len(kept))
        await self._write("sandbox", kept, now, out)
        await self.relabel("sandbox")
        log.info("sandbox fills read: %s", out.line())
        return out

    # ---- history ------------------------------------------------------------------------------
    async def record_history(self, fills: list[dict[str, Any]],
                             labels: Mapping[str, Mapping[str, Any]]) -> Outcome:  # fmt: skip
        """Fills rebuilt from Upstox's trade history (the 22:00 catch-up), with their orders'
        labels written in the same transaction, so no label pass ever asks about them."""
        async with self.locks["live"]:
            out = Outcome(read=len(fills))
            await self._write("live", fills, self.service.clock(), out, labels=labels)
            return out

    # ---- writing and labelling ---------------------------------------------------------------
    async def _write(self, mode: str, fills: list[dict[str, Any]], now: int, out: Outcome,
                     withdraw_day: int | None = None, listed: Iterable[str] = (),
                     labels: Mapping[str, Mapping[str, Any]] | None = None) -> None:  # fmt: skip
        store = self.service.store
        firsts: dict[str, int] = {}
        for f in fills:
            firsts[f["order_id"]] = min(firsts.get(f["order_id"], f["exch_ts_ms"]), f["exch_ts_ms"])
        listed = list(listed)

        def write(tx):
            counts = tx.fills_upsert(fills, now)
            for order_id, first in firsts.items():
                tx.order_ref_note(mode, order_id, first, now)
            for order_id, values in (labels or {}).items():
                tx.order_ref_set(mode, order_id, values, now)
            gone = tx.fills_withdraw(mode, withdraw_day, listed, now) if withdraw_day else 0
            return counts, gone

        counts, gone = await self._thread(lambda: store.write(OWNER, write))
        out.new, out.fixed, out.back, out.withdrawn = (
            counts["new"], counts["fixed"], counts["back"], gone,
        )  # fmt: skip
        out.days = {f["day"] for f in fills} | ({withdraw_day} if gone else set())
        if out.new or out.fixed or out.back or out.withdrawn:
            for hook in list(self.hooks):
                try:
                    await hook(mode, set(out.days))
                except Exception:
                    log.exception("a step after recording the %s fills failed", mode)

    async def relabel(self, mode: str) -> int:
        """Label every order of ``mode`` still unknown that can be labelled now; returns how many
        were. A live order labelled "Upstox app" is asked of the portfolio's order history."""
        svc = self.service
        store, now = svc.store, svc.clock()
        pending = await self._thread(
            lambda: list(
                store.read(OWNER, lambda tx: tx.order_refs(mode, surface="unknown")).values()
            )
        )
        if not pending:
            return 0
        records = records_for(svc)
        since = ist_day(now - PLACEMENT_DAYS * 86_400_000)
        try:
            placements = await self._thread(lambda: records.placements(mode, since))
        except RecordsUnavailable:
            placements = {}
        found = [r["order_id"] for r in pending if r["order_id"] in placements]
        try:
            latency = await self._thread(lambda: records.latency(found)) if found else {}
        except RecordsUnavailable:
            latency = {}
        sandbox_strategy = self._sandbox_strategy
        labels: dict[str, dict[str, Any]] = {}
        for ref in pending:
            oid = ref["order_id"]
            label = label_of(mode, ref, placements.get(oid), now, sandbox_strategy.get(oid))
            if label is None:
                continue
            rtt, total = latency.get(oid, (None, None))
            labels[oid] = {**label, "rtt_ms": rtt, "total_latency_ms": total}
        outside = [oid for oid, label in labels.items() if label["surface"] == "upstox_app"]
        for oid in outside[:HISTORY_PER_PASS]:
            try:
                answer = await link_for(svc).order_history(oid)
            except PortfolioUnavailable:
                break
            if answer.get("state") == "ok":
                labels[oid].update(history_label(answer.get("rows") or []))
        if labels:
            await self._thread(
                lambda: store.write(
                    OWNER,
                    lambda tx: [tx.order_ref_set(mode, oid, values, now)
                                for oid, values in labels.items()],
                )
            )  # fmt: skip
            days = {ist_day(int(r["first_fill_ms"])) for r in pending if r["order_id"] in labels}
            for hook in list(self.hooks):
                try:
                    await hook(mode, days)
                except Exception:
                    log.exception("a step after labelling the %s orders failed", mode)
        log.info("%s orders labelled: %d of %d waiting", mode, len(labels), len(pending))
        return len(labels)

    async def pending(self, mode: str) -> int:
        """Orders of ``mode`` whose label is still unknown."""
        store = self.service.store
        return await self._thread(
            lambda: store.read(
                OWNER, lambda tx: tx.count("order_ref", mode=mode, surface="unknown")
            )
        )


def capture_for(service: Any) -> Capture:
    """The service's recorder of fills, built on first use."""
    return service.part("capture", Capture)
