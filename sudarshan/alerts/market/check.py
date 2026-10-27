"""The replay check (M10.1): what market alerts would have told on a recorded day, offline.

    python -m sudarshan.alerts.market.check /var/lib/sudarshan-feed/ticks/2026-10-21.jsonl.gz

Runs the pure engine over the feed's recording of a day against the alerts in market.db, and
prints what would have fired, with its time, its words and its marks, then a summary. It sends
nothing and writes nothing: market.db is opened read-only (it is never created or migrated, A7.p),
and openalgo.db, for the stocks that close by auction, only read.

How it judges, as the service would, with what a recording cannot hold left out:

- the clock is each tick's own arrival (``ts_recv``), and the timer runs every second of it;
- the gate uses the calendar only: the exchange's status messages and the feed's health are not
  recorded, so the feed counts as healthy and the segment as open within its session;
- every alert that is Active now starts the day fresh (``--all`` adds the Paused and Stopped
  ones as if Active): its first value only seeds, so an opening gap from the day before is not
  told, and no pause is marked.

Compare its lines with the day's History on the Alerts page; a fire the service told and this
did not (or the other way round) is worth a look in the journal.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sudarshan.alerts.market import engine, sessions, spec, words
from sudarshan.alerts.market.book import Book
from sudarshan.alerts.market.clock import ARRIVAL_GRACE_MS, Feed, Market
from sudarshan.autologin.schedule import (
    EXTRA_TRADING_DAYS_FILE,
    HOLIDAYS_FILE,
    IST,
    TradingCalendar,
)

#: After the day's last session, the timer runs this much longer (candles judged at their end).
TAIL_MS = 60_000


@dataclass(frozen=True)
class Told:
    """One thing the service would have done: a fire, a stop or a note."""

    at: int
    alert_id: int
    text: str
    fire: engine.Fire | None = None


def judge_day(
    ticks: Iterable[tuple[str, int, dict]],
    alerts: list[spec.Alert],
    day: date,
    *,
    found: dict[str, sessions.Session],
    previous: int,
    cas: frozenset[str] = frozenset(),
) -> tuple[list[Told], dict[str, int]]:
    """The engine over one day's ticks, (key, ts_recv, frame) in order, judged at ``ts_recv``."""
    book = Book()
    by_id = {}
    for alert in alerts:
        live = dataclasses.replace(alert, status="active")
        by_id[live.id] = live
        book.put(live)
    keys = {alert.key for alert in by_id.values()}
    today = sessions.day_key(day)
    auction = frozenset(alert.key for alert in by_id.values() if alert.key in cas)
    told: list[Told] = []
    counts = {"ticks": 0, "judged": 0}

    def market(now: int) -> Market:
        return Market(today, found, previous, {}, Feed("replay", True, now), (), True, auction)

    def note(outcomes, now: int) -> None:
        for out in outcomes:
            alert = by_id[out.alert_id]
            if out.fire is not None:
                line = words.line(alert, out.fire, day)
                told.append(Told(out.fire.at, alert.id, line, out.fire))
            if out.status is not None:
                reason = words.status_reason(out.reason)
                told.append(Told(now, alert.id, f"{out.status.capitalize()}: {reason}"))
            if out.note is not None and out.note.code in _NOTED:
                told.append(Told(now, alert.id, f"Note: {words.note_words(out.note)}"))

    clock = None
    for key, ts_recv, frame in ticks:
        if key not in keys:
            continue
        if clock is None:
            clock = ts_recv - ts_recv % 1000
        while clock <= ts_recv:  # the service's 1 s timer, on the recording's clock
            note(book.on_clock(clock, market(clock)), clock)
            clock += 1000
        counts["ticks"] += 1
        result = book.on_tick(frame, ts_recv, market(ts_recv))
        counts["judged"] += result.check == "ok"
        note(result.outcomes, ts_recv)
    if clock is not None and found:
        last = max(s.auction_until or s.end for s in found.values()) + ARRIVAL_GRACE_MS + TAIL_MS
        while clock <= last:
            note(book.on_clock(clock, market(clock)), clock)
            clock += 1000
    return told, counts


