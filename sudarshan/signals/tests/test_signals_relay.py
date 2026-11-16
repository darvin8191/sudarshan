"""AL2 (M13.10): the relay on the signals side, ``relay.py`` at the hand-on seam.

What is pending in signals.db goes to the alerts' one listener as the contract's RelayChange and
RelayAlert, sealed, at most 8 to a call and 16 KiB; what the desk has is marked handed, what has
waited 30 minutes expired, everything while the owner's relay is off skipped. The end-to-end
test runs the real service, the real alerts listener and the real signals desk with a fake
Telegram: no outbound connection. Every document is invented.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from signals_fakes import AT, KEYS, PEPPER, Box, document, free_port, reading, settings_for, until

from sudarshan.signals import contract as c
from sudarshan.signals import relay
from sudarshan.signals.service import Batch, Service
from sudarshan.vault import ALERTS_SIGNALS_INFO, fernet_for

RELAY_KEY = fernet_for(PEPPER, ALERTS_SIGNALS_INFO)
WHY = {"en": "Invented explanation.", "hi": "Banaya hua samjhauta.", "gu": None}


def change_row(n: int = 1, recorded_ms: int = AT, **over) -> dict:
    """A pending timeline row as ``store.pending_relay`` gives it (the contract's Change)."""
    row = {
        "id": n, "at_ms": recorded_ms, "recorded_ms": recorded_ms, "module": "regime",
        "instrument": "NIFTY", "kind": "state", "from_state": "ACC", "from_label":
        "FII accumulation", "from_bias": "bull", "to_state": "DIST", "to_label":
        "FII distribution", "bias": "bear", "score": -0.4, "confidence": 0.71, "quality": "ok",
        "horizon": "swing", "since_ms": None, "as_of_ms": recorded_ms, "valid_until_ms": None,
        "levels": None, "inputs": [], "why": dict(WHY),
    }  # fmt: skip
    return row | over


def alert_row(ident: str = "x-1", received_ms: int = AT, **over) -> dict:
    row = {"id": ident, "at_ms": received_ms, "module": "regime", "instrument": "NIFTY",
           "level": "act", "title": "Invented title", "text": "Invented text.", "lang": "en",
           "received_ms": received_ms}  # fmt: skip
    return row | over


class Store:
    """``pending_relay``, ``mark_relay`` and ``call`` as the signals store has them."""

    def __init__(self, changes=(), alerts=()):
        self.rows = {("change", r["id"]): dict(r, relay="pending") for r in changes}
        self.rows |= {("alert", r["id"]): dict(r, relay="pending") for r in alerts}

    async def call(self, fn, *args):
        return fn(*args)

    def pending_relay(self, limit: int = 50):
        pending = [r for r in self.rows.values() if r["relay"] == "pending"]
        changes = [r for r in pending if "kind" in r][:limit]
        alerts = [r for r in pending if "level" in r][:limit]
        return changes, alerts

    def mark_relay(self, table, ids, state):
        assert state in c.RELAY_STATES
        for ident in ids:
            self.rows[(table, ident)]["relay"] = state
        return len(ids)

    def states(self) -> dict:
        return {key: row["relay"] for key, row in self.rows.items()}


def fake_service(store, url: str, now: int = AT + 1000, **prefs) -> SimpleNamespace:
    return SimpleNamespace(
        store=store,
        prefs={"lang": "hi", "relay": True, "night": True, "version": 1} | prefs,
        clock=lambda: now,
        settings=SimpleNamespace(alerts_url=url, env_file=Path("/nonexistent/.env")),
        relay_key=RELAY_KEY,
    )


class Desk:
    """The alerts' one listener (the real MarketApi with the relay's key), answered by a
    recording stand-in for the signals desk: the ids it has never come back taken twice."""

    def __init__(self):
        self.calls: list[dict] = []
        self.seen: set[str] = set()
        self.refuse: str | None = None

    async def relay(self, message) -> dict:
        from sudarshan.alerts.market.api import ApiError

        if self.refuse:
            raise ApiError(503, "busy", self.refuse)
        args = message["args"]
        self.calls.append(args)
        ids = [item["id"] for item in (*args["changes"], *args["alerts"])]
        taken = [i for i in ids if i not in self.seen]
        self.seen |= set(ids)
        return {"taken": taken, "known": [i for i in ids if i not in taken]}


async def listening(desk_relay) -> tuple[object, str]:
    from sudarshan.alerts.market.api import MarketApi, OffDesk

    server = MarketApi(OffDesk(), fernet_for(PEPPER, b"another purpose"), relay_fernet=RELAY_KEY)
    server.relay = desk_relay
    await server.start(0)
    return server, f"http://127.0.0.1:{server.server.sockets[0].getsockname()[1]}"


# ---- what goes ----------------------------------------------------------------------------------


def test_pending_rows_become_the_contracts_relay_records_in_the_owners_language():
    long_why = dict(WHY, hi="ठीक " * 200)
    items = relay.items_for([change_row(why=long_why)], [alert_row()],
                            {"lang": "hi", "night": False})  # fmt: skip
    change, alert = items
    assert set(change) == {f.name for f in c.record("RelayChange")}
    assert set(alert) == {f.name for f in c.record("RelayAlert")}
    assert (change["id"], alert["id"]) == ("c-1", "a-x-1")
    assert change["why_lang"] == "hi" and len(change["why"]) == c.RELAY["text"]
    assert change["module_name"] == "DRISHTI" and change["night"] is False


def test_calls_carry_at_most_eight_items_and_16_kib_sealed():
    short = relay.items_for([change_row(n) for n in range(1, 20)], [], {"lang": "en"})
    calls = relay.pack(RELAY_KEY, short)
    assert [len(call) for call in calls] == [8, 8, 3]
    # Eight items at the contract's limits fit 16 KiB in a 3-byte script; only 4-byte letters
    # (here a script letter, U+10900) at every limit take a call past it.
    wide = "\U00010900"
    heavy = relay.items_for(
        [change_row(n, from_label=wide * 40, to_label=wide * 40,
                    why={"en": None, "hi": None, "gu": wide * 300}) for n in range(1, 20)],
        [alert_row(f"x-{n}", title=wide * 80, text=wide * 300) for n in range(1, 9)],
        {"lang": "gu"},
    )  # fmt: skip
    calls = relay.pack(RELAY_KEY, heavy)
    assert [item["id"] for call in calls for item in call] == [item["id"] for item in heavy]
    for call in calls:
        assert 1 <= len(call) <= 8 and len(relay.sealed(RELAY_KEY, call)) <= 16 * 1024
    assert any(len(call) < 8 for call in calls[:-1])  # the size, not the count, split these


class Bare:
    """A service object as the seam passes it (the real Service is a plain class too)."""


def test_the_hook_only_wakes_the_job_and_only_for_pending_items():
    service = Bare()

    async def scenario():
        await relay.on_batch(service, Batch(changes=[{"relay": "skipped"}]))
        assert not relay._event(service).is_set()
        await relay.on_batch(service, Batch(alerts=[{"relay": "pending"}]))
        assert relay._event(service).is_set()

    asyncio.run(scenario())


# ---- one round ----------------------------------------------------------------------------------


def test_what_the_desk_has_is_marked_handed_and_a_retry_sends_nothing_twice():
    async def scenario():
        desk = Desk()
        server, url = await listening(desk.relay)
        try:
            store = Store([change_row(1), change_row(2)], [alert_row()])
            service = fake_service(store, url)
            assert await relay.relay_pending(service) == 3
            assert set(store.states().values()) == {"handed"}
            ((args,),) = [desk.calls]
            assert [i["id"] for i in args["changes"]] == ["c-1", "c-2"]
            assert args["alerts"][0]["id"] == "a-x-1"
            store.rows[("change", 1)]["relay"] = "pending"  # as if the answer had been lost
            assert await relay.relay_pending(service) == 1  # the desk knows it: handed, once
            assert store.states()[("change", 1)] == "handed" and len(desk.seen) == 3
        finally:
            await server.close()

    asyncio.run(scenario())


def test_while_the_desk_does_not_take_them_they_stay_pending_then_expire_at_30_minutes():
    async def scenario():
        desk = Desk()
        desk.refuse = "The alert service is starting."
        server, url = await listening(desk.relay)
        try:
            store = Store([change_row(1)], [alert_row()])
            assert await relay.relay_pending(fake_service(store, url)) == 0
            assert set(store.states().values()) == {"pending"}
            later = AT + c.RELAY["hold_ms"] - 1000
            assert await relay.relay_pending(fake_service(store, url, now=later)) == 0
            assert set(store.states().values()) == {"pending"}  # 29 minutes: still tried
            past = AT + c.RELAY["hold_ms"] + 1000
            desk.refuse = None
            assert await relay.relay_pending(fake_service(store, url, now=past)) == 0
            assert set(store.states().values()) == {"expired"} and desk.calls == []
        finally:
            await server.close()

    asyncio.run(scenario())


def test_nothing_is_handed_while_the_owners_relay_is_off():
    async def scenario():
        desk = Desk()
        server, url = await listening(desk.relay)
        try:
            store = Store([change_row(1)], [alert_row()])
            assert await relay.relay_pending(fake_service(store, url, relay=False)) == 0
            assert set(store.states().values()) == {"skipped"} and desk.calls == []
        finally:
            await server.close()

    asyncio.run(scenario())


def test_nothing_listening_or_a_seal_the_desk_refuses_leaves_them_pending():
    async def scenario():
        store = Store([change_row(1)])
        nobody = f"http://127.0.0.1:{free_port()}"
        assert await relay.relay_pending(fake_service(store, nobody)) == 0
        desk = Desk()
        server, url = await listening(desk.relay)
        try:
            wrong = fake_service(store, url)
            wrong.relay_key = fernet_for("q" * 48, ALERTS_SIGNALS_INFO)  # another .env's pepper
            assert await relay.relay_pending(wrong) == 0
            assert store.states() == {("change", 1): "pending"} and desk.calls == []
        finally:
            await server.close()

    asyncio.run(scenario())


def test_the_key_is_derived_from_openalgos_pepper_like_the_services_others(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(f"API_KEY_PEPPER = '{'e' * 64}'\n")
    service = SimpleNamespace(settings=SimpleNamespace(env_file=env))
    item = relay.items_for([change_row()], [], {})
    monkeypatch.setattr(relay, "_keys", {})
    monkeypatch.delenv("API_KEY_PEPPER")
    token = relay.sealed(relay.key_for(service), item)
    assert fernet_for("e" * 64, ALERTS_SIGNALS_INFO).decrypt(token)  # OpenAlgo's .env
    monkeypatch.setattr(relay, "_keys", {})
    monkeypatch.setenv("API_KEY_PEPPER", PEPPER)
    token = relay.sealed(relay.key_for(service), item)
    assert RELAY_KEY.decrypt(token)  # the environment wins, as in the service's main
    monkeypatch.setattr(relay, "_keys", {})
    monkeypatch.delenv("API_KEY_PEPPER")
    assert relay.key_for(SimpleNamespace(settings=SimpleNamespace(env_file=tmp_path / "x"))) is None


# ---- end to end: letterbox, service, relay, the alerts' listener and desk ---------------------


def test_a_published_change_reaches_telegram_through_the_alerts_desk_once(tmp_path):
    from sudarshan.alerts import outbox
    from sudarshan.alerts import signals_desk as desk_module
    from sudarshan.alerts.outbox import SENT, Pacer
    from sudarshan.alerts.signals_desk import SignalsDesk
    from sudarshan.alerts.store import Store as AlertsStore

    texts: list[str] = []

    async def telegram(text: str) -> str:
        texts.append(text)
        return SENT

    async def scenario():
        old = (desk_module.MERGE_S, outbox.MIN_GAP_S)
        desk_module.MERGE_S, outbox.MIN_GAP_S = 0.05, 0.0
        alerts_store = AlertsStore(tmp_path / "alerts" / "alerts.db")
        from datetime import UTC, datetime

        desk = SignalsDesk(
            alerts_store,
            telegram,
            lambda: True,
            lambda: datetime.fromtimestamp((AT + 2000) / 1000, UTC),
            Pacer(),
        )
        server, url = await listening(desk.relay)
        desk_task = asyncio.create_task(desk.run())
        port = free_port()
        box = Box(tmp_path / "in")
        svc = Service(settings_for(tmp_path, port, alerts_url=url,
                                   env_file=tmp_path / "missing.env"), KEYS,
                      hooks={"relay": relay.on_batch}, jobs={"relay": relay.relayer},
                      letterbox=box.reader, scan_s=0.02, setup_every_s=0.05)  # fmt: skip
        svc.clock = lambda: AT + 1000
        alert = {"id": "a-1", "module": "regime", "instrument": "NIFTY", "level": "act",
                 "title": "Invented title", "text": "Invented text.", "lang": "en"}  # fmt: skip
        box.drop(document(signals=[reading()], alerts=[alert]), written_ms=AT)
        task = asyncio.create_task(svc.run())
        try:
            await until(lambda: len(texts) == 2, 10)
            await until(lambda: svc.store is not None and not svc.store.pending_relay()[0]
                        and not svc.store.pending_relay()[1], 10)  # fmt: skip
            rows = json.loads(json.dumps(svc.store.pending_relay()))
            assert rows == [[], []]
        finally:
            svc.stop()
            await asyncio.wait_for(task, 10)
            await desk.close(1.0)
            desk_task.cancel()
            await asyncio.gather(desk_task, return_exceptions=True)
            await server.close()
            alerts_store.close()
            desk_module.MERGE_S, outbox.MIN_GAP_S = old
        lines = sorted(text.split("\n")[1] for text in texts)
        assert lines == [
            "TRINETRA · NIFTY regime · Act: Invented title",
            "TRINETRA · NIFTY regime: FII distribution (bearish), confidence 0.71.",
        ]
        quoted = [text.split("\n")[2] for text in texts]
        assert '"Banaya hua."' in quoted  # the owner's language, Hinglish by default
        assert desk.stats["taken"] == 2 and desk.stats["sent"] == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["relay"])
def test_relay_is_attached_at_the_seam_with_one_marked_line(name):
    from sudarshan.signals import service

    assert service.HAND_ON[name] is relay.on_batch and service.JOBS[name] is relay.relayer
    tail = Path(service.__file__).read_text().rstrip().split("\n")[-1]
    assert tail.startswith("from sudarshan.signals import relay as _relay") and "AL2" in tail
