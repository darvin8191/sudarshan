"""The golden vectors (market_vectors.json) through the engine: the contract of market alerts.

Alert vectors run through the Book, the runtime's own path: feed ticks in, the gate, candles, the
engine's decisions out. ``World`` plays the runtime: the vectors' calendar, the feed's health,
pauses, and a real market.db: fires recorded with their state, a flush once a second, the
owner's pause, resume and edits written as the bridge writes them, and a restart that rebuilds
the Book from what the file holds (a clean stop flushes first).
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from sudarshan.alerts.market import clock, engine, sessions, spec
from sudarshan.alerts.market.book import Book
from sudarshan.alerts.market.grain import Grain, edge_down, edge_up, paise, rupees, to_decimal
from sudarshan.alerts.market.store import MarketStore
from sudarshan.autologin.schedule import IST, parse_window

VECTORS = json.loads(Path(__file__).with_name("market_vectors.json").read_text(encoding="utf-8"))
DEFAULTS = VECTORS["defaults"]
CALENDAR = VECTORS["calendar"]
DIRECTIONS = {spec.UP: "up", spec.DOWN: "down"}
SIDES = {-1: "below", 0: "at", 1: "above", None: None}
#: The flush that saves the Book's changed states runs once a second.
FLUSH_MS = 1000
BOTH = {"telegram": True, "app": True}


def ms(day: str, clock_text: str) -> int:
    """'2026-10-21', '10:00:00.300' -> epoch ms in IST."""
    hh, mm, ss = clock_text.split(":")
    start = datetime.combine(date.fromisoformat(day), datetime.min.time(), IST)
    seconds = int(hh) * 3600 + int(mm) * 60
    return int(start.timestamp()) * 1000 + seconds * 1000 + round(float(ss) * 1000)


def hms(at: int) -> str:
    text = datetime.fromtimestamp(at / 1000, IST).strftime("%H:%M:%S.%f")[:-3]
    return text[:-4] if text.endswith(".000") else text


def number(value):
    return float(value) if isinstance(value, str) else value  # "NaN", "Infinity"


def trades(day: date) -> bool:
    iso = day.isoformat()
    return iso in CALENDAR["windows"] or (day.weekday() < 5 and iso not in CALENDAR["holidays"])


def window_of(day: date):
    text = CALENDAR["windows"].get(day.isoformat())
    return parse_window(text) if text else None


def market(day: date, now: int, *, pauses=(), down=(), status=None, feed=None, replay=False):
    found = {}
    for segment in sessions.SEGMENTS:
        span = sessions.session(segment, day, trading_day=trades(day), window=window_of(day))
        if span is not None:
            found[segment] = span
    previous = sessions.previous_trading_day(day, trades)
    if feed is None:
        out = any(start <= now < end for start, end in down)
        feed = clock.Feed("backoff" if out else "connected", not out, now)
    today = tuple(
        clock.Pause(start, end if end <= now else None, cause)
        for start, end, cause in pauses
        if start <= now and datetime.fromtimestamp(start / 1000, IST).date() == day
    )
    key = sessions.day_key(day)
    prev = sessions.day_key(previous) if previous else 0
    return clock.Market(key, found, prev, status or {}, feed, today, replay)


def expand(steps: list[dict], day: str) -> list[dict]:
    """Steps with each ``cycle`` written out: ``times`` rounds of its prices, ``every_ms`` apart."""
    out = []
    for raw in steps:
        cycle = raw.get("cycle")
        if cycle is None:
            out.append(raw)
            continue
        at = ms(raw.get("day", day), cycle["from"])
        for _ in range(cycle["times"]):
            for price in cycle["ltp"]:
                out.append({"at": hms(at) if at % 1000 else hms(at) + ".000", "ltp": price})
                at += cycle["every_ms"]
    return out


def fields_of(vector: dict) -> dict:
    instrument = VECTORS["instruments"].get(vector.get("instrument"), DEFAULTS["instrument"])
    return instrument | vector.get("instrument_fields", {}) | {"kind": "price"} | vector["alert"]


def make_row(vector: dict, fields: dict, version: int = 1) -> dict:
    day = vector.get("day", DEFAULTS["day"])
    created = ms(day, vector.get("created", DEFAULTS["created"]))
    return spec.check(fields, created) | {"id": 1, "version": version, "status": "active"}


class World:
    """The runtime around the Book, as far as the vectors need one."""

    def __init__(self, vector: dict, path: Path):
        self.vector = vector
        self.day = vector.get("day", DEFAULTS["day"])
        self.created = ms(self.day, vector.get("created", DEFAULTS["created"]))
        self.fields = fields_of(vector)
        self.path = path
        self.store = MarketStore(path)
        self.row = self.store.create("owner", spec.check(self.fields, self.created), self.created)
        self.alert = spec.Alert.of(self.row)
        self.book = Book()
        self.book.put(self.alert)
        self.flushed_at = self.created
        self.pauses: list[tuple[int, int, str]] = []
        self.down: list[tuple[int, int]] = []
        self.fires: list[engine.Fire] = []
        self.notes: list[tuple[int, engine.Reason]] = []
        self.stop: engine.Outcome | None = None

    def flush(self, now: int) -> None:
        self.store.save_states("owner", self.book.changed(), now)
        self.flushed_at = now

    def owner(self, now: int, fields: dict, **change) -> None:
        """The owner's edit, pause or resume, written to market.db as the bridge writes it."""
        self.row = self.store.update("owner", self.alert.id, self.row["version"], fields, now,
                                     **change)  # fmt: skip
        self.alert = spec.Alert.of(self.row)

    def market(self, on: str, now: int) -> clock.Market:
        return market(date.fromisoformat(on), now, pauses=self.pauses, down=self.down)

    def frame(self, raw: dict, on: str) -> dict:
        """A trade or quote step as the feed's frame: its fields, ltt and ts_recv in epoch ms."""
        frame = {"key": self.alert.key, "ts_recv": ms(on, raw.get("recv", raw["at"]))}
        if "ltp" in raw:
            frame |= {"ltp": number(raw["ltp"]), "ltt": ms(on, raw.get("ltt", raw["at"]))}
            frame["ltq"] = raw.get("ltq", 1)
        for name in ("bid", "ask", "cp", "atp", "vtt"):
            if name in raw:
                frame[name] = number(raw[name])
        ohlc = {name: number(raw[name]) for name in ("open", "high", "low") if name in raw}
        if ohlc:  # the 1d candle, dated as the feed dates it: its day's IST midnight
            frame["ohlc"] = ohlc | {"ts": ms(raw.get("ohlc_day", on), "00:00:00")}
        return frame

    def take(self, outcomes, now: int) -> None:
        """Each outcome as the runtime writes it: a fire with its state and stop in one
        transaction, an automatic stop or pause on the version it was judged at."""
        for out in outcomes:
            if out.fire is not None:
                self.fires.append(out.fire)
                reason = out.reason.code if out.reason else ""
                self.store.record_fire("owner", out.fire, out.state, title="t", line="l",
                                       value="v", level="l", channels=BOTH, status=out.status,
                                       status_reason=reason)  # fmt: skip
            elif out.status is not None:
                self.store.set_status("owner", self.alert.id, out.status, out.reason.code, now,
                                      version=self.alert.version)  # fmt: skip
            if out.note is not None:
                self.notes.append((now, out.note))
            if out.status is not None:
                self.stop = out
                self.alert = dataclasses.replace(self.alert, status=out.status)

    def run(self, raw: dict) -> None:
        on = raw.get("day", self.day)
        if "pause" in raw:
            start, end = (ms(on, t) for t in raw["pause"])
            self.pauses.append((start, end, "feed"))
            self.down.append((start, end))
            return
        if "restart" in raw:
            start, end = (ms(on, t) for t in raw["restart"])
            self.pauses.append((start, end, "service"))
            self.flush(start)  # a clean stop
            self.store.close()
            self.store = MarketStore(self.path)  # only what market.db holds comes back
            self.row = self.store.alert("owner", self.alert.id)
            self.alert = spec.Alert.of(self.row)
            self.book = Book()
            self.book.put(self.alert, self.store.states("owner").get(self.alert.id))
            self.flushed_at = end
            return
        now = ms(on, raw["at"])
        if now - self.flushed_at >= FLUSH_MS:
            self.flush(now)
        if raw.get("clock"):
            self.take(self.book.on_clock(now, self.market(on, now)), now)
        elif raw.get("suspend"):
            self.owner(now, self.row, status="paused")
            self.book.put(self.alert)
        elif raw.get("resume"):
            self.owner(now, self.row, status="active")
            self.book.put(self.alert, engine.resume(self.book.state(self.alert.id)))
        elif "edit" in raw:
            self.fields |= raw["edit"]
            fields = spec.check(self.fields, self.created)
            reset = spec.changes_condition(self.row, fields)
            self.owner(now, fields, reset_state=reset)
            self.book.put(self.alert, engine.State() if reset else None)
        elif "seed" in raw:
            snap = raw["seed"]
            day = snap.get("day", on)
            frame = {"key": self.alert.key, "ts_recv": now, "ltt": ms(day, snap["ltt"])}
            frame |= {name: number(snap[name]) for name in ("ltp", "bid", "ask") if name in snap}
            seeded = self.book.seed(self.alert.id, frame, self.market(on, now))
            assert seeded == raw.get("seeded", True), raw
        else:
            result = self.book.on_tick(self.frame(raw, on), now, self.market(on, now))
            if "check" in raw:
                assert result.check == raw["check"], (raw, result.check)
            self.take(result.outcomes, now)


