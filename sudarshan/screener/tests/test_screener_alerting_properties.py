"""Properties of alerting.py over random days, restated from the rules rather than from the code.

The oracle keeps its own log of who was told and when, and works out run by run, from that log alone,
what the rules allow. The code under test is handed the rows it asked the service to remember, as the
service would hand them back, so a slip in what it remembers shows up as a later verdict that
disagrees with the log. The whole-history properties then read the decisions' own rows, not the
oracle's.
"""

from __future__ import annotations

import copy
import random
from collections import defaultdict

import pytest

from sudarshan.screener import alerting as al

FIRST_DAY = 20261026
SYMBOLS = [f"S{n}" for n in range(1, 9)]
SEEDS = range(120)


def over_seeds(test):
    """Run a test body once for each seed, naming the seed when it fails."""

    def run() -> None:
        for seed in SEEDS:
            try:
                test(seed)
            except AssertionError as error:
                raise AssertionError(f"seed {seed}: {error}") from error

    run.__name__ = test.__name__
    run.__doc__ = test.__doc__
    return run


def names(symbols) -> tuple[al.Named, ...]:
    return tuple(al.Named(symbol, symbol, "NSE") for symbol in symbols)


def policy_of(rng: random.Random, **fixed) -> al.Policy:
    chosen = {
        "on": rng.random() < 0.9,
        "min_matches": rng.choice([1, 1, 2, 3, 5]),
        "unique_per_day": rng.random() < 0.5,
        "skip_zero": rng.random() < 0.7,
        "tell_exits": rng.random() < 0.4,
        "after_trigger": rng.choice([al.CONTINUE, al.CONTINUE, al.PAUSE]),
        "cooldown_runs": rng.choice([0, 0, 1, 2, 4]),
        "max_per_day": rng.choice([1, 2, 3, 5]),
    }
    return al.Policy(**{**chosen, **fixed})


def days(rng: random.Random) -> list[al.Facts]:
    """A few sessions of runs, numbered from 1 within each session."""
    out, session = [], FIRST_DAY
    for _ in range(rng.randint(1, 3)):
        for k in range(1, rng.randint(2, 14) + 1):
            pool = SYMBOLS[:]
            rng.shuffle(pool)
            entered = pool[: rng.choice([0, 0, 1, 1, 2, 3, 6, 8])]
            exited = [s for s in pool if s not in entered][: rng.choice([0, 0, 1, 2, 4])]
            late = rng.randint(1, 10**9) if rng.random() < 0.15 else None
            matches = rng.randint(0, 12)
            out.append(al.Facts(session, k, matches, names(entered), names(exited), late))
        session += rng.choice([1, 1, 3])
    return out


class Oracle:
    """The rules, read off a log of (session, run, symbol) tellings."""

    def __init__(self, policy: al.Policy) -> None:
        self.policy = policy
        self.log: list[tuple[int, int, str]] = []
        self.paused = False

    def runs_told(self, session: int, symbol: str) -> list[int]:
        return [k for s, k, sym in self.log if s == session and sym == symbol]

    def may_tell(self, session: int, k: int, symbol: str) -> bool:
        done = self.runs_told(session, symbol)
        if not done:
            return True
        if self.policy.unique_per_day or len(done) >= self.policy.max_per_day:
            return False
        return k - done[-1] > self.policy.cooldown_runs

    def verdict(self, facts: al.Facts) -> tuple[str, list[str]]:
        """(why, the entries that may be told) for a run."""
        policy = self.policy
        if not policy.on:
            return al.OFF, []
        if self.paused:
            return al.PAUSED, []
        if facts.matches < policy.min_matches:
            return al.FEW, []
        who = [n.key for n in facts.entered if self.may_tell(facts.session, facts.k, n.key)]
        has_exits = policy.tell_exits and bool(facts.exited)
        if policy.skip_zero and not who and not has_exits:
            return al.NOTHING, who
        return al.TOLD, who

    def record(self, facts: al.Facts, why: str, who: list[str]) -> None:
        if why == al.TOLD:
            self.log += [(facts.session, facts.k, key) for key in who]
            if self.policy.after_trigger == al.PAUSE:
                self.paused = True


def keys(items) -> list[str]:
    return [item.key for item in items]


