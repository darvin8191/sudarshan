"""AL2 (M13.10): TRINETRA's changes on the owner's Telegram, the alerts' signals desk.

Against the real outbox Pacer, a real alerts.db in a temporary folder and a fake send: no
network. Every reading and sentence here is invented (``signals_fakes.py``); the words and the
relay's contract checks are in ``test_alerts_signals_words.py``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import httpx
import pytest
from alerts_fakes import ist, wait_until
from signals_fakes import AT, WORDS_EXAMPLE, alert, change, relay_message

from sudarshan.alerts import outbox, signals_desk
from sudarshan.alerts.market.api import RELAY_MAX, ApiError, MarketApi, OffDesk
from sudarshan.alerts.outbox import DROP, ORDERS, RETRY, SENT, SIGNALS, Pacer
from sudarshan.alerts.service import AlertService
from sudarshan.alerts.signals_desk import SignalsDesk
from sudarshan.alerts.store import Store
from sudarshan.autologin.handoff import seal
from sudarshan.tasks import cancel_and_wait
from sudarshan.vault import (
    ALERTS_API_INFO,
    ALERTS_SCREENER_DIGEST_INFO,
    ALERTS_SIGNALS_INFO,
    fernet_for,
)

PEPPER = "p" * 48  # fake, test only
RELAY_KEY = fernet_for(PEPPER, ALERTS_SIGNALS_INFO)
NIGHT = (22 * 60 + 30, 7 * 60)


class Sender:
    """OpenAlgoTelegram.send's stand-in: records each text, answers from a script, then SENT."""

    def __init__(self, *outcomes: str):
        self.outcomes, self.texts = list(outcomes), []

    async def __call__(self, text: str) -> str:
        self.texts.append(text)
        return self.outcomes.pop(0) if self.outcomes else SENT


class Now:
    def __init__(self, at=AT):
        self.at = at

    def __call__(self):
        return self.at


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(signals_desk, "MERGE_S", 0.05)
    monkeypatch.setattr(signals_desk, "POLL_S", 0.01)
    monkeypatch.setattr(signals_desk, "RETRY_S", (0.02, 0.02))
    monkeypatch.setattr(outbox, "MIN_GAP_S", 0.0)


@pytest.fixture
def store(tmp_path):
    held = Store(tmp_path / "state" / "alerts.db")
    yield held
    held.close()


def desk_for(store, sender, clock=None, pacer=None) -> SignalsDesk:
    return SignalsDesk(store, sender, lambda: True, clock or Now(), pacer or Pacer())


async def running(desk):
    return asyncio.create_task(desk.run())


def test_items_are_taken_once_and_sent_in_the_signals_lane(quick, store):
    async def scenario():
        sender, pacer = Sender(), Pacer()
        desk = desk_for(store, sender, pacer=pacer)
        task = await running(desk)
        try:
            answer = await desk.relay(relay_message([change(1)], [alert()]))
            assert answer == {"taken": ["c-1", "a-20261116-0007"], "known": []}
            await wait_until(lambda: len(sender.texts) == 2, 3)
            again = await desk.relay(relay_message([change(1)], [alert()]))
            assert again == {"taken": [], "known": ["c-1", "a-20261116-0007"]}
            await asyncio.sleep(0.2)
            assert len(sender.texts) == 2  # a relay sent again sends nothing
            assert [lane for _, lane in pacer._starts] == [SIGNALS, SIGNALS]
            assert desk.health() == {
                "waiting": 0,
                "taken": 2,
                "known": 2,
                "sent": 2,
                "dropped": 0,
                "skipped": 0,
                "stale": 0,
                "unsure": 0,
            }
        finally:
            await cancel_and_wait(task)

    asyncio.run(scenario())


