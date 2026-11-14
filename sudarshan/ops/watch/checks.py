"""The watch's checks: pure functions from one run's readings to a level each (M19.5, P8 7.1).

No I/O, no clock, no logging: ``readers.py`` gathers the readings, ``run.py`` keeps what each
check remembers between runs and records the changes. ``test_watch_checks.py`` holds the vectors.

The readings (a dict; a value of None, or a key that is missing, means "could not be read"):

    ist_minute        minutes since midnight in India
    ist_date          today's date in India, YYYY-MM-DD
    window_open       whether the price feed's own market window is open (trading day and hours)
    health            {service: its /health answer as a dict, or None when it did not answer}
    enabled           {service: whether its unit is enabled on this server}
    openalgo          whether OpenAlgo answered a public read
    master_date       the date in India of the last instrument list download ("" when never)
    disk              {"free_gb": ..., "inodes_free_pct": ...} for the app's disk
    mem_available_mb  the kernel's MemAvailable
    cert_hours_left   hours until the certificate nginx serves runs out

A check returns ``Result(name, level, facts, keep)``: ``facts`` are short plain values for the
words (``alerts/notices.py``), ``keep`` is what the check wants back as ``memory`` next run.
A reading that could not be read is never "fine": it is UNREAD, which tells nobody and leaves
what was last told as it stands.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

FINE, WARNING, FAILING, UNREAD, OFF = "fine", "warning", "failing", "unread", "off"
#: The levels that are told: a change between two of them is an event. UNREAD and OFF are not.
TOLD = (FINE, WARNING, FAILING)

#: SUDARSHAN's ring: service -> its one port on 127.0.0.1 (the README's port table).
SERVICES = {
    "feed": 5580,
    "alerts": 5582,
    "portfolio": 5583,
    "screener": 5584,
    "family": 5585,
    "journal": 5586,
}
RUN_EVERY_MIN = 2  # the timer's period; "two runs in a row" is 4 minutes
MISSES_TO_FAIL = 2
LOGIN_FROM_MIN = 8 * 60 + 58  # 08:58: auto-login has had its three tries
SESSION_FROM_MIN = 9 * 60 + 15
MASTER_FROM_MIN = 9 * 60  # 09:00: the instrument list should be today's
DISK_WARN_GB, DISK_FAIL_GB = 15.0, 5.0
INODES_WARN_PCT, INODES_FAIL_PCT = 10.0, 3.0
MEM_LOW_MB, MEM_LOW_RUNS = 1024, 3
CERT_FAIL_HOURS = 48.0


@dataclass(frozen=True)
class Result:
    name: str
    level: str
    facts: Mapping[str, str] = field(default_factory=dict)
    keep: Mapping[str, int] = field(default_factory=dict)


def _misses(memory: Mapping, missed: bool) -> int:
    return int(memory.get("misses", 0)) + 1 if missed else 0


def service(name: str, readings: Mapping, memory: Mapping) -> Result:
    """One ring service's ``/health``: failing after two runs in a row without an answer, for a
    service that is enabled here and has answered before (one never set up is OFF, not a fault)."""
    check = f"service_{name}"
    answers, enabled = readings.get("health") or {}, readings.get("enabled") or {}
    seen = int(bool(memory.get("seen")))
    if isinstance(answers.get(name), Mapping):
        return Result(check, FINE, keep={"seen": 1})
    if enabled.get(name) is False or not seen:
        return Result(check, OFF, keep={"seen": seen})
    if name not in answers:  # not asked at all: the reading itself is missing
        return Result(check, UNREAD, keep={**memory})
    misses = _misses(memory, True)
    level = FAILING if misses >= MISSES_TO_FAIL else FINE
    facts = {"service": name, "minutes": str(misses * RUN_EVERY_MIN)}
    return Result(check, level, facts, {"seen": 1, "misses": misses})


def openalgo(readings: Mapping, memory: Mapping) -> Result:
    """OpenAlgo itself. Told through the outside heartbeat only: the notice path needs OpenAlgo."""
    answered = readings.get("openalgo")
    if answered is None:
        return Result("openalgo", UNREAD, keep={**memory})
    misses = _misses(memory, not answered)
    level = FAILING if misses >= MISSES_TO_FAIL else FINE
    return Result("openalgo", level, {"minutes": str(misses * RUN_EVERY_MIN)}, {"misses": misses})


def _alerts_state(readings: Mapping) -> str | None:
    answer = (readings.get("health") or {}).get("alerts")
    state = answer.get("state") if isinstance(answer, Mapping) else None
    return state if isinstance(state, str) else None


def login(readings: Mapping, memory: Mapping) -> Result:
    """Today's Upstox login, seen through the order stream the alerts service holds with it.

    From 08:58 on a trading day the stream should be connected. Two runs without it: before
    09:15 that is "no login for today"; later, "the order stream is not connected"."""
    minute, window = readings.get("ist_minute"), readings.get("window_open")
    state = _alerts_state(readings)
    if minute is None or window is None or state is None:
        return Result("login", UNREAD, keep={**memory})
    if not window or minute < LOGIN_FROM_MIN or state in ("closed", "off", "stopped"):
        return Result("login", OFF)
    misses = _misses(memory, state != "connected")
    level = FAILING if misses >= MISSES_TO_FAIL else FINE
    when = "before_open" if minute < SESSION_FROM_MIN else "in_session"
    facts = {"when": when, "minutes": str(misses * RUN_EVERY_MIN)}
    return Result("login", level, facts, {"misses": misses})


def feed(readings: Mapping, memory: Mapping) -> Result:
    """The price feed's own word on its connection, while its market window is open."""
    answer = (readings.get("health") or {}).get("feed")
    if not isinstance(answer, Mapping) or not isinstance(answer.get("state"), str):
        return Result("feed", UNREAD, keep={**memory})
    window = answer.get("market_window")
    if not (isinstance(window, Mapping) and window.get("open") is True):
        return Result("feed", OFF)
    misses = _misses(memory, answer["state"] != "connected")
    level = FAILING if misses >= MISSES_TO_FAIL else FINE
    return Result("feed", level, {"minutes": str(misses * RUN_EVERY_MIN)}, {"misses": misses})


