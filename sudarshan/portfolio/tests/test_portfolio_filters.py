"""core/filters.py (PA6): which holdings a view shows, and the lots held over or under a year.

Four witnesses, as for the other cores. The golden vectors (vectors/filters.json) are worked out by
hand and by an evaluator in make_pa6.py that imports nothing from sudarshan; the plain second
implementation in portfolio_oracle.py (which reads the lots of a replay one at a time) is run on
the same inputs and on hundreds of random tabs replayed from random entries; and identities that
must hold whatever the entries are close the gaps: the lots held over a year and the lots held
under it are the whole line, a choice within one kind only ever adds lines and a choice of
another kind only ever takes them away, and a filter that is read once is read the same again.
"""

from __future__ import annotations

import json
import random
import re
from collections import Counter
from pathlib import Path

import pytest
from portfolio_oracle import filtered_lines, simulate
from portfolio_ts import interfaces, members
from test_portfolio_daylines import crowded
from test_portfolio_ledger import DAYS, DECIMALS, random_rows, repaired
from test_portfolio_vectors import load

from sudarshan.portfolio.core import days, kinds, words
from sudarshan.portfolio.core.allocation import Facts, Owner, allocate
from sudarshan.portfolio.core.daylines import lines_of
from sudarshan.portfolio.core.filters import BadFilter, Filters, Selection, select
from sudarshan.portfolio.core.ledger import replay
from sudarshan.portfolio.core.model import Entry, Ledger
from sudarshan.portfolio.core.valuation import Fixed, Line, Row, tab_totals

VECTORS = Path(__file__).parent / "vectors"
FILE = json.loads((VECTORS / "filters.json").read_text(encoding="utf-8"))
TYPES_TS = Path(__file__).resolve().parents[3] / "frontend/src/sudarshan/portfolio/types.ts"
GEN = load("make_pa6.py")  # the independent evaluator
U = kinds.QTY_SCALE
TAG_SENTENCE = words.filter_tags(kinds.TAG_MAX, kinds.TAGS_MAX)


# -- helpers --------------------------------------------------------------------------------------


def lines_in(case):
    return {int(asset): [Line.of(line) for line in found] for asset, found in case["lines"].items()}


def positions_in(case):
    """The case's lots as a replayed ledger's positions: ``"account/asset": [[day, qty, cost]]``."""
    ledger = Ledger()
    order = 0
    for name, lots in case["positions"].items():
        account, asset = (int(part) for part in name.split("/"))
        position = ledger.position(account, asset)
        for day, qty_u, cost_paise in lots:
            order += 1
            position.add(day, order, qty_u, cost_paise)
    return ledger.positions


def facts_in(case):
    return {int(asset): Facts.of(info) for asset, info in case["facts"].items()}


def as_dicts(lines):
    return {asset: [line.as_dict() for line in found] for asset, found in lines.items()}


def pairs(lines):
    """Which (asset, account) lines there are."""
    return {(asset, line.account_id) for asset, found in lines.items() for line in found}


# -- the golden vectors ---------------------------------------------------------------------------


def test_the_file_has_what_the_design_needs():
    assert {f"FP{n}" for n in range(1, 7)} <= {case["id"] for case in FILE["parse"]}
    assert {f"FE{n}" for n in range(1, 17)} <= {case["id"] for case in FILE["parse"]}
    assert {f"FS{n}" for n in range(1, 16)} <= {case["id"] for case in FILE["select"]}
    for case in (*FILE["parse"], *FILE["select"]):
        assert case["title"] and len(case["why"]) > 25, case["id"]


@pytest.mark.parametrize("case", FILE["parse"], ids=lambda case: case["id"])
def test_a_filter_is_read_as_the_vectors_say(case):
    want = case["expect"]
    if "error" not in want:
        assert Filters.of(case["raw"]).as_dict() == want
        return
    with pytest.raises(BadFilter) as caught:
        Filters.of(case["raw"])
    assert str(caught.value) == want["error"]