def test_merged_over_five_seconds_one_message_per_reading(quick, monkeypatch):
    monkeypatch.setattr(signals_desk, "MERGE_S", 0.3)

    async def scenario(store):
        sender = Sender()
        desk = desk_for(store, sender)
        task = await running(desk)
        try:
            await desk.relay(relay_message([change(1)]))
            await desk.relay(relay_message(
                [change(2, from_label="FII distribution", to_label="FII panic"),
                 change(3, instrument="BANKNIFTY")],
            ))  # fmt: skip
            await asyncio.sleep(0.15)
            assert sender.texts == []  # still inside the merge of the first
            await wait_until(lambda: len(sender.texts) == 2, 3)
        finally:
            await cancel_and_wait(task)
        lines = [text.split("\n")[1] for text in sender.texts]
        assert lines == [
            "TRINETRA · NIFTY regime: FII accumulation → FII panic (bearish), confidence 0.71.",
            "TRINETRA · BANKNIFTY regime: FII accumulation → FII distribution (bearish), "
            "confidence 0.71.",
        ]

    with_store(scenario)


def with_store(scenario) -> None:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as folder:
        store = Store(Path(folder) / "alerts.db")
        try:
            asyncio.run(scenario(store))
        finally:
            store.close()


def test_the_claim_holds_across_a_restart_and_what_waited_goes_once(quick, tmp_path):
    """A restart of the alerts service between the claim and the send: the item still goes, once;
    the signals service's retry after the restart is answered known (P8 10.6, "once only")."""
    path = tmp_path / "alerts.db"

    async def first_run():
        store = Store(path)
        desk = desk_for(store, Sender(), clock=Now())
        # taken, but the service stops before its message could go (no run task at all)
        assert (await desk.relay(relay_message([change(1)])))["taken"] == ["c-1"]
        await desk.close(0.1)
        store.close()

    async def second_run():
        store = Store(path)
        sender = Sender()
        desk = desk_for(store, sender)
        task = await running(desk)
        try:
            await wait_until(lambda: len(sender.texts) == 1, 3)
            assert (await desk.relay(relay_message([change(1)]))) == {"taken": [], "known": ["c-1"]}
            await asyncio.sleep(0.2)
            assert len(sender.texts) == 1
        finally:
            await cancel_and_wait(task)
            store.close()
        return sender.texts

    asyncio.run(first_run())
    (text,) = asyncio.run(second_run())
    assert WORDS_EXAMPLE in text

    async def third_run():
        store = Store(path)
        sender = Sender()
        desk = desk_for(store, sender)
        task = await running(desk)
        try:
            await asyncio.sleep(0.2)
            assert await desk.relay(relay_message([change(1)])) == {"taken": [], "known": ["c-1"]}
        finally:
            await cancel_and_wait(task)
            store.close()
        assert sender.texts == []  # sent once, in the second run, never again

    asyncio.run(third_run())


def test_a_message_on_its_way_at_a_restart_is_never_sent_again(quick, tmp_path):
    path = tmp_path / "alerts.db"
    store = Store(path)
    body = json.dumps(["change", change(5)])
    assert store.claim_signals([("c-5", AT.timestamp(), body)], AT.timestamp()) == ["c-5"]
    store.set_signals(["c-5"], "sending", AT.timestamp())  # the process stopped mid-send
    store.close()

    async def restart():
        again = Store(path)
        sender = Sender()
        desk = desk_for(again, sender)
        task = await running(desk)
        try:
            await asyncio.sleep(0.2)
            assert (await desk.relay(relay_message([change(5)])))["known"] == ["c-5"]
        finally:
            await cancel_and_wait(task)
            again.close()
        assert sender.texts == []

    asyncio.run(restart())


def test_dropped_when_the_bot_is_stopped_or_the_chat_is_not_linked(quick, store):
    """OpenAlgo's 409 and 404 are DROP for every lane (outbox.DROP_REASONS): never held back."""

    async def scenario():
        sender = Sender(DROP)
        desk = desk_for(store, sender)
        task = await running(desk)
        try:
            await desk.relay(relay_message([change(1)]))
            await wait_until(lambda: desk.stats["dropped"] == 1, 3)
            await asyncio.sleep(0.2)
            assert len(sender.texts) == 1  # tried once, not again
            await desk.relay(relay_message([change(2, instrument="BANKNIFTY")]))
            await wait_until(lambda: len(sender.texts) == 2, 3)  # the next still goes
        finally:
            await cancel_and_wait(task)

    asyncio.run(scenario())
    assert store.queued_signals(AT.timestamp()) == []


