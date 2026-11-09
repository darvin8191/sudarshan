"""The replay check (M9.1, design 7.2): what the screener would have found on a recorded day, offline.

    python -m sudarshan.screener.check /var/lib/sudarshan-feed/ticks/2026-10-28.jsonl.gz --from-db
    python -m sudarshan.screener.check /var/lib/sudarshan-feed/ticks/2026-10-28.jsonl.gz --from-db --compare

Runs the pure pipeline (bars, runs, membership, the alert policy) over the feed's recording of a
day with the tick clock (each tick's own ``ts_recv``; the 1 s timer runs on it) and the
calendar, and prints each run: its time, the screen, matches, entered, exited, not checked and
whether a digest would have been told. It sends nothing and writes nothing: screener.db is opened
read-only (never created or migrated) for the active screens and their universes; a ``--spec``
file (one screen as JSON) and ``--keys`` run it without a database, for CI's synthetic
recordings.

``--compare`` checks each run against the live ``run`` rows of the same day: a difference on a
run the service marked repaired or late is listed as explained; any other difference, or a run
the service made that the replay did not (or the other way round), exits 1. A clean check prints
"Replay check: N runs compared, all match" and exits 0. With ``--from-db`` each screen starts the
day with the members it carried from earlier days (a member touched today is taken as matching
at the start), so they are not counted as the day's entries.

Before the screener was ever switched on there is no screener.db: use ``--spec`` and ``--keys``.

What a recording cannot hold is left out, as the service would in replay (``replay=True``): the
exchange's status and the feed's health are not recorded, so the feed counts as healthy within
the session and no pause is marked; the auction print is judged as recorded.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sudarshan.alerts.market.clock import Feed, Market
from sudarshan.alerts.market.grain import paise
from sudarshan.alerts.market.sessions import day_key, epoch_ms, previous_trading_day, sessions_of
from sudarshan.autologin.schedule import (
    EXTRA_TRADING_DAYS_FILE,
    HOLIDAYS_FILE,
    IST,
    TradingCalendar,
)

from . import alerting as al
from . import candles, scheduler, spec, words
from . import membership as mem
from .bars import Bars
from .engine import Context, Subject
from .runs import Judged, Member, evaluate
from .screen_ops import sched_screen
from .store import OWNER, open_read_only
from .types import DAY, HIT, MINUTE_TFS, NOT_CHECKED, Plan, Series

CLEAN, DIFFERENT, CANNOT = 0, 1, 2


def read_recording(path: Path) -> Iterator[tuple[str, int, dict]]:
    """(key, ts_recv, frame) per recorded tick, as sudarshan.feed.replay reads them."""
    from sudarshan.feed.replay import read_ticks

    for key, ts_recv, payload in read_ticks(path):
        try:
            frame = json.loads(payload)
        except ValueError:
            continue
        if isinstance(frame, dict) and ts_recv:
            frame.setdefault("key", key)
            yield key, ts_recv, frame


@dataclass
class ReplaySource:
    """runs.Source over the bars built from the recording."""

    bars: Bars
    market: Market
    members_by_universe: Mapping[str, Sequence[Member]]
    last: dict[str, Mapping[str, Any]] = field(default_factory=dict)

    def members(self, universe: Mapping[str, Any]) -> Sequence[Member]:
        return self.members_by_universe.get(_uni_key(universe), [])

    def subject(self, member: Member, plan: Plan, boundary: int) -> Subject | None:
        frame = self.last.get(member.key)
        if frame is None and not self.bars.bars(member.key):
            return None
        series: dict[Any, Series] = {}
        for tf in plan.need:
            if tf in MINUTE_TFS or tf == DAY:
                series[tf] = candles.read(self.bars, member.key, tf, self.market, boundary)
            else:
                series[tf] = Series.of(tf, [])
        own = self.market.sessions.get(member.segment)
        return Subject(
            member.symbol,
            series,
            cp=paise(frame.get("cp")) if frame else None,
            auction_end=own.end if (member.cas and own is not None) else None,
            printed=self.bars.printed(member.key, self.market.today) if member.cas else False,
        )

    def context(self, boundary: int, session: int) -> Context:
        return Context(boundary, session, self.market.previous)

    def prices(self, member: Member) -> tuple[int | None, int | None]:
        frame = self.last.get(member.key)
        return (paise(frame.get("ltp")), paise(frame.get("cp"))) if frame else (None, None)

    def printed(self, member: Member) -> bool:
        return self.bars.printed(member.key, self.market.today)


@dataclass
class RunLine:
    """One run the replay made."""

    run_key: str
    screen: str
    at: int
    matched: int
    entered: tuple[str, ...]
    exited: tuple[str, ...]
    not_checked: int
    told: bool
    why: str
    late: bool


def _uni_key(kind_spec: Mapping[str, Any]) -> str:
    kind = kind_spec["kind"]
    extra = kind_spec.get("id") or kind_spec.get("underlying") or kind_spec.get("list_id")
    return f"{kind}:{extra}" if extra is not None else kind


def market_for(day: date, calendar: TradingCalendar, auction: frozenset[str], now: int) -> Market:
    windows = calendar.windows() if hasattr(calendar, "windows") else {}
    sessions = sessions_of(day, trading_day=calendar.is_trading_day(day), window=windows.get(day))
    previous = previous_trading_day(day, calendar.is_trading_day)
    return Market(
        day_key(day), sessions, day_key(previous) if previous else 0, {},
        Feed("replay", True, now), (), True, auction,
    )  # fmt: skip


def members_from_keys(keys: Iterable[str]) -> list[Member]:
    """A universe for a synthetic recording: every key as it is, the symbol its tail."""
    out = []
    for key in sorted(set(keys)):
        segment, _, tail = key.partition("|")
        exchange = {"NSE_EQ": "NSE", "NSE_FO": "NFO", "NSE_INDEX": "NSE_INDEX"}.get(
            segment, segment
        )
        out.append(Member(key, tail or key, exchange, segment, False))
    return out


async def replay_day(
    ticks: Iterable[tuple[str, int, dict]],
    screens: Sequence[Mapping[str, Any]],
    universes: Mapping[str, Sequence[Member]],
    calendar: TradingCalendar,
    *,
    day: date | None = None,
    members: Mapping[int, Mapping[str, mem.Row]] | None = None,
) -> list[RunLine]:
    """The pipeline over one day's ticks, judged at each tick's arrival. ``members`` are the
    screens' members at the day's start (``--from-db``): carried from earlier days, they are not
    entries of this day."""
    bars = Bars()
    ticks = iter(ticks)
    first = next(ticks, None)
    if first is None:
        return []
    day = day or datetime.fromtimestamp(first[1] / 1000, IST).date()
    auction = frozenset(m.key for members in universes.values() for m in members if m.cas)
    market = market_for(day, calendar, auction, first[1])
    source = ReplaySource(bars, market, universes)
    plans = {row["id"]: spec.compile_plan(row["spec"]) for row in screens}
    sched = [sched_screen(row) for row in screens]
    rows: dict[int, dict[str, mem.Row]] = {
        row["id"]: dict((members or {}).get(row["id"], {})) for row in screens
    }
    told: dict[int, dict[str, al.Told]] = {row["id"]: {} for row in screens}
    paused: dict[int, bool] = {row["id"]: False for row in screens}
    recorded: set[str] = set()
    lines: list[RunLine] = []
    clock = first[1] - first[1] % 1000

    async def run_due(now: int) -> None:
        market_now = Market(*market[:4], Feed("replay", True, now), (), True, auction)
        source.market = market_now
        for due in scheduler.due(sched, market_now.sessions, now, recorded.__contains__,
                                 printed=lambda s: _printed(source, universes, screens, s)):  # fmt: skip
            recorded.add(str(due.key))
            row = next(r for r in screens if r["id"] == due.screen.id)
            plan = plans[row["id"]]
            members = list(source.members(row["spec"]["universe"]))
            judged = await evaluate(
                plan, members, source, source.context(due.boundary.end, due.key.session)
            )
            lines.append(_apply(row, due, judged, rows[row["id"]], told[row["id"]], paused, now))

    for key, ts_recv, frame in [first, *ticks]:
        while clock <= ts_recv:  # the service's 1 s timer, on the recording's clock
            source.market = Market(*market[:4], Feed("replay", True, clock), (), True, auction)
            bars.on_clock(clock, source.market)
            await run_due(clock)
            clock += 1000
        source.market = Market(*market[:4], Feed("replay", True, ts_recv), (), True, auction)
        source.last[key] = frame
        bars.on_tick(frame, ts_recv, source.market)
    for _ in range(6):  # the grace after the last tick: the last candle seals and runs
        bars.on_clock(clock, source.market)
        await run_due(clock)
        clock += 1000
    return lines


def _printed(source, universes, screens, screen_sched) -> bool:
    row = next(r for r in screens if r["id"] == screen_sched.id)
    cas = [m for m in source.members(row["spec"]["universe"]) if m.cas]
    return all(source.printed(m) for m in cas) if cas else True


def _apply(
    row, due, judged: Sequence[Judged], rows: dict[str, mem.Row], told: dict, paused: dict, now: int
) -> RunLine:
    stored = row["spec"]
    target = stored.get("target") or {}
    grace = mem.Grace.of(target.get("grace"))
    results = [
        mem.Result(
            j.member.key,
            j.member.symbol,
            j.member.exchange,
            j.outcome.kind,
            j.outcome.rank,
            j.prices[0],
        )
        for j in judged
    ]
    applied = mem.apply_run(
        rows, results, mem.Run(len(rows) + 1, due.boundary.end, due.key.session, due.key.version, last=due.last),
        grace, items={}, max_adds=target.get("max_adds_per_run", 10),
        order=(stored.get("rank") or {}).get("order", "desc"), free=mem.LIST_MAX,
    )  # fmt: skip
    rows.update(applied.rows)
    entered, exited = al.named(applied.changes)
    matched = sum(1 for j in judged if j.outcome.kind == HIT)
    policy = al.Policy.of(stored.get("alerts"))
    decision = al.decide(
        policy, al.Facts(due.key.session, due.key.k, matched, entered, exited, now if due.late else None),
        told, paused=paused[row["id"]],
    )  # fmt: skip
    if decision.tell:
        told.update(decision.told)
        if decision.pause:
            paused[row["id"]] = True
    return RunLine(
        str(due.key), row["name"], due.boundary.end, matched,
        tuple(n.symbol for n in entered), tuple(n.symbol for n in exited),
        sum(1 for j in judged if j.outcome.kind == NOT_CHECKED),
        decision.tell, words.told_words(decision, policy), due.late,
    )  # fmt: skip


def line_words(line: RunLine) -> str:
    parts = [
        words.hm(line.at), line.screen, f"{line.matched} matched",
        f"{len(line.entered)} entered" + (f" ({', '.join(line.entered[:5])})" if line.entered else ""),
        f"{len(line.exited)} left" + (f" ({', '.join(line.exited[:5])})" if line.exited else ""),
    ]  # fmt: skip
    if line.not_checked:
        parts.append(f"{line.not_checked} not checked")
    parts.append(("digest: " if line.told else "") + line.why)
    if line.late:
        parts.append("late")
    return "  ".join(parts)


def compare(
    lines: Sequence[RunLine], live: Sequence[Mapping[str, Any]]
) -> tuple[list[str], list[str]]:
    """(explained, unexplained) differences between the replay and the live run rows."""
    explained, unexplained = [], []
    by_key = {row["run_key"]: row for row in live if row["status"] == "done"}
    seen = set()
    for line in lines:
        row = by_key.get(line.run_key)
        seen.add(line.run_key)
        if row is None:
            unexplained.append(
                f"{line.run_key}: the service made no run; the replay found {line.matched} matched"
            )
            continue
        same = (row["matched"], row["entered"], row["exited"]) == (
            line.matched,
            len(line.entered),
            len(line.exited),
        )
        if same:
            continue
        text = (
            f"{line.run_key}: service {row['matched']} matched, {row['entered']} entered, {row['exited']} left; "
            f"replay {line.matched} matched, {len(line.entered)} entered, {len(line.exited)} left"
        )
        marks = row.get("marks") or {}
        if marks.get("repaired") or marks.get("late") is not None:
            explained.append(
                text
                + " (the service's run was "
                + ("repaired" if marks.get("repaired") else "late")
                + ")"
            )
        else:
            unexplained.append(text)
    for key in by_key:
        if key not in seen:
            unexplained.append(f"{key}: the service made this run; the replay did not")
    return explained, unexplained


def _say(text: str) -> None:
    sys.stdout.write(text + "\n")


def main(argv: Sequence[str] | None = None, say: Callable[[str], None] = _say) -> int:
    parser = argparse.ArgumentParser(description="the screener's replay check")
    parser.add_argument("recording", type=Path)
    parser.add_argument(
        "--from-db", action="store_true", help="the active screens and universes of screener.db"
    )
    parser.add_argument(
        "--db", type=Path, default=None, help="screener.db (default: the state directory's)"
    )
    parser.add_argument(
        "--spec", type=Path, default=None, help="one screen as JSON (no database needed)"
    )
    parser.add_argument(
        "--keys", default=None, help="comma-separated keys as the universe (with --spec)"
    )
    parser.add_argument("--screen", type=int, default=None, help="only this screen id")
    parser.add_argument("--compare", action="store_true", help="compare with the live run rows")
    parser.add_argument("--holidays", type=Path, default=Path(HOLIDAYS_FILE))
    parser.add_argument("--extra-days", type=Path, default=Path(EXTRA_TRADING_DAYS_FILE))
    args = parser.parse_args(argv)
    if not args.recording.is_file():
        say(f"No recording at {args.recording}.")
        return CANNOT
    calendar = TradingCalendar(args.holidays, args.extra_days)
    screens: list[dict] = []
    universes: dict[str, Sequence[Member]] = {}
    engine = None
    if args.from_db or args.compare:
        from . import store as store_module

        path = args.db or store_module.default_path()
        try:
            engine = open_read_only(path)
        except Exception as exc:  # StoreError in words
            say(f"screener.db could not be opened read-only: {exc}")
            return CANNOT
    try:
        if args.spec is not None:
            try:
                stored = spec.check(json.loads(args.spec.read_text(encoding="utf-8")))
            except Exception as exc:
                say(f"The screen file could not be used: {exc}")
                return CANNOT
            screens = [
                {
                    "id": args.screen or 0,
                    "version": 1,
                    "name": stored["name"],
                    "spec": stored,
                    "status": "active",
                }
            ]
        elif engine is not None:
            screens = _screens_from_db(engine, args.screen)
        if not screens:
            say("No active screen to replay.")
            return CANNOT
        if args.keys:
            members = members_from_keys(k.strip() for k in args.keys.split(",") if k.strip())
            universes = {_uni_key(row["spec"]["universe"]): members for row in screens}
        elif engine is not None:
            universes = _universes_from_db(engine, screens)
        missing = [
            _uni_key(r["spec"]["universe"])
            for r in screens
            if _uni_key(r["spec"]["universe"]) not in universes
        ]
        if missing:
            say(
                "The universes "
                + ", ".join(sorted(set(missing)))
                + " are not in screener.db; give --keys or run the service once."
            )
            return CANNOT
        ticks = read_recording(args.recording)
        first = next(ticks, None)
        if first is None:
            say(f"The recording {args.recording.name} holds no tick.")
            return CANNOT
        day = datetime.fromtimestamp(first[1] / 1000, IST).date()
        start = epoch_ms(day, 0)
        carried = _members_from_db(engine, screens, start) if engine is not None else {}
        lines = asyncio.run(
            replay_day(
                itertools.chain([first], ticks),
                screens,
                universes,
                calendar,
                day=day,
                members=carried,
            )  # fmt: skip
        )
        for line in lines:
            say(line_words(line))
        say(f"{len(lines)} runs replayed from {args.recording.name}")
        if not args.compare:
            return CLEAN
        live = _runs_from_db(engine, screens, day_key(day))
        explained, unexplained = compare(lines, live)
        for text in explained:
            say("explained: " + text)
        for text in unexplained:
            say("DIFFERENT: " + text)
        if unexplained:
            say(f"Replay check: {len(unexplained)} difference(s) the marks do not explain.")
            return DIFFERENT
        say(
            f"Replay check: {len(lines)} runs compared, all match"
            + (f" ({len(explained)} explained)" if explained else "")
        )
        return CLEAN
    finally:
        if engine is not None:
            engine.dispose()


def _screens_from_db(engine, only: int | None) -> list[dict]:
    from sqlalchemy import text

    rows = []
    with engine.connect() as conn:
        for found in conn.execute(
            text("SELECT id, version, name, spec, status FROM screen WHERE principal = :p"),
            {"p": OWNER},
        ).mappings():
            if found["status"] != "active" or (only is not None and found["id"] != only):
                continue
            rows.append({**dict(found), "spec": json.loads(found["spec"])})
    return rows


def _universes_from_db(engine, screens: Sequence[Mapping[str, Any]]) -> dict[str, Sequence[Member]]:
    from sqlalchemy import text

    out: dict[str, Sequence[Member]] = {}
    with engine.connect() as conn:
        for row in screens:
            name = _uni_key(row["spec"]["universe"])
            if name in out:
                continue
            found = (
                conn.execute(
                    text(
                        "SELECT key, symbol, exchange, segment, cas FROM universe_member WHERE universe = :u ORDER BY key"
                    ),
                    {"u": name},
                )
                .mappings()
                .all()
            )
            if found:
                out[name] = [
                    Member(r["key"], r["symbol"], r["exchange"], r["segment"], bool(r["cas"]))
                    for r in found
                ]
    return out


def _members_from_db(
    engine, screens: Sequence[Mapping[str, Any]], start: int
) -> dict[int, dict[str, mem.Row]]:
    """Each screen's members at the day's start (``start``, epoch ms), from its membership rows:
    a row nothing touched today is as it was; one a run or the owner touched today is taken as
    matching at the start (its misses before today are not kept), or as dismissed. A row that
    entered today, or left before it, is no member at the start (release review MEDIUM-11)."""
    from sqlalchemy import text

    ids = {row["id"] for row in screens}
    out: dict[int, dict[str, mem.Row]] = {sid: {} for sid in ids}
    with engine.connect() as conn:
        found = conn.execute(
            text("SELECT * FROM membership WHERE principal = :p ORDER BY id"), {"p": OWNER}
        ).mappings()
        for row in found:
            if row["screen_id"] not in ids or row["first_seen_at"] >= start:
                continue
            if row["status"] == mem.EXITED and (row["exited_at"] or 0) < start:
                continue
            fields = {n: row[n] for n in mem.Row.__dataclass_fields__ if n in row.keys()}
            for flag in ("pinned", "added_by_screener", "in_list"):
                fields[flag] = bool(fields.get(flag))
            if row["updated_at"] >= start:  # touched today: how it stood this morning is not kept
                status = mem.DISMISSED if row["status"] == mem.DISMISSED else mem.ACTIVE
                fields.update(status=status, misses=0, missing_session=0, exited_at=None)
            out[row["screen_id"]][row["key"]] = mem.Row(**fields)
    return out


def _runs_from_db(engine, screens: Sequence[Mapping[str, Any]], session: int) -> list[dict]:
    """The live runs of the replayed day only: earlier days' runs are no difference."""
    from sqlalchemy import text

    ids = [row["id"] for row in screens]
    with engine.connect() as conn:
        found = (
            conn.execute(
                text(
                    "SELECT run_key, matched, entered, exited, marks, status, screen_id FROM run "
                    "WHERE principal = :p AND session = :s"
                ),
                {"p": OWNER, "s": session},
            )
            .mappings()
            .all()
        )
    out = []
    for row in found:
        if row["screen_id"] in ids:
            out.append({**dict(row), "marks": json.loads(row["marks"]) if row["marks"] else {}})
    return out


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
