"""One run of the watch: read, judge, remember, record what changed, write the report (M19.5).

    readers.gather -> checks.run_checks -> checks.changes
      -> events.jsonl   one line per change, for the notice desk (sudarshan/alerts/notices.py)
      -> state.json     what each check remembers and what was last told (private, 600)
      -> report.json    every check's level now, for the System page (640)

All three are in the unit's state folder, ``/var/lib/sudarshan-watch``. The watch only records:
it holds no key to Telegram or OpenAlgo and sends nothing itself, as the deploy tool and the
backup job do. The events of a run are recorded before its state is kept: a run that then
cannot write its state ends with exit code 1 and the next run tells the change again, which is
better than a change nobody was told. An event id names the check, the level and the minute, so
a line written twice is one notice.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from sudarshan.ops import events
from sudarshan.ops.files import read_regular
from sudarshan.ops.release import installed_release

from . import checks, readers

STATE_DIR = "/var/lib/sudarshan-watch"
APP_DIR = "/opt/sudarshan/apps/openalgo"
FORMAT = 1
MAX_STATE = 1 << 18
#: Checks whose change is never an event: OpenAlgo carries the notices, so its own silence is told
#: by the outside heartbeat alone.
NO_EVENT = frozenset({"openalgo"})
KIND = {checks.FAILING: "watch_failing", checks.WARNING: "watch_warning", checks.FINE: "watch_fine"}


@dataclass(frozen=True)
class Settings:
    state_dir: Path
    app_dir: Path
    env_file: Path

    @property
    def state_file(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def report_file(self) -> Path:
        return self.state_dir / "report.json"

    @property
    def spool(self) -> Path:
        return self.state_dir / events.WATCH_SPOOL.name


def settings_from(env: Mapping[str, str]) -> Settings:
    state = env.get("SUDARSHAN_WATCH_STATE_DIR") or env.get("STATE_DIRECTORY") or STATE_DIR
    app = Path(env.get("SUDARSHAN_OPENALGO_DIR") or APP_DIR)
    return Settings(Path(state.split(":")[0]), app, app / ".env")


def load_state(path: Path) -> dict:
    """The last run's state; an empty one when there is none or it cannot be used."""
    raw = read_regular(path, MAX_STATE)
    try:
        state = json.loads(raw) if raw else {}
    except ValueError:
        state = {}
    if not isinstance(state, dict) or state.get("v") != FORMAT:
        return {"v": FORMAT, "told": {}, "memory": {}, "since": {}}
    for key in ("told", "memory", "since"):
        if not isinstance(state.get(key), dict):
            state[key] = {}
    return state


def write_json(path: Path, document: dict, mode: int) -> None:
    """The whole document or the old file: written beside it, then moved into place."""
    tmp = path.with_name(f".{path.name}.new")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), mode)  # the umask took bits off the creation mode
            json.dump(document, handle, ensure_ascii=False, separators=(",", ":"))
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)


def judge(readings: Mapping, state: Mapping) -> tuple[list[checks.Result], list[dict], dict]:
    """The results, the changes to tell and the state to keep, from one run's readings. Pure."""
    results = checks.run_checks(readings, state.get("memory") or {})
    moved, told = checks.changes(results, state.get("told") or {})
    now = readings.get("now")
    since = dict(state.get("since") or {})
    for change in moved:
        since[change["check"]] = now
    master = readings.get("master_date")
    kept = {
        "v": FORMAT,
        "told": told,
        "memory": {result.name: dict(result.keep) for result in results},
        "since": since,
        "master_date": master if master else state.get("master_date"),
    }
    return results, moved, kept


def report_of(results: list[checks.Result], state: Mapping, now: float, release) -> dict:
    """The report for the System page. Its shape is pinned by test_watch_run.py."""
    told, since = state.get("told") or {}, state.get("since") or {}
    return {
        "v": FORMAT,
        "at": int(now),
        "release": release,
        "checks": [
            {
                "name": result.name,
                "level": result.level,
                "told": told.get(result.name, checks.FINE),
                "since": since.get(result.name),
                "facts": dict(result.facts),
            }
            for result in results
        ],
    }


def record_changes(spool: Path, moved: list[dict], now: float) -> int:
    """One event per change, all of one run together; returns how many are in the file."""
    run, written = f"watch-{int(now)}", 0
    for change in moved:
        if change["check"] in NO_EVENT:
            continue
        written += events.record(
            spool,
            kind=KIND[change["level"]],
            event_id=f"watch-{change['check']}-{change['level']}-{int(now) // 60}",
            at=now,
            run=run,
            src="watch",
            check=change["check"],
            was=change["was"],
            **change["facts"],
        )
    return written


def run_once(
    settings: Settings,
    *,
    clock: Callable[[], float] = time.time,
    gather: Callable[..., dict] = readers.gather,
    installing: Callable[[], bool] = readers.installing,
) -> int:
    """One run. 0 when it finished (whatever the checks said), 1 when its files could not be
    written, 0 with nothing done when another run holds the folder or while the deploy tool is
    installing a release: it stops and starts every service, and none of that is a fault."""
    settings.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = os.open(settings.state_dir / "lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 0
        if installing():
            return 0
        now = clock()
        state = load_state(settings.state_file)
        readings = gather(now, settings.app_dir, settings.env_file, state.get("master_date"))
        results, moved, kept = judge(readings, state)
        due = sum(change["check"] not in NO_EVENT for change in moved)
        if record_changes(settings.spool, moved, now) != due:
            return 1  # not kept either: the next run finds the same change and tells it
        try:
            write_json(settings.state_file, kept, 0o600)
            write_json(
                settings.report_file, report_of(results, kept, now, installed_release()), 0o640
            )
        except OSError:
            return 1
        return 0
    finally:
        os.close(lock)
