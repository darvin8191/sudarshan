"""Generator of the PD2 golden vectors: valuation_ticks.json, a tab moved by ticks, frame by frame.

The phone prices a tab from the service's rows and the ticks that arrive after the service's read
(useTabLive / liveTab.ts). This writes a synthetic tick sequence and, for every frame, the tab as
the rules say it should stand: a tick moves a listed row only if it is a WebSocket tick, is newer
than the read, and its row is among the first ``cap`` held listed rows; its price is the feed's
float turned into paise with Python's round (half to even) and times 100; its previous close
replaces the row's only when it carries one. The sums come from the evaluator in
portfolio_vector_tools (value, Today, the tab), which works them out again with exact fractions.
Like the other generators it imports nothing from sudarshan. valuation_ticks.json is read by the
phone's tests and by test_portfolio_ticks.py, which runs the Python core on the same ticks.

Run: python3 make_pd2.py
"""

from pathlib import Path

from portfolio_vector_tools import U, epoch_ms, totals_of, val, write_all

READ_AT = epoch_ms(20261027, 10, 0, 0)
SEC = 1000


def line(qty, opened, cost, inn=0, out=0):
    return {
        "account_id": 1,
        "qty_u": qty,
        "open_qty_u": opened,
        "cost_paise": cost,
        "in_today_paise": inn,
        "out_today_paise": out,
        "over_year_qty_u": 0,
    }


def row(kind, exchange, symbol, mode, price_u, prev_u, *lines):
    return {
        "asset": {"kind": kind, "exchange": exchange, "symbol": symbol},
        "today_mode": mode,
        "price": {"price_u": price_u, "prev_close_u": prev_u},
        "lines": list(lines),
    }


def fixed(realised=6_000, money_in=2_100_000, money_out=42_000):
    return {
        "realised_paise": realised,
        "income_paise": 0,
        "fees_paise": 0,
        "money_in_paise": money_in,
        "money_out_paise": money_out,
        "closed_today_paise": 0,
        "closed_today_base_paise": 0,
    }


def feed_u(v):
    """The feed's float as price_u: paise(v) x 100, paise by Python's round (half to even)."""
    if isinstance(v, (int, float)) and 0 < v < float("inf"):
        count = round(v * 100)
        return count * 100 if count else None
    return None


def tick(key, ltp, at, prev=None, live=True):
    return {"key": key, "ltp": ltp, "prev_close": prev, "at": at, "live": live}


def live_rows(rows, cap):
    """The rows that move with ticks: listed ones held now, the first ``cap`` of them."""
    ids = [
        n
        for n, r in enumerate(rows)
        if r["asset"]["kind"] == "listed" and any(ln["qty_u"] for ln in r["lines"])
    ]
    return set(ids[:cap])


def priced(rows, newest, cap):
    """The rows as priced after the ticks so far, and which of them a tick moved."""
    allowed, out, moved = live_rows(rows, cap), [], []
    for n, r in enumerate(rows):
        r = {**r, "price": dict(r["price"])}
        t = newest.get(f"{r['asset']['exchange']}:{r['asset']['symbol']}")
        price = feed_u(t["ltp"]) if t else None
        ok = n in allowed and t and t["live"] and t["at"] > READ_AT and price is not None
        if ok:
            r["price"]["price_u"] = price
            prev = feed_u(t["prev_close"])
            if prev is not None:
                r["price"]["prev_close_u"] = prev
        out.append(r)
        moved.append(bool(ok))
    return out, moved


def replay(rid, title, why, rows, fx, cap, frames):
    newest, out = {}, []
    for note, ticks in frames:
        for t in ticks:
            if feed_u(t["ltp"]) is not None:  # a price that is no price leaves the last good one
                newest[t["key"]] = t
        shown, moved = priced(rows, newest, cap)
        out.append(
            {
                "note": note,
                "ticks": ticks,
                "expect": {
                    "totals": totals_of(shown, fx),
                    "price_u": [r["price"]["price_u"] for r in shown],
                    "prev_close_u": [r["price"]["prev_close_u"] for r in shown],
                    "moved": moved,
                },
            }
        )
    return {
        "id": rid,
        "title": title,
        "why": why,
        "read_at": READ_AT,
        "cap": cap,
        "rows": rows,
        "fixed": fx,
        "frames": out,
    }


ROWS = [
    # 10 at the open (close 1,400.00), 5 bought today for 7,100.00
    row(
        "listed",
        "NSE",
        "ALPHA",
        "ok",
        14_000_000,
        14_000_000,
        line(15 * U, 10 * U, 2_010_000, 710_000),
    ),
    # 10 at the open (close 100.00), 4 sold today for 420.00
    row(
        "listed", "NSE", "BRAVO", "ok", 1_000_000, 1_000_000, line(6 * U, 10 * U, 54_000, 0, 42_000)
    ),
    # held, but the service has no previous close for it yet
    row("listed", "BSE", "CHARLIE", "ok", 15_000_000, None, line(2 * U, 2 * U, 280_000)),
    # a cheap share, for the half-to-even rounding of the feed's float
    row("listed", "NSE", "DELTA", "ok", 100_000, 100_000, line(1000 * U, 1000 * U, 1_000_000)),
    # a fund: no ticks, a NAV that is not out yet
    row("mf", None, "ECHO", "pending", 456_789, 455_000, line(109_459_000, 109_459_000, 480_000)),
]

