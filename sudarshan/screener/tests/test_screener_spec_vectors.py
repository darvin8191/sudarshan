"""screener.spec against the hand-written golden vectors (vectors/spec.json).

The vectors say what a screen is stored as, what the engine is given and the words the owner reads
when a screen cannot run. They were written from the design, not from the code: a change that makes
one of them fail changes what the phone's builder shows, so the vector is changed on purpose or the
code is put back.
"""

from __future__ import annotations

import copy
import json
import random
import re
from pathlib import Path

import pytest
from screener_helpers import plan as plan_view
from screener_helpers import pointer

from sudarshan.screener import catalog as cat
from sudarshan.screener import spec
from sudarshan.screener.types import MAX_AGO

VECTORS = json.loads((Path(__file__).parent / "vectors" / "spec.json").read_text(encoding="utf-8"))
CASES = VECTORS["cases"]
ACCEPTED = [case for case in CASES if "error" not in case]
REFUSED = [case for case in CASES if "error" in case]


def screen_of(case: dict) -> dict:
    """The case's screen: the base with the case laid over it."""
    screen = copy.deepcopy(VECTORS["base"])
    for key in case.get("drop", ()):
        screen.pop(key, None)
    screen.update(copy.deepcopy(case["screen"]))
    return screen


def ids(cases):
    return [case["id"] for case in cases]


def test_the_vectors_are_what_the_tests_expect():
    assert len(set(ids(CASES))) == len(CASES), "two vectors share an id"
    assert len(ACCEPTED) >= 26 and len(REFUSED) >= 53
    for case in CASES:
        assert case.get("note"), f"{case['id']} has no note saying what it pins"


@pytest.mark.parametrize("case", ACCEPTED, ids=ids(ACCEPTED))
def test_a_screen_is_stored_and_planned(case):
    fields = screen_of(case)
    before = copy.deepcopy(fields)
    stored = spec.check(fields)
    assert fields == before, "check changed the screen it was given"
    for path, expected in case.get("stored", ()):
        assert pointer(stored, path) == expected, f"{case['id']} stored {path}"
    view = plan_view(spec.compile_plan(fields))
    for path, expected in case.get("plan", ()):
        assert pointer(view, path) == expected, f"{case['id']} plan {path}"


@pytest.mark.parametrize("case", ACCEPTED, ids=ids(ACCEPTED))
def test_checking_twice_changes_nothing(case):
    """The stored form is a fixed point, survives JSON, and plans the same."""
    fields = screen_of(case)
    stored = spec.check(fields)
    again = spec.check(stored)
    assert again == stored
    assert spec.check(json.loads(json.dumps(stored))) == stored
    assert plan_view(spec.compile_plan(stored)) == plan_view(spec.compile_plan(fields))
    assert spec.check(copy.deepcopy(fields)) == stored


@pytest.mark.parametrize("case", REFUSED, ids=ids(REFUSED))
def test_a_screen_that_cannot_run_is_refused_in_words(case):
    fields = screen_of(case)
    before = copy.deepcopy(fields)
    with pytest.raises(spec.SpecError) as raised:
        spec.check(fields)
    assert fields == before
    assert raised.value.field == case["error"]["field"], case["id"]
    assert raised.value.message == case["error"]["message"], case["id"]
    assert str(raised.value) == raised.value.message
    with pytest.raises(spec.SpecError):
        spec.compile_plan(fields)


# Words a trader reads (CLAUDE.md): a sentence that names what to change, nothing a developer says.
_DEVELOPER_WORDS = re.compile(
    r"\b(arm|armed|arming|exception|traceback|http|json|null|nan|object|paper|virtual|error code|"
    r"status)\b",
    re.IGNORECASE,
)


@pytest.mark.parametrize("case", REFUSED, ids=ids(REFUSED))
def test_every_refusal_is_a_sentence_for_a_trader(case):
    message = case["error"]["message"]
    assert message.endswith((".", ")")), message
    assert message[0].isupper() or message[0] in "'\"", message
    assert message.isascii(), "no icons, no stray symbols"
    assert not _DEVELOPER_WORDS.search(message), message
    assert len(message) < 260, "one sentence or two, not a paragraph"


def test_the_design_strings_are_verbatim():
    """Six messages the design (3.2) quotes; the builder shows them as written."""
    by_id = {case["id"]: case["error"] for case in REFUSED}
    assert by_id["SE1"]["message"] == "Turn on at least one condition."
    assert by_id["SE3"]["message"] == (
        "A cross compares two lines on the same candles. Pick the same timeframe on both sides."
    )
    assert by_id["SE4"]["message"] == (
        "Today's first candle is for minute candles. For day candles, use 1 candle ago."
    )
    assert by_id["SE5"]["message"] == "Groups can go three levels deep."
    assert by_id["SE7"]["message"] == (
        "EMA 200 on 60-min candles needs 600 candles of history before it is ready; "
        "the screener keeps 441. Use a shorter length or a smaller timeframe."
    )
    assert by_id["SE6"]["message"].startswith("NIFTY 500 is checked after the close (16:05).")