@pytest.mark.parametrize("case", FILE["select"], ids=lambda case: case["id"])
def test_the_lines_a_filter_leaves_are_the_vectors(case):
    got = select(
        Filters.of(case["filters"]),
        lines_in(case),
        positions_in(case),
        facts_in(case),
        case["today"],
    )
    assert isinstance(got, Selection)
    assert {str(asset): found for asset, found in as_dicts(got.lines).items()} == (
        case["expect"]["lines"]
    )
    assert got.today_off is case["expect"]["today_off"]


def oracle_inputs(case):
    lines = {int(asset): found for asset, found in case["lines"].items()}
    positions = {name: {"lots": lots} for name, lots in case["positions"].items()}
    facts = {int(asset): info for asset, info in case["facts"].items()}
    return lines, positions, facts


@pytest.mark.parametrize("case", FILE["select"], ids=lambda case: case["id"])
def test_the_naive_oracle_leaves_the_vectors_lines(case):
    lines, positions, facts = oracle_inputs(case)
    got = filtered_lines(case["filters"], lines, positions, facts, case["today"])
    want = case["expect"]
    assert {str(asset): found for asset, found in got["lines"].items()} == want["lines"]
    assert got["today_off"] is want["today_off"]


@pytest.mark.parametrize("case", FILE["select"], ids=lambda case: case["id"])
def test_the_evaluator_leaves_the_vectors_lines(case):
    lines, _, facts = oracle_inputs(case)
    lots = {
        tuple(int(part) for part in name.split("/")): found
        for name, found in case["positions"].items()
    }
    got = GEN.selected(case["filters"], lines, lots, facts, case["today"])
    assert {str(asset): found for asset, found in got["lines"].items()} == case["expect"]["lines"]
    assert got["today_off"] is case["expect"]["today_off"]


# -- reading a filter -----------------------------------------------------------------------------


def test_a_filter_is_read_into_one_order_whoever_wrote_it():
    one = Filters.of({"accounts": [3, 1], "classes": ["gold", "equity"], "tags": ["b", "a"]})
    two = Filters.of({"tags": ("a", "b"), "classes": ("equity", "gold"), "accounts": (1, 3, 1)})
    assert one == two and one.as_dict() == two.as_dict()
    assert one.as_dict() == {
        "accounts": [1, 3],
        "classes": ["equity", "gold"],
        "tags": ["a", "b"],
        "held": None,
        "as_of": None,
    }


def test_the_classes_of_a_filter_are_in_the_order_of_the_kinds():
    got = Filters.of({"classes": list(reversed(kinds.ASSET_CLASSES))})
    assert got.classes == tuple(kinds.ASSET_CLASSES)


def test_a_key_it_does_not_know_is_dropped_and_a_null_is_nothing():
    raw = {
        "view": 3,
        "sort": "value",
        "accounts": None,
        "classes": None,
        "tags": None,
        "held": None,
    }
    assert Filters.of(raw) == Filters()
    assert Filters.of({"accounts": [], "classes": [], "tags": []}) == Filters()


def test_a_filter_read_once_is_read_the_same_again():
    rng = random.Random(3)
    for _ in range(300):
        got = Filters.of(random_filter(rng))
        assert Filters.of(got.as_dict()) == got
        assert Filters.of(got.as_dict()).as_dict() == got.as_dict()


def test_what_a_filter_says_it_is():
    assert Filters().empty and Filters.of({}).empty and not Filters().today_off
    for raw in (
        {"accounts": [1]},
        {"classes": ["gold"]},
        {"tags": ["core"]},
        {"held": "over"},
        {"as_of": 20260105},
    ):
        assert not Filters.of(raw).empty, raw
    assert Filters.of({"held": "under"}).today_off and Filters.of({"as_of": 20260105}).today_off
    for raw in ({"accounts": [1]}, {"classes": ["gold"]}, {"tags": ["core"]}):
        assert not Filters.of(raw).today_off, raw


def test_a_filter_with_nothing_chosen_lets_everything_through():
    nothing = Filters()
    assert nothing.account_ok(1) and nothing.account_ok(10**6)
    assert nothing.asset_ok(Facts(1, "gold")) and nothing.asset_ok(Facts(2, "equity", tags=("x",)))