def seen(alert: spec.Alert, fire: engine.Fire) -> dict:
    found = fire.found_later
    return {
        "at": hms(fire.at),
        "day": sessions.key_day(fire.session).isoformat(),
        "trade_time": hms(fire.trade_time),
        "value": str(rupees(fire.value)),
        "level": str(rupees(fire.level)),
        "direction": DIRECTIONS[fire.direction],
        "n": fire.n,
        "candle": fire.candle,
        "claim": fire.claim,
        "opening": fire.opening,
        "previous": None if fire.previous is None else str(rupees(fire.previous)),
        "found_later": None if found is None else [hms(found[0]), hms(found[1])],
        "base": None if fire.base is None else str(rupees(fire.base)),
        "price": None if fire.price is None else str(rupees(fire.price)),
    }


ALERTS = VECTORS["alerts"]


@pytest.mark.parametrize("vector", ALERTS, ids=[v["id"] for v in ALERTS])
def test_alert_vector(vector, tmp_path):
    world = World(vector, tmp_path / "market.db")
    for raw in expand(vector["steps"], world.day):
        world.run(raw)
    world.store.close()
    fires = world.fires
    if "fire_count" in vector:
        assert len(fires) == vector["fire_count"]
    else:
        assert len(fires) == len(vector["fires"]), [hms(f.at) for f in fires]
    for fire, expected in zip(fires, vector.get("fires", []), strict=False):
        shown = seen(world.alert, fire)
        assert {name: shown[name] for name in expected} == expected
    assert world.alert.status == vector.get("status", world.alert.status)
    if "reason" in vector:
        assert world.stop.reason.code == vector["reason"]
    if "reason_at" in vector:
        assert hms(world.stop.reason.at) == vector["reason_at"]
    if "side" in vector:
        assert SIDES[world.book.state(world.alert.id).side] == vector["side"]
    notes = []
    for at, note in world.notes:
        shown = {"at": hms(at), "note": note.code}
        if note.code in (engine.NOT_CHECKED, engine.NOT_JUDGED):
            shown["candle"] = hms(note.at)[:5]
        notes.append(shown)
    assert notes == vector.get("notes", [])