def test_never_sent_later_than_30_minutes_after_it_arrived(quick, store):
    async def scenario():
        clock = Now()
        sender = Sender(*[RETRY] * 1000)  # OpenAlgo busy all along
        desk = desk_for(store, sender, clock=clock)
        task = await running(desk)
        try:
            await desk.relay(relay_message([change(1)]))
            await wait_until(lambda: len(sender.texts) >= 2, 3)
            clock.at = AT + timedelta(minutes=29)
            await asyncio.sleep(0.1)
            assert desk.stats["stale"] == 0  # still inside the 30 minutes: still tried
            clock.at = AT + timedelta(minutes=30, seconds=1)
            await wait_until(lambda: desk.stats["stale"] == 1, 3)
            tried = len(sender.texts)
            await asyncio.sleep(0.1)
            assert len(sender.texts) == tried  # not sent late
        finally:
            await cancel_and_wait(task)

    asyncio.run(scenario())


def test_info_alerts_and_lapsed_changes_are_claimed_and_never_sent(quick, store):
    async def scenario():
        sender = Sender()
        desk = desk_for(store, sender)
        task = await running(desk)
        try:
            answer = await desk.relay(relay_message(
                [change(1, kind="lapsed")], [alert(level="info")],
            ))  # fmt: skip
            assert answer["taken"] == ["c-1", "a-20261116-0007"]
            await asyncio.sleep(0.2)
        finally:
            await cancel_and_wait(task)
        assert sender.texts == [] and desk.stats["skipped"] == 2

    asyncio.run(scenario())


def test_a_signal_gives_way_to_waiting_order_alerts(quick, store):
    async def scenario():
        sender, pacer = Sender(), Pacer()
        orders = {"waiting": True}
        pacer.orders_waiting = lambda: orders["waiting"]
        desk = desk_for(store, sender, pacer=pacer)
        task = await running(desk)
        try:
            await desk.relay(relay_message([change(1)]))
            await asyncio.sleep(0.3)
            assert sender.texts == [] and pacer.waiting(SIGNALS)  # fills first
            orders["waiting"] = False
            await wait_until(lambda: len(sender.texts) == 1, 3)
            assert not pacer.waiting(SIGNALS)
        finally:
            await cancel_and_wait(task)
        assert ORDERS not in [lane for _, lane in pacer._starts]

    asyncio.run(scenario())


def test_night_hours_are_off_until_they_are_built(quick, store):
    assert signals_desk.NIGHT_HOURS is None

    async def scenario():
        sender = Sender()
        desk = desk_for(store, sender, clock=Now(ist(2026, 11, 16, 23, 30)))
        task = await running(desk)
        try:
            await desk.relay(relay_message([change(1, night=True)]))
            await wait_until(lambda: len(sender.texts) == 1, 3)  # at night too
        finally:
            await cancel_and_wait(task)

    asyncio.run(scenario())


def test_with_night_hours_set_all_but_act_alerts_wait_for_the_morning(quick, store, monkeypatch):
    monkeypatch.setattr(signals_desk, "NIGHT_HOURS", NIGHT)

    async def scenario():
        clock = Now(ist(2026, 11, 16, 23, 30))
        sender = Sender()
        desk = desk_for(store, sender, clock=clock)
        task = await running(desk)
        try:
            await desk.relay(relay_message(
                [change(1, night=True), change(2, instrument="BANKNIFTY", night=False)],
                [alert("a-act", night=True), alert("a-watch", level="watch", night=True)],
            ))  # fmt: skip
            await wait_until(lambda: len(sender.texts) == 2, 3)
            await asyncio.sleep(0.2)
            told = [text.split("\n")[1] for text in sender.texts]
            assert told[0].startswith("TRINETRA · BANKNIFTY")  # the owner let it go at night
            assert told[1].endswith("Act: FII selling quickens")  # act never waits
            clock.at = ist(2026, 11, 17, 7, 0, 1)  # morning: the held ones go, the 30 minutes
            desk._wake.set()  # counting from 07:00, not from 23:30
            await wait_until(lambda: len(sender.texts) == 4, 3)
            assert desk.stats["stale"] == 0
        finally:
            await cancel_and_wait(task)

    asyncio.run(scenario())


