"""A closing record whose holdings could not be read is shown as not known, never as 0."""

from __future__ import annotations

import asyncio

from test_portfolio_eod import Rig, ist, trade

from sudarshan.portfolio import eod
from sudarshan.portfolio import store as store_module
from sudarshan.portfolio.ops.upstox_ops import upstox_history
from sudarshan.portfolio.store import Scope

OWNER = Scope.owner()


def test_a_day_whose_holdings_were_not_read_is_not_shown_as_zero(tmp_path):
    rig = Rig(tmp_path)
    try:
        trade(rig)
        rig.upstox.status["holdings"] = 503  # positions and funds still answer
        assert rig.run(eod.eod_step, ist(26, 15, 55)) == "ok"
        head = rig.svc.store.read(OWNER, lambda tx: tx.rows("upstox_eod", "day"))[0]
        assert head["holdings_value_paise"] is None  # the record itself says "not known"

        rig.now = ist(27, 10)
        shown = asyncio.run(upstox_history(rig.svc, {}))["days"][0]
        # The phone must not be told the holdings were worth 0 that day.
        assert shown["holdings_value_paise"] is None, shown
        assert shown["holdings_cost_paise"] is None
        assert shown["positions_pnl_paise"] is not None  # the part that was read stays a figure
        assert shown["not_read"] == ["Holdings were not read at the close."]
    finally:
        rig.close()
        store_module.stop()