MORNING = replay(
    "T1",
    "A morning of ticks over five holdings",
    "Frame 0 is the service's own figures. The newest valid tick of a symbol stands: a price of nothing leaves the last good one. A tick moves a row only when it is a WebSocket tick "
    "newer than the read: one stamped before the read, and one the REST fallback brought, change "
    "nothing. A tick with no previous close moves the price but keeps the service's close (and "
    "a row with none stays 'not known yet'). 10.125 is exactly 1012.5 paise and Python rounds "
    "that to 1012 (half to even), not 1013: the phone has to do the same. The fund has no tick.",
    ROWS,
    fixed(),
    200,
    [
        ("before any tick", []),
        (
            "ALPHA ticks; BRAVO's tick is older than the read",
            [
                tick("NSE:ALPHA", 1428.25, READ_AT + 1 * SEC, 1400.0),
                tick("NSE:BRAVO", 104.0, READ_AT - 5 * SEC, 100.0),
            ],
        ),
        (
            "BRAVO arrives by the REST fallback, not a tick",
            [
                tick("NSE:BRAVO", 104.0, READ_AT + 2 * SEC, 100.0, live=False),
            ],
        ),
        (
            "CHARLIE ticks without a previous close",
            [
                tick("BSE:CHARLIE", 1510.5, READ_AT + 3 * SEC),
            ],
        ),
        (
            "CHARLIE's tick now carries one",
            [
                tick("BSE:CHARLIE", 1512.0, READ_AT + 4 * SEC, 1500.0),
            ],
        ),
        (
            "BRAVO, and a cheap share at a half paisa",
            [
                tick("NSE:BRAVO", 104.0, READ_AT + 5 * SEC, 100.0),
                tick("NSE:DELTA", 10.125, READ_AT + 5 * SEC, 10.0),
            ],
        ),
        (
            "a tick for a symbol the tab does not hold, and a bad price",
            [
                tick("NSE:ZULU", 5.0, READ_AT + 6 * SEC),
                tick("NSE:ALPHA", 0, READ_AT + 6 * SEC),
            ],
        ),
        (
            "ALPHA falls below the close",
            [
                tick("NSE:ALPHA", 1390.4, READ_AT + 7 * SEC, 1400.0),
            ],
        ),
    ],
)

CAPPED = replay(
    "T2",
    "Only the first rows move when there are more than the cap",
    "With a cap of 2 the first two held listed rows (ALPHA, BRAVO) are priced from ticks; CHARLIE "
    "and DELTA keep the service's price however their ticks go. The fund is never in the count.",
    ROWS,
    fixed(),
    2,
    [
        (
            "all four tick",
            [
                tick("NSE:ALPHA", 1428.25, READ_AT + SEC, 1400.0),
                tick("NSE:BRAVO", 104.0, READ_AT + SEC, 100.0),
                tick("BSE:CHARLIE", 1512.0, READ_AT + SEC, 1500.0),
                tick("NSE:DELTA", 10.125, READ_AT + SEC, 10.0),
            ],
        ),
    ],
)

SOLD_OUT = replay(
    "T3",
    "A holding sold out today stays in Today and does not take a tick",
    "BRAVO was sold out today (+50.00 on a base of 1,000.00 comes from the fixed sums), so it is "
    "not held and takes no tick; ALPHA does.",
    [
        ROWS[0],
        row("listed", "NSE", "BRAVO", "ok", None, 1_000_000, line(0, 10 * U, 0, 0, 1_050_000)),
    ],
    {**fixed(), "closed_today_paise": 50_000, "closed_today_base_paise": 1_000_000},
    200,
    [
        ("before any tick", []),
        (
            "both tick; only ALPHA moves",
            [
                tick("NSE:ALPHA", 1428.25, READ_AT + SEC, 1400.0),
                tick("NSE:BRAVO", 120.0, READ_AT + SEC, 100.0),
            ],
        ),
    ],
)

TICKS = {
    "about": (
        "Golden vectors for the phone's tab, moved by ticks frame by frame (PD2). make_pd2.py writes "
        "this file; the totals come from the evaluator in portfolio_vector_tools, which imports "
        "nothing from sudarshan. liveTab.ts reads it, and so does test_portfolio_ticks.py against "
        "core/valuation.py."
    ),
    "replays": [MORNING, CAPPED, SOLD_OUT],
}


def build() -> dict:
    """The file name and contents of every vector file this generator writes."""
    return {"valuation_ticks.json": TICKS}


def main() -> None:
    write_all(build(), Path(__file__).parent)


if __name__ == "__main__":
    main()
