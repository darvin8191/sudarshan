"""Fakes for the master contract and the feed: a temporary symtoken file and a loopback feed.

Nothing here is a real account, token or holding.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from sudarshan.portfolio.resolver import Listing, ResolverUnavailable

#: (symbol, exchange, instrument key, name, instrumenttype, tick size)
SHARES = [
    ("RELIANCE", "NSE", "NSE_EQ|INE002A01018", "Reliance Industries", "EQ", 0.1),
    ("TCS", "NSE", "NSE_EQ|INE467B01029", "Tata Consultancy Services", "EQ", 0.05),
    ("TCS", "BSE", "BSE_EQ|INE467B01029", "Tata Consultancy Services", "A", 0.05),
    ("INFY", "NSE", "NSE_EQ|INE009A01021", "Infosys", "EQ", 0.05),
    ("SMALLCO", "NSE", "NSE_EQ|INE000Z01011", "Small Company", "EQ", 0.05),
]


def make_symtoken(
    path, shares=SHARES, *, downloaded: str | None = "2026-10-26", futures=("RELIANCE",)
):
    """A copy of the columns of OpenAlgo's master contract that the resolver reads."""
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE symtoken (id INTEGER PRIMARY KEY, symbol TEXT, brsymbol TEXT, name TEXT,"
        " exchange TEXT, brexchange TEXT, token TEXT, instrumenttype TEXT, tick_size REAL);"
        "CREATE TABLE master_contract_status (id INTEGER PRIMARY KEY, broker TEXT,"
        " download_date TEXT, status TEXT);"
    )
    for symbol, exchange, key, name, kind, tick in shares:
        conn.execute(
            "INSERT INTO symtoken (symbol, brsymbol, name, exchange, brexchange, token,"
            " instrumenttype, tick_size) VALUES (?,?,?,?,?,?,?,?)",
            (symbol, symbol, name, exchange, exchange + "_EQ", key, kind, tick),
        )
    for symbol in futures:
        conn.execute(
            "INSERT INTO symtoken (symbol, brsymbol, name, exchange, brexchange, token,"
            " instrumenttype, tick_size) VALUES (?,?,?,?,?,?,?,?)",
            (
                f"{symbol}28OCT26FUT",
                "x",
                symbol,
                "NFO",
                "NSE_FO",
                f"NSE_FO|{symbol}",
                "FUTSTK",
                0.05,
            ),
        )
    if downloaded:
        conn.execute(
            "INSERT INTO master_contract_status (broker, download_date, status) VALUES"
            " ('upstox', ?, 'success')",
            (downloaded,),
        )
    conn.commit()
    conn.close()
    return path


class FakeResolver:
    """The resolver's questions answered from a dict, for tests that are not about the file."""

    def __init__(self, listings: dict | None = None, *, down: bool = False, futures=()):
        self.listings = listings if listings is not None else {
            ("TCS", "NSE"): Listing("TCS", "NSE", "NSE_EQ|INE467B01029",
                                    "Tata Consultancy Services", "INE467B01029", 500),
            ("RELIANCE", "NSE"): Listing("RELIANCE", "NSE", "NSE_EQ|INE002A01018",
                                         "Reliance Industries", "INE002A01018", 1000),
        }  # fmt: skip
        self.down, self.futures = down, set(futures)

    def find(self, symbol, exchange):
        if self.down:
            raise ResolverUnavailable
        return self.listings.get((symbol.strip().upper(), exchange))

    def by_key(self, key):
        return next((v for v in self.listings.values() if v.key == key), None)

    def by_keys(self, keys):
        wanted = set(keys)
        return {v.key: v for v in self.listings.values() if v.key in wanted}

    def has_derivatives(self, symbol):
        return symbol.upper() in self.futures

    def downloaded_today(self, today_iso):
        return True

    def close(self):
        pass