def test_an_account_is_one_of_those_chosen():
    some = Filters.of({"accounts": [2, 5]})
    assert some.account_ok(2) and some.account_ok(5)
    assert not some.account_ok(1) and not some.account_ok(3)


def test_an_asset_is_of_one_of_the_kinds_and_carries_one_of_the_tags():
    by_kind = Filters.of({"classes": ["gold", "equity"]})
    assert by_kind.asset_ok(Facts(1, "gold")) and by_kind.asset_ok(Facts(1, "equity"))
    assert not by_kind.asset_ok(Facts(1, "crypto"))
    by_tag = Filters.of({"tags": ["core", "income"]})
    assert by_tag.asset_ok(Facts(1, "gold", tags=("x", "income")))
    assert not by_tag.asset_ok(Facts(1, "gold", tags=("x",))) and not by_tag.asset_ok(
        Facts(1, "gold")
    )
    both = Filters.of({"classes": ["gold"], "tags": ["core"]})
    assert both.asset_ok(Facts(1, "gold", tags=("core",)))
    assert not both.asset_ok(Facts(1, "equity", tags=("core",)))
    assert not both.asset_ok(Facts(1, "gold", tags=("income",)))


def test_a_tag_is_exact_apart_from_the_spaces_round_it():
    got = Filters.of({"tags": ["core", " core ", "Core", "high growth"]})
    assert got.tags == ("Core", "core", "high growth")
    assert not Filters.of({"tags": ["core"]}).asset_ok(Facts(1, "gold", tags=("Core",)))
    assert Filters.of({"tags": [" core"]}).asset_ok(Facts(1, "gold", tags=("core",)))
    assert Filters.of({"tags": ["high growth"]}).asset_ok(Facts(1, "gold", tags=("high growth",)))


def test_a_tag_may_be_in_any_script_and_as_long_as_the_platform_allows():
    income = chr(0x0906) + chr(0x092F)  # "income" in Hindi
    assert Filters.of({"tags": [income]}).tags == (income,)
    assert Filters.of({"tags": ["x" * kinds.TAG_MAX]}).tags == ("x" * kinds.TAG_MAX,)
    many = [f"t{n}" for n in range(kinds.TAGS_MAX)]
    assert Filters.of({"tags": many}).tags == tuple(sorted(many))
    assert Filters.of({"tags": [*many, *many, " t0"]}).tags == tuple(
        sorted(many)
    )  # a repeat is one
    with pytest.raises(BadFilter):
        Filters.of({"tags": [*many, "one more"]})


def test_the_first_and_the_last_day_a_filter_may_name():
    assert Filters.of({"as_of": days.FIRST_DAY}).as_of == days.FIRST_DAY
    assert Filters.of({"as_of": days.LAST_DAY}).as_of == days.LAST_DAY
    assert Filters.of({"as_of": 20240229}).as_of == 20240229
    for day in (days.FIRST_DAY - 1, days.LAST_DAY + 1, 20250229, 0, -20260105, 20260100):
        with pytest.raises(BadFilter) as caught:
            Filters.of({"as_of": day})
        assert str(caught.value) == words.FILTER_DAY, day


