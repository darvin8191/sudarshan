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
    cert_hours_left   hours until SUDARSHAN's own certificate runs out, or why none was judged
    posture           what ``posture.read`` found, read once an hour: {"mcp": {...}, "debug": bool,
                      "loose": [names], "open_ports": [ports], "reboot": bool,
                      "updates": {"all": n, "security": n} or None}
    counts            {"clients": approved AI clients, "families": live AI sign-ins,
                      "links": Telegram accounts linked to the bot}, from OpenAlgo's own tables
    backup            {"age_h": hours since the newest complete archive or None when there is
                      none, "size_mb": its size, "offsite": the last run's offsite word,
                      "fy": the newest financial-year archive's label, "fy_mb": its size,
                      "fy_owed": the year whose archive has no offsite copy yet, if any}
    drill             the restore drill's last result: {"at": seconds, "ok": bool}, {} when never
    historify_gb      the size of Historify's file, 0 when there is none

A check returns ``Result(name, level, facts, keep, news)``: ``facts`` are short plain values for
the words (``alerts/notices.py``), ``keep`` is what the check wants back as ``memory`` next run,
and ``news`` is something that happened since the last run and is told once whatever the level
(a new AI connection, a Telegram account linked).
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
BACKUP_LATE_HOURS = 30.0  # the backup job's own "overdue" line (backup.OVERDUE_S)
DRILL_LATE_DAYS = 100  # one drill a month by its timer; a quarter without one is told
HISTORIFY_WARN_GB = 3.0
#: Checks that only show (the System page, the weekly summary) and are never a notice: OpenAlgo
#: carries the notices, so its own silence is the outside heartbeat's; the backup job tells its
#: own late night; updates and a reboot are the owner's weekly reading, applied by hand.
SHOWN_ONLY = frozenset({"openalgo", "backup", "posture_reboot", "posture_updates"})


@dataclass(frozen=True)
class Result:
    name: str
    level: str
    facts: Mapping[str, str] = field(default_factory=dict)
    keep: Mapping[str, int] = field(default_factory=dict)
    news: Mapping[str, str] = field(default_factory=dict)


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


#: Why the certificate was not judged (``readers.CERT_NO_NAME``, ``readers.CERT_OTHER_SITE``).
CERT_CAUSES = ("no_name", "other_site")


def certificate(readings: Mapping, memory: Mapping) -> Result:
    """SUDARSHAN's own certificate, as nginx serves it: failing under 48 hours left (it should
    renew before that). A reading that names why none was judged (no name for the site is known,
    or nginx gave another site's) is unread with that cause, never another site's hours."""
    hours = readings.get("cert_hours_left")
    if isinstance(hours, str) and hours in CERT_CAUSES:
        return Result("certificate", UNREAD, {"cause": hours})
    if isinstance(hours, bool) or not isinstance(hours, int | float):
        return Result("certificate", UNREAD)
    level = FAILING if hours < CERT_FAIL_HOURS else FINE
    return Result("certificate", level, {"hours": str(max(0, int(hours)))})


def _posture(readings: Mapping) -> Mapping | None:
    found = readings.get("posture")
    return found if isinstance(found, Mapping) else None


def posture_locks(readings: Mapping, memory: Mapping) -> Result:
    """The assistant's locks in OpenAlgo's settings: read-only tools, no write scope, and, once
    it is switched on, the owner's approval for every new client."""
    mcp = (_posture(readings) or {}).get("mcp")
    if not isinstance(mcp, Mapping) or not all(
        isinstance(mcp.get(key), bool) for key in ("switched_on", "locked", "approval")
    ):
        return Result("posture_locks", UNREAD)
    if not mcp["locked"]:
        return Result("posture_locks", WARNING, {"cause": "locks"})
    if mcp["switched_on"] and not mcp["approval"]:
        return Result("posture_locks", WARNING, {"cause": "approval"})
    return Result("posture_locks", FINE)


def posture_debug(readings: Mapping, memory: Mapping) -> Result:
    debug = (_posture(readings) or {}).get("debug")
    if not isinstance(debug, bool):
        return Result("posture_debug", UNREAD)
    return Result("posture_debug", WARNING if debug else FINE)


def _named(readings: Mapping, key: str) -> list[str] | None:
    found = (_posture(readings) or {}).get(key)
    if not isinstance(found, list):
        return None
    return sorted(str(item) for item in found)


def posture_modes(readings: Mapping, memory: Mapping) -> Result:
    """The settings folder, the vault and OpenAlgo's .env are closed to other accounts."""
    loose = _named(readings, "loose")
    if loose is None:
        return Result("posture_modes", UNREAD)
    return (
        Result("posture_modes", WARNING, {"what": loose[0]})
        if loose
        else Result("posture_modes", FINE)
    )


def posture_ports(readings: Mapping, memory: Mapping) -> Result:
    """Every port of the ring listens on this machine only."""
    wide = _named(readings, "open_ports")
    if wide is None:
        return Result("posture_ports", UNREAD)
    return (
        Result("posture_ports", WARNING, {"port": wide[0]})
        if wide
        else Result("posture_ports", FINE)
    )


def posture_reboot(readings: Mapping, memory: Mapping) -> Result:
    reboot = (_posture(readings) or {}).get("reboot")
    if not isinstance(reboot, bool):
        return Result("posture_reboot", UNREAD)
    return Result("posture_reboot", WARNING if reboot else FINE)


