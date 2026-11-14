"""One table of privacy categories (cross-design Y24, Y6): the owner's Family page, the family service
and the portfolio name the same four, in the same order, and agree on what every choice hides.

``sudarshan/family/privacy_fields.json`` is the one table. The family service's contract, the
categories it tells a member are hidden (``viewer_api.hidden_of``) and the choices it keeps
(``members.clean_privacy``); the portfolio's floor (``privacy.py``, ``sharing.privacy_set``); and
the phone's two copies (``family/types.ts``, which the owner's page reads, and
``portfolio/types.ts``, which the page sends the floor with) are each held to it here, for every
one of the sixteen choices, so none of them can drift from the others.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from portfolio_ts import read_const

from sudarshan.family import contract, members, viewer_api
from sudarshan.family.errors import FamilyError
from sudarshan.portfolio import privacy, serialize
from sudarshan.portfolio.core import kinds

REPO = Path(__file__).resolve().parents[3]
TABLE = json.loads((REPO / "sudarshan" / "family" / "privacy_fields.json").read_text("utf-8"))
CATEGORIES = tuple(TABLE["categories"])
BITS = itertools.product((False, True), repeat=len(CATEGORIES))
CHOICES = [dict(zip(CATEGORIES, bits, strict=True)) for bits in BITS]


def name_of(shown: dict[str, bool]) -> str:
    return ",".join(c for c in CATEGORIES if shown[c]) or "nothing"


def test_the_three_name_the_categories_of_the_one_table_in_its_order():
    assert CATEGORIES == ("pnl", "qty", "funds", "orders")
    assert contract.CATEGORIES == CATEGORIES  # the family service
    assert kinds.PRIVACY_CATEGORIES == CATEGORIES  # the portfolio
    assert tuple(privacy.CATEGORIES) == CATEGORIES  # the portfolio's floor, read from the table
    owner_page = (REPO / "frontend/src/sudarshan/family/types.ts").read_text("utf-8")
    floor_page = (REPO / "frontend/src/sudarshan/portfolio/types.ts").read_text("utf-8")
    assert read_const(owner_page, "CATEGORIES") == list(CATEGORIES)  # the owner's page
    assert read_const(floor_page, "PRIVACY_CATEGORIES") == list(CATEGORIES)  # what it sends
    assert read_const(owner_page, "PRIVACY_DEFAULTS") == contract.PRIVACY_DEFAULTS
    for shown in contract.PRIVACY_DEFAULTS.values():
        assert tuple(shown) == CATEGORIES


@pytest.mark.parametrize("shown", CHOICES, ids=name_of)
def test_the_family_service_and_the_floor_hide_the_same_categories(shown):
    """What the family service tells a member is hidden is what the portfolio's floor hides."""
    assert set(viewer_api.hidden_of(shown)) == set(privacy.hidden_categories(shown))


@pytest.mark.parametrize("shown", CHOICES, ids=name_of)
def test_a_choice_one_keeps_the_other_keeps(shown):
    """The page sends the same four switches to both; neither may refuse what the other keeps
    (P&L shown needs quantities shown, in both)."""
    try:
        members.clean_privacy(shown, "viewer")
        family_keeps = True
    except FamilyError:
        family_keeps = False
    assert family_keeps == (privacy.valid_shown(shown) is None)


@pytest.mark.parametrize("shown", CHOICES, ids=name_of)
def test_the_floor_is_the_table_for_what_is_hidden(shown):
    """The floor of a choice is the union of the table's lists for the hidden categories, and the
    cost list while P&L is hidden (review H1): what the family service sends under the family
    purpose, built from the same table, can only add to it."""
    hidden = viewer_api.hidden_of(shown)
    expected: set[str] = set()
    for category in hidden:
        expected |= set(TABLE["categories"][category])
    if "pnl" in hidden:
        expected |= set(TABLE["cost"])
    assert privacy.floor(shown) == frozenset(expected)
    for name in TABLE["never_hidden"]:
        assert not serialize.hidden(name, privacy.floor(shown)), name


def test_quantities_hidden_hide_pnl_on_every_side():
    choice = {"pnl": True, "qty": False, "funds": True, "orders": True}
    assert viewer_api.hidden_of(choice)[0] == "pnl"
    assert "pnl" in privacy.hidden_categories(choice)
    with pytest.raises(FamilyError):
        members.clean_privacy(choice, "viewer")