BAD = [
    ({"accounts": [0]}, words.FILTER_ACCOUNT),
    ({"accounts": [-1]}, words.FILTER_ACCOUNT),
    ({"accounts": ["1"]}, words.FILTER_ACCOUNT),
    ({"accounts": [True]}, words.FILTER_ACCOUNT),
    ({"accounts": [1.0]}, words.FILTER_ACCOUNT),
    ({"accounts": [None]}, words.FILTER_ACCOUNT),
    ({"accounts": [[1]]}, words.FILTER_ACCOUNT),
    ({"accounts": [1, 2, 0]}, words.FILTER_ACCOUNT),
    ({"accounts": [0], "classes": "equity"}, words.FILTER_ACCOUNT),
    ({"classes": ["x"], "tags": "core"}, words.FILTER_CLASS),
    ({"accounts": 5}, words.FILTER_SHAPE),
    ({"accounts": 0}, words.FILTER_SHAPE),
    ({"accounts": False}, words.FILTER_SHAPE),
    ({"classes": ""}, words.FILTER_SHAPE),
    ({"tags": {}}, words.FILTER_SHAPE),
    ({"tags": {"core"}}, words.FILTER_SHAPE),
    ({"accounts": "1"}, words.FILTER_SHAPE),
    ({"accounts": {"1": 1}}, words.FILTER_SHAPE),
    ({"classes": ["shares"]}, words.FILTER_CLASS),
    ({"classes": [""]}, words.FILTER_CLASS),
    ({"classes": ["Equity"]}, words.FILTER_CLASS),
    ({"classes": [5]}, words.FILTER_CLASS),
    ({"classes": [None]}, words.FILTER_CLASS),
    ({"classes": [["equity"]]}, words.FILTER_CLASS),
    ({"classes": "equity"}, words.FILTER_SHAPE),
    ({"tags": "core"}, words.FILTER_SHAPE),
    ({"tags": [5]}, words.FILTER_SHAPE),
    ({"tags": [None]}, words.FILTER_SHAPE),
    ({"tags": [" "]}, TAG_SENTENCE),
    ({"tags": [""]}, TAG_SENTENCE),
    ({"tags": ["a,b"]}, TAG_SENTENCE),
    ({"tags": [","]}, TAG_SENTENCE),
    ({"tags": ["x" * (kinds.TAG_MAX + 1)]}, TAG_SENTENCE),
    ({"tags": [f"t{n}" for n in range(kinds.TAGS_MAX + 1)]}, TAG_SENTENCE),
    ({"held": "sideways"}, words.FILTER_HELD),
    ({"held": ""}, words.FILTER_HELD),
    ({"held": "OVER"}, words.FILTER_HELD),
    ({"held": 0}, words.FILTER_HELD),
    ({"held": False}, words.FILTER_HELD),
    ({"held": ["over"]}, words.FILTER_HELD),
    ({"as_of": "20261027"}, words.FILTER_DAY),
    ({"as_of": True}, words.FILTER_DAY),
    ({"as_of": 20261027.0}, words.FILTER_DAY),
    ({"as_of": [20261027]}, words.FILTER_DAY),
    ({"as_of": 1}, words.FILTER_DAY),
    ([], words.FILTER_SHAPE),
    ([1, 2], words.FILTER_SHAPE),
    ("accounts", words.FILTER_SHAPE),
    (5, words.FILTER_SHAPE),
    (None, words.FILTER_SHAPE),
]


@pytest.mark.parametrize(("raw", "message"), BAD, ids=[str(n) for n in range(len(BAD))])
def test_a_filter_that_cannot_be_read_is_refused_in_the_traders_words(raw, message):
    with pytest.raises(BadFilter) as caught:
        Filters.of(raw)
    assert str(caught.value) == message
    assert caught.value.args == (message,)


def test_the_first_thing_wrong_is_the_one_said():
    bad = {"accounts": [0], "classes": ["x"], "tags": ["a,b"], "held": "x", "as_of": 1}
    for key, message in (
        ("accounts", words.FILTER_ACCOUNT),
        ("classes", words.FILTER_CLASS),
        ("tags", TAG_SENTENCE),
        ("held", words.FILTER_HELD),
        ("as_of", words.FILTER_DAY),
    ):
        with pytest.raises(BadFilter) as caught:
            Filters.of(bad)
        assert str(caught.value) == message
        del bad[key]
    assert Filters.of(bad) == Filters()


NOT_HERE = re.compile(
    r"\b(arm|armed|arming|paper|virtual|live|real-time|http|exception|traceback|none|nan|json)\b",
    re.IGNORECASE,
)


def test_the_refusals_are_plain_sentences_a_trader_can_act_on():
    assert issubclass(BadFilter, ValueError)
    sentences = [
        words.FILTER_SHAPE,
        words.FILTER_ACCOUNT,
        words.FILTER_CLASS,
        words.FILTER_HELD,
        words.FILTER_DAY,
        TAG_SENTENCE,
    ]
    for sentence in sentences:
        assert sentence.isascii() and sentence == sentence.strip() and "  " not in sentence
        assert sentence.endswith(".") and not sentence.endswith("..")
        assert not NOT_HERE.search(sentence), sentence
        assert "{" not in sentence and not re.search(r"\b[45]\d\d\b", sentence)
    assert len(set(sentences)) == len(sentences)
    assert str(kinds.TAG_MAX) in TAG_SENTENCE and str(kinds.TAGS_MAX) in TAG_SENTENCE


