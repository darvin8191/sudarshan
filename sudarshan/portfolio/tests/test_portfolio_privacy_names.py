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


# ---- the age of a price is never hidden (v0.10.0's open point, settled in v0.12.0) ---------------
FRESH = {"state": "old", "words": "Value from 1 Oct \u00b7 45 days old"}


def test_fresh_is_hidden_by_no_category_and_listed_as_never_hidden():
    """v0.10.0 said a member not shown quantities would miss the line saying how old the figures
    are, to be settled before the family view is switched on: ``fresh`` travelled with ``qty``.
    Fails on the old table: it was in the qty list and in neither of the other two."""
    from sudarshan.family import privacy as family_privacy

    assert TABLE["version"] == 3
    assert "fresh" in TABLE["never_hidden"] and TABLE["kept_whole"] == ["fresh"]
    for name, entries in TABLE["categories"].items():
        assert "fresh" not in entries, name
    assert "fresh" not in TABLE["cost"]
    for shown in CHOICES:
        assert not serialize.hidden("fresh", privacy.floor(shown))
        assert not family_privacy.is_hidden("fresh", family_privacy.mask_for({"privacy": shown}))
    assert privacy.KEPT_WHOLE == family_privacy.KEPT_WHOLE == frozenset({"fresh"})


@pytest.mark.parametrize("shown", CHOICES, ids=name_of)
def test_a_holdings_age_reaches_the_member_whatever_is_hidden_and_nothing_else_does(shown):
    """Both maskers, for each of the sixteen choices: the age line arrives with its sentence
    (``words`` is otherwise hidden with quantities and with P&L), and the figures beside it go
    exactly as before. Fails on the old code for every choice that hides quantities or P&L."""
    from sudarshan.family import privacy as family_privacy

    row = {
        "asset": {"id": 7, "name": "A fund", "kind": "mf"},
        "fresh": dict(FRESH),
        "words": "a note with a figure",
        "price": {"price_u": 1234500, "day": 20261001},
        "lines": [{"qty_u": 5, "value_paise": 100}],
        "value_paise": 100,
    }
    for masked, mask in (
        (serialize.masked, privacy.floor(shown)),
        (family_privacy.masked, family_privacy.mask_for({"privacy": shown})),
    ):
        got = masked({"rows": [row]}, mask)["rows"][0]
        assert got["fresh"] == FRESH and got["asset"] == row["asset"]
        if not shown["qty"]:
            assert set(got) == {"asset", "fresh"}
        if not (shown["qty"] and shown["pnl"]):
            assert "words" not in got and "value_paise" not in got


@pytest.mark.parametrize(
    "odd",
    [
        {"state": "old", "words": "x", "value_paise": 5},
        {"state": "old", "words": {"qty_u": 5}},
        {"state": 1, "words": "x", "qty_u": 9},
        [{"qty_u": 5}],
    ],
)
def test_a_fresh_of_any_other_shape_is_looked_inside_like_every_other_field(odd):
    """Kept whole only while it is what ``core.freshness`` makes: a figure put inside one by a
    later change is still removed, on both sides."""
    from sudarshan.family import privacy as family_privacy

    for masked, mask in (
        (serialize.masked, serialize.VIEWER_MASK),
        (family_privacy.masked, family_privacy.mask_of(CATEGORIES)),
    ):
        got = masked({"fresh": odd}, mask)["fresh"]
        text = json.dumps(got)
        assert "paise" not in text and "qty_u" not in text and '"words"' not in text


def test_every_fresh_the_portfolio_sends_is_the_state_and_the_sentence_and_no_amount():
    """The closed world behind ``kept_whole``: every ``"fresh"`` in the portfolio's answers is
    ``Freshness.as_dict()``, and no sentence the freshness rules make carries an amount."""
    import re

    from sudarshan.portfolio.core import freshness

    assert set(freshness.Freshness("old", "x").as_dict()) == {"state", "words"}
    folder = REPO / "sudarshan" / "portfolio"
    made = [
        (path.name, line.strip())
        for path in sorted(folder.rglob("*.py"))
        if "tests" not in path.parts and path.name != "privacy.py"  # its table of shapes
        for line in path.read_text("utf-8").splitlines()
        if re.search(r"""["']fresh["']\s*:""", line)
    ]
    assert made and all(line == '"fresh": fresh.as_dict(),' for _, line in made), made
    vectors = json.loads((folder / "tests" / "vectors" / "freshness.json").read_text("utf-8"))
    sentences = re.findall(r'"words":\s*"([^"]*)"', json.dumps(vectors))
    assert len(sentences) > 5
    for sentence in sentences:
        assert not re.search(r"\u20b9|Rs\.?\s|paise|\d,\d|\d\.\d", sentence), sentence


def test_a_table_cannot_keep_whole_what_is_not_never_hidden_or_not_known(tmp_path):
    from sudarshan.family import privacy as family_privacy

    path = tmp_path / "f.json"
    for body in (
        '{"never_hidden": [], "kept_whole": ["fresh"]}',
        '{"never_hidden": ["lines"], "kept_whole": ["lines"]}',
        '{"never_hidden": ["fresh"], "kept_whole": "fresh"}',
    ):
        path.write_text(body)
        with pytest.raises(ValueError):
            privacy.load_whole(path)
        with pytest.raises(ValueError):
            family_privacy.load_whole(path)
