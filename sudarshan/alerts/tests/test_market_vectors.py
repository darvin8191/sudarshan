"""The golden vectors (market_vectors.json) through the engine: the contract of market alerts."""

from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from sudarshan.alerts.market import engine, sessions, spec
from sudarshan.alerts.market.grain import Grain, to_decimal
from sudarshan.autologin.schedule import IST, parse_window

VECTORS = json.loads(Path(__file__).with_name("market_vectors.json").read_text(encoding="utf-8"))
DEFAULTS = VECTORS["defaults"]
DIRECTIONS = {spec.UP: "up", spec.DOWN: "down"}


def ms(day: str, clock: str) -> int:
    """'2026-10-21', '10:00:00.300' -> epoch ms in IST."""
    hh, mm, ss = clock.split(":")
    start = datetime.combine(date.fromisoformat(day), datetime.min.time(), IST)
    return (
        int(start.timestamp()) * 1000
        + (int(hh) * 3600 + int(mm) * 60) * 1000
        + round(float(ss) * 1000)
    )


def number(value):
    return float(value) if isinstance(value, str) else value  # "NaN", "Infinity"


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
                when = datetime.fromtimestamp(at / 1000, IST)
                out.append({"at": when.strftime("%H:%M:%S.%f")[:-3], "ltp": price})
                at += cycle["every_ms"]
    return out


def make_alert(vector: dict) -> spec.Alert:
    instrument = VECTORS["instruments"].get(vector.get("instrument"), DEFAULTS["instrument"])
    fields = instrument | vector.get("instrument_fields", {}) | {"kind": "price"} | vector["alert"]
    created = ms(DEFAULTS["day"], vector.get("created", DEFAULTS["created"]))
    row = spec.check(fields, created) | {"id": 1, "version": 1, "status": "active"}
    return spec.Alert.of(row)


def run(vector: dict) -> tuple[spec.Alert, engine.State, list[engine.Fire], engine.Outcome | None]:
    alert, state = make_alert(vector), engine.State()
    fires, last = [], None
    day = vector.get("day", DEFAULTS["day"])
    for raw in expand(vector["steps"], day):
        on = raw.get("day", day)
        now = ms(on, raw["at"])
        if raw.get("clock"):
            out = engine.clock(alert, state, now)
        else:
            source = alert.cond.source
            value = alert.grain.ticks(number(raw.get(source)))
            if value is None:
                continue
            at = ms(on, raw["ltt"]) if source == "ltp" and "ltt" in raw else now
            key = sessions.day_key(date.fromisoformat(on))
            out = engine.step(
                alert, state, engine.Obs(value, at, key, alert.cond.up, alert.cond.down), now
            )
        if out is None:
            continue
        state = out.state
        if out.fire is not None:
            fires.append(out.fire)
        if out.status is not None:
            alert, last = dataclasses.replace(alert, status=out.status), out
    return alert, state, fires, last


def clock_of(at: int) -> str:
    text = datetime.fromtimestamp(at / 1000, IST).strftime("%H:%M:%S.%f")[:-3]
    return text[:-4] if text.endswith(".000") else text


ALERTS = VECTORS["alerts"]


@pytest.mark.parametrize("vector", ALERTS, ids=[v["id"] for v in ALERTS])
def test_alert_vector(vector):
    alert, state, fires, last = run(vector)
    if "fire_count" in vector:
        assert len(fires) == vector["fire_count"]
    else:
        assert len(fires) == len(vector["fires"]), [clock_of(f.at) for f in fires]
    for fire, expected in zip(fires, vector.get("fires", []), strict=False):
        seen = {
            "at": clock_of(fire.at),
            "trade_time": clock_of(fire.trade_time),
            "value": str(alert.grain.price(fire.value)),
            "level": str(alert.grain.price(fire.level)),
            "direction": DIRECTIONS[fire.direction],
            "n": fire.n,
        }
        assert {name: seen[name] for name in expected} == expected
    assert alert.status == vector.get("status", alert.status)
    if "reason" in vector:
        assert last.reason.code == vector["reason"]
    if "reason_at" in vector:
        assert clock_of(last.reason.at) == vector["reason_at"]


@pytest.mark.parametrize("vector", VECTORS["snap"], ids=[v["id"] for v in VECTORS["snap"]])
def test_snap_vector(vector):
    grain = Grain.of(vector["tick"])
    if "level" in vector:
        level = to_decimal(vector["level"])
        assert str(grain.price(grain.up(level))) == vector["up"]
        assert str(grain.price(grain.down(level))) == vector["down"]
    else:
        ticks = grain.ticks(number(vector["price"]))
        assert ticks == vector["ticks"]
        if ticks is not None:
            assert str(grain.price(ticks)) == vector["shown"]


@pytest.mark.parametrize("vector", VECTORS["sessions"], ids=[v["id"] for v in VECTORS["sessions"]])
def test_session_vector(vector):
    day = date.fromisoformat(vector["day"])
    window = parse_window(vector["window"]) if vector.get("window") else None
    found = sessions.session(
        vector["segment"], day, trading_day=vector.get("trading_day", True), window=window
    )
    if vector["hours"] is None:
        assert found is None
        return
    start, end = (datetime.fromtimestamp(t / 1000, IST) for t in (found.start, found.end))
    assert (start.date(), end.date()) == (day, day)
    assert [f"{start:%H:%M}", f"{end:%H:%M}"] == vector["hours"]


@pytest.mark.parametrize("vector", VECTORS["refused"], ids=[v["id"] for v in VECTORS["refused"]])
def test_refused_vector(vector):
    with pytest.raises(spec.SpecError) as caught:
        make_alert(vector)
    assert (caught.value.field, caught.value.message) == (vector["field"], vector["message"])


def test_every_vector_has_a_unique_id():
    ids = [v["id"] for group in ("snap", "sessions", "refused", "alerts") for v in VECTORS[group]]
    assert len(ids) == len(set(ids))


def test_cycles_expand_in_order():
    steps = expand(
        [{"cycle": {"from": "10:00:00", "every_ms": 500, "times": 2, "ltp": [1, 2]}}], "2026-10-21"
    )
    assert [s["at"] for s in steps] == [
        "10:00:00.000",
        "10:00:00.500",
        "10:00:01.000",
        "10:00:01.500",
    ]
    assert ms("2026-10-21", "00:00:00") + timedelta(hours=10).seconds * 1000 == ms(
        "2026-10-21", "10:00:00"
    )