def test_a_filter_has_the_fields_the_phone_sends():
    found = interfaces(TYPES_TS.read_text(encoding="utf-8"))
    assert list(Filters().as_dict()) == members(found["Filters"])
    assert tuple(kinds.HELD_FILTERS) == ("over", "under")


# -- the lines a filter leaves, through the ledger -------------------------------------------------

KINDS_OF_ASSET = ["equity", "gold", "mf_equity", "crypto"]
TAGS = ["core", "income", "growth"]
#: Days the lots are judged on: before, between and after the anniversaries of the lots below.
JUDGED = [
    20250601,
    20251201,
    20260115,
    20260201,
    20260215,
    20260301,
    20260315,
    20260401,
    20260501,
    20260520,
    20260601,
    20270101,
]


def random_filter(rng):
    raw = {}
    if rng.random() < 0.5:
        raw["accounts"] = rng.sample([1, 2, 3], rng.randint(0, 3))
    if rng.random() < 0.4:
        raw["classes"] = rng.sample(KINDS_OF_ASSET, rng.randint(0, 3))
    if rng.random() < 0.4:
        raw["tags"] = rng.sample([" core", "income ", "growth", "nothing"], rng.randint(0, 3))
    if rng.random() < 0.6:
        raw["held"] = rng.choice(["over", "under"])
    if rng.random() < 0.25:
        raw["as_of"] = rng.choice(JUDGED)
    return raw


class World:
    """A sound random ledger replayed up to a day: its lines, its positions, its two assets."""

    def __init__(self, rng):
        if rng.random() < 0.4:  # a crowded day, with positions sold out on it
            self.rows, self.day = crowded(rng)
        else:
            self.rows = repaired(random_rows(rng, rng.randint(2, 40)))
            # Entries up to the day; a later day only ages the lots.
            self.day = rng.choice(JUDGED) if rng.random() < 0.5 else rng.choice(DAYS)
        self.ledger = replay([Entry.of(r) for r in self.rows], as_of=self.day, decimals=DECIMALS)
        self.lines = lines_of(self.ledger, self.day, {})
        self.facts = {
            asset: Facts(
                asset,
                rng.choice(KINDS_OF_ASSET),
                tags=tuple(rng.sample(TAGS, rng.randint(0, 3))),
            )
            for asset in (1, 2)
        }

    @property
    def positions(self):
        return self.ledger.positions

    def select(self, raw, on=None):
        on = self.day if on is None else on
        return select(Filters.of(raw), self.lines, self.positions, self.facts, on)


def test_the_engine_the_evaluator_and_the_oracle_agree_on_random_tabs():
    rng = random.Random(20261030)
    seen = Counter()
    for _ in range(300):
        world = World(rng)
        on = rng.choice(JUDGED)
        lines = as_dicts(world.lines)
        positions = simulate(world.rows, world.day, DECIMALS)["positions"]
        facts = {
            a: {"asset_class": f.asset_class, "tags": list(f.tags)} for a, f in world.facts.items()
        }
        lots = {
            (account, asset): [[lot.day, lot.qty_u, lot.cost_paise] for lot in position.lots]
            for (account, asset), position in world.positions.items()
            if asset is not None
        }
        for _ in range(5):
            raw = random_filter(rng)
            got = world.select(raw, on)
            normal = Filters.of(raw)
            want = filtered_lines(raw, lines, positions, facts, on)
            assert as_dicts(got.lines) == want["lines"], (raw, on)
            assert got.today_off is want["today_off"]
            again = GEN.selected(normal.as_dict(), lines, lots, facts, on)
            assert as_dicts(got.lines) == again["lines"], (raw, on)
            assert got.today_off is again["today_off"]
            seen["asked"] += 1
            seen["kept"] += bool(got.lines)
            seen["over"] += normal.held == "over" and bool(got.lines)
            seen["under"] += normal.held == "under" and bool(got.lines)
            seen["accounts cut"] += bool(normal.accounts) and pairs(got.lines) != pairs(world.lines)
            seen["kinds"] += bool(normal.classes or normal.tags)
            seen["a day"] += normal.as_of is not None
            seen["part of a line"] += any(
                line.qty_u < original.qty_u
                for asset, found in got.lines.items()
                for line in found
                for original in world.lines[asset]
                if original.account_id == line.account_id
            )
    # The generator must exercise what it is there for, or the comparison proves nothing.
    assert seen["kept"] >= 700 and seen["over"] >= 150 and seen["under"] >= 150
    assert seen["accounts cut"] >= 100 and seen["kinds"] >= 400 and seen["a day"] >= 200
    assert seen["part of a line"] >= 150