class FakeFeed:
    """sudarshan-feed's loopback API: /health, /snapshot, /claims and the governor path."""

    def __init__(self):
        self.ticks: dict[str, dict] = {}
        self.started_at = "2026-10-26T09:00:00"
        self.claims: dict[str, dict] = {}
        self.claim_status = 200
        self.governor = "off"  # "off" answers 503 with the reason; "on" answers quotes
        self.quotes: dict[str, dict] = {}
        #: instrument key -> [(YYYY-MM-DD, close)] the governor answers daily candles from
        self.candle_rows: dict[str, list] = {}
        self.times: list[float] = []
        self.calls: list[tuple[str, str, dict]] = []
        self.down = False
        self._server = None

    def _handler(feed):  # noqa: N805 - a closure over the fake, not a method
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body, headers=None):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def _do(self):
                url = urlsplit(self.path)
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
                feed.calls.append(
                    (self.command, url.path, query | {"_body": body} | {"_h": dict(self.headers)})
                )
                if feed.down:
                    self.connection.close()
                    return
                if url.path == "/health":
                    return self._send(200, {"state": "live", "started_at": feed.started_at})
                if url.path == "/snapshot":
                    keys = [k for k in query.get("keys", "").split(",") if k]
                    return self._send(200, {"ticks": {k: feed.ticks.get(k) for k in keys}})
                if url.path.startswith("/claims/"):
                    if feed.claim_status == 200:
                        feed.claims[url.path.rpartition("/")[2]] = body.get("keys", {})
                    return self._send(feed.claim_status, {"ok": feed.claim_status == 200})
                if url.path.startswith("/upstox/"):
                    if feed.governor == "off":
                        return self._send(
                            503, {"error": "switched off"}, {"X-Sudarshan-Reason": "off"}
                        )
                    if feed.governor == "bulk-hours":  # the governor's market hours (G4)
                        return self._send(
                            503, {"error": "market hours"}, {"X-Sudarshan-Reason": "bulk-hours"}
                        )
                    if "/historical-candle/" in url.path:
                        import time
                        from urllib.parse import unquote

                        feed.times.append(time.monotonic())
                        parts = url.path.split("/")  # .../historical-candle/KEY/days/1/TO/FROM
                        key, end, start = unquote(parts[-5]), parts[-2], parts[-1]
                        rows = [
                            [f"{d}T00:00:00+05:30", c, c, c, c, 0, 0]
                            for d, c in sorted(feed.candle_rows.get(key, []), reverse=True)
                            if start <= d <= end
                        ]
                        return self._send(200, {"status": "success", "data": {"candles": rows}})
                    keys = [k for k in query.get("instrument_key", "").split(",") if k]
                    return self._send(
                        200, {"data": {f"x:{k}": feed.quotes[k] for k in keys if k in feed.quotes}}
                    )
                self._send(404, {"error": "not found"})

            do_GET = do_PUT = do_DELETE = _do

        return Handler

    def start(self) -> str:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def paths(self, method: str, prefix: str) -> list[dict]:
        return [c[2] for c in self.calls if c[0] == method and c[1].startswith(prefix)]


