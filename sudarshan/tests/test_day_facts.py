"""The day's facts for the dashboard's status row and hours sheet (M3.5, M3.6; D1 of the screens
design), from the shared vectors on both sides of 7 Dec 2026, and the route that answers them:
the logged-in session only, a family phone refused, the calendar and the master read, never the
broker."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from sudarshan import index_contracts, regimes
from sudarshan import instrument_session as facts_module
from sudarshan.autologin.schedule import IST

VECTORS = json.loads(Path(__file__).with_name("regimes_vectors.json").read_text(encoding="utf-8"))
DAY_FACTS = VECTORS["day_facts"]
MASTER = {
    exchange: {name: frozenset(map(date.fromisoformat, days)) for name, days in names.items()}
    for exchange, names in DAY_FACTS["master"].items()
}
INDICES = frozenset(DAY_FACTS["indices"])


def at(text: str) -> int:
    """'2026-11-17 15:40', IST, in epoch ms."""
    moment = datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=IST)
    return int(moment.timestamp()) * 1000


def on(day: date, hhmm: str) -> int:
    return at(f"{day.isoformat()} {hhmm}")


def shown(epoch_ms: int | None, day: date | None = None) -> str | None:
    """An instant as the vectors write it: HH:MM on ``day``, otherwise YYYY-MM-DD HH:MM."""
    if epoch_ms is None:
        return None
    moment = datetime.fromtimestamp(epoch_ms / 1000, IST)
    return moment.strftime("%H:%M" if moment.date() == day else "%Y-%m-%d %H:%M")


def hours_on(day: date) -> dict[str, tuple[int, int]]:
    """The vectors' calendar, as OpenAlgo's answers a day: each exchange that trades, its window."""
    text = day.isoformat()
    if text in DAY_FACTS["special"]:
        spans = DAY_FACTS["special"][text]["open"]
    elif text in DAY_FACTS["closed"]:
        spans = DAY_FACTS["closed"][text]["open"]
    elif day.weekday() >= 5:
        spans = {}
    else:
        fo = regimes.fo_close(day)
        close = f"{fo // 60:02d}:{fo % 60:02d}"
        spans = {ex: [start, close if end == "fo" else end] for ex, (start, end) in DAY_FACTS["usual"].items()}  # fmt: skip
    return {exchange: (on(day, start), on(day, end)) for exchange, (start, end) in spans.items()}


def occasion_on(day: date) -> tuple[str, str] | None:
    text = day.isoformat()
    if text in DAY_FACTS["special"]:
        return "special", DAY_FACTS["special"][text]["occasion"]
    if text in DAY_FACTS["closed"]:
        return "holiday", DAY_FACTS["closed"][text]["occasion"]
    return None


def facts_at(now: str, **changed) -> dict:
    given = {
        "hours_on": hours_on,
        "occasion_on": occasion_on,
        "expiries": MASTER,
        "indices": INDICES,
    }
    return facts_module.day_facts(at(now), **{**given, **changed})


def readable(found: dict) -> dict:
    """The answer with its instants written as the vectors write them."""
    day = date.fromisoformat(found["day"])

    def stops(rows):
        return (
            None if rows is None else [{**row, "stops_at": shown(row["stops_at"])} for row in rows]
        )

    return {
        **found,
        "exchanges": {
            exchange: {key: shown(value, day) for key, value in hours.items()}
            for exchange, hours in found["exchanges"].items()
        },
        "expiries": stops(found["expiries"]),
        "stock_expiries": stops(found["stock_expiries"]),
        "next_open": shown(found["next_open"]),
    }


@pytest.mark.parametrize("case", DAY_FACTS["cases"], ids=[c["now"] for c in DAY_FACTS["cases"]])
def test_the_day_s_facts_on_each_vector_day(case):
    assert readable(facts_at(case["now"])) == case["facts"]


def test_before_the_master_is_loaded_the_expiries_are_not_known_and_nothing_else_changes():
    found = readable(facts_at("2026-11-17 11:00", expiries=None))
    expected = next(c["facts"] for c in DAY_FACTS["cases"] if c["now"] == "2026-11-17 11:00")
    assert found == {**expected, "expiries": None, "stock_expiries": None}


def test_a_weekday_the_calendar_gives_nothing_for_is_closed_not_called_a_holiday():
    found = facts_at("2026-11-18 11:00", hours_on=lambda day: {}, occasion_on=lambda day: None)
    assert (found["trading_day"], found["note"], found["occasion"]) == (False, "Closed", None)
    assert found["exchanges"] == {} and found["next_open"] is None  # no trading day ahead either


def test_the_next_open_is_today_s_until_the_market_opens_then_the_next_trading_day_s():
    assert shown(facts_at("2026-11-17 09:05")["next_open"]) == "2026-11-17 09:15"  # pre-open
    assert shown(facts_at("2026-11-17 09:15")["next_open"]) == "2026-11-18 09:15"
    assert shown(facts_at("2026-11-17 18:00")["next_open"]) == "2026-11-18 09:15"
    assert shown(facts_at("2026-11-23 18:00")["next_open"]) == "2026-11-25 09:15"  # over a holiday


@pytest.fixture
def world(monkeypatch):
    """OpenAlgo's calendar and in-memory master, as plain functions over the vectors."""
    import database.market_calendar_db as calendar
    import database.token_db_enhanced as master

    def window(day, exchange):
        span = hours_on(day).get(exchange)
        return (
            None if span is None else {"start_ms": span[0], "end_ms": span[1], "is_special": False}
        )

    def holidays(year):
        rows = [{"date": d, "description": v["occasion"], "holiday_type": "TRADING_HOLIDAY"}
                for d, v in DAY_FACTS["closed"].items()]  # fmt: skip
        rows += [{"date": d, "description": v["occasion"], "holiday_type": "SPECIAL_SESSION"}
                 for d, v in DAY_FACTS["special"].items()]  # fmt: skip
        return [row for row in rows if row["date"].startswith(str(year))]

    texts = {
        (exchange, name): frozenset(day.strftime("%d-%b-%y").upper() for day in days)
        for exchange, names in MASTER.items()
        for name, days in names.items()
    }
    texts[("MCX", "CRUDEOIL")] = frozenset({"17-NOV-26"})  # not an F&O exchange the facts read
    snapshot = SimpleNamespace(expiries_by_exchange_underlying=texts)
    loaded = {"snapshot": snapshot}
    cache = SimpleNamespace(valid_snapshot=lambda: loaded["snapshot"])
    monkeypatch.setattr(calendar, "get_effective_session_window", window)
    monkeypatch.setattr(calendar, "get_holidays_by_year", holidays)
    monkeypatch.setattr(master, "get_cache", lambda: cache)
    monkeypatch.setattr(index_contracts, "underlyings", lambda: index_contracts.Underlyings(
        INDICES, frozenset({"RELIANCE", "BANKBARODA"})))  # fmt: skip
    return loaded


