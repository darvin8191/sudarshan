"""presets.py against the library as typed in vectors/presets.json (design 5.3).

Each usable preset has its checked form written out line by line (only the defaults every screen
shares are typed once), the words of its card and what the engine is told to keep. The tests check
what the code makes against that text, never the other way round.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest
from screener_helpers import plan as plan_view

from sudarshan.screener import catalog as cat
from sudarshan.screener import presets as P
from sudarshan.screener import spec

VECTORS = json.loads((Path(__file__).parent / "vectors" / "presets.json").read_text("utf-8"))
LIBRARY = VECTORS["presets"]
USABLE = [case for case in LIBRARY if "screen" in case]
FOLLOWING = [case for case in USABLE if "followed" in case]
STEADY = [case for case in USABLE if "followed" not in case]
DEFAULTS = VECTORS["defaults"]
ORDER = [
    "long_buildup",
    "short_buildup",
    "short_covering",
    "iv_extremes",
    "orb_breakout",
    "ema_cross_15",
    "vwap_reclaim",
    "delivery_spike",
    "volume_spike",
]
PLAN_KEYS = ("on", "tf", "need", "instances", "uses", "ids", "rank_order")


def keys(cases):
    return [case["key"] for case in cases]


def stored_of(case: dict, run: dict | None = None) -> dict:
    """The checked form of a preset, laid out from the typed lines and the shared defaults."""
    screen = case["screen"]
    return {
        "schema": 1,
        "name": case["name"],
        "universe": screen["universe"],
        "run": screen["run"] if run is None else run,
        "where": screen["where"],
        "rank": screen.get("rank", DEFAULTS["rank"]),
        "target": {**DEFAULTS["target"], "create_name": f"Screener: {case['name']}"},
        "alerts": {
            **DEFAULTS["alerts"],
            "columns": case.get("columns", DEFAULTS["alerts"]["columns"]),
        },
    }


def planned(stored: dict) -> dict:
    view = plan_view(spec.compile_plan(stored))
    return {key: view[key] for key in PLAN_KEYS}


def test_the_vectors_are_what_the_tests_expect():
    assert keys(LIBRARY) == ORDER, "the library lists the presets in the design's order"
    assert [item.key for item in P.PRESETS] == ORDER
    assert len(USABLE) == 8 and len(FOLLOWING) == 3
    assert len(VECTORS["lines"]) >= 16
    assert len({case["name"] for case in LIBRARY}) == len(LIBRARY), "two presets share a name"
    assert [case["key"] for case in LIBRARY if "reason" in case] == ["delivery_spike"]


@pytest.mark.parametrize("case", USABLE, ids=keys(USABLE))
def test_a_preset_is_stored_as_the_design_says(case):
    item = P.preset(case["key"])
    before = copy.deepcopy(item.spec)
    assert spec.check(item.spec) == stored_of(case)
    assert item.spec == before, "check changed the preset it was given"
    assert item.name == case["name"] and item.about == case["about"]


@pytest.mark.parametrize("case", USABLE, ids=keys(USABLE))
def test_a_preset_is_planned(case):
    assert planned(stored_of(case)) == case["plan"]
    assert planned(P.use(case["key"])["spec"]) == case["plan"]


@pytest.mark.parametrize("case", USABLE, ids=keys(USABLE))
def test_checking_a_preset_twice_changes_nothing(case):
    stored = P.use(case["key"])["spec"]
    assert spec.check(stored) == stored
    assert spec.check(json.loads(json.dumps(stored))) == stored


@pytest.mark.parametrize("case", USABLE, ids=keys(USABLE))
def test_using_a_preset_makes_a_paused_copy(case):
    made = P.use(case["key"])
    assert made == {"name": case["name"], "spec": stored_of(case), "status": "paused"}
    assert P.PAUSED == "paused"


@pytest.mark.parametrize("case", USABLE, ids=keys(USABLE))
def test_the_card_of_a_usable_preset(case):
    card = next(item for item in P.library() if item["key"] == case["key"])
    assert card == {
        "key": case["key"],
        "name": case["name"],
        "about": case["about"],
        "usable": True,
        "where": case["line"],
        "spec": stored_of(case),
        "reason": None,
    }


def test_the_card_of_the_delivery_spike_says_why_it_cannot_be_used():
    case = next(item for item in LIBRARY if item["key"] == "delivery_spike")
    card = next(item for item in P.library() if item["key"] == "delivery_spike")
    assert card == {
        "key": "delivery_spike",
        "name": "Delivery spike",
        "about": case["about"],
        "usable": False,
        "where": None,
        "spec": None,
        "reason": case["reason"],
    }
    assert card["reason"] == (
        "Needs the daily delivery file, not set up yet. Volume spike does the same on volume."
    )


@pytest.mark.parametrize("followed", [False, True])
def test_the_delivery_spike_cannot_be_used(followed):
    with pytest.raises(P.Unavailable) as raised:
        P.use("delivery_spike", futures_followed=followed)
    assert raised.value.key == "delivery_spike"
    assert (
        raised.value.message == next(c for c in LIBRARY if c["key"] == "delivery_spike")["reason"]
    )
    assert str(raised.value) == raised.value.message
    assert isinstance(raised.value, ValueError)


@pytest.mark.parametrize("key", ["nope", "", "Long build-up", None, 7, ["long_buildup"]])
def test_a_preset_that_is_not_in_the_library(key):
    for ask in (lambda: P.preset(key), lambda: P.use(key)):
        with pytest.raises(P.Unavailable) as raised:
            ask()
        assert raised.value.message == "That preset is not in the library."
        assert raised.value.key == str(key)


@pytest.mark.parametrize("case", FOLLOWING, ids=keys(FOLLOWING))
def test_a_build_up_is_checked_at_every_15_minute_close_once_the_futures_are_followed(case):
    followed = case["followed"]
    made = P.use(case["key"], futures_followed=True)
    assert made["spec"] == stored_of(case, run=followed["run"])
    assert made["status"] == "paused" and made["name"] == case["name"]
    assert planned(made["spec"]) == followed["plan"]
    card = next(item for item in P.library(futures_followed=True) if item["key"] == case["key"])
    assert card["where"] == followed["line"] and card["spec"] == made["spec"]
    assert P.use(case["key"], futures_followed=False)["spec"] == stored_of(case)


@pytest.mark.parametrize("case", STEADY, ids=keys(STEADY))
def test_the_other_presets_do_not_depend_on_the_futures(case):
    assert P.use(case["key"], futures_followed=True) == P.use(case["key"])
    card = next(item for item in P.library(futures_followed=True) if item["key"] == case["key"])
    assert card["where"] == case["line"]


def test_the_library_is_the_same_cards_whether_or_not_the_futures_are_followed_except_build_ups():
    plain, followed = P.library(), P.library(futures_followed=True)
    changed = [a["key"] for a, b in zip(plain, followed, strict=True) if a != b]
    assert changed == ["long_buildup", "short_buildup", "short_covering"]


@pytest.mark.parametrize("case", VECTORS["lines"], ids=[c["id"] for c in VECTORS["lines"]])
def test_where_and_when_a_screen_looks(case):
    stored = {"universe": case["universe"], "run": case["run"]}
    assert P.where(stored) == case["line"]


@pytest.mark.parametrize("case", USABLE, ids=keys(USABLE))
def test_what_a_copy_changes_never_reaches_the_library(case):
    made = P.use(case["key"])
    made["spec"]["where"]["all" if "all" in made["spec"]["where"] else "any"].clear()
    made["spec"]["alerts"]["columns"].append("junk")
    made["spec"]["target"]["grace"]["n"] = 99
    made["name"] = "changed"
    assert P.use(case["key"]) == {
        "name": case["name"],
        "spec": stored_of(case),
        "status": "paused",
    }
    for card in P.library():
        if card["spec"] is not None:
            card["spec"]["run"]["on"] = "close"
    assert P.library()[0]["spec"]["run"] == {"on": "after_close"}


def test_a_preset_cannot_be_changed_by_a_caller_holding_its_card():
    before = [copy.deepcopy(item.spec) for item in P.PRESETS]
    for card in P.library(futures_followed=True):
        if card["spec"] is not None:
            card["spec"]["where"].clear()
    assert [item.spec for item in P.PRESETS] == before


# ---- the words the owner reads ----------------------------------------------------------------
BANNED = re.compile(r"\b(arm|armed|arming|paper|virtual|live)\b", re.IGNORECASE)


def library_texts():
    for case in LIBRARY:
        for key in ("name", "about", "reason", "line"):
            if key in case:
                yield f"{case['key']}.{key}", case[key]
    for card in P.library():
        for key in ("name", "about", "where", "reason"):
            if card[key] is not None:
                yield f"{card['key']}.{key}", card[key]


TEXTS = list(library_texts())


@pytest.mark.parametrize("label, text", TEXTS, ids=[label for label, _ in TEXTS])
def test_the_library_is_written_for_a_trader(label, text):
    assert all(" " <= ch <= "~" or ch == "·" for ch in text), f"{label}: only plain text"
    assert not BANNED.search(text), f"{label}: a word this platform does not use"
    assert text == " ".join(text.split()), f"{label}: spacing"
    assert text[0].isupper() or text[0].isdigit(), f"{label}: starts with a capital or a figure"
    if label.endswith((".about", ".reason")):
        assert text.endswith("."), f"{label}: a sentence ends with a full stop"


def test_a_name_fits_the_name_a_screen_may_have():
    assert all(0 < len(item.name) <= cat.NAME_MAX for item in P.PRESETS)
