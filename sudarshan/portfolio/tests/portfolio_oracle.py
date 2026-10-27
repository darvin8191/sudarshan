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

import math
from datetime import date, timedelta
from decimal import Decimal, localcontext
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


def business_days_walked(start: int, end: int, holidays=()) -> int:
    """Business days after ``start`` up to and including ``end``, counted one calendar day at a
    time: a Monday to Friday that is not one of ``holidays``."""
    shut = set(holidays)
    count = 0
    when = date(start // 10000, start // 100 % 100, start % 100)
    stop = date(end // 10000, end // 100 % 100, end % 100)
    while when < stop:
        when += timedelta(days=1)
        key = when.year * 10000 + when.month * 100 + when.day
        count += when.weekday() < 5 and key not in shut
    return count


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


def day_lines(
    rows: list[dict],
    today: int,
    prev_close: dict | None = None,
    decimals: dict | None = None,
) -> dict:
    """What each holding line says on ``today``, worked out from the entries themselves.

    The quantity at the open is read from a replay of the entries without today's trades (today's
    splits and bonus shares stay: they take effect before trading), the money in and out from
    today's entries one by one, the rest from a replay up to today. Returns ``{asset: [line]}``,
    a line being the contract's HoldingLine as a dict, lowest account first.
    """
    prev_close = prev_close or {}
    after = simulate(rows, today, decimals, over_year_on=today)
    assert "refused" not in after, after
    before = simulate(
        [
            r
            for r in rows
            if r["trade_date"] < today
            or (r["trade_date"] == today and r["kind"] in ("SPLIT", "BONUS"))
        ],
        today,
        decimals,
    )
    assert "refused" not in before, before
    money_in: dict = {}
    money_out: dict = {}

    def put(book, account, asset, paise):
        book[(account, asset)] = book.get((account, asset), 0) + paise

    for r in rows:
        if r.get("status", "posted") != "posted" or r["trade_date"] != today:
            continue
        kind, asset = r["kind"], r.get("asset_id")
        if kind in ("BUY", "RIGHTS"):
            put(money_in, r["account_id"], asset, amount_of(r))
        elif kind == "SELL":
            put(money_out, r["account_id"], asset, amount_of(r))
        elif kind == "TRANSFER":
            source, target = r.get("account_id"), r.get("to_account_id")
            if source is None:
                value = r["amount_paise"] if r.get("value_paise") is None else r["value_paise"]
                put(money_in, target, asset, value)
            elif target is None:
                cost = -sum(m["cost_paise"] for m in after["moves"] if m["entry"] == r["id"])
                put(
                    money_out,
                    source,
                    asset,
                    cost if r.get("value_paise") is None else r["value_paise"],
                )
            else:
                close = prev_close.get(asset)
                value = 0 if close is None else half_away(Fraction(r["qty_u"] * close, 10**8))
                put(money_out, source, asset, value)
                put(money_in, target, asset, value)
    lines: dict = {}
    for name, position in after["positions"].items():
        account, asset = name.split("/")
        if asset == "-":
            continue
        account, asset = int(account), int(asset)
        opened = before["positions"].get(name, {}).get("qty_u", 0)
        line = {
            "account_id": account,
            "qty_u": position["qty_u"],
            "open_qty_u": opened,
            "cost_paise": position["cost_paise"],
            "in_today_paise": money_in.get((account, asset), 0),
            "out_today_paise": money_out.get((account, asset), 0),
            "over_year_qty_u": position["over_year_qty_u"],
        }
        if any(line[key] for key in ("qty_u", "open_qty_u", "in_today_paise", "out_today_paise")):
            lines.setdefault(asset, []).append(line)
    return {
        asset: sorted(found, key=lambda line: line["account_id"])
        for asset, found in sorted(lines.items())
    }


# ---------------------------------------------------------------- performance (PA4)


def flows(rows: list[dict], as_of: int | None = None, decimals: dict | None = None) -> list[dict]:
    """The money flows of a tab (design 4.7), read from the entries themselves one at a time.

    A purchase or rights issue is money in with its charges; a sale is money out less its
    charges; income is money out; a fee is its own term; shares brought in from outside are money
    in at the value entered (their cost when none was), and shares taken out to outside are money
    out at the value entered (the cost of the lots they took when none was). Only the replay
    (``simulate``) is asked anything, and only for that cost.
    """
    ran = simulate(rows, as_of, decimals)
    assert "refused" not in ran, ran
    days: dict[int, list[int]] = {}
    for r in rows:
        if r.get("status", "posted") != "posted" or (as_of is not None and r["trade_date"] > as_of):
            continue
        kind = r["kind"]
        paid = received = fee = 0
        if kind in ("BUY", "RIGHTS"):
            paid = amount_of(r) + r.get("charges_paise", 0)
        elif kind == "SELL":
            received = amount_of(r) - r.get("charges_paise", 0)
        elif kind in ("DIVIDEND", "INTEREST"):
            received = r["amount_paise"]
        elif kind == "FEE":
            fee = r["amount_paise"]
        elif kind == "TRANSFER" and r.get("account_id") is None:
            paid = r["amount_paise"] if r.get("value_paise") is None else r["value_paise"]
        elif kind == "TRANSFER" and r.get("to_account_id") is None:
            cost = sum(
                piece[2] for m in ran["moves"] if m["entry"] == r["id"] for piece in m["used"]
            )
            received = cost if r.get("value_paise") is None else r["value_paise"]
        if paid or received or fee:
            total = days.setdefault(r["trade_date"], [0, 0, 0])
            total[0] += paid
            total[1] += received
            total[2] += fee
    return [
        {"day": day, "in_paise": t[0], "out_paise": t[1], "fee_paise": t[2]}
        for day, t in sorted(days.items())
    ]


def ordinal(day: int) -> int:
    return date(day // 10000, day // 100 % 100, day % 100).toordinal()


def xirr_roots(cash: list, step: float = 0.01) -> list[float]:
    """Every yearly rate at which dated cash (``(YYYYMMDD, paise)``) adds up to nothing, nearest
    zero first (on the log scale, a gain before the loss that is just as near), found in floats:
    the sum is walked along x = ln(1 + rate) in steps and every sign change is halved down. Cash
    that never pays, or never receives, has none.
    """
    by_day: dict[int, int] = {}
    for day, paise in cash:
        by_day[ordinal(day)] = by_day.get(ordinal(day), 0) + paise
    items = sorted((d, p) for d, p in by_day.items() if p)
    if not (any(p < 0 for _, p in items) and any(p > 0 for _, p in items)):
        return []
    years = [((d - items[0][0]) / 365, p) for d, p in items]
    reach = min(23.0, 600 / years[-1][0])  # keep exp() inside what a float can hold

    def total(x: float) -> float:
        return sum(p * math.exp(-x * t) for t, p in years)

    roots = []
    count = int(2 * reach / step)
    x_before, f_before = -reach, total(-reach)
    for k in range(1, count + 1):
        x = -reach + k * step
        f = total(x)
        if f == 0:
            roots.append(x)
        elif f_before != 0 and (f > 0) != (f_before > 0):
            low, high = x_before, x
            for _ in range(100):
                mid = (low + high) / 2
                if (total(mid) > 0) == (f_before > 0):
                    low = mid
                else:
                    high = mid
            roots.append((low + high) / 2)
        x_before, f_before = x, f
    ordered = sorted(roots, key=abs)
    for i in range(len(ordered) - 1):  # two mirror images are equally near: the gain goes first
        near, far = ordered[i], ordered[i + 1]
        if near < 0 < far and abs(far) - abs(near) <= 1e-9 * abs(near):
            ordered[i], ordered[i + 1] = far, near
    return [math.exp(x) - 1 for x in ordered]


def pct_text(growth: Fraction) -> str:
    """A growth multiple less one as a percentage of four decimals, a tie away from zero."""
    tenths = half_away((growth - 1) * 10**6)
    return f"{'-' if tenths < 0 else ''}{abs(tenths) // 10**4}.{abs(tenths) % 10**4:04d}"


def twr(points: list[dict], opening: int | None = None) -> dict:
    """The time-weighted return of a daily series, in exact fractions.

    ``points`` are ``{"day", "value_paise" or None, "in_paise", "out_paise", "fee_paise"}``. Money
    in works from the start of its day and money out leaves at its end; a day with no closing
    value hands its money on to the next one that has; a stretch with nothing at work is skipped
    and one cannot lose more than everything it began with. Returns the stretches (start, day,
    base, grown), their product, and the figure's percentages.
    """
    by_day: dict[int, dict] = {}
    for p in sorted(points, key=lambda p: p["day"]):
        got = by_day.setdefault(p["day"], {"v": None, "in": 0, "out": 0, "fee": 0})
        if p.get("value_paise") is not None:
            got["v"] = p["value_paise"]
        got["in"] += p.get("in_paise", 0)
        got["out"] += p.get("out_paise", 0)
        got["fee"] += p.get("fee_paise", 0)
    days = sorted(by_day)
    stretches = []
    before, began = opening, None  # the value at the last complete close, and the day it was
    carry = {"in": 0, "out": 0, "fee": 0}
    first_open = None
    for d in days:
        got = by_day[d]
        if first_open is None:
            first_open = d
        for key in carry:
            carry[key] += got[key]
        if got["v"] is None:
            continue
        if before is not None and before + carry["in"] > 0:
            base = before + carry["in"]
            grown = max(0, got["v"] + carry["out"] - carry["fee"])
            stretches.append((first_open if began is None else began, d, base, grown))
        before, began, first_open = got["v"], d, None
        carry = {"in": 0, "out": 0, "fee": 0}
    growth = Fraction(1)
    for _, _, base, grown in stretches:
        growth *= Fraction(grown, base)
    figure = {"pct": None, "annualised_pct": None, "start_day": None}
    if stretches:
        figure["start_day"] = stretches[0][0]
        figure["pct"] = pct_text(growth)
        span = ordinal(stretches[-1][1]) - ordinal(stretches[0][0])
        if span > 365:
            with localcontext() as ctx:
                ctx.prec = 60
                ratio = Decimal(growth.numerator) / Decimal(growth.denominator)
                rate = ratio ** (Decimal(365) / Decimal(span)) - 1
            figure["annualised_pct"] = pct_text(Fraction(rate + 1))
    return {"stretches": stretches, "growth": growth, "figure": figure}


def year_figures(
    rows: list[dict], as_of: int | None = None, decimals: dict | None = None
) -> list[dict]:
    """Realised gain, gross income and fees for each financial year that has any, from the
    replay's moves and the entries' own dates (a financial year runs April to March)."""
    ran = simulate(rows, as_of, decimals)
    assert "refused" not in ran, ran
    by_id = {r["id"]: r for r in rows}
    years: dict[int, list[int]] = {}
    for m in ran["moves"]:
        r = by_id[m["entry"]]
        slot = {"SELL": 0, "DIVIDEND": 1, "INTEREST": 1, "FEE": 2}.get(r["kind"])
        if slot is None:
            continue
        day = r["trade_date"]
        fy = day // 10000 if day // 100 % 100 >= 4 else day // 10000 - 1
        paise = m["gain_paise"] if slot == 0 else m["paise"]
        years.setdefault(fy, [0, 0, 0])[slot] += paise
    return [
        {
            "fy": fy,
            "label": f"FY {fy}-{(fy + 1) % 100:02d}",
            "realised_paise": t[0],
            "income_paise": t[1],
            "fees_paise": t[2],
        }
        for fy, t in sorted(years.items())
        if any(t)
    ]


def income_by_month(rows: list[dict], valuation_day: int, decimals: dict | None = None) -> dict:
    """Income this financial year and in each of the twelve months ending with the valuation
    day's, from the entries (income dated after the valuation day is not counted)."""
    ran = simulate(rows, None, decimals)
    assert "refused" not in ran, ran
    by_id = {r["id"]: r for r in rows}
    year, month = valuation_day // 10000, valuation_day // 100 % 100
    keys = []
    for back in range(11, -1, -1):
        n = year * 12 + (month - 1) - back
        keys.append(n // 12 * 100 + n % 12 + 1)
    start = year if month >= 4 else year - 1
    this_fy = 0
    by_month = dict.fromkeys(keys, 0)
    for m in ran["moves"]:
        r = by_id[m["entry"]]
        day = r["trade_date"]
        if r["kind"] not in ("DIVIDEND", "INTEREST") or day > valuation_day:
            continue
        if (day >= start * 10000 + 401) and (day <= (start + 1) * 10000 + 331):
            this_fy += m["paise"]
        if day // 100 in by_month:
            by_month[day // 100] += m["paise"]
    return {
        "this_fy_paise": this_fy,
        "by_month": [{"month": k, "paise": v} for k, v in by_month.items()],
    }
