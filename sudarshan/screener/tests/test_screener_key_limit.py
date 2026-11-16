"""A screen that would pass the key limit is refused when it is saved (gap audit GA7.3).

Before, such a screen was saved and started, and the instruments past the limit read "Not
checked: past the 320 instruments" at every run, with nothing said at the save. Now the save
itself is refused, in a sentence that names the limit and how far over the screen would go. A
screen saved Paused is not judged until it is resumed; a screen that adds no instrument is never
refused; and when the count cannot be worked out, nothing is refused on a guess.
"""

from __future__ import annotations

import asyncio

import pytest
from screener_helpers import ms
from test_screener_feed_runtime import FakeFeed
from test_screener_runs import SPEC
from test_screener_runtime import Calendar
from test_screener_universe import master as master_fixture  # noqa: F401  (the fixture)

from sudarshan.screener import spec, universe
from sudarshan.screener import store as store_module
from sudarshan.screener.ops import REGISTRY, ApiError
from sudarshan.screener.runtime import LiveSource
from sudarshan.screener.service import Service
from sudarshan.screener.store import OWNER, ScreenerStore
from sudarshan.screener.tests.test_screener_service import FERNET, settings_for

DAY = "2026-10-28"
NOW = ms(DAY, "10:00")
STOCKS = {"kind": "fno_stocks"}  # RELIANCE and SBIN in the fixture: two instruments
WINDOW = {"kind": "options", "underlying": "NIFTY"}  # the index and 21 strikes, a call and a put
LIST = {"kind": "watchlist", "list_id": 3}


class Lists:
    def __init__(self, *symbols):
        self.symbols = symbols

    async def __call__(self, specs):
        if self.symbols == (None,):
            return {}
        items = [{"id": i, "symbol": s, "exchange": "NSE"} for i, s in enumerate(self.symbols)]
        return {int(s["list_id"]): items for s in specs if s["kind"] == "watchlist"}


def service_with(tmp_path, master, limit: int, lists=None, source=True):
    svc = Service(
        settings_for(
            1,
            SUDARSHAN_SCREENER_STATE_DIR=str(tmp_path),
            SUDARSHAN_SCREENER_MAX_KEYS=str(limit),
        ),
        FERNET,
        jobs={},
    )
    svc.store = ScreenerStore(tmp_path / "screener.db")
    svc.clock = lambda: NOW
    if source:
        made = LiveSource(svc.store, FakeFeed(), master, svc.settings, svc.clock, Calendar())
        made.new_day(NOW)
        made.lists_reader = lists
        svc.parts["source"] = made
    return svc


@pytest.fixture
def make(tmp_path, master_fixture):  # noqa: F811
    made = []

    def build(limit: int, **extra):
        home = tmp_path / f"service-{len(made)}"
        home.mkdir()
        made.append(service_with(home, master_fixture, limit, **extra))
        return made[-1]

    yield build
    for svc in made:
        svc.store.close()
    store_module.stop()


def call(svc, op, **args):
    return asyncio.run(REGISTRY[op](svc, args))


def refused(svc, op, **args) -> ApiError:
    with pytest.raises(ApiError) as caught:
        call(svc, op, **args)
    assert caught.value.code == "refused", caught.value.message
    return caught.value


def on(universe_spec, name="S", **extra) -> dict:
    return dict(SPEC, name=name, universe=universe_spec, target=None, **extra)


def screens(svc) -> list[str]:
    return [r["name"] for r in svc.store.read(OWNER, lambda tx: tx.screens())]


def test_a_screen_past_the_key_limit_is_refused_with_the_limit_and_how_far_over(make):
    svc = make(40)
    error = refused(svc, "create", spec=on(WINDOW))
    assert error.message == (
        "With this screen the screener would follow 43 instruments tick by tick, 3 over its "
        "limit of 40. Pause another screen, or choose a smaller list for this one."
    )
    assert error.field == "universe"
    assert screens(svc) == [], "a refused screen was saved"


