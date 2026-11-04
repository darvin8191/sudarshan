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
