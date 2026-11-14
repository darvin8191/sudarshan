"""A desk for the automation's tests: a master, the app's switches and a smart order service that
keeps positions by target, all in memory. Nothing here reaches Upstox, NSE, TradingView or
Telegram.

The fake order service follows the real one's answers (services/place_smart_order_service.py and
services/sandbox_service.py): an order id when something was sent, "Positions Already Matched"
when the position is there, "No open position found" for an exit from flat, and a refusal or a
failure a test scripts.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))
TV = "52.89.214.238"
API_KEY = "k" * 64  # fake, test only: its shape is what the no-secret tests look for


def at(text: str) -> float:
    """``2026-11-16 10:15:02`` in India as epoch seconds."""
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST).timestamp()


def iso(epoch_s: float) -> str:
    return datetime.fromtimestamp(int(epoch_s), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = at("2026-11-16 10:15:02")
_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def option_symbols(options: dict[str, dict[str, list[float]]]) -> set[str]:
    """Every call and put of a strike list, as OpenAlgo writes the symbol."""
    out = set()
    for underlying, by_expiry in options.items():
        for day, strikes in by_expiry.items():
            code = f"{day[8:10]}{_MONTHS[int(day[5:7]) - 1]}{day[2:4]}"
            for strike in strikes:
                written = str(int(strike)) if strike == int(strike) else str(strike)
                out |= {f"{underlying}{code}{written}{side}" for side in ("CE", "PE")}
    return out


class FakeDesk:
    def __init__(self, now: float = NOW):
        self.clock = now
        self.listed = {
            ("NFO", "NIFTY24NOV26FUT"), ("NFO", "NIFTY29DEC26FUT"), ("NFO", "NIFTY25JAN27FUT"),
            ("NFO", "BANKNIFTY24NOV26FUT"), ("NFO", "BANKNIFTY29DEC26FUT"),
            ("NSE", "RELIANCE"), ("NSE", "M&M"), ("NSE_INDEX", "NIFTY"),
        }  # fmt: skip
        self.futures = {
            "NIFTY": ["2026-11-24", "2026-12-29", "2027-01-25"],
            "BANKNIFTY": ["2026-11-24", "2026-12-29"],
        }
        # NIFTY's weekly option expiries (Tuesdays; 24 Nov and 29 Dec are the monthly ones), every
        # strike from 24800 to 25200 in steps of 50, a call and a put each.
        self.options = {
            "NIFTY": {
                day: [24800.0 + 50 * n for n in range(9)]
                for day in ("2026-11-17", "2026-11-24", "2026-12-01", "2026-12-08", "2026-12-15", "2026-12-29")
            }
        }  # fmt: skip
        self.listed |= {("NFO", symbol) for symbol in option_symbols(self.options)}
        self.price: dict[str, float | None] = {"NIFTY": 25010.0}
        self.price_reads = 0
        self.lots = {"NIFTY": 65, "BANKNIFTY": 30}
        self.freeze = {"NIFTY": 1800, "BANKNIFTY": 600}
        self.loaded = True
        self.analyze_app = False
        self.guard = "sandbox"
        self.key: str | None = API_KEY
        self.semi = False
        self.kill_switch = False
        self.phrase = "STOP"
        self.phrases_asked: list[tuple[str, str]] = []
        self.positions: dict[tuple, int] = {}
        self.placed: list[dict[str, Any]] = []
        self.script: list[Any] = []  # answers to give next, before the book is asked
        self.delay = 0.0
        self.before_place = None  # called with the order, holding nothing
        self.emitted: list[dict[str, Any]] = []
        self.told: list[tuple[int, str, str]] = []
        self.slept = 0.0
        self.order_seq = 0
        self._lock = threading.Lock()

    # time
    def now(self) -> float:
        return self.clock

    def sleep(self, seconds: float) -> None:
        self.slept += seconds
        time.sleep(seconds)

    # master
    def master_loaded(self) -> bool:
        return self.loaded

    def known(self, exchange: str, symbol: str) -> bool:
        return (exchange, symbol) in self.listed

    def future_expiries(self, underlying: str) -> list[str]:
        return list(self.futures.get(underlying, []))

    def option_expiries(self, underlying: str) -> list[str]:
        return sorted(self.options.get(underlying, {}))

    def strikes(self, underlying: str, expiry: str) -> list[float]:
        return list(self.options.get(underlying, {}).get(expiry, []))

    def underlying_price(self, underlying: str, exchange: str) -> float | None:
        self.price_reads += 1
        return self.price.get(underlying)

    def lot_size(self, exchange: str, symbol: str) -> int | None:
        for underlying, lot in self.lots.items():
            if symbol.startswith(underlying):
                return lot
        return 1 if (exchange, symbol) in self.listed else None

    def indices(self) -> frozenset[str]:
        return frozenset({"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"})

    def closes(self, dates, exchange) -> dict[str, int]:
        return {}

    def freeze_qty(self, underlying: str, exchange: str) -> int:
        return self.freeze.get(underlying, 0)

    # switches
    def app_in_analyze(self) -> bool:
        return self.analyze_app

    def guard_stage(self) -> str:
        return self.guard

    def owner(self) -> str:
        return "owner"

    def api_key(self) -> str | None:
        return self.key

    def semi_auto(self, api_key: str) -> bool:
        return self.semi

    def kill_switch_on(self) -> bool:
        return self.kill_switch

    def phrase_ok(self, typed, asked: str, surface: str) -> bool:
        self.phrases_asked.append((asked, surface))
        return str(typed or "").strip().casefold() == self.phrase.casefold()

    def others(self):
        return [
            {"kind": "flow", "name": "A flow", "on": True, "detail": "webhook on", "path": "/flow"}
        ]

    # the sender
    def place(self, order: dict[str, Any], api_key: str, analyze: bool):
        assert api_key == self.key
        if self.before_place:
            self.before_place(order)
        if self.delay:
            time.sleep(self.delay)
        with self._lock:
            self.placed.append({**order, "analyze": analyze})
            if self.script:
                answer = self.script.pop(0)
                if isinstance(answer, BaseException):
                    raise answer
                return answer
            key = (analyze, order["exchange"], order["symbol"], order["product"])
            have, want = self.positions.get(key, 0), int(order["position_size"])
            if want == 0 and have == 0:
                said = "No open position found. Not placing exit order."
                return True, {"status": "success", "message": said}, 200
            if want == have:
                said = "Positions Already Matched. No Action needed."
                return True, {"status": "success", "message": said}, 200
            self.positions[key] = want
            self.order_seq += 1
            return True, {"status": "success", "orderid": f"2611160000{self.order_seq:02d}"}, 200

    def position(self, symbol: str, analyze: bool = True, product: str = "MIS") -> int:
        exchange = "NFO" if symbol.endswith(("FUT", "CE", "PE")) else "NSE"
        return self.positions.get((analyze, exchange, symbol, product), 0)

    # telling
    def emit(self, event: dict[str, Any]) -> None:
        self.emitted.append(event)

    def tell(self, strategy_id: int, reason: str, sentence: str) -> None:
        self.told.append((strategy_id, reason, sentence))


REFUSED_BUSY = (False, {"status": "error", "message": "Another exit for it is on its way."}, 409)
REFUSED_GATE = (False, {"status": "error", "message": "The kill switch is on."}, 403)
REFUSED_UNSIZED = (False, {"status": "error", "message": "The quantity held is unknown."}, 424)
NOT_CONFIRMED = (False, {"status": "error", "message": "This order is not confirmed."}, 500)


def strategy_alert(position: str, fired: float, *, before: str | None = None, order_id: str = "L",
                   size: int | None = None, ticker: str | None = None, **more) -> bytes:  # fmt: skip
    """A strategy's order-fill alert as TradingView fills it in."""
    size = (0 if position == "flat" else 1) if size is None else size
    data: dict[str, Any] = {
        "v": 1, "id": order_id, "position": position, "size": str(size),
        "interval": "1", "fired": iso(fired),
    }  # fmt: skip
    if before is not None:
        data.update(before=before, before_size="0" if before == "flat" else "1")
    if ticker:
        data["ticker"] = ticker
    data.update(more)
    return json.dumps(data).encode()