def test_a_screen_inside_the_limit_is_saved_and_the_next_one_counts_with_it(make):
    svc = make(3, lists=Lists("SBIN", "INFY", "HDFCBANK"))
    assert call(svc, "create", spec=on(STOCKS, "Stocks"))["status"] == "active"
    # SBIN is followed already: the list adds two instruments to the two, one over the three
    error = refused(svc, "create", spec=on(LIST, "Mine"))
    assert "would follow 4 instruments tick by tick, 1 over its limit of 3" in error.message
    svc.parts["source"].lists_reader = Lists("SBIN", "INFY")
    assert call(svc, "create", spec=on(LIST, "Mine"))["status"] == "active"
    assert screens(svc) == ["Stocks", "Mine"]


def test_a_paused_screen_is_judged_when_it_is_resumed(make):
    svc = make(40)
    made = call(svc, "create", spec=on(WINDOW), status="paused")
    assert made["status"] == "paused"
    error = refused(svc, "update", id=made["id"], version=made["version"], action="resume")
    assert "3 over its limit of 40" in error.message and error.field == "universe"
    held = svc.store.read(OWNER, lambda tx: tx.screen(made["id"]))
    assert held["status"] == "paused" and held["version"] == made["version"]


def test_an_edit_to_a_wider_universe_is_refused_and_any_other_edit_is_not(make):
    svc = make(40)
    made = call(svc, "create", spec=on(STOCKS))
    error = refused(
        svc, "update", id=made["id"], version=made["version"], action="edit", spec=on(WINDOW)
    )
    assert "3 over its limit of 40" in error.message
    renamed = call(
        svc,
        "update",
        id=made["id"],
        version=made["version"],
        action="edit",
        spec=on(STOCKS, "Renamed"),
    )
    assert renamed["name"] == "Renamed"


def test_a_screen_that_adds_no_instrument_is_never_refused(make):
    svc = make(1)
    stored = spec.check(on(STOCKS, "Held"))  # saved before the limit was lowered: two followed
    held = svc.store.write(OWNER, lambda tx: tx.screen_insert("Held", stored, "active", NOW))
    assert call(svc, "create", spec=on(STOCKS, "Twin"))["status"] == "active"
    # and the screen already there can still be edited, paused and resumed on its own universe
    edited = call(
        svc,
        "update",
        id=held["id"],
        version=held["version"],
        action="edit",
        spec=on(STOCKS, "Held on"),
    )
    assert edited["name"] == "Held on"


def test_a_save_sent_again_answers_the_screen_it_made(make):
    svc = make(2)
    made = call(svc, "create", spec=on(STOCKS), request_id="r1")
    svc.settings = svc.settings.__class__(**{**svc.settings.__dict__, "max_keys": 1})
    svc.parts["source"].settings = svc.settings
    again = call(svc, "create", spec=on(STOCKS), request_id="r1")
    assert again["id"] == made["id"]


def test_nothing_is_refused_on_a_guess(make, monkeypatch):
    # no price source in this process: the count is not known
    bare = make(1, source=False)
    assert call(bare, "create", spec=on(WINDOW, "No source"))["status"] == "active"
    # a list the bridge cannot read
    svc = make(1, lists=Lists(None))
    assert call(svc, "create", spec=on(LIST, "Unread"))["status"] == "active"

    # the master being downloaded
    def down(*args, **kwargs):
        raise universe.MasterUnavailable

    monkeypatch.setattr(svc.parts["source"].master, "cash", down)
    assert call(svc, "create", spec=on(STOCKS, "Downloading"))["status"] == "active"


def test_a_preset_past_the_limit_is_added_paused_and_says_why(make):
    svc = make(1)
    got = call(svc, "widget_add", version=0, preset="vwap_reclaim")
    assert got["screen"]["status"] == "paused"
    assert got["notice"] == (
        "Added Paused: with it the screener would follow 2 instruments tick by tick, 1 over its "
        "limit of 1. Pause another screen to start this."
    )
    roomy = make(320)
    got = call(roomy, "widget_add", version=0, preset="vwap_reclaim")
    assert got["screen"]["status"] == "active" and got["notice"] is None