def posture_updates(readings: Mapping, memory: Mapping) -> Result:
    """Operating-system updates waiting: a warning while a security update is among them."""
    updates = (_posture(readings) or {}).get("updates")
    try:
        waiting, security = int(updates["all"]), int(updates["security"])
    except (TypeError, KeyError, ValueError):
        return Result("posture_updates", UNREAD)
    facts = {"all": str(waiting), "security": str(security)}
    return Result("posture_updates", WARNING if security > 0 else FINE, facts)


def _count(readings: Mapping, key: str) -> int | None:
    found = (
        (readings.get("counts") or {}).get(key)
        if isinstance(readings.get("counts"), Mapping)
        else None
    )
    return found if isinstance(found, int) and not isinstance(found, bool) and found >= 0 else None


def _known(memory: Mapping, key: str) -> int | None:
    found = memory.get(key)
    return found if isinstance(found, int) and not isinstance(found, bool) else None


def assistant(readings: Mapping, memory: Mapping) -> Result:
    """AI connections: approved clients and live sign-ins. One more of either than at the last
    run is news, told at once. The first reading tells nothing: it is where counting starts."""
    clients, families = _count(readings, "clients"), _count(readings, "families")
    if clients is None or families is None:
        return Result("assistant", UNREAD, keep={**memory})
    was_clients, was_families = _known(memory, "clients"), _known(memory, "families")
    more = (was_clients is not None and clients > was_clients) or (
        was_families is not None and families > was_families
    )
    facts = {"clients": str(clients), "families": str(families)}
    keep = {"clients": clients, "families": families}
    level = FINE if clients or families else OFF
    return Result("assistant", level, facts, keep, {"what": "connection"} if more else {})


def telegram_links(readings: Mapping, memory: Mapping) -> Result:
    """Telegram accounts linked to the owner's bot: any change in their number is news."""
    links = _count(readings, "links")
    if links is None:
        return Result("telegram_links", UNREAD, keep={**memory})
    was = _known(memory, "links")
    news = {}
    if was is not None and links != was:
        news = {"what": "linked" if links > was else "unlinked"}
    return Result("telegram_links", FINE, {"links": str(links)}, {"links": links}, news)


def backup(readings: Mapping, memory: Mapping) -> Result:
    """The newest complete nightly archive's age, and a year's archive still owed its offsite
    copy. Shown only: the backup job tells a late night and a failed upload itself."""
    reading = readings.get("backup")
    if not isinstance(reading, Mapping):
        return Result("backup", UNREAD)
    facts = {
        key: str(reading[key])
        for key in ("size_mb", "offsite", "fy", "fy_mb", "fy_owed")
        if isinstance(reading.get(key), int | str) and not isinstance(reading.get(key), bool)
    }
    age = reading.get("age_h")
    if age is None:
        return Result("backup", OFF, facts)
    if isinstance(age, bool) or not isinstance(age, int | float):
        return Result("backup", UNREAD)
    facts["age_h"] = str(max(0, int(age)))
    # A year's archive with no offsite copy yet is something to look at, on the page and in the
    # weekly summary (the backup job itself tells the failed upload, each night it fails).
    late = age > BACKUP_LATE_HOURS or "fy_owed" in facts
    return Result("backup", WARNING if late else FINE, facts)


def drill(readings: Mapping, memory: Mapping) -> Result:
    """The restore drill: a warning when the last one failed, when none was ever done, or when
    the last is over 100 days old. Only "over 100 days" is the watch's to tell (``told_by``):
    the drill tells its own result, and "never" is on the System page from the first day."""
    reading, now = readings.get("drill"), readings.get("now")
    if not isinstance(reading, Mapping) or not isinstance(now, int | float):
        return Result("drill", UNREAD)
    if not reading:
        return Result("drill", WARNING, {"cause": "never"})
    at, ok = reading.get("at"), reading.get("ok")
    if isinstance(at, bool) or not isinstance(at, int | float) or not isinstance(ok, bool):
        return Result("drill", UNREAD)
    days = max(0, int((now - at) // 86400))
    facts = {"days": str(days), "when": str(int(at))}
    if not ok:
        return Result("drill", WARNING, {**facts, "cause": "failed"})
    if days > DRILL_LATE_DAYS:
        return Result("drill", WARNING, {**facts, "cause": "late"})
    return Result("drill", FINE, facts)


def historify(readings: Mapping, memory: Mapping) -> Result:
    """Historify's file has no keep rule of its own: a warning from 3 GB."""
    size = readings.get("historify_gb")
    if isinstance(size, bool) or not isinstance(size, int | float):
        return Result("historify", UNREAD)
    if size <= 0:
        return Result("historify", OFF)
    return Result(
        "historify", WARNING if size >= HISTORIFY_WARN_GB else FINE, {"gb": f"{size:.1f}"}
    )


def told_by(change: Mapping) -> bool:
    """Whether a change of level is the watch's to tell. Not for a check that is shown only, and
    for the drill only its growing old: its own result, pass or fail, it tells itself."""
    if change["check"] in SHOWN_ONLY:
        return False
    if change["check"] == "drill":
        return (change.get("facts") or {}).get("cause") == "late"
    return True


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
        ("posture_locks", posture_locks),
        ("posture_debug", posture_debug),
        ("posture_modes", posture_modes),
        ("posture_ports", posture_ports),
        ("posture_reboot", posture_reboot),
        ("posture_updates", posture_updates),
        ("assistant", assistant),
        ("telegram_links", telegram_links),
        ("backup", backup),
        ("drill", drill),
        ("historify", historify),
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