#: The notes worth a line: a candle not judged at its close (the others are the card's).
_NOTED = {"not_checked", "not_judged"}


def read_recording(path: Path) -> Iterable[tuple[str, int, dict]]:
    """(key, ts_recv, frame) per recorded tick, as sudarshan.feed.replay reads them."""
    from sudarshan.feed.replay import read_ticks

    for key, ts_recv, payload in read_ticks(path):
        try:
            frame = json.loads(payload)
        except ValueError:
            continue
        if isinstance(frame, dict) and ts_recv:
            yield key, ts_recv, frame


def day_of(path: Path) -> date:
    """The day a recording is named for: ticks/YYYY-MM-DD.jsonl.gz."""
    return date.fromisoformat(path.name[:10])


def main(argv: list[str] | None = None) -> int:
    # The store and the master read import OpenAlgo's database package: only when run.
    from sudarshan.alerts.market.days import Days, MasterUnavailable, cas_keys, fno_stocks
    from sudarshan.alerts.market.migrate import default_path
    from sudarshan.alerts.market.migrations import SchemaTooNew
    from sudarshan.alerts.market.store import MarketStore, StoreError

    parser = argparse.ArgumentParser(
        prog="python -m sudarshan.alerts.market.check",
        description="What market alerts would have told on a recorded day. Reads only.",
    )
    parser.add_argument("recording", type=Path, help="a feed recording: ticks/YYYY-MM-DD.jsonl.gz")
    parser.add_argument("--db", type=Path, help="market.db (default: the service's own)")
    parser.add_argument("--all", action="store_true", help="Paused and Stopped alerts too")
    parser.add_argument("--openalgo-db", type=Path, help="openalgo.db, for the stocks that close "
                        "by auction (their futures in the master contract)")  # fmt: skip
    parser.add_argument("--holidays", type=Path, default=Path(HOLIDAYS_FILE))
    parser.add_argument("--extra-days", type=Path, default=Path(EXTRA_TRADING_DAYS_FILE))
    args = parser.parse_args(argv)
    out, err = sys.stdout, sys.stderr
    try:
        day = day_of(args.recording)
    except ValueError:
        err.write("The recording's name must start with its day: YYYY-MM-DD.jsonl.gz\n")
        return 2
    try:
        store = MarketStore(args.db or default_path(), read_only=True)
    except (StoreError, SchemaTooNew) as exc:  # both speak in the owner's words
        err.write(f"{exc}\n")
        return 2
    try:
        rows = store.alerts("owner")
    except StoreError as exc:
        err.write(f"{exc}\n")
        return 2
    finally:
        store.close()
    alerts = [spec.Alert.of(row) for row in rows if args.all or row["status"] == "active"]
    found, previous = Days(TradingCalendar(args.holidays, args.extra_days)).of(day)
    fno = frozenset()
    if args.openalgo_db is not None:
        try:
            fno = fno_stocks(args.openalgo_db)
        except MasterUnavailable:
            err.write("openalgo.db could not be read; no stock is taken to close by auction\n")
    cas = cas_keys(alerts, day, fno)
    try:
        told, counts = judge_day(
            read_recording(args.recording), alerts, day, found=found, previous=previous, cas=cas
        )
    except OSError as exc:
        err.write(f"The recording could not be read: {exc.strerror or exc}\n")
        return 2
    if not found:
        out.write(f"{day} is not a trading day in the calendar files: nothing is judged.\n")
    for item in told:
        moment = datetime.fromtimestamp(item.at / 1000, IST)
        out.write(f"{moment:%H:%M:%S}.{moment.microsecond // 1000:03d}  #{item.alert_id}  "
                  f"{item.text}\n")  # fmt: skip
    fires = sum(1 for item in told if item.text.startswith("Alert:"))
    out.write(
        f"{day}: {len(alerts)} alerts, {counts['ticks']} ticks on their instruments, "
        f"{counts['judged']} judged, {fires} would have fired.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