GATE = VECTORS["gate"]


@pytest.mark.parametrize("vector", GATE, ids=[v["id"] for v in GATE])
def test_gate_vector(vector):
    day = vector.get("day", DEFAULTS["day"])
    at = ms(day, vector["at"])
    recv = ms(day, vector.get("recv", vector["at"]))
    now = ms(day, vector.get("now", vector.get("recv", vector["at"])))
    given = vector.get("feed", {})
    feed = clock.Feed(
        given.get("state", "connected"), given.get("live", True), now - given.get("age_s", 1) * 1000
    )
    segment = vector.get("segment", "NSE_EQ")
    status = {vector.get("status_of", segment): vector["status"]} if "status" in vector else None
    world = market(
        date.fromisoformat(day), now, status=status, feed=feed, replay=vector.get("replay", False)
    )
    quote = vector.get("quote", False)
    check = clock.gate(world, segment, recv if quote else at, recv, now, trade=not quote)
    assert check == vector["check"]


@pytest.mark.parametrize("vector", VECTORS["snap"], ids=[v["id"] for v in VECTORS["snap"]])
def test_snap_vector(vector):
    """A level's edges in paise, and the tick hint ("Checks at ₹100.05") when it is off the
    tick; a feed price in paise."""
    if "level" in vector:
        level = to_decimal(vector["level"])
        assert [str(rupees(edge_up(level))), str(rupees(edge_down(level)))] == [
            vector["up"],
            vector["down"],
        ]
        grain = Grain.of(vector["tick"])
        up, down = grain.up(level), grain.down(level)
        hint = None if up == down else [str(rupees(edge_up(up))), str(rupees(edge_up(down)))]
        assert hint == vector["checks_at"]
    else:
        count = paise(number(vector["price"]))
        assert count == vector["paise"]
        if count is not None:
            assert str(rupees(count)) == vector["shown"]


