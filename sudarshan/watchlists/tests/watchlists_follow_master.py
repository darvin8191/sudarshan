"""A small master of the tests' own for the follow-row tests (W15): NIFTY (an index, options on
three expiries, futures), SBIN (cash, a future only) and SENSEX on the BSE side."""

from __future__ import annotations

from types import SimpleNamespace

EXPIRIES = ("24-NOV-26", "01-DEC-26", "29-DEC-26")
STRIKES = (25800.0, 25850.0, 25900.0, 25950.0, 26000.0)


def fo(symbol, u, exch, typ, expiry, strike=None):
    return SimpleNamespace(
        symbol=symbol, name=u, exchange=exch, token=f"{exch}|{symbol}", expiry=expiry,
        strike=strike, lotsize=75, tick_size=0.05, instrumenttype=typ, underlying=u,
    )  # fmt: skip


def cash(symbol, exch, name=None):
    return SimpleNamespace(
        symbol=symbol, name=name or symbol, exchange=exch, token=f"{exch}|{symbol}", expiry=None,
        strike=None, lotsize=1, tick_size=0.05, instrumenttype="EQ", underlying=None,
    )  # fmt: skip


class Snap:
    """A snapshot the master can hold a weak reference to (a namespace cannot be)."""

    def __init__(self, rows):
        self.by_symbol_exchange = {(r.symbol, r.exchange): r for r in rows}
        self.by_exchange = {"ALL": tuple(rows)}
        self.by_token = {r.token: r for r in rows}


def build_master(drop=()):
    """NIFTY (index, options on three expiries, futures), SBIN (cash, futures only), SENSEX on
    the BSE side. ``drop`` names option symbols the master lacks."""
    rows = [cash("NIFTY", "NSE_INDEX"), cash("SBIN", "NSE"), cash("SENSEX", "BSE_INDEX")]
    for iso in EXPIRIES:
        tag = iso.replace("-", "")
        rows.append(fo(f"NIFTY{tag}FUT", "NIFTY", "NFO", "FUT", iso))
        for k in STRIKES:
            for typ in ("CE", "PE"):
                sym = f"NIFTY{tag}{int(k)}{typ}"
                if sym not in drop:
                    rows.append(fo(sym, "NIFTY", "NFO", typ, iso, k))
    rows.append(fo("SBIN24NOV26FUT", "SBIN", "NFO", "FUT", "24-NOV-26"))
    rows.append(fo("SENSEX26NOV2680000CE", "SENSEX", "BFO", "CE", "26-NOV-26", 80000.0))
    return Snap(rows)
