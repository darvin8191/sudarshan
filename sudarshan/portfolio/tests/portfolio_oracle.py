"""A second, deliberately naive implementation of the ledger rules, for the tests to check against.

The engine (core/ledger.py, core/ledger_actions.py) is an optimised replay over typed entries.
This is the same rules as a reader of the design would write them first: plain dicts in, plain
dicts out, exact fractions for every division, lots kept as short lists. It shares no code with
the engine (it imports nothing from sudarshan), so a test that runs both on the same entries
compares two readings of the design rather than a function with itself.

A row is the contract's ActivityInput as a dict (``id``, ``seq``, ``kind``, ``trade_date``,
``account_id``, ``asset_id``, ``qty_u``, ``amount_paise``, ``price_u``, ``charges_paise`` ...).
"""

from __future__ import annotations

from datetime import date
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


def rank(row: dict) -> int:
    """Where in its day a row goes: splits and bonus, then what comes in, what moves between
    accounts, what goes out, and last the income and fees."""
    kind = row["kind"]
    if kind in ("SPLIT", "BONUS"):
        return 0
    if kind in ("BUY", "RIGHTS"):
        return 1
    if kind == "TRANSFER":
        if row.get("account_id") is None:
            return 1
        return 3 if row.get("to_account_id") is None else 2
    return 3 if kind == "SELL" else 4