def check_run(oracle: Oracle, facts: al.Facts, decision: al.Decision) -> None:
    policy = oracle.policy
    why, who = oracle.verdict(facts)
    assert decision.why == why
    assert decision.tell == (why == al.TOLD)
    assert decision.matches == facts.matches
    if why != al.TOLD:
        assert (decision.entered, decision.exited) == ((), ())
        assert (decision.more_entered, decision.more_exited) == (0, 0)
        assert decision.told == {} and decision.pause is False and decision.late_at is None
        assert decision.held == (len(facts.entered) if why == al.NOTHING else 0)
        return
    assert keys(decision.entered) == who[: al.NAMED_MAX]
    assert decision.more_entered == max(0, len(who) - al.NAMED_MAX)
    exits = keys(facts.exited) if policy.tell_exits else []
    assert keys(decision.exited) == exits[: al.NAMED_MAX]
    assert decision.more_exited == max(0, len(exits) - al.NAMED_MAX)
    assert decision.held == len(facts.entered) - len(who)
    assert decision.late_at == facts.late_at
    assert decision.pause == (policy.after_trigger == al.PAUSE)
    assert set(decision.told) == set(who)
    for key, row in decision.told.items():
        before = len(oracle.runs_told(facts.session, key))
        assert row == al.Told(facts.session, before + 1, facts.k)
    for item in decision.entered + decision.exited:
        assert (item.symbol, item.exchange) == (item.key, "NSE")


def play(seed: int, **fixed):
    """Run a random policy over a random few days; yield each run with its decision."""
    rng = random.Random(seed)
    policy = policy_of(rng, **fixed)
    oracle = Oracle(policy)
    told: dict[str, al.Told] = {}
    decisions = []
    for facts in days(rng):
        before = dict(told)
        decision = al.decide(policy, facts, told, paused=oracle.paused)
        assert told == before, "the rows handed in were changed"
        check_run(oracle, facts, decision)
        oracle.record(facts, *oracle.verdict(facts))
        told.update(decision.told)
        decisions.append((facts, decision))
    return policy, oracle, decisions


@over_seeds
def test_every_run_follows_the_rules(seed):
    play(seed)


# ---- the whole day, read from the decisions' own rows ---------------------------------------------------------
def tellings(decisions) -> dict[tuple[int, str], list[int]]:
    """{(session, symbol): the runs that told it}, read from the rows each decision asked to keep."""
    out: dict[tuple[int, str], list[int]] = defaultdict(list)
    for facts, decision in decisions:
        for key in decision.told:
            out[facts.session, key].append(facts.k)
    return out


@over_seeds
def test_an_instrument_is_told_once_a_day_when_the_screen_says_so(seed):
    _, _, decisions = play(seed, unique_per_day=True, on=True)
    assert all(len(runs) == 1 for runs in tellings(decisions).values())


@over_seeds
def test_the_most_a_day_and_the_wait_hold_for_every_instrument(seed):
    policy, _, decisions = play(seed, unique_per_day=False, on=True)
    for (_, symbol), runs in tellings(decisions).items():
        assert len(runs) <= policy.max_per_day, symbol
        assert all(b - a > policy.cooldown_runs for a, b in zip(runs, runs[1:], strict=False)), (
            symbol,
            runs,
        )
        assert runs == sorted(set(runs)), "a run tells an instrument once"


@over_seeds
def test_a_digest_is_made_of_entries_that_entered(seed):
    _, _, decisions = play(seed, on=True)
    for facts, decision in decisions:
        assert set(decision.told) <= set(keys(facts.entered))
        assert set(keys(decision.entered)) <= set(decision.told)
        assert set(keys(decision.exited)) <= set(keys(facts.exited))
        assert len(decision.entered) <= al.NAMED_MAX and len(decision.exited) <= al.NAMED_MAX
        if decision.tell:
            assert len(decision.entered) + decision.more_entered + decision.held == len(
                facts.entered
            )


@over_seeds
def test_the_pause_holds_until_the_owner_resumes(seed):
    _, _, decisions = play(seed, after_trigger=al.PAUSE, on=True)
    first = next((i for i, (_, d) in enumerate(decisions) if d.tell), None)
    if first is None:
        return
    assert all(d.why == al.PAUSED for _, d in decisions[first + 1 :])
    assert not any(d.why == al.PAUSED for _, d in decisions[: first + 1])


@over_seeds
def test_continue_is_never_paused(seed):
    _, _, decisions = play(seed, after_trigger=al.CONTINUE, on=True)
    assert not any(d.pause or d.why == al.PAUSED for _, d in decisions)