def indicator_alert(position: str, fired: float, **more) -> bytes:
    return json.dumps({"v": 1, "position": position, "fired": int(fired * 1000), **more}).encode()


# ---- the owner's routes: an app of its own, with CSRF and sessions as OpenAlgo has them ----------

OWNER = "owner"
OWNER_PASSWORD = "Fake-Pass-123!"  # fake, for the throwaway test database only


def make_app(*, rate_limits: bool = False):
    """The real Automation blueprint on a minimal app."""
    from flask import Flask
    from flask_wtf.csrf import CSRFProtect, generate_csrf

    import blueprints.sudarshan_automation as routes
    from database import auth_db, user_db
    from limiter import limiter

    app = Flask("sudarshan-automation-tests")
    app.secret_key = "test-only-secret-key-" + "5" * 32
    app.config.update(
        TESTING=True, WTF_CSRF_ENABLED=True, WTF_CSRF_TIME_LIMIT=None, RATELIMIT_ENABLED=rate_limits
    )
    app.csrf = CSRFProtect(app)
    limiter.init_app(app)
    if rate_limits:
        limiter.reset()
    app.register_blueprint(routes.sudarshan_automation_bp)

    @app.route("/csrf-token-for-tests")
    def csrf_token_for_tests():
        return {"csrf_token": generate_csrf()}

    @app.teardown_appcontext
    def remove_sessions(exception=None):
        auth_db.db_session.remove()
        user_db.db_session.remove()

    return app


def fresh_owner() -> None:
    """An empty users table with one owner, in the throwaway database."""
    from database import auth_db, user_db

    auth_db.Base.metadata.create_all(auth_db.engine)
    user_db.Base.metadata.create_all(user_db.engine)
    user_db.User.query.delete()
    user_db.db_session.commit()
    user_db.add_user(OWNER, "owner@example.invalid", OWNER_PASSWORD, is_admin=True)
    user_db.db_session.remove()


def login_session(client, username: str = OWNER) -> None:
    now = datetime.now(IST)
    with client.session_transaction() as session:
        session["user"] = username
        session["logged_in"] = True
        session["login_time"] = now.isoformat()


def csrf_headers(client) -> dict:
    token = client.get("/csrf-token-for-tests").get_json()["csrf_token"]
    return {"X-CSRFToken": token}
