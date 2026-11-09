"""Whether a run is worth telling (M9.18): the screen's alert policy. Pure.

The screener decides here and nowhere else whether a run gets a digest. Everything after that
belongs to sudarshan-alerts: the record, the once-only claim, the Telegram lane, the push and the
delivery statuses (the digest is built by ``digests`` from a ``Decision``). Nothing in this module
reads a clock, a database or the feed: the run's facts and what the day has already told come in as
arguments, and the decision, with the rows to remember, goes out as a value.

The rules, each one a decision the owner reads on his phone:

* One digest a run at most. It names the instruments that entered (at most ``NAMED_MAX``, the best
  ranked first, then "and N more") and, when the screen asks for them, those that left.
* Only entries and exits are told. A member that came back (``BACK``), one that stopped matching
  for now (``MISSING``) and a dismissed one that was forgotten (``FORGOTTEN``) are not.
* ``min_matches`` counts everything that matches now, not just the entries.
* ``unique_per_day``: an instrument is told once a trading day. The day is the session
  (``Facts.session``), never a calendar date; a row of another session says nothing about today.
  Turned off, ``max_per_day`` caps how often one instrument is told, and ``cooldown_runs`` is how
  many runs of the screen must pass after one telling before that instrument is told again.
  The list still holds an instrument the digest does not tell: telling and keeping are separate.
* ``skip_zero``: a run with nothing to name is not told. Off, a run with enough matches is told
  even when nothing entered ("0 entered, 7 match now").
* ``after_trigger = pause``: the first digest told pauses the screen's alerts until the owner
  resumes them, however many runs and days that takes. The screen keeps checking.
* Every entry of a digest counts as told, named or not: the ones behind "and N more" are on the
  Screener page, and told means the owner has been given the chance to see them.
* A run made late (after a restart) is still told, marked with when it was made.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import NamedTuple

from . import catalog as cat
from . import membership as mem

#: The most instruments a digest names on each side; the rest are counted ("and 7 more").
NAMED_MAX = 5
CONTINUE, PAUSE = cat.AFTER_TRIGGER

# Why a run was told or not.
TOLD = "told"
OFF = "off"  # the screen's alerts are switched off
PAUSED = "paused"  # paused after the first alert, until the owner resumes them
FEW = "few"  # fewer matches than the screen asks for
NOTHING = "nothing"  # nothing new to name


class Named(NamedTuple):
    """An instrument a digest names."""

    key: str
    symbol: str
    exchange: str


@dataclass(frozen=True, slots=True)
class Told:
    """What one trading day has told about one instrument (the ``alert_state`` row)."""

    session: int
    count: int = 0
    #: The run (its number within the session) that told it last.
    last: int | None = None


@dataclass(frozen=True, slots=True)
class Policy:
    """The screen's ``alerts`` block, as far as it decides whether to tell."""

    on: bool = True
    min_matches: int = 1
    unique_per_day: bool = True
    skip_zero: bool = True
    tell_exits: bool = False
    after_trigger: str = CONTINUE
    cooldown_runs: int = 0
    max_per_day: int = cat.DEFAULT_ALERT_CAP

    def __post_init__(self) -> None:
        if (
            self.after_trigger not in cat.AFTER_TRIGGER
            or self.min_matches < 1
            or self.cooldown_runs < 0
            or self.max_per_day < 1
        ):
            raise ValueError(f"not an alert policy: {self}")

    @classmethod
    def of(cls, raw: Mapping | None) -> Policy:
        """From the stored ``alerts`` block (``spec.check``'s form); None is the defaults."""
        if raw is None:
            return cls()
        known = cls()
        return cls(
            on=raw.get("on", known.on),
            min_matches=raw.get("min_matches", known.min_matches),
            unique_per_day=raw.get("unique_per_day", known.unique_per_day),
            skip_zero=raw.get("skip_zero", known.skip_zero),
            tell_exits=raw.get("tell_exits", known.tell_exits),
            after_trigger=raw.get("after_trigger", known.after_trigger),
            cooldown_runs=raw.get("cooldown_runs", known.cooldown_runs),
            max_per_day=raw.get("max_per_day_per_symbol", known.max_per_day),
        )


