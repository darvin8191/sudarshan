"""Properties of words.py: what it never says, and rules restated from the design in another shape.

The page words are for a trader, so the first group holds every line the module can make to the same
bar: printable text, no identifier, no code, no word the platform does not use. The rest restate the
engine line's order, the chip table, the time rules and the "Not checked" line without looking at the
code's own branches.
"""

from __future__ import annotations

import itertools
import random
import re
from datetime import date

from sudarshan.screener import alerting as al
from sudarshan.screener import membership as mem
from sudarshan.screener import types as T
from sudarshan.screener import words as w

SEEDS = range(150)
DAY_MS = 86_400_000
IST_MS = 19_800_000  # +05:30
FIRST = 1_793_000_000_000  # late October 2026, in epoch ms
BANNED = re.compile(r"\b(arm|armed|arming|live|paper|virtual)\b", re.IGNORECASE)
LEAKS = (
    "_",
    "None",
    "True",
    "False",
    "Why(",
    "{",
    "}",
    "[",
    "]",
    "Traceback",
    "Error",
    "Exception",
)
CODES = [
    T.WARMING,
    T.NO_CANDLE,
    T.NO_DATA,
    T.ZERO,
    T.NOT_A_NUMBER,
    T.PAUSED,
    T.NO_AUCTION,
    T.NO_OPTIONS,
    T.SHORT_STORE,
    T.WINDOW_OPEN,
    T.OTHER_DAY,
    T.NO_FEED,
]


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


def clean_text(text: str) -> None:
    """The bar for every line the owner reads."""
    assert text.strip() == text and "  " not in text and "\n" not in text, repr(text)
    assert all(ch.isascii() and ch.isprintable() or ch == "·" for ch in text), repr(text)
    assert not BANNED.search(text), text
    assert not any(leak in text for leak in LEAKS), text


def instants(rng: random.Random, count: int = 2) -> list[int]:
    """Epoch ms within a few weeks of FIRST, at any second of the day."""
    return [
        FIRST + rng.randint(-20, 20) * DAY_MS + rng.randint(0, DAY_MS - 1) for _ in range(count)
    ]


# ---- every reason, with any detail -------------------------------------------------------------------------
def details(rng: random.Random):
    junk = [None, 0, 1, -5, 3.7, 10**30, "", "x", "India VIX", "NIFTY", (1, 2), [], True]
    return tuple(rng.choice(junk) for _ in range(rng.randint(0, 3)))


def a_why(rng: random.Random) -> T.Why:
    if rng.random() < 0.2:
        return T.Why(rng.choice(["", "nope", "warming ", "NO_DATA"]), details(rng))
    code = rng.choice(CODES)
    fine = {
        T.WARMING: (rng.randint(2, 500), rng.randint(0, 499)),
        T.NO_DATA: (rng.choice(["oi", "vwap", "prev_close", "index", "zzz"]),),
        T.PAUSED: (rng.choice(instants(rng)),),
        T.NO_OPTIONS: (rng.choice(["NIFTY", "self", "BANKNIFTY", " "]),),
        T.SHORT_STORE: (rng.randint(0, 19), 20),
        T.WINDOW_OPEN: (rng.choice([5, 15, 30]),),
        T.OTHER_DAY: (rng.choice(["buildup", "oi_change_pct", "?"]),),
    }.get(code, ())
    return T.Why(code, fine if rng.random() < 0.7 else details(rng))


@over_seeds
def test_no_reason_ever_leaks_a_code_or_fails(seed):
    rng = random.Random(seed)
    whys = [a_why(rng) for _ in range(rng.randint(0, 6))]
    for why in whys:
        sentence = w.why_words(why)
        clean_text(sentence)
        assert sentence[0].isupper() and not sentence.endswith("."), sentence
        assert w.not_checked_words([why]) == "Not checked: " + sentence[0].lower() + sentence[1:]
    clean_text(w.not_checked_words(whys))
    clean_text(w.not_checked_title(rng.randint(0, 10**7)))