async def served(**options) -> tuple[MarketApi, str]:
    server = MarketApi(OffDesk(), fernet_for(PEPPER, ALERTS_API_INFO), relay_fernet=RELAY_KEY,
                       **options)  # fmt: skip
    await server.start(0)
    return server, f"http://127.0.0.1:{server.server.sockets[0].getsockname()[1]}"


async def post(base: str, body: bytes, op: str = "signals_relay") -> httpx.Response:
    async with httpx.AsyncClient(trust_env=False) as client:
        return await client.post(f"{base}/v1/{op}", content=body, timeout=5)


def sealed(fernet=RELAY_KEY, op: str = "signals_relay", principal="sudarshan-signals", **args):
    return seal(fernet, op, principal=principal, args=args or {"changes": [change(1)]})


def test_the_relay_opens_only_under_its_own_key_and_principal_market_alerts_off_or_on():
    async def scenario():
        got = []

        async def relay(message):
            got.append(message)
            return {"taken": ["c-1"], "known": []}

        server, base = await served()
        server.relay = relay
        try:
            body = sealed()
            answer = await post(base, body)
            assert answer.status_code == 200
            assert answer.json() == {"status": "success", "data": {"taken": ["c-1"], "known": []}}
            assert got[0]["args"] == {"changes": [change(1)]}
            refused = [
                await post(base, body),  # the same seal: its nonce is spent
                await post(base, sealed(fernet_for(PEPPER, ALERTS_API_INFO))),  # the bridge's
                await post(base, sealed(fernet_for(PEPPER, ALERTS_SCREENER_DIGEST_INFO))),
                await post(base, sealed(fernet_for("q" * 48, ALERTS_SIGNALS_INFO))),  # another .env
                await post(base, sealed(op="state"), op="state"),  # the relay's key, another op
                await post(base, b"not a seal"),
            ]
            assert [r.status_code for r in refused] == [403] * 6
            assert {r.json()["code"] for r in refused} == {"forbidden"}
            owner = await post(base, sealed(principal="owner"))
            assert (owner.status_code, owner.json()["code"]) == (403, "principal")
            assert len(got) == 1
        finally:
            await server.close()

    asyncio.run(scenario())


def test_a_relay_past_16_kib_is_refused_before_it_is_opened():
    from sudarshan.alerts.market import api

    assert (RELAY_MAX, api.RELAY_PRINCIPAL, api.RELAY_OP) == (
        16 * 1024, "sudarshan-signals", "signals_relay",
    )  # fmt: skip

    async def scenario():
        server, base = await served()
        server.relay = lambda message: pytest.fail("a relay past 16 KiB was opened")
        try:
            big = b"x" * (RELAY_MAX + 1)  # far under the listener's own 64 KiB
            answer = await post(base, big)
            assert (answer.status_code, answer.json()["code"]) == (413, "size")
        finally:
            await server.close()

    asyncio.run(scenario())


def test_before_the_desk_is_built_the_relay_is_asked_to_come_again():
    async def scenario():
        server, base = await served()
        try:
            answer = await post(base, sealed())
            assert (answer.status_code, answer.json()["code"]) == (503, "busy")
        finally:
            await server.close()

    asyncio.run(scenario())


def test_the_service_answers_the_relay_through_its_desk_or_says_why_not(settings_for):
    async def scenario():
        svc = AlertService(settings_for(), owner_reader=lambda: None)
        with pytest.raises(ApiError) as starting:
            await svc.signals_relay(relay_message([change(1)]))
        assert (starting.value.status, starting.value.code) == (503, "busy")
        off = AlertService(settings_for(SUDARSHAN_ORDER_ALERTS="off"), owner_reader=lambda: None)
        await off.run()
        with pytest.raises(ApiError) as refused:
            await off.signals_relay(relay_message([change(1)]))
        assert (refused.value.status, refused.value.code) == (409, "off")
        assert "switched off" in refused.value.message

    asyncio.run(scenario())