def instrument_list(readings: Mapping, memory: Mapping) -> Result:
    """From 09:00 on a trading day the instrument list should have been downloaded today."""
    minute, window = readings.get("ist_minute"), readings.get("window_open")
    today, last = readings.get("ist_date"), readings.get("master_date")
    if minute is None or window is None or not today or last is None:
        return Result("instrument_list", UNREAD)
    if not window or minute < MASTER_FROM_MIN:
        return Result("instrument_list", OFF)
    return Result("instrument_list", FINE if last == today else FAILING)


def disk(readings: Mapping, memory: Mapping) -> Result:
    """Free space and free inodes on the app's disk: the worse of the two decides."""
    reading = readings.get("disk")
    try:
        free, inodes = float(reading["free_gb"]), float(reading["inodes_free_pct"])
    except (TypeError, KeyError, ValueError):
        return Result("disk", UNREAD)
    space = FAILING if free < DISK_FAIL_GB else WARNING if free < DISK_WARN_GB else FINE
    files = FAILING if inodes < INODES_FAIL_PCT else WARNING if inodes < INODES_WARN_PCT else FINE
    level = max(space, files, key=TOLD.index)
    cause = "files" if TOLD.index(files) > TOLD.index(space) else "space"
    return Result("disk", level, {"free_gb": f"{free:.1f}", "cause": cause})


def memory_free(readings: Mapping, memory: Mapping) -> Result:
    """Under 1 GB available for three runs in a row is a warning."""
    available = readings.get("mem_available_mb")
    if isinstance(available, bool) or not isinstance(available, int | float):
        return Result("memory", UNREAD, keep={**memory})
    low = int(memory.get("low", 0)) + 1 if available < MEM_LOW_MB else 0
    level = WARNING if low >= MEM_LOW_RUNS else FINE
    return Result("memory", level, {"available_mb": str(int(available))}, {"low": low})


def certificate(readings: Mapping, memory: Mapping) -> Result:
    """The certificate nginx serves: failing under 48 hours left (it should renew before that)."""
    hours = readings.get("cert_hours_left")
    if isinstance(hours, bool) or not isinstance(hours, int | float):
        return Result("certificate", UNREAD)
    level = FAILING if hours < CERT_FAIL_HOURS else FINE
    return Result("certificate", level, {"hours": str(max(0, int(hours)))})


def run_checks(readings: Mapping, memories: Mapping[str, Mapping]) -> list[Result]:
    """Every check over one run's readings. ``memories`` is each check's ``keep`` from the run
    before, by check name. A check that raises is UNREAD: never a crash, never "fine"."""
    checks = [(f"service_{name}", lambda r, m, n=name: service(n, r, m)) for name in SERVICES]
    checks += [
        ("openalgo", openalgo),
        ("login", login),
        ("feed", feed),
        ("instrument_list", instrument_list),
        ("disk", disk),
        ("memory", memory_free),
        ("certificate", certificate),
    ]
    results = []
    for name, check in checks:
        memory = memories.get(name)
        memory = memory if isinstance(memory, Mapping) else {}
        try:
            results.append(check(readings, memory))
        except Exception:
            results.append(Result(name, UNREAD, keep={**memory}))
    return results


def changes(results: list[Result], told: Mapping[str, str]) -> tuple[list[dict], dict[str, str]]:
    """What to tell: one change per check whose level moved between two told levels.

    ``told`` is the level last told per check (FINE when never told). UNREAD and OFF tell nothing
    and leave it as it stands, so a failure is told once and its recovery once, whatever was
    unreadable in between. Returns the changes (check, level, was, facts) and the new ``told``.
    """
    now_told, moved = dict(told), []
    for result in results:
        was = told.get(result.name, FINE)
        if result.level not in TOLD or result.level == was:
            continue
        now_told[result.name] = result.level
        moved.append(
            {"check": result.name, "level": result.level, "was": was, "facts": dict(result.facts)}
        )
    return moved, now_told