@over_seeds
def test_the_not_checked_line_names_each_reason_once_and_counts_the_rest(seed):
    rng = random.Random(seed)
    whys = [a_why(rng) for _ in range(rng.randint(1, 9))]
    limit = rng.randint(1, 4)
    phrases = list(
        dict.fromkeys(w.why_words(why)[0].lower() + w.why_words(why)[1:] for why in whys)
    )
    want = "Not checked: " + "; ".join(phrases[:limit])
    if len(phrases) > limit:
        want += f"; and {len(phrases) - limit} more"
    assert w.not_checked_words(whys, limit) == want


# ---- the engine line -------------------------------------------------------------------------------------------
HOLDS = {
    w.NOT_ANSWERING: lambda e: not e.answering,
    w.SWITCHED_OFF: lambda e: not e.enabled,
    w.WAITING_MASTER: lambda e: not e.master_ready,
    w.CLOSED: lambda e: not e.trading,
    w.FEED_DOWN: lambda e: e.feed != w.FEED_OK,
    w.AUCTION: lambda e: e.auction_at is not None,
    w.WARMING_UP: lambda e: e.loading > 0,
    w.NO_PRICES: lambda e: e.without_prices > 0,
    w.REAL_TIME: lambda e: True,
}
STARTS = {
    w.NOT_ANSWERING: "The screener is not answering",
    w.SWITCHED_OFF: "Switched off on the server",
    w.WAITING_MASTER: "Waiting for today's master contract",
    w.CLOSED: "Market closed",
    w.FEED_DOWN: "Not checking · ",
    w.AUCTION: "Closing auction · price at about ",
    w.WARMING_UP: "Warming up · history loading for ",
    w.NO_PRICES: "No prices from Upstox for ",
    w.REAL_TIME: "Real-time",
}


def test_the_state_is_the_first_that_is_true():
    assert tuple(HOLDS) == w.STATES, "the order the design lists them in is the order they win"
    seen = set()
    flags = itertools.product([True, False], repeat=4)
    rest = itertools.product(["ok", "stopped"], [None, FIRST], [0, 3], [0, 2])
    for (answering, enabled, ready, trading), (feed, auction, loading, bare) in itertools.product(
        flags, rest
    ):
        engine = w.Engine(
            FIRST, answering, enabled, ready, trading, auction, feed, FIRST, loading, 9, bare, FIRST
        )
        want = next(state for state, holds in HOLDS.items() if holds(engine))
        assert w.engine_state(engine) == want
        text = w.engine_words(engine)
        assert text.startswith(STARTS[want]), (want, text)
        assert ("Real-time" in text) == (want == w.REAL_TIME), "Real-time only while ticks arrive"
        clean_text(text)
        seen.add(want)
    assert seen == set(w.STATES)


@over_seeds
def test_the_engine_line_is_always_fit_to_read(seed):
    rng = random.Random(seed)
    now, other = instants(rng)
    engine = w.Engine(
        now,
        rng.random() < 0.9,
        rng.random() < 0.9,
        rng.random() < 0.9,
        rng.random() < 0.7,
        rng.choice([None, other]),
        rng.choice(
            [
                w.FEED_OK,
                w.FEED_OK,
                "reconnecting",
                "token_rejected",
                "no_token",
                "stopped",
                "silent",
                "?",
            ]
        ),
        rng.choice([None, other]),
        rng.choice([0, 0, rng.randint(1, 600)]),
        rng.randint(0, 9000),
        rng.choice([0, 0, rng.randint(1, 600)]),
        rng.choice([None, other]),
    )
    clean_text(w.engine_words(engine))