def over_a_year(acquired: int, on: int) -> bool:
    """Held for more than a year: ``on`` is after the first anniversary (29 Feb becomes 28 Feb)."""
    start = date(acquired // 10000, acquired // 100 % 100, acquired % 100)
    try:
        later = start.replace(year=start.year + 1)
    except ValueError:
        later = start.replace(year=start.year + 1, day=28)
    return on > later.year * 10000 + later.month * 100 + later.day


def simulate(
    rows: list[dict],
    as_of: int | None = None,
    decimals: dict | None = None,
    over_year_on: int | None = None,
) -> dict:
    """Replay ``rows``; returns ``{"positions", "moves", "totals"}`` or ``{"refused": {...}}``."""
    decimals = decimals or {}
    live = [
        r
        for r in rows
        if r.get("status", "posted") == "posted" and (as_of is None or r["trade_date"] <= as_of)
    ]
    live.sort(key=lambda r: (r["trade_date"], rank(r), r["seq"], r.get("id") or 0))
    book: dict = {}
    moves: list[dict] = []
    made = 0

    def get(account, asset):
        return book.setdefault(
            (account, asset), {"lots": [], "realised": 0, "income": 0, "fees": 0}
        )

    def held(account, asset):
        return sum(lot[2] for lot in get(account, asset)["lots"])

    def move(row, account, qty, cost, gain, paise, used):
        moves.append(
            {
                "entry": row["id"],
                "account": account,
                "qty_u": qty,
                "cost_paise": cost,
                "gain_paise": gain,
                "paise": paise,
                "used": used,
            }
        )

    def put(account, asset, day, qty, cost):
        nonlocal made
        made += 1
        lots = get(account, asset)["lots"]
        lots.append([day, made, qty, cost])
        lots.sort(key=lambda lot: (lot[0], lot[1]))

    def take(account, asset, qty):
        lots, used, need = get(account, asset)["lots"], [], qty
        while need:
            lot = lots[0]
            part = min(need, lot[2])
            cost = lot[3] if part == lot[2] else half_away(Fraction(lot[3] * part, lot[2]))
            used.append([lot[0], part, cost])
            lot[2] -= part
            lot[3] -= cost
            need -= part
            if lot[2] == 0:
                lots.pop(0)
        return used

    for r in live:
        kind, asset, account = r["kind"], r.get("asset_id"), r.get("account_id")
        amount = amount_of(r)
        if kind in ("BUY", "RIGHTS"):
            cost = amount + r.get("charges_paise", 0)
            put(account, asset, r["trade_date"], r["qty_u"], cost)
            move(r, account, r["qty_u"], cost, 0, amount, [])
        elif kind == "SELL":
            have = held(account, asset)
            if have < r["qty_u"]:
                return {
                    "refused": {
                        "entry": r["id"],
                        "reason": "short",
                        "have": have,
                        "want": r["qty_u"],
                    }
                }
            used = take(account, asset, r["qty_u"])
            cost = sum(piece[2] for piece in used)
            gain = amount - r.get("charges_paise", 0) - cost
            get(account, asset)["realised"] += gain
            move(r, account, -r["qty_u"], -cost, gain, amount, used)
        elif kind in ("DIVIDEND", "INTEREST"):
            get(account, asset)["income"] += r["amount_paise"]
            move(r, account, 0, 0, 0, r["amount_paise"], [])
        elif kind == "FEE":
            get(account, asset)["fees"] += r["amount_paise"]
            move(r, account, 0, 0, 0, r["amount_paise"], [])
        elif kind == "BONUS":
            if held(account, asset) == 0:
                return {"refused": {"entry": r["id"], "reason": "not_held"}}
            put(account, asset, r["trade_date"], r["qty_u"], 0)
            move(r, account, r["qty_u"], 0, 0, None, [])
        elif kind == "SPLIT":
            num, den = r["ratio_num"], r["ratio_den"]
            if account is None:
                targets = sorted(a for (a, s), p in book.items() if s == asset and held(a, s))
            else:
                targets = [account] if held(account, asset) else []
            if not targets:
                return {"refused": {"entry": r["id"], "reason": "not_held"}}
            plans = []
            for target in targets:
                lots = get(target, asset)["lots"]
                total = sum(lot[2] for lot in lots)
                exact = Fraction(total * num, den)
                left = half_away(exact)
                quantities, running, given = [], 0, 0
                for lot in lots:
                    running += lot[2]
                    upto = half_away(Fraction(running * num, den))
                    quantities.append(upto - given)
                    given = upto
                unit = 10 ** (6 - decimals.get(asset, 0))
                if exact.denominator != 1 or left % unit or min(quantities) <= 0:
                    return {
                        "refused": {
                            "entry": r["id"],
                            "reason": "fraction",
                            "have": total,
                            "left": left,
                            "account": target,
                        }
                    }
                plans.append((target, lots, total, quantities))
            for target, lots, total, quantities in plans:
                for lot, quantity in zip(lots, quantities, strict=True):
                    lot[2] = quantity
                after = [[lot[0], lot[2], lot[3]] for lot in lots]
                move(r, target, sum(quantities) - total, 0, 0, None, after)
        else:  # TRANSFER
            target, qty = r.get("to_account_id"), r["qty_u"]
            if account is None:
                value = r["amount_paise"] if r.get("value_paise") is None else r["value_paise"]
                put(target, asset, r.get("lot_date") or r["trade_date"], qty, r["amount_paise"])
                move(r, target, qty, r["amount_paise"], 0, value, [])
                continue
            have = held(account, asset)
            if have < qty:
                return {"refused": {"entry": r["id"], "reason": "short", "have": have, "want": qty}}
            used = take(account, asset, qty)
            cost = sum(piece[2] for piece in used)
            if target is None:
                value = cost if r.get("value_paise") is None else r["value_paise"]
                move(r, account, -qty, -cost, 0, value, used)
                continue
            for piece in used:
                put(target, asset, piece[0], piece[1], piece[2])
            move(r, account, -qty, -cost, 0, None, used)
            move(r, target, qty, cost, 0, None, used)
    positions = {}
    for (account, asset), pos in book.items():
        entry = {
            "qty_u": sum(lot[2] for lot in pos["lots"]),
            "cost_paise": sum(lot[3] for lot in pos["lots"]),
            "realised_paise": pos["realised"],
            "income_paise": pos["income"],
            "fees_paise": pos["fees"],
            "lots": [[lot[0], lot[2], lot[3]] for lot in pos["lots"]],
        }
        if over_year_on is not None:
            entry["over_year_qty_u"] = sum(
                lot[2] for lot in pos["lots"] if over_a_year(lot[0], over_year_on)
            )
        positions[f"{account}/{'-' if asset is None else asset}"] = entry
    return {
        "positions": positions,
        "moves": moves,
        "totals": {
            "realised_paise": sum(p["realised_paise"] for p in positions.values()),
            "income_paise": sum(p["income_paise"] for p in positions.values()),
            "fees_paise": sum(p["fees_paise"] for p in positions.values()),
        },
    }
