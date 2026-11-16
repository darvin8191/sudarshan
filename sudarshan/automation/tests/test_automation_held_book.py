"""The automation card's P&L line (M12.1, GA10.4): the held contract's row in the book of the
strategy's own mode rides on the page's reads, and the page moves the P&L on with the ticks.

Only reads: the sandbox's own row as last priced (no quote fetched, nothing written), or Upstox's
positions through OpenAlgo's position book. Nothing here reaches Upstox.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from automation_world import (
    NOW,
    TV,
    FakeDesk,
    csrf_headers,
    fresh_owner,
    indicator_alert,
    login_session,
    make_app,
)

from sudarshan.automation import contract, control, flag, intake
from sudarshan.automation import model as m
from sudarshan.automation import store as store_mod
from sudarshan.automation.desk import Desk, positions_book_row, sandbox_book_row

API = contract.API_PREFIX
FORM = {"name": "ORB", "kind": "future", "underlying": "NIFTY", "product": "MIS", "lots": 1}
NOW_MS = int(NOW * 1000)


@pytest.fixture
def desk():
    return FakeDesk()


@pytest.fixture
def client(store, desk, monkeypatch):
    fresh_owner()
    monkeypatch.setenv(flag.ENV, "sandbox")
    monkeypatch.setattr(intake, "DESK", lambda: desk)
    monkeypatch.setattr(control, "Desk", lambda: desk)
    monkeypatch.setattr(store_mod, "_shared", store_mod.LazyInit(lambda: store, "test"))
    intake._per_link.clear()
    return make_app().test_client()


def owner(client, method, path, body=None):
    login_session(client)
    return getattr(client, method)(
        path, json=body, headers={"Accept": "application/json", **csrf_headers(client)}
    )


def test_the_list_carries_the_held_contracts_row_from_the_strategys_own_book(client, store, desk):
    made = owner(client, "post", f"{API}/create", FORM).get_json()
    owner(client, "post", f"{API}/create", {**FORM, "name": "Swing", "underlying": "BANKNIFTY"})
    owner(client, "post", f"{API}/state", {"id": 1, "version": 1, "state": "active"})
    intake.handle(made["token"], indicator_alert("long", NOW - 1), TV, desk, store)

    listed = owner(client, "get", f"{API}/list").get_json()["strategies"]
    held, idle = (next(s for s in listed if s["id"] == n) for n in (1, 2))
    assert held["held_symbol"] == "NIFTY24NOV26FUT"
    assert held["held_book"] == {
        "quantity": 65,
        "average_price": 25000.0,
        "ltp": 25010.0,
        "pnl": 650.0,
        "today_realized_pnl": 0.0,
        "priced_ms": NOW_MS,
    }
    assert idle["held_book"] is None
    # Read in the strategy's own pipe, and only for the strategy that holds something.
    assert desk.held_book_reads == [("sandbox", "NFO", "NIFTY24NOV26FUT", "MIS")]

    one = owner(client, "get", f"{API}/one?id=1").get_json()["strategy"]
    assert one["held_book"]["quantity"] == 65


def test_a_book_that_cannot_be_read_leaves_the_row_out_and_the_rest_of_the_card(
    client, store, desk
):
    made = owner(client, "post", f"{API}/create", FORM).get_json()
    owner(client, "post", f"{API}/state", {"id": 1, "version": 1, "state": "active"})
    intake.handle(made["token"], indicator_alert("long", NOW - 1), TV, desk, store)
    desk.books_unreadable = True
    answer = owner(client, "get", f"{API}/list")
    assert answer.status_code == 200
    (seen,) = answer.get_json()["strategies"]
    assert seen["held_symbol"] == "NIFTY24NOV26FUT" and seen["held_book"] is None


def test_every_view_names_the_field_and_asks_no_book_without_a_reader(store, desk):
    strategy, _token = store.create(contract.clean(FORM))
    strategy = store.set_state(strategy.id, strategy.version, m.ACTIVE)
    seen = control.view(strategy, store, NOW_MS, desk.trading_day, desk.held_book)
    assert set(seen) == set(contract.STRATEGY) and seen["held_book"] is None
    assert desk.held_book_reads == []  # nothing held, nothing read


# ---- the sandbox's own row ----------------------------------------------------------------------


def _row(**over):
    base = {
        "quantity": 65,
        "average_price": 25000,
        "ltp": 25040,
        "pnl": 0,  # the column as last priced; the card never trusts it beside a later fill
        "today_realized_pnl": 150,
        "updated_at": datetime(2026, 11, 16, 4, 45, 2),  # naive: the sandbox's SQLite clock, UTC
    }
    return SimpleNamespace(**{**base, **over})


def test_a_sandbox_row_is_priced_at_the_price_the_sandbox_last_wrote_on_it():
    got = sandbox_book_row(_row(), NOW_MS)
    assert got == {
        "quantity": 65,
        "average_price": 25000.0,
        "ltp": 25040.0,
        "pnl": 2750.0,  # 150 booked + 40 a unit on 65
        "today_realized_pnl": 150.0,
        "priced_ms": int(datetime(2026, 11, 16, 4, 45, 2, tzinfo=UTC).timestamp() * 1000),
    }
    short = sandbox_book_row(_row(quantity=-65), NOW_MS)
    assert short["pnl"] == 150.0 - 2600.0  # a short loses as the price rises


def test_a_sandbox_row_closed_today_says_the_days_booked_part_and_no_price():
    got = sandbox_book_row(_row(quantity=0, today_realized_pnl=-420.5), NOW_MS)
    assert (got["quantity"], got["pnl"], got["today_realized_pnl"]) == (0, -420.5, -420.5)


def test_a_sandbox_row_with_no_price_leaves_the_pnl_to_the_first_tick():
    got = sandbox_book_row(_row(ltp=None), NOW_MS)
    assert (got["ltp"], got["pnl"], got["today_realized_pnl"]) == (None, None, 150.0)


def test_no_sandbox_row_is_flat_and_a_write_time_ahead_of_now_reads_as_now():
    assert sandbox_book_row(None, NOW_MS) == {
        "quantity": 0,
        "average_price": 0.0,
        "ltp": None,
        "pnl": None,
        "today_realized_pnl": 0.0,
        "priced_ms": NOW_MS,
    }
    ahead = datetime.fromtimestamp(NOW + 600, tz=UTC).replace(tzinfo=None)
    assert sandbox_book_row(_row(updated_at=ahead), NOW_MS)["priced_ms"] == NOW_MS


# ---- Upstox's positions, as OpenAlgo's position book maps them ----------------------------------

ROWS = [
    "not a row",
    {"symbol": "NIFTY24NOV26FUT", "exchange": "NFO", "product": "NRML", "quantity": 130,
     "average_price": 24900.0, "ltp": 25040.0, "pnl": 18200.0},
    {"symbol": "NIFTY24NOV26FUT", "exchange": "NFO", "product": "MIS", "quantity": -65,
     "average_price": 25100.0, "ltp": 25040.0, "pnl": 3900.0},
]  # fmt: skip


def test_upstoxs_row_is_the_one_in_the_strategys_product_with_its_own_pnl():
    got = positions_book_row(ROWS, "NFO", "NIFTY24NOV26FUT", "MIS", NOW_MS)
    assert got == {
        "quantity": -65,
        "average_price": 25100.0,
        "ltp": 25040.0,
        "pnl": 3900.0,
        "today_realized_pnl": 0.0,
        "priced_ms": NOW_MS,
    }
    assert positions_book_row(ROWS, "NFO", "NIFTY24NOV26FUT", "CNC", NOW_MS)["quantity"] == 0
    assert positions_book_row([], "NFO", "NIFTY24NOV26FUT", "MIS", NOW_MS)["pnl"] is None


# ---- the desk itself ----------------------------------------------------------------------------


@pytest.fixture
def sandbox_rows():
    from database import sandbox_db

    sandbox_db.Base.metadata.create_all(sandbox_db.engine)
    session = sandbox_db.db_session
    session.query(sandbox_db.SandboxPositions).delete()
    for product, quantity in (("MIS", 65), ("NRML", -30)):
        session.add(
            sandbox_db.SandboxPositions(
                user_id="owner", symbol="NIFTY24NOV26FUT", exchange="NFO", product=product,
                quantity=quantity, average_price=25000, ltp=25040, pnl=0, today_realized_pnl=150,
                updated_at=datetime(2026, 11, 16, 4, 45, 2),
            )
        )  # fmt: skip
    session.commit()
    yield
    session.query(sandbox_db.SandboxPositions).delete()
    session.commit()
    session.remove()


def test_the_desk_reads_the_sandboxs_row_in_the_strategys_product(sandbox_rows, monkeypatch):
    import services.sandbox_service as sandbox_service

    monkeypatch.setattr(Desk, "api_key", lambda self: "owner-key")
    monkeypatch.setattr(sandbox_service, "get_user_id_from_apikey", lambda key: "owner")
    monkeypatch.setattr(Desk, "now", lambda self: NOW)
    got = Desk().held_book("sandbox", "NFO", "NIFTY24NOV26FUT", "MIS")
    assert (got["quantity"], got["pnl"]) == (65, 2750.0)
    assert Desk().held_book("sandbox", "NFO", "NIFTY24NOV26FUT", "NRML")["quantity"] == -30
    assert Desk().held_book("sandbox", "NFO", "NIFTY29DEC26FUT", "MIS")["quantity"] == 0


def test_the_desk_reads_upstoxs_positions_with_the_owners_session(monkeypatch):
    import services.positionbook_service as positionbook

    asked = []
    monkeypatch.setattr(Desk, "api_key", lambda self: "owner-key")
    monkeypatch.setattr(Desk, "broker_session", lambda self, key: ("session", "upstox"))

    def book(**kwargs):
        asked.append(kwargs)
        return True, {"status": "success", "data": ROWS}, 200

    monkeypatch.setattr(positionbook, "get_positionbook", book)
    got = Desk().held_book("live", "NFO", "NIFTY24NOV26FUT", "MIS")
    assert got["quantity"] == -65 and asked == [{"auth_token": "session", "broker": "upstox"}]

    monkeypatch.setattr(positionbook, "get_positionbook", lambda **k: (False, {}, 500))
    assert Desk().held_book("live", "NFO", "NIFTY24NOV26FUT", "MIS") is None
    monkeypatch.setattr(Desk, "broker_session", lambda self, key: (None, None))
    assert Desk().held_book("live", "NFO", "NIFTY24NOV26FUT", "MIS") is None
    monkeypatch.setattr(Desk, "api_key", lambda self: None)
    assert Desk().held_book("sandbox", "NFO", "NIFTY24NOV26FUT", "MIS") is None


def test_a_desk_read_that_fails_is_none_and_logged_never_raised(monkeypatch):
    import services.positionbook_service as positionbook

    monkeypatch.setattr(Desk, "api_key", lambda self: "owner-key")
    monkeypatch.setattr(Desk, "broker_session", lambda self, key: ("session", "upstox"))

    def broken(**kwargs):
        raise RuntimeError("the broker's answer could not be parsed")

    monkeypatch.setattr(positionbook, "get_positionbook", broken)
    assert Desk().held_book("live", "NFO", "NIFTY24NOV26FUT", "MIS") is None
