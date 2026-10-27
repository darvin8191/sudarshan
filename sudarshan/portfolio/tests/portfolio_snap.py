"""A replayed ledger as plain data, in the shape of the vectors and of the naive oracle."""

from __future__ import annotations

from sudarshan.portfolio.core.model import Ledger
from sudarshan.portfolio.core.words import Names


def snapshot(ledger: Ledger, over_year_on: int | None = None) -> dict:
    """``{"positions", "moves", "totals"}``; a position is keyed ``"<account>/<asset>"`` ("-" for
    the account's own income and fees), a lot is ``[day, qty_u, cost_paise]``. With a day in
    ``over_year_on`` each position also says how many units were held for more than a year."""
    positions = {
        f"{p.account_id}/{'-' if p.asset_id is None else p.asset_id}": {
            "qty_u": p.qty_u,
            "cost_paise": p.cost_paise,
            "realised_paise": p.realised_paise,
            "income_paise": p.income_paise,
            "fees_paise": p.fees_paise,
            "lots": [[lot.day, lot.qty_u, lot.cost_paise] for lot in p.lots],
            **(
                {} if over_year_on is None else {"over_year_qty_u": p.over_year_qty_u(over_year_on)}
            ),
        }
        for p in ledger.positions.values()
    }
    moves = [
        {
            "entry": m.entry.id,
            "account": m.account_id,
            "qty_u": m.qty_u,
            "cost_paise": m.cost_paise,
            "gain_paise": m.gain_paise,
            "paise": m.paise,
            "used": [list(piece) for piece in m.used],
        }
        for m in ledger.journal
    ]
    totals = {
        "realised_paise": ledger.realised_paise,
        "income_paise": ledger.income_paise,
        "fees_paise": ledger.fees_paise,
    }
    return {"positions": positions, "moves": moves, "totals": totals}


def names_of(spec: dict | None) -> Names:
    """Names from a vector's ``names`` (JSON keys are text)."""
    spec = spec or {}
    return Names(
        accounts={int(k): v for k, v in spec.get("accounts", {}).items()},
        assets={int(k): v for k, v in spec.get("assets", {}).items()},
        units={int(k): v for k, v in spec.get("units", {}).items()},
    )
