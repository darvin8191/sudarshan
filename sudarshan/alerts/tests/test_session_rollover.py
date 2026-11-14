"""v0.5.2: SUDARSHAN's session gates across the owner's day and the 03:00 IST rollover.

Upstream e2bdd061 compares a login with the most recent 03:00 rollover instead of only today's:
between midnight and 03:00 v0.5.1 took a login of any age. Every SUDARSHAN surface asks that one
check, utils.session.is_session_valid(), through one of three gates:

- extensions.is_owner_session(): every Socket.IO connect, the default namespace and /market, and
  the alerts room's join (SIO1);
- utils.session.check_session_validity: the alerts, auto-login and risk routes;
- blueprints.sudarshan_portfolio._session_required: the portfolio's routes.

The owner's day: he logs in after 03:00 and uses the app into the night, and that login holds until
the next 03:00, past midnight included. A login from before the most recent 03:00 is refused at
every gate, and the two route gates end it as they always did. The clock in utils.session is held;
the gates are the real code.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
import pytz
from flask import Flask, session

import blueprints.sudarshan_portfolio as portfolio
import database.user_db as user_db
import extensions
from utils import session as session_utils

IST = pytz.timezone("Asia/Kolkata")
OWNER = "owner"


def at(day: int, hour: int, minute: int) -> datetime:
    """An IST instant in November 2026: Wednesday the 11th to Saturday the 14th."""
    return IST.localize(datetime(2026, 11, day, hour, minute))


#: (now, login, admitted). Friday's login at 09:05 is the owner's ordinary day.
DAY = [
    (at(13, 9, 10), at(13, 9, 5), True),
    (at(13, 15, 30), at(13, 9, 5), True),
    (at(13, 23, 55), at(13, 9, 5), True),
    (at(14, 0, 30), at(13, 9, 5), True),
    (at(14, 2, 59), at(13, 9, 5), True),
    (at(14, 3, 1), at(13, 9, 5), False),
    (at(14, 10, 0), at(14, 2, 40), False),
    (at(14, 2, 50), at(14, 2, 40), True),
    # The hole v0.5.1 left between midnight and 03:00: both of these were admitted.
    (at(14, 1, 0), at(11, 10, 0), False),
    (at(14, 1, 0), at(13, 2, 0), False),
]
IDS = [
    "Friday 09:10, his 09:05 login",
    "Friday 15:30, the same login",
    "Friday 23:55, the same login",
    "Saturday 00:30, the same login past midnight",
    "Saturday 02:59, the same login",
    "Saturday 03:01, after the rollover",
    "Saturday 10:00, a login from 02:40 today",
    "Saturday 02:50, that 02:40 login before the rollover",
    "Saturday 01:00, a login three days old",
    "Saturday 01:00, a login from before Friday's 03:00",
]


@pytest.fixture
def clock(monkeypatch):
    """utils.session's clock, held at the instant given."""

    def hold(moment: datetime) -> None:
        class Held(datetime):
            @classmethod
            def now(cls, tz=None):
                return moment.astimezone(tz) if tz is not None else moment.replace(tzinfo=None)

        monkeypatch.setattr(session_utils, "datetime", Held)

    return hold


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DISABLE_SESSION_EXPIRY", "false")
    monkeypatch.delenv("SESSION_EXPIRY_TIME", raising=False)
    monkeypatch.setattr(
        user_db, "find_user_by_username", lambda *a, **k: SimpleNamespace(username=OWNER)
    )
    flask_app = Flask(__name__)
    flask_app.config.update(SECRET_KEY="test only", TESTING=True)
    return flask_app


def _signed_in(login: datetime) -> None:
    """The session OpenAlgo's login leaves once the broker step is done."""
    session.update(user=OWNER, logged_in=True, login_time=login.isoformat())


@pytest.mark.parametrize(("now", "login", "admitted"), DAY, ids=IDS)
def test_the_socket_io_owner_check(app, clock, now, login, admitted):
    clock(now)
    with app.test_request_context():
        _signed_in(login)
        assert extensions.is_owner_session() is admitted


@pytest.mark.parametrize(("now", "login", "admitted"), DAY, ids=IDS)
def test_the_alerts_auto_login_and_risk_routes(app, clock, monkeypatch, now, login, admitted):
    ended = []
    monkeypatch.setattr(session_utils, "revoke_user_tokens", lambda *a, **k: ended.append(1))
    view = session_utils.check_session_validity(lambda: ("answered", 200))
    clock(now)
    with app.test_request_context(headers={"Accept": "application/json"}):
        _signed_in(login)
        result = view()
        if admitted:
            assert result == ("answered", 200) and ended == [] and session.get("user") == OWNER
        else:
            response, status = result
            assert status == 401 and response.get_json()["error"] == "session_expired"
            assert ended == [1] and "user" not in session


@pytest.mark.parametrize(("now", "login", "admitted"), DAY, ids=IDS)
def test_the_portfolio_routes(app, clock, monkeypatch, now, login, admitted):
    ended = []
    monkeypatch.setattr(portfolio, "revoke_user_tokens", lambda *a, **k: ended.append(1))
    view = portfolio._session_required(lambda: ("answered", 200))
    clock(now)
    with app.test_request_context():
        _signed_in(login)
        result = view()
        if admitted:
            assert result == ("answered", 200) and ended == [] and session.get("user") == OWNER
        else:
            response, status = result
            assert status == 401 and response.get_json()["error"]["code"] == "unauthorized"
            assert ended == [1] and "user" not in session