def test_no_filter_leaves_every_line_as_it_is_and_reads_nothing_else():
    rng = random.Random(21)
    for _ in range(100):
        world = World(rng)
        got = select(Filters(), world.lines, {}, {}, world.day)  # no lots and no facts needed
        assert got.lines == world.lines and got.today_off is False
        assert list(got.lines) == list(world.lines)


def test_the_lots_over_a_year_and_the_lots_under_it_are_the_whole_line():
    rng = random.Random(22)
    split = Counter()
    for _ in range(200):
        world = World(rng)
        for on in rng.sample(JUDGED, 3):
            over = world.select({"held": "over"}, on).lines
            under = world.select({"held": "under"}, on).lines
            for asset, found in world.lines.items():
                for line in found:
                    up = [x for x in over.get(asset, []) if x.account_id == line.account_id]
                    down = [x for x in under.get(asset, []) if x.account_id == line.account_id]
                    assert len(up) <= 1 and len(down) <= 1
                    qty = sum(x.qty_u for x in (*up, *down))
                    cost = sum(x.cost_paise for x in (*up, *down))
                    assert (qty, cost) == (line.qty_u, line.cost_paise)
                    assert all(x.over_year_qty_u == x.qty_u for x in up)
                    assert all(x.over_year_qty_u == 0 for x in down)
                    assert all(x.open_qty_u == x.qty_u for x in (*up, *down))
                    assert all(x.in_today_paise == x.out_today_paise == 0 for x in (*up, *down))
                    split["both"] += bool(up and down)
                    split["sold out"] += not line.qty_u
    assert split["both"] >= 100 and split["sold out"] >= 20


def test_the_lots_over_a_year_are_the_lines_own_over_year_units_on_the_same_day():
    rng = random.Random(23)
    checked = 0
    for _ in range(200):
        world = World(rng)
        over = world.select({"held": "over"}).lines  # judged on the day the lines are for
        for asset, found in world.lines.items():
            for line in found:
                mine = [x for x in over.get(asset, []) if x.account_id == line.account_id]
                assert sum(x.qty_u for x in mine) == line.over_year_qty_u
                checked += line.over_year_qty_u > 0
    assert checked >= 50


def test_a_choice_within_one_kind_only_adds_lines_and_a_choice_of_another_only_takes_them_away():
    rng = random.Random(24)
    pools = {"accounts": [1, 2, 3], "classes": KINDS_OF_ASSET, "tags": TAGS}
    for _ in range(200):
        world = World(rng)
        chosen = {key: rng.sample(pool, rng.randint(1, len(pool))) for key, pool in pools.items()}
        alone = {}
        for key, value in chosen.items():
            alone[key] = pairs(world.select({key: value}).lines)
            fewer = pairs(world.select({key: value[:1]}).lines)
            assert fewer <= alone[key]  # one more choice of the same kind: more lines
        both = pairs(world.select(chosen).lines)
        assert both == alone["accounts"] & alone["classes"] & alone["tags"]
        for left, right in (("accounts", "classes"), ("classes", "tags"), ("accounts", "tags")):
            two = pairs(world.select({left: chosen[left], right: chosen[right]}).lines)
            assert two == alone[left] & alone[right]
            assert both <= two  # one more kind of choice: fewer lines