# ---- the chip table ---------------------------------------------------------------------------------------------
LOOKS = {
    w.CHIP_NEW: ("circle-plus", w.TONE_BRAND, "New · "),
    w.CHIP_SINCE: ("clock", w.TONE_QUIET, "Since "),
    w.CHIP_MISSING: ("hourglass", w.TONE_AMBER, "Missing"),
    w.CHIP_PINNED: ("pin", w.TONE_OUTLINE, "Pinned"),
    w.CHIP_REMOVED: ("ban", w.TONE_QUIET, "Removed by you"),
}
#: The design's table, first match wins: (what is true of the row, the chip it gets).
TABLE = [
    (lambda r, last: r.status == mem.DISMISSED, w.CHIP_REMOVED),
    (lambda r, last: r.status == mem.EXITED, None),
    (lambda r, last: r.pinned, w.CHIP_PINNED),
    (lambda r, last: not r.added_by_screener, None),
    (lambda r, last: r.status == mem.MISSING, w.CHIP_MISSING),
    (lambda r, last: r.first_seen_run == last, w.CHIP_NEW),
    (lambda r, last: True, w.CHIP_SINCE),
]


@over_seeds
def test_a_row_gets_the_chip_the_table_says_and_always_an_icon_and_a_word(seed):
    rng = random.Random(seed)
    seen, last = instants(rng)[0], rng.randint(1, 30)
    status = rng.choice(mem.STATUSES)
    row = mem.Row(
        "K",
        "SBIN",
        "NSE",
        status,
        pinned=status in (mem.ACTIVE, mem.MISSING) and rng.random() < 0.3,
        added_by_screener=rng.random() < 0.8,
        first_seen_at=seen,
        first_seen_run=rng.choice([last, last, rng.randint(1, 30)]),
        last_seen_at=seen + rng.randint(0, 5) * DAY_MS,
        misses=rng.randint(1, 4) if status == mem.MISSING else 0,
    )
    grace = rng.choice(
        [mem.Grace(), mem.Grace(mem.RUNS, 5), mem.Grace(mem.DAY_END), mem.Grace(mem.NEVER)]
    )
    screen = rng.choice(["15-min EMA cross", "two   spaces\n and a  line", "x" * 80, ""])
    got = w.chip(row, now=instants(rng)[0], last_run=last, grace=grace, screen=screen)
    want = next(kind for holds, kind in TABLE if holds(row, last))
    if want is None:
        assert got is None
        return
    icon, tone, start = LOOKS[want]
    assert (got.kind, got.icon, got.tone) == (want, icon, tone)
    assert got.text.startswith(start) and got.text.strip() == got.text
    for text in (got.text, got.name, got.note):
        if text:
            clean_text(text)
    assert got.name, "a screen reader always has a name"
    assert bool(got.note) == (want == w.CHIP_PINNED and status == mem.MISSING)


# ---- marks and the alert line -----------------------------------------------------------------------------------
@over_seeds
def test_marks_come_in_one_fixed_order(seed):
    rng = random.Random(seed)
    late = rng.choice([None, instants(rng)[0]])
    marks = {"would_tell": rng.random() < 0.5, "late": late, "repaired": rng.random() < 0.5}
    marks = dict(rng.sample(sorted(marks.items()), len(marks)))
    got = w.mark_words(marks)
    want = []
    if marks["repaired"]:
        want.append("repaired after a pause")
    if late is not None:
        want.append(f"checked late at {w.hm(late)} after a restart")
    if marks["would_tell"]:
        want.append("would have raised an alert")
    assert got == tuple(want)
    for text in got:
        clean_text(text)
    made = w.run_marks(repaired=marks["repaired"], late_at=late, would_tell=marks["would_tell"])
    assert w.mark_words(made) == got
    assert set(made) <= {w.M_REPAIRED, w.M_LATE, w.M_WOULD_TELL}