@dataclass(frozen=True, slots=True)
class Facts:
    """What a run produced."""

    #: The trading day (the session key), and the run's number within it (the run key's last part).
    session: int
    k: int
    #: How many instruments match now.
    matches: int
    #: Who entered and who left, each in the order the Screener page ranks them.
    entered: tuple[Named, ...] = ()
    exited: tuple[Named, ...] = ()
    #: When a run made late after a restart was made (epoch ms); None for one made on time.
    late_at: int | None = None


@dataclass(frozen=True, slots=True)
class Decision:
    """The verdict on one run, and what to remember if it was told."""

    tell: bool
    why: str
    #: Who the digest names, and how many more it counts.
    entered: tuple[Named, ...] = ()
    more_entered: int = 0
    exited: tuple[Named, ...] = ()
    more_exited: int = 0
    #: Entries not told again because of the day's rules (already told, the daily cap, the wait).
    held: int = 0
    matches: int = 0
    late_at: int | None = None
    #: The screen's alerts pause from this digest on (``after_trigger = pause``).
    pause: bool = False
    #: The ``alert_state`` rows to write: every instrument this digest counts as told.
    told: Mapping[str, Told] = field(default_factory=dict)


def named(changes: Iterable[mem.Change]) -> tuple[tuple[Named, ...], tuple[Named, ...]]:
    """(entered, exited) of a run from membership's transitions, each in the order given."""
    entered, exited = [], []
    for change in changes:
        found = Named(change.key, change.row.symbol, change.row.exchange)
        if change.event == mem.EV_ENTERED:
            entered.append(found)
        elif change.event == mem.EV_EXITED:
            exited.append(found)
    return tuple(entered), tuple(exited)


def allowed(policy: Policy, facts: Facts, row: Told | None) -> bool:
    """Whether an instrument that entered may be told today."""
    if row is None or row.session != facts.session or row.count == 0:
        return True
    if policy.unique_per_day or row.count >= policy.max_per_day:
        return False
    return row.last is None or facts.k - row.last > policy.cooldown_runs


def decide(
    policy: Policy,
    facts: Facts,
    told: Mapping[str, Told] | None = None,
    *,
    paused: bool = False,
) -> Decision:
    """Whether the run is told, who it names and what to remember.

    ``told`` is what the screen's ``alert_state`` rows say (any session; only today's count) and
    ``paused`` whether the screen's alerts are paused until the owner resumes them.
    """
    if not policy.on:
        return Decision(False, OFF, matches=facts.matches)
    if paused:
        return Decision(False, PAUSED, matches=facts.matches)
    if facts.matches < policy.min_matches:
        return Decision(False, FEW, matches=facts.matches)
    told = told or {}
    entries = [item for item in facts.entered if allowed(policy, facts, told.get(item.key))]
    exits = facts.exited if policy.tell_exits else ()
    if not entries and not exits and policy.skip_zero:
        return Decision(False, NOTHING, held=len(facts.entered), matches=facts.matches)
    remembered = {}
    for item in entries:
        row = told.get(item.key)
        count = row.count if row is not None and row.session == facts.session else 0
        remembered[item.key] = Told(facts.session, count + 1, facts.k)
    return Decision(
        True,
        TOLD,
        entered=tuple(entries[:NAMED_MAX]),
        more_entered=max(0, len(entries) - NAMED_MAX),
        exited=tuple(exits[:NAMED_MAX]),
        more_exited=max(0, len(exits) - NAMED_MAX),
        held=len(facts.entered) - len(entries),
        matches=facts.matches,
        late_at=facts.late_at,
        pause=policy.after_trigger == PAUSE,
        told=remembered,
    )