@pytest.mark.parametrize("case", DAY_FACTS["cases"], ids=[c["now"] for c in DAY_FACTS["cases"]])
def test_today_reads_openalgo_s_calendar_and_master(world, case):
    assert readable(facts_module.today(at(case["now"]))) == case["facts"]


def test_today_says_the_expiries_are_not_known_while_the_master_is_not_loaded(world):
    world["snapshot"] = None
    found = facts_module.today(at("2026-11-17 11:00"))
    assert found["expiries"] is None and found["stock_expiries"] is None
    assert found["trading_day"] is True


@pytest.fixture
def client(world, monkeypatch):
    from flask import Flask

    import blueprints.sudarshan_session as route
    import utils.session as user_session

    monkeypatch.setattr(user_session, "session_ended_elsewhere", lambda: False)
    monkeypatch.setattr(route, "_now_ms", lambda: at("2026-12-08 11:00"))
    app = Flask("sudarshan-day-facts-tests")
    app.secret_key = "test-only-secret-key-" + "2" * 32  # pragma: allowlist secret
    app.register_blueprint(route.sudarshan_session_bp)
    return app.test_client()


def sign_in(client) -> None:
    with client.session_transaction() as session:
        session.update(logged_in=True, user="owner", login_time=datetime.now(IST).isoformat())


PATH = "/sudarshan/session/today"


def test_the_route_refuses_a_caller_without_a_session_and_a_family_phone(client):
    assert client.get(PATH, headers={"Accept": "application/json"}).status_code == 401
    # A family phone sends only its own cookie, which OpenAlgo does not read (P6 0.1).
    client.set_cookie("__Host-sudarshan_family", "a-viewer-session")
    refused = client.get(PATH, headers={"Accept": "application/json"})
    assert refused.status_code == 401
    assert b"Expiry" not in refused.data and b"NIFTY" not in refused.data


def test_the_route_answers_the_owner_today_s_facts(client):
    sign_in(client)
    answer = client.get(PATH)
    assert answer.status_code == 200
    body = answer.get_json()
    assert body["status"] == "success"
    expected = next(c["facts"] for c in DAY_FACTS["cases"] if c["now"] == "2026-12-08 11:00")
    assert readable(body["data"]) == expected


def test_the_route_says_in_words_when_the_hours_cannot_be_read(client, monkeypatch):
    import blueprints.sudarshan_session as route

    def broken(now_ms):
        raise RuntimeError("calendar table locked")

    monkeypatch.setattr(facts_module, "today", broken)
    sign_in(client)
    answer = client.get(PATH)
    assert answer.status_code == 503
    assert answer.get_json() == {"status": "error", "message": route.HOURS_NOT_READ}
    assert "locked" not in answer.get_data(as_text=True)
