"""The order ticket's charges estimate (B6, M5.10): golden figures worked by hand against the rate
card for every segment and side, the order's fields checked, the owner's plan from the settings,
and nothing written. Every value is synthetic."""

from __future__ import annotations

import asyncio
import json
import types
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from sudarshan.journal import store
from sudarshan.journal.core import charges as charges_core
from sudarshan.journal.core import kinds
from sudarshan.journal.core.money import IST
from sudarshan.journal.errors import ApiError
from sudarshan.journal.ops import REGISTRY
from sudarshan.journal.store import JournalStore, Scope

OWNER = Scope.owner()
NOW = 1_794_300_000_000
DATA = json.loads(
    (Path(__file__).parent / "vectors" / "charges_estimate.json").read_text(encoding="utf-8")
)
VEC = {v["id"]: v for v in DATA["vectors"]}


def at(day: int) -> int:
    """11:00 in India on a YYYYMMDD day, epoch ms."""
    stamp = datetime(day // 10000, day // 100 % 100, day % 100, 11, 0, tzinfo=IST)
    return int(stamp.timestamp() * 1000)


@pytest.fixture
def made(tmp_path):
    opened = JournalStore(tmp_path / store.DB_FILENAME, now=NOW)
    yield opened
    opened.close()
    store.stop()


def plan(made, name):
    made.write(OWNER, lambda tx: tx.settings_save({"charges_plan": name},
                                                   tx.settings()["version"], NOW))  # fmt: skip


def ask(made, day=20261116, **args):
    ctx = SimpleNamespace(store=made, clock=lambda: at(day))
    return asyncio.run(REGISTRY["charges_estimate"](ctx, args))


@pytest.mark.parametrize("vec", DATA["vectors"], ids=[v["id"] for v in DATA["vectors"]])
def test_golden_figures(made, vec):
    if vec["plan"] != charges_core.DEFAULT_PLAN:
        plan(made, vec["plan"])
    got = ask(made, vec["day"], **vec["args"])
    want = vec["expect"]
    assert {k: got[k] for k in want} == want
    assert got["plan"] == vec["plan"] and got["day"] == vec["day"]
    parts = got["parts"]
    assert parts["total_paise"] == sum(v for k, v in parts.items() if k != "total_paise")
    assert set(got) == {f.name for f in kinds.record("OrderCharges")}
    assert set(parts) == {f.name for f in kinds.record("ChargeParts")}


def test_every_segment_and_side_has_a_figure():
    seen = {(v["expect"]["segment"], v["args"]["action"]) for v in DATA["vectors"]}
    for segment in ("equity_delivery", "equity_intraday", "futures", "options"):
        assert {(segment, "BUY"), (segment, "SELL")} <= seen, segment


def test_the_rate_card_named_is_the_one_of_the_day(made):
    got = ask(made, 20261116, **VEC["G2"]["args"])
    assert got["rate_card"] == {"version": charges_core.CARD_VERSION, "from_day": 20260401}
    earlier = ask(made, 20260315, **VEC["G11"]["args"])
    assert earlier["rate_card"] == {"version": charges_core.CARD_VERSION, "from_day": 20260301}


def test_the_figure_moves_with_the_order(made):
    """Twice the quantity is twice the turnover, never a second brokerage."""
    one = ask(made, **VEC["G7"]["args"])
    two = ask(made, **{**VEC["G7"]["args"], "quantity": 200})
    assert two["turnover_paise"] == 2 * one["turnover_paise"]
    assert two["parts"]["brokerage_paise"] == one["parts"]["brokerage_paise"] == 3000
    assert two["parts"]["total_paise"] > one["parts"]["total_paise"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quantity", 0),
        ("quantity", True),
        ("quantity", "65"),
        ("quantity", 10_000_001),
        ("price_u", 0),
        ("price_u", -5),
        ("price_u", 10**12 + 1),
        ("action", "HOLD"),
        ("product", "XYZ"),
        ("exchange", ""),
        ("exchange", "NSE;"),
        ("exchange", "N" * 17),
        ("symbol", ""),
        ("symbol", "S" * 61),
        ("symbol", None),
    ],
)
def test_a_field_the_ticket_could_not_have_sent_is_the_usual_sentence(made, field, value):
    args = {**VEC["G1"]["args"], field: value}
    if value is None:
        args.pop(field)
    with pytest.raises(ApiError) as caught:
        ask(made, **args)
    assert (caught.value.status, caught.value.code, caught.value.field) == (
        400, "bad_request", field)  # fmt: skip
    assert "Reload the page" in caught.value.message


def test_lower_case_names_are_read_as_the_ticket_means_them(made):
    got = ask(made, **{**VEC["G5"]["args"], "exchange": "nse", "symbol": "sbin"})
    assert got["parts"] == VEC["G5"]["expect"]["parts"]


def test_it_writes_nothing(made, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("the estimate wrote to the journal")

    monkeypatch.setattr(made, "write", refuse)
    assert ask(made, **VEC["G2"]["args"])["parts"]["total_paise"] == 5178


def test_no_store_is_the_journals_sentence():
    ctx = SimpleNamespace(store=None, clock=lambda: at(20261116))
    with pytest.raises(ApiError) as caught:
        asyncio.run(REGISTRY["charges_estimate"](ctx, dict(VEC["G1"]["args"])))
    assert (caught.value.status, caught.value.code) == (503, "unavailable")


def test_a_plan_the_card_does_not_know_is_priced_on_the_default():
    from sudarshan.journal.ops.charges_ops import plan_of

    assert plan_of({"charges_plan": "gold"}) == charges_core.DEFAULT_PLAN
    assert plan_of({}) == charges_core.DEFAULT_PLAN
    assert plan_of({"charges_plan": "standard"}) == "standard"


def test_the_plus_brokerage_is_one_setting():
    """Owner decision 7 (still open): Rs 30 an executed order on Plus, every surface, until he
    confirms. A later Rs 10 is a change of PLUS_ORDER_PAISE alone: the card's Plus row and its
    edition are built from it, so the ticket, the trade sheet and the Reports page all follow."""
    assert charges_core.PLUS_ORDER_PAISE == 3000
    for surface in ("app", "api", "telegram", "upstox_app", None):
        assert charges_core.brokerage_paise("plus", surface, 20261116) == 3000
    assert charges_core.CARD_VERSION == "2026-11-14.2, Plus Rs 30 an order"
    source = Path(charges_core.__file__).read_text(encoding="utf-8")
    assert source.count("PLUS_ORDER_PAISE = 3000\n") == 1
    changed = types.ModuleType("charges_at_rs_10")
    exec(compile(source.replace("PLUS_ORDER_PAISE = 3000\n", "PLUS_ORDER_PAISE = 1000\n"),
                 "charges_at_rs_10", "exec"), changed.__dict__)  # fmt: skip
    for surface in ("app", "api", "upstox_app", None):
        assert changed.brokerage_paise("plus", surface, 20261116) == 1000
    assert changed.CARD_VERSION == "2026-11-14.2, Plus Rs 10 an order"
    order = {**VEC["G2"]["args"], "qty": 65, "order_id": "t", "day": 20261116, "surface": "app"}
    assert changed.estimate([order])["parts"]["total_paise"] == 2818  # G10's Rs 10 figures
