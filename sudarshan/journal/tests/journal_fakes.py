"""Fakes on 127.0.0.1 for the journal's service tests (the feed tests' pattern): the price feed's
HTTP API, and helpers to seal a request as each caller does. Every value is synthetic."""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from sudarshan.autologin.handoff import seal
from sudarshan.vault import (
    JOURNAL_API_INFO,
    JOURNAL_DELIVERY_INFO,
    JOURNAL_FAMILY_INFO,
    JOURNAL_NUDGE_INFO,
    PORTFOLIO_JOURNAL_API_INFO,
    fernet_for,
)

PEPPER = "p" * 48  # fake, test only
KEYS = {
    "api": fernet_for(PEPPER, JOURNAL_API_INFO),
    "delivery": fernet_for(PEPPER, JOURNAL_DELIVERY_INFO),
    "nudge": fernet_for(PEPPER, JOURNAL_NUDGE_INFO),
    "family": fernet_for(PEPPER, JOURNAL_FAMILY_INFO),
}
PORTFOLIO_KEY = fernet_for(PEPPER, PORTFOLIO_JOURNAL_API_INFO)
PRINCIPALS = {
    "api": "owner",
    "delivery": "sudarshan-alerts",
    "nudge": "sudarshan-alerts",
    "family": "sudarshan-family",
}


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def sealed(op: str, purpose: str = "api", *, now: float | None = None, principal=None,
           **args) -> bytes:  # fmt: skip
    """A request as its caller seals it: the bridge and the desk put the arguments under
    ``args``; the alerts' nudge writes its three fields into the seal itself."""
    who = PRINCIPALS[purpose] if principal is None else principal
    if purpose == "nudge":
        return seal(KEYS["nudge"], op, now=now, principal=who, **args)
    return seal(KEYS[purpose], op, now=now, principal=who, args=args)


class _Server:
    """A ThreadingHTTPServer on a free loopback port, in a daemon thread; ``stop`` closes it."""

    def __init__(self):
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def handler(self) -> type[BaseHTTPRequestHandler]:  # pragma: no cover - overridden
        raise NotImplementedError

    def start(self) -> str:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


class FakeFeed(_Server):
    """sudarshan-feed's loopback API as the journal uses it: /health and /claims/<client>."""

    def __init__(self):
        super().__init__()
        self.started_at = "2026-11-09T09:00:00"
        self.claims: dict[str, dict] = {}
        self.claim_status = 200
        self.calls: list[tuple[str, str, dict]] = []
        self.down = False

    def handler(feed):  # noqa: N805 - a closure over the fake, not a method
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
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
                    return self._send(
                        200,
                        {"state": "live", "started_at": feed.started_at,
                         "subscribed": {"total": len(feed.claims)}},
                    )  # fmt: skip
                if url.path.startswith("/claims/"):
                    client = url.path.rpartition("/")[2]
                    if self.command == "DELETE":
                        feed.claims.pop(client, None)
                        return self._send(200, {"ok": True})
                    if feed.claim_status == 200:
                        feed.claims[client] = body.get("keys", {})
                    return self._send(feed.claim_status, {"ok": feed.claim_status == 200})
                return self._send(404, {"error": "not found"})

            do_GET = do_PUT = do_DELETE = do_POST = _do

        return Handler


class FakePortfolio(_Server):
    """sudarshan-portfolio's sealed API as the journal uses it: ``POST /v1/<op>`` under the
    journal purpose. ``answers[op]`` is the ``data`` of the envelope (or ``(status, body)``, or a
    function of the request's args that returns either);
    every call is recorded as (op, args, principal)."""

    def __init__(self, key=None):
        super().__init__()
        self.key = PORTFOLIO_KEY if key is None else key
        self.answers: dict[str, object] = {}
        self.calls: list[tuple[str, dict, str]] = []
        self.down = False

    def handler(fake):  # noqa: N805 - a closure over the fake, not a method
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):  # noqa: N802 - the stdlib's name
                length = int(self.headers.get("Content-Length") or 0)
                blob = self.rfile.read(length)
                if fake.down:
                    self.connection.close()
                    return
                op = self.path.rpartition("/")[2]
                try:
                    message = json.loads(fake.key.decrypt(blob, ttl=60))
                except Exception:  # noqa: BLE001 - a seal this key does not open
                    return self._send(403, {"status": "error", "code": "forbidden"})
                if message.get("op") != op:
                    return self._send(403, {"status": "error", "code": "forbidden"})
                fake.calls.append((op, message.get("args") or {}, message.get("principal")))
                answer = fake.answers.get(op)
                if callable(answer):  # an answer that depends on what was asked
                    answer = answer(message.get("args") or {})
                if isinstance(answer, tuple):
                    return self._send(*answer)
                if answer is None:
                    return self._send(501, {"status": "error", "code": "not_ready"})
                return self._send(200, {"status": "success", "data": answer})

        return Handler