def test_the_other_choices_and_the_lots_commute():
    rng = random.Random(25)
    for _ in range(200):
        world = World(rng)
        held = rng.choice(["over", "under"])
        on = rng.choice(JUDGED)
        accounts = rng.sample([1, 2], rng.randint(1, 2))
        both = world.select({"held": held, "accounts": accounts}, on).lines
        first = world.select({"held": held}, on).lines
        cut = {
            asset: [line for line in found if line.account_id in accounts]
            for asset, found in first.items()
        }
        assert both == {asset: found for asset, found in cut.items() if found}


def test_a_choice_that_is_made_twice_changes_nothing():
    rng = random.Random(26)
    for _ in range(200):
        world = World(rng)
        raw = {
            key: value
            for key, value in random_filter(rng).items()
            if key in ("accounts", "classes", "tags")
        }
        once = world.select(raw)
        again = select(Filters.of(raw), once.lines, world.positions, world.facts, world.day)
        assert again.lines == once.lines and again.today_off == once.today_off


def test_the_lines_and_the_assets_keep_their_order():
    rng = random.Random(27)
    for _ in range(200):
        world = World(rng)
        got = world.select(random_filter(rng), rng.choice(JUDGED))
        assets = list(world.lines)
        assert [asset for asset in assets if asset in got.lines] == list(got.lines)
        for asset, found in got.lines.items():
            order = [line.account_id for line in world.lines[asset]]
            kept = [line.account_id for line in found]
            assert kept == [account for account in order if account in kept]


def test_a_line_sold_out_is_never_among_the_lots_and_a_day_alone_changes_no_line():
    rng = random.Random(28)
    for _ in range(200):
        world = World(rng)
        for held in ("over", "under"):
            for found in world.select({"held": held}).lines.values():
                assert all(line.qty_u > 0 for line in found)
        day = world.select({"as_of": rng.choice(JUDGED)})
        assert day.lines == world.lines and day.today_off is True


def test_today_is_off_exactly_when_the_lots_or_a_past_day_are_asked_for():
    rng = random.Random(29)
    for _ in range(300):
        world = World(rng)
        raw = random_filter(rng)
        want = raw.get("held") is not None or raw.get("as_of") is not None
        assert world.select(raw).today_off is want
        assert Filters.of(raw).today_off is want


def test_a_class_or_tag_is_only_read_when_one_is_asked_for():
    rng = random.Random(30)
    for _ in range(100):
        world = World(rng)
        for raw in ({"accounts": [1]}, {"held": "over"}, {"as_of": 20260105}, {}):
            got = select(Filters.of(raw), world.lines, world.positions, {}, world.day)
            assert got == world.select(raw)


def test_a_filtered_view_can_be_valued_and_divided_like_any_other():
    rng = random.Random(31)
    owners = {1: Owner("Demat A", 1), 2: Owner("Demat B", 2), 3: Owner("Demat C", 2)}
    people = {1: "Me", 2: "Member B"}
    counted = Counter()
    for _ in range(200):
        world = World(rng)
        got = world.select(random_filter(rng), rng.choice(JUDGED))
        rows, holdings, want = [], [], 0
        for asset, found in got.lines.items():
            price = (
                rng.choice([10_000, 12_345, 5_000_000, 987_654_321]) if rng.random() < 0.8 else None
            )
            row = Row(kinds.TODAY_MODES[2] if got.today_off else "ok", price, None, tuple(found))
            rows.append(row)
            holdings.append((world.facts[asset], row))
            for line in found:
                if price is None:
                    want += line.cost_paise
                else:
                    want += (2 * line.qty_u * price + 10**8) // (2 * 10**8)
        value = tab_totals(rows, Fixed()).value_paise
        assert value == want
        for by in ("class", "sector", "mcap", "account", "member"):
            bars = allocate(by, holdings, owners, people)
            assert sum(bar.value_paise for bar in bars) == value, by
        counted["with lines"] += bool(got.lines)
    assert counted["with lines"] >= 100