@over_seeds
def test_off_is_off_whatever_else_is_true(seed):
    rng = random.Random(seed)
    policy = policy_of(rng, on=False)
    for facts in days(rng):
        for paused in (False, True):
            decision = al.decide(policy, facts, {}, paused=paused)
            assert (decision.tell, decision.why) == (False, al.OFF)


@over_seeds
def test_with_skip_zero_off_a_run_with_enough_matches_is_always_told(seed):
    policy, _, decisions = play(seed, skip_zero=False, after_trigger=al.CONTINUE, on=True)
    for facts, decision in decisions:
        assert decision.tell == (facts.matches >= policy.min_matches)


# ---- what the verdict does not depend on -----------------------------------------------------------------------
def rows_after(decisions) -> dict[str, al.Told]:
    told: dict[str, al.Told] = {}
    for _, decision in decisions:
        told.update(decision.told)
    return told


@over_seeds
def test_the_order_the_rows_arrive_in_does_not_matter(seed):
    policy, _, decisions = play(seed, on=True)
    rng = random.Random(seed + 1)
    told = rows_after(decisions[:-1])
    facts, _ = decisions[-1]
    items = list(told.items())
    for _ in range(4):
        rng.shuffle(items)
        assert al.decide(policy, facts, dict(items), paused=False) == al.decide(
            policy, facts, told, paused=False
        )


@over_seeds
def test_rows_of_other_days_say_nothing_about_today(seed):
    rng = random.Random(seed)
    policy = policy_of(rng, on=True)
    today = al.Facts(FIRST_DAY + 10, rng.randint(1, 20), rng.randint(0, 9), names(SYMBOLS[:5]))
    old = {
        symbol: al.Told(
            rng.choice([FIRST_DAY, FIRST_DAY + 9, FIRST_DAY + 11]), rng.randint(1, 9), 99
        )
        for symbol in SYMBOLS
    }
    assert al.decide(policy, today, old) == al.decide(policy, today, {})
    assert al.decide(policy, today, old) == al.decide(policy, today, None)


@over_seeds
def test_a_row_with_no_tellings_is_the_same_as_none(seed):
    rng = random.Random(seed)
    policy = policy_of(rng, on=True)
    facts = days(rng)[0]
    blank = {symbol: al.Told(facts.session) for symbol in SYMBOLS}
    assert al.decide(policy, facts, blank) == al.decide(policy, facts, {})


@over_seeds
def test_deciding_twice_gives_the_same_and_changes_nothing(seed):
    rng = random.Random(seed)
    policy = policy_of(rng)
    rows = {symbol: al.Told(FIRST_DAY, rng.randint(0, 3), rng.randint(1, 9)) for symbol in SYMBOLS}
    for facts in days(rng):
        before, frozen = copy.deepcopy(rows), copy.deepcopy(facts)
        one = al.decide(policy, facts, rows)
        assert one == al.decide(policy, facts, rows)
        assert rows == before and facts == frozen


# ---- the settings ----------------------------------------------------------------------------------------------
def test_settings_that_make_no_sense_are_refused():
    for bad in (
        {"after_trigger": "stop"},
        {"after_trigger": ""},
        {"min_matches": 0},
        {"min_matches": -3},
        {"cooldown_runs": -1},
        {"max_per_day": 0},
        {"max_per_day": -2},
    ):
        with pytest.raises(ValueError):
            al.Policy(**bad)
    for fine in (
        {"min_matches": 1},
        {"cooldown_runs": 0},
        {"max_per_day": 1},
        {"after_trigger": al.PAUSE},
    ):
        assert al.Policy(**fine)


def test_the_spec_forms_of_the_settings_round_trip():
    rng = random.Random(7)
    for _ in range(50):
        policy = policy_of(rng)
        raw = {
            "on": policy.on,
            "min_matches": policy.min_matches,
            "unique_per_day": policy.unique_per_day,
            "skip_zero": policy.skip_zero,
            "tell_exits": policy.tell_exits,
            "after_trigger": policy.after_trigger,
            "cooldown_runs": policy.cooldown_runs,
            "max_per_day_per_symbol": policy.max_per_day,
            "channels": None,
            "columns": ["ltp"],
        }
        assert al.Policy.of(raw) == policy
    assert al.Policy.of(None) == al.Policy() == al.Policy.of({})
