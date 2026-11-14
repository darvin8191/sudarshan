"""What the journal asks of the tax phase (M7.7, P4; design p7 4.6, 8): the owner's working paper
over the journal's own purpose, and Upstox's charges for some days of a year.

Through the sealed API with the journal's key, on the fake Upstox of the purposes tests.
"""

from __future__ import annotations

import dataclasses

from test_portfolio_purposes import NOW, scenario
from test_portfolio_purposes import fake as fake  # noqa: F401  (the fake Upstox fixture)


@scenario
async def test_the_journal_is_given_the_owners_paper_only_while_tax_is_on(rig):
    status, body = await rig.journal("tax_pack", fy=2026)
    assert (status, body["code"]) == (501, "not_ready")
    rig.svc.settings = dataclasses.replace(rig.svc.settings, tax=True)
    status, body = await rig.journal("tax_pack", fy=2026)
    assert status == 200 and body["data"]["fy"] == 2026 and body["data"]["as_of_ms"] == NOW
    assert body["data"] == await rig.owner("tax_get", fy=2026)
    assert (await rig.journal("tax_pack", fy=2020))[0] == 400  # Upstox keeps three years


@scenario
async def test_the_charges_of_some_days_are_read_with_the_dates_upstox_takes(rig, fake):
    base = {"part": "charges", "segment": "FO", "fy": 2026}
    status, body = await rig.journal("pnl_report", **base, **{"from": 20261103, "to": 20261103})
    assert status == 200, body
    assert (body["data"]["from"], body["data"]["to"]) == (20261103, 20261103)
    sent = fake.queries[-1][1]
    assert (sent["from_date"], sent["to_date"], sent["financial_year"]) == (
        "03-11-2026",
        "03-11-2026",
        "2627",
    )
    whole = (await rig.journal("pnl_report", **base))[1]["data"]
    assert "from" not in whole and "from_date" not in fake.queries[-1][1]
    for span in ({"from": 20260331, "to": 20260401}, {"from": 20261104, "to": 20261103},
                 {"from": 20261103}, {"to": 20270401}):  # fmt: skip
        assert (await rig.journal("pnl_report", **base, **span))[0] == 400, span