#: Synthetic rows typed from the documented field lists; nothing here is an account.
HOLDINGS = {
    "status": "success",
    "data": [
        {
            "isin": "INE467B01029",
            "instrument_token": "NSE_EQ|INE467B01029",
            "trading_symbol": "TCS",
            "exchange": "NSE",
            "company_name": "Tata Consultancy Services",
            "quantity": 10,
            "t1_quantity": 2,
            "average_price": 3400.005,
            "last_price": 3500.55,
            "close_price": 3450.0,
            "pnl": 1005.495,
            "day_change": 50.55,
            "day_change_percentage": 1.46,
            "product": "D",
            "collateral_quantity": 0,
            "collateral_type": None,
            "haircut": 0.2,
            "cnc_used_quantity": 0,
        },
        {
            "isin": "INE000Z01011",
            "instrument_token": "NSE_EQ|INE000Z01011",
            "trading_symbol": "SMALLCO",
            "exchange": "NSE",
            "company_name": "Small Company",
            "quantity": 3,
            "t1_quantity": 0,
            "average_price": 100.0,
            "last_price": 90.0,
            "close_price": 0,
            "pnl": -30.0,
            "product": "D",
            "haircut": None,
        },
    ],
}
POSITIONS = {
    "status": "success",
    "data": [
        {
            "exchange": "NFO",
            "instrument_token": "NSE_FO|12345",
            "trading_symbol": "NIFTY26NOVFUT",
            "product": "D",
            "quantity": -65,
            "overnight_quantity": 0,
            "day_buy_quantity": 0,
            "day_sell_quantity": 65,
            "average_price": 24000.25,
            "buy_price": 0.0,
            "sell_price": 24000.25,
            "last_price": 23990.0,
            "close_price": 24010.0,
            "multiplier": 1.0,
            "pnl": 666.25,
            "realised": 0.0,
            "unrealised": 666.25,
            "value": -1559350.0,
        }
    ],
}
FUNDS = {
    "status": "success",
    "data": {
        "available_to_trade": {
            "total": 125000.5,
            "cash_available_to_trade": {
                "total": 100000.5,
                "cash": {
                    "opening_balance": 100000.5,
                    "added_today": 0,
                    "withdrawn_today": 0.0,
                    "amount_from_stock_sale": 0.0,
                    "unpaid_charges": 0,
                },
                "margin_used": {
                    "total": 0.0,
                    "mtf": 0.0,
                    "loss": {"total": 0.0, "realised": 0.0, "unrealised": 0.0},
                    "span_exposure": 0.0,
                    "cash_margin_var_elm": 0.0,
                    "premium_present": 0.0,
                    "delivery_margin": {"total": 0.0, "equity": 0.0, "fo_settlement": 0.0},
                },
            },
            "pledge_available_to_trade": {
                "total": 25000.0,
                "margin_from_pledge": {"total": 25000.0, "equity": 25000.0, "mutual_funds": 0.0},
                "margin_used": {"total": 0.0},
            },
        },
        "unavailable_to_trade": {
            "cash_unavailable_to_trade": {
                "unsettled_profit": {"todays_profit": 10.5, "previous_days": 0.0}
            },
            "pledge_unavailable_to_trade": {"equity": 0.0, "mutual_funds": 0.0},
        },
    },
}


#: Upstox's documented example rows for the reports (M7.6, M7.7), as the docs print them.
TRADES = {
    "status": "success",
    "data": [
        {
            "exchange": "NSE", "segment": "EQ", "option_type": None, "quantity": 10,
            "amount": 25874.50, "trade_id": "50001", "trade_date": "2026-10-27",
            "transaction_type": "BUY", "scrip_name": "RELIANCE INDUSTRIES", "strike_price": None,
            "expiry": None, "price": 2587.45, "isin": "INE002A01018", "symbol": "RELIANCE",
            "instrument_token": "NSE_EQ|INE002A01018",
        },
        {
            "exchange": "NFO", "segment": "FO", "option_type": "CE", "quantity": 65,
            "amount": 8125.00, "trade_id": "50002", "trade_date": "2026-10-28",
            "transaction_type": "SELL", "scrip_name": "NIFTY 25900 CE", "strike_price": 25900.0,
            "expiry": "2026-11-03", "price": 125.0, "isin": None, "symbol": "NIFTY26NOV25900CE",
            "instrument_token": None,
        },
    ],
    "metadata": {"page": {"page_number": 1, "page_size": 500, "total_records": 2, "total_pages": 1}},
}  # fmt: skip
PNL_META = {"status": "success", "data": {"trades_count": 1, "page_size_limit": 5000}}
PNL = {
    "status": "success",
    "data": [
        {
            "quantity": 100, "isin": "INE256A01028", "scrip_name": "ZEE ENTER", "trade_type": "EQ",
            "buy_date": "14-09-2026", "buy_average": 250.25, "sell_date": "15-09-2026",
            "sell_average": 262.75, "buy_amount": 25025.0, "sell_amount": 26275.0,
        }
    ],
    "metadata": {"page": {"page_number": 1, "page_size": 5000}},
}  # fmt: skip
CHARGES = {
    "status": "success",
    "data": {
        "charges_breakdown": {
            "total": 154.23, "brokerage": 97.23,
            "taxes": {"gst": 20.93, "stt": 15, "stamp_duty": 2},
            "charges": {"transaction": 0.56, "clearing": 0, "ipft": None, "others": 0,
                        "sebi_turnover": 0.01, "demat_transaction": 18.5},
        }
    },
}  # fmt: skip


