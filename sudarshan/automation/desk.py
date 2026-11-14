"""Everything the intake reads from, or hands to, the rest of the app (M12.1).

One object, so the intake itself stays a sequence of decisions and a test can stand a fake desk in
its place: no test here reaches Upstox, NSE, TradingView or Telegram. Every import of OpenAlgo's
order code is inside the method that needs it, so a request that is refused early (the flag off, a
link nobody made) imports none of it.

Nothing here starts a thread, a timer or a loop.
"""

from __future__ import annotations

import os
import time
from typing import Any

from utils.logging import get_logger

logger = get_logger(__name__)

SENDERS_ENV = "SUDARSHAN_AUTOMATION_SENDERS"
#: TradingView's published sender addresses ("About webhooks").
TRADINGVIEW = ("52.89.214.238", "34.212.75.30", "54.218.53.128", "52.32.178.7")


def senders() -> frozenset[str]:
    """The addresses an alert may come from: TradingView's four, or the owner's own list in
    ``SUDARSHAN_AUTOMATION_SENDERS`` (commas) for the day TradingView changes them."""
    raw = os.environ.get(SENDERS_ENV, "").strip().strip("'\"")
    if not raw:
        return frozenset(TRADINGVIEW)
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


class Desk:
    """The real readers and the one sender. Each method is one small piece of I/O."""

    # -- time -----------------------------------------------------------------------------------

    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)  # green under eventlet: the hub keeps running

    # -- the master contract (in memory inside OpenAlgo) ---------------------------------------

    def _master(self):
        from sudarshan.watchlists import master

        return master.shared()

    def master_loaded(self) -> bool:
        return self._master().loaded()

    def known(self, exchange: str, symbol: str) -> bool:
        return self._master().known(exchange, symbol)

    def future_expiries(self, underlying: str) -> list[str]:
        return self._master().expiries(underlying, kind="futures")

    def lot_size(self, exchange: str, symbol: str) -> int | None:
        row = self._master().row(exchange, symbol)
        try:
            return int(getattr(row, "lotsize", None))
        except (TypeError, ValueError):
            return None

    def indices(self) -> frozenset[str]:
        from sudarshan.watchlists import sessions

        return sessions.index_underlyings()

    def closes(self, dates: list[str], exchange: str) -> dict[str, int]:
        from sudarshan.watchlists import sessions

        return sessions.closes(dates, exchange)

    def freeze_qty(self, underlying: str, exchange: str) -> int:
        from database.qty_freeze_db import get_freeze_qty

        return int(get_freeze_qty(underlying, exchange) or 0)

    # -- the app's switches ---------------------------------------------------------------------

    def app_in_analyze(self) -> bool:
        from database.settings_db import get_analyze_mode

        return bool(get_analyze_mode())

    def guard_stage(self) -> str:
        from sudarshan.guard import flag as guard_flag

        return guard_flag.stage()

    def owner(self) -> str | None:
        """The owner's OpenAlgo username."""
        from database.user_db import find_user_by_username

        admin = find_user_by_username()
        return None if admin is None else admin.username

    def api_key(self) -> str | None:
        """The owner's API key, read inside the process. Never stored, logged or sent anywhere but
        to the order service of this same process."""
        from database.auth_db import get_api_key_for_tradingview

        owner = self.owner()
        return get_api_key_for_tradingview(owner) if owner else None

    def semi_auto(self, api_key: str) -> bool:
        """Whether the app holds orders in the Action Center for a tap. An order queued there is
        later sent wherever the app's mode then points, so a Sandbox strategy's order could reach
        Upstox: the intake does not use it."""
        from services.order_router_service import should_route_to_pending

        return bool(should_route_to_pending(api_key, "smartorder"))

    # -- the one sender -------------------------------------------------------------------------

    def place(self, order: dict[str, Any], api_key: str, analyze: bool) -> tuple[bool, Any, int]:
        """One smart order, in the pipe the caller decided. Every claim, hold, sizing rule and the
        risk gate are the service's and stay there."""
        from services.place_smart_order_service import place_smart_order

        return place_smart_order(dict(order), api_key=api_key, analyze=analyze)

    # -- telling the owner ----------------------------------------------------------------------

    def emit(self, event: dict[str, Any]) -> None:
        """One event to the owner's admitted screens. Never fails an alert."""
        try:
            from extensions import emit_from_any_thread
            from sudarshan.automation import contract

            # A broadcast reaches only the owner's admitted sockets (SIO1, extensions.py).
            emit_from_any_thread(contract.EVENT, event)
        except Exception:
            logger.exception("Automation: the live event could not be sent")

    def tell(self, strategy_id: int, reason: str, sentence: str) -> None:
        """A line for the owner's Telegram (telegram.py merges them per minute)."""
