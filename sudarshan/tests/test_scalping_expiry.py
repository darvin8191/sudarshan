"""The scalping terminal's nearest expiry on an expiry day (v0.8.1 review M3): today's expiry
leaves the list it offers at its contract's stop, not at the F&O close, so the terminal's default
moves to the next expiry then. Until 6 Dec 2026 every contract stops with the F&O close at 15:40;
from 7 Dec an index's stops at 15:30 (and a stock's trades to 15:45). This chooses which contract
to offer; no order goes anywhere differently."""

from __future__ import annotations

from datetime import date

import pytest

from sudarshan import index_contracts
from sudarshan.alerts.market import sessions
from sudarshan.instrument_session import offered_expiries


def ms(day: date, hhmm: str) -> int:
    hours, minutes = map(int, hhmm.split(":"))
    return sessions.epoch_ms(day, hours * 60 + minutes)


@pytest.mark.parametrize(
    ("now", "index", "offered", "rolls"),
    [
        # Tue 1 Dec 2026, before the change: NIFTY's weekly stops with the F&O close.
        ((date(2026, 12, 1), "15:30"), True, ["01DEC26", "08DEC26"], (date(2026, 12, 1), "15:40")),
        ((date(2026, 12, 1), "15:39"), True, ["01DEC26", "08DEC26"], (date(2026, 12, 1), "15:40")),
        ((date(2026, 12, 1), "15:40"), True, ["08DEC26"], None),
        # Tue 8 Dec 2026, after it: the expiring index contract stops at 15:30.
        ((date(2026, 12, 8), "15:29"), True, ["08DEC26", "15DEC26"], (date(2026, 12, 8), "15:30")),
        ((date(2026, 12, 8), "15:30"), True, ["15DEC26"], None),
        ((date(2026, 12, 8), "15:40"), True, ["15DEC26"], None),
        # A stock's monthly trades to 15:45 from 7 Dec.
        ((date(2026, 12, 29), "15:30"), False, ["29DEC26", "26JAN27"], (date(2026, 12, 29), "15:45")),
        ((date(2026, 12, 29), "15:45"), False, ["26JAN27"], None),
    ],
)
def test_today_s_expiry_leaves_the_list_at_its_contract_s_stop(now, index, offered, rolls):
    listed = {date(2026, 12, 1): ["01DEC26", "08DEC26"], date(2026, 12, 8): ["08DEC26", "15DEC26"],
              date(2026, 12, 29): ["29DEC26", "26JAN27"]}[now[0]]  # fmt: skip
    found, rolls_at = offered_expiries(listed, ms(*now), index=index)
    assert found == offered
    assert rolls_at == (ms(*rolls) if rolls else None)


def test_another_day_s_list_is_left_as_it_is():
    listed = ["08DEC26", "15DEC26"]
    assert offered_expiries(listed, ms(date(2026, 12, 7), "15:50"), index=True) == (listed, None)


@pytest.fixture
def client(monkeypatch):
    from flask import Flask

    import blueprints.scalping as scalping
    import utils.session as session_utils

    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="test")  # pragma: allowlist secret
    app.register_blueprint(scalping.scalping_bp)
    monkeypatch.setattr(session_utils, "is_session_valid", lambda: True)
    monkeypatch.setattr(scalping, "_get_api_key", lambda: "test-key")
    monkeypatch.setattr(scalping, "get_expiry_dates", lambda **kw: (
        True, {"status": "success", "data": ["08-DEC-26", "15-DEC-26"]}, 200))  # fmt: skip
    monkeypatch.setattr(index_contracts, "underlyings", lambda: index_contracts.KNOWN)
    return app.test_client(), scalping, monkeypatch


@pytest.mark.parametrize(
    ("clock", "data", "rolls"),
    [("15:29", ["08DEC26", "15DEC26"], "15:30"), ("15:30", ["15DEC26"], None)],
)
def test_the_terminal_s_nearest_expiry_rolls_at_15_30_on_8_december(client, clock, data, rolls):
    test_client, scalping, monkeypatch = client
    day = date(2026, 12, 8)
    monkeypatch.setattr(scalping, "_now_ms", lambda: ms(day, clock))
    answer = test_client.get("/scalping/api/expiry?underlying=NIFTY&exchange=NFO")
    assert answer.status_code == 200
    body = answer.get_json()
    assert body["data"] == data
    assert body["rolls_at"] == (ms(day, rolls) if rolls else None)