@over_seeds
def test_a_runs_alert_line_agrees_with_the_decision(seed):
    rng = random.Random(seed)
    policy = al.Policy(
        on=rng.random() < 0.9,
        min_matches=rng.randint(1, 4),
        skip_zero=rng.random() < 0.6,
        tell_exits=rng.random() < 0.5,
        unique_per_day=rng.random() < 0.5,
        after_trigger=rng.choice([al.CONTINUE, al.PAUSE]),
    )
    symbols = [f"S{n}" for n in range(12)]
    rng.shuffle(symbols)
    named = lambda items: tuple(al.Named(s, s, "NSE") for s in items)  # noqa: E731
    facts = al.Facts(
        20261028,
        rng.randint(1, 9),
        rng.randint(0, 9),
        named(symbols[: rng.randint(0, 8)]),
        named(symbols[8 : 8 + rng.randint(0, 4)]),
    )
    told = {s: al.Told(20261028, 1, 1) for s in symbols[: rng.randint(0, 6)]}
    decision = al.decide(policy, facts, told, paused=rng.random() < 0.3)
    text = w.told_words(decision, policy)
    clean_text(text)
    raised = len(decision.entered) + decision.more_entered
    left = len(decision.exited) + decision.more_exited
    assert text.startswith("Alert raised") == decision.tell
    if decision.tell:
        assert (f"{raised} entered" in text) == (raised > 0)
        assert (f"{left} left" in text) == (left > 0)
        assert ("nothing new" in text) == (raised == 0 and left == 0)
    assert text.startswith("No alert") == (decision.why in (al.FEW, al.NOTHING))
    assert w.alerts_words(policy, paused=False) == ("Alerts on" if policy.on else "Alerts off")


@over_seeds
def test_the_exited_line_counts_what_it_does_not_name(seed):
    rng = random.Random(seed)
    count = rng.randint(0, 12)
    exits = [(f"S{n}", FIRST + n * 60_000) for n in range(count)]
    text = w.exited_words(exits)
    if not count:
        assert text == ""
        return
    clean_text(text)
    shown = min(count, w.EXITS_SHOWN)
    head = text.removeprefix("Exited today: ")
    assert head != text
    named, _, more = head.partition(" and ")
    assert len(named.split(", ")) == shown
    assert more == (f"{count - shown} more" if count > shown else "")


# ---- time, restated from the calendar ---------------------------------------------------------------------------------
WEEKDAYS = ("Thu", "Fri", "Sat", "Sun", "Mon", "Tue", "Wed")  # day 0 of the epoch was a Thursday
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def local(at: int):
    """(days since the epoch, milliseconds into the day) on the clock of Mumbai."""
    return divmod(at + IST_MS, DAY_MS)


@over_seeds
def test_time_is_read_on_the_clock_of_mumbai(seed):
    rng = random.Random(seed)
    at, now = instants(rng)
    if rng.random() < 0.3:
        now = at + rng.randint(-DAY_MS, DAY_MS)
    day, into = local(at)
    today, _ = local(now)
    clock = f"{into // 3_600_000:02d}:{into // 60_000 % 60:02d}"
    assert w.hm(at) == clock
    assert w.hms(at) == f"{clock}:{into // 1000 % 60:02d}"
    calendar = date.fromordinal(719_163 + day)
    year = f" {calendar.year}" if calendar.year != date.fromordinal(719_163 + today).year else ""
    if day == today:
        want_day = clock
    elif abs(today - day) < 7:
        want_day = WEEKDAYS[day % 7]
    else:
        want_day = f"{calendar.day} {MONTHS[calendar.month - 1]}{year}"
    assert w.day_words(at, now) == want_day
    assert w.when(at, now) == (want_day if day == today else f"{want_day} {clock}")


def test_the_pause_notice_is_the_same_everywhere_it_is_said():
    policy = al.Policy()
    decision = al.Decision(False, al.PAUSED)
    assert w.told_words(decision, policy) == w.PAUSED_NOTE == w.alerts_words(policy, paused=True)
    assert w.RESUME == "Resume"