@pytest.mark.parametrize("vector", VECTORS["sessions"], ids=[v["id"] for v in VECTORS["sessions"]])
def test_session_vector(vector):
    day = date.fromisoformat(vector["day"])
    window = parse_window(vector["window"]) if vector.get("window") else None
    trading_day = vector.get("trading_day", True)
    found = sessions.session(vector["segment"], day, trading_day=trading_day, window=window)
    if vector["hours"] is None:
        assert found is None
        return
    start, end = (datetime.fromtimestamp(t / 1000, IST) for t in (found.start, found.end))
    assert (start.date(), end.date()) == (day, day)
    assert [f"{start:%H:%M}", f"{end:%H:%M}"] == vector["hours"]


@pytest.mark.parametrize("vector", VECTORS["refused"], ids=[v["id"] for v in VECTORS["refused"]])
def test_refused_vector(vector):
    with pytest.raises(spec.SpecError) as caught:
        make_row(vector, fields_of(vector))
    assert (caught.value.field, caught.value.message) == (vector["field"], vector["message"])


def test_every_vector_has_a_unique_id():
    groups = ("snap", "sessions", "refused", "gate", "alerts", "words")
    ids = [v["id"] for group in groups for v in VECTORS[group]]
    assert len(ids) == len(set(ids))


def test_cycles_expand_in_order():
    cycle = {"cycle": {"from": "10:00:00", "every_ms": 500, "times": 2, "ltp": [1, 2]}}
    steps = expand([cycle], "2026-10-21")
    assert [s["at"] for s in steps] == [
        "10:00:00.000",
        "10:00:00.500",
        "10:00:01.000",
        "10:00:01.500",
    ]
    assert ms("2026-10-21", "10:00:00") - ms("2026-10-21", "00:00:00") == timedelta(
        hours=10
    ) / timedelta(milliseconds=1)
