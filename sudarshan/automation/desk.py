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
#: Said when the day's broker login is gone (or the API key is no longer the owner's).
NO_SESSION = "The app is not logged in to Upstox. Log in, then the next alert is handled."


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

    def option_expiries(self, underlying: str) -> list[str]:
        return self._master().expiries(underlying, kind="options")

    def strikes(self, underlying: str, expiry: str) -> list[float]:
        return self._master().strikes(underlying, expiry)

    def underlying_price(self, underlying: str, exchange: str) -> float | None:
        """The underlying's last price, for an option strategy's strike: the index's own for an
        index, the share's for a stock. ``None`` when it cannot be read: no strike is guessed.
        The read is cut off after a few seconds (the risk gate's own limit), because the alert
        holds its strategy's lock while it waits."""
        index = underlying in self.indices()
        cash = "BSE" if exchange == "BFO" else "NSE"
        try:
            from services.quotes_service import get_quotes
            from sudarshan.guard import readers

            with readers.limited():
                ok, answer, _status = get_quotes(
                    underlying, f"{cash}_INDEX" if index else cash, api_key=self.api_key()
                )
            price = float((answer.get("data") or {}).get("ltp")) if ok else 0.0
        except Exception:
            logger.exception("Automation: the underlying's last price could not be read")
            return None
        return price if price > 0 else None

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

    def sender_banned(self) -> bool:
        """Whether an address alerts come from is on the app's ban list (put there by hand, or
        before the abuse trackers left these addresses alone). The page says so."""
        try:
            from database.traffic_db import IPBan

            return any(IPBan.is_ip_banned(address) for address in sorted(senders()))
        except Exception:
            logger.exception("Automation: the ban list could not be read")
            return False

    def kill_switch_on(self) -> bool:
        try:
            from sudarshan.guard import gate

            state = gate.GATE.state()
            return bool(state is not None and state.on)
        except Exception:
            logger.exception("Automation: the kill switch could not be read")
            return False

    def phrase_ok(self, typed: object, asked: str, surface: str) -> bool:
        """Whether ``typed`` is the owner's confirm phrase (the risk layer's own, read fresh). A
        wrong one goes to the risk layer's History, as its own routes write it: what was asked,
        never what was typed. An empty box is asked for again and not recorded."""
        from sudarshan.guard import killswitch
        from sudarshan.guard import store as risk_store

        try:
            expected = risk_store.STORE.settings(fresh=True).confirm_phrase
        except Exception:
            logger.exception("Automation: the confirm phrase could not be read")
            return False
        if killswitch.phrase_matches(typed, expected):
            return True
        if str(typed or "").strip():
            killswitch.note_phrase_refused(surface, self.owner() or "", asked)
        return False

    def trading_day(self, day) -> bool:
        """Whether the cash market traded on a date, by OpenAlgo's calendar. When the calendar
        cannot be read a weekday counts, which can only make the quiet line come a day early."""
        try:
            from database.market_calendar_db import is_market_holiday

            return not is_market_holiday(day, "NSE")
        except Exception:
            logger.exception("Automation: the market calendar could not be read")
            return day.weekday() < 5

    def others(self) -> list[dict[str, Any]]:
        """The other automation on this server, read only: each reader is tried on its own."""
        found: list[dict[str, Any]] = []
        owner = self.owner() or ""
        try:
            from database import strategy_module_db

            for row in strategy_module_db.list_strategies(owner):
                detail = "webhook locked" if row.get("webhook_locked") else "webhook open"
                detail += ", Live allowed" if row.get("live_enabled") else ", Sandbox only"
                found.append(
                    {
                        "kind": "strategy",
                        "name": str(row.get("name", "")),
                        "on": row.get("status") == "running",
                        "detail": detail,
                        "path": "/strategy",
                    }
                )
        except Exception:
            logger.exception("Automation: the strategy module's list could not be read")
        try:
            from database import flow_db

            for row in flow_db.get_all_workflows():
                if getattr(row, "webhook_enabled", False):
                    found.append(
                        {
                            "kind": "flow",
                            "name": str(row.name),
                            "on": bool(getattr(row, "is_active", False)),
                            "detail": "webhook on",
                            "path": "/flow",
                        }
                    )
        except Exception:
            logger.exception("Automation: the Flow list could not be read")
        try:
            from database import chartink_db

            for row in chartink_db.get_all_strategies():
                if getattr(row, "is_active", False):
                    found.append(
                        {
                            "kind": "chartink",
                            "name": str(row.name),
                            "on": True,
                            "detail": "active",
                            "path": "/chartink",
                        }
                    )
        except Exception:
            logger.exception("Automation: the Chartink list could not be read")
        try:
            import sys

            # Only when the app has loaded the Python strategy host: importing it here would
            # start its scheduler, and this is a read.
            host = sys.modules.get("blueprints.python_strategy")
            for config in list(getattr(host, "STRATEGY_CONFIGS", {}).values()):
                if config.get("is_running") or config.get("is_scheduled"):
                    found.append(
                        {
                            "kind": "python",
                            "name": str(config.get("name") or "Unnamed"),
                            "on": bool(config.get("is_running")),
                            "detail": "running" if config.get("is_running") else "scheduled",
                            "path": "/python",
                        }
                    )
        except Exception:
            logger.exception("Automation: the Python strategies could not be read")
        return found

    # -- the one sender -------------------------------------------------------------------------

    def broker_session(self, api_key: str) -> tuple[str | None, str | None]:
        """The owner's broker session for his API key: (token, broker), or (None, None) when the
        key is not his or the day's login is gone. Never stored, logged or sent anywhere but to
        the order service of this same process."""
        from database.auth_db import get_auth_token_broker

        found = get_auth_token_broker(api_key)
        token, broker = (found[0], found[1]) if found else (None, None)
        return (token, broker) if token and broker else (None, None)

    def held_quantity(
        self, exchange: str, symbol: str, product: str, api_key: str, analyze: bool
    ) -> int | None:
        """The signed quantity the account holds in one contract and product, in one pipe; or
        ``None`` when it cannot be read. Never a guess: an unread book is not "flat".

        The sandbox's own position row is the one its smart order reads. Upstox's is the
        positions rows corrected by the order book's fills (``sudarshan.books.fills``), read in
        that order, as an exit is sized: the row alone can show a fill a minute late. This only
        reads; it is asked when an earlier alert's answer was never recorded (intake.settle).
        """
        try:
            if analyze:
                from database.sandbox_db import SandboxPositions
                from services.sandbox_service import get_user_id_from_apikey

                user_id = get_user_id_from_apikey(api_key)
                if not user_id:
                    return None
                row = SandboxPositions.query.filter_by(
                    user_id=user_id, symbol=symbol, exchange=exchange, product=product
                ).first()
                return int(row.quantity or 0) if row is not None else 0
            token, broker = self.broker_session(api_key)
            if token is None or broker != "upstox":
                return None
            import broker.upstox.api.order_api as upstox
            from broker.upstox.mapping.transform_data import map_product_type
            from sudarshan.books import exit_guard

            upstox._invalidate_position_cache(token)
            rows = upstox._position_rows(symbol, exchange, token)
            orders = upstox._read_order_rows(token, exit_guard.EXIT_ORDERS_READ_S)
            held = upstox._held_products(rows, symbol, exchange, orders)
            if held is None:
                return None
            found = held.get(map_product_type(product), 0)
            return None if found is None else int(found)
        except Exception:
            logger.exception("Automation: the position could not be read")
            return None

    def place(self, order: dict[str, Any], api_key: str, analyze: bool) -> tuple[bool, Any, int]:
        """One smart order, in the pipe the caller decided. Every claim, hold, sizing rule and the
        risk gate are the service's and stay there.

        The broker session is resolved here and handed over, which is the service's direct case:
        it never asks the order mode. Called with the API key alone, the service asks the order
        mode first, after the intake has decided, and in Semi-Auto queues the order for the
        Action Center with no mark of its pipe; an approval later sends it wherever the app's
        mode then points, so a Sandbox strategy's order could reach Upstox. The pipe is decided
        once, holding the strategy's lock, and nothing after that can move the order."""
        from services.place_smart_order_service import place_smart_order
        from sudarshan.books import add_sizing

        if analyze not in (True, False):
            raise ValueError("the intake's order has no pipe")
        token, broker = self.broker_session(api_key)
        if token is None or broker is None:
            return False, {"status": "error", "message": NO_SESSION}, 403
        # A7: an order of the intake's that only adds is sized from the fills, not from a
        # positions row that may not show the strategy's last fill yet. The mark is this call's
        # alone and no part of the order, so no event or log line carries it.
        with add_sizing.sized_from_fills():
            return place_smart_order(
                dict(order), api_key=api_key, auth_token=token, broker=broker, analyze=analyze
            )

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
        from . import telegram

        telegram.NOTICES.tell(strategy_id, reason, sentence)