# ---- OpenAlgo's databases, as files a test writes (synthetic rows only) ---------------------------
def make_openalgo_db(path, *, symtoken=(), order_logs=(), analyzer_logs=()):
    """openalgo.db with the three tables the journal reads. ``symtoken`` rows are dicts with
    token, symbol, exchange, expiry, lotsize, instrumenttype (and name); log rows have api_type,
    request_data and response_data (dicts) and created_at (text)."""
    import sqlite3

    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS symtoken (id INTEGER PRIMARY KEY, symbol TEXT, "
            "brsymbol TEXT, name TEXT, exchange TEXT, brexchange TEXT, token TEXT, expiry TEXT, "
            "strike REAL, lotsize INTEGER, instrumenttype TEXT, tick_size REAL)"
        )
        for table in ("order_logs", "analyzer_logs"):
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY, api_type TEXT, "
                "request_data TEXT, response_data TEXT, created_at TEXT)"
            )
        for row in symtoken:
            conn.execute(
                "INSERT INTO symtoken (symbol, brsymbol, name, exchange, token, expiry, lotsize, "
                "instrumenttype) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (row["symbol"], row["symbol"], row.get("name", ""), row["exchange"],
                 row["token"], row.get("expiry", ""), row.get("lotsize", 1),
                 row.get("instrumenttype", "")),
            )  # fmt: skip
        for table, rows in (("order_logs", order_logs), ("analyzer_logs", analyzer_logs)):
            for row in rows:
                conn.execute(
                    f"INSERT INTO {table} (api_type, request_data, response_data, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (row["api_type"], json.dumps(row["request_data"]),
                     json.dumps(row["response_data"]), row["created_at"]),
                )  # fmt: skip
    return path


def make_latency_db(path, rows=()):
    """latency.db's order_latency with (order_id, rtt_ms, total_latency_ms) rows."""
    import sqlite3

    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS order_latency (id INTEGER PRIMARY KEY, order_id TEXT, "
            "rtt_ms REAL, total_latency_ms REAL, request_body TEXT, response_body TEXT)"
        )
        conn.executemany(
            "INSERT INTO order_latency (order_id, rtt_ms, total_latency_ms) VALUES (?, ?, ?)", rows
        )
    return path


def make_sandbox_db(path, trades=()):
    """sandbox.db's sandbox_trades; each trade a dict with the table's columns."""
    import sqlite3

    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sandbox_trades (id INTEGER PRIMARY KEY, tradeid TEXT, "
            "orderid TEXT, user_id TEXT, symbol TEXT, exchange TEXT, action TEXT, quantity INTEGER, "
            "price NUMERIC, product TEXT, strategy TEXT, trade_timestamp TEXT)"
        )
        for t in trades:
            conn.execute(
                "INSERT INTO sandbox_trades (tradeid, orderid, user_id, symbol, exchange, action, "
                "quantity, price, product, strategy, trade_timestamp) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (t["tradeid"], t["orderid"], "owner", t["symbol"], t["exchange"], t["action"],
                 t["quantity"], t["price"], t["product"], t.get("strategy"), t["trade_timestamp"]),
            )  # fmt: skip
    return path


def make_risk_db(path, *, kill_switch=False, since=None, locks=()):
    """db/sudarshan_risk.db's two tables the journal reads: risk_state row 1 and daily_locks
    (pipe, ist_date, at) rows; times are naive UTC text, as the risk store writes them."""
    import sqlite3

    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS risk_state (id INTEGER PRIMARY KEY, ks_on BOOLEAN NOT NULL "
            "DEFAULT 0, ks_since DATETIME)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS daily_locks (pipe TEXT, ist_date TEXT, at DATETIME, "
            "figure REAL, loss_limit REAL, PRIMARY KEY (pipe, ist_date))"
        )
        conn.execute(
            "INSERT OR REPLACE INTO risk_state (id, ks_on, ks_since) VALUES (1, ?, ?)",
            (1 if kill_switch else 0, since),
        )
        conn.executemany(
            "INSERT INTO daily_locks (pipe, ist_date, at, figure, loss_limit) VALUES (?,?,?,?,?)",
            [(p, d, a, -1000.0, 1000.0) for p, d, a in locks],
        )
    return path


def trade_row(trade_id, order_id, *, key="NSE_FO|50001", symbol="NIFTY 25000 CE 25 NOV 26",
              product="I", side="BUY", qty=65, price_u=1_000_000, at=None, placed=None):  # fmt: skip
    """One of the portfolio's ``trades_day`` rows (its parsed shape: qty_u x 10^6, price_u)."""
    return {
        "trade_id": trade_id, "order_id": order_id, "exchange_order_id": None,
        "instrument_key": key, "exchange": "NFO", "symbol": symbol, "product": product,
        "order_type": "MARKET", "transaction_type": side, "qty_u": qty * 1_000_000,
        "price_u": price_u, "amount_paise": qty * price_u // 100, "placed_at": placed,
        "exchange_at": at,
    }  # fmt: skip