def test_every_off_line_stays_in_the_stored_form_and_out_of_the_plan():
    case = next(item for item in ACCEPTED if item["id"] == "SP8")
    fields = screen_of(case)
    stored = spec.check(fields)
    assert [item["on"] for item in stored["where"]["all"]] == [True, False]
    assert [c.id for c in spec.compile_plan(fields).clauses] == ["a"]


# ---- never a crash, only words ----------------------------------------------------------------
_JUNK = [None, True, False, 0, -1, 7, 10**30, 1.5, float("nan"), float("inf"), "", "zzz", "5", [], {},
         [None], {"a": 1}, [[]], {"all": []}, {"num": None}, {"field": None}, {"ind": []}, "close"]  # fmt: skip


def _paths(node, prefix=()):
    """Every place in a nested JSON document that can be replaced."""
    yield prefix
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _paths(value, (*prefix, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _paths(value, (*prefix, index))


def _replace(document, path, value):
    if not path:
        return value
    document = copy.deepcopy(document)
    node = document
    for step in path[:-1]:
        node = node[step]
    if value is _DROP:
        if isinstance(node, dict):
            node.pop(path[-1], None)
        else:
            node.pop(path[-1])
    else:
        node[path[-1]] = copy.deepcopy(value)
    return document


_DROP = object()


def test_a_damaged_screen_is_refused_in_words_never_a_crash():
    """Any one place replaced by junk or removed: either a screen that is sound, or a SpecError."""
    rng = random.Random(20261027)
    seeds = [screen_of(case) for case in ACCEPTED[:12]]
    tried = refused = 0
    for seed in seeds:
        places = list(_paths(seed))[1:]
        for _ in range(140):
            path = rng.choice(places)
            value = rng.choice([*_JUNK, _DROP])
            try:
                damaged = _replace(seed, path, value)
            except (KeyError, IndexError):
                continue
            tried += 1
            try:
                stored = spec.check(damaged)
            except spec.SpecError as error:
                refused += 1
                assert error.field and error.message and error.message.endswith((".", ")"))
                continue
            assert spec.check(stored) == stored, "an accepted screen must be a fixed point"
            spec.compile_plan(stored)
    assert tried > 1000 and refused > 300


def test_a_screen_that_is_not_a_dict_is_refused():
    for junk in (None, [], "x", 5, ()):
        with pytest.raises(spec.SpecError) as raised:
            spec.check(junk)
        assert raised.value.field == "screen"


def test_booleans_are_not_numbers():
    base = copy.deepcopy(VECTORS["base"])
    base["where"] = {"all": [{"left": {"field": "close"}, "op": "gt", "right": {"num": True}}]}
    with pytest.raises(spec.SpecError) as raised:
        spec.check(base)
    assert raised.value.field == "c1.right"
    base["where"] = {
        "all": [{"left": {"ind": "sma", "p": {"length": True}}, "op": "gt", "right": {"num": "1"}}]
    }
    with pytest.raises(spec.SpecError):
        spec.check(base)


def test_numbers_may_come_as_json_numbers_or_text():
    base = copy.deepcopy(VECTORS["base"])
    results = set()
    for typed in ("2500.5", 2500.5, "2,500.50", " 2500.50 "):
        base["where"] = {"all": [{"left": {"field": "close"}, "op": "gt", "right": {"num": typed}}]}
        results.add(json.dumps(spec.check(base)["where"], sort_keys=True))
    assert len(results) == 1


def test_a_number_with_too_many_places_or_too_large_is_refused():
    base = copy.deepcopy(VECTORS["base"])
    for typed in ("1.0000001", "1e13", "-1e13", "nan", "inf", "", "1.2.3"):
        base["where"] = {"all": [{"left": {"field": "close"}, "op": "gt", "right": {"num": typed}}]}
        with pytest.raises(spec.SpecError):
            spec.check(base)


def test_the_limits_in_the_catalogue_are_the_ones_the_vectors_pin():
    """SE8 (21 lines), SE9 (13 indicators), SE5 (4 deep) and SE35 (101 back) sit just past them."""
    assert (cat.MAX_CLAUSES, cat.MAX_INSTANCES, cat.MAX_DEPTH, MAX_AGO) == (20, 12, 3, 100)


def test_a_list_or_object_where_a_name_belongs_is_refused_not_a_crash():
    """Found by fuzzing: a table lookup with an unhashable value raised TypeError, not words."""
    oi_where = {"all": [{"left": {"oi": {"num": None}}, "op": "is", "args": {"name": "none"}}]}
    cases = [
        ({"universe": {"kind": "index_list", "id": []}}, "universe"),
        ({"universe": {"kind": "index_list", "id": {"a": 1}}}, "universe"),
        ({"alerts": {"columns": [{}]}}, "alerts"),
        ({"alerts": {"columns": [["ltp"]]}}, "alerts"),
        ({"universe": {"kind": "fno_futures"}, "where": oi_where}, "c1.left"),
    ]
    for change, field in cases:
        screen = copy.deepcopy(VECTORS["base"])
        screen.update(copy.deepcopy(change))
        with pytest.raises(spec.SpecError) as raised:
            spec.check(screen)
        assert raised.value.field == field, change
