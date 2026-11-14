"""The week against NIFTY 50, and beta (JA5, M14.3 and M15.3; design 5.5).

* The account's week is the net of the week's closed trips as a percentage of the trading
  capital the owner typed; without one the figure has no number and says so.
* The index's week is its Friday close over the previous Friday close, minus one.
* Beta: daily ``r_p = day net / capital`` against ``r_m = close / previous close - 1`` over the
  last 60 trading days (a day with no trades is 0), shown only from 20 days. Population
  covariance and variance, exact fractions; correlation takes one square root, in Decimal. The
  arithmetic is ``sudarshan.common.stats`` (shared with the portfolio); this module turns the
  journal's days into returns and the answers into the journal's strings.
  ``beta = cov(r_p, r_m) / var(r_m)``; correlation is ``cov / (sd_p x sd_m)``. Where a standard
  deviation is 0 the correlation says "no variation"; where the index did not move there is no
  beta either.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Any

from sudarshan.common import stats
from sudarshan.common.stats import sqrt_ratio as _sqrt_ratio  # noqa: F401  (its old name here)
from sudarshan.journal.core import kinds, money

NO_CAPITAL_NOTE = "set your trading capital under Reports to see this"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def week_vs_index(
    net_paise: int,
    capital_base_paise: int | None,
    index_close_u: int | None,
    index_prev_close_u: int | None,
) -> dict[str, Any]:
    """The account's week and the index's week, each as a percentage string with 2 decimals."""
    index_pct = None
    if index_close_u is not None and index_prev_close_u:
        index_pct = money.ratio_str(
            (index_close_u - index_prev_close_u) * 100, index_prev_close_u, 2
        )
    if not capital_base_paise:
        return {"account_pct": None, "account_state": "no_capital", "index_pct": index_pct}
    return {
        "account_pct": money.ratio_str(net_paise * 100, capital_base_paise, 2),
        "account_state": "ok",
        "index_pct": index_pct,
    }


def beta_note(have: int, need: int, ready_day: int | None = None) -> str:
    """ "shown after 20 trading days (from 9 Dec 2026)": the day is the caller's, from the calendar."""
    text = f"shown after {need} trading days"
    if ready_day is not None:
        d = money.to_date(ready_day)
        text += f" (from {d.day} {MONTHS[d.month - 1]} {d.year})"
    return text


def correlation_beta(
    days: Sequence[dict[str, int]],
    capital_base_paise: int | None,
    min_days: int = kinds.LIMITS["beta_min_days"],
    window: int = kinds.LIMITS["beta_days"],
    ready_day: int | None = None,
) -> dict[str, Any]:
    """Beta and correlation of the account against the index over the last ``window`` days.

    Each day is ``{"net_paise", "close_u", "prev_close_u"}``, oldest first.
    """
    used = list(days)[-window:]
    out: dict[str, Any] = {
        "beta": None,
        "correlation": None,
        "state": "ok",
        "correlation_state": "ok",
        "days": len(used),
        "need": min_days,
        "note": None,
    }
    if not capital_base_paise:
        out["state"] = out["correlation_state"] = "no_capital"
        out["note"] = NO_CAPITAL_NOTE
        return out
    if len(used) < min_days or not used:
        out["state"] = out["correlation_state"] = "short"
        out["note"] = beta_note(len(used), min_days, ready_day)
        return out
    r_p = [Fraction(int(d["net_paise"]), capital_base_paise) for d in used]
    r_m = [Fraction(int(d["close_u"]), int(d["prev_close_u"])) - 1 for d in used]
    found = stats.correlation_beta(r_p, r_m, min_days, window)
    out["state"], out["correlation_state"] = found["state"], found["correlation_state"]
    if found["beta"] is not None:
        out["beta"] = money.fraction_str(found["beta"], kinds.RATIO_PLACES["beta"])
    if found["correlation"] is not None:
        out["correlation"] = str(
            found["correlation"].quantize(Decimal(1).scaleb(-4), rounding=ROUND_HALF_UP)
        )
    return out