# Typed from the field lists Upstox documents for the order book and the day's trades (never a
# captured answer); the ids, symbols and prices are made up.
ORDERS = {
    "status": "success",
    "data": [
        {
            "exchange": "NSE", "product": "D", "price": 3500.55, "quantity": 10,
            "status": "complete", "guid": None, "tag": "demo", "instrument_token": "NSE_EQ|INE467B01029",
            "placed_by": "ZZ9999", "trading_symbol": "TCS", "order_type": "LIMIT",
            "validity": "DAY", "trigger_price": 0.0, "disclosed_quantity": 0,
            "transaction_type": "BUY", "average_price": 3499.995, "filled_quantity": 10,
            "pending_quantity": 0, "status_message": None, "status_message_raw": None,
            "exchange_order_id": "1100000000001", "parent_order_id": None,
            "order_id": "261026000000001", "variety": "SIMPLE",
            "order_timestamp": "2026-10-26 09:16:02", "exchange_timestamp": "2026-10-26 09:16:03",
            "is_amo": False, "order_request_id": "1", "order_ref_id": "r1",
        },
        {
            "exchange": "NFO", "product": "I", "price": 0, "quantity": 65, "status": "trigger pending",
            "instrument_token": "NSE_FO|12345", "placed_by": "ZZ9999",
            "trading_symbol": "NIFTY26NOVFUT", "order_type": "SL-M", "validity": "DAY",
            "trigger_price": 24100.5, "disclosed_quantity": 0, "transaction_type": "SELL",
            "average_price": 0, "filled_quantity": 0, "pending_quantity": 65,
            "status_message": "Trigger pending", "exchange_order_id": None,
            "parent_order_id": None, "order_id": "261026000000002", "variety": "SIMPLE",
            "order_timestamp": "2026-10-26 10:01:00", "exchange_timestamp": None, "is_amo": False,
        },
    ],
}  # fmt: skip
DAY_TRADES = {
    "status": "success",
    "data": [
        {
            "exchange": "NSE", "product": "D", "trading_symbol": "TCS",
            "instrument_token": "NSE_EQ|INE467B01029", "order_type": "LIMIT",
            "transaction_type": "BUY", "quantity": 4, "exchange_order_id": "1100000000001",
            "order_id": "261026000000001", "exchange_timestamp": "2026-10-26 09:16:03",
            "average_price": 3499.995, "trade_id": "T-1", "order_ref_id": "r1",
            "order_timestamp": "2026-10-26 09:16:02",
        },
        {
            "exchange": "NSE", "product": "D", "trading_symbol": "TCS",
            "instrument_token": "NSE_EQ|INE467B01029", "order_type": "LIMIT",
            "transaction_type": "BUY", "quantity": 6, "exchange_order_id": "1100000000001",
            "order_id": "261026000000001", "exchange_timestamp": "2026-10-26 09:16:04",
            "average_price": 3500.0, "trade_id": "T-2", "order_ref_id": "r1",
            "order_timestamp": "2026-10-26 09:16:02",
        },
    ],
}  # fmt: skip


ORDER_HISTORY = {
    "status": "success",
    "data": [
        {**ORDERS["data"][1], "status": "put order req received"},
        ORDERS["data"][1],
    ],
}


