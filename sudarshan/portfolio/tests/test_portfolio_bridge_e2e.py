"""The real bridge in front of the real service: the bridge seals a request's arguments under "args",
the service hands exactly those to the op, and the owner's whole flow works end to end (a tab, an
account, an asset, entries, an import preview, commit and undo, export, performance).

Earlier the bridge tests answered from a fake registry and the op tests sealed fields flat, so each
half passed against its own idea of the message while no tab could be opened or saved.
"""

from __future__ import annotations

import asyncio
import io
import os
import threading

import httpx
import pytest
from portfolio_bridge_app import csrf_headers, login_session, make_app, reset_users
from portfolio_fakes import FakeResolver
from test_portfolio_service import settings_for

import blueprints.sudarshan_portfolio as bp_module
from sudarshan.portfolio import store as store_module
from sudarshan.portfolio.service import Service
from sudarshan.portfolio.store import DB_FILENAME, PortfolioStore
from sudarshan.portfolio.views import Prices
from sudarshan.vault import PORTFOLIO_API_INFO, fernet_for
from utils.thread_safe_cache import LockedTTLCache

PREFIX = "/sudarshan/portfolio/api"


class RealService:
    """sudarshan-portfolio's Service with its real ops and a real store, on a free port."""

    def __init__(self, tmp_path):
        fernet = fernet_for(os.environ["API_KEY_PEPPER"], PORTFOLIO_API_INFO)
        self.svc = Service(settings_for(tmp_path), fernet, jobs={})
        self.svc.store = PortfolioStore(tmp_path / "s" / DB_FILENAME)
        self.svc.resolver = FakeResolver()
        self.svc.prices = Prices()
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.svc.api.start(0))
        self.ready.set()
        self.loop.run_forever()
        self.loop.run_until_complete(self.svc.api.close())
        self.loop.close()

    def __enter__(self):
        self.thread.start()
        assert self.ready.wait(10)
        self.url = f"http://127.0.0.1:{self.svc.api.port}"
        return self

    def __exit__(self, *exc):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)
        self.svc.store.close()
        store_module.stop()


@pytest.fixture
def wired(tmp_path, monkeypatch):
    reset_users()
    http = httpx.Client(trust_env=False)
    monkeypatch.setattr(bp_module, "get_httpx_client", lambda: http)
    monkeypatch.setattr(bp_module, "_loopback_warned", LockedTTLCache(maxsize=8, ttl=600))
    with RealService(tmp_path) as service:
        monkeypatch.setenv("SUDARSHAN_PORTFOLIO_API_URL", service.url)
        client = make_app().test_client()
        login_session(client)
        yield client
    http.close()


def test_control_an_op_without_arguments_works(wired):
    answer = wired.get(PREFIX + "/state").get_json()
    assert answer["status"] == "success", answer
    assert any(tab["name"] == "Upstox" for tab in answer["data"]["tabs"])


def test_the_owner_can_make_a_tab_through_the_bridge(wired):
    answer = wired.post(
        PREFIX + "/tabs",
        json={"request_id": "probe-1", "name": "Gold", "kind": "gold"},
        headers=csrf_headers(wired),
    ).get_json()
    assert answer["status"] == "success", answer


def test_the_owner_can_open_a_tab_through_the_bridge(wired):
    tabs = wired.get(PREFIX + "/state").get_json()["data"]["tabs"]
    upstox = next(tab for tab in tabs if tab["name"] == "Upstox")
    answer = wired.get(f"{PREFIX}/tabs/{upstox['id']}").get_json()
    assert answer["status"] == "success", answer


TODAY_FILE = "date,action,symbol,quantity,price,account\n2026-10-01,BUY,RELIANCE,10,1000,Zerodha\n"


def ok(answer):
    body = answer.get_json()
    assert body["status"] == "success" and body["enabled"] is True, body
    return body["data"]


def test_an_owner_flow_end_to_end(wired):
    c = wired
    h = csrf_headers(c)
    state = ok(c.get(PREFIX + "/state"))
    me = state["members"][0]["id"]
    acc = ok(c.post(PREFIX + "/accounts", headers=h, json={
        "request_id": "a-1", "member_id": me, "name": "Zerodha", "kind": "demat"}))  # fmt: skip
    share = ok(c.post(PREFIX + "/assets", headers=h, json={
        "request_id": "s-1", "kind": "listed", "symbol": "RELIANCE", "exchange": "NSE"}))  # fmt: skip
    tab = ok(c.post(PREFIX + "/tabs", headers=h, json={
        "request_id": "t-1", "name": "Long term", "kind": "stocks"}))  # fmt: skip
    entry = {"kind": "BUY", "trade_date": 20261002, "account_id": acc["id"],
             "asset_id": share["id"], "qty_u": 5_000_000, "amount_paise": 500_000}  # fmt: skip
    dry = ok(c.post(f"{PREFIX}/tabs/{tab['id']}/activities", headers=h, json={
        "version": tab["version"], "request_id": "e-1", "dry_run": True, "entry": entry}))  # fmt: skip
    assert dry["activity"] is None and dry["effect"]["words"]
    saved = ok(c.post(f"{PREFIX}/tabs/{tab['id']}/activities", headers=h, json={
        "version": tab["version"], "request_id": "e-1", "entry": entry}))  # fmt: skip
    assert saved["activity"]["id"]
    view = ok(c.get(f"{PREFIX}/tabs/{tab['id']}"))
    assert view is not None
    preview = ok(c.post(
        f"{PREFIX}/tabs/{tab['id']}/imports", headers=h,
        data={"format": "generic", "file": (io.BytesIO(TODAY_FILE.encode()), "t.csv")},
        content_type="multipart/form-data",
    ))  # fmt: skip
    assert preview["rows_new"] == 1, preview
    done = ok(c.post(f"{PREFIX}/imports/{preview['batch_id']}/commit", headers=h,
                     json={"version": preview["tab_version"]}))  # fmt: skip
    undone = ok(c.delete(f"{PREFIX}/imports/{preview['batch_id']}", headers=h,
                         json={"version": done["tab_version"]}))  # fmt: skip
    assert undone["status"] == "undone"
    ok(c.get(f"{PREFIX}/tabs/{tab['id']}/export.csv"))
    ok(c.get(f"{PREFIX}/tabs/{tab['id']}/performance"))
    ok(c.get(f"{PREFIX}/tabs/{tab['id']}/activities", query_string={"limit": "10"}))
