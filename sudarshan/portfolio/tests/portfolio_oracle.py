"""A second, deliberately naive implementation of the ledger rules, for the tests to check against.

The engine (core/ledger.py) is an optimised replay over typed entries. This is the same rules as
a reader of the design would write them first: plain dicts in, plain dicts out, exact fractions
for every division, lots kept as short lists. It shares no code with the engine (it imports
nothing from sudarshan), so a test that runs both on the same entries compares two readings of
the design rather than a function with itself.

A row is the contract's ActivityInput as a dict (``id``, ``seq``, ``kind``, ``trade_date``,
``account_id``, ``asset_id``, ``qty_u``, ``amount_paise``, ``price_u``, ``charges_paise`` ...).
"""

from __future__ import annotations

from fractions import Fraction


def half_away(x: Fraction) -> int:
    """The whole number nearest ``x``, a tie going away from zero."""
    whole = int(abs(x) + Fraction(1, 2))  # abs(x) + 1/2 is not negative: int() is floor here
    return whole if x >= 0 else -whole


def amount_of(row: dict) -> int | None:
    if row.get("amount_paise") is not None:
        return row["amount_paise"]
    if row.get("price_u") is not None and row.get("qty_u") is not None:
        return half_away(Fraction(row["qty_u"] * row["price_u"], 10**8))
    return None


#: Where in the day each kind goes: purchases, then sales, then income and fees.
RANK = {"BUY": 1, "RIGHTS": 1, "SELL": 3, "DIVIDEND": 4, "INTEREST": 4, "FEE": 4}


def simulate(rows: list[dict], as_of: int | None = None) -> dict:
    """Replay ``rows``; returns ``{"positions", "moves", "totals"}`` or ``{"refused": {...}}``."""
    live = [
        r
        for r in rows
        if r.get("status", "posted") == "posted" and (as_of is None or r["trade_date"] <= as_of)
    ]
    live.sort(key=lambda r: (r["trade_date"], RANK[r["kind"]], r["seq"], r.get("id") or 0))
    book: dict = {}
    moves = []
    made = 0
    for r in live:
        pos = book.setdefault(
            (r["account_id"], r.get("asset_id")),
            {"lots": [], "realised": 0, "income": 0, "fees": 0},
        )
        amount = amount_of(r)
        if r["kind"] in ("BUY", "RIGHTS"):
            made += 1
            cost = amount + r.get("charges_paise", 0)
            pos["lots"].append([r["trade_date"], made, r["qty_u"], cost])
            pos["lots"].sort(key=lambda lot: (lot[0], lot[1]))
            moves.append((r["id"], r["qty_u"], cost, 0, amount, []))
        elif r["kind"] == "SELL":
            held = sum(lot[2] for lot in pos["lots"])
            if held < r["qty_u"]:
                return {"refused": {"entry": r["id"], "have": held, "want": r["qty_u"]}}
            need, used = r["qty_u"], []
            while need:
                lot = pos["lots"][0]
                take = min(need, lot[2])
                cost = lot[3] if take == lot[2] else half_away(Fraction(lot[3] * take, lot[2]))
                used.append([lot[0], take, cost])
                lot[2] -= take
                lot[3] -= cost
                need -= take
                if lot[2] == 0:
                    pos["lots"].pop(0)
            cost = sum(piece[2] for piece in used)
            gain = amount - r.get("charges_paise", 0) - cost
            pos["realised"] += gain
            moves.append((r["id"], -r["qty_u"], -cost, gain, amount, used))
        elif r["kind"] in ("DIVIDEND", "INTEREST"):
            pos["income"] += r["amount_paise"]
            moves.append((r["id"], 0, 0, 0, r["amount_paise"], []))
        else:  # FEE
            pos["fees"] += r["amount_paise"]
            moves.append((r["id"], 0, 0, 0, r["amount_paise"], []))
    positions = {
        f"{account}/{'-' if asset is None else asset}": {
            "qty_u": sum(lot[2] for lot in pos["lots"]),
            "cost_paise": sum(lot[3] for lot in pos["lots"]),
            "realised_paise": pos["realised"],
            "income_paise": pos["income"],
            "fees_paise": pos["fees"],
            "lots": [[lot[0], lot[2], lot[3]] for lot in pos["lots"]],
        }
        for (account, asset), pos in book.items()
    }
    return {
        "positions": positions,
        "moves": [
            {
                "entry": m[0],
                "qty_u": m[1],
                "cost_paise": m[2],
                "gain_paise": m[3],
                "paise": m[4],
                "used": m[5],
            }
            for m in moves
        ],
        "totals": {
            "realised_paise": sum(p["realised_paise"] for p in positions.values()),
            "income_paise": sum(p["income_paise"] for p in positions.values()),
            "fees_paise": sum(p["fees_paise"] for p in positions.values()),
        },
    }