class FakeUpstox:
    """Upstox's read paths on loopback. ``status`` and ``bodies`` are set per part; a report
    path may hold a list of bodies, one per page asked for (``page_number``)."""

    PATHS = {
        "/v2/portfolio/long-term-holdings": "holdings",
        "/v2/portfolio/short-term-positions": "positions",
        "/v3/portfolio/mtf-positions": "mtf",
        "/v3/user/get-funds-and-margin": "funds",
        "/v2/order/retrieve-all": "day_orders",
        "/v2/order/trades/get-trades-for-day": "day_trades",
        "/v2/order/history": "order_history",
        "/v2/charges/historical-trades": "trades",
        "/v2/trade/profit-loss/metadata": "pnl_meta",
        "/v2/trade/profit-loss/data": "pnl",
        "/v2/trade/profit-loss/charges": "charges",
    }

    def __init__(self):
        self.status = {}
        self.bodies = {"holdings": HOLDINGS, "positions": POSITIONS, "mtf": {"status": "success", "data": []},
                       "funds": FUNDS, "trades": TRADES, "day_orders": ORDERS, "day_trades": DAY_TRADES,
                       "order_history": ORDER_HISTORY, "pnl_meta": PNL_META, "pnl": PNL,
                       "charges": CHARGES}  # fmt: skip
        self.raw: dict[str, bytes] = {}
        self.delay = 0.0
        self.requests: list[tuple[str, dict]] = []
        self.queries: list[tuple[str, dict]] = []  # (part, query parameters) of every request
        self._server = None
        self.url = ""

    def count(self, part: str | None = None) -> int:
        return sum(1 for p, _ in self.requests if part in (None, p))

    def _body_for(self, part: str, query: dict) -> bytes:
        body = self.bodies[part]
        if isinstance(body, list):  # one body per page
            page = int(query.get("page_number", "1"))
            body = body[min(page, len(body)) - 1]
        return json.dumps(body).encode()

    def start(self) -> str:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                import time

                split = urlsplit(self.path)
                part = fake.PATHS.get(split.path)
                query = {k: v[-1] for k, v in parse_qs(split.query).items()}
                fake.requests.append((part, dict(self.headers)))
                fake.queries.append((part, query))
                time.sleep(fake.delay)
                if part is None:
                    body, status = b"{}", 404
                else:
                    status = fake.status.get(part, 200)
                    body = fake.raw.get(part) or fake._body_for(part, query)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        return self.url

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


#: A synthetic NAV file in the layout of AMFI's NAVAll.txt as the design describes it.
NAVALL = """Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date

Open Ended Schemes(Equity Scheme - Large Cap Fund)

Alpha Mutual Fund

100001;INF000A01011;-;Alpha Bluechip Fund - Direct Plan - Growth;123.45675;04-Nov-2026
100002;INF000A01029;INF000A01037;Alpha Bluechip Fund - Regular Plan - IDCW;N.A.;04-Nov-2026
100003;-;-;Alpha Value Fund - Direct Plan - Growth;50.1;04-Nov-2026

Open Ended Schemes(Debt Scheme - Liquid Fund)

Beta Mutual Fund

200001;INF000B01010;-;Beta Liquid Fund - Direct Plan - Growth;1234.5;04-Nov-2026
200003;INF000B01036;-;Beta Comma Fund;1,234.5;04-Nov-2026
200002;INF000B01028;-;Beta Overnight Fund - Direct Plan - Growth;1100.2500;04-Nov-2026
this line is not a scheme; and neither is the next
300001;INF000C01010;-;Broken Date Fund;10.0;not a date
"""


class FakeAmfi:
    """AMFI's file on loopback: the body, a 304 for a repeated ask, and the asks it saw."""

    def __init__(self, body: str = NAVALL):
        self.body, self.status, self.modified = body, 200, True
        self.asks: list[dict] = []
        self._server = None
        self.url = ""

    def start(self) -> str:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                fake.asks.append(dict(self.headers))
                if self.headers.get("If-Modified-Since") and not fake.modified:
                    self.send_response(304)
                    self.end_headers()
                    return
                data = fake.body.encode()
                self.send_response(fake.status)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/NAVAll.txt"
        return self.url

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
