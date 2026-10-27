"""The real close routes, services and Upstox module over a simulated Upstox (M6.9).

Adopted from the independent review of 16b0997..ec0c6bd (its review_world.py), with an order book
added, because Exit all now reads one. Only the HTTP layer and the symbol master are replaced. The
simulated Upstox fills every market order at once, but its position book shows a fill only ``lag``
seconds after it happened (the behaviour the guard is built around). Everything between the
phone's POST and that HTTP layer is the code under test: blueprints/orders.py,
services/close_position_service.py, services/place_smart_order_service.py,
sudarshan/books/single_exit.py, sudarshan/books/exit_guard.py and broker/upstox/api/order_api.py.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx

IST = timezone(timedelta(hours=5, minutes=30), "IST")  # Upstox stamps its orders in India's time

INSTRUMENTS = {
    # openalgo symbol: (instrument_token, exchange, upstox product, openalgo product)
    "NIFTY27OCT2625900CE": ("NSE_FO|1001", "NFO", "I", "MIS"),
    "BANKNIFTY27OCT2657500PE": ("NSE_FO|1002", "NFO", "D", "NRML"),
    "SBIN": ("NSE_EQ|INE062A01020", "NSE", "I", "MIS"),
}
BY_TOKEN = {token: symbol for symbol, (token, *_rest) in INSTRUMENTS.items()}
POSITIONS_URL = "/v2/portfolio/short-term-positions"
ORDERS_URL = "/v2/order/retrieve-all"


class FakeResponse:
    def __init__(self, status: int, body: dict):
        self.status_code = status
        self.status = status
        self._body = body
        self.text = json.dumps(body)
        self.headers: dict = {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error", request=httpx.Request("GET", "https://x"), response=self
            )


class Broker:
    """A simulated Upstox account: instant fills, a position book that lags them."""

    def __init__(self, held: dict[str, int], lag: float):
        self.lag = lag
        self.lock = threading.Lock()
        self.base = dict(held)  # symbol -> signed quantity at the start
        self.fills: list[tuple[float, str, int]] = []
        self.orders: list[dict] = []
        self.reads = 0
        self.order_book_reads = 0
        self.unreachable: set[str] = set()  # symbols whose order never reaches Upstox
        self.rejected_later: set[str] = set()  # accepted with an id, then rejected: no fill
        self.hang: dict[str, float] = {}  # symbol -> seconds the place call takes
        self.part: dict[str, int] = {}  # symbol -> quantity that fills; the rest stays working
        self.working: list[dict] = []  # orders with quantity still working
        self.read_flip = False  # alternate fresh and lagging reads (two replicas)
        self.fresh_parity = 1  # which reads are fresh while read_flip is on
        self.orders_unreadable = False  # the order book read fails
        self.slow_read = 0.0  # seconds a positions read takes to answer, its rows as at its start
        self.read_delay = None  # or callable(read number from 1) -> seconds, as slow_read
        self.order_book_delay = 0.0  # seconds the order book takes; past the read's limit, cut off

    # -- state ---------------------------------------------------------------
    def true_qty(self, symbol: str) -> int:
        with self.lock:
            return self.base.get(symbol, 0) + sum(d for _, s, d in self.fills if s == symbol)

    def shown_qty(self, symbol: str, now: float, fresh: bool = False) -> int:
        with self.lock:
            cutoff = now if fresh else now - self.lag
            return self.base.get(symbol, 0) + sum(
                d for t, s, d in self.fills if s == symbol and t <= cutoff
            )

    def rest(self, symbol: str, side: str, qty: int) -> None:
        """An order placed elsewhere (a stop loss in Upstox's own app, say), still working."""
        with self.lock:
            self.working.append({"symbol": symbol, "side": side, "qty": qty, "at": time.time()})

    def fill_working(self) -> None:
        """Every working remainder fills now (the market came back)."""
        now = time.monotonic()
        with self.lock:
            for order in self.working:
                delta = order["qty"] if order["side"] == "BUY" else -order["qty"]
                self.fills.append((now, order["symbol"], delta))
            self.working.clear()

    def history(self, symbol: str) -> list[int]:
        """The true quantity after each fill, in order."""
        with self.lock:
            qty = self.base.get(symbol, 0)
            out = [qty]
            for _, s, d in sorted(self.fills):
                if s == symbol:
                    qty += d
                    out.append(qty)
            return out

    # -- HTTP ----------------------------------------------------------------
    def get(self, url, headers=None, **kwargs):
        if url.endswith(ORDERS_URL):
            return self._order_book(kwargs.get("timeout"))
        assert url.endswith(POSITIONS_URL), url
        now = time.monotonic()
        with self.lock:
            self.reads += 1
            number = self.reads
            fresh = self.read_flip and self.reads % 2 == self.fresh_parity
        rows = []
        for symbol, (token, exchange, product, _oa) in INSTRUMENTS.items():
            if symbol not in self.base:
                continue
            rows.append(
                {
                    "instrument_token": token,
                    "exchange": exchange,
                    "product": product,
                    "tradingsymbol": symbol,
                    "quantity": self.shown_qty(symbol, now, fresh=fresh),
                    "average_price": 100.0,
                    "last_price": 101.0,
                    "pnl": 0.0,
                    "buy_price": 100.0,
                    "sell_price": 0.0,
                }
            )
        delay = self.read_delay(number) if self.read_delay is not None else self.slow_read
        if delay:
            time.sleep(delay)
        return FakeResponse(200, {"status": "success", "data": rows})

    def _order_book(self, timeout=None):
        """The order book; one slower than the read's own limit is cut off there, as httpx does."""
        delay = self.order_book_delay
        if delay and timeout is not None and delay > timeout:
            time.sleep(timeout)
            raise httpx.ReadTimeout("The read operation timed out")
        if delay:
            time.sleep(delay)
        with self.lock:
            self.order_book_reads += 1
            if self.orders_unreadable:
                return FakeResponse(503, {"status": "error", "errors": [{"message": "down"}]})
            rows = []
            for n, order in enumerate(self.working):
                token, exchange, product, _oa = INSTRUMENTS[order["symbol"]]
                placed = datetime.fromtimestamp(order.get("at", time.time()), IST)
                rows.append(
                    {
                        "order_id": f"W{n}",
                        "instrument_token": token,
                        "exchange": exchange,
                        "product": product,
                        "tradingsymbol": order["symbol"],
                        "transaction_type": order["side"],
                        "status": "open",
                        "pending_quantity": order["qty"],
                        "order_timestamp": placed.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
        return FakeResponse(200, {"status": "success", "data": rows})

    def post(self, url, headers=None, content=None, **kwargs):
        assert url.endswith("/v3/order/place"), url
        payload = json.loads(content)
        symbol = BY_TOKEN[payload["instrument_token"]]
        side = payload["transaction_type"]
        qty = int(payload["quantity"])
        if symbol in self.hang:
            time.sleep(self.hang[symbol])
        if symbol in self.unreachable:
            raise httpx.ConnectTimeout("connect timed out")
        now = time.monotonic()
        with self.lock:
            oid = f"2610270000{len(self.orders) + 1:02d}"
            self.orders.append({"at": now, "symbol": symbol, "side": side, "qty": qty, "id": oid})
            if symbol not in self.rejected_later:
                filled = self.part.get(symbol, qty)
                delta = filled if side == "BUY" else -filled
                self.fills.append((now, symbol, delta))
                if filled < qty:
                    self.working.append(
                        {"symbol": symbol, "side": side, "qty": qty - filled, "at": time.time()}
                    )
        return FakeResponse(200, {"status": "success", "data": {"order_ids": [oid]}})


def install(monkeypatch, broker: Broker):
    """Point the real Upstox module and the services at the simulated Upstox."""
    import blueprints.orders as orders
    import broker.upstox.api.order_api as upstox
    import broker.upstox.mapping.order_data as upstox_mapping
    import database.settings_db as settings_db
    import services.close_position_service as close_service
    import services.place_smart_order_service as smart_service
    import utils.session as session_utils
    from database.latency_db import init_latency_db
    from sudarshan.books import exit_guard

    init_latency_db()
    monkeypatch.setenv("BROKER_API_KEY", "fake")
    monkeypatch.setattr(upstox, "get_httpx_client", lambda: broker)
    # The order book is read from the simulated Upstox, as the real get_order_book does (the
    # conftest gives every other test an order book with nothing working).
    monkeypatch.setattr(
        upstox, "get_order_book", lambda auth: upstox.get_api_response(ORDERS_URL, auth)
    )
    monkeypatch.setattr(upstox, "get_token", lambda symbol, exchange: INSTRUMENTS[symbol][0])
    monkeypatch.setattr(upstox, "get_br_symbol", lambda symbol, exchange: symbol)
    monkeypatch.setattr(upstox, "get_symbol", lambda token, exchange: BY_TOKEN.get(token))
    monkeypatch.setattr(upstox_mapping, "get_symbol", lambda token, exchange: BY_TOKEN.get(token))
    upstox._POSITION_BOOK.invalidate("token")

    monkeypatch.setattr(session_utils, "is_session_valid", lambda: True)
    monkeypatch.setattr(orders, "get_auth_token", lambda user: "token")
    monkeypatch.setattr(orders, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(settings_db, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(orders, "get_api_key_for_tradingview", lambda user: None)
    monkeypatch.setattr(close_service, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(smart_service, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(exit_guard, "EXITS", exit_guard.ExitsInFlight())
    return World(broker)


class World:
    def __init__(self, broker: Broker):
        self.broker = broker
        self.answers: list[tuple[str, int, dict, float, float]] = []
        self._lock = threading.Lock()

    def _client(self):
        from flask import Flask

        import blueprints.orders as orders

        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(orders.orders_bp)
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user"] = "owner"
            sess["broker"] = "upstox"
        return client

    def _record(self, what, code, body, started):
        with self._lock:
            self.answers.append((what, code, body, started, time.monotonic()))
        return code, body

    def phone_exit(self, symbol: str):
        _token, exchange, _p, product = INSTRUMENTS[symbol]
        started = time.monotonic()
        reply = self._client().post(
            "/close_position", json={"symbol": symbol, "exchange": exchange, "product": product}
        )
        return self._record(f"phone exit {symbol}", reply.status_code, reply.get_json(), started)

    def app_exit_all(self):
        started = time.monotonic()
        reply = self._client().post("/close_all_positions", json={})
        return self._record("app exit all", reply.status_code, reply.get_json(), started)

    def api_exit_all(self):
        from services.close_position_service import close_position

        started = time.monotonic()
        _ok, body, code = close_position(
            position_data={"strategy": "TV"}, auth_token="token", broker="upstox"
        )
        return self._record("api exit all", code, body, started)

    def flow_close(self, symbol: str):
        """Flow's "Close Positions" node naming one position, as its client sends it."""
        from services.close_position_service import close_position

        _token, exchange, _p, product = INSTRUMENTS[symbol]
        started = time.monotonic()
        _ok, body, code = close_position(
            position_data={
                "strategy": "flow_workflow",
                "symbol": symbol,
                "exchange": exchange,
                "product": product,
            },
            auth_token="token",
            broker="upstox",
        )
        return self._record(f"flow close {symbol}", code, body, started)

    def tv_smart_exit(self, symbol: str, quantity: str = "0", action: str = "SELL"):
        from services.place_smart_order_service import place_smart_order

        _token, exchange, _p, product = INSTRUMENTS[symbol]
        started = time.monotonic()
        _ok, body, code = place_smart_order(
            {
                "apikey": "KEY",
                "strategy": "TV",
                "symbol": symbol,
                "exchange": exchange,
                "action": action,
                "quantity": quantity,
                "position_size": "0",
                "product": product,
                "pricetype": "MARKET",
            },
            auth_token="token",
            broker="upstox",
        )
        return self._record(f"tv smart exit {symbol}", code, body, started)

    def background(self, fn, *args):
        out: dict = {}

        def run():
            out["result"] = fn(*args)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread, out
